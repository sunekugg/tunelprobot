"""Notifikasi tunnel-bot: topic grup (BUAT AKUN / EXPIRED / USER LOGIN) & user.

Aturan (blueprint §11): tidak ada IP VPS di notifikasi. Pakai domain.
- Buat akun: HANYA ke topic BUAT AKUN. Tidak ke user/ admin pribadi.
- Expired: ke topic EXPIRED + ke masing-masing user pemilik.
- Login: ke topic USER LOGIN.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from .db import DB

log = logging.getLogger(__name__)

TOPIC_NAMES = {"buat_akun": "BUAT AKUN", "expired": "EXPIRED", "login": "USER LOGIN"}

PROTO_LABELS = {
    "ssh": "SSH",
    "vmess": "VMESS",
    "vless": "VLESS",
    "trojan": "TROJAN",
}


def _topic_keyboard(topic: str) -> InlineKeyboardMarkup | None:
    if topic == "buat_akun":
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔍 Detail Akun", callback_data="notif_detail")],
                [InlineKeyboardButton("🗑️ Hapus Akun", callback_data="notif_delete")],
            ]
        )
    return None


def send_to_topic(context: CallbackContext, d: DB, topic: str, text: str,
                  kb: InlineKeyboardMarkup | None = None) -> None:
    """Kirim ke topic grup (di-support message_thread_id)."""
    t = d.get_topic(topic)
    if not t or not t["enabled"] or not t["group_id"]:
        log.info("topic %s belum dikonfigurasi; lewati", topic)
        return
    try:
        context.bot.send_message(
            chat_id=t["group_id"],
            text=text,
            message_thread_id=int(t["topic_id"]) if t["topic_id"] else None,
            reply_markup=kb,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("gagal kirim topic %s: %s", topic, e)


def notify_create_account(context: CallbackContext, d: DB, acc) -> None:
    """Notifikasi pembuatan akun — HANYA topic BUAT AKUN. Tanpa IP."""
    proto = PROTO_LABELS.get(acc["protocol"], acc["protocol"])
    text = (
        f"✅ Akun dibuat\n\n"
        f"Domain : {acc['host']}\n"
        f"ISP    : (ISP dari domain)\n"
        f"Protokol: {proto}\n"
        f"Username: {acc['username']}\n"
        f"Expired: {acc['expired_at']}\n"
        f"Limit IP: {acc['ip_limit']}"
    )
    send_to_topic(context, d, "buat_akun", text, _topic_keyboard("buat_akun"))


def notify_expired_group(context: CallbackContext, d: DB, by_protocol: dict) -> None:
    """Format grup expired (blueprint §11.B). Header per protokol + DOMAIN/ISP + list + Total."""
    for proto, rows in by_protocol.items():
        header = f"{'SSH/Dropbear' if proto == 'ssh' else PROTO_LABELS.get(proto, proto)} Account Expired"
        lines = [header]
        lines.append(f"DOMAIN : {config_domain()}")
        lines.append("ISP    : (ISP dari domain)")
        lines.append("")
        for r in rows:
            lines.append(f"{r['username']} | {r['expired_at']}")
        lines.append("")
        lines.append(f"Total : {len(rows)}")
        send_to_topic(context, d, "expired", "\n".join(lines))


def notify_expired_user(context: CallbackContext, d: DB, acc) -> None:
    """Pesan pribadi ke user pemilik akun. Tanpa IP, emoticon ⏰."""
    if not acc["owner_id"]:
        return
    proto = PROTO_LABELS.get(acc["protocol"], acc["protocol"])
    text = (
        f"⏰ Akun kamu habis masa aktifnya.\n\n"
        f"Username: {acc['username']}\n"
        f"Protokol: {proto}\n"
        f"Expired: {acc['expired_at']}"
    )
    try:
        context.bot.send_message(chat_id=acc["owner_id"], text=text)
    except Exception as e:  # noqa: BLE001
        log.warning("gagal kirim ke owner %s: %s", acc["owner_id"], e)


def notify_login(context: CallbackContext, d: DB, rows_by_proto: dict) -> None:
    """Notifikasi user login — topic USER LOGIN, isi per protokol (jumlah PID + kuota)."""
    lines = ["👥 User Online"]
    for proto, stats in rows_by_proto.items():
        lines.append(f"{PROTO_LABELS.get(proto, proto)}: {stats['pid']} PID | kuota {stats['quota']}")
    send_to_topic(context, d, "login", "\n".join(lines))


def config_domain() -> str:
    # hindari circular import
    from .config import config
    return config.get("DOMAIN", "")
