from __future__ import annotations
import asyncio
from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    TelegramMethod, SendMessage, EditMessageText, DeleteMessage
)
from aiogram.types import Message, TelegramObject

_CHAT_NAV_LOCKS: dict[int, asyncio.Lock] = {}

def get_chat_nav_lock(chat_id: int) -> asyncio.Lock:
    lk = _CHAT_NAV_LOCKS.get(chat_id)
    if lk is None:
        lk = asyncio.Lock()
        _CHAT_NAV_LOCKS[chat_id] = lk
    return lk

def track_bot_msg(chat_id: int | str | None, message_id: int | None):
    # Dinonaktifkan sesuai permintaan: /start tidak menghapus pesan sebelumnya
    return

def untrack_bot_msg(chat_id: int | str | None, message_id: int | None):
    return

async def cleanup_previous_menus(bot: Bot, chat_id: int, keep_mids: set[int] | None = None, **kwargs):
    # Dinonaktifkan sesuai permintaan: biarkan pesan/menu sebelumnya tetap ada di ruang chat
    return

class CallbackTrackerOuterMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        return await handler(event, data)

async def session_menu_tracker_middleware(make_request, bot: Bot, method: TelegramMethod):
    try:
        return await make_request(bot, method)
    except TelegramBadRequest as e:
        err_l = str(e).lower()
        if "message is not modified" in err_l:
            return True
        if isinstance(method, EditMessageText) and (
            "there is no text in the message to edit" in err_l
            or "message can't be edited" in err_l
            or "message to edit not found" in err_l
        ):
            if method.chat_id:
                new_res = await make_request(
                    bot,
                    SendMessage(
                        chat_id=method.chat_id,
                        text=method.text,
                        parse_mode=method.parse_mode,
                        reply_markup=method.reply_markup,
                        link_preview_options=method.link_preview_options,
                    )
                )
                if method.message_id:
                    try:
                        await make_request(bot, DeleteMessage(chat_id=method.chat_id, message_id=method.message_id))
                    except Exception:
                        pass
                return new_res
        raise