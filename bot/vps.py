"""Tambah VPS & pasang komponen dari bot (blueprint §7, §8).

Di VPS uji, komponen dipasang dari Telegram dengan status ⏳ ✅ ❌.
"""

from __future__ import annotations

import asyncio
import logging
import re

from telegram.ext import CallbackContext

from .db import DB, fmt_wib, now_wib

log = logging.getLogger(__name__)


def _valid_ip_or_host(s: str) -> bool:
    """Terima domain (hostname) atau IPv4. Hanya dipakai utk koneksi, TIDAK
    untuk ditampilkan (IP VPS dilarang muncul di Telegram)."""
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s):
        return all(0 <= int(p) <= 255 for p in s.split("."))
    return bool(re.match(r"^[a-zA-Z0-9._-]+$", s)) and "." in s


async def cmd_vps_add(update, context: CallbackContext, d: DB) -> None:
    """Tambah VPS: alur satu langkah input (nama/host). Selanjutnya daftar
    komponen dipilih utk dipasang dengan indikator 1/1."""
    msg = (
        "🌐 Tambah VPS Baru\n\n"
        "Ketik alamat VPS (domain atau IP) yang mau ditambahkan.\n"
        "Catatan: IP tidak akan pernah tampil di Telegram."
    )
    context.user_data["state"] = "vps_input"
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(msg)


async def handle_text_vps(update, context: CallbackContext, d: DB) -> None:
    host = update.message.text.strip()
    if not _valid_ip_or_host(host):
        await update.message.reply_text("❌ Alamat tidak valid. Kirim domain atau IPv4.")
        return
    d._conn.execute(
        "INSERT INTO vps (name, domain, created_at) VALUES (?,?,?)",
        (host, host, fmt_wib(now_wib())),
    )
    d._conn.commit()
    await update.message.reply_text("✅ VPS ditambahkan.")
    context.user_data.pop("state", None)


async def cmd_component_install(update, context: CallbackContext, d: DB, name: str) -> None:
    """Pasang komponen dari bot dengan indikator ⏳ → ⌛ → ✅/❌."""
    await update.callback_query.answer()
    status_msg = await update.callback_query.edit_message_text(f"⏳ Memasang {name}...")
    # Placeholder: kerja nyata di scripts/components.sh dipanggil via subprocess
    # (di satu langkah utk menghindari pemblokiran). Di sini simulasi + catat.
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "/opt/tunnel-bot/scripts/components.sh", name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        output = out.decode(errors="replace")
        if proc.returncode == 0:
            await status_msg.edit_text("⌛ Selesai...")
            await status_msg.edit_text(f"✅ {name} terpasang.")
            d._conn.execute(
                "INSERT INTO components (name, installed, checked_at) VALUES (?,1,?) "
                "ON CONFLICT(name) DO UPDATE SET installed=1",
                (name, fmt_wib(now_wib())),
            )
            d._conn.commit()
            log.info("komponen %s terpasang", name)
        else:
            await status_msg.edit_text(f"❌ {name} gagal.\n<pre>{output[:800]}</pre>")
    except Exception as e:  # noqa: BLE001
        await status_msg.edit_text(f"❌ {name} gagal: {e}")
