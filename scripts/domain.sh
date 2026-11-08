#!/usr/bin/env bash
# Ganti domain utama tunnel-bot & update konfigurasi terkait.
# Aksi berbahaya -> wajib konfirmasi di CLI tunnel. Backup config sebelum ubah.
# Usage: domain.sh <domain_baru>
set -euo pipefail

DATA_DIR="${DATA_DIR:-/etc/tunnel-bot}"
ENV_FILE="$DATA_DIR/config.env"
LOG="/var/log/tunnel-bot-install.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

NEW="${1:-}"
[ -n "$NEW" ] || { echo "❌ Domain baru kosong"; exit 1; }

[ -f "$ENV_FILE" ] || { echo "❌ config.env tidak ada"; exit 1; }

# Backup config lama (dengan timestamp).
cp -a "$ENV_FILE" "$ENV_FILE.bak.$(date '+%Y%m%d-%H%M%S')"

# Update value DOMAIN (hapus baris lama, tambah baru), pertahankan lainnya.
sed -i "/^DOMAIN=/d" "$ENV_FILE"
echo "DOMAIN=$NEW" >> "$ENV_FILE"

log "Domain diubah -> $NEW"

# Catatan: penyesuaian nginx/stunnel/xray utk domain baru bersifat per-instal
# (VPS produksi). Di sini cukup kunci config. Lihat blueprint §8 / scripts komponen.
echo "✅ Domain di-set ke $NEW"
