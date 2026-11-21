"""Self-check pilih durasi admin: keyboard + callback + tulis expired_at (tanpa Telegram)."""
import asyncio
import datetime
import os
import tempfile
from types import SimpleNamespace

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name

import aiosqlite
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from bot import db as bot_db
from bot import handlers_admin as ha
from bot.keyboards import duration_picker_kb
from bot.states import AdminAccountEditFSM


async def _noop(*args, **kwargs):
    return None


async def _is_admin(uid):
    return True


async def main():
    kb = duration_picker_kb("set_accexp_7", "adm_acc_detail:7")
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == [
        "set_accexp_7:1d", "set_accexp_7:2d", "set_accexp_7:3d",
        "set_accexp_7:7d", "set_accexp_7:14d", "set_accexp_7:30d",
        "set_accexp_7:0", "set_accexp_7:manual", "adm_acc_detail:7",
    ], cbs

    # Tombol durasi muncul saat FSM berada di state `duration`, bukan `username`.
    from aiogram.fsm.state import State as AiogramState
    state_filter = next(
        f.callback
        for h in ha.router.callback_query.handlers
        if h.callback == ha.fsm_adm_cdur_pick
        for f in h.filters
        if isinstance(f.callback, AiogramState)
    )
    assert state_filter.state == "AdminCustomAccountFSM:duration", state_filter.state

    await bot_db.init_db()
    async with aiosqlite.connect(bot_db.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO accounts (id, tg_id, acc_type, username, expired_at) VALUES (?, ?, ?, ?, ?)",
            (9001, 1, "ssh", "test-dur-9001", "2020-01-01 00:00:00"),
        )
        await conn.commit()

    old_guard, old_backup = ha.is_user_admin, ha.generate_portable_backup_sh
    ha.is_user_admin, ha.generate_portable_backup_sh = _is_admin, _noop
    ctx = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    try:
        # Callback preset diterjemahkan ke teks durasi sebelum diproses handler pesan.
        seen = []
        old_edit = ha.fsm_adm_acc_edit_val

        async def _record(message, state):
            seen.append(message.text)

        ha.fsm_adm_acc_edit_val = _record
        call = SimpleNamespace(
            data="set_accexp_9001:2d",
            from_user=SimpleNamespace(id=1),
            answer=_noop,
            message=None,
        )
        await ctx.set_state(AdminAccountEditFSM.value)
        await ctx.set_data({"acc_id": 9001, "action": "duration"})
        await ha.cb_set_accexp(call, ctx)
        assert seen == ["2d"], seen

        # Handler pesan benar-benar menulis expired_at sesuai durasi.
        ha.fsm_adm_acc_edit_val = old_edit
        fake_msg = SimpleNamespace(
            text="2d",
            from_user=SimpleNamespace(id=1),
            answer=_noop,
        )
        await ctx.set_state(AdminAccountEditFSM.value)
        await ctx.set_data({"acc_id": 9001, "action": "duration"})
        await ha.fsm_adm_acc_edit_val(fake_msg, ctx)

        async with aiosqlite.connect(bot_db.DB_PATH) as conn:
            async with conn.execute("SELECT expired_at FROM accounts WHERE id = 9001") as cur:
                row = await cur.fetchone()
        assert row, "akun uji hilang"
        exp = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        delta = exp - ha.now_wib()
        # expired_at dibulatkan ke detik, jadi ≤1 detik lebih pendek dari 2 hari penuh.
        assert datetime.timedelta(days=1, hours=23, minutes=59) < delta <= datetime.timedelta(days=2), delta
    finally:
        ha.is_user_admin, ha.generate_portable_backup_sh = old_guard, old_backup
        if 'old_edit' in locals():
            ha.fsm_adm_acc_edit_val = old_edit
        await ctx.clear()
        os.unlink(bot_db.DB_PATH)

    print("ADMIN_DURATION_OK")


if __name__ == "__main__":
    asyncio.run(main())
