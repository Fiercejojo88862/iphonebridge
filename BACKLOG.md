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

- [x] **MAP send / iMessage send** (`MessageAccess1.PushMessage`) — **CONFIRMED WORKING 2026-05-19 via spike/07_map_send.py**. iOS routes outgoing to iMessage-capable recipients as iMessage (blue bubble). iphonebridge is now read+send. NEXT: build a proper `iphonebridge sms-send <number> <body>` CLI command backed by a daemon DBus method (so we don't have to stop/restart the daemon to free the MAP session per send).
- [ ] **Graceful toggle-disabled handling** — when iPhone toggles are off, daemon currently crash-loops via systemd. Should log + back off + wait, not die.
- [ ] **First-run pair-setup wizard** — guide new users through CoD sudoers install + iPhone-side toggles.
- [ ] **Notification dismissal sync** — dismissing a libnotify popup → mark MAP `Message1.Status = "read"` on the iPhone.
- [ ] **`iphonebridge sms list` from MAP, not just JSONL** — pull recent inbox on demand via the live MAP session.
- [ ] **ANCS** for per-app notifications (Slack/WhatsApp/etc.) — deprioritized since iMessage already comes through. **Update 2026-05-20:** The fork [bmh129/ancs4linux](https://github.com/bmh129/ancs4linux) is actively developing fixes for the exact BR/EDR-vs-BLE coexistence issue our Phase 0 found. Key commit `0db80f3` fixes `_trigger_gatt_discovery` to probe ANCS UUIDs in the DBus tree instead of trusting `ServicesResolved`, plus uses `LastUsedBearer=le` to bias toward BLE reconnects. **No USB BT dongle needed** — bmh129 explicitly documents that no tested USB adapter works with ANCS on Linux (Realtek firmware uses P-192 keys, blocking CTKD). Our Intel-chipset adapter is the recommended hardware. Phase 2a path: vendor or port their fix into iphonebridge.
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
