"""Menu admin tunnel-bot (blueprint §8). Semua aksi dicek admin.

Setiap pesan & callback WAJIB dicek dgn is_admin. User biasa mencoba callback
admin mendapat "❌ Akses ditolak".
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from .db import DB
from .config import config

log = logging.getLogger(__name__)


def is_admin(update, d: DB) -> bool:
    """Cek user_id ada di admin DB ATAU ADMIN_IDS config."""
    uid = update.effective_user.id if update and update.effective_user else None
    if uid is None:
        return False
    if d.is_admin(uid):
        return True
    return uid in config.admin_ids()


def deny(update) -> None:
    try:
        update.callback_query.answer("❌ Akses ditolak")
    except Exception:
        pass


# ---- toggle fitur (settings on/off) ----
TOGGLES = [
    ("mandiri", "mandiri"),
    ("check_bug", "check_bug"),
    ("auto_heal", "auto_heal"),
    ("trial", "trial"),
    ("wajib_join", "wajib_join"),
    ("notify_create", "notify_create"),
    ("notify_expired", "notify_expired"),
    ("notify_login", "notify_login"),
]


def toggle_menu(d: DB) -> InlineKeyboardMarkup:
    rows = []
    for key, label in TOGGLES:
        val = d.get_setting(key, "0")
        status = "ON" if val in ("1", "true") else "OFF"
        icon = "🟢" if status == "ON" else "🔴"
        rows.append([InlineKeyboardButton(
            f"{icon} {label} ({status})",
            callback_data=f"tog:{key}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="adm_toggle")])
    return InlineKeyboardMarkup(rows)


async def handle_toggle(update, context: CallbackContext, d: DB) -> None:
    query = update.callback_query
    await query.answer()
    # callback_data = "adm_toggle" (buka menu) atau "tog:<key>"
    if ":" in query.data:
        key = query.data.split(":", 1)[1]
        cur = d.get_setting(key, "0")
        new = "0" if cur in ("1", "true") else "1"
        d.set_setting(key, new)
        log.info("toggle %s -> %s", key, new)
    await query.edit_message_reply_markup(toggle_menu(d))


# ---- pengaturan (durasi, kuota, batas) ----
SETTINGS_KEYS = [
    ("duration_days", "Durasi akun (hari)"),
    ("quota_days", "Kuota per periode (hari)"),
    ("pid_limit", "Batas PID"),
    ("default_ip_limit", "Limit IP default"),
    ("create_batch_days", "Batas buat akun (periode hari)"),
    ("create_batch_limit", "Batas buat akun (jumlah)"),
    ("trial_days", "Durasi trial (hari)"),
]


async def handle_settings(update, context: CallbackContext, d: DB) -> None:
    query = update.callback_query
    await query.answer()
    lines = ["🎨 Pengaturan\n"]
    rows = []
    for key, label in SETTINGS_KEYS:
        val = d.get_setting(key, "")
        lines.append(f"{label}: {val}")
        rows.append([InlineKeyboardButton(f"✏️ {label}", callback_data=f"set:{key}")])
    rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="adm_settings")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def handle_set_input(update, context: CallbackContext, d: DB) -> None:
    """Input nilai setting berdasarkan state 'set:<key>'."""
    key = context.user_data.get("state", "").replace("set:", "", 1)
    if not key:
        return
    val = update.message.text.strip()
    if not val.isdigit() or int(val) < 0:
        await update.message.reply_text("❌ Harus angka non-negatif.")
        return
    d.set_setting(key, val)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"✅ {key} di-set ke {val}.")


# ---- topic ----
TOPICS = [("buat_akun", "Topic BUAT AKUN"),
          ("expired", "Topic EXPIRED"),
          ("login", "Topic USER LOGIN")]


async def handle_topics(update, context: CallbackContext, d: DB) -> None:
    query = update.callback_query
    await query.answer()
    lines = ["📣 Topic Notifikasi\n"]
    rows = []
    for name, label in TOPICS:
        t = d.get_topic(name)
        status = f"grup={t['group_id'] or '-'} topic={t['topic_id'] or '-'}" if t else "belum diatur"
        lines.append(f"{label}: {status}")
        rows.append([InlineKeyboardButton(f"✏️ {label}", callback_data=f"topic:{name}")])
    rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="adm_topics")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def handle_topic_input(update, context: CallbackContext, d: DB) -> None:
    """Input: '<group_id> <topic_id>'."""
    name = context.user_data.get("state", "").replace("topic:", "", 1)
    if not name:
        return
    parts = update.message.text.strip().split()
    if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
        await update.message.reply_text("❌ Format: <group_id> <topic_id>")
        return
    d.set_topic(name, parts[0], parts[1], enabled=1)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"✅ Topic {name} di-set.")


# ---- wajib join ----
async def handle_join(update, context: CallbackContext, d: DB) -> None:
    query = update.callback_query
    await query.answer()
    cur = d.get_setting("wajib_join_channel", "")
    await query.edit_message_text(
        "📌 Wajib Join\n\n"
        f"Channel saat ini: {cur or 'belum diatur'}\n"
        "Kirim channel (tanpa @) untuk mengubah.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Kembali", callback_data="adm_join")],
        ]),
    )


async def handle_join_input(update, context: CallbackContext, d: DB) -> None:
    ch = update.message.text.strip().lstrip("@")
    if not ch:
        return
    d.set_setting("wajib_join_channel", ch)
    context.user_data.pop("state", None)
    await update.message.reply_text(f"✅ Wajib join di-set ke @{ch}.")


# ---- broadcast ----
async def handle_broadcast(update, context: CallbackContext, d: DB) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = "broadcast"
    await query.edit_message_text(
        "📢 Broadcast\n\nKirim pesan yang mau di-broadcast ke semua user pemilik akun.",
    )


async def handle_broadcast_text(update, context: CallbackContext, d: DB) -> None:
    context.user_data.pop("state", None)
    owners = d._conn.execute(
        "SELECT DISTINCT owner_id FROM accounts WHERE owner_id IS NOT NULL"
    ).fetchall()
    sent = 0
    for r in owners:
        try:
            await update.effective_message.copy_to(r["owner_id"])
            sent += 1
        except Exception as e:  # noqa: BLE001
            log.warning("broadcast gagal ke %s: %s", r["owner_id"], e)
    await update.message.reply_text(f"✅ Broadcast terkirim ke {sent} user.")


# ---- logs / stats ----
async def handle_logs(update, context: CallbackContext, d: DB) -> None:
    query = update.callback_query
    await query.answer()
    rows = d.list_logs(30)
    lines = ["📜 Log (30 terakhir)\n"]
    for r in rows:
        lines.append(f"{r['ts']} [{r['level']}] {r['event']}: {r['detail'][:60]}")
    if not rows:
        lines.append("(kosong)")
    await query.edit_message_text("\n".join(lines)[:4000])


async def handle_stats(update, context: CallbackContext, d: DB) -> None:
    query = update.callback_query
    await query.answer()
    from .accounts import list_expired
    counts = d._conn.execute(
        "SELECT protocol, COUNT(*) c FROM accounts WHERE status='active' GROUP BY protocol"
    ).fetchall()
    exp = len(list_expired(d))
    lines = ["📊 Statistik\n"]
    for r in counts:
        lines.append(f"  {r['protocol']}: {r['c']}")
    lines.append(f"  expired: {exp}")
    await query.edit_message_text("\n".join(lines))
