"""DaemonClient — the UI's link to the running iphonebridge daemon.

The daemon owns ``com.gabriel.iphonebridge`` on the session bus. This client
talks to it over **GDBus (Gio)** — not dbus-python — so it works inside the
Flatpak sandbox (``--talk-name=com.gabriel.iphonebridge`` only) and avoids
the fragile ``dbus-python`` build.

* Subscribes to live signals (Events1 + Calls1) and re-emits them as
  GObject signals the UI pages connect to.
* Calls its methods (Messages1.Send, Calls1.Dial/Answer/Hangup, …).
* Reads message/notification history straight from the daemon's state
  files (events.jsonl).

Slow methods (Send, Dial) are issued asynchronously so the UI never blocks.
"""

from __future__ import annotations

import json
import logging
from typing import ClassVar

from gi.repository import Gio, GLib, GObject

from iphonebridge import config

log = logging.getLogger(__name__)

BUS_NAME = "com.gabriel.iphonebridge"
OBJECT_PATH = "/com/gabriel/iphonebridge"
MESSAGES_IFACE = "com.gabriel.iphonebridge.Messages1"
CALLS_IFACE = "com.gabriel.iphonebridge.Calls1"
EVENTS_IFACE = "com.gabriel.iphonebridge.Events1"


def _unwrap(value) -> object:
    """Recursively unpack GLib.Variants into plain Python values."""
    if isinstance(value, GLib.Variant):
        try:
            value = value.unpack()
        except Exception:
            return value
        return _unwrap(value)
    if isinstance(value, dict):
        return {str(k): _unwrap(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_unwrap(v) for v in value]  # type: ignore[return-value]
    return value


def _plain_dict(raw: object) -> dict:
    """Coerce a{sv} payload (already unwrapped) into str-keyed plain dict."""
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}  # type: ignore[return-value]
    return {}


def dbus_error_text(e: Exception) -> str:
    # GDBus surfaces errors as GLib.Error with message "GDBus.Error:..."
    # Strip the prefix for a cleaner toast.
    msg = str(e)
    # GLib.Error often looks like "GDBus.Error:com.gabriel.iphonebridge.Error.X: details"
    if "GDBus.Error:" in msg:
        # keep the human part after the last ": "
        parts = msg.split(":", 2)
        if len(parts) == 3:
            return parts[2].strip() or msg
    return msg


class DaemonClient(GObject.Object):
    """Live link to the daemon. Emits GObject signals as D-Bus signals arrive."""

    __gsignals__: ClassVar = {
        "message-received": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "message-sent": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "message-seen": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "ancs-notification": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "call-state-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "availability-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self) -> None:
        super().__init__()
        self._conn: Gio.DBusConnection | None = None
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            log.warning("session bus not available: %s", e)
            self._conn = None

        self._sub_ids: list[int] = []
        self.available = False  # is the daemon reachable on D-Bus?
        self.healthy = False  # is the MAP session up?
        self._subscribe()
        self.refresh_availability()

    # ---- signal subscription -------------------------------------------

    def _subscribe(self) -> None:
        if self._conn is None:
            return
        # Subscribe even before the daemon is up — delivery starts once it
        # claims the bus name. Gio handles the "name not owned" case.
        mapping = [
            (EVENTS_IFACE, "MessageReceived", "message-received"),
            (EVENTS_IFACE, "MessageSent", "message-sent"),
            (EVENTS_IFACE, "MessageSeen", "message-seen"),
            (EVENTS_IFACE, "AncsNotification", "ancs-notification"),
            (CALLS_IFACE, "CallStateChanged", "call-state-changed"),
        ]
        for iface, signal_name, gsig in mapping:

            def make_handler(gsig_name: str):  # capture per-iteration
                def _handler(
                    _conn: Gio.DBusConnection,
                    _sender: str | None,
                    _obj: str,
                    _iface: str,
                    _sig: str,
                    params: GLib.Variant,
                    *_a,
                ) -> None:
                    try:
                        unpacked = params.unpack() if isinstance(params, GLib.Variant) else params
                        # Signals carry a single a{sv} argument → unpacked is (dict,) or dict
                        if isinstance(unpacked, tuple) and len(unpacked) == 1:
                            raw = unpacked[0]
                        else:
                            raw = unpacked
                        is_dict = isinstance(raw, (dict, GLib.Variant))
                        plain = _plain_dict(_unwrap(raw) if is_dict else raw)  # type: ignore[arg-type]
                        # _unwrap already handled dict case; ensure plain
                        if isinstance(plain, dict):
                            self.emit(gsig_name, plain)
                        else:
                            self.emit(gsig_name, {})
                    except Exception:
                        log.exception("signal %s handler failed", gsig_name)

                return _handler

            handler = make_handler(gsig)
            sub_id = self._conn.signal_subscribe(
                None,  # sender
                iface,
                signal_name,
                OBJECT_PATH,
                None,  # arg0
                Gio.DBusSignalFlags.NONE,
                handler,
                None,
            )
            self._sub_ids.append(sub_id)

    def stop(self) -> None:
        if self._conn is not None:
            for sid in self._sub_ids:
                try:
                    self._conn.signal_unsubscribe(sid)
                except Exception:
                    pass
        self._sub_ids = []

    # ---- availability ---------------------------------------------------

    def refresh_availability(self) -> bool:
        """Re-probe the daemon. Emits availability-changed on a transition."""
        reachable, healthy = True, False
        if self._conn is None:
            reachable = False
        else:
            try:
                reply = self._conn.call_sync(
                    BUS_NAME,
                    OBJECT_PATH,
                    MESSAGES_IFACE,
                    "IsHealthy",
                    None,
                    GLib.VariantType.new("(b)"),
                    Gio.DBusCallFlags.NONE,
                    5000,
                    None,
                )
                (healthy,) = reply.unpack()  # type: ignore[assignment]
                healthy = bool(healthy)
            except GLib.Error as e:  # type: ignore[attr-defined]
                # ServiceUnknown / NameHasNoOwner → daemon not running
                log.debug("IsHealthy failed: %s", e)
                reachable = False
            except Exception as e:
                log.debug("IsHealthy failed: %s", e)
                reachable = False
        self.healthy = healthy
        if reachable != self.available:
            self.available = reachable
            self.emit("availability-changed", reachable)
        return reachable

    # ---- Messages1 ------------------------------------------------------

    def send_message(self, recipient: str, body: str, on_ok, on_err) -> None:
        """Send asynchronously. on_ok(transfer_path) / on_err(text)."""
        if self._conn is None:
            on_err("No session bus — cannot reach daemon")
            return

        params = GLib.Variant("(ss)", (recipient, body))

        def _cb(conn: Gio.DBusConnection, result: Gio.AsyncResult, _ud) -> None:
            try:
                reply = conn.call_finish(result)
                (transfer_path,) = reply.unpack()  # type: ignore[assignment]
                on_ok(str(transfer_path))
            except GLib.Error as e:  # type: ignore[attr-defined]
                on_err(dbus_error_text(e))
            except Exception as e:
                on_err(dbus_error_text(e))

        try:
            self._conn.call(
                BUS_NAME,
                OBJECT_PATH,
                MESSAGES_IFACE,
                "Send",
                params,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                60000,
                None,
                _cb,
                None,
            )
        except Exception as e:
            on_err(dbus_error_text(e))

    # ---- Calls1 ---------------------------------------------------------

    def dial(self, number: str, on_ok, on_err) -> None:
        if self._conn is None:
            on_err("No session bus — cannot reach daemon")
            return
        params = GLib.Variant("(s)", (number,))

        def _cb(conn: Gio.DBusConnection, result: Gio.AsyncResult, _ud) -> None:
            try:
                reply = conn.call_finish(result)
                (call_path,) = reply.unpack()  # type: ignore[assignment]
                on_ok(str(call_path))
            except GLib.Error as e:  # type: ignore[attr-defined]
                on_err(dbus_error_text(e))
            except Exception as e:
                on_err(dbus_error_text(e))

        try:
            self._conn.call(
                BUS_NAME,
                OBJECT_PATH,
                CALLS_IFACE,
                "Dial",
                params,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                45000,
                None,
                _cb,
                None,
            )
        except Exception as e:
            on_err(dbus_error_text(e))

    def answer_call(self, call_path: str) -> str | None:
        variant = GLib.Variant("(s)", (call_path,))
        return self._call_method(CALLS_IFACE, "AnswerCall", variant, 20000)

    def hangup_call(self, call_path: str) -> str | None:
        variant = GLib.Variant("(s)", (call_path,))
        return self._call_method(CALLS_IFACE, "HangupCall", variant, 20000)

    def hangup_all(self) -> str | None:
        return self._call_method(CALLS_IFACE, "HangupAll", None, 20000)

    def _call_method(
        self,
        iface: str,
        method: str,
        params: GLib.Variant | None,
        timeout_msec: int,
    ) -> str | None:
        """Synchronous call for the quick ones. Returns an error string or None."""
        if self._conn is None:
            return "No session bus — cannot reach daemon"
        try:
            self._conn.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                iface,
                method,
                params,
                None,
                Gio.DBusCallFlags.NONE,
                timeout_msec,
                None,
            )
            return None
        except GLib.Error as e:  # type: ignore[attr-defined]
            text = dbus_error_text(e)
            log.warning("%s failed: %s", method, text)
            return text
        except Exception as e:
            text = dbus_error_text(e)
            log.warning("%s failed: %s", method, text)
            return text

    def list_calls(self) -> list[dict]:
        if self._conn is None:
            return []
        try:
            reply = self._conn.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                CALLS_IFACE,
                "ListCalls",
                None,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                15000,
                None,
            )
            (raw,) = reply.unpack()  # type: ignore[assignment]
            return json.loads(str(raw))
        except Exception:
            return []

    # ---- history (read straight from the daemon's state files) ----------

    @staticmethod
    def read_events(kinds: set[str] | None = None, limit: int | None = None) -> list[dict]:
        """Parse events.jsonl, oldest-first. Optionally filter by ``kind``."""
        path = config.EVENTS_JSONL
        out: list[dict] = []
        if not path.exists():
            return out
        try:
            for line in path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if kinds and ev.get("kind") not in kinds:
                    continue
                out.append(ev)
        except OSError as e:
            log.warning("could not read %s: %s", path, e)
        if limit is not None:
            out = out[-limit:]
        return out
