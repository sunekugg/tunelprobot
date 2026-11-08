from __future__ import annotations
import time
import aiosqlite
from aiogram import BaseMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from .config import DB_PATH
from .db import is_user_admin, get_setting

_JOIN_CACHE: dict[int, tuple[float, bool, list[tuple[str, str]]]] = {}

def invalidate_join_cache(tg_id: int = 0):
    if tg_id:
        _JOIN_CACHE.pop(tg_id, None)
    else:
        _JOIN_CACHE.clear()

async def check_user_membership(bot, tg_id: int, force_refresh: bool = False) -> tuple[bool, list[tuple[str, str]]]:
    if await is_user_admin(tg_id):
        return True, []
    if await get_setting("force_join_enabled") != "1":
        return True, []

    now = time.time()
    if not force_refresh and tg_id in _JOIN_CACHE:
        ts, ok, missing = _JOIN_CACHE[tg_id]
        if now - ts < 60:
            return ok, missing

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT chat_id, title, invite_link FROM force_join_chats WHERE is_active = 1") as cur:
            chats = await cur.fetchall()

    if not chats:
        _JOIN_CACHE[tg_id] = (now, True, [])
        return True, []

    missing: list[tuple[str, str]] = []
    for chat_id, title, invite_link in chats:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=tg_id)
            if member.status in ("creator", "administrator", "member") or (member.status == "restricted" and getattr(member, "is_member", False)):
                continue
            missing.append((title or chat_id, invite_link or f"https://t.me/{str(chat_id).lstrip('@')}"))
        except Exception:
            continue

    is_ok = len(missing) == 0
    _JOIN_CACHE[tg_id] = (now, is_ok, missing)
    return is_ok, missing

class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        bot = data["bot"]
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        # Jika pesan berasal dari Grup / Supergroup, hanya izinkan command /id atau /setlog dari Admin
        if isinstance(event, Message) and event.chat.type in ("group", "supergroup"):
            txt = (event.text or "").strip()
            if txt.startswith(("/id", "/setlog")):
                if await is_user_admin(user.id):
                    return await handler(event, data)
            return

        is_adm = await is_user_admin(user.id)
        if await get_setting("bot_enabled") != "1" and not is_adm:
            if isinstance(event, CallbackQuery):
                await event.answer("🔧 Bot sedang OFF / Maintenance.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("🔧 <b>Bot sedang dinonaktifkan sementara (Maintenance) oleh Administrator.</b>")
            return

        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            async with db.execute("SELECT is_banned FROM users WHERE tg_id = ?", (user.id,)) as cur:
                row = await cur.fetchone()
                if row and row[0] == 1 and not is_adm:
                    if isinstance(event, CallbackQuery):
                        await event.answer("🚫 Akun Anda telah diblokir oleh Admin.", show_alert=True)
                    elif isinstance(event, Message):
                        await event.answer("🚫 Akun Anda telah diblokir oleh Admin.")
                    return

        if isinstance(event, CallbackQuery) and event.data == "verify_force_join":
            return await handler(event, data)

        is_ok, missing = await check_user_membership(bot, user.id, force_refresh=False)
        if is_ok:
            return await handler(event, data)

        fj_text = await get_setting("force_join_text")
        buttons = [[InlineKeyboardButton(text=f"📌 Join {title}", url=link)] for title, link in missing]
        buttons.append([InlineKeyboardButton(text="✅ Saya Sudah Join", callback_data="verify_force_join")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        if isinstance(event, Message):
            await event.answer(fj_text, reply_markup=kb)
        elif isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(fj_text, reply_markup=kb)
            except Exception:
                await event.message.answer(fj_text, reply_markup=kb)
            await event.answer("⚠️ Kamu wajib join grup/channel terlebih dahulu!", show_alert=True)
        return