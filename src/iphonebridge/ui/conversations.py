"""Conversations page — SMS/iMessage threads, read history + send replies.

History comes from the daemon's events.jsonl (incoming messages). Messages
sent from this window are appended locally after the daemon confirms the
transfer. Live incoming messages arrive via the `message-received` signal.
"""
from __future__ import annotations

from gi.repository import GLib, Gtk, Pango

from iphonebridge.ui.util import event_ts, format_ts

_ELLIPSIZE_END = Pango.EllipsizeMode.END


def _thread_key(ev: dict) -> str:
    return (ev.get("contact_name") or ev.get("sender_phone")
            or ev.get("sender_phone_norm") or "(unknown)")


class ConversationsPage(Gtk.Box):
    def __init__(self, client, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self._client = client
        self._toast = toast
        self._threads: dict[str, dict] = {}
        self._current: str | None = None

        # ---- left: thread list ----------------------------------------
        self._thread_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self._thread_list.connect("row-selected", self._on_thread_selected)
        sidebar_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=240,
            child=self._thread_list)
        self.append(sidebar_scroll)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ---- right: message view + compose ----------------------------
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self._msg_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=["background"])
        self._msg_scroll = Gtk.ScrolledWindow(
            vexpand=True, child=self._msg_list)
        self._placeholder = Gtk.Label(
            label="Select a conversation", css_classes=["dim-label", "title-2"],
            vexpand=True)
        self._stack = Gtk.Stack()
        self._stack.add_named(self._placeholder, "empty")
        self._stack.add_named(self._msg_scroll, "messages")
        right.append(self._stack)

        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          margin_top=6, margin_bottom=6,
                          margin_start=6, margin_end=6)
        self._entry = Gtk.Entry(
            placeholder_text="Message", hexpand=True, sensitive=False)
        self._entry.connect("activate", self._on_send)
        self._send_btn = Gtk.Button(
            icon_name="document-send-symbolic", sensitive=False,
            css_classes=["suggested-action"], tooltip_text="Send")
        self._send_btn.connect("clicked", self._on_send)
        compose.append(self._entry)
        compose.append(self._send_btn)
        right.append(Gtk.Separator())
        right.append(compose)
        self.append(right)

        self._load_history()
        client.connect("message-received", self._on_incoming)

    # ---- data ----------------------------------------------------------

    def _load_history(self) -> None:
        for ev in self._client.read_events(kinds={"sms_received"}):
            self._ingest(ev, outgoing=False, refresh=False)
        self._rebuild_thread_list()

    def _ingest(self, ev: dict, *, outgoing: bool, refresh: bool = True) -> None:
        key = _thread_key(ev)
        thread = self._threads.get(key)
        if thread is None:
            thread = {"key": key, "name": key,
                      "phone": ev.get("sender_phone")
                      or ev.get("sender_phone_norm") or key,
                      "messages": []}
            self._threads[key] = thread
        msg = {"body": ev.get("body") or "",
               "ts": event_ts(ev), "outgoing": outgoing}
        thread["messages"].append(msg)
        thread["last_ts"] = msg["ts"]
        if refresh:
            self._rebuild_thread_list()
            if self._current == key:
                self._append_bubble(msg)
                self._scroll_to_bottom()

    # ---- thread list ---------------------------------------------------

    def _rebuild_thread_list(self) -> None:
        selected = self._current
        self._thread_list.remove_all()
        order = sorted(self._threads.values(),
                       key=lambda t: t.get("last_ts", ""), reverse=True)
        for thread in order:
            row = Gtk.ListBoxRow()
            row.thread_key = thread["key"]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                          margin_top=8, margin_bottom=8,
                          margin_start=10, margin_end=10)
            box.append(Gtk.Label(label=thread["name"], xalign=0,
                                 css_classes=["heading"],
                                 ellipsize=_ELLIPSIZE_END))
            last = thread["messages"][-1]["body"] if thread["messages"] else ""
            box.append(Gtk.Label(label=last.replace("\n", " "), xalign=0,
                                  ellipsize=_ELLIPSIZE_END,
                                  css_classes=["dim-label"]))
            row.set_child(box)
            self._thread_list.append(row)
            if thread["key"] == selected:
                self._thread_list.select_row(row)

    def _on_thread_selected(self, _list, row) -> None:
        if row is None:
            return
        self._current = row.thread_key
        thread = self._threads.get(self._current)
        self._entry.set_sensitive(True)
        self._send_btn.set_sensitive(True)
        self._stack.set_visible_child_name("messages")
        self._msg_list.remove_all()
        for msg in thread["messages"]:
            self._append_bubble(msg)
        self._scroll_to_bottom()

    # ---- message bubbles ----------------------------------------------

    def _append_bubble(self, msg: dict) -> None:
        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        margin_top=3, margin_bottom=3,
                        margin_start=8, margin_end=8)
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         css_classes=["card", "msg-bubble"])
        bubble.set_halign(Gtk.Align.END if msg["outgoing"] else Gtk.Align.START)
        if msg["outgoing"]:
            bubble.add_css_class("msg-out")
        body = Gtk.Label(label=msg["body"], xalign=0, wrap=True,
                         selectable=True, max_width_chars=46)
        bubble.append(body)
        ts = format_ts(msg["ts"])
        if ts:
            bubble.append(Gtk.Label(label=ts, xalign=1,
                                    css_classes=["dim-label", "caption"]))
        outer.append(bubble)
        row.set_child(outer)
        self._msg_list.append(row)

    def _scroll_to_bottom(self) -> None:
        def _scroll() -> bool:
            adj = self._msg_scroll.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False
        GLib.idle_add(_scroll)

    # ---- send ----------------------------------------------------------

    def _on_send(self, _widget) -> None:
        body = self._entry.get_text().strip()
        if not body or self._current is None:
            return
        thread = self._threads[self._current]
        self._entry.set_sensitive(False)
        self._send_btn.set_sensitive(False)

        def done(_transfer: str) -> None:
            self._entry.set_text("")
            self._entry.set_sensitive(True)
            self._send_btn.set_sensitive(True)
            self._entry.grab_focus()
            self._ingest({"contact_name": thread["name"],
                          "sender_phone": thread["phone"],
                          "body": body, "seen_at": ""}, outgoing=True)

        def failed(text: str) -> None:
            self._entry.set_sensitive(True)
            self._send_btn.set_sensitive(True)
            self._toast(f"Send failed: {text}")

        self._client.send_message(thread["phone"], body, done, failed)

    # ---- live ----------------------------------------------------------

    def _on_incoming(self, _client, ev: dict) -> None:
        self._ingest(ev, outgoing=False)
