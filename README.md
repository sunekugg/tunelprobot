# tunnel-bot

Bot Telegram untuk mengelola akun tunneling: SSH, VMess, VLESS, Trojan,
dan protokol UDP (SlowDNS, UDP-Custom, UDPGW, ZiVPN).

**STATUS: produksi.** Installer penuh + panel Telegram (user & admin),
multi-VPS via SSH key, notifikasi topic, auto backup, protokol UDP.

## Protokol

| Protokol | Transport | Keterangan |
|----------|-----------|------------|
| SSH | Dropbear 2019.78 (port 90/109/143) | akun user biasa |
| Trojan / VLESS / VMess | Xray (WS + gRPC + upgrade) | akun Xray |
| SlowDNS | DNS-over-UDP (`:53`→`:5300`) | tunnel via DNS, mux multi-protokol |
| UDP-Custom | UDP `1-65535` → `:36712` | UDP mentah |
| UDPGW (badvpn) | UDP `:7300` | UDP gateway |
| ZiVPN | UDP `6000-19999` → `:5667` | UDP proxy, auth password |

## Pasang (VPS baru)

```bash
bash install-full-vpn.sh
```

Installer menyiapkan Dropbear, Xray, nginx, haproxy, ws-ssh, dan stack UDP
(SlowDNS + UDP-Custom + UDPGW + ZiVPN) lewat `install_udp_stack()`.

## Update (VPS lama)

```bash
cd /opt/tunnel-bot && git pull && systemctl restart tunnel-bot
```

## Struktur

- `bot/main.py` — entry point & pendaftaran router
- `bot/config.py` — konfigurasi, prefix username, zona waktu WIB
- `bot/db.py` — SQLite (akun, server, setting, log, referral)
- `bot/system_manager.py` — engine: akun SSH/Xray/ZiVPN, remote multi-VPS, stack UDP, statistik
- `bot/handlers_user.py` — alur user (buat/trial/pindah server/kelola akun)
- `bot/handlers_admin.py` — panel admin (server, akun, kuota, speed, broadcast)
- `bot/formatter.py` — format output tiap protokol
- `bot/scheduler.py` — tugas terjadwal (expired, kuota, laporan)
- `bot/expiry_cleaner.py` — hapus akun expired dari server
- `bot/sub_server.py` — subscription server
- `bot/keyboards.py`, `bot/states.py`, `bot/ui_theme.py` — UI & FSM
- `scripts/` — skrip shell komponen, SlowDNS mux, Xray hot-sync, bandwidth manager
- `tests/` — self-check tanpa framework
- `templates/` — banner HTML & welcome default
- `tunnel` — perintah CLI kontrol dari SSH

## Uji

```bash
.venv/bin/python tests/test_zivpn.py
.venv/bin/python tests/test_udp_stack.py
.venv/bin/python tests/test_slowdns_mux.py
.venv/bin/python tests/test_referral.py
```

## Data

- SQLite: `/etc/tunnel-bot/data.db`
- Konfigurasi: `/etc/tunnel-bot/config.env` (token, ADMIN_IDS, domain, ID grup, ID topic)
- Config ZiVPN: `/etc/zivpn/config.json` (auth password)

## Zona waktu

Server di-set `Asia/Jakarta`. Semua expired dihitung dalam WIB.

## Keamanan

- IP VPS **tidak boleh** muncul di Telegram, notifikasi, atau log bot.
- Token, password admin, dan key **tidak pernah** di-commit ke repo.
