"""DBus service the daemon exposes on the session bus for CLI clients.

Bus name:    com.gabriel.iphonebridge
Object path: /com/gabriel/iphonebridge
Interface:   com.gabriel.iphonebridge.Messages1

Methods:
  • Send(string recipient, string body) → string transfer_path
      Send an SMS/iMessage from the iPhone via MAP PushMessage. iOS
      routes as iMessage automatically when the recipient is
      iMessage-capable.

Designed to be simple/synchronous. PushMessage typically completes in
<2s on iOS 26.5 over the existing daemon session.
"""
from __future__ import annotations

import logging

import dbus
import dbus.exceptions
import dbus.service

from iphonebridge.bus import session_bus
from iphonebridge.obex.map_query import list_recent_messages
from iphonebridge.obex.map_send import send_message
from iphonebridge.obex.sessions import SessionManager

log = logging.getLogger(__name__)

BUS_NAME = "com.gabriel.iphonebridge"
OBJECT_PATH = "/com/gabriel/iphonebridge"
IFACE = "com.gabriel.iphonebridge.Messages1"


class MessagesService(dbus.service.Object):
    def __init__(self, bus_name: dbus.service.BusName, sessions: SessionManager):
        super().__init__(bus_name, OBJECT_PATH)
        self.sessions = sessions

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
            return send_message(self.sessions.map_path, recipient, body)
        except Exception as e:
            log.exception("Send failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.SendFailed"
            )

    @dbus.service.method(IFACE, in_signature="su", out_signature="s")
    def ListRecent(self, folder: str, limit: int) -> str:
        """Return up to `limit` recent messages from `folder` as a JSON array."""
        import json
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
