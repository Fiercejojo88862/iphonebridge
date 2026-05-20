# Changelog

## [0.1.0] — 2026-05-19

First tagged release. Working iphonebridge daemon on Pop!_OS 24.04
against iPhone 16 Pro Max running iOS 26.5.

### Confirmed working
- Real-time SMS + iMessage notifications via MAP MNS push
- Outgoing SMS + iMessage send via MAP `PushMessage` — iOS auto-routes
  as iMessage when the recipient is iMessage-capable
- 1000+ contacts pulled via PBAP, cached in SQLite, name-resolved for
  incoming messages
- systemd user service for autostart, graceful degradation when iPhone
  toggles are off, automatic retry every 60s
- DBus service `com.gabriel.iphonebridge.Messages1` with Send,
  ListRecent, IsHealthy methods
- CLI: `run`, `doctor`, `pair-setup`, `sms-list`, `sms-send`,
  `contacts-sync`, `version`

### Documented constraints (won't change)
- No iMessage attachments / reactions / read receipts / typing
  indicators (MAP doesn't expose them)
- No group iMessage / MMS / RCS (MAP is 1:1 only)
- No per-app notifications (ANCS over BLE, would need a second BT
  adapter — Phase 2a)
- No outgoing call audio routing (HFP HF role — Phase 2c)

## [Unreleased]

### Project-defining discoveries (2026-05-19, post-launch)

- **Incoming iMessage IS exposed via MAP on iOS 26.5 / iPhone 16 Pro Max**, labeled as `Type: sms-gsm` indistinguishably from SMS. This contradicts every prior Bluetooth-on-Linux writeup. Verified: sender (Contact B, confirmed iMessage thread, both on iPhone) sent "test-iphonebridge-XYZ123" → daemon received and rendered the body within ~2s.

- **Outgoing iMessage via MAP `PushMessage` ALSO works.** Tested via `spike/07_map_send.py`: constructed a minimal bMessage (originator + BENV-wrapped recipient VCARD), called `MessageAccess1.PushMessage(sourcefile, "telecom/msg/outbox", {})` — transfer completed, the iPhone's outgoing bubble appeared **blue** (iMessage) in the recipient thread.

Together: **iphonebridge is potentially the first free open-source Linux iMessage bridge that does not require a Mac relay**. README, BACKLOG, RESULTS.md updated accordingly.

### Phase 1 — MVP daemon (2026-05-19)
- Working iphonebridge daemon: BLE-advert / CoD startup dance, long-lived MAP + PBAP sessions, MAP MNS push subscription, bMessage parsing, SQLite contacts cache, libnotify + JSONL sinks.
- Typer CLI: `run`, `doctor`, `sms-list`, `contacts-sync`, `version`.
- systemd user service for auto-start.
- sudoers.d entry (`install-cod-sudoers.sh`) for passwordless `btmgmt class 4 8` so CoD survives reboots.
- End-to-end verified: SMS from a known contact arrives as a GNOME desktop notification within ~20 ms of the iPhone push.

### Phase 0 — Empirical spike (2026-05-19)
- Confirmed against iPhone 16 Pro Max / iOS 26.5: MAP read ✓, MAP MNS push ✓, PBAP (1957 contacts) ✓, HFP HF role partial (needs WirePlumber config work), ANCS deferred (needs BLE-only pairing flow incompatible with the BR/EDR pair MAP/PBAP need).
- Documented non-obvious findings in `spike/RESULTS.md`: the hidden-toggle dance, single-OBEX-session-per-fresh-obexd, SMS body in `Subject`, PBAP `Select` vs `SetFolder`, BR/EDR-vs-BLE pairing mutex for ANCS.
