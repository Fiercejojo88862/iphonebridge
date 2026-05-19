#!/usr/bin/env python3
"""04_obex_pbap.py  —  Phase 0 step 04: pull the iPhone's phonebook via PBAP.

Same OBEX-over-Bluetooth-Classic plumbing as MAP, different Target string.
The "Show Message Notifications" toggle on the iPhone may also gate PBAP
(it likely covers all OBEX targets to this paired adapter).

Run:
    python3 04_obex_pbap.py 2>&1 | tee results/04_obex_pbap.log
"""
from __future__ import annotations

import sys
import time
import tempfile
from pathlib import Path

import dbus
from dbus.mainloop.glib import DBusGMainLoop

IPHONE_MAC = "AA:BB:CC:DD:EE:FF"
TIMEOUT_S  = 30.0
HOW_MANY   = 50    # ask for first 50 contacts so we don't pull 2000 entries

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()

def obex(path, iface):
    return dbus.Interface(bus.get_object("org.bluez.obex", path), iface)

def fail(code, msg):
    print(f"[FAIL] {msg}", flush=True)
    sys.exit(code)

# Restart obexd first — iPhone allows one PBAP session per fresh daemon
# (same single-session-per-daemon constraint we observed for MAP).
import subprocess
print("[+] Restarting user obex.service for a clean session ...", flush=True)
subprocess.run(["systemctl", "--user", "restart", "obex.service"], check=True)
time.sleep(1)

print(f"[+] Creating PBAP session to {IPHONE_MAC} ...", flush=True)
client = obex("/org/bluez/obex", "org.bluez.obex.Client1")
try:
    session_path = str(client.CreateSession(
        IPHONE_MAC, {"Target": "PBAP"}, timeout=TIMEOUT_S
    ))
except dbus.DBusException as e:
    fail(2, f"CreateSession PBAP: {e.get_dbus_name()}: {e.get_dbus_message()}")

print(f"[+] Session: {session_path}", flush=True)

try:
    pbap = obex(session_path, "org.bluez.obex.PhonebookAccess1")

    # PBAP uses Select(location, phonebook) — location="int" (internal),
    # phonebook="pb" (main phonebook). Not SetFolder like MAP.
    print("[+] Select(int, pb) ...", flush=True)
    try:
        pbap.Select("int", "pb")
    except dbus.DBusException as e:
        fail(3, f"Select: {e.get_dbus_name()}: {e.get_dbus_message()}")

    # PullAll downloads the whole phonebook to a file. We cap with MaxListCount.
    out = Path(tempfile.mkdtemp(prefix="iphone_pb_")) / "pb.vcf"
    print(f"[+] PullAll to {out} (max {HOW_MANY}) ...", flush=True)
    try:
        ret = pbap.PullAll(
            str(out),
            {"MaxListCount": dbus.UInt16(HOW_MANY),
             "Format": dbus.String("Vcard30")},
        )
        transfer_path = str(ret[0]) if isinstance(ret, (tuple, list)) else str(ret)
    except dbus.DBusException as e:
        fail(4, f"PullAll: {e.get_dbus_name()}: {e.get_dbus_message()}")

    # Poll transfer to completion
    tprops = obex(transfer_path, "org.freedesktop.DBus.Properties")
    status = None
    for _ in range(200):  # up to 20s
        try:
            status = str(tprops.Get("org.bluez.obex.Transfer1", "Status"))
        except dbus.DBusException:
            status = "gone"
            break
        if status in ("complete", "error"):
            break
        time.sleep(0.1)
    print(f"[+] Transfer status: {status}", flush=True)

    if not (out.exists() and out.stat().st_size > 0):
        fail(5, f"No phonebook file written to {out}")

    raw = out.read_text(errors="replace")
    # Each contact is a BEGIN:VCARD ... END:VCARD block
    cards = [c for c in raw.split("BEGIN:VCARD") if "END:VCARD" in c]
    print(f"\n[+] Phonebook file: {out.stat().st_size} bytes, "
          f"{len(cards)} contact entries parsed", flush=True)

    # Show first 5 contacts (name + first phone number)
    for i, c in enumerate(cards[:5]):
        fn = next((ln[3:].strip() for ln in c.splitlines()
                   if ln.startswith("FN:")), "?")
        tel = next((ln.split(":", 1)[-1].strip() for ln in c.splitlines()
                    if ln.startswith("TEL")), "?")
        print(f"    {i+1}. FN={fn!r}  TEL={tel!r}", flush=True)

    print(f"\n[VERDICT] PASS — PBAP works. Contacts pull successful "
          f"({len(cards)} entries).", flush=True)

finally:
    try:
        client.RemoveSession(session_path)
        print("[+] Session removed.", flush=True)
    except Exception as e:
        print(f"[WARN] RemoveSession: {e}", flush=True)
