#!/usr/bin/env bash
# Bootstrap tunnel-bot — jalankan SATU kali di VPS baru.
# Idempotent: dijalankan ulang TIDAK merusak data/config yang sudah ada.
# URL repo ditanya saat install. Setelah selesai, bot berjalan sebagai
# systemd service `tunnel-bot.service`.
set -euo pipefail

REPO_ROOT="/opt/tunnel-bot"
DATA_DIR="/etc/tunnel-bot"
LOG_FILE="/var/log/tunnel-bot-install.log"
TZ_TARGET="Asia/Jakarta"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
err() { echo "❌ $*" >&2; exit 1; }

# ---- pastikan root ----
if [ "$(id -u)" -ne 0 ]; then err "Jalankan sebagai root: sudo bash install.sh"; fi

mkdir -p "$(dirname "$LOG_FILE")" ; touch "$LOG_FILE"
log "=== Mulai install tunnel-bot ==="

# ---- backup instalan lama bila ada ----
if [ -d "$REPO_ROOT" ]; then
  TS="$(date '+%Y%m%d-%H%M%S')"
  BK="/root/tunnel-bot-backup-$TS.tar.gz"
  log "Backup repo lama -> $BK"
  tar -czf "$BK" -C "$(dirname "$REPO_ROOT")" "$(basename "$REPO_ROOT")" 2>/dev/null || true
fi

# ---- timezone Asia/Jakarta ----
log "Set zona waktu $TZ_TARGET"
timedatectl set-timezone "$TZ_TARGET" 2>/dev/null || \
  ln -sf /usr/share/zoneinfo/"$TZ_TARGET" /etc/localtime
log "Zona waktu saat ini: $(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)"

# ---- dependency dasar ----
log "Update apt & pasang dependency dasar"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >>"$LOG_FILE" 2>&1
apt-get install -y git curl wget jq cron python3 python3-venv python3-pip \
                   sqlite3 tzdata >>"$LOG_FILE" 2>&1

# ---- Dropbear 2019 (terkunci) ----
log "Pasang Dropbear dan kunci versi 2019.x"
apt-get install -y dropbear >>"$LOG_FILE" 2>&1 || true
DROPBEAR_VER="$(dropbear -V 2>&1 | head -1 || true)"
log "Dropbear terpasang: $DROPBEAR_VER"
# Kunci versi saat ini (jangan auto-upgrade).
apt-mark hold dropbear >/dev/null 2>&1 || true
log "apt-mark showhold: $(apt-mark showhold | tr '\n' ' ')"

# ---- input interaktif ----
echo
echo "=== Konfigurasi awal (hanya ini yang ditanya) ==="
read -rp "Token bot Telegram: " TOKEN_IN
read -rp "ID admin utama (boleh lebih dari satu, dipisah koma): " ADMIN_IN
read -rp "Domain utama (misal clode2.sunekvip.my.id): " DOMAIN_IN
read -rp "URL repo GitHub (https://github.com/<user>/<repo>.git): " REPO_URL_IN
[ -z "$TOKEN_IN" ] && err "Token bot Telegram wajib diisi."
[ -z "$ADMIN_IN" ] && err "ID admin utama wajib diisi."
[ -z "$DOMAIN_IN" ] && err "Domain utama wajib diisi."
[ -z "$REPO_URL_IN" ] && err "URL repo GitHub wajib diisi."

# ---- clone / pull repo (non-destructive) ----
if [ -d "$REPO_ROOT/.git" ]; then
  log "Repo sudah ada, tarik versi terbaru (tanpa reset --hard)"
  git -C "$REPO_ROOT" fetch origin
  git -C "$REPO_ROOT" checkout main || true
  git -C "$REPO_ROOT" merge --ff-only origin/main || \
    log "⚠️ ff-only gagal; repo mungkin punya commit lokal. Cek manual."
else
  log "Clone repo dari $REPO_URL_IN"
  rm -rf "$REPO_ROOT"
  git clone --branch main "$REPO_URL_IN" "$REPO_ROOT" >>"$LOG_FILE" 2>&1
fi

# ---- cek port sebelum bind (lihat blueprint §4) ----
log "=== Port yang sedang dipakai ==="
ss -lntup >> "$LOG_FILE" 2>&1 || true

# ---- direktori data & config.env ----
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"
ENV_FILE="$DATA_DIR/config.env"
if [ -f "$ENV_FILE" ]; then
  log "config.env SUDAH ada, TIDAK ditimpa (idempotent)"
else
  log "Buat $ENV_FILE"
  {
    echo "TOKEN=$TOKEN_IN"
    echo "ADMIN_IDS=$ADMIN_IN"
    echo "DOMAIN=$DOMAIN_IN"
    echo "REPO_URL=$REPO_URL_IN"
    echo "PREFIKS_USERNAME=sunek-"
    echo "TZ=$TZ_TARGET"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

# ---- virtualenv & deps Python ----
log "Siapkan virtualenv & deps Python"
python3 -m venv "$REPO_ROOT/.venv" >>"$LOG_FILE" 2>&1 || true
"$REPO_ROOT/.venv/bin/pip" install --upgrade pip >>"$LOG_FILE" 2>&1
"$REPO_ROOT/.venv/bin/pip" install -r "$REPO_ROOT/requirements.txt" >>"$LOG_FILE" 2>&1

# ---- perintah tunnel di PATH ----
log "Pasang perintah tunnel ke /usr/local/bin/tunnel"
install -m 755 "$REPO_ROOT/tunnel" /usr/local/bin/tunnel

# ---- systemd service ----
log "Pasang systemd service tunnel-bot.service"
cp "$REPO_ROOT/tunnel-bot.service" /etc/systemd/system/tunnel-bot.service
systemctl daemon-reload
systemctl enable tunnel-bot.service >/dev/null 2>&1 || true
# Restart hanya kalau entry point bot sudah ada (Fase 5).
if [ -f "$REPO_ROOT/bot/main.py" ]; then
  systemctl restart tunnel-bot.service || true
else
  log "⚠️ bot/main.py belum ada — service tidak di-start (bot inti Fase 5)."
fi

# ---- pasang komponen (SSH + Xray + mux) ----
log "Pasang komponen SSH + Xray"
bash "$REPO_ROOT/scripts/components.sh" || log "components.sh gagal, lanjut (cek manual)"

# ---- final ----
log "=== Install selesai ==="
systemctl --no-pager status tunnel-bot.service | head -6 || true
echo
echo "✅ tunnel-bot terpasang. Jalankan 'tunnel status' untuk cek."
echo "   ID grup / ID topic / wajib join diatur dari menu admin di dalam bot."
