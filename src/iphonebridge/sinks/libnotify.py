"""Desktop notification sink via org.freedesktop.Notifications.

Body format: title = display sender (contact name or phone number),
             body  = SMS text (truncated at ~280 chars to avoid huge popups).
"""
from __future__ import annotations

import logging

import dbus

from iphonebridge.bus import session_bus
from iphonebridge.events import SmsEvent

log = logging.getLogger(__name__)

_APP_NAME = "iphonebridge"
_BODY_LIMIT = 280


class LibnotifySink:
    name = "libnotify"

    def __init__(self) -> None:
        self._notif = dbus.Interface(
            session_bus.get_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
            ),
            "org.freedesktop.Notifications",
        )
        log.info("libnotify sink ready")

    def handle(self, event: SmsEvent) -> None:
        title = f"\U0001f4ac {event.display_sender}"
        body = (event.body or "").strip()
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        try:
            self._notif.Notify(
                _APP_NAME,                  # app_name
                dbus.UInt32(0),             # replaces_id (0 = new)
                "phone-symbolic",           # icon (built into GNOME)
                title,
                body,
                dbus.Array([], signature="s"),     # actions
                dbus.Dictionary({}, signature="sv"),  # hints
                dbus.Int32(8000),           # expire_timeout ms
            )
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify failed: %s", e.get_dbus_name())
