# Flatpak packaging — iphonebridge-ui

This packages **only the GTK desktop app** (`iphonebridge-ui`). The daemon
stays a native install: it needs privileged setup — `btmgmt` Class-of-Device,
the `LastUsedBearer=le` file edit, oFono — that a Flatpak sandbox cannot do.
The sandboxed UI reaches the native daemon over the session bus
(`--talk-name=com.gabriel.iphonebridge`).

## Build

```bash
# One-time: tooling + the GNOME SDK/runtime (a few hundred MB)
sudo apt install flatpak-builder
flatpak install flathub org.gnome.Platform//47 org.gnome.Sdk//47

# Build + install for the current user
flatpak-builder --user --install --force-clean \
  build-dir packaging/flatpak/com.gabriel.iphonebridge.UI.yml

flatpak run com.gabriel.iphonebridge.UI
```

## Status — draft

The manifest has **not been built yet**. It is committed as a starting point;
expect to iterate on it during the first real build (runtime version, the
dbus-python module's build, install paths).

### The one real open issue

The UI imports `iphonebridge.bus`, which uses **dbus-python** and opens a
**system-bus** connection at import time. Two consequences for the Flatpak:

1. `dbus-python` is not in the GNOME runtime, so the manifest builds it as a
   module (`python3-dbus`).
2. `iphonebridge.bus` calls `dbus.SystemBus()` on import. Inside the sandbox,
   with no system-bus access, that connection fails and the app won't start.

**Recommended fix** (do this before the first build): port
`src/iphonebridge/ui/client.py` to GLib **GDBus** (`Gio.bus_get` /
`Gio.DBusProxy`) and stop importing `iphonebridge.bus` / `iphonebridge.contacts`
from the UI. GDBus is part of GLib — already in the runtime — so:

- the `python3-dbus` module can be deleted from this manifest;
- the UI talks only to the **session** bus, matching the `finish-args`;
- contact-name lookups read `contacts.sqlite` directly (a few lines of
  `sqlite3`) instead of going through `ContactsResolver`.

As a stopgap only, adding `--socket=system-bus` to `finish-args` would let the
current code start — but that grants broad system-bus access and should not
ship in a release.
