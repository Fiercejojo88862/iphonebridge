# iOS Version Regression Matrix

Track Bluetooth profile behavior across iOS versions and hardware. The `spike/RESULTS.md:1` scoreboard is the source of truth for 26.5; this file extends it incrementally without re-running the full Phase 0 each time.

## Matrix

| iOS | Device | MAP read | MAP MNS push | PBAP | ANCS | HFP HF (oFono) | Toggles visible | Notes |
|---|---|---|---|---|---|---|---|---|
| 26.5 | iPhone 16 Pro Max (Pop!_OS 24.04, BlueZ 5.72, AX) | ✅ PASS | ✅ PASS | ✅ PASS (1291) | ⚠ DEFER → ✅ via `LastUsedBearer=le` | ✅ PASS (3/3 dial) | Show Message Notifications / Sync Contacts only after CoD=4/8 + ANCS advert | Baseline in `spike/RESULTS.md:1` |
| 27.0 dev beta | iPhone 13 Pro (your device) | ☐ TODO | ☐ TODO | ☐ TODO | ☐ TODO | ☐ TODO | ☐ TODO | **Current regression target** — run `spike/08_ios27_regression.py` |

Legend: ✅ PASS / ❌ FAIL / ⚠ PARTIAL / ☐ TODO. For ANCS, Intel AX required; Realtek/USB dongles never pass per `README.md:56`.

## What to check on iOS 27.0 dev beta (iPhone 13 Pro)

1. **Pair fresh** — `bluetoothctl` or GNOME Settings → Bluetooth. iPhone should appear as phone icon.
2. **Run `iphonebridge pair-setup`** — writes `~/.config/iphonebridge/local.env:1` with `IPHONEBRIDGE_MAC`.
3. **Daemon toggle dance** — `iphonebridge run` or `systemctl --user start iphonebridge` must set CoD `0x240408` (`bluez_setup.py:59`) and register BLE advert `7905F431-B5CE-4E99-A40F-4B1E122D00D0`. On iPhone: Settings → Bluetooth → (i) → check **Show Message Notifications**, **Sync Contacts**, **Show System Notifications** appear.
4. **Profiles** (use `spike/08_ios27_regression.py`):
   - `02_obex_map_session.py` → MAP read: pull 10 handles, body in `Subject`.
   - `03_obex_map_notify.py` → MNS push: send SMS to phone, expect `InterfacesAdded` within ~5s.
   - `04_obex_pbap.py` → PBAP: `PullAll` count and vCard parse.
   - `07_map_send.py` → MAP `PushMessage` to `+1555...`, verify bubble color (iMessage vs SMS).
   - `05b_hfp_ofono.py` → HFP: `VoiceCallManager`, `CallAdded`, `Answer`/`Hangup`, `Dial` 3/3.
   - `01_ancs_subscribe.py` → ANCS: `LastUsedBearer=le` helper first (`iphonebridge ancs-enable`), then watch for 3 UUIDs under `dev_XX`.

## How to run the regression

```bash
# 1. Update MAC for this phone (replaces 16 Pro Max placeholder)
iphonebridge pair-setup  # pick iPhone 13 Pro from list

# 2. One-shot regression (no daemon running — it restarts obexd per session)
python spike/08_ios27_regression.py --mac AA:BB:CC:DD:EE:FF --out spike/results/ios27_iphone13pro.json

# 3. Or live daemon + manual SMS
systemctl --user restart iphonebridge
journalctl --user -u iphonebridge -f &
# send SMS to the phone from another number, watch for:
#   new Message1 at ... — fetching body
#   sms_received from ...
```

Results go to `spike/results/ios27_iphone13pro.json` and this table. Copy the JSON + update the row above. If any profile flips ❌, open an issue with `spike/results/*.log` attached.

## Known deltas to watch for on iOS 27.0 beta

- Apple may gate toggles differently (new name or extra entitlement).
- MAP `Type: sms-gsm` for iMessage may change if Apple splits the label.
- ANCS `LastUsedBearer=le` path is fragile across BlueZ 5.72 → 5.8x; watch `journalctl -u bluetooth` for `P-192` CTKD failures on non-Intel adapters.
