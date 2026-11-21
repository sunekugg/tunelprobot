"""Self-check durasi akun dipilih Admin dari menu utama (tanpa Telegram nyata)."""
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
from bot import handlers_user as hu
from bot.keyboards import duration_picker_kb
from bot.states import AdminAccountEditFSM


async def _noop(*args, **kwargs):
    return None


async def _is_admin(uid):
    return True


async def _not_admin(uid):
    return False


async def main():
    kb = duration_picker_kb("create_duration", "menu_main")
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "create_duration:2d" in cbs and "create_duration:manual" in cbs, cbs

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
    old_get_server, old_is_admin = hu.get_server_by_id, hu.is_user_admin
    try:
        async def _fake_server(sid):
            return {
                "id": sid, "name": "Server Utama", "domain": "example.com",
                "max_users": 100, "used_users": 0,
            }
        hu.get_server_by_id = _fake_server
        hu.is_user_admin = _is_admin

        # Preset durasi menu utama tersimpan di FSM untuk akun baru.
        call = SimpleNamespace(
            data="create_duration:2d",
            from_user=SimpleNamespace(id=1),
            answer=_noop,
            message=None,
        )
        await ctx.set_data({
            "create_action": "create_ssh",
            "server_id": 1,
            "started_at": 0,  # regresi: durasi lama bikin step berikut timeout
        })
        await hu.cb_create_duration(call, ctx)
        after = await ctx.get_data()
        assert after.get("duration") == "2d"
        assert after.get("started_at", 0) > __import__("time").time() - 5, after

        # Non-admin ditolak.
        answers = []

        async def _answer(text, **kw):
            answers.append(text)

        hu.is_user_admin = _not_admin
        denied = SimpleNamespace(
            data="create_duration:7d",
            from_user=SimpleNamespace(id=2),
            answer=_answer,
            message=None,
        )
        await hu.cb_create_duration(denied, ctx)
        assert any("Administrator" in a for a in answers), answers

        #Perpanjang akun via panel admin tetap menulis expired_at.
        seen = []
        old_edit = ha.fsm_adm_acc_edit_val

        async def _record(message, state):
            seen.append(message.text)

        ha.fsm_adm_acc_edit_val = _record
        acall = SimpleNamespace(
            data="set_accexp_9001:2d",
            from_user=SimpleNamespace(id=1),
            answer=_noop,
            message=None,
        )
        await ctx.set_state(AdminAccountEditFSM.value)
        await ctx.set_data({"acc_id": 9001, "action": "duration"})
        await ha.cb_set_accexp(acall, ctx)
        assert seen == ["2d"], seen

        ha.fsm_adm_acc_edit_val = old_edit
        fake_msg = SimpleNamespace(text="2d", from_user=SimpleNamespace(id=1), answer=_noop)
        await ctx.set_state(AdminAccountEditFSM.value)
        await ctx.set_data({"acc_id": 9001, "action": "duration"})
        await ha.fsm_adm_acc_edit_val(fake_msg, ctx)
        async with aiosqlite.connect(bot_db.DB_PATH) as conn:
            async with conn.execute("SELECT expired_at FROM accounts WHERE id = 9001") as cur:
                row = await cur.fetchone()
        assert row, "akun uji hilang"
        exp = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        delta = exp - ha.now_wib()
        assert datetime.timedelta(days=1, hours=23, minutes=59) < delta <= datetime.timedelta(days=2), delta
    finally:
        ha.is_user_admin, ha.generate_portable_backup_sh = old_guard, old_backup
        hu.get_server_by_id, hu.is_user_admin = old_get_server, old_is_admin
        if 'old_edit' in locals():
            ha.fsm_adm_acc_edit_val = old_edit
        await ctx.clear()
        os.unlink(bot_db.DB_PATH)

    print("MENU_DURATION_OK")


if __name__ == "__main__":
    asyncio.run(main())
