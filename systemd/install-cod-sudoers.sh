#!/usr/bin/env bash
# Install a sudoers.d entry that lets the iphonebridge user daemon set the
# adapter CoD without prompting for a password — but only for that one
# specific btmgmt invocation.
#
# Run as root: sudo bash systemd/install-cod-sudoers.sh
set -euo pipefail

SRC="$(dirname "$0")/sudoers-iphonebridge-cod"
DST=/etc/sudoers.d/iphonebridge-cod

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2; exit 1
fi

# Validate before installing — bad sudoers files can lock you out
if ! visudo -cf "$SRC" >/dev/null; then
    echo "FATAL: $SRC failed visudo -c. Not installing." >&2
    exit 1
fi

install -m 440 -o root -g root "$SRC" "$DST"
echo "[+] Installed $DST"

# Quick verification
if visudo -cf "$DST" >/dev/null; then
    echo "[+] visudo says $DST is valid"
else
    echo "[!] $DST failed validation post-install — removing"
    rm -f "$DST"
    exit 1
fi

cat <<EOF

[+] The iphonebridge user daemon can now run 'btmgmt class 4 8' without
    a password. On each daemon start (e.g. boot, login), it will set
    the adapter to A/V Hands-Free CoD automatically.

    Verify by restarting the daemon:
      systemctl --user restart iphonebridge

    Then check journalctl --user -u iphonebridge for a 'CoD set ok' line.

    Uninstall:
      sudo rm $DST
EOF
