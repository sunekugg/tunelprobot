# Tunnel-bot — Status & Akar Masalah yang Diperbaiki

Terakhir diperbarui: 2026-11-08

## Fase selesai
- **Fondasi** (F1): repo `tunnel-bot`, struktur folder, config, DB, VERSION.
- **Adopsi penuh bot lama (aiogram 3)**: 17 modul (~12.5k baris) dipindah dari installer `install-full-vpn.sh` ke `bot/`. Semua modul import bersih.
- **Deployment VPS uji** (103.92.214.240): xray, nginx, haproxy, dropbear, ws-ssh aktif; tunnel-bot service berjalan.
- **Ganti domain** per-server dari panel admin (FSM edit_domain, tombol 🌐 di daftar server).
- **Prefix `sunek-` otomatis** di semua buat/trial akun SSH & Xray.

## Akar masalah yang diperbaiki
1. **Skema DB bercampur** — DB lama (skema PTB) punya `protocol NOT NULL`; bot baru pakai kolom baru (`acc_type`, dll). Fix: reset DB ke skema persis install-full-vpn.sh + guard migrasi.
2. **Sandbox systemd memblokir useradd** — `ProtectSystem=full` membuat /etc read-only di dalam service → `useradd` gagal "cannot lock /etc/passwd" → buat akun SSH selalu error. Fix: hapus `ProtectSystem=full` dari `tunnel-bot.service` (bot manajer VPN butuh akses root penuh ke /etc).
3. **Bot lama (tgbot-vpn) hidup lagi** — konflik polling Telegram. Fix: unit file dipindah ke `.disabled` + proses dimatikan.
4. **Domain lokal ditimpa config.env tiap restart** — fix: hapus `UPDATE servers SET domain=... WHERE is_local=1`.

## File kunci
- `bot/` (config.py, db.py, system_manager.py, handlers_user.py, handlers_admin.py, dsb)
- `tunnel-bot.service` (tanpa ProtectSystem=full)
- `install.sh`, `requirements.txt`

## Branch aktif
`dev` (origin: https://github.com/sunekugg/tunelprobot)
