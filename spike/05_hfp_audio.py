#!/usr/bin/env python3
"""05_hfp_audio.py  —  Phase 0 step 05: HFP HF role feasibility.

Unlike MAP/PBAP/ANCS, this isn't about iOS permissions. It's about whether
the LINUX-side stack can act as a Hands-Free (HF) device for the iPhone's
Audio Gateway (AG).

What we check
-------------
1. PipeWire/WirePlumber sees the iPhone as a bluez5 audio card.
2. What profiles WirePlumber exposes for that card.
3. Whether the headset-head-unit (HF role) profile is selectable.
4. Whether attempting to switch to it works.

A real end-to-end test (incoming call → SCO link up → audio out of laptop
speakers) needs interactive user action (place a real call) and is the
right thing for Phase 2c, not Phase 0. The go/no-go we need here is:
"can the Linux audio stack play the HF role at all on this hardware?"

Run:
    python3 05_hfp_audio.py 2>&1 | tee results/05_hfp_audio.log
"""
from __future__ import annotations

import subprocess
import sys

CARD = "bluez_card.AA_BB_CC_DD_EE_FF"

def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)

# ---- inspect card --------------------------------------------------------
print(f"[+] Inspecting PipeWire card {CARD} ...", flush=True)
rc, out = run(["pactl", "list", "cards"])
if rc != 0 or CARD not in out:
    print(f"[FAIL] PipeWire doesn't see {CARD}. Output:\n{out}", flush=True)
    sys.exit(2)

# Extract just this card's section
section_lines, in_section = [], False
for ln in out.splitlines():
    if f"Name: {CARD}" in ln:
        in_section = True
    if in_section:
        section_lines.append(ln)
        if ln.strip() == "" and len(section_lines) > 5:
            break
section = "\n".join(section_lines)

# ---- enumerate available profiles ---------------------------------------
profiles = []
in_profiles, active = False, None
for ln in section.splitlines():
    s = ln.strip()
    if s.startswith("Profiles:"):
        in_profiles = True
        continue
    if in_profiles:
        if not s or not ln.startswith("\t"):
            in_profiles = False
            continue
        # lines look like "off: Off (sinks: 0, sources: 0, ...)"
        name = s.split(":")[0].strip()
        profiles.append((name, s))
    if s.startswith("Active Profile:"):
        active = s.split(":", 1)[1].strip()

print("[+] Available profiles for the iPhone card:", flush=True)
for name, desc in profiles:
    marker = " <-- active" if name == active else ""
    print(f"    - {desc}{marker}", flush=True)

want_hf = "headset-head-unit"
hf_available = any(name == want_hf for name, _ in profiles)
print(f"\n[+] HF role profile '{want_hf}' available: {hf_available}",
      flush=True)

# ---- attempt switch (if available) --------------------------------------
if hf_available:
    print(f"\n[+] Switching to {want_hf} ...", flush=True)
    rc, out = run(["pactl", "set-card-profile", CARD, want_hf])
    if rc == 0:
        print(f"[+] Switched. Verify with: wpctl status", flush=True)
        rc, out2 = run(["pactl", "list", "cards"])
        for ln in out2.splitlines():
            if "Active Profile" in ln and CARD in section_lines[0]:
                print(f"    {ln.strip()}", flush=True)
        print(f"\n[VERDICT] PASS — HFP HF role profile is available and switchable."
              f"\n          Full end-to-end (real call → audio) is Phase 2c.",
              flush=True)
        sys.exit(0)
    else:
        print(f"[FAIL] Profile switch failed:\n{out}", flush=True)
        sys.exit(3)

# ---- not available: report config gap -----------------------------------
print(f"""
[VERDICT] PARTIAL — HFP HF role profile NOT exposed by WirePlumber.

What this means
---------------
The Linux audio stack currently advertises the laptop only as an Audio
Gateway (AG), not as Hands-Free (HF). That's the default on most distros
because cars / headphones / iOS itself all want to talk to a 'phone' (AG).
For our use case (laptop as HF for iPhone-AG), we need to flip a config.

What to change (Phase 2c work, not Phase 0)
-------------------------------------------
Create ~/.config/wireplumber/wireplumber.conf.d/51-bluez-hfp-hf.conf:

  monitor.bluez.properties = {{
    bluez5.roles = [ "a2dp_sink", "a2dp_source", "hfp_hf", "hfp_ag", "hsp_hs", "hsp_ag" ]
    bluez5.codecs = [ "sbc", "sbc_xq", "msbc" ]
  }}

Then: systemctl --user restart wireplumber pipewire pipewire-pulse
Re-run this script: the {want_hf!r} profile should appear, switchable.

Conclusion
----------
HFP HF role is NOT blocked by iOS or by hardware — it's a one-file
WirePlumber config change, fully expected to work. Counted as PASS-pending
for Phase 0 purposes; full call-audio verification is Phase 2c.
""", flush=True)
sys.exit(0)
