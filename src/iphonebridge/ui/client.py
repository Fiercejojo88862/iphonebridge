"""DaemonClient — the UI's link to the running iphonebridge daemon.

The daemon owns `com.gabriel.iphonebridge` on the session bus. This client:
  • subscribes to its live signals (Events1 + Calls1) and re-emits them as
    GObject signals the UI pages connect to;
  • calls its methods (Messages1.Send, Calls1.Dial/Answer/Hangup, …);
  • reads message/notification history straight from the daemon's state
    files (events.jsonl) — cheaper than a D-Bus round-trip and works even
    while the daemon is mid-restart.

Slow methods (Send, Dial) are issued asynchronously so the UI never blocks.
"""
from __future__ import annotations

import json
import logging
from typing import ClassVar

import dbus
import dbus.exceptions
from gi.repository import GObject

from iphonebridge import config
from iphonebridge.bus import session_bus

log = logging.getLogger(__name__)

BUS_NAME = "com.gabriel.iphonebridge"
OBJECT_PATH = "/com/gabriel/iphonebridge"
MESSAGES_IFACE = "com.gabriel.iphonebridge.Messages1"
CALLS_IFACE = "com.gabriel.iphonebridge.Calls1"
EVENTS_IFACE = "com.gabriel.iphonebridge.Events1"


def _plain(value):
    """Recursively convert dbus-python types into plain Python values."""
    if isinstance(value, dbus.Dictionary):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, dbus.Array):
        return [_plain(v) for v in value]
    if isinstance(value, dbus.String):
        return str(value)
    if isinstance(value, dbus.Boolean):
        return bool(value)
    if isinstance(value, (dbus.Int16, dbus.Int32, dbus.Int64,
                          dbus.UInt16, dbus.UInt32, dbus.UInt64, dbus.Byte)):
        return int(value)
    if isinstance(value, dbus.Double):
        return float(value)
    return value


def dbus_error_text(e: Exception) -> str:
    if isinstance(e, dbus.exceptions.DBusException):
        return e.get_dbus_message() or e.get_dbus_name() or str(e)
    return str(e)


class DaemonClient(GObject.Object):
    """Live link to the daemon. Emits GObject signals as D-Bus signals arrive."""

    __gsignals__: ClassVar = {
        "message-received":     (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "message-seen":         (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "ancs-notification":    (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "call-state-changed":   (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "availability-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self) -> None:
        super().__init__()
        self._bus = session_bus
        self._matches: list = []
        self.available = False   # is the daemon reachable on D-Bus?
        self.healthy = False     # is the MAP session up?
        self._subscribe()
        self.refresh_availability()

    # ---- signal subscription -------------------------------------------

    def _subscribe(self) -> None:
        # add_signal_receiver works even before the daemon is up — delivery
        # just starts once it claims the bus name.
        for sig, gsig in (("MessageReceived", "message-received"),
                          ("MessageSeen", "message-seen"),
                          ("AncsNotification", "ancs-notification")):
            self._matches.append(self._bus.add_signal_receiver(
                lambda props, _g=gsig: self.emit(_g, _plain(props)),
                dbus_interface=EVENTS_IFACE, signal_name=sig,
                bus_name=BUS_NAME, path=OBJECT_PATH))
        self._matches.append(self._bus.add_signal_receiver(
            lambda props: self.emit("call-state-changed", _plain(props)),
            dbus_interface=CALLS_IFACE, signal_name="CallStateChanged",
            bus_name=BUS_NAME, path=OBJECT_PATH))

    def stop(self) -> None:
        for m in self._matches:
            try:
                m.remove()
            except Exception:
                pass
        self._matches = []

    # ---- proxy helpers --------------------------------------------------

    def _iface(self, name: str) -> dbus.Interface:
        return dbus.Interface(self._bus.get_object(BUS_NAME, OBJECT_PATH), name)

    def refresh_availability(self) -> bool:
        """Re-probe the daemon. Emits availability-changed on a transition."""
        reachable, healthy = True, False
        try:
            healthy = bool(self._iface(MESSAGES_IFACE).IsHealthy(timeout=5))
        except dbus.exceptions.DBusException:
            reachable = False
        self.healthy = healthy
        if reachable != self.available:
            self.available = reachable
            self.emit("availability-changed", reachable)
        return reachable

    # ---- Messages1 ------------------------------------------------------

    def send_message(self, recipient: str, body: str, on_ok, on_err) -> None:
        """Send asynchronously. on_ok(transfer_path) / on_err(text)."""
        try:
            self._iface(MESSAGES_IFACE).Send(
                recipient, body, timeout=60,
                reply_handler=lambda t: on_ok(str(t)),
                error_handler=lambda e: on_err(dbus_error_text(e)))
        except dbus.exceptions.DBusException as e:
            on_err(dbus_error_text(e))

    # ---- Calls1 ---------------------------------------------------------

    def dial(self, number: str, on_ok, on_err) -> None:
        try:
            self._iface(CALLS_IFACE).Dial(
                number, timeout=45,
                reply_handler=lambda p: on_ok(str(p)),
                error_handler=lambda e: on_err(dbus_error_text(e)))
        except dbus.exceptions.DBusException as e:
            on_err(dbus_error_text(e))

    def answer_call(self, call_path: str) -> str | None:
        return self._call_method(CALLS_IFACE, "AnswerCall", call_path)

    def hangup_call(self, call_path: str) -> str | None:
        return self._call_method(CALLS_IFACE, "HangupCall", call_path)

    def hangup_all(self) -> str | None:
        return self._call_method(CALLS_IFACE, "HangupAll")

    def _call_method(self, iface: str, method: str, *args) -> str | None:
        """Synchronous call for the quick ones. Returns an error string or None."""
        try:
            getattr(self._iface(iface), method)(*args, timeout=20)
            return None
        except dbus.exceptions.DBusException as e:
            log.warning("%s failed: %s", method, dbus_error_text(e))
            return dbus_error_text(e)

    def list_calls(self) -> list[dict]:
        try:
            raw = str(self._iface(CALLS_IFACE).ListCalls(timeout=15))
            return json.loads(raw)
        except (dbus.exceptions.DBusException, ValueError):
            return []

    # ---- history (read straight from the daemon's state files) ----------

    @staticmethod
    def read_events(kinds: set[str] | None = None,
                     limit: int | None = None) -> list[dict]:
        """Parse events.jsonl, oldest-first. Optionally filter by `kind`."""
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
