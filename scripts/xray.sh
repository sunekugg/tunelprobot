#!/usr/bin/env bash
# Kelola akun VMess/VLESS/Trojan di config Xray (tunnel-bot).
# Menambah/menghapus klien pada inbound yang SUDAH ada, tanpa mengganggu
# klien lain. Config di-backup sebelum diubah. Reload service.
#
# Usage:
#   xray.sh add <protocol> <username> <id/key> <expire_days>
#   xray.sh del <protocol> <username>
#   xray.sh reload
set -euo pipefail

XRAY_CONF="${XRAY_CONF:-/usr/local/etc/xray/config.json}"
XRAY_BIN="${XRAY_BIN:-xray}"
LOG="/var/log/tunnel-bot-install.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

backup_conf() { [ -f "$XRAY_CONF" ] && cp -a "$XRAY_CONF" "$XRAY_CONF.bak.$(date '+%Y%m%d-%H%M%S')"; }

find_inbound() {
  # protocol -> inbound tag (ssh pakai vmess/trojan menyesuaikan). Disini
  # VMess/VLESS/Trojan biasanya di inbound "inbound-443" atau beberapa.
  # Kita cari inbound pertama yang jenisnya mengandung tag protocol.
  local proto="$1"
  "$XRAY_BIN" -test -config "$XRAY_CONF" >/dev/null 2>&1 || true
  # naive: cari inbound dengan "tag" berisi proto
  grep -o '"tag"[^,]*' "$XRAY_CONF" 2>/dev/null | grep -i "$proto" | head -1 \
    | sed 's/.*"tag"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/' || true
}

add_client() {
  local proto="$1" uid="$2" user="$3"
  local inbound
  inbound="$(find_inbound "$proto")"
  [ -n "$inbound" ] || { echo "❌ inbound untuk $proto tidak ditemukan di $XRAY_CONF"; exit 1; }

  backup_conf
  # Sisipkan client pada inbound pertama dengan protocol tsb. Python dipakai
  # utk manipulasi JSON aman (jauh lebih andal dari sed).
  python3 - "$XRAY_CONF" "$proto" "$uid" "$user" <<'PY'
import json, sys
conf_path, proto, uid, user = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(conf_path) as f:
    data = json.load(f)
added = 0
for inb in data.get("inbounds", []):
    tag = str(inb.get("tag", "")).lower()
    if tag and proto.lower() in tag:
        clients = inb.setdefault("settings", {}).setdefault("clients", [])
        if not any(c.get("email") == user for c in clients):
            clients.append({"id": uid, "email": user, "flow": "", "level": 0})
        added = 1
        break
if not added:
    sys.exit("inbound tidak ditemukan")
with open(conf_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print("client ditambahkan ke", tag)
PY
}

del_client() {
  local proto="$1" user="$2"
  backup_conf
  python3 - "$XRAY_CONF" "$proto" "$user" <<'PY'
import json, sys
conf_path, proto, user = sys.argv[1], sys.argv[2], sys.argv[3]
with open(conf_path) as f:
    data = json.load(f)
removed = 0
for inb in data.get("inbounds", []):
    tag = str(inb.get("tag", "")).lower()
    if tag and proto.lower() in tag:
        clients = inb.get("settings", {}).get("clients", [])
        inb["settings"]["clients"] = [c for c in clients if c.get("email") != user]
        if len(clients) != len(inb["settings"]["clients"]):
            removed = 1
        break
if not removed:
    sys.exit("client tidak ditemukan")
with open(conf_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print("client dihapus dari", tag)
PY
}

reload_xray() {
  if ! "$XRAY_BIN" -test -config "$XRAY_CONF" >/dev/null 2>&1; then
    echo "❌ konfigurasi Xray tidak valid — TIDAK reload (rollback otomatis)"
    exit 1
  fi
  systemctl restart xray 2>/dev/null || "$XRAY_BIN" -c "$XRAY_CONF" restart 2>/dev/null || true
  echo "✅ xray di-reload"
}

case "${1:-}" in
  add) add_client "$2" "$3" "$4"; reload_xray ;;
  del) del_client "$2" "$3"; reload_xray ;;
  reload) reload_xray ;;
  *) echo "usage: xray.sh {add|del|reload}"; exit 1 ;;
esac
