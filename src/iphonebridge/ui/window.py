"""Main application window — the four-surface shell."""
from __future__ import annotations

from gi.repository import Adw, Gtk

from iphonebridge.ui.calls import CallsPage
from iphonebridge.ui.conversations import ConversationsPage
from iphonebridge.ui.notifications import NotificationsPage
from iphonebridge.ui.status import StatusPage


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application, client) -> None:
        super().__init__(application=application, title="iphonebridge")
        self._client = client
        self.set_default_size(940, 660)

        self._toasts = Adw.ToastOverlay()
        self._stack = Adw.ViewStack(vexpand=True, hexpand=True)

        for widget, name, title, icon in (
            (ConversationsPage(client, self.toast), "conversations",
             "Messages", "mail-unread-symbolic"),
            (NotificationsPage(client, self.toast), "notifications",
             "Notifications", "preferences-system-notifications-symbolic"),
            (CallsPage(client, self.toast), "calls",
             "Calls", "call-start-symbolic"),
            (StatusPage(client, self.toast), "status",
             "Setup", "emblem-system-symbolic"),
        ):
            self._stack.add_titled_with_icon(widget, name, title, icon)

        switcher = Adw.ViewSwitcher(
            stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)

        self._banner = Adw.Banner(
            title="Daemon not reachable — run: "
                  "systemctl --user start iphonebridge")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self._banner)
        content.append(self._stack)
        self._toasts.set_child(content)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._toasts)
        self.set_content(toolbar)

        client.connect("availability-changed", self._on_availability)
        client.connect("call-state-changed", self._on_call)
        self._on_availability(client, client.available)

    def toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text))

    def _on_availability(self, _client, available: bool) -> None:
        self._banner.set_revealed(not available)

    def _on_call(self, _client, ev: dict) -> None:
        if ev.get("kind") == "call_incoming":
            peer = ev.get("contact_name") or ev.get("peer_phone") or "someone"
            self._stack.set_visible_child_name("calls")
            self.toast(f"Incoming call from {peer}")
            self.present()
