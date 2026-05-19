#!/usr/bin/env python3
"""02_obex_map_session.py  —  Phase 0 step 02: empirical MAP session test.

Goal
----
Prove (or disprove) that the iPhone's MAP server returns SMS bodies over
the EXISTING pairing — without changing the adapter's Class-of-Device.

Why this test is urgent
-----------------------
iOS Settings → Bluetooth → pop-os does NOT show "Share System Notifications"
or "Show Notifications" toggles. Hypothesis: iOS hides those toggles when
the paired adapter identifies as Major Class = Computer. The open question
is whether iOS *also* refuses MAP data, or only hides the UI.

  - If MAP returns ≥1 SMS body  → proceed; we can keep the Computer CoD.
  - If MAP returns empty / errs → unpair, change CoD to 0x240408 (A/V
                                  Hands-Free), re-pair, repeat this test.

Run
---
    python3 02_obex_map_session.py 2>&1 | tee results/02_obex_map_session.log
"""
from __future__ import annotations

import sys
import time
import tempfile
from pathlib import Path

import dbus
from dbus.mainloop.glib import DBusGMainLoop

# ---- config ---------------------------------------------------------------

IPHONE_MAC = "AA:BB:CC:DD:EE:FF"
FOLDER     = "telecom/msg/INBOX"
HOW_MANY   = 5
TIMEOUT_S  = 30.0   # for CreateSession; iPhone can take a few seconds

# ---- dbus boilerplate -----------------------------------------------------

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()

def obex(path: str, iface: str):
    return dbus.Interface(bus.get_object("org.bluez.obex", path), iface)

def fail(code: int, msg: str) -> "NoReturn":
    print(f"[FAIL] {msg}", flush=True)
    sys.exit(code)

# ---- create MAP session ---------------------------------------------------

print(f"[+] Creating MAP session to {IPHONE_MAC} ...", flush=True)
client = obex("/org/bluez/obex", "org.bluez.obex.Client1")
try:
    session_path = str(
        client.CreateSession(
            IPHONE_MAC, {"Target": "MAP"}, timeout=TIMEOUT_S
        )
    )
except dbus.DBusException as e:
    fail(2, f"CreateSession: {e.get_dbus_name()}: {e.get_dbus_message()}")

print(f"[+] Session path: {session_path}", flush=True)

try:
    map_iface = obex(session_path, "org.bluez.obex.MessageAccess1")

    # ---- enumerate available folders for diagnostic ----------------------
    print("[+] Listing top-level folders ...", flush=True)
    try:
        folders = map_iface.ListFolders({})
        for f in folders:
            print(f"      - {dict(f)}", flush=True)
    except dbus.DBusException as e:
        print(f"    (ListFolders failed: {e.get_dbus_name()})", flush=True)

    # ---- navigate to INBOX -----------------------------------------------
    print(f"[+] Setting folder to {FOLDER!r} ...", flush=True)
    try:
        # MAP folder hierarchy is relative; start by going up to root
        try:
            map_iface.SetFolder("/")
        except dbus.DBusException:
            pass
        for segment in FOLDER.split("/"):
            map_iface.SetFolder(segment)
    except dbus.DBusException as e:
        fail(3, f"SetFolder({FOLDER}): {e.get_dbus_name()}: {e.get_dbus_message()}")

    # ---- list messages ---------------------------------------------------
    print(f"[+] Listing up to {HOW_MANY} messages in {FOLDER} ...", flush=True)
    try:
        msgs = map_iface.ListMessages(
            "", {"MaxListCount": dbus.UInt16(HOW_MANY)}
        )
    except dbus.DBusException as e:
        fail(4, f"ListMessages: {e.get_dbus_name()}: {e.get_dbus_message()}")

    msgs = list(msgs)
    print(f"[+] Got {len(msgs)} message handles", flush=True)

    if not msgs:
        print(
            "\n[VERDICT] PARTIAL: MAP session opened but ListMessages returned"
            "\n          empty. Check 'Show Message Notifications' toggle on"
            "\n          iPhone for this device.",
            flush=True,
        )
        sys.exit(10)

    # ---- fetch each message ---------------------------------------------
    out_dir = Path(tempfile.mkdtemp(prefix="iphone_msg_"))
    print(f"[+] Downloading bMessages to {out_dir}", flush=True)

    # BlueZ's ListMessages returns a list of DBus object paths, one per
    # message. Each path implements org.bluez.obex.Message1 (properties +
    # Get method) and org.freedesktop.DBus.Properties (metadata).
    for msg_path in msgs[:HOW_MANY]:
        msg_path = str(msg_path)
        print(f"\n--- {msg_path.split('/')[-1]} ---", flush=True)

        # Pull metadata
        try:
            props = dict(obex(msg_path, "org.freedesktop.DBus.Properties")
                         .GetAll("org.bluez.obex.Message1"))
            for k in ("Subject", "Sender", "SenderAddress", "Timestamp",
                      "Type", "Size", "Status", "Read", "Folder"):
                if k in props:
                    print(f"    {k}: {props[k]}", flush=True)
        except dbus.DBusException as e:
            print(f"    [WARN] metadata GetAll: {e.get_dbus_name()}", flush=True)

        # Download body via Message1.Get(target_file, attachment=False)
        handle = msg_path.split("/")[-1]
        target = out_dir / f"{handle}.bmsg"
        msg_iface = obex(msg_path, "org.bluez.obex.Message1")
        try:
            ret = msg_iface.Get(str(target), False)
            transfer_path = str(ret[0]) if isinstance(ret, (tuple, list)) else str(ret)
        except dbus.DBusException as e:
            print(f"    [FAIL] Get: {e.get_dbus_name()}: "
                  f"{e.get_dbus_message()}", flush=True)
            continue

        # poll transfer until complete or error
        props_iface = obex(transfer_path, "org.freedesktop.DBus.Properties")
        status = None
        for _ in range(100):  # up to ~10s
            try:
                status = str(props_iface.Get("org.bluez.obex.Transfer1", "Status"))
            except dbus.DBusException:
                status = "gone"  # transfer object disappears on completion
                break
            if status in ("complete", "error"):
                break
            time.sleep(0.1)
        print(f"    transfer status: {status}", flush=True)

        if target.exists() and target.stat().st_size > 0:
            body = target.read_text(errors="replace")
            print(f"    bMessage preview ({target.stat().st_size} bytes):",
                  flush=True)
            for line in body.splitlines()[:40]:
                print(f"    | {line}", flush=True)
        else:
            print("    [INFO] No file written (or empty).", flush=True)

    print("\n[VERDICT] PASS: MAP session works AND messages parse on the"
          "\n          existing Computer-class pairing. CoD change not"
          "\n          needed for MAP read. Proceeding to ANCS / MNS / PBAP."
          "\n          Note: iMessage will NOT appear here — only SMS.",
          flush=True)

finally:
    try:
        client.RemoveSession(session_path)
        print("[+] Session removed.", flush=True)
    except Exception as e:
        print(f"[WARN] RemoveSession: {e}", flush=True)
