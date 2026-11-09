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

# Tunggu apt/dpkg lock bebas (unattended-upgrade dll) hingga 5 menit.
wait_apt_lock() {
  local n=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; do
    n=$((n+1))
    if [ "$n" -ge 60 ]; then
      err "apt/dpkg masih terkunci setelah 5 menit. Hentikan proses update lain lalu jalankan ulang."
    fi
    log "apt terkunci (proses lain) — tunggu 5s ($((5*n))s)"
    sleep 5
  done
}

# ---- pastikan root ----
if [ "$(id -u)" -ne 0 ]; then err "Jalankan sebagai root: sudo bash install.sh"; fi

mkdir -p "$(dirname "$LOG_FILE")" ; touch "$LOG_FILE"
log "=== Mulai install tunnel-bot ==="

# ---- Zona waktu, conntrack, dsb. ----
log "Load module nf_conntrack & naikkan nf_conntrack_max ke 65536"
modprobe nf_conntrack 2>/dev/null || true
if [ -f /proc/sys/net/netfilter/nf_conntrack_max ]; then
  mkdir -p /etc/modules-load.d
  echo "nf_conntrack" > /etc/modules-load.d/tunnel-bot.conf
  cat > /etc/sysctl.d/99-conntrack.conf <<SYSCTL
net.netfilter.nf_conntrack_max = 65536
SYSCTL
  sysctl -p /etc/sysctl.d/99-conntrack.conf >>"$LOG_FILE" 2>&1 || true
  log "nf_conntrack_max: $(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo 0)"
else
  log "⚠️ nf_conntrack_max tidak tersedia (modul tidak bisa dimuat)"
fi

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
wait_apt_lock
apt-get update -y >>"$LOG_FILE" 2>&1
wait_apt_lock
apt-get install -y git curl wget jq cron python3 python3-venv python3-pip \
                   sqlite3 tzdata >>"$LOG_FILE" 2>&1

# ---- Dependency: build-essential utk compile Dropbear 2019 ----
log "Pasang build-essential (utk compile Dropbear 2019)"
wait_apt_lock
apt-get install -y build-essential zlib1g-dev libssl-dev >>"$LOG_FILE" 2>&1 || true

# ---- Dropbear 2019 (compile dari source, sesuai blueprint §1) ----
# Blueprint minta Dropbear 2019. Ubuntu apt hanya sedia 2020.81+.
# Sumber resmi: matt.ucc.asn.au (mirror release distro Dropbear 2019.78).
DROPBEAR_VER_2019="2019.78"
DROPBEAR_URL="${DROPBEAR_URL:-https://matt.ucc.asn.au/dropbear/releases/dropbear-${DROPBEAR_VER_2019}.tar.bz2}"
DROPBEAR_SRC="/usr/local/src/dropbear-2019"
log "Compile Dropbear $DROPBEAR_VER_2019 dari $DROPBEAR_URL"
wait_apt_lock
apt-get remove -y dropbear >>"$LOG_FILE" 2>&1 || true
rm -rf "$DROPBEAR_SRC"
mkdir -p "$DROPBEAR_SRC" && cd "$DROPBEAR_SRC"
curl -fsSL "$DROPBEAR_URL" -o dropbear-src.tar.bz2 >>"$LOG_FILE" 2>&1 \
  || err "Gagal download source Dropbear $DROPBEAR_URL"
tar -xjf dropbear-src.tar.bz2 --strip-components=1
./configure >>"$LOG_FILE" 2>&1 || err "configure Dropbear gagal"
make -j"$(nproc)" >>"$LOG_FILE" 2>&1 || err "make Dropbear gagal"
make install >>"$LOG_FILE" 2>&1 || err "install Dropbear gagal"
# binary di /usr/local/sbin/dropbear; pastikan di PATH & versi terkunci
ln -sf /usr/local/sbin/dropbear /usr/sbin/dropbear || true
DROPBEAR_VER="$(PATH=/usr/local/sbin:$PATH dropbear -V 2>&1 | head -1 || true)"
log "Dropbear 2019 terpasang: $DROPBEAR_VER"
apt-mark hold dropbear >/dev/null 2>&1 || true
log "apt-mark showhold: $(apt-mark showhold | tr '\n' ' ')"

# Konfigurasi systemd dropbear-2019 (port 90, sesuai blueprint §4)
log "Pasang service dropbear-2019 di port 90"
mkdir -p /etc/dropbear
chmod 700 /etc/dropbear
[ -f /etc/dropbear/dropbear_rsa_host_key ] || \
  /usr/local/sbin/dropbearkey -t rsa -s 2048 -f /etc/dropbear/dropbear_rsa_host_key >/dev/null 2>&1 || true
[ -f /etc/dropbear/dropbear_ed25519_host_key ] || \
  /usr/local/sbin/dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key >/dev/null 2>&1 || true
cat > /etc/systemd/system/dropbear-2019.service <<UNIT
[Unit]
Description=Dropbear SSH (2019) - tunnel-bot port 90
After=network.target

[Service]
Type=forking
ExecStart=/usr/local/sbin/dropbear -p 90 -r /etc/dropbear/dropbear_rsa_host_key -r /etc/dropbear/dropbear_ed25519_host_key
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable dropbear-2019 >/dev/null 2>&1 || true
# start hanya bila port 90 bebas
if ! ss -lntu | awk '{print $4}' | grep -q ":90\$"; then
  systemctl start dropbear-2019 || log "⚠️ dropbear-2019 gagal start (cek port 90)"
fi

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

# Untuk clone dari repo privat, token GitHub opsional (kalau token Telegram
# TIDAK sama dengan token GitHub). Kalau token Telegram juga valid untuk
# GitHub, bisa langsung pakai itu. Beri prompt sekali (boleh kosong jika
# repo publik).
read -rp "Token GitHub (opsional, kosongkan utk repo publik): " GH_TOKEN_IN || true

# ---- clone / pull repo (non-destructive) ----
if [ -d "$REPO_ROOT/.git" ]; then
  log "Repo sudah ada, tarik versi terbaru (tanpa reset --hard)"
  git -C "$REPO_ROOT" fetch origin
  git -C "$REPO_ROOT" checkout dev || true
  git -C "$REPO_ROOT" merge --ff-only origin/dev || \
    log "⚠️ ff-only gagal; repo mungkin punya commit lokal. Cek manual."
else
  log "Clone repo dari $REPO_URL_IN"
  rm -rf "$REPO_ROOT"
  # Simpan token ke credential helper agar tidak bocor ke log/command (URL).
  if [ -n "${GH_TOKEN_IN:-}" ]; then
    host="$(printf '%s' "$REPO_URL_IN" | sed -E 's#https?://([^/]+)/.*#\1#')"
    printf 'protocol=https\nhost=%s\nusername=oauth2\npassword=%s\n\n' "$host" "$GH_TOKEN_IN" \
      | git credential approve 2>/dev/null || true
  fi
  GIT_TERMINAL_PROMPT=0 git clone --branch dev --depth 1 "$REPO_URL_IN" "$REPO_ROOT" >>"$LOG_FILE" 2>&1 \
    && log "clone sukses" \
    || err "clone gagal. Periksa URL repo atau set GITHUB_TOKEN."
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
    first_id="${ADMIN_IN%%,*}"
    echo "ADMIN_ID=$first_id"
    echo "ADMIN_IDS=$ADMIN_IN"
    echo "BOT_TOKEN=$TOKEN_IN"
    echo "DOMAIN=$DOMAIN_IN"
    echo "DB_PATH=$DATA_DIR/data.db"
    echo "XRAY_CONFIG=/usr/local/etc/xray/config.json"
    echo "XRAY_ACCESS_LOG=/var/log/xray/access.log"
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
