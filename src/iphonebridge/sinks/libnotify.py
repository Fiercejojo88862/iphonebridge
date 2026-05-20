"""Desktop notification sink via org.freedesktop.Notifications.

Body format: title = display sender (contact name or phone number),
             body  = SMS text (truncated at ~280 chars to avoid huge popups).

Read-state sync: when the user dismisses a popup, we set the Read property
on the corresponding BlueZ obex Message1 object. iOS's MAP server is
supposed to propagate that back to the iPhone, marking the message read
in the Messages app. Empirical — works on iOS 26.5 in practice, may be
silently ignored on older iOS or under heavy throttling.
"""
from __future__ import annotations

import logging

import dbus
import dbus.exceptions

from iphonebridge.bus import session_bus
from iphonebridge.events import SmsEvent

log = logging.getLogger(__name__)

_APP_NAME = "iphonebridge"
_BODY_LIMIT = 280

# NotificationClosed reason codes (org.freedesktop.Notifications spec):
#   1 = expired (timeout)
#   2 = dismissed by user
#   3 = CloseNotification() called programmatically
#   4 = undefined / reserved
_REASONS_THAT_MARK_READ = {1, 2}


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
        # notification_id (uint32 from Notify) -> Message1 DBus path
        self._pending: dict[int, str] = {}
        # Subscribe once — the signal fires whenever ANY notification closes,
        # not just ours. We filter by id in the handler.
        self._match = self._notif.connect_to_signal(
            "NotificationClosed", self._on_closed,
        )
        log.info("libnotify sink ready (with mark-read sync on dismiss)")

    def handle(self, event: SmsEvent) -> None:
        title = f"\U0001f4ac {event.display_sender}"
        body = (event.body or "").strip()
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        try:
            nid = int(self._notif.Notify(
                _APP_NAME,                  # app_name
                dbus.UInt32(0),             # replaces_id (0 = new)
                "phone-symbolic",           # icon (built into GNOME)
                title,
                body,
                dbus.Array([], signature="s"),     # actions
                dbus.Dictionary({}, signature="sv"),  # hints
                dbus.Int32(8000),           # expire_timeout ms
            ))
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify failed: %s", e.get_dbus_name())
            return

        if event.message_path:
            self._pending[nid] = event.message_path

    # ---- mark-read on close ---------------------------------------------

    def _on_closed(self, nid, reason) -> None:
        try:
            nid_i = int(nid)
            reason_i = int(reason)
        except (TypeError, ValueError):
            return
        message_path = self._pending.pop(nid_i, None)
        if message_path is None:
            return
        if reason_i not in _REASONS_THAT_MARK_READ:
            return
        try:
            dbus.Interface(
                session_bus.get_object("org.bluez.obex", message_path),
                "org.freedesktop.DBus.Properties",
            ).Set("org.bluez.obex.Message1", "Read", dbus.Boolean(True))
            log.info("marked %s as read on iPhone (close reason=%d)",
                     message_path.rsplit("/", 1)[-1], reason_i)
        except dbus.exceptions.DBusException as e:
            # iPhone may reject the write or the Message1 object may have
            # been garbage-collected by BlueZ. Either way: log and move on.
            log.debug("mark-read failed for %s: %s",
                      message_path, e.get_dbus_name())
