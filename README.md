# iphonebridge

Linux desktop bridge from a paired iPhone over Bluetooth. SMS, real-time message push notifications, and contacts on Pop!_OS / GNOME. Built because Microsoft's Phone Link works on Windows but nothing equivalent exists on Linux.

**Status:** Alpha. Phase 1 in progress. See [`spike/RESULTS.md`](spike/RESULTS.md) for Phase 0 findings against iPhone 16 Pro Max / iOS 26.5.

## What it does (Phase 1 target)

- **Real-time SMS notifications** — desktop popup the moment an SMS arrives on the iPhone, with sender name (resolved from your phone's contacts) and body.
- **SMS history readable from CLI** — `iphonebridge sms list` dumps your recent inbox.
- **Contacts cached locally** — phonebook pulled from the iPhone via PBAP, stored in SQLite for fast number-to-name resolution.

## What it explicitly does *not* do (and will never)

These limits come from Apple's Bluetooth stack, not Linux.

- **No iMessage.** iPhone's MAP server exposes SMS only. iMessage threads are invisible. Replies sent via MAP land as green-bubble SMS.
- **No per-app notification mirroring** in Phase 1. (ANCS — every-app notification source — is deferred; it requires a different iPhone pairing strategy that conflicts with the BR/EDR pair MAP/PBAP need.)
- **No outgoing call audio routing** in Phase 1 (HFP HF role is Phase 2c).

## Requirements

- Pop!_OS 24.04 / Ubuntu 24.04 (GNOME, BlueZ 5.72+, PipeWire 1.x)
- `bluez-obexd` installed (`sudo apt install bluez-obexd`)
- Python 3.10+
- An iPhone running iOS 16.5+ (tested against 26.5 on iPhone 16 Pro Max)

## Setup

```bash
cd ~/code/iphonebridge

# 1. Create a venv that shares the system PyGObject + dbus-python
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .

# 2. Verify prerequisites
iphonebridge doctor

# 3. (One-time) Let the daemon set the adapter CoD on every start without
#    a password prompt. Without this, after each reboot you'd have to
#    manually run `sudo btmgmt class 4 8` before the daemon could open
#    MAP/PBAP. (See spike/RESULTS.md §1 for why this CoD is load-bearing.)
sudo bash systemd/install-cod-sudoers.sh

# 4. Install + start the daemon as a systemd user service
mkdir -p ~/.config/systemd/user
cp systemd/iphonebridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now iphonebridge

# 5. iPhone-side: Settings → Bluetooth → tap (i) next to pop-os,
#    enable "Show Message Notifications" + "Sync Contacts".
```

## Daily usage

```bash
# Tail the daemon log
journalctl --user -u iphonebridge -f

# Show recent SMS events (read from local JSONL log)
iphonebridge sms-list -n 20

# Force a fresh contact pull (otherwise auto-refreshes every 24h)
iphonebridge contacts-sync

# Stop / start
systemctl --user stop iphonebridge
systemctl --user start iphonebridge
```

## Architecture

```
iPhone (BR/EDR pair)
  ├─ MAP MNS → push events
  └─ PBAP → vCards (cached)
        │
        ↓
   iphonebridge daemon (Python, GLib mainloop)
        │
        ↓
   sinks: libnotify popup • JSONL event log • (later) DBus service
```

See [`spike/RESULTS.md`](spike/RESULTS.md) for the empirical constraints encoded into this design.

## License

GPL-2.0-or-later, matching `pzmarzly/ancs4linux` (whose ANCS subscription patterns we'd port in a future Phase).
