# tunnel-bot

Bot Telegram untuk mengelola akun tunneling (SSH, VMess, VLESS, Trojan).

**STATUS: Fase 1 (fondasi) — belum siap produksi.**

Lihat `blueprint.md` untuk spesifikasi lengkap. Repo ini dipisah dari
`tunnel-allinone` dan **tidak boleh** mengubah VPS produksi.

## Pasang (VPS baru)

```
bash <(curl -fsSL <URL_REPO>/install.sh)
```

Input interaktif: token bot Telegram, ID admin utama (boleh lebih dari satu,
dipisah koma), domain utama, URL repo GitHub.
ID grup / topic / wajib join diatur dari menu admin di dalam bot.

## Update (VPS lama)

```
tunnel update
```

Data akun, admin, setting, dan interval tidak diubah.

## Struktur

- `bot/main.py` — entry point & router handler
- `bot/config.py` — baca/tulis config.env
- `bot/db.py` — SQLite (akun, admin, setting, interval, log, topic, vps)
- `bot/menus.py` — keyboard menu user & admin
- `bot/accounts.py` — buat/trial/hapus/limit IP/expired
- `bot/formats.py` — format output SSH/VMess/VLESS/Trojan
- `bot/notify.py` — notifikasi topic grup & user
- `bot/scheduler.py` — tugas terjadwal berdasarkan interval DB
- `bot/admin.py` — menu admin
- `bot/vps.py` — tambah VPS & pasang komponen dari bot
- `scripts/` — skrip shell komponen, SSH, Xray, domain, backup, health
- `templates/` — banner HTML & welcome default
- `tunnel` — perintah CLI kontrol penuh dari SSH

## Perintah `tunnel`

`status`, `restart`, `update`, `backup`, `restore`, `log`, `domain`,
`komponen`, `vps-tambah`, `cek-port`, `uninstall`.

## Data

- SQLite: `/etc/tunnel-bot/data.db`
- Konfigurasi: `/etc/tunnel-bot/config.env` (token, ADMIN_IDS, domain, ID grup, ID topic)
- Log: `/var/log/tunnel-bot.log` (tanpa IP VPS yang bisa dilihat pengguna)

## Zona waktu

Server di-set `Asia/Jakarta`. Semua expired dihitung dalam WIB.

## Keamanan

- IP VPS **tidak boleh** muncul di Telegram, notifikasi, atau log bot.
- Token, password admin, dan key **tidak pernah** di-commit ke repo.
