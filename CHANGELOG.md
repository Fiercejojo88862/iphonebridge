# Changelog

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
