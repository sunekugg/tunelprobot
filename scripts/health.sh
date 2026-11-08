#!/usr/bin/env bash
# Auto-heal service tunnel-bot & cek port — dipanggil cron/scheduler.
# Hanya restart service milik bot yang mati; tidak menyentuh layanan lain.
set -euo pipefail

SERVICE="tunnel-bot"
LOG_FILE="/var/log/tunnel-bot-install.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

restart_if_down() {
  local svc="$1"
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    log "$svc OK."
  else
    log "⚠️ $svc TIDAK aktif — restart."
    systemctl restart "$svc" 2>/dev/null || true
    sleep 1
    systemctl is-active --quiet "$svc" && log "$svc pulih." || log "❌ $svc GAGAL restart."
  fi
}

# Service milik bot (bukan nginx/stunnel/xray manual — jangan sentuh tanpa keputusan).
restart_if_down "$SERVICE"

# Cek conntrack dan beri peringatan jika rendah.
max="$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo 0)"
if [ "$max" -gt 0 ] && [ "$max" -lt 65536 ]; then
  log "⚠️ nf_conntrack_max=$max DI BAWAH 65536."
fi

# Laporkan port penting milik bot (daftar blueprint §4) apakah terbuka.
for p in 90 444 8080 8443 2082 2083 2052 2053 8880 9080 9088; do
  if ss -lntu 2>/dev/null | grep -oE ":[0-9]+" | grep -qE ":${p}\$"; then
    log "port $p: TERBUKA."
  fi
done

log "health.sh selesai."
