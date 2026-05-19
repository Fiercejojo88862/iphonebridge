# iphonebridge

A native Linux desktop bridge for a paired iPhone over Bluetooth. SMS notifications with sender names, conversation history readable from the CLI, and contacts cached locally. Built because Microsoft's Phone Link works on Windows but **no equivalent exists on Linux/GNOME** — and the open-source alternatives (KDE Connect, ancs4linux, macaron) each cover only a fraction of what's possible.

> **Status:** Alpha. Phase 1 (MVP) complete and stable. Tested against **iPhone 16 Pro Max running iOS 26.5** on **Pop!_OS 24.04**. See [`spike/RESULTS.md`](spike/RESULTS.md) for the empirical findings underpinning the design.

## What it does

- **Real-time SMS push notifications.** A desktop popup appears within ~20ms of an SMS arriving on the iPhone, showing the sender's name (resolved from the iPhone's address book) and the message body.
- **Contacts sync.** 1000+ contacts pulled from the iPhone via PBAP, cached in SQLite, auto-refreshed every 24 hours.
- **SMS history readable from the CLI.** `iphonebridge sms-list` dumps recent inbox activity from the local event log.
- **Persistent and unattended.** Runs as a systemd user service. Auto-starts on login, restarts on failure, logs to journald.

### What it explicitly does *not* (and never will) do

These limits come from Apple's Bluetooth stack, not Linux. Documented for honesty:

- **No iMessage.** iPhone's MAP server exposes carrier SMS only. iMessage threads are invisible. Replies sent through this bridge will arrive as green-bubble SMS, even to iMessage contacts. For full iMessage, you need a Mac relay (BlueBubbles / AirMessage) or a paid Beeper subscription.
- **No per-app notification mirroring** *yet*. ANCS — the every-app notification source (Slack, WhatsApp, Mail, etc.) — is deferred to Phase 2. iOS requires a different pairing strategy for ANCS that currently conflicts with the BR/EDR pair MAP/PBAP need.
- **No outgoing call audio routing.** HFP Hands-Free role on Linux is a separate config rabbit hole (WirePlumber 1.5 + possibly oFono); deferred.
- **No group MMS / RCS.** iPhone's MAP doesn't expose them.
- **No notification body when iPhone's "Show Previews" is set to "When Unlocked" or "Never".** ANCS/MAP respect that setting; unfixable from Linux side.

## Requirements

| | |
|---|---|
| OS | Pop!_OS 24.04 / Ubuntu 24.04 (any GNOME-based modern distro should work; tested only on Pop) |
| Bluetooth | BlueZ 5.72+ with `bluez-obexd` |
| Audio | PipeWire 1.x (only relevant if HFP support is added later) |
| Python | 3.10+ |
| iPhone | iOS 16.5+ recommended (tested on 26.5 on iPhone 16 Pro Max) |
| Bluetooth hardware | Any modern adapter with BLE 4.0+; integrated controllers work fine |

## Installation

```bash
# 1. Install Bluetooth OBEX support (provides MAP + PBAP daemons)
sudo apt install bluez-obexd

# 2. Clone the repo
git clone https://github.com/gabrielmeir53/iphonebridge.git
cd iphonebridge

# 3. Create a venv that inherits system PyGObject + dbus-python
#    (DO NOT install dbus-python or PyGObject from PyPI — those builds are
#    notoriously fragile. The system packages just work.)
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .

# 4. Verify prerequisites
iphonebridge doctor

# 5. (One-time) Let the daemon set the adapter Class-of-Device without
#    a password prompt. Without this, after each reboot you'd have to
#    manually run `sudo btmgmt class 4 8` before the daemon can open MAP
#    sessions. See "Why the CoD matters" below.
sudo bash systemd/install-cod-sudoers.sh

# 6. Pair the iPhone with this machine via the normal GNOME Bluetooth panel
#    or `bluetoothctl pair <MAC>`. Note the MAC of the iPhone — you'll
#    need it in step 8.

# 7. (One-time) On the iPhone, in Settings → Bluetooth → tap (i) next to
#    your computer's name, enable BOTH of:
#      • Show Message Notifications     (gates MAP / SMS)
#      • Sync Contacts                  (gates PBAP / contacts)
#    These toggles only surface after step 5 is done and the daemon has
#    been started once. They are PER-DEVICE and PER-PROFILE.

# 8. Set the target iPhone MAC. The default in src/iphonebridge/config.py
#    works for the author's device. For yours, edit config.py OR export
#    IPHONEBRIDGE_MAC=AA:BB:CC:DD:EE:FF before running the daemon.

# 9. Install + start the daemon as a systemd user service
mkdir -p ~/.config/systemd/user
cp systemd/iphonebridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now iphonebridge

# 10. Test
journalctl --user -u iphonebridge -f
# (then have someone text you — within seconds you should see the event)
```

### Why the CoD matters

iOS 26.5 (and most versions since iOS 16.5) hides per-device permission toggles for MAP, PBAP, and other Bluetooth profiles **unless** the paired adapter declares itself with the right Class-of-Device + BLE solicit advert. This project encodes that "identity dance" in `src/iphonebridge/bluez_setup.py`:

- **CoD = `0x240408`** (Major = Audio/Video, Minor = Hands-Free Device, plus Telephony + Object Transfer + Audio service-class bits) — set via `btmgmt class 4 8` at daemon startup.
- **BLE peripheral advert with `SolicitUUIDs=[ANCS UUID]`** — registered via `org.bluez.LEAdvertisingManager1.RegisterAdvertisement`.

Without that combination, the iPhone's `Settings → Bluetooth → (i)` page won't even show the toggles. With it, the toggles surface within seconds and the OBEX servers (MAP/PBAP) become reachable. This is the single most important non-obvious finding from the Phase 0 spike — see [`spike/RESULTS.md`](spike/RESULTS.md) §1.

## Daily usage

```bash
# Watch the daemon in real time
journalctl --user -u iphonebridge -f

# Show recent SMS events from the local log
iphonebridge sms-list -n 20

# Force a fresh contact pull (otherwise auto-runs every 24h)
iphonebridge contacts-sync

# Stop / start / restart
systemctl --user stop iphonebridge
systemctl --user start iphonebridge
systemctl --user restart iphonebridge

# Disable autostart
systemctl --user disable iphonebridge
```

The local event log is at `~/.local/state/iphonebridge/events.jsonl` (one JSON object per SMS, append-only) and the contact cache is at `~/.local/state/iphonebridge/contacts.sqlite`. Both are safe to delete; they regenerate.

## Troubleshooting

### `iphonebridge doctor` complains the CoD is wrong

Either the boot-time sudoers rule isn't installed yet (run step 5 above) or you're running before `bluetooth.service` is fully up. Try `systemctl --user restart iphonebridge` and watch the logs.

### Notifications stop appearing after a while

Most likely: the iPhone re-locked OBEX sessions after its own internal timeout. Restart the daemon:

```bash
systemctl --user restart iphonebridge
```

If that doesn't bring them back, try also restarting `obex.service` and re-doing the toggle dance:

```bash
systemctl --user restart obex.service
systemctl --user restart iphonebridge
```

If the toggles disappear from the iPhone's Bluetooth settings page, do a fresh unpair and re-pair — the toggles re-surface during a fresh pair when the daemon's advert and CoD are correct.

### `Forbidden` errors in the journald log

MAP/PBAP returned OBEX response `0x43`. Almost always means one of the iPhone toggles got disabled. Check `Settings → Bluetooth → (i) → pop-os → Show Message Notifications` / `Sync Contacts`.

### Daemon won't start

```bash
systemctl --user status iphonebridge
journalctl --user -u iphonebridge -n 50
```

Common culprits:
- Adapter MAC wrong in `src/iphonebridge/config.py`.
- iPhone not currently paired or not connected.
- `bluez-obexd` not installed (run step 1 above).
- The user-bus session isn't available (e.g. running under SSH without a graphical session) — set `XDG_RUNTIME_DIR` correctly.

### `btmgmt` hangs

If you try to set the CoD by hand while the daemon is running, `btmgmt class` will deadlock because the daemon's BLE advert is active. Stop the daemon first, or let the daemon do it for you at startup. See [`spike/RESULTS.md`](spike/RESULTS.md) §9.

## Architecture

```
                    iPhone (BR/EDR paired + bonded + trusted)
                                    │
              ┌───────────── BT Classic (OBEX, RFCOMM) ─┐
              │                                          │
        MAP MNS push                              PBAP PullAll
        (new-SMS events)                          (vCard transfer)
              │                                          │
              ↓                                          ↓
  ┌───────────────────────────────────────────────────────────────┐
  │              iphonebridge daemon (Python, GLib mainloop)      │
  │                                                                │
  │  obex/sessions.py   ←   long-lived MAP + PBAP sessions        │
  │  obex/map_events.py ←   InterfacesAdded → Message1.Get        │
  │  obex/bmessage.py   ←   parse VCARD originator + MSG body     │
  │  contacts.py        ←   SQLite cache, phone-number lookup     │
  │  bluez_setup.py     ←   CoD dance + BLE advert at startup     │
  │                                                                │
  │                          ↓ pub/sub                              │
  │                          ↓                                      │
  │           sinks/libnotify.py  ──→  GNOME notification           │
  │           sinks/jsonl.py      ──→  events.jsonl event log      │
  └───────────────────────────────────────────────────────────────┘
```

Key design decisions:

- **Single daemon, DBus boundary at daemon ↔ UI.** Most prior art (ancs4linux) splits into 3-4 separate daemons coordinated over DBus. We keep one process with internal pub/sub so the GLib mainloop and the BlueZ object state stay in one address space — simpler, fewer races. The DBus seam is reserved for future UI clients to subscribe to.
- **Python + dasbus + GLib + PyGObject.** Battle-tested combo; system packages (`python3-dbus`, `python3-gi`) instead of fragile PyPI builds.
- **System sessions persist across the daemon's lifetime.** iPhone refuses repeat OBEX connects within a short window — opening fresh sessions per query gets `0x43 Forbidden`. We open one MAP and one PBAP session at startup and keep them alive. See `spike/RESULTS.md` §2.
- **SMS body lives in `Subject`.** MAP's `Message1` properties put the SMS text in the `Subject` field, not a separate body field. Saves a roundtrip per message. See `spike/RESULTS.md` §3.
- **Event sinks are pluggable.** Adding a new output (DBus service for desktop widgets, webhook to a notification aggregator, etc.) is one new file in `src/iphonebridge/sinks/`.

## Configuration

Most settings live in `src/iphonebridge/config.py`. Environment overrides:

| Env var | Default | Purpose |
|---|---|---|
| `IPHONEBRIDGE_MAC` | hard-coded author's MAC | Target iPhone Bluetooth MAC |
| `IPHONEBRIDGE_ADAPTER` | `hci0` | Which local BT adapter |
| `XDG_STATE_HOME` | `~/.local/state` | Where events.jsonl + contacts.sqlite live |

## Roadmap

See [`BACKLOG.md`](BACKLOG.md) for the full list. Major upcoming work:

- **Phase 2a — ANCS for per-app notifications.** Mirror Slack, WhatsApp, Mail, iMessage *headers* (titles only, not bodies) over BLE. Requires solving the BR/EDR-vs-BLE pairing conflict. Likely path: dual-pair (same iPhone bonded over both modes) or a second BT adapter dedicated to BLE.
- **Phase 2b — MAP send.** SMS reply path. Document iMessage caveat explicitly (replies arrive as green bubbles).
- **Phase 2c — HFP HF role.** Accept incoming iPhone calls through laptop speakers/mic. Blocked on WirePlumber 1.5 config investigation.
- **Phase 3 — GTK4/libadwaita tray UI** and proper Flatpak packaging.

## Contributing

This is a one-person project right now. PRs welcome but expect slow review. Open an issue first if you want to discuss anything non-trivial.

### Development setup

After cloning + venv setup above:

```bash
pip install -e ".[dev]"   # adds pytest + ruff
ruff check src/
# pytest tests/   (no tests yet — see BACKLOG.md)
```

The Phase 0 spike scripts under `spike/` are the easiest way to test individual Bluetooth profile behavior in isolation. They're throwaway-quality but well-commented.

## Related projects

- [`pzmarzly/ancs4linux`](https://github.com/pzmarzly/ancs4linux) — the established ANCS-over-BLE implementation. Notifications-only, last commit May 2022. Patterns we plan to port for Phase 2a.
- [Microsoft Phone Link](https://learn.microsoft.com/en-us/windows/whats-new/whats-new-in-windows-11) — the Windows equivalent. Uses MAP/PBAP/HFP too but with polished UX backed by a full Windows team.
- [BlueBubbles](https://bluebubbles.app/) / [AirMessage](https://airmessage.org/) — Mac-relay-based iMessage on Linux. Different problem (requires a Mac), different protocol path.
- [Beeper](https://www.beeper.com/) — paid commercial iMessage bridge.

## License

GPL-2.0-or-later. See [`LICENSE`](LICENSE). Chosen for code-port compatibility with `ancs4linux` (whose GATT subscription patterns will be borrowed for Phase 2a).
