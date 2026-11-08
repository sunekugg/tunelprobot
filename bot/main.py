"""Entry point tunnel-bot: koneksi Telegram & router handler.

Menyalakan bot via python-telegram-bot (Application). Menangani:
- /start (welcome)
- callback_query routing utk menu user/admin
- flow input (state machine sederhana via context.user_data["state"])
- wajib join, akses admin, scheduler loop, cek timezone/conntrack saat start
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import admin as A
from . import menus as M
from . import notify as N
from .accounts import acak_username, create_account
from .config import config
from .db import DB, fmt_wib, now_wib
from .scheduler import loop as sched_loop

LOG_PATH = Path("/var/log/tunnel-bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("bot")


# ---------- helpers ----------

def _user_is_admin(update: Update, d: DB) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    return bool(uid) and (d.is_admin(uid) or uid in config.admin_ids())


def _wajib_join_ok(update: Update, d: DB) -> bool:
    if not _user_is_admin(update, d):
        return True
    ch = d.get_setting("wajib_join_channel", "")
    if not ch or d.get_setting("wajib_join", "0") not in ("1", "true"):
        return True
    # implementasi check join di handler on/off; di sini default izinkan (Fase 6)
    return True


# ---------- commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d: DB = context.bot_data["db"]
    user = update.effective_user
    if not user:
        return
    if not _wajib_join_ok(update, d):
        await update.message.reply_text(
            "🔒 Untuk memakai bot, wajib join channel dulu.", reply_markup=M.join_keyboard(),
        )
        return
    nama = user.first_name or user.username or "Pengguna"
    duration = d.get_setting("duration_days", "30")
    limit = d.get_setting("default_ip_limit", "2")
    limit_txt = "UNLIMITED" if limit == "0" else f"{limit} IP"
    waktu = fmt_wib(now_wib())
    # batas buat akun: pemakaian/limit + reset
    from .accounts import sisa_kuota_text
    sisa = sisa_kuota_text(d, user.id)
    welcome_text = (
        f"Selamat datang, {nama}!\n"
        f"Waktu server (WIB): {waktu}\n"
        f"Batas buat akun: {sisa}\n"
        f"Masa aktif akun: {duration} hari\n"
        f"Limit IP: {limit_txt}"
    )
    photo = d.get_setting("welcome_photo", "")
    is_admin = _user_is_admin(update, d)
    if photo:
        await update.message.reply_photo(photo, caption=welcome_text)
    else:
        await update.message.reply_text(welcome_text)
    await update.message.reply_text(
        "Pilih menu di bawah:", reply_markup=M.user_menu(is_admin),
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d: DB = context.bot_data["db"]
    if not _user_is_admin(update, d):
        await update.message.reply_text("❌ Akses ditolak")
        return
    await update.message.reply_text("📡 Panel Administrator", reply_markup=M.admin_menu())


# ---------- callback routing ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d: DB = context.bot_data["db"]
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    is_admin = _user_is_admin(update, d)
    if not is_admin and (data.startswith("adm_") or data in ("tog", "set", "topic", "join")):
        await q.answer("❌ Akses ditolak")
        return
    if not is_admin and data in ("adm_toggle", "adm_bot_status", "adm_vps", "adm_users",
                                  "adm_accounts", "adm_del_expired", "adm_online", "adm_unlock",
                                  "adm_reset_limit", "adm_del_all", "adm_settings", "adm_intervals",
                                  "adm_topics", "adm_join", "adm_welcome", "adm_backup", "adm_stats",
                                  "adm_broadcast", "adm_logs", "adm_domain", "adm_banner"):
        await q.answer("❌ Akses ditolak")
        return

    # route admin first
    if is_admin and data == "adm_toggle":
        await q.answer()
        await q.edit_message_text("🎛️ ON/OFF Fitur", reply_markup=A.toggle_menu(d))
        return
    if is_admin and data.startswith("tog:"):
        await A.handle_toggle(update, context, d)
        return
    if is_admin and data == "adm_settings":
        await A.handle_settings(update, context, d)
        return
    if is_admin and data == "adm_topics":
        await A.handle_topics(update, context, d)
        return
    if is_admin and data.startswith("topic:"):
        context.user_data["state"] = data
        await q.answer("Kirim '<group_id> <topic_id>'.")
        return
    if is_admin and data == "adm_join":
        await A.handle_join(update, context, d)
        return
    if is_admin and data == "adm_stats":
        await A.handle_stats(update, context, d)
        return
    if is_admin and data == "adm_logs":
        await A.handle_logs(update, context, d)
        return
    if is_admin and data == "adm_broadcast":
        await A.handle_broadcast(update, context, d)
        return

    # admin panel open
    if data == "admin_panel":
        if not is_admin:
            await q.answer("❌ Akses ditolak")
            return
        await q.answer()
        await q.edit_message_text("📡 Panel Administrator", reply_markup=M.admin_menu())
        return
    if data == "back_user":
        await q.answer()
        await q.edit_message_text("Menu User", reply_markup=M.user_menu(is_admin))
        return

    # create account flows (trial langsung, buat minta username)
    if data.startswith(("trial_", "create_")):
        kind, proto = data.split("_", 1)
        if kind == "trial":
            await _do_trial(update, context, d, proto)
            return
        else:
            context.user_data["state"] = f"create_username:{proto}"
            await q.answer("Ketik username yang kamu mau (awali 'sunek-').")
            return

    # user menu
    if data == "my_accounts":
        owner = update.effective_user.id
        rows = d.list_accounts(owner)
        lines = ["📋 Akun Saya\n"]
        for r in rows:
            ip = "UNLIMITED" if r["ip_limit"] == 0 else f"{r['ip_limit']} IP"
            lines.append(f"{r['protocol'].upper()} | {r['username']} | expired: {r['expired_at']} | Limit IP: {ip}")
        if not rows:
            lines.append("(belum ada)")
        await q.answer()
        await q.edit_message_text("\n".join(lines)[:4000])
        return
    if data == "usage":
        from .accounts import sisa_kuota_text
        sisa = sisa_kuota_text(d, update.effective_user.id)
        await q.answer()
        await q.edit_message_text(
            f"📊 Data Pemakaian\nWaktu Server (WIB): {fmt_wib(now_wib())}\nBatas buat akun: {sisa}"
        )
        return
    if data == "check_bug":
        await q.answer()
        await q.edit_message_text("🔍 Cek Bug / Host ISP — sedang dibangun (Fase 9).")
        return
    if data == "copy_account":
        await q.answer("📋 Disalin", show_alert=False)
        return

    # notifikasi buat akun: detail / hapus (khusus admin, dengan konfirmasi)
    if data in ("notif_detail", "notif_delete"):
        if not is_admin:
            await q.answer("❌ Akses ditolak")
            return
        want = data.split("_", 1)[1]
        # cari akun terakhir sesuai (latest) — detail sederhana
        latest = d._conn.execute(
            "SELECT * FROM accounts WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not latest:
            await q.answer("Tidak ada akun.")
            return
        if want == "detail":
            await q.answer()
            ip = "UNLIMITED" if latest["ip_limit"] == 0 else f"{latest['ip_limit']} IP"
            await q.edit_message_text(
                f"🔍 Detail: {latest['username']}\n"
                f"Protokol : {latest['protocol'].upper()}\n"
                f"Expired  : {latest['expired_at']}\n"
                f"Limit IP : {ip}",
                reply_markup=M.copy_keyboard(),
            )
        else:
            # konfirmasi
            context.user_data["confirm_delete"] = latest["username"]
            await q.answer()
            await q.edit_message_text(
                f"🗑️ Hapus akun *{latest['username']}*?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Ya, hapus", callback_data="notif_delete_yes")],
                    [InlineKeyboardButton("❌ Batal", callback_data="back_user")],
                ]),
            )
        return
    if data == "notif_delete_yes":
        username = context.user_data.pop("confirm_delete", None)
        if username:
            d._conn.execute("UPDATE accounts SET status='deleted' WHERE username=?", (username,))
            d._conn.commit()
            await q.answer()
            await q.edit_message_text(f"✅ Akun {username} dihapus.")
        return

    await q.answer()


async def _do_trial(update: Update, context: ContextTypes.DEFAULT_TYPE, d: DB, proto: str) -> None:
    owner = update.effective_user.id
    if d.get_setting("trial", "1") not in ("1", "true"):
        await update.callback_query.edit_message_text("❌ Fitur trial sedang nonaktif.")
        return
    ok, msg, data = create_account(d, owner, proto, acak_username(), trial=True)
    if not ok:
        await update.callback_query.edit_message_text(f"❌ {msg}")
        return
    text = _render_account(d, data)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text, reply_markup=M.copy_keyboard())
    acc = d.get_account_by_username(data["username"])
    if acc:
        if d.get_setting("notify_create", "1") in ("1", "true"):
            N.notify_create_account(context, d, acc)


def _render_account(d: DB, data: dict) -> str:
    from . import formats as F
    proto = data["protocol"]
    if proto == "ssh":
        return F.format_ssh(data["username"], data["password"], data["host"], data["days"])
    exp = data["expired_text"]
    if proto == "vmess":
        return F.format_vmess(data["username"], data["host"], data["password"],
                              "(ISP)", "(CITY)", exp)
    if proto == "vless":
        return F.format_vless(data["username"], data["host"], data["password"],
                              "(ISP)", "(CITY)", exp)
    if proto == "trojan":
        return F.format_trojan(data["username"], data["host"], data["password"],
                               "(ISP)", "(CITY)", exp)
    return "(protokol tidak dikenal)"


# ---------- text state machine ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d: DB = context.bot_data["db"]
    state = context.user_data.get("state", "")
    if not state:
        await update.message.reply_text("Gunakan /start untuk membuka menu.")
        return
    if state.startswith("create_username:"):
        proto = state.split(":", 1)[1]
        username = update.message.text.strip()
        ok, msg, data = create_account(d, update.effective_user.id, proto, username)
        if not ok:
            await update.message.reply_text(f"❌ {msg}\nCoba lagi.")
            return
        if proto == "ssh":
            context.user_data["pending_create"] = data
            context.user_data["state"] = "create_password:ssh"
            await update.message.reply_text("Ketik password yang kamu mau (min 6, huruf+angka).")
            return
        text = _render_account(d, data)
        context.user_data.pop("state", None)
        await update.message.reply_text(text, reply_markup=M.copy_keyboard())
        acc = d.get_account_by_username(data["username"])
        if acc and d.get_setting("notify_create", "1") in ("1", "true"):
            N.notify_create_account(context, d, acc)
        return
    if state.startswith("create_password:"):
        pending = context.user_data.get("pending_create")
        if not pending:
            context.user_data.pop("state", None)
            return
        username = pending["username"]
        proto = "ssh"
        ok, msg, data = create_account(d, update.effective_user.id, proto, username,
                                       password=update.message.text.strip())
        if not ok:
            await update.message.reply_text(f"❌ {msg}\nCoba lagi.")
            return
        context.user_data.pop("state", None)
        context.user_data.pop("pending_create", None)
        text = _render_account(d, data)
        await update.message.reply_text(text, reply_markup=M.copy_keyboard())
        acc = d.get_account_by_username(data["username"])
        if acc and d.get_setting("notify_create", "1") in ("1", "true"):
            N.notify_create_account(context, d, acc)
        return
    if state.startswith("set:"):
        await A.handle_set_input(update, context, d)
        return
    if state.startswith("topic:"):
        await A.handle_topic_input(update, context, d)
        return
    if state == "broadcast":
        await A.handle_broadcast_text(update, context, d)
        return
    if state == "vps_input":
        from . import vps as V
        await V.handle_text_vps(update, context, d)
        return
    if state == "join_input":
        await A.handle_join_input(update, context, d)
        return

    await update.message.reply_text("Perintah tidak dikenal. Gunakan /start.")


async def _post_init(app: Application) -> None:
    """Cek zona waktu & conntrack saat start. Kirim log ke stderr; tidak
    bocorkan IP VPS."""
    import subprocess
    tz = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"],
                        capture_output=True, text=True).stdout.strip()
    if tz and tz != "Asia/Jakarta":
        log.warning("Zona waktu server %s, bukan Asia/Jakarta — expired bisa meleset!", tz)
    max_conn = 0
    try:
        max_conn = int(Path("/proc/sys/net/netfilter/nf_conntrack_max").read_text())
    except Exception:
        pass
    if 0 < max_conn < 65536:
        log.warning("nf_conntrack_max=%d di bawah 65536", max_conn)
    # jalankan scheduler background
    stop = asyncio.Event()
    app.bot_data["sched_stop"] = stop
    app.create_task(sched_loop(app, app.bot_data["db"], stop))


def main() -> None:
    token = config.get("TOKEN")
    if not token:
        log.error("TOKEN belum diset di config.env")
        sys.exit(1)
    db = DB()
    db.add_log("bot_start", "tunnel-bot start")
    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data["db"] = db
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("tunnel-bot mulai")
    app.run_polling()


if __name__ == "__main__":
    main()
