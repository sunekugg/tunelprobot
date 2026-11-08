#!/usr/bin/env bash
# Auto backup & restore data tunnel-bot.
# Usage:
#   backup.sh              -> buat backup tar.gz di $DATA_DIR/backups/
#   backup.sh restore <f>  -> restore dari file (harus via konfirmasi di CLI)
set -euo pipefail

DATA_DIR="${DATA_DIR:-/etc/tunnel-bot}"
BACKUP_DIR="$DATA_DIR/backups"
LOG="/var/log/tunnel-bot-install.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

backup() {
  mkdir -p "$BACKUP_DIR"
  local ts out
  ts="$(date '+%Y%m%d-%H%M%S')"
  out="$BACKUP_DIR/data-$ts.tar.gz"
  tar -czf "$out" -C "$DATA_DIR" data.db config.env 2>/dev/null
  # simpan catatan path utk CLI restore
  echo "$out" >> "$DATA_DIR/backups.log"
  log "backup: $out ($(du -h "$out" | cut -f1))"
  echo "✅ Backup: $out"
}

restore() {
  local f="${1:-}"
  [ -n "$f" ] && [ -f "$f" ] || { echo "❌ file backup tidak ada"; exit 1; }
  # backup kondisi sekarang dulu (keamanan rollback)
  backup
  tar -xzf "$f" -C "$DATA_DIR"
  log "restore dari $f"
  echo "✅ Restore dari $f"
}

case "${1:-}" in
  restore) restore "${2:-}" ;;
  *) backup ;;
esac
