"""DBus service the daemon exposes on the session bus for CLI clients.

Bus name:    com.gabriel.iphonebridge
Object path: /com/gabriel/iphonebridge
Interfaces:  com.gabriel.iphonebridge.Messages1   — messaging
             com.gabriel.iphonebridge.Calls1      — HFP call control

Messages1:
  • Send(string recipient, string body) → string transfer_path
      Send an SMS/iMessage from the iPhone via MAP PushMessage. iOS routes
      as iMessage automatically when the recipient is iMessage-capable.
  • ListRecent(string folder, uint32 limit) → string json
  • IsHealthy() → bool

Calls1 (HFP, via oFono):
  • Dial(string number) → string call_path
  • AnswerCall(string call_path)
  • HangupCall(string call_path)
  • HangupAll()
  • ListCalls() → string json
  • CallStateChanged(dict)  [signal] — emitted on every call lifecycle change

Events1 (live event feed for separate UIs):
  • MessageReceived(dict)   [signal] — a new SMS/iMessage arrived
  • MessageSent(dict)       [signal] — a message we sent via Send()
  • MessageSeen(dict)       [signal] — a message's read-state changed
  • AncsNotification(dict)  [signal] — a per-app ANCS notification

Designed to be simple/synchronous. PushMessage typically completes in
<2s on iOS 26.5 over the existing daemon session.
"""
from __future__ import annotations

import json
import logging

import dbus
import dbus.exceptions
import dbus.service

from iphonebridge.bus import session_bus
from iphonebridge.hfp.ofono_client import HfpError, HfpManager
from iphonebridge.obex.map_query import list_recent_messages
from iphonebridge.obex.map_send import send_message
from iphonebridge.obex.sessions import SessionManager

log = logging.getLogger(__name__)

BUS_NAME = "com.gabriel.iphonebridge"
BUS_NAME_ALIAS = "com.gabriel.IPhoneBridge"
OBJECT_PATH = "/com/gabriel/iphonebridge"
IFACE = "com.gabriel.iphonebridge.Messages1"
CALLS_IFACE = "com.gabriel.iphonebridge.Calls1"
EVENTS_IFACE = "com.gabriel.iphonebridge.Events1"


def _variant_dict(d: dict) -> dbus.Dictionary:
    """Coerce a plain dict into a D-Bus a{sv}. None → empty string."""
    out = dbus.Dictionary({}, signature="sv")
    for k, v in d.items():
        if v is None:
            out[k] = dbus.String("")
        elif isinstance(v, bool):
            out[k] = dbus.Boolean(v)
        elif isinstance(v, int):
            out[k] = dbus.Int64(v)
        elif isinstance(v, float):
            out[k] = dbus.Double(v)
        else:
            out[k] = dbus.String(str(v))
    return out


class MessagesService(dbus.service.Object):
    def __init__(
        self,
        bus_name: dbus.service.BusName,
        sessions: SessionManager,
        hfp: HfpManager | None = None,
        on_sent=None,
    ):
        super().__init__(bus_name, OBJECT_PATH)
        self.sessions = sessions
        self.hfp = hfp
        # on_sent(recipient, body, transfer_path) — daemon hook to record a
        # message we just sent (logs it to history + the event feed).
        self._on_sent = on_sent

    # ---- Messages1 ------------------------------------------------------

    @dbus.service.method(IFACE, in_signature="ss", out_signature="s")
    def Send(self, recipient: str, body: str) -> str:
        log.info("DBus Send called for %s (%d-byte body)", recipient, len(body))
        if not recipient.strip() or not body.strip():
            raise dbus.exceptions.DBusException(
                "recipient and body must both be non-empty",
                name="com.gabriel.iphonebridge.Error.InvalidArgs",
            )
        if self.sessions.map is None:
            raise dbus.exceptions.DBusException(
                "MAP session not open — iPhone toggles probably off",
                name="com.gabriel.iphonebridge.Error.NotReady",
            )
        try:
            transfer = send_message(self.sessions.map_path, recipient, body)
        except Exception as e:
            log.exception("Send failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.SendFailed"
            )
        # Record the sent message (history + event feed). Never let a logging
        # failure fail the send — the message already went out.
        if self._on_sent is not None:
            try:
                self._on_sent(recipient, body, transfer)
            except Exception:
                log.exception("on_sent hook failed (message was still sent)")
        return transfer

    @dbus.service.method(IFACE, in_signature="su", out_signature="s")
    def ListRecent(self, folder: str, limit: int) -> str:
        """Return up to `limit` recent messages from `folder` as a JSON array."""
        if self.sessions.map is None:
            raise dbus.exceptions.DBusException(
                "MAP session not open — iPhone toggles probably off",
                name="com.gabriel.iphonebridge.Error.NotReady",
            )
        folder = folder or "telecom/msg/INBOX"
        try:
            msgs = list_recent_messages(self.sessions.map_path,
                                        folder=folder,
                                        limit=max(1, min(int(limit), 200)))
        except Exception as e:
            log.exception("ListRecent failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.QueryFailed"
            )
        return json.dumps(msgs, ensure_ascii=False)

    @dbus.service.method(IFACE, in_signature="", out_signature="b")
    def IsHealthy(self) -> bool:
        return self.sessions.map is not None

    # ---- Calls1 (HFP) ---------------------------------------------------

    def _require_hfp(self) -> HfpManager:
        if self.hfp is None:
            raise dbus.exceptions.DBusException(
                "HFP not available in this daemon build",
                name="com.gabriel.iphonebridge.Error.NotReady",
            )
        return self.hfp

    @dbus.service.method(CALLS_IFACE, in_signature="s", out_signature="s")
    def Dial(self, number: str) -> str:
        """Place a call. Returns the new oFono VoiceCall object path."""
        log.info("DBus Dial called for %s", number)
        if not number.strip():
            raise dbus.exceptions.DBusException(
                "number must be non-empty",
                name="com.gabriel.iphonebridge.Error.InvalidArgs",
            )
        try:
            return self._require_hfp().dial(number)
        except HfpError as e:
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.NotReady"
            )
        except Exception as e:
            log.exception("Dial failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.CallFailed"
            )

    @dbus.service.method(CALLS_IFACE, in_signature="s", out_signature="")
    def AnswerCall(self, call_path: str) -> None:
        log.info("DBus AnswerCall %s", call_path)
        try:
            self._require_hfp().answer(call_path)
        except HfpError as e:
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.NotReady"
            )
        except Exception as e:
            log.exception("AnswerCall failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.CallFailed"
            )

    @dbus.service.method(CALLS_IFACE, in_signature="s", out_signature="")
    def HangupCall(self, call_path: str) -> None:
        log.info("DBus HangupCall %s", call_path)
        try:
            self._require_hfp().hangup(call_path)
        except HfpError as e:
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.NotReady"
            )
        except Exception as e:
            log.exception("HangupCall failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.CallFailed"
            )

    @dbus.service.method(CALLS_IFACE, in_signature="", out_signature="")
    def HangupAll(self) -> None:
        log.info("DBus HangupAll")
        try:
            self._require_hfp().hangup_all()
        except HfpError as e:
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.NotReady"
            )
        except Exception as e:
            log.exception("HangupAll failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.CallFailed"
            )

    @dbus.service.method(CALLS_IFACE, in_signature="", out_signature="s")
    def ListCalls(self) -> str:
        """Return the currently-tracked calls as a JSON array."""
        calls = self.hfp.list_calls() if self.hfp is not None else []
        return json.dumps(calls, ensure_ascii=False)

    @dbus.service.signal(CALLS_IFACE, signature="a{sv}")
    def CallStateChanged(self, props):
        """Emitted on every call lifecycle change. Payload is CallEvent.to_dict()."""

    def emit_call_state(self, event) -> None:
        """Daemon-side helper — push a CallEvent out as a CallStateChanged signal."""
        try:
            self.CallStateChanged(_variant_dict(event.to_dict()))
        except Exception:
            log.exception("CallStateChanged emit failed")

    # ---- Events1 (live event feed for separate UIs) ---------------------

    @dbus.service.signal(EVENTS_IFACE, signature="a{sv}")
    def MessageReceived(self, props):
        """Emitted when a new SMS/iMessage arrives. Payload: SmsEvent.to_dict()."""

    @dbus.service.signal(EVENTS_IFACE, signature="a{sv}")
    def MessageSent(self, props):
        """Emitted when we send a message via Send(). Payload: SmsEvent.to_dict()."""

    @dbus.service.signal(EVENTS_IFACE, signature="a{sv}")
    def MessageSeen(self, props):
        """Emitted on a message read-state change. Payload: SmsEvent.to_dict()."""

    @dbus.service.signal(EVENTS_IFACE, signature="a{sv}")
    def AncsNotification(self, props):
        """Emitted on a per-app ANCS notification. Payload: AncsEvent.to_dict()."""

    def emit_message(self, event) -> None:
        """Daemon-side helper — push an SmsEvent out as a D-Bus signal."""
        try:
            payload = _variant_dict(event.to_dict())
            kind = getattr(event, "kind", "")
            if kind == "sms_seen":
                self.MessageSeen(payload)
            elif kind == "sms_sent":
                self.MessageSent(payload)
            else:
                self.MessageReceived(payload)
        except Exception:
            log.exception("message signal emit failed")

    def emit_ancs(self, event) -> None:
        """Daemon-side helper — push an AncsEvent out as an AncsNotification signal."""
        try:
            self.AncsNotification(_variant_dict(event.to_dict()))
        except Exception:
            log.exception("AncsNotification emit failed")


def claim_bus_name() -> dbus.service.BusName:
    """Acquire com.gabriel.iphonebridge on the session bus.

    Raises if the name is already taken by another instance — caller
    should treat that as 'another daemon is already running'.
    """
    return dbus.service.BusName(
        BUS_NAME,
        bus=session_bus,
        do_not_queue=True,
        replace_existing=False,
    )


def claim_bus_alias() -> dbus.service.BusName | None:
    """Also claim com.gabriel.IPhoneBridge so other UIs can subscribe

    under the capitalized alias from BACKLOG. Best-effort — if the alias
    is already taken or the bus is unavailable, return None and let the
    primary name carry the service.
    """
    try:
        return dbus.service.BusName(
            BUS_NAME_ALIAS,
            bus=session_bus,
            do_not_queue=True,
            replace_existing=False,
        )
    except dbus.exceptions.DBusException:
        return None
