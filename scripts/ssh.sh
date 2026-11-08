#!/usr/bin/env bash
# Kelola akun SSH (tunnel-bot) di VPS.
# Usage:
#   ssh.sh create <username> <password> <expire_days>
#   ssh.sh delete <username>
#   ssh.sh lock <username>
#   ssh.sh unlock <username>
#   ssh.sh set-pid-limit <username> <limit>   (0 = unlimited)
#   ssh.sh check-expired
# Semua operasi idempotent. Tidak menghapus user di luar /home/bot.
set -euo pipefail

BOT_HOME="/home/bot"
LOG="/var/log/tunnel-bot-install.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

cmd_create() {
  local u="$1" p="$2" days="$3"
  ensure_base
  # Username valid: huruf kecil, angka, minus; tanpa slash.
  [[ "$u" =~ ^[a-z0-9-]{4,20}$ ]] || { echo "❌ username tidak valid"; exit 1; }
  if id "$u" >/dev/null 2>&1; then
    log "user $u sudah ada; perbarui password & expire"
    echo "$u:$p" | chpasswd
  else
    useradd -m -s /bin/bash -d "$BOT_HOME/$u" "$u"
    echo "$u:$p" | chpasswd
    log "user $u dibuat"
  fi
  # Expired WIB (Asia/Jakarta): +N hari dari sekarang.
  local exp
  exp="$(TZ=Asia/Jakarta date -d "+$days days" '+%Y-%m-%d')"
  chage -E "$exp" "$u"
  log "user $u expired: $exp (WIB)"
  echo "OK"
}

cmd_delete() {
  local u="$1"
  # Hanya hapus user di bawah /home/bot (jangan sentuh user sistem).
  if id "$u" >/dev/null 2>&1 && [ "$(getent passwd "$u" | cut -d: -f6)" = "$BOT_HOME/$u" ]; then
    userdel -r "$u" 2>/dev/null || userdel "$u"
    log "user $u dihapus"
    echo "OK"
  else
    echo "❌ user $u tidak ditemukan / bukan milik bot"
    exit 1
  fi
}

cmd_lock() {
  local u="$1"
  usermod -L "$u" 2>/dev/null || { echo "❌ user $u tidak ada"; exit 1; }
  log "user $u dikunci"
  echo "OK"
}

cmd_unlock() {
  local u="$1"
  usermod -U "$u" 2>/dev/null || { echo "❌ user $u tidak ada"; exit 1; }
  log "user $u dibuka"
  echo "OK"
}

cmd_set_pid_limit() {
  local u="$1" limit="$2"
  id "$u" >/dev/null 2>&1 || { echo "❌ user $u tidak ada"; exit 1; }
  # Batas PID login: 0 = unlimited. Simpan di file akun (dibaca bot utk
  # notifikasi user login, dan health untuk enforce).
  mkdir -p /etc/tunnel-bot
  echo "$limit" > "/etc/tunnel-bot/pidlimit.$u"
  log "pid limit $u = $limit"
  echo "OK"
}

ensure_base() {
  # Grup & dir dasar untuk akun bot.
  getent group bot >/dev/null 2>&1 || groupadd bot
  mkdir -p "$BOT_HOME"
  chmod 711 "$BOT_HOME"
}

cmd_check_expired() {
  # Cetak username yg expired hari ini/lebih (Y-m-d WIB) — dipanggil scheduler.
  local today
  today="$(TZ=Asia/Jakarta date '+%Y-%m-%d')"
  local out=""
  for d in "$BOT_HOME"/*/; do
    [ -d "$d" ] || continue
    local u; u="$(basename "$d")"
    local exp
    exp="$(chage -l "$u" 2>/dev/null | awk -F': ' '/Account expires/{print $2; exit}')"
    if [ -n "$exp" ] && [ "$exp" != "never" ]; then
      local ed
      ed="$(date -d "$exp" '+%Y-%m-%d' 2>/dev/null || echo "$exp")"
      # ISO Y-M-D sortable secara leksikal.
      if [[ "$ed" < "$today" ]]; then
        out="$out $u"
      fi
    fi
  done
  echo "${out# }"
}

case "${1:-}" in
  create) [ "$#" -eq 4 ] && cmd_create "$2" "$3" "$4" || { echo "usage: ssh.sh create <u> <p> <days>"; exit 1; } ;;
  delete) cmd_delete "${2:?usage: ssh.sh delete <u>}" ;;
  lock) cmd_lock "${2:?usage: ssh.sh lock <u>}" ;;
  unlock) cmd_unlock "${2:?usage: ssh.sh unlock <u>}" ;;
  set-pid-limit) cmd_set_pid_limit "${2?}" "${3?}" ;;
  check-expired) cmd_check_expired ;;
  *) echo "perintah tidak dikenal"; exit 1 ;;
esac
