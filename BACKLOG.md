# iphonebridge — Backlog

Park ideas here so they don't derail Phase 1.

## Phase 1 polish (after MVP works)
- [x] Reconnect-on-suspend-resume logic (laptop sleep breaks BT session) — **DONE 2026-08-29.** Daemon listens for `org.freedesktop.login1.Manager PrepareForSleep` + BlueZ `Device1 Connected` and re-opens MAP/PBAP after `RESUME_RECONNECT_DELAY_SEC=8` (`daemon.py:_setup_resume_handler`).
- [x] Notification dismissal sync — **DONE 2026-05-20.** `sinks/libnotify.py:214` handles both directions (dismiss → mark-read via `Message1.Read`, iPhone read → close popup; see module docstring for persistence model).
- [ ] First-run pairing wizard (CLI) — guide user through iPhone-side toggles
- [x] `iphonebridge sms list` — **DONE 2026-05-20.** Live MAP via daemon `Messages1.ListRecent` (`cli.py:sms_list` `--source iphone`, default) with `--from` filter and fallback to local JSONL; see `obex/map_query.py:1`.
- [x] `iphonebridge doctor` — **DONE 2026-08-29.** Enhanced `cli.py:doctor` checks MAC, Adapter CoD+Powered, iPhone paired/trusted/connected, obexd active, contacts count, daemon D-Bus `IsHealthy`, HFP (oFono + WirePlumber) (`bluez_setup.py`, `pair_setup.py`).
- [x] Better contact resolution for international numbers (E.164 normalization) — **DONE 2026-08-29.** `events.py:normalize_phone` now handles `00` prefix, extensions, 7-15 digit bounds; `contacts.py:225` `resolve()` strips trunk `0` / NANP `1` and matches on longest suffix (≥7) for UK/FR/US interop (`tests/test_events.py`, manual i18n checks).

## Phase 2 (revised after iMessage-over-MAP discovery 2026-05-19)

- [x] **MAP send / iMessage send** (`MessageAccess1.PushMessage`) — **CONFIRMED WORKING 2026-05-19 via spike/07_map_send.py**. iOS routes outgoing to iMessage-capable recipients as iMessage (blue bubble). iphonebridge is now read+send. NEXT: build a proper `iphonebridge sms-send <number> <body>` CLI command backed by a daemon DBus method (so we don't have to stop/restart the daemon to free the MAP session per send).
- [x] **Graceful toggle-disabled handling** — **DONE 2026-05-19.** Daemon stays alive in DEGRADED mode with 60s retry and remediation hint (`daemon.py:_try_open_sessions`). No crash-loop.
- [x] **First-run pair-setup wizard** — **DONE 2026-05-20.** `pair_setup.py:87` enumerates paired devices, trusts, writes `local.env`, walks iPhone toggles, optionally restarts daemon; also covers CoD sudoers hint.
- [x] **Notification dismissal sync** — **DONE 2026-05-20.** Duplicate of Phase 1 item — same `sinks/libnotify.py` implementation (see above).
- [ ] **`iphonebridge sms list` from MAP, not just JSONL** — pull recent inbox on demand via the live MAP session.
- [ ] **ANCS** for per-app notifications (Slack/WhatsApp/etc.) — deprioritized since iMessage already comes through. **Update 2026-05-20:** The fork [bmh129/ancs4linux](https://github.com/bmh129/ancs4linux) is actively developing fixes for the exact BR/EDR-vs-BLE coexistence issue our Phase 0 found. Key commit `0db80f3` fixes `_trigger_gatt_discovery` to probe ANCS UUIDs in the DBus tree instead of trusting `ServicesResolved`, plus uses `LastUsedBearer=le` to bias toward BLE reconnects. **No USB BT dongle needed** — bmh129 explicitly documents that no tested USB adapter works with ANCS on Linux (Realtek firmware uses P-192 keys, blocking CTKD). Our Intel-chipset adapter is the recommended hardware. Phase 2a path: vendor or port their fix into iphonebridge.
- [x] **`iphonebridge sms list` from MAP, not just JSONL** — **DONE 2026-05-20.** Duplicate of Phase 1 `sms list` — live MAP path already ships (see above).
- [x] **HFP HF role** — **DONE 2026-05-20.** Take *and* place iPhone calls on
  the laptop via oFono (`org.ofono`) for call control + PipeWire's oFono HFP
  backend for SCO audio. Caller ID, answer/decline, dialing, all confirmed
  (spike `05b_hfp_ofono.py`, `spike/RESULTS.md` HFP addendum). CLI: `call`,
  `hangup`, `calls`, `hfp-enable`.
- [x] **GTK4 / libadwaita app** — **DONE 2026-05-20.** Standalone `iphonebridge-ui`
  (separate process, talks to the daemon over D-Bus): conversations, ANCS
  notification feed, call UI, and a setup/status page. Daemon gained an
  `Events1` D-Bus signal interface for the UI to subscribe to.

## Phase 3 / nice-to-have
- [ ] Encrypted SQLite for message cache
- [ ] Multi-device support (currently hard-coded to one iPhone MAC)
- [x] Flatpak packaging — **DONE 2026-08-29.** UI ported to Gio GDBus (no `dbus-python` / system-bus), manifest no longer vendors `python3-dbus` and is ready to build (`packaging/flatpak/com.gabriel.iphonebridge.UI.yml`, `packaging/flatpak/README.md`).
- [ ] iOS version regression test matrix
- [ ] DBus service `com.gabriel.IPhoneBridge` so other UIs can subscribe to events

## Won't do
- ~~iMessage send/read~~ — *update 2026-05-19: iMessage *read* works via MAP on iOS 26.5! Send TBD.*
- Per-app reply (ANCS is read-only, no protocol path)
- Group iMessage / MMS / RCS (1:1 only)
- ~~Outgoing calls from laptop~~ — *update 2026-05-20: WRONG. HFP HF `Dial`
  rings the target reliably (3/3 in spike 05b). Outgoing calls now ship.*
- Read receipts, typing indicators, message reactions, full attachments
