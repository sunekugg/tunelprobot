# tunnel-bot — Status

Terakhir diperbarui: 2026-11-17

## Status: produksi

Bot jalan di VPS produksi (tunnel-bot, xray, dropbear, nginx, haproxy, ws-ssh,
slowdns, udp-custom, udpgw, zivpn — semua aktif).

## Terakhir dikerjakan

- **ZiVPN (UDP) end-to-end** (`92275e8`→`d829a3d`): binary amd64/arm64, cert
  self-signed, config auth password, unit systemd, pasang otomatis via
  `install_udp_stack()`, menu Buat/Trial ZiVPN, Pindah Server, Ganti Password,
  panel admin, auto-expired.
- **Pembersihan repo**: hapus 9 file migrasi one-off (`patch_*.py`, `test_xff.py`),
  duplikat `ws-ssh.py` root, dan 3 test basi (`test_fase1/34/5`) yang mengimpor
  modul yang sudah tak ada. README & STATUS ditulis ulang.

## Akar masalah yang diperbaiki (ZiVPN)

1. **Rule NAT ZiVPN kalah urutan** — ada VPS dengan rule DNAT usang di atas
   rule baru (`5301:65535 → 36712`, `1:52 → 36712`) sehingga traffic udp
   6000–19999 tersedot ke udp-custom. Fix: ZiVPN di-`-I PREROUTING 1`
   (insert posisi teratas, idempoten), bukan `-A`.
2. **`create:zivpn:0` tak pernah cocok** — `cb_picked_server` membandingkan
   `act == "create:zivpn_0"`, padahal `act` sudah di-replace `:`→`_`. Fix:
   cocokkan `create_zivpn_0/1`. (Bug ini membuat Buat/Trial ZiVPN mati total.)
3. **Backslash dalam f-string** — Python 3.10 (Ubuntu 22.04) menolak `\` di
   dalam `{}` f-string → SyntaxError saat bot start. Fix: pindahkan logika
   keluar dari f-string.
4. **`cb_my_accounts() missing 'state'`** — pemanggilan lama tak meneruskan
   `state` → TypeError saat hapus akun. Fix: teruskan `state`.

## Catatan desain ZiVPN

- Kredensial = password plaintext (`acc_type='zivpn'`, `username =
  password_or_uuid = password`).
- Regex ZiVPN hanya menerima alfanumerik → prefix di-join **tanpa** `-`
  (tanda `-` di-strip).
- Remote multi-VPS memakai python3-inline yang mengedit `/etc/zivpn/config.json`
  `auth.config` langsung, tanpa bergantung pada repo di node tujuan. Array
  password tak pernah dikosongkan (sisakan minimal `zivpn`).

## File kunci

- `bot/system_manager.py` (engine ZiVPN + stack UDP + remote multi-VPS)
- `bot/handlers_user.py` (alur Buat/Trial/Kelola ZiVPN)
- `bot/formatter.py` (`format_zivpn_output`)
- `bot/scheduler.py`, `bot/expiry_cleaner.py` (daur ulang akun)
- `install-full-vpn.sh` (installer penuh)

## Branch aktif

`dev` (origin: https://github.com/sunekugg/tunelprobot)
