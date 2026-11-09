#!/usr/bin/env bash
# ==============================================================================
# deploy-full.sh — Deploy tunnel-bot SEKALI JALAN ke VPS baru.
# Semua komponen langsung terpasang & konfigurasi live dari server lama
# (terbukti jalan) langsung direstore. Cukup isi 3 data di bagian CONFIG.
# ==============================================================================
set -euo pipefail

LOG="/var/log/tunnel-bot-deploy.log"
REPO="/opt/tunnel-bot"
DATA="/etc/tunnel-bot"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
die() { echo "FATAL: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Jalankan sebagai root."

# ======== AMBIL DATA DARI ARGUMEN ATAU TANYA INTERAKTIF ========
# Pakai: bash deploy-full.sh <TOKEN> <ADMIN_ID> <DOMAIN> [REPO_URL] [BRANCH] [OLD_DOMAIN]
BOT_TOKEN="${1:-}"
ADMIN_ID="${2:-}"
DOMAIN="${3:-}"
REPO_URL="${4:-https://github.com/sunekugg/tunelprobot.git}"
BRANCH="${5:-dev}"
OLD_DOMAIN="${6:-}"
GH_TOKEN="${GH_TOKEN:-}"

if [ -z "$BOT_TOKEN" ] || [ -z "$ADMIN_ID" ] || [ -z "$DOMAIN" ]; then
  echo "=== Deploy tunnel-bot SEKALI JALAN ==="
  [ -z "$BOT_TOKEN" ] && read -rp "Token bot Telegram: " BOT_TOKEN
  [ -z "$ADMIN_ID" ]  && read -rp "ID admin utama (angka): " ADMIN_ID
  [ -z "$DOMAIN" ]    && read -rp "Domain VPS baru (A record -> IP ini): " DOMAIN
  if [ -z "$OLD_DOMAIN" ]; then
    read -rp "Domain config lama (Enter = deteksi otomatis): " OLD_DOMAIN
  fi
fi
[ -n "$BOT_TOKEN" ] || die "Token bot wajib diisi."
[ -n "$ADMIN_ID" ]  || die "ID admin wajib diisi."
[ -n "$DOMAIN" ]    || die "Domain wajib diisi."

# ---------- snapshot konfigurasi live (base64) ----------
SNAP_B64="
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
"

# Mapping file snapshot -> path tujuan di VPS baru
SNAP_FILES=(
    "_usr_local_etc_xray_config.json|/usr/local/etc/xray/config.json"
    "_etc_haproxy_haproxy.cfg|/etc/haproxy/haproxy.cfg"
    "_etc_nginx_nginx.conf|/etc/nginx/nginx.conf"
    "_etc_nginx_conf.d_vpn-multiplex.conf|/etc/nginx/conf.d/vpn-multiplex.conf"
    "_usr_local_bin_ws-ssh.py|/usr/local/bin/ws-ssh.py"
    "_etc_systemd_system_ws-ssh.service|/etc/systemd/system/ws-ssh.service"
    "_etc_systemd_system_xray.service|/etc/systemd/system/xray.service"
    "_etc_systemd_system_tunnel-bot.service|/etc/systemd/system/tunnel-bot.service"
    "_usr_local_bin_vpn-bw-manager.py|/usr/local/bin/vpn-bw-manager.py"
    "_usr_local_bin_tunnel|/usr/local/bin/tunnel"
    "_root_node-agent-setup.sh|/root/node-agent-setup.sh"
)

log "=== [1/9] Persiapan sistem ==="
# Bersihkan installer LAMA (tgbot-vpn) bila ada — mencegah bentrok service/port.
# Bot aktif sekarang adalah /opt/tunnel-bot (tunnel-bot.service).
if [ -d /root/tgbot-vpn ] || systemctl list-unit-files 2>/dev/null | grep -q "^tgbot-vpn"; then
  log "hapus instalasi lama tgbot-vpn"
  systemctl stop tgbot-vpn 2>/dev/null || true
  systemctl disable tgbot-vpn 2>/dev/null || true
  rm -f /etc/systemd/system/tgbot-vpn.service
  rm -f /etc/cron.d/vpn-auto-expired /usr/local/bin/check-vpn-expired.py
  systemctl daemon-reload
fi
export DEBIAN_FRONTEND=noninteractive
timedatectl set-timezone Asia/Jakarta 2>/dev/null || ln -sf /usr/share/zoneinfo/Asia/Jakarta /etc/localtime
modprobe nf_conntrack 2>/dev/null || true
echo "nf_conntrack" > /etc/modules-load.d/tunnel-bot.conf 2>/dev/null || true
echo "net.netfilter.nf_conntrack_max = 65536" > /etc/sysctl.d/99-conntrack.conf 2>/dev/null || true
sysctl -p /etc/sysctl.d/99-conntrack.conf >/dev/null 2>&1 || true
wait_apt() { local n=0; while fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; do n=$((n+1)); [ $n -ge 60 ] && die "apt lock > 5 menit"; sleep 5; done; }

log "=== [2/9] Install dependency dasar ==="
wait_apt; apt-get update -y >>"$LOG" 2>&1
wait_apt; apt-get install -y git curl wget jq socat cron net-tools lsof iproute2 \
  iptables vnstat haproxy nginx openssl uuid-runtime python3 python3-venv python3-pip \
  sqlite3 tzdata build-essential zlib1g-dev libssl-dev bzip2 unzip ca-certificates \
  >>"$LOG" 2>&1

log "=== [3/9] Compile Dropbear 2019 (port 90/109/143) ==="
# Metode TERUJI (nyala di VPS Ubuntu 22.04):
#  - CFLAGS '-fcommon' WAJIB (GCC11+ default -fno-common membuat binary rusak).
#  - dropbear 2019 TIDAK support ed25519 -> pakai rsa + ecdsa.
#  - binary di /usr/sbin, Type=simple, log ke /var/log/dropbear.log.
cat > /root/install-dropbear.sh <<'DROPBEAR_SH'
#!/usr/bin/env bash
export DEBIAN_FRONTEND=noninteractive
echo "[1/4] Membuka kunci APT & instal paket pendukung..."
systemctl stop apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service >/dev/null 2>&1 || true
killall -9 apt apt-get dpkg unattended-upgr >/dev/null 2>&1 || true
rm -f /var/lib/apt/lists/lock /var/cache/apt/archives/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend
dpkg --configure -a --force-confdef --force-confold >/dev/null 2>&1 || true
apt-get update -y
apt-get install -y wget bzip2 build-essential zlib1g-dev psmisc dropbear >/dev/null 2>&1
apt-get install -y libcrypt-dev >/dev/null 2>&1 || true

echo "[2/4] Mengunduh & kompilasi Dropbear v2019.78..."
cd /tmp && rm -rf dropbear-2019.78*
wget -q --timeout=15 "https://matt.ucc.asn.au/dropbear/releases/dropbear-2019.78.tar.bz2" -O dropbear-2019.78.tar.bz2 || \
wget -q --timeout=15 "https://dropbear.nl/mirror/releases/dropbear-2019.78.tar.bz2" -O dropbear-2019.78.tar.bz2
tar -xjf dropbear-2019.78.tar.bz2 && cd dropbear-2019.78
./configure CFLAGS="-O2 -fcommon -Wno-error -Wno-unused-result" --prefix=/usr --sysconfdir=/etc/dropbear >/dev/null 2>&1
make PROGRAMS="dropbear dbclient dropbearkey dropbearconvert scp" -j2 >/dev/null 2>&1
systemctl stop dropbear >/dev/null 2>&1 || true
killall -9 dropbear >/dev/null 2>&1 || true
rm -f /etc/init.d/dropbear
cp -f dropbear /usr/sbin/dropbear
cp -f dbclient dropbearkey dropbearconvert /usr/bin/
chmod +x /usr/sbin/dropbear /usr/bin/dropbearkey
apt-mark hold dropbear dropbear-bin >/dev/null 2>&1 || true
cd /root && rm -rf /tmp/dropbear-2019.78*

echo "[3/4] Menyiapkan kunci host, shell & banner..."
mkdir -p /etc/dropbear
[[ -f /etc/dropbear/dropbear_rsa_host_key ]] || /usr/bin/dropbearkey -t rsa -f /etc/dropbear/dropbear_rsa_host_key >/dev/null 2>&1
[[ -f /etc/dropbear/dropbear_ecdsa_host_key ]] || /usr/bin/dropbearkey -t ecdsa -f /etc/dropbear/dropbear_ecdsa_host_key >/dev/null 2>&1
grep -q "^/bin/false$" /etc/shells || echo "/bin/false" >> /etc/shells
touch /var/log/dropbear.log && chmod 666 /var/log/dropbear.log

echo "[4/4] Mengaktifkan service Dropbear (port 90/109/143)..."
cat << 'EOSVC' > /etc/systemd/system/dropbear.service
[Unit]
Description=Dropbear SSH Server v2019.78
After=network.target
[Service]
Type=simple
ExecStart=/usr/sbin/dropbear -EF -p 90 -p 109 -p 143 -W 65536 -b /etc/issue.net -r /etc/dropbear/dropbear_rsa_host_key -r /etc/dropbear/dropbear_ecdsa_host_key
StandardOutput=append:/var/log/dropbear.log
StandardError=append:/var/log/dropbear.log
KillMode=process
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
EOSVC
systemctl daemon-reload
systemctl unmask dropbear >/dev/null 2>&1 || true
systemctl enable --now dropbear
systemctl restart dropbear
echo "Dropbear : $(/usr/sbin/dropbear -V 2>&1 | head -n1) | status=$(systemctl is-active dropbear)"
DROPBEAR_SH
chmod +x /root/install-dropbear.sh
bash /root/install-dropbear.sh 2>&1 | tee -a "$LOG" || log "dropbear installer ada peringatan (cek $LOG)"
[ -x /usr/sbin/dropbear ] || die "dropbear binary tak terpasang (cek $LOG)"
echo "Selamat datang di server VPN" > /etc/issue.net 2>/dev/null || true

log "=== [4/9] Restore konfigurasi live dari snapshot ==="
mkdir -p /tmp/snap && cd /tmp/snap && rm -rf ./*
echo "$SNAP_B64" | base64 -d | tar xzf - -C /tmp/snap
for pair in "${SNAP_FILES[@]}"; do
  src="${pair%%|*}"; dst="${pair##*|}"
  if [ -f "/tmp/snap/$src" ]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "/tmp/snap/$src" "$dst"
    log "restore: $dst"
  else
    log "SKIP (tidak ada di snapshot): $dst"
  fi
done
chmod +x /usr/local/bin/ws-ssh.py /usr/local/bin/vpn-bw-manager.py /usr/local/bin/tunnel 2>/dev/null || true
# Xray unit (dari snapshot) jalan sbg User=xray -> sering 217/USER gagal di VPS baru.
# Metode TERUJI (nyala di VPS user): User=root. Folder log tetap disiapkan.
mkdir -p /var/log/xray /usr/local/share/xray
touch /var/log/xray/access.log /var/log/xray/error.log
chmod 666 /var/log/xray/access.log /var/log/xray/error.log
if [ -f /etc/systemd/system/xray.service ]; then
  sed -i 's/^User=.*/User=root/; s/^Group=.*/Group=root/' /etc/systemd/system/xray.service
  grep -q "^User=root" /etc/systemd/system/xray.service || sed -i '/\[Service\]/a User=root' /etc/systemd/system/xray.service
fi

# Auto-ganti domain lama -> domain baru di semua config proxy (SNI/cert cocok)
if [ -z "$OLD_DOMAIN" ]; then
  OLD_DOMAIN="$(grep -rhoE "[a-zA-Z0-9.-]+\.(web\.id|my\.id|com|net|org|vip)" /tmp/snap/_usr_local_etc_xray_config.json 2>/dev/null | grep -vE "github|xtls|acme|matt\.ucc|dropbear" | head -1 || true)"
fi
if [ -n "$OLD_DOMAIN" ] && [ "$OLD_DOMAIN" != "$DOMAIN" ]; then
  log "ganti domain $OLD_DOMAIN -> $DOMAIN di config proxy"
  sed -i "s/${OLD_DOMAIN}/${DOMAIN}/g" \
    /usr/local/etc/xray/config.json \
    /etc/haproxy/haproxy.cfg \
    /etc/nginx/nginx.conf \
    /etc/nginx/conf.d/vpn-multiplex.conf 2>/dev/null || true
else
  log "domain config sudah sesuai ($DOMAIN), tidak diganti"
fi

log "=== [5/9] Xray core ==="
if ! command -v xray >/dev/null 2>&1 && [ ! -f /usr/local/bin/xray ]; then
  log "unduh Xray core terbaru (arsitektur: $(uname -m))"
  case "$(uname -m)" in
    x86_64) XRAY_ARCH="64" ;;
    aarch64|arm64) XRAY_ARCH="arm64-v8a" ;;
    *) XRAY_ARCH="64" ;;
  esac
  mkdir -p /usr/local/bin
  # releases/latest/download lebih andal (tanpa API rate-limit), + fallback mirror
  wget -qO /tmp/xray.zip \
    "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${XRAY_ARCH}.zip" \
    || die "Gagal unduh Xray core (cek koneksi VPS)"
  mkdir -p /usr/local/bin /usr/local/share/xray
  # Ekstrak binary + .dat (geoip/geosite) — Xray gagal start bila .dat hilang saat routing memakai geosite/geoip
  rm -rf /tmp/xray_ext && unzip -qo /tmp/xray.zip -d /tmp/xray_ext
  cp -f /tmp/xray_ext/xray /usr/local/bin/xray
  cp -f /tmp/xray_ext/*.dat /usr/local/share/xray/ 2>/dev/null || true
  cp -f /tmp/xray_ext/*.dat /usr/local/bin/ 2>/dev/null || true
  chmod +x /usr/local/bin/xray
  rm -rf /tmp/xray.zip /tmp/xray_ext
  systemctl daemon-reload
fi
[ -x /usr/local/bin/xray ] || log "WARNING: binary xray belum ada"
systemctl daemon-reload
systemctl enable xray >/dev/null 2>&1 || true
# Xray access logger wajib folder log ada (deploy VPS baru: folder tak otomatis dibuat)
mkdir -p /var/log/xray && chmod 755 /var/log/xray
[ -f /etc/logrotate.d/xray ] || cat > /etc/logrotate.d/xray <<'LR'
/var/log/xray/*.log {
  daily
  rotate 7
  compress
  missingok
  notifempty
}
LR
# validasi config lalu start (selalu restart bila binary+config ada)
if [ -x /usr/local/bin/xray ] && [ -f /usr/local/etc/xray/config.json ]; then
  /usr/local/bin/xray run -test -c /usr/local/etc/xray/config.json >>"$LOG" 2>&1 \
    && systemctl restart xray \
    || { log "xray run -test gagal, coba start langsung"; systemctl restart xray || log "xray start gagal"; }
  sleep 1
  systemctl is-active xray >/dev/null 2>&1 || log "WARNING: xray belum active (cek: journalctl -u xray -n 20)"
else
  log "xray binary/config belum ada — cek manual"
fi

log "=== [6/9] SSL cert (acme.sh standalone port 80) ==="
mkdir -p /etc/xray/certs

# renew-ssl.sh — auto perpanjang cert (dipanggil cron harian)
cat > /usr/local/bin/renew-ssl.sh <<'RENEW'
#!/usr/bin/env bash
set -e
FORCE_MODE="${1:-}"
DOMAIN=$(grep -E '^DOMAIN=' /etc/tunnel-bot/config.env | cut -d'=' -f2 | tr -d '[:space:]')
[ -z "$DOMAIN" ] && exit 1
if [ "$FORCE_MODE" != "--force" ] && [ -s /etc/xray/certs/fullchain.pem ]; then
    if ! openssl x509 -in /etc/xray/certs/fullchain.pem -noout -issuer 2>/dev/null | grep -qi "VPNServer"; then
        if openssl x509 -checkend 2592000 -noout -in /etc/xray/certs/fullchain.pem >/dev/null 2>&1; then
            exit 0
        fi
    fi
fi
systemctl stop nginx 2>/dev/null || true
fuser -k 80/tcp 2>/dev/null || true
RENEWED=0
if [ -f /root/.acme.sh/acme.sh ]; then
    if /root/.acme.sh/acme.sh --renew -d "${DOMAIN}" --ecc --force || \
       /root/.acme.sh/acme.sh --issue -d "${DOMAIN}" --standalone -k ec-256 --force; then
        /root/.acme.sh/acme.sh --install-cert -d "${DOMAIN}" --ecc \
            --fullchain-file /etc/xray/certs/fullchain.pem \
            --key-file /etc/xray/certs/privkey.pem
        RENEWED=1
    fi
fi
if [ "$RENEWED" -eq 1 ]; then
    cat /etc/xray/certs/fullchain.pem /etc/xray/certs/privkey.pem > /etc/xray/certs/haproxy.pem
    chmod 644 /etc/xray/certs/*
fi
systemctl start nginx 2>/dev/null || true
systemctl reload haproxy 2>/dev/null || systemctl restart haproxy 2>/dev/null || true
RENEW
chmod +x /usr/local/bin/renew-ssl.sh

curl -fsSL https://get.acme.sh | sh -s email=admin@${DOMAIN} >>"$LOG" 2>&1 || log "acme.sh install gagal"
if [ ! -s /etc/xray/certs/fullchain.pem ]; then
  systemctl stop nginx haproxy 2>/dev/null || true; fuser -k 80/tcp 2>/dev/null || true
  /root/.acme.sh/acme.sh --issue -d "$DOMAIN" --standalone -k ec-256 --force >>"$LOG" 2>&1 \
    && /root/.acme.sh/acme.sh --install-cert -d "$DOMAIN" --ecc \
        --fullchain-file /etc/xray/certs/fullchain.pem \
        --key-file /etc/xray/certs/privkey.pem >>"$LOG" 2>&1 \
    && cat /etc/xray/certs/fullchain.pem /etc/xray/certs/privkey.pem > /etc/xray/certs/haproxy.pem \
    && chmod 644 /etc/xray/certs/* \
    && log "SSL diterbitkan utk $DOMAIN" \
    || log "SSL gagal — cek DNS A record ke IP ini, ulangi manual: /root/.acme.sh/acme.sh --issue -d $DOMAIN --standalone -k ec-256 --force"
fi
# cron auto-renew
cat > /etc/cron.d/vpn-ssl-autorenew <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
0 3 * * * root /usr/local/bin/renew-ssl.sh >/var/log/ssl-renew.log 2>&1
CRON
chmod 644 /etc/cron.d/vpn-ssl-autorenew

log "=== [7/9] Start proxy stack (nginx + haproxy + ws-ssh) ==="
nginx -t >/dev/null 2>&1 && systemctl enable --now nginx >/dev/null 2>&1 || log "nginx config error — cek /etc/nginx"
systemctl enable --now haproxy >/dev/null 2>&1 || log "haproxy start gagal"
systemctl enable --now ws-ssh >/dev/null 2>&1 || log "ws-ssh start gagal (config.service)"
systemctl reload haproxy 2>/dev/null || true

log "=== [8/9] Clone & install tunnel-bot ==="
mkdir -p "$DATA" && chmod 700 "$DATA"
if [ -d "$REPO/.git" ]; then
  git -C "$REPO" fetch origin && git -C "$REPO" checkout "$BRANCH" && git -C "$REPO" merge --ff-only "origin/$BRANCH" || true
else
  rm -rf "$REPO"
  if [ -n "$GH_TOKEN" ]; then
    host="$(printf '%s' "$REPO_URL" | sed -E 's#https?://([^/]+)/.*#\1#')"
    printf 'protocol=https\nhost=%s\nusername=oauth2\npassword=%s\n\n' "$host" "$GH_TOKEN" | git credential approve 2>/dev/null || true
  fi
  GIT_TERMINAL_PROMPT=0 git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$REPO" >>"$LOG" 2>&1 || die "clone repo gagal"
fi
python3 -m venv "$REPO/.venv" >>"$LOG" 2>&1
"$REPO/.venv/bin/pip" install --upgrade pip >>"$LOG" 2>&1
"$REPO/.venv/bin/pip" install -r "$REPO/requirements.txt" >>"$LOG" 2>&1

# config.env (idempotent: jangan timpa jika sudah ada)
if [ ! -f "$DATA/config.env" ]; then
  cat > "$DATA/config.env" <<ENV
BOT_TOKEN=$BOT_TOKEN
TOKEN=$BOT_TOKEN
ADMIN_ID=$ADMIN_ID
ADMIN_IDS=$ADMIN_ID
DOMAIN=$DOMAIN
DB_PATH=$DATA/data.db
XRAY_CONFIG=/usr/local/etc/xray/config.json
XRAY_ACCESS_LOG=/var/log/xray/access.log
REPO_URL=$REPO_URL
PREFIKS_USERNAME=sunek-
TZ=Asia/Jakarta
ENV
  chmod 600 "$DATA/config.env"
fi

# pastikan unit tunnel-bot.service menunjuk ke /opt/tunnel-bot & config.env
if [ -f /etc/systemd/system/tunnel-bot.service ]; then
  # pastikan EnvironmentFile menunjuk /etc/tunnel-bot/config.env
  grep -q "EnvironmentFile=$DATA/config.env" /etc/systemd/system/tunnel-bot.service || \
    sed -i "s#EnvironmentFile=.*#EnvironmentFile=$DATA/config.env#" /etc/systemd/system/tunnel-bot.service
fi
systemctl daemon-reload
systemctl enable tunnel-bot >/dev/null 2>&1 || true

log "=== [9/9] SSH keypair master + final start ==="
if [ ! -f /root/.ssh/id_ed25519_vpn ]; then
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519_vpn -C "tgbot-vpn-master" >/dev/null 2>&1
fi
systemctl restart tunnel-bot
sleep 4

echo ""
echo "=============================================================="
echo "✅ DEPLOY SELESAI — $DOMAIN"
echo "=============================================================="
echo ""
echo "Status service:"
for s in tunnel-bot ws-ssh xray haproxy nginx dropbear; do
  st="$(systemctl is-active "$s" 2>/dev/null || echo inactive)"
  if systemctl cat "$s" >/dev/null 2>&1; then
    printf "  %-14s %s\n" "$s" "$st"
  else
    printf "  %-14s tidak terpasang\n" "$s"
  fi
done
echo ""
echo "Log deploy: $LOG"
echo "Langkah lanjutan:"
echo "  1. Di Telegram: /start di bot kamu → panel admin"
echo "  2. Tambah user / atur limit / notifikasi topic"
echo "  3. Jika ada VPS node tambahan: jalankan /root/node-agent-setup.sh <domain-node>"
echo "=============================================================="
