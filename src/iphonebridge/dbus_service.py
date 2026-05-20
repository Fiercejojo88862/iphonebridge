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
    def Send(self, recipient: str, body: str) -> str:  # noqa: N802 — DBus method
        log.info("DBus Send called for %s (%d-byte body)", recipient, len(body))
        if not recipient.strip() or not body.strip():
            raise dbus.exceptions.DBusException(
                "recipient and body must both be non-empty",
                name="com.gabriel.iphonebridge.Error.InvalidArgs",
            )
        try:
            return send_message(self.sessions.map_path, recipient, body)
        except Exception as e:
            log.exception("Send failed")
            raise dbus.exceptions.DBusException(
                str(e), name="com.gabriel.iphonebridge.Error.SendFailed"
            )

    @dbus.service.method(IFACE, in_signature="", out_signature="b")
    def IsHealthy(self) -> bool:  # noqa: N802
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
