#!/usr/bin/env python3
"""08_ios27_regression.py — iOS 27.0 dev beta regression on iPhone 13 Pro.

Aggregates the Phase-0 spike checks into one JSON report so the
`docs/ios-matrix.md` row can be filled without re-running each spike
manually. Each profile is probed via the same BlueZ OBEX / oFono paths
the daemon uses; failures are captured as structured JSON, not as
process exit codes, so a partial run still produces a useful report.

Usage:
  python spike/08_ios27_regression.py --mac AA:BB:CC:DD:EE:FF --out spike/results/ios27_iphone13pro.json
  # Or rely on IPHONEBRIDGE_MAC / local.env:
  python spike/08_ios27_regression.py --out spike/results/ios27_iphone13pro.json

Requires: paired iPhone 13 Pro on iOS 27.0 beta, BlueZ 5.72+, obexd,
          python3-dbus + python3-gi (system packages), oFono for HFP.

See docs/ios-matrix.md:1 for the matrix and manual toggle checklist.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import dbus
from dbus.mainloop.glib import DBusGMainLoop

DBusGMainLoop(set_as_default=True)
from iphonebridge import config as cfg  # noqa: E402  (after DBusGMainLoop)


@dataclass(slots=True)
class Check:
    name: str
    status: str  # PASS / FAIL / SKIP / PARTIAL
    detail: str
    extra: dict | None = None


def _obex(path: str, iface: str):
    return dbus.Interface(
        dbus.SessionBus().get_object("org.bluez.obex", path), iface
    )


def _bluez(path: str, iface: str):
    return dbus.Interface(
        dbus.SystemBus().get_object("org.bluez", path), iface
    )


def check_mac_placeholder(mac: str) -> Check:
    if mac.upper() in ("AA:BB:CC:DD:EE:FF", ""):
        return Check("config.MAC", "FAIL", "IPHONEBRIDGE_MAC still placeholder")
    return Check("config.MAC", "PASS", f"MAC={mac}")


def check_adapter_cod() -> Check:
    try:
        from iphonebridge.bluez_setup import current_cod, desired_cod_matches
        cod = current_cod()
        if cod is None:
            return Check("adapter.CoD", "FAIL", f"Adapter {cfg.ADAPTER} not reachable")
        ok = desired_cod_matches(cod)
        return Check("adapter.CoD", "PASS" if ok else "FAIL",
                     f"0x{cod:06x} {'A/V Hands-Free' if ok else 'not A/V Hands-Free'}")
    except Exception as e:
        return Check("adapter.CoD", "FAIL", str(e))


def check_device_paired(mac: str) -> Check:
    try:
        from iphonebridge.pair_setup import list_paired_devices
        devs = {d.mac.upper(): d for d in list_paired_devices()}
        dev = devs.get(mac.upper())
        if dev is None:
            return Check("device.paired", "FAIL", f"{mac} not in paired devices")
        return Check("device.paired", "PASS",
                     f"{dev.name} trusted={dev.trusted} connected={dev.connected}",
                     {"name": dev.name, "trusted": dev.trusted, "connected": dev.connected})
    except Exception as e:
        return Check("device.paired", "FAIL", str(e))


def check_map_read(mac: str) -> Check:
    client = _obex("/org/bluez/obex", "org.bluez.obex.Client1")
    # Restart obexd for clean state (matches SessionManager.open_all)
    subprocess.run(["systemctl", "--user", "restart", "obex.service"], check=False)
    time.sleep(1.0)
    try:
        session = str(client.CreateSession(mac, {"Target": "MAP"}, timeout=30))
    except dbus.DBusException as e:
        return Check("MAP.read", "FAIL",
                     f"CreateSession MAP: {e.get_dbus_name()}: {e.get_dbus_message()}")
    try:
        map_iface = _obex(session, "org.bluez.obex.MessageAccess1")
        try:
            map_iface.SetFolder("/")
        except dbus.DBusException:
            pass
        for seg in "telecom/msg/INBOX".split("/"):
            map_iface.SetFolder(seg)
        msgs = list(map_iface.ListMessages("", {"MaxListCount": dbus.UInt16(5)}))
        if not msgs:
            return Check("MAP.read", "PARTIAL", "ListMessages empty — check Show Message Notifications toggle")
        # Pull metadata for first message to prove body in Subject
        first = str(msgs[0])
        props = dict(_obex(first, "org.freedesktop.DBus.Properties").GetAll("org.bluez.obex.Message1"))
        subject = str(props.get("Subject", ""))
        return Check("MAP.read", "PASS",
                     f"ListMessages={len(msgs)}, Subject preview={subject[:60]!r}",
                     {"handles": len(msgs), "subject_preview": subject[:80]})
    except dbus.DBusException as e:
        return Check("MAP.read", "FAIL", f"{e.get_dbus_name()}: {e.get_dbus_message()}")
    finally:
        try:
            client.RemoveSession(session)
        except Exception:
            pass


def check_pbap(mac: str) -> Check:
    client = _obex("/org/bluez/obex", "org.bluez.obex.Client1")
    try:
        session = str(client.CreateSession(mac, {"Target": "PBAP"}, timeout=30))
    except dbus.DBusException as e:
        return Check("PBAP", "FAIL", f"CreateSession PBAP: {e.get_dbus_name()}: {e.get_dbus_message()}")
    try:
        pbap = _obex(session, "org.bluez.obex.PhonebookAccess1")
        pbap.Select("int", "pb")
        out = Path(tempfile.mkdtemp(prefix="pb27_")) / "pb.vcf"
        ret = pbap.PullAll(str(out), {"MaxListCount": dbus.UInt16(100), "Format": dbus.String("Vcard30")})
        tpath = str(ret[0]) if isinstance(ret, (tuple, list)) else str(ret)
        tprops = _obex(tpath, "org.freedesktop.DBus.Properties")
        for _ in range(100):
            try:
                st = str(tprops.Get("org.bluez.obex.Transfer1", "Status"))
            except dbus.DBusException:
                st = "gone"
                break
            if st in ("complete", "error"):
                break
            time.sleep(0.1)
        size = out.stat().st_size if out.exists() else 0
        text = out.read_text(errors="replace") if size else ""
        ncards = text.count("BEGIN:VCARD")
        return Check("PBAP", "PASS" if ncards else "FAIL",
                     f"cards={ncards}, bytes={size}, status={st}",
                     {"cards": ncards, "bytes": size})
    except dbus.DBusException as e:
        return Check("PBAP", "FAIL", f"{e.get_dbus_name()}: {e.get_dbus_message()}")
    finally:
        try:
            client.RemoveSession(session)
        except Exception:
            pass


def check_hfp() -> Check:
    # oFono VoiceCallManager presence + Dial dry-run not possible without target number,
    # so just check modem + manager exist.
    try:
        bus = dbus.SystemBus()
        om = dbus.Interface(bus.get_object("org.ofono", "/"), "org.freedesktop.DBus.ObjectManager")
        managed = om.GetManagedObjects()
        has_modem = any("org.ofono.VoiceCallManager" in ifaces for ifaces in managed.values())
        if has_modem:
            return Check("HFP.oFono", "PASS", "VoiceCallManager present")
        return Check("HFP.oFono", "FAIL", "No VoiceCallManager — is ofono running? sudo systemctl status ofono")
    except dbus.DBusException as e:
        if "ServiceUnknown" in str(e.get_dbus_name()):
            return Check("HFP.oFono", "SKIP", "oFono not installed / not running (optional for calls)")
        return Check("HFP.oFono", "FAIL", f"{e.get_dbus_name()}: {e.get_dbus_message()}")


def check_ancs_chars(mac: str) -> Check:
    # Look for ANCS GATT chars under the device path
    device_path = f"/org/bluez/{cfg.ADAPTER}/dev_{mac.replace(':', '_')}"
    try:
        bus = dbus.SystemBus()
        om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        managed = om.GetManagedObjects()
        from iphonebridge.ancs.constants import ANCS_CHAR_UUIDS
        found = set()
        for path, ifaces in managed.items():
            if not path.startswith(device_path):
                continue
            char = ifaces.get("org.bluez.GattCharacteristic1")
            if char:
                uuid = str(char.get("UUID", "")).lower()
                if uuid in ANCS_CHAR_UUIDS:
                    found.add(uuid)
        if len(found) == 3:
            return Check("ANCS.chars", "PASS", f"3/3 ANCS chars under {device_path}")
        return Check("ANCS.chars", "PARTIAL",
                     f"{len(found)}/3 ANCS chars — run iphonebridge ancs-enable and re-pair for BLE",
                     {"found": sorted(found)})
    except Exception as e:
        return Check("ANCS.chars", "FAIL", str(e))


def main() -> int:
    ap = argparse.ArgumentParser(description="iOS 27.0 regression for iPhone 13 Pro")
    ap.add_argument("--mac", default=os.environ.get("IPHONEBRIDGE_MAC") or cfg.IPHONE_MAC,
                    help="iPhone MAC (default: IPHONEBRIDGE_MAC / local.env)")
    ap.add_argument("--out", default="spike/results/ios27_iphone13pro.json",
                    help="JSON output path")
    args = ap.parse_args()
    mac = args.mac.strip()

    checks: list[Check] = []
    checks.append(check_mac_placeholder(mac))
    checks.append(check_adapter_cod())
    checks.append(check_device_paired(mac))
    checks.append(check_map_read(mac))
    checks.append(check_pbap(mac))
    checks.append(check_hfp())
    checks.append(check_ancs_chars(mac))

    report = {
        "ios": "27.0 dev beta",
        "device": "iPhone 13 Pro",
        "mac": mac,
        "adapter": cfg.ADAPTER,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checks": [asdict(c) for c in checks],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    # Exit code: 0 if no FAIL, 1 otherwise
    has_fail = any(c.status == "FAIL" for c in checks)
    if has_fail:
        print(f"\n[VERDICT] FAIL — see {out}", file=sys.stderr)
        return 1
    print(f"\n[VERDICT] PASS — report at {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
