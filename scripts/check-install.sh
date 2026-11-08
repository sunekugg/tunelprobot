#!/usr/bin/env bash
# Verifikasi pasca-install tunnel-bot (acceptance partial, blueprint §15).
# Dipakai di VPS uji setelah install. Tidak mengubah apa pun.
# Usage: bash scripts/check-install.sh
set -euo pipefail

DATA_DIR="${DATA_DIR:-/etc/tunnel-bot}"
REPO_ROOT="${REPO_ROOT:-/opt/tunnel-bot}"
fail=0

pass() { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }
failn() { echo "  ❌ $*"; fail=1; }

echo "== 1. Zona waktu =="
tz="$(timedatectl show -p Timezone --value 2>/dev/null || echo '')"
if [ "$tz" = "Asia/Jakarta" ]; then pass "zona waktu: $tz (WIB)"; else failn "zona waktu: ${tz:-?} bukan Asia/Jakarta"; fi

echo "== 2. Dropbear 2019 terkunci =="
if command -v dropbear >/dev/null 2>&1; then
  dv="$(dropbear -V 2>&1 | head -1 || echo 'unknown')"
  echo "  version: $dv"
  pass "dropbear terpasang"
else
  warn "dropbear tidak terdeteksi di PATH"
fi
hold="$(apt-mark showhold 2>/dev/null | tr '\n' ' ')"
echo "  apt-mark showhold: $hold"
case "$hold" in *dropbear*) pass "dropbear dikunci (hold)";; *) warn "dropbear TIDAK dikunci";; esac

echo "== 3. Port penting (blueprint §4) =="
for p in 22 80 443 81 90 444 53 5300 8080 8443 3128 9080 9088; do
  if ss -lntu | awk '{print $4}' | grep -qE "[:.]${p}\$"; then
    pass "port $p: TERBUKA"
  else
    warn "port $p: belum terdeteksi (boleh, tergantung service)"
  fi
done

echo "== 4. Conntrack =="
max="$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo 0)"
if [ "$max" -ge 65536 ]; then pass "nf_conntrack_max=$max (OK)"; else failn "nf_conntrack_max=${max} DI BAWAH 65536"; fi

echo "== 5. Data & config =="
[ -d "$DATA_DIR" ] && pass "dir $DATA_DIR ada" || failn "dir $DATA_DIR tidak ada"
[ -f "$DATA_DIR/config.env" ] && pass "config.env ada" || failn "config.env tidak ada"
[ -f "$DATA_DIR/data.db" ] && pass "data.db ada" || warn "data.db belum ada (bot belum start)"
if [ -f "$DATA_DIR/config.env" ]; then
  # pastikan tidak ada IP yang bocor ke value terlihat (bukan yang tampil ke user, tapi sanity)
  if grep -qE '^DOMAIN=.*[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' "$DATA_DIR/config.env"; then
    warn "DOMAIN berupa IP — seharusnya domain (cek blueprint)"
  else
    pass "DOMAIN bukan IP"
  fi
fi

echo "== 6. Service & CLI =="
if systemctl is-active --quiet tunnel-bot 2>/dev/null; then
  pass "service tunnel-bot aktif"
else
  warn "service tunnel-bot tidak aktif (mungkin bot/main.py belum ada saat install)"
fi
command -v tunnel >/dev/null 2>&1 && pass "CLI tunnel ada" || failn "CLI tunnel tidak ada"
command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; print("  python:", sys.version.split()[0])'

echo
if [ "$fail" -eq 0 ]; then
  echo "== SEMUA CEK LULUS (dengan catatan ⚠️  bukan kegagalan) =="
else
  echo "== ADA KEGAGALAN ($fail) =="
  exit 1
fi
