"""Desktop notification sink via org.freedesktop.Notifications.

Body format: title = display sender (contact name or phone number),
             body  = SMS text (truncated at ~280 chars to avoid huge popups).

Persistence model — notifications stay visible until ONE of:
  • The user dismisses the popup (clicks/swipes)        → we mark-read on iPhone
  • The iPhone marks the message read (user opens it)  → we auto-close popup
That way an unread message is never "missed" on the desktop side.

Read-state sync:
  Linux dismiss → MAP Message1.Properties.Set(Read=true)  → iPhone marks read
  iPhone reads  → MAP PropertiesChanged(Read=true)         → we close popup

Empirical on iOS 26.5: both directions work. iPhone propagates Read=true
back over MAP within a few seconds of opening the Messages app.
"""
from __future__ import annotations

import logging

import dbus
import dbus.exceptions

from iphonebridge.ancs.events import AncsEvent
from iphonebridge.bus import session_bus
from iphonebridge.events import SmsEvent

log = logging.getLogger(__name__)

_APP_NAME = "iphonebridge"
_BODY_LIMIT = 280

# NotificationClosed reason codes (org.freedesktop.Notifications spec):
#   1 = expired (timeout)
#   2 = dismissed by user
#   3 = CloseNotification() called programmatically (e.g. by us on iPhone-read)
#   4 = undefined / reserved
#
# We mark-read only on dismissed-by-user. Reason 3 = we're already closing
# because the iPhone marked it read (so we'd be in a write-self-write loop).
# Reason 1 = expired, but with timeout=0 this shouldn't happen for us.
_REASON_DISMISSED = 2


class LibnotifySink:
    name = "libnotify"

    def __init__(self, hfp=None) -> None:
        # Optional HfpManager — when present, incoming-call popups carry
        # Answer / Decline action buttons wired straight to it.
        self._hfp = hfp
        self._notif = dbus.Interface(
            session_bus.get_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
            ),
            "org.freedesktop.Notifications",
        )
        # notification_id (uint32 from Notify) -> Message1 DBus path
        self._pending: dict[int, str] = {}
        # notification_id -> SignalMatch for the per-Message1 PropertiesChanged sub
        self._msg_subs: dict[int, object] = {}
        # Incoming-call popups: call_path <-> notification_id
        self._call_notifs: dict[str, int] = {}
        self._notif_calls: dict[int, str] = {}

        # Listen for any of our notifications closing (dismissed, expired,
        # or programmatically closed).
        self._match = self._notif.connect_to_signal(
            "NotificationClosed", self._on_closed,
        )
        # Listen for action-button clicks (Answer / Decline on call popups).
        self._action_match = self._notif.connect_to_signal(
            "ActionInvoked", self._on_action,
        )
        log.info(
            "libnotify sink ready (persistent + bidirectional read-sync)")

    def handle(self, event: SmsEvent) -> None:
        title = f"\U0001f4ac {event.display_sender}"
        body = (event.body or "").strip()
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        try:
            # expire_timeout=0 → notification stays visible indefinitely.
            # We close it ourselves when the iPhone marks the message read,
            # or rely on the user to dismiss it manually (which we then
            # propagate back as mark-read).
            nid = int(self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "phone-symbolic",
                title,
                body,
                dbus.Array([], signature="s"),
                dbus.Dictionary({"urgency": dbus.Byte(1)}, signature="sv"),
                dbus.Int32(0),  # 0 = never expire
            ))
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify failed: %s", e.get_dbus_name())
            return

        if event.message_path:
            self._pending[nid] = event.message_path
            # Subscribe to PropertiesChanged on this specific Message1 path
            # so we get notified if iOS marks it read.
            self._msg_subs[nid] = session_bus.add_signal_receiver(
                lambda iface, changed, _inv, nid=nid:
                    self._on_msg_props(nid, iface, changed),
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                path=event.message_path,
            )

    # ---- ANCS events (per-app notifications) ----------------------------

    def handle_ancs(self, event: AncsEvent) -> None:
        # Title: "📱 AppName" or "📱 com.bundle.id" if no name yet
        app = event.app_name or event.app_id or "Notification"
        title = f"\U0001f4f1 {app}"
        # Body: prefer Title field for headline, then Message
        body_parts = [p for p in (event.title, event.body) if p]
        body = " — ".join(body_parts) if body_parts else ""
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        try:
            # ANCS notifications also persistent (timeout=0). User dismisses
            # or we close on demand. No mark-read sync for ANCS (the iPhone
            # doesn't expose a write-back path for app notification state).
            self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "phone-symbolic",
                title,
                body,
                dbus.Array([], signature="s"),
                dbus.Dictionary({"urgency": dbus.Byte(1)}, signature="sv"),
                dbus.Int32(0),
            )
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify (ANCS) failed: %s", e.get_dbus_name())

    # ---- HFP call events (incoming-call popups with actions) ------------

    def handle_call(self, event) -> None:
        if event.kind == "call_incoming":
            self._show_incoming_call(event)
        elif event.kind in ("call_active", "call_ended"):
            # Answered (here or on the phone) or ended — drop the ringing popup.
            self._close_call_notif(event.call_path)

    def _show_incoming_call(self, event) -> None:
        if event.call_path in self._call_notifs:
            return  # already showing a popup for this call
        title = f"\U0001f4de {event.display_peer}"
        # Action buttons are only useful if we can actually act on them.
        if self._hfp is not None:
            actions = dbus.Array(
                ["answer", "Answer", "decline", "Decline"], signature="s")
        else:
            actions = dbus.Array([], signature="s")
        try:
            nid = int(self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "call-start-symbolic",
                title,
                "Incoming call",
                actions,
                dbus.Dictionary({"urgency": dbus.Byte(2)}, signature="sv"),
                dbus.Int32(0),  # 0 = never expire (we close it ourselves)
            ))
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify (call) failed: %s", e.get_dbus_name())
            return
        self._call_notifs[event.call_path] = nid
        self._notif_calls[nid] = event.call_path

    def _close_call_notif(self, call_path: str) -> None:
        nid = self._call_notifs.pop(call_path, None)
        if nid is None:
            return
        self._notif_calls.pop(nid, None)
        try:
            self._notif.CloseNotification(dbus.UInt32(nid))
        except dbus.exceptions.DBusException:
            pass

    def _on_action(self, nid, action_key) -> None:
        try:
            nid_i = int(nid)
        except (TypeError, ValueError):
            return
        call_path = self._notif_calls.get(nid_i)
        if call_path is None or self._hfp is None:
            return
        action = str(action_key)
        try:
            if action == "answer":
                self._hfp.answer(call_path)
                log.info("answered call from notification: %s", call_path)
            elif action == "decline":
                self._hfp.hangup(call_path)
                log.info("declined call from notification: %s", call_path)
        except Exception as e:
            log.warning("call action %r failed: %s", action, e)

    # ---- iPhone marks read → close our popup ----------------------------

    def _on_msg_props(self, nid: int, iface: str, changed) -> None:
        if iface != "org.bluez.obex.Message1":
            return
        # Look for Read going True. Some BlueZ versions send Status instead.
        read_now = (
            bool(changed.get("Read", False))
            or str(changed.get("Status", "")).lower() in ("read", "complete")
        )
        if not read_now:
            return
        if nid not in self._pending:
            return  # already closed/handled
        try:
            self._notif.CloseNotification(dbus.UInt32(nid))
            log.info("iPhone marked message read — closed popup %d", nid)
        except dbus.exceptions.DBusException as e:
            log.debug("CloseNotification(%d): %s", nid, e.get_dbus_name())
        # _on_closed will clean up the dict + signal match (reason=3)

    # ---- Linux user dismisses → mark-read on iPhone ----------------------

    def _on_closed(self, nid, reason) -> None:
        try:
            nid_i = int(nid)
            reason_i = int(reason)
        except (TypeError, ValueError):
            return

        message_path = self._pending.pop(nid_i, None)

        # Clean up call-popup bookkeeping if this was an incoming-call popup.
        call_path = self._notif_calls.pop(nid_i, None)
        if call_path is not None:
            self._call_notifs.pop(call_path, None)

        # Always remove the per-message subscription, no matter the reason
        sub = self._msg_subs.pop(nid_i, None)
        if sub is not None:
            try:
                sub.remove()
            except Exception:
                pass

        if message_path is None:
            return

        # Only propagate read-state to iPhone when the human actively
        # dismissed (reason=2). Don't loop on programmatic close (reason=3,
        # which is fired when we closed it ourselves because iPhone already
        # marked it read).
        if reason_i != _REASON_DISMISSED:
            return
        try:
            dbus.Interface(
                session_bus.get_object("org.bluez.obex", message_path),
                "org.freedesktop.DBus.Properties",
            ).Set("org.bluez.obex.Message1", "Read", dbus.Boolean(True))
            log.info("marked %s as read on iPhone (user dismissed popup)",
                     message_path.rsplit("/", 1)[-1])
        except dbus.exceptions.DBusException as e:
            log.debug("mark-read failed for %s: %s",
                      message_path, e.get_dbus_name())
