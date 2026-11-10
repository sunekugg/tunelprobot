"""Self-check alur Referral Otomatis: register → reward → bonus-create → cek limit.
Jalankan via python3 tests/test_referral.py (pakai DB sementara, tanpa bot nyata)."""
import os, sys, tempfile, aiosqlite, asyncio

# Redirect DB_PATH bot ke file sementara SEBELUM import modul bot
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch DB_PATH di config (dibaca sekali oleh modul bot.db)
import bot.config as _cfg
_cfg.DB_PATH = _tmp.name

from bot import db as D  # noqa: E402

async def main():
    await D.init_db()
    A, B = 111, 222  # A=pengundang, B=teman

    # Seed: A sebagai user biasa
    async with aiosqlite.connect(_cfg.DB_PATH) as d:
        await d.execute("INSERT INTO users (tg_id, username) VALUES (?, 'inviter')", (A,))
        await d.execute("UPDATE settings SET value='1' WHERE key='feat_referral'")
        # Kuota user = 1 per 1 hari
        await d.execute("UPDATE settings SET value='1' WHERE key='daily_create_limit'")
        await d.execute("UPDATE settings SET value='1d' WHERE key='create_limit_window'")
        await d.commit()

    # 1. B bukan user lama → referral tercatat
    ok = await D.try_register_referral(B, f"ref_{A}")
    assert ok, "referral baru harus tercatat"

    # 2. Curang: A mencoba pakai link sendiri
    assert not await D.try_register_referral(A, f"ref_{A}"), "referral diri sendiri harus gagal"

    # 3. Curang: user lama mencoba register referral
    assert not await D.try_register_referral(A, f"ref_{B}"), "user lama tak boleh jadi referral"

    # 4. Cek kuota B = 0/1
    ok, used, mx, _ = await D.check_daily_creation_quota(B)
    assert ok and used == 0 and mx == 1, f"kuota awal B salah: ok={ok} used={used} max={mx}"

    # 5. B buat akun pertama → reward terkirim ke A
    await D.record_account_creation(B, "vmess", "sunek-b1")  # bot=None (tak kirim notif)
    async with aiosqlite.connect(_cfg.DB_PATH) as d:
        b = (await (await d.execute("SELECT ref_rewarded FROM users WHERE tg_id=?", (B,))).fetchone())[0]
        a_bonus = (await (await d.execute("SELECT bonus_create FROM users WHERE tg_id=?", (A,))).fetchone())[0]
    assert b == 1, f"ref_rewarded B harus 1, dapat {b}"
    assert a_bonus == 1, f"bonus A harus +1, dapat {a_bonus}"

    # 6. B buat akun kedua → reward TIDAK dobel
    await D.record_account_creation(B, "vless", "sunek-b2")
    async with aiosqlite.connect(_cfg.DB_PATH) as d:
        a_bonus2 = (await (await d.execute("SELECT bonus_create FROM users WHERE tg_id=?", (A,))).fetchone())[0]
    assert a_bonus2 == 1, f"bonus A tak boleh dobel, dapat {a_bonus2}"

    # 7. A habis kuota reguler (1/1d) → lalu bonus menembus limit
    await D.record_account_creation(A, "vmess", "sunek-a1")   # 1/1 → penuh
    ok, _, _, _ = await D.check_daily_creation_quota(A)
    assert not ok, "kuota A harus penuh (1/1)"
    # Konsumsi bonus (dipanggil di record_account_creation otomatis saat reguler penuh)
    await D.record_account_creation(A, "vless", "sunek-a2")
    async with aiosqlite.connect(_cfg.DB_PATH) as d:
        a_bonus3 = (await (await d.execute("SELECT bonus_create FROM users WHERE tg_id=?", (A,))).fetchone())[0]
    assert a_bonus3 == 0, f"bonus A harus habis, masih {a_bonus3}"

    # 8. get_ref_stats A: 1 sukses, 0 sisa, 0 pending
    sukses, sisa, pending = await D.get_ref_stats(A)
    assert sukses == 1 and sisa == 0 and pending == 0, f"stats salah: {sukses},{sisa},{pending}"

    print("✅ Semua uji Referral Otomatis LULUS")
    return True

if __name__ == "__main__":
    asyncio.run(main())
