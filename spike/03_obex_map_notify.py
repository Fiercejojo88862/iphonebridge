#!/usr/bin/env python3
"""03_obex_map_notify.py  —  Phase 0 step 03: MAP MNS push notifications.

Goal
----
Prove the iPhone PUSHES new-SMS events to us in real time. Without this,
the production app would have to poll MAP every few seconds, which is
slow and inefficient. With it, we get an instant signal the moment a new
SMS arrives.

How MNS works in BlueZ 5.x
--------------------------
After opening a MAP session and enabling the notification filter, BlueZ
transparently registers itself as an MNS server and accepts pushes from
the iPhone. New messages appear as new DBus objects implementing
org.bluez.obex.Message1 under the session path. BlueZ emits
InterfacesAdded signals via ObjectManager, which we can subscribe to.

Run
---
    python3 03_obex_map_notify.py 2>&1 | tee results/03_obex_map_notify.log

While running, send yourself an SMS from another phone. Watch for [+] NEW.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

IPHONE_MAC = "AA:BB:CC:DD:EE:FF"
RUN_S      = 180          # 3 minutes
FILTER_BIT_NEWMSG = 0x01  # MAP NotificationFilter: NEW_MESSAGE event

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()

def obex(path, iface):
    return dbus.Interface(bus.get_object("org.bluez.obex", path), iface)

# Restart obexd so we get a fresh session (iPhone refuses repeat connects
# in tight windows — proven in step 02).
print("[+] Restarting user obex.service for a clean session ...", flush=True)
subprocess.run(["systemctl", "--user", "restart", "obex.service"], check=True)
time.sleep(1)

print(f"[+] Creating MAP session to {IPHONE_MAC} ...", flush=True)
client = obex("/org/bluez/obex", "org.bluez.obex.Client1")
try:
    session_path = str(client.CreateSession(
        IPHONE_MAC, {"Target": "MAP"}, timeout=30.0
    ))
except dbus.DBusException as e:
    print(f"[FAIL] CreateSession: {e.get_dbus_name()}: {e.get_dbus_message()}",
          flush=True)
    sys.exit(2)
print(f"[+] Session: {session_path}", flush=True)

# Navigate to INBOX (some BlueZ versions require us to be in a folder
# before MNS events are routed correctly)
map_iface = obex(session_path, "org.bluez.obex.MessageAccess1")
try:
    map_iface.SetFolder("/")
except Exception:
    pass
for seg in ("telecom", "msg", "INBOX"):
    try:
        map_iface.SetFolder(seg)
    except dbus.DBusException as e:
        print(f"    SetFolder({seg}): {e.get_dbus_name()}", flush=True)

# Enable the NEW_MESSAGE notification event
print(f"[+] SetNotificationFilter (NEW_MESSAGE = 0x{FILTER_BIT_NEWMSG:02x}) ...",
      flush=True)
try:
    map_iface.SetNotificationFilter(dbus.UInt16(FILTER_BIT_NEWMSG))
    print("[+] Notification filter set.", flush=True)
except dbus.DBusException as e:
    # Some BlueZ versions don't have SetNotificationFilter, but MNS may
    # work by default. Don't fail.
    print(f"    (SetNotificationFilter unavailable: {e.get_dbus_name()}; "
          "trying without)", flush=True)

# ---- listen for new messages via ObjectManager InterfacesAdded -----------

om = dbus.Interface(bus.get_object("org.bluez.obex", "/"),
                    "org.freedesktop.DBus.ObjectManager")

new_message_count = 0

def on_interfaces_added(path, ifaces):
    global new_message_count
    if not str(path).startswith(session_path):
        return
    if "org.bluez.obex.Message1" not in ifaces:
        return
    props = dict(ifaces["org.bluez.obex.Message1"])
    new_message_count += 1
    print(f"\n[+] NEW MESSAGE #{new_message_count}  {path}", flush=True)
    for k in ("Subject", "Sender", "SenderAddress", "Timestamp",
              "Type", "Size", "Status", "Read"):
        if k in props:
            print(f"      {k}: {props[k]}", flush=True)

om.connect_to_signal("InterfacesAdded", on_interfaces_added)

print(f"""
[+] Listening for {RUN_S}s ({RUN_S//60} min). Send yourself an SMS from
    another phone now — the iPhone should push the event to us within ~2s.
    (iMessage replies WILL NOT appear here — only carrier SMS.)
""", flush=True)

loop = GLib.MainLoop()

def stop():
    print(f"\n[+] Stopping. New messages observed: {new_message_count}",
          flush=True)
    try: client.RemoveSession(session_path)
    except Exception: pass
    loop.quit()
    return False

GLib.timeout_add_seconds(RUN_S, stop)
signal.signal(signal.SIGINT, lambda *_: stop())

try:
    loop.run()
except KeyboardInterrupt:
    stop()

if new_message_count > 0:
    print(f"\n[VERDICT] PASS — MAP MNS works. {new_message_count} push events "
          f"observed.", flush=True)
    sys.exit(0)
else:
    print("\n[VERDICT] NO EVENTS within window. Either:"
          "\n  (a) No SMS arrived — re-run and send a real text from"
          "\n      another phone (not iMessage, must be carrier SMS),"
          "\n  (b) BlueZ MNS server didn't register properly,"
          "\n  (c) iPhone needs additional toggle / unlock to push.", flush=True)
    sys.exit(10)
