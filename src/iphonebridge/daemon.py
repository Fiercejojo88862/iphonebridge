"""iphonebridge daemon — orchestrates everything.

Startup order:
  1. bluez_setup.prepare — set adapter CoD, register BLE advert
  2. SessionManager.open_all — long-lived MAP + PBAP OBEX sessions
     (retries on Forbidden — see _try_open_sessions)
  3. ContactsResolver — warm SQLite cache; if empty, pull PBAP
  4. MapEventListener — subscribe to MAP MNS push events
  5. Sinks — register libnotify + jsonl
  6. DBus service (com.gabriel.iphonebridge.Messages1 + alias)
  7. GLib.MainLoop().run()

Shutdown order is the reverse.

Degraded mode: if MAP/PBAP can't open (typically because the user hasn't
enabled iPhone toggles yet), the daemon stays alive, logs a clear
remediation hint, and retries every 60s. This avoids the systemd
crash-loop we hit in an earlier version.

Multi-device: when IPHONEBRIDGE_MACS contains multiple MACs
(e.g. "AA:..,BB:.."), the daemon opens one SessionManager + ANCS client +
MNS listener per device. The first MAC is the primary (backward compat for
DBus Send/Dial which defaults to primary). Events from all devices fan out
through the same sinks and D-Bus signals, with device MAC in the payload.
"""
from __future__ import annotations

import logging
import signal

from gi.repository import GLib

from iphonebridge import bluez_setup, config
from iphonebridge.ancs.client import AncsClient
from iphonebridge.ancs.events import AncsEvent
from iphonebridge.bus import main_loop, system_bus
from iphonebridge.contacts import ContactsResolver, pull_phonebook
from iphonebridge.dbus_service import MessagesService, claim_bus_alias, claim_bus_name
from iphonebridge.events import SmsEvent, sms_sent_event
from iphonebridge.hfp.events import CallEvent
from iphonebridge.hfp.ofono_client import HfpManager
from iphonebridge.obex.map_events import MapEventListener
from iphonebridge.obex.sessions import SessionError, SessionManager
from iphonebridge.sinks import Sink
from iphonebridge.sinks.clipboard import ClipboardSink
from iphonebridge.sinks.jsonl import JsonlSink
from iphonebridge.sinks.libnotify import LibnotifySink

log = logging.getLogger(__name__)

# How often to re-pull the iPhone's phonebook (so the cache picks up new contacts)
CONTACTS_REFRESH_SEC = 24 * 60 * 60  # 24h

# How often to retry MAP/PBAP session open when blocked by the iPhone
# (toggles off, paired-but-not-connected, etc.)
SESSION_RETRY_SEC = 60

# How long after a suspend/resume to wait before re-establishing BT
# sessions (BlueZ + obexd need a few seconds to settle after resume).
RESUME_RECONNECT_DELAY_SEC = 8


def _valid_macs() -> list[str]:
    return [m for m in config.IPHONE_MACS if m.upper() != config.PLACEHOLDER_MAC]


class Daemon:
    def __init__(self) -> None:
        macs = _valid_macs()
        primary = macs[0] if macs else config.IPHONE_MAC
        self.sessions = SessionManager(mac=primary)
        # Extra managers for secondary devices (multi-device mode)
        self.extra_managers: dict[str, SessionManager] = {
            m: SessionManager(mac=m) for m in macs[1:]
        }
        self.contacts = ContactsResolver()
        self.sinks: list[Sink] = []
        self.listener: MapEventListener | None = None
        self.extra_listeners: dict[str, MapEventListener] = {}
        self.ancs: AncsClient | None = None
        self.extra_ancs: dict[str, AncsClient] = {}
        self.hfp: HfpManager | None = None
        self._contacts_refresh_id: int | None = None
        self._session_retry_id: int | None = None
        self._bus_name = None
        self._bus_alias = None
        self._dbus_service: MessagesService | None = None
        self._post_sessions_done = False
        # Suspend/resume handling (see _setup_resume_handler)
        self._resume_match = None
        self._device_monitor_matches: list = []
        self._resume_pending_id: int | None = None

    def _all_managers(self) -> list[SessionManager]:
        return [self.sessions] + list(self.extra_managers.values())

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        log.info("=== iphonebridge starting ===")
        config.ensure_dirs()

        if not bluez_setup.prepare():
            log.warning(
                "bluez_setup.prepare reported issues — continuing anyway, "
                "but MAP/PBAP may be refused. Re-pair on iPhone after the "
                "adapter is in A/V Hands-Free CoD if the toggles aren't there."
            )

        # ANCS — per-app notifications via BLE GATT, one client per device
        for mac in _valid_macs():
            device_path = f"/org/bluez/{config.ADAPTER}/dev_{mac.replace(':', '_')}"
            client = AncsClient(device_path, on_event=self._fanout_ancs)
            client.start()
            if mac == self.sessions.mac:
                self.ancs = client
            else:
                self.extra_ancs[mac] = client
        if not _valid_macs():
            # Placeholder config — still create one client for the placeholder path
            device_path = f"/org/bluez/{config.ADAPTER}/dev_{config.IPHONE_MAC.replace(':', '_')}"
            self.ancs = AncsClient(device_path, on_event=self._fanout_ancs)
            self.ancs.start()

        # HFP — global (oFono handles multiple modems)
        self.hfp = HfpManager(
            on_event=self._fanout_call,
            resolve_contact=lambda raw: self.contacts.resolve(raw),
        )
        self.hfp.start()

        # Sinks don't need the OBEX sessions — set them up now so ANCS and
        # HFP events still reach the desktop while MAP/PBAP are degraded.
        self._setup_sinks()

        # Try to open MAP/PBAP for all devices
        self._try_open_sessions(first_attempt=True)

        # Always set up the DBus service so a CLI can at least query
        # IsHealthy and learn the daemon's status. Send() will fail until
        # the MAP session is open, but the service surface itself is up.
        try:
            self._bus_name = claim_bus_name()
            # For multi-device, D-Bus Send/Dial defaults to primary; extra
            # devices can be addressed via optional --device flag (future).
            self._dbus_service = MessagesService(
                self._bus_name, self.sessions, hfp=self.hfp,
                on_sent=self._record_sent)
            log.info("DBus service ready: com.gabriel.iphonebridge")
            # Alias for other UIs (BACKLOG: com.gabriel.IPhoneBridge)
            try:
                self._bus_alias = claim_bus_alias()
                if self._bus_alias is not None:
                    log.info("DBus alias ready: com.gabriel.IPhoneBridge")
            except Exception:
                log.debug("DBus alias claim failed", exc_info=True)
        except Exception:
            log.exception("DBus service registration failed — continuing "
                          "without send capability")

        # Suspend/resume handler — re-establishes OBEX sessions after sleep
        self._setup_resume_handler()

        # Signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._signal)

        if not self._post_sessions_done:
            log.warning("=== iphonebridge running in DEGRADED mode ===")
            log.warning("    No MAP/PBAP session yet. Retrying every %ds.",
                        SESSION_RETRY_SEC)
        # The "ready" line in the happy path is emitted by
        # _post_sessions_setup, so we don't duplicate it here.

    def _try_open_sessions(self, *, first_attempt: bool) -> None:
        """Open MAP + PBAP for all devices. On Forbidden, schedule retry."""
        failed: list[str] = []
        for mgr in self._all_managers():
            try:
                mgr.open_all()
            except SessionError as e:
                msg = str(e)
                log.warning("could not open MAP/PBAP sessions for %s: %s", mgr.mac, msg)
                failed.append(mgr.mac)
                if "Forbidden" in msg or "0x43" in msg:
                    log.warning("")
                    log.warning(
                        "  → Toggles off for %s — check iPhone Bluetooth (i).",
                        mgr.mac)
                    log.warning("  → On the iPhone:")
                    log.warning("       Settings → Bluetooth → tap (i) next to this device")
                    log.warning("       Enable: Show Message Notifications")
                    log.warning("       Enable: Sync Contacts")
                    log.warning("")
        if failed:
            if first_attempt and self._session_retry_id is None:
                self._session_retry_id = GLib.timeout_add_seconds(
                    SESSION_RETRY_SEC, self._retry_sessions
                )
                log.warning("  → Daemon stays running. Will retry every %ds.",
                            SESSION_RETRY_SEC)
            return
        # Sessions opened — wire everything that depends on them.
        self._post_sessions_setup()

    def _retry_sessions(self) -> bool:
        """GLib timer callback. Return True to keep the timer firing."""
        log.info("retrying MAP/PBAP session open ...")
        still_failed = False
        for mgr in self._all_managers():
            if mgr.map is not None:
                continue
            try:
                mgr.open_all()
                log.info("sessions opened on retry for %s — promoting", mgr.mac)
            except SessionError as e:
                log.info("still blocked for %s: %s", mgr.mac, str(e)[:120])
                still_failed = True
        if still_failed:
            return True
        log.info("all sessions opened on retry — promoting to ready state")
        self._post_sessions_setup()
        self._session_retry_id = None
        return False

    def _setup_sinks(self) -> None:
        """Register the JSONL + libnotify sinks. Independent of the OBEX
        sessions, so ANCS/HFP events reach the desktop even in degraded mode."""
        if self.sinks:
            return
        self.sinks.append(JsonlSink())
        try:
            self.sinks.append(LibnotifySink(hfp=self.hfp))
        except Exception:
            log.exception("libnotify sink failed to init — continuing")
        try:
            self.sinks.append(ClipboardSink())
        except Exception:
            log.exception("clipboard sink failed to init — continuing")
        log.info("sinks ready: %s", [s.name for s in self.sinks])

    def _post_sessions_setup(self) -> None:
        """Everything that requires live MAP+PBAP sessions. Idempotent so
        we can call it either at first-attempt success or at retry success."""
        if self._post_sessions_done:
            return
        self._post_sessions_done = True

        # Warm contacts; if empty, do a one-time pull from primary
        if self.contacts.count() == 0:
            log.info("contacts cache empty — pulling from iPhone via PBAP")
            self._refresh_contacts()

        # Schedule periodic contacts refresh
        if self._contacts_refresh_id is None:
            self._contacts_refresh_id = GLib.timeout_add_seconds(
                CONTACTS_REFRESH_SEC, self._periodic_refresh_contacts
            )

        # Wire up MAP MNS listeners — one per device
        if self.listener is None and self.sessions.map is not None:
            self.listener = MapEventListener(
                sessions=self.sessions,
                on_sms=self._fanout,
                resolve_contact=lambda raw: self.contacts.resolve(raw),
            )
            self.listener.start()
        for mac, mgr in self.extra_managers.items():
            if mac in self.extra_listeners or mgr.map is None:
                continue
            lst = MapEventListener(
                sessions=mgr,
                on_sms=self._fanout,
                resolve_contact=lambda raw: self.contacts.resolve(raw),
            )
            lst.start()
            self.extra_listeners[mac] = lst

        log.info("=== iphonebridge ready (contacts=%d, sinks=%s, devices=%s) ===",
                  self.contacts.count(),
                  [s.name for s in self.sinks],
                  [m.mac for m in self._all_managers()])

    def _refresh_contacts(self) -> None:
        """Pull phonebook from primary iPhone + reload in-process cache."""
        try:
            pulled = pull_phonebook(self.sessions)
            count = self.contacts.refresh()
            log.info("contacts refresh: pulled %d, cached %d", pulled, count)
        except Exception:
            log.exception("contacts refresh failed — running with previous cache")

    def _periodic_refresh_contacts(self) -> bool:
        """GLib timeout callback. Return True to keep the timer running."""
        log.info("periodic contacts refresh tick")
        self._refresh_contacts()
        return True

    def stop(self) -> None:
        log.info("=== iphonebridge stopping ===")
        for tid_attr in ("_contacts_refresh_id", "_session_retry_id", "_resume_pending_id"):
            tid = getattr(self, tid_attr, None)
            if tid is not None:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, tid_attr, None)
        if self._resume_match is not None:
            try:
                self._resume_match.remove()
            except Exception:
                pass
            self._resume_match = None
        for m in list(self._device_monitor_matches):
            try:
                m.remove()
            except Exception:
                pass
        self._device_monitor_matches = []
        if self.listener is not None:
            self.listener.stop()
        for lst in list(self.extra_listeners.values()):
            try:
                lst.stop()
            except Exception:
                pass
        self.extra_listeners = {}
        if self.ancs is not None:
            self.ancs.stop()
        for ac in list(self.extra_ancs.values()):
            try:
                ac.stop()
            except Exception:
                pass
        self.extra_ancs = {}
        if self.hfp is not None:
            self.hfp.stop()
        for mgr in self._all_managers():
            try:
                mgr.close_all()
            except Exception:
                pass
        bluez_setup.unregister_advert()
        main_loop.quit()

    def run(self) -> None:
        self.start()
        try:
            main_loop.run()
        finally:
            self.stop()

    # ---- internals -------------------------------------------------------

    def _fanout(self, event: SmsEvent) -> None:
        for sink in self.sinks:
            try:
                sink.handle(event)
            except Exception:
                log.exception("sink %s failed on event %s",
                              sink.name, event.handle)
        if self._dbus_service is not None:
            self._dbus_service.emit_message(event)

    def _record_sent(self, recipient: str, body: str, transfer_path: str) -> None:
        """Hook for DBus Send() — log + broadcast a message we just sent so it
        shows up in conversation history alongside incoming messages."""
        event = sms_sent_event(
            recipient, body,
            contact_name=self.contacts.resolve(recipient),
            transfer_path=transfer_path,
        )
        log.info("sms_sent to %s: %r", event.display_sender, (body or "")[:80])
        self._fanout(event)

    def _fanout_ancs(self, event: AncsEvent) -> None:
        for sink in self.sinks:
            try:
                handler = getattr(sink, "handle_ancs", None)
                if handler is None:
                    continue  # sink doesn't know about ANCS events
                handler(event)
            except Exception:
                log.exception("sink %s failed on ANCS event %d",
                              sink.name, event.notification_id)
        if self._dbus_service is not None:
            self._dbus_service.emit_ancs(event)

    def _fanout_call(self, event: CallEvent) -> None:
        for sink in self.sinks:
            try:
                handler = getattr(sink, "handle_call", None)
                if handler is None:
                    continue  # sink doesn't know about call events
                handler(event)
            except Exception:
                log.exception("sink %s failed on call event %s",
                              sink.name, event.call_path)
        if self._dbus_service is not None:
            self._dbus_service.emit_call_state(event)

    def _signal(self, signum, _frame):
        log.info("received signal %d, stopping", signum)
        main_loop.quit()

    # ---- suspend/resume handling ----------------------------------------

    def _setup_resume_handler(self) -> None:
        """Listen for systemd login1 PrepareForSleep + BlueZ Connected changes.

        * ``PrepareForSleep(True)``  → going to sleep, just log.
        * ``PrepareForSleep(False)`` → resumed, schedule a reconnect after
          ``RESUME_RECONNECT_DELAY_SEC`` so BlueZ/obexd settle.
        * BlueZ ``PropertiesChanged`` on each iPhone device's ``Connected``
          → if the device disconnects outside suspend, schedule the same
          delayed reconnect (covers adapter power-cycle, iPhone BT toggle).

        Both are best-effort — if the bus isn't there (e.g. tests / container)
        we just log and continue.
        """
        # login1 — suspend/resume
        try:
            self._resume_match = system_bus.add_signal_receiver(
                self._on_prepare_for_sleep,
                dbus_interface="org.freedesktop.login1.Manager",
                signal_name="PrepareForSleep",
                path="/org/freedesktop/login1",
                bus_name="org.freedesktop.login1",
            )
            log.info("suspend/resume handler: listening for login1 PrepareForSleep")
        except Exception:
            log.debug("login1 PrepareForSleep subscribe failed — continuing without it",
                      exc_info=True)

        # BlueZ device Connected — one monitor per MAC
        for mac in _valid_macs() or [config.IPHONE_MAC]:
            device_path = f"/org/bluez/{config.ADAPTER}/dev_{mac.replace(':', '_')}"
            try:
                m = system_bus.add_signal_receiver(
                    self._on_device_properties_changed,
                    dbus_interface="org.freedesktop.DBus.Properties",
                    signal_name="PropertiesChanged",
                    path=device_path,
                )
                self._device_monitor_matches.append(m)
                log.info("suspend/resume handler: monitoring %s Connected", device_path)
            except Exception:
                log.debug("BlueZ device monitor subscribe failed for %s — continuing", device_path,
                          exc_info=True)

    def _on_prepare_for_sleep(self, going_to_sleep: bool) -> None:
        # dbus-python delivers a dbus.Boolean — cast to plain bool
        sleeping = bool(going_to_sleep)
        if sleeping:
            log.info("suspend detected (PrepareForSleep=True) — BT sessions will stall")
            return
        log.info("resume detected (PrepareForSleep=False) — scheduling reconnect in %ds",
                 RESUME_RECONNECT_DELAY_SEC)
        self._schedule_resume_reconnect()

    def _on_device_properties_changed(self, iface, changed, _invalidated) -> None:
        if iface != "org.bluez.Device1":
            return
        if "Connected" not in changed:
            return
        connected = bool(changed["Connected"])
        if connected:
            log.info("BlueZ device Connected=True — scheduling session check")
        else:
            log.info("BlueZ device Connected=False — scheduling reconnect")
        self._schedule_resume_reconnect()

    def _schedule_resume_reconnect(self) -> None:
        if self._resume_pending_id is not None:
            # Already scheduled — don't stack timers
            return
        self._resume_pending_id = GLib.timeout_add_seconds(
            RESUME_RECONNECT_DELAY_SEC, self._resume_reconnect
        )

    def _resume_reconnect(self) -> bool:
        """GLib timeout callback after resume / disconnect. Return False to run once."""
        self._resume_pending_id = None
        log.info("resume reconnect: tearing down stale sessions and retrying")

        # Stop any in-flight retry timer — we're doing a fresh attempt now
        if self._session_retry_id is not None:
            try:
                GLib.source_remove(self._session_retry_id)
            except Exception:
                pass
            self._session_retry_id = None

        # Tear down stale OBEX sessions + listeners, then re-run the normal
        # open/retry path. _post_sessions_done guards idempotency.
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:
                log.exception("listener stop on resume failed")
            self.listener = None
        for lst in list(self.extra_listeners.values()):
            try:
                lst.stop()
            except Exception:
                log.exception("extra listener stop on resume failed")
        self.extra_listeners = {}

        for mgr in self._all_managers():
            try:
                mgr.close_all()
            except Exception:
                log.exception("session close on resume failed for %s", mgr.mac)

        # Reset the post-setup gate so _post_sessions_setup can run again
        # if the reconnect succeeds.
        was_ready = self._post_sessions_done
        if was_ready and any(m.map is None for m in self._all_managers()):
            self._post_sessions_done = False

        self._try_open_sessions(first_attempt=True)
        if not self._post_sessions_done:
            log.warning("resume reconnect: still degraded — periodic retry continues")
        else:
            log.info("resume reconnect: back to ready")

        # Restart ANCS/HFP clients if they stopped (they are independent but
        # also lose their GATT/oFono state across suspend).
        for ac in [self.ancs] + list(self.extra_ancs.values()):
            if ac is not None:
                try:
                    ac.stop()
                    ac.start()
                    log.info("restarted ANCS client after resume for %s", ac.device_path)
                except Exception:
                    log.exception("restart ANCS after resume failed for %s", ac.device_path)
        if self.hfp is not None:
            try:
                self.hfp.stop()
                self.hfp.start()
                log.info("restarted HFP client after resume")
            except Exception:
                log.exception("restart HFP after resume failed")

        return False
