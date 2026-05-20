"""iphonebridge-ui — GTK4 / libadwaita desktop app entry point.

A separate process from the daemon. Its application id is
`com.gabriel.iphonebridge.UI` — distinct from the daemon's bus name
`com.gabriel.iphonebridge`, which it talks to over D-Bus.
"""
from __future__ import annotations

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from iphonebridge.ui.client import DaemonClient  # noqa: E402
from iphonebridge.ui.window import MainWindow  # noqa: E402

APP_ID = "com.gabriel.iphonebridge.UI"

_CSS = """
.msg-bubble { padding: 6px 10px; }
.msg-out { background: @accent_bg_color; color: @accent_fg_color; }
"""


class IphonebridgeApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._client: DaemonClient | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(_CSS)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        win = self.props.active_window
        if win is None:
            if self._client is None:
                self._client = DaemonClient()
            win = MainWindow(application=self, client=self._client)
        win.present()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        stream=sys.stderr)
    return IphonebridgeApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
