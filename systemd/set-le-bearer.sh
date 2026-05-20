#!/usr/bin/env bash
# iphonebridge-set-le-bearer ADAPTER_MAC DEVICE_MAC
#
# Sets LastUsedBearer=le in the BlueZ pairing record at
#   /var/lib/bluetooth/<adapter>/<device>/info
# to bias BlueZ toward BLE on the next Connect() of that device. This is
# the key unlock for ANCS access on iOS — without it, BlueZ defaults to
# BR/EDR (which carries MAP/PBAP but not ANCS) and BLE GATT enumeration
# never runs. See https://github.com/bmh129/ancs4linux for the rationale.
#
# Designed for narrow sudoers exposure:
#   user ALL=(root) NOPASSWD: /usr/local/bin/iphonebridge-set-le-bearer
#
# Both MAC arguments are validated against a strict format before being
# used in any path, so even with relaxed sudoers the attack surface is
# limited to writing one specific file with one specific value.

set -euo pipefail

usage() {
    echo "usage: $0 <adapter-mac> <device-mac>" >&2
    exit 1
}

[[ $# -eq 2 ]] || usage

ADAPTER="$1"
DEVICE="$2"

mac_re='^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'
[[ "$ADAPTER" =~ $mac_re ]] || { echo "bad adapter MAC: $ADAPTER" >&2; exit 1; }
[[ "$DEVICE"  =~ $mac_re ]] || { echo "bad device MAC: $DEVICE"  >&2; exit 1; }

# Upper-case for the path (BlueZ uses uppercase MACs in directory names)
ADAPTER_UC=$(echo "$ADAPTER" | tr 'a-f' 'A-F')
DEVICE_UC=$(echo "$DEVICE"  | tr 'a-f' 'A-F')

FILE="/var/lib/bluetooth/${ADAPTER_UC}/${DEVICE_UC}/info"
[[ -f "$FILE" ]] || { echo "no pairing info at $FILE" >&2; exit 1; }

TMP=$(mktemp -p "$(dirname "$FILE")" .info.XXXXXX) || exit 1
trap 'rm -f "$TMP"' EXIT

if grep -q "^LastUsedBearer=" "$FILE"; then
    sed 's/^LastUsedBearer=.*/LastUsedBearer=le/' "$FILE" > "$TMP"
else
    cat "$FILE" > "$TMP"
    echo "LastUsedBearer=le" >> "$TMP"
fi

# Preserve original ownership + mode (typically root:root, 0600)
chmod --reference="$FILE" "$TMP"
chown --reference="$FILE" "$TMP"

mv "$TMP" "$FILE"
trap - EXIT

echo "[ok] LastUsedBearer=le set in $FILE"
