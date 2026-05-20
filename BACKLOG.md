# iphonebridge — Backlog

Park ideas here so they don't derail Phase 1.

## Phase 1 polish (after MVP works)
- [ ] Reconnect-on-suspend-resume logic (laptop sleep breaks BT session)
- [ ] Notification dismissal sync — when user dismisses libnotify popup, mark SMS read via MAP `Message1.Status = "read"`
- [ ] First-run pairing wizard (CLI) — guide user through iPhone-side toggles
- [ ] `iphonebridge sms list` — recent inbox dump
- [ ] `iphonebridge doctor` — check BlueZ, obexd, sessions, toggles
- [ ] Better contact resolution for international numbers (E.164 normalization)

## Phase 2 (revised after iMessage-over-MAP discovery 2026-05-19)

- [ ] **MAP send** (`Message1.PushMessage`) — **PROMOTED to top priority**. With iMessage now confirmed working on the *read* side, the question is whether the *write* side also routes as iMessage when the recipient is iMessage-capable. If yes: free open-source Linux iMessage bridge, no Mac relay needed.
- [ ] **Graceful toggle-disabled handling** — when iPhone toggles are off, daemon currently crash-loops via systemd. Should log + back off + wait, not die.
- [ ] **First-run pair-setup wizard** — guide new users through CoD sudoers install + iPhone-side toggles.
- [ ] **Notification dismissal sync** — dismissing a libnotify popup → mark MAP `Message1.Status = "read"` on the iPhone.
- [ ] **`iphonebridge sms list` from MAP, not just JSONL** — pull recent inbox on demand via the live MAP session.
- [ ] **ANCS** for per-app notifications (Slack/WhatsApp/etc.) — deprioritized since iMessage already comes through. Path forward is a second BT adapter (~$10 USB dongle). Phase 0 confirmed iOS combines BR/EDR+BLE on same MAC into a single BR/EDR bond.
- [ ] **HFP HF role** — needs WirePlumber 1.5 config investigation; possibly oFono backend.
- [ ] GTK4 / libadwaita tray + conversation window

## Phase 3 / nice-to-have
- [ ] Encrypted SQLite for message cache
- [ ] Multi-device support (currently hard-coded to one iPhone MAC)
- [ ] Flatpak packaging
- [ ] iOS version regression test matrix
- [ ] DBus service `com.gabriel.IPhoneBridge` so other UIs can subscribe to events

## Won't do
- ~~iMessage send/read~~ — *update 2026-05-19: iMessage *read* works via MAP on iOS 26.5! Send TBD.*
- Per-app reply (ANCS is read-only, no protocol path)
- Group iMessage / MMS / RCS (1:1 only)
- Outgoing calls from laptop (HFP HF role can't reliably ATD on iPhone)
- Read receipts, typing indicators, message reactions, full attachments
