"""iphonebridge-ui — GTK4 / libadwaita desktop app.

A standalone app, separate from the daemon. It subscribes to the daemon's
live D-Bus event feed (Events1 + Calls1) and calls its methods (Messages1 +
Calls1); message/notification history is read straight from the daemon's
state files. The daemon stays headless.
"""
