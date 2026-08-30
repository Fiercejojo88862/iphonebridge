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

## Status — ready to build

`src/iphonebridge/ui/client.py` now uses **Gio GDBus** only (no `dbus-python`,
no `iphonebridge.bus` / system-bus import). The UI talks only to the session
bus, matching `finish-args: --talk-name=com.gabriel.iphonebridge`, so the
manifest no longer vendors `python3-dbus`. Build with:

```bash
flatpak-builder --user --install --force-clean \
  build-dir packaging/flatpak/com.gabriel.iphonebridge.UI.yml
flatpak run com.gabriel.iphonebridge.UI
```

The daemon stays native (see manifest header comment).
