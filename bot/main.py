import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery

from .config import BOT_TOKEN
from .db import init_db
from .force_join import ForceJoinMiddleware
from .menu_tracker import CallbackTrackerOuterMiddleware, session_menu_tracker_middleware
from . import handlers_user as user, handlers_admin as admin
from .system_manager import sync_xray_clients
from .sub_server import start_subscription_web_server
from .scheduler import scheduler_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout
)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
# Pasang Session Middleware untuk melacak semua pesan menu & menangani transisi Foto <-> Teks otomatis
bot.session.middleware(session_menu_tracker_middleware)

dp = Dispatcher(storage=MemoryStorage())

# Pasang Outer Middleware untuk mencatat pesan menu setiap kali tombol diklik
dp.callback_query.outer_middleware(CallbackTrackerOuterMiddleware())

# Pasang middleware Force Join
dp.message.middleware(ForceJoinMiddleware())
dp.callback_query.middleware(ForceJoinMiddleware())

# Handler /start, /menu & menu_main terpusat di Root Dispatcher (Prioritas Tertinggi)
@dp.message(Command("start", "menu"), StateFilter("*"))
@dp.message(F.text.in_({"✨ Buat Config Baru", "❌ Batal / Reset", "🚀 Menu Utama (/start)"}), StateFilter("*"))
async def root_cmd_start(message: Message, state: FSMContext, command: CommandObject | None = None):
    if command and command.args:
        await user.cmd_start(message, state, command)
        return
    await user.cmd_start(message, state)

@dp.callback_query(F.data == "menu_main", StateFilter("*"))
async def root_cb_menu_main(call: CallbackQuery, state: FSMContext):
    await user.cb_menu_main(call, state)

# Daftarkan router Admin dan User
dp.include_router(admin.router)
dp.include_router(user.router)

async def _post_startup_tasks(bot_instance: Bot):
    await start_subscription_web_server()
    await asyncio.sleep(1)
    try:
        await sync_xray_clients()
    except Exception as e:
        logging.error(f"Startup Xray sync error: {e}")
    _ensure_sshpass()
    asyncio.create_task(scheduler_loop(bot_instance))


def _ensure_sshpass() -> None:
    """self-heal: sshpass dibutuhkan cek server khusus (system_manager). Kalau binary
    hilang (VPS lama dari installer sebelum 6eeeda1), pasang via apt sekali saat startup."""
    import shutil, subprocess
    if shutil.which("sshpass"):
        return
    try:
        subprocess.run(["apt-get", "install", "-y", "sshpass"],
                       capture_output=True, timeout=180)
        ok = bool(shutil.which("sshpass"))
        logging.info(f"Startup self-heal sshpass: {'terpasang' if ok else 'GAGAL — pasang manual'}")
    except Exception as e:
        logging.error(f"Startup self-heal sshpass error: {e}")

async def main():
    await init_db()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logging.warning(f"Delete webhook warning: {e}")

    asyncio.create_task(_post_startup_tasks(bot))
    logging.info("🚀 Bot VPN siap dan mulai menjalankan polling Telegram...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
