# iphonebridge — Backlog

Park ideas here so they don't derail Phase 1.

## Phase 1 polish (after MVP works)
- [ ] Reconnect-on-suspend-resume logic (laptop sleep breaks BT session)
- [ ] Notification dismissal sync — when user dismisses libnotify popup, mark SMS read via MAP `Message1.Status = "read"`
- [ ] First-run pairing wizard (CLI) — guide user through iPhone-side toggles
- [ ] `iphonebridge sms list` — recent inbox dump
- [ ] `iphonebridge doctor` — check BlueZ, obexd, sessions, toggles
- [ ] Better contact resolution for international numbers (E.164 normalization)

## Phase 2
- [ ] **PBAP**: already proven in Phase 0, integrated in Phase 1
- [ ] **MAP send** (`Message1.PushMessage`): the SMS reply path. Document iMessage caveat clearly.
- [ ] **ANCS** as a side experiment: try BLE-only pairing, see if it can coexist with BR/EDR
- [ ] **HFP HF role**: needs WirePlumber config investigation; possibly oFono backend
- [ ] GTK4 / libadwaita tray + conversation window

## Phase 3 / nice-to-have
- [ ] Encrypted SQLite for message cache
- [ ] Multi-device support (currently hard-coded to one iPhone MAC)
- [ ] Flatpak packaging
- [ ] iOS version regression test matrix
- [ ] DBus service `com.gabriel.IPhoneBridge` so other UIs can subscribe to events

## Won't do
- iMessage send/read (Apple-locked; needs Mac relay or paid Beeper)
- Per-app reply (ANCS is read-only, no protocol path)
- Group MMS / RCS
- Outgoing calls from laptop (HFP HF role can't reliably ATD on iPhone)
