#!/usr/bin/env bash
# Pasang & konfigurasi komponen SSH dan protokol (tunnel-bot).
# Aman dipanggil berulang: idempotent. Backup config sebelum diubah.
# Hanya menyentuh service milik bot — TIDAK menghentikan service lain.
set -euo pipefail

LOG_FILE="/var/log/tunnel-bot-install.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

backup_file() {
  local f="$1"
  [ -f "$f" ] || return 0
  cp -a "$f" "$f.bak.$(date '+%Y%m%d-%H%M%S')"
}

# ---- 1. konfigurasi service yang sudah terpasang, tanpa mematikan ----
ensure_dir() { sudo mkdir -p "$1" 2>/dev/null || mkdir -p "$1"; }

# ---- 2. xray: pastikan config ada; kita tidak sentuh inbound milik service lain ----
setup_xray() {
  if command -v xray >/dev/null 2>&1 || [ -f /usr/local/bin/xray ]; then
    log "xray sudah ada, pastikan service berjalan"
    sudo systemctl enable --now xray 2>/dev/null || true
  else
    log "xray belum terpasang — lewati (installer protokol lain menyediakan)."
  fi
}

# ---- 3. nginx / stream — hanya cek & backup, tidak rebut port 80/443 ----
backup_nginx() {
  if [ -d /etc/nginx ]; then
    backup_file /etc/nginx/nginx.conf
    log "nginx.conf di-backup sebelum perubahan."
  else
    log "nginx belum terpasang."
  fi
}

# ---- 4. stunnel4 — cek saja ----
check_stunnel() {
  if [ -d /etc/stunnel ]; then
    backup_file /etc/stunnel/stunnel.conf
    log "stunnel.conf di-backup."
  fi
}

# ---- main ----
log "=== components.sh mulai ==="
if [ $# -ge 1 ]; then
  # pasang komponen tertentu (dipanggil bot/admin)
  case "$1" in
    xray) setup_xray ;;
    nginx) backup_nginx ;;
    stunnel) check_stunnel ;;
    *) log "komponen $1 tidak dikenal" ;;
  esac
  log "components.sh $1 selesai."
  exit 0
fi

setup_xray
backup_nginx
check_stunnel

# Cek port konflik untuk komponen yang akan dibuka oleh bot.
log "=== Cek port penting (harus bebas / milik bot) ==="
for p in 90 444 8080 8443 2082 2083 2052 2053 8880; do
  if ss -lntu 2>/dev/null | grep -oE ":[0-9]+" | grep -qE ":${p}\$"; then
    log "⚠️ port $p SUDAH DIPAKAI service lain — jangan dibuka ulang tanpa keputusan."
  else
    log "port $p bebas."
  fi
done

log "=== components.sh selesai ==="
