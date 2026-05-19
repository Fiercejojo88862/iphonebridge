"""iphonebridge daemon — orchestrates everything.

Startup order:
  1. bluez_setup.prepare — set adapter CoD, register BLE advert
  2. SessionManager.open_all — long-lived MAP + PBAP OBEX sessions
  3. ContactsResolver — warm SQLite cache; if empty, pull PBAP
  4. MapEventListener — subscribe to MAP MNS push events
  5. Sinks — register libnotify + jsonl
  6. GLib.MainLoop().run()

Shutdown order is the reverse.
"""
from __future__ import annotations

import logging
import signal
from collections.abc import Iterable

from gi.repository import GLib

from iphonebridge import bluez_setup, config
from iphonebridge.bus import main_loop
from iphonebridge.contacts import ContactsResolver, pull_phonebook
from iphonebridge.events import SmsEvent
from iphonebridge.obex.map_events import MapEventListener
from iphonebridge.obex.sessions import SessionManager
from iphonebridge.sinks import Sink
from iphonebridge.sinks.jsonl import JsonlSink
from iphonebridge.sinks.libnotify import LibnotifySink

log = logging.getLogger(__name__)

# How often to re-pull the iPhone's phonebook (so the cache picks up new contacts)
CONTACTS_REFRESH_SEC = 24 * 60 * 60  # 24h


class Daemon:
    def __init__(self) -> None:
        self.sessions = SessionManager()
        self.contacts = ContactsResolver()
        self.sinks: list[Sink] = []
        self.listener: MapEventListener | None = None
        self._contacts_refresh_id: int | None = None

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

        self.sessions.open_all()

        # Warm contacts; if empty, do a one-time pull. PBAP pull is cheap.
        if self.contacts.count() == 0:
            log.info("contacts cache empty — pulling from iPhone via PBAP")
            self._refresh_contacts()

        # Schedule periodic contacts refresh
        self._contacts_refresh_id = GLib.timeout_add_seconds(
            CONTACTS_REFRESH_SEC, self._periodic_refresh_contacts
        )

        # Set up sinks
        self.sinks.append(JsonlSink())
        try:
            self.sinks.append(LibnotifySink())
        except Exception:
            log.exception("libnotify sink failed to init — continuing")

        # Wire up MAP MNS listener.
        # IMPORTANT: pass an indirect lambda so the resolver can be refreshed
        # in place via self.contacts.refresh() without breaking this binding.
        self.listener = MapEventListener(
            sessions=self.sessions,
            on_sms=self._fanout,
            resolve_contact=lambda raw: self.contacts.resolve(raw),
        )
        self.listener.start()

        # Signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._signal)

        log.info("=== iphonebridge ready (contacts=%d, sinks=%s) ===",
                 self.contacts.count(),
                 [s.name for s in self.sinks])

    def _refresh_contacts(self) -> None:
        """Pull phonebook from iPhone + reload in-process cache. Idempotent."""
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
        if self._contacts_refresh_id is not None:
            GLib.source_remove(self._contacts_refresh_id)
            self._contacts_refresh_id = None
        if self.listener is not None:
            self.listener.stop()
        self.sessions.close_all()
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

    def _signal(self, signum, _frame):
        log.info("received signal %d, stopping", signum)
        main_loop.quit()
