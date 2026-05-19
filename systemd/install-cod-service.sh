#!/usr/bin/env bash
# Install the system-level oneshot that sets the adapter CoD on every boot.
# Run with sudo: sudo bash systemd/install-cod-service.sh
set -euo pipefail

SRC="$(dirname "$0")/system/iphonebridge-cod.service"
DST=/etc/systemd/system/iphonebridge-cod.service

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2; exit 1
fi

install -m 644 "$SRC" "$DST"
systemctl daemon-reload
systemctl enable --now iphonebridge-cod.service
systemctl status iphonebridge-cod.service --no-pager | head -10

echo
echo "[+] iphonebridge-cod.service installed and enabled."
echo "    btmgmt class 4 8 will now run at every bluetooth.service start."
echo
echo "    Uninstall:  sudo systemctl disable --now iphonebridge-cod.service"
echo "                sudo rm $DST && sudo systemctl daemon-reload"
