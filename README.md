# tunnel-bot

Bot Telegram untuk mengelola akun tunneling: **SSH, VMess, VLESS, Trojan**, dan
protokol **UDP** (SlowDNS, UDP-Custom, UDPGW, ZiVPN) — lengkap dengan installer
VPS satu jalan, panel user & admin, multi-VPS via SSH key, notifikasi topic,
auto backup, dan auto-expired.

| | |
|---|---|
| Versi | 1.1.0 |
| Bahasa | Python 3.10+ (aiogram 3) |
| Target OS | Ubuntu 22.04 (teruji), Debian 11+ |
| Zona waktu | `Asia/Jakarta` (WIB) — semua expired dihitung dalam WIB |
| Status | Produksi |

---

## Protokol

| Protokol | Transport | Keterangan |
|----------|-----------|------------|
| SSH | Dropbear 2019.78 (port 90 / 109 / 143) | akun user biasa |
| Trojan / VLESS / VMess | Xray (WS + gRPC + WS upgrade) | akun Xray, sync via hot-sync |
| SlowDNS (VayDNS) | DNS-over-UDP `:53` → `:5300` | tunnel via DNS, mux multi-protokol |
| UDP-Custom | UDP `1-65535` → `:36712` | UDP mentah |
| UDPGW (badvpn) | UDP `:7300` | UDP gateway |
| ZiVPN | UDP `6000-19999` → `:5667` | UDP proxy, auth password |

---

## Pasang (VPS baru)

```bash
git clone https://github.com/sunekugg/tunelprobot.git /opt/tunnel-bot
cd /opt/tunnel-bot
bash install-full-vpn.sh
```

Installer interaktif menanyakan **token bot**, **ID admin**, dan **domain**.
Opsional lewat argumen:

```bash
bash install-full-vpn.sh <TOKEN> <ADMIN_ID> <DOMAIN> [REPO_URL] [BRANCH] [OLD_DOMAIN]
# token GitHub private repo: GH_TOKEN=ghp_xxx bash install-full-vpn.sh ...
```

Yang dipasang oleh `install-full-vpn.sh` (9 langkah, idempotent):

1. Persiapan sistem — timezone WIB, `nf_conntrack_max=65536`, bersihkan instalasi lama.
2. Dependency dasar — git, nginx, haproxy, iptables, vnstat, python3-venv, dll.
3. **Dropbear 2019.78** dikompilasi dari source — wajib `CFLAGS='-O2 -fcommon'`
   (GCC11 default `-fno-common` menghasilkan binary rusak), host key rsa+ecdsa
   (2019 tak dukung ed25519), binary ke `/usr/sbin`, port 90/109/143.
4. Restore konfigurasi live dari snapshot (xray, haproxy, nginx, ws-ssh, unit systemd).
5. Xray core + unit systemd (wajib `User=root`, folder `/var/log/xray` harus ada).
6. Sertifikat SSL via `acme.sh` standalone di port 80.
7. Proxy stack: nginx + haproxy + ws-ssh.
8. Clone/upgrade repo, venv, `pip install -r requirements.txt`, sinkron akun Xray,
   tulis `/etc/tunnel-bot/config.env`.
8b. **Stack UDP** — `install_udp_stack()` dari `bot/system_manager.py`
    (SlowDNS mux + UDP-Custom + UDPGW + ZiVPN). Idempotent.
9. SSH keypair master + start `tunnel-bot.service`, lalu cetak status semua service.

> Port 80 milik nginx (`vpn-multiplex.conf` listen 80/8080/2052/2082/8880) —
> jangan bind `:80` di haproxy.

### Instalasi ringkas (alternatif)

`install.sh` adalah bootstrap minimal (229 baris): siapkan OS, clone repo ke
`/opt/tunnel-bot`, buat venv, pasang unit systemd. Pakai ini bila komponen VPN
sudah terpasang sendiri.

---

## Update (VPS lama)

```bash
cd /opt/tunnel-bot && git pull && systemctl restart tunnel-bot
```

Atau jalankan ulang `install-full-vpn.sh` — idempotent, tak merusak data/config.

---

## Struktur

```
bot/
  main.py            entry point, pendaftaran router, startup tasks
  config.py          env config, prefix username, zona waktu WIB
  db.py              SQLite async (akun, server, setting, log, referral)
  system_manager.py  engine: akun SSH/Xray/ZiVPN, remote multi-VPS, stack UDP, statistik
  handlers_user.py   alur user: buat / trial / pindah server / kelola akun
  handlers_admin.py  panel admin: server, akun, kuota, speed, broadcast
  formatter.py       format output tiap protokol (format_zivpn_output, dll.)
  scheduler.py       tugas terjadwal: expired, kuota, laporan harian
  expiry_cleaner.py  hapus akun expired dari server
  sub_server.py      subscription server
  login_notifier.py  notifikasi login SSH
  force_join.py      paksa join channel
  keyboards.py       papan tombol inline
  states.py          FSM state
  ui_theme.py        tema tampilan (emoji, layout)
  ws-ssh.py          helper SSH over WebSocket
  _slowdns_fmt.py    formatter SlowDNS
scripts/
  components.sh      komponen installer
  ssh.sh / xray.sh / domain.sh   setup per-komponen
  slowdns_mux.py     SlowDNS multi-protokol mux
  xray_hot_sync.py   sinkronisasi akun Xray tanpa restart
  vpn_bw_manager.py  bandwidth manager per-akun
  deploy_slowdns_multi.py
  backup.sh / health.sh / check-install.sh
  systemd/           unit service tambahan
tests/               self-check tanpa framework
templates/           banner HTML & welcome default
tunnel               perintah CLI kontrol dari SSH
install.sh           bootstrap ringkas
install-full-vpn.sh  installer penuh 9 langkah
tunnel-bot.service   unit systemd
```

---

## Uji

```bash
.venv/bin/python tests/test_zivpn.py
.venv/bin/python tests/test_udp_stack.py
.venv/bin/python tests/test_slowdns_mux.py
.venv/bin/python tests/test_referral.py
```

---

## Data & konfigurasi

| Path | Isi |
|------|-----|
| `/etc/tunnel-bot/data.db` | SQLite (akun, server, setting, log, referral) |
| `/etc/tunnel-bot/config.env` | `BOT_TOKEN`, `ADMIN_IDS`, `DOMAIN`, `DB_PATH`, `XRAY_CONFIG`, `XRAY_ACCESS_LOG`, `PREFIKS_USERNAME`, `TZ` |
| `/usr/local/etc/xray/config.json` | config kanonik Xray (di-symlink dari `/etc/xray/config.json`) |
| `/etc/zivpn/config.json` | auth password ZiVPN |
| `/usr/local/bin/xray-hot-sync.py` | script hot-sync akun Xray |
| `/root/node-agent-setup.sh` | setup node VPS tambahan |
| `/var/log/tunnel-bot-deploy.log` | log installer |

### Multi-VPS

VPS node tambahan di kelola lewat SSH key master (`/root/.ssh/id_ed25519_vpn`).
Setelah install, jalankan di node baru:

```bash
/root/node-agent-setup.sh <domain-node>
```

---

## Keamanan

- **IP VPS tidak boleh** muncul di Telegram, notifikasi, atau log bot.
- Token, password admin, dan SSH key **tidak pernah** di-commit. `.gitignore`
  memblokir `config.env`, `*.db`, `.env`, `*.tar.gz`.
- `config.env` di-`chmod 600`.
- Trial (`is_trial=1`) selalu `ip_limit=0`; limit IP hanya untuk akun reguler.
- Kuota ditegakkan scheduler: server dengan total pemakaian ≥ kuota otomatis
  di-set `is_active=0`.

---

## Lisensi

Penggunaan internal. Hubungi pemilik repo untuk lisensi lain.
