from __future__ import annotations
import os
import sys
import time
import json
import html
import re
import asyncio
import datetime
import asyncio
import datetime
import html
import os
import subprocess
import uuid
import aiosqlite
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from .config import DB_PATH, DOMAIN, now_wib
from .db import (
    is_user_admin, get_setting, set_setting, get_effective_limit_ip,
    get_effective_daily_limit, get_effective_quota_window, parse_window_str, reset_all_creation_limits, reset_user_creation_limit, add_log,
    get_servers_with_usage, get_server_by_id
)
from .keyboards import admin_menu_kb, cancel_kb, limit_ip_picker_kb
from .force_join import invalidate_join_cache
from .expiry_cleaner import purge_accounts
from .formatter import (
    format_trojan_output, format_ssh_output,
    build_vless_messages, build_vmess_messages, send_multi_part_output
)
from .system_manager import (
    get_isp_info, parse_duration, format_expired_display,
    inspect_remote_node, use_existing_remote_node, install_remote_node_stack,
    create_ssh_on_server, delete_ssh_on_server, lock_ssh_on_server,
    sync_xray_by_server, update_ssh_banner, sync_banner_all_servers,
    format_bytes_human, set_server_speed_limit, sync_ssh_bw_rules_on_server,
    get_ssh_active_sessions, get_xray_active_ips, get_xray_conn_counts, get_server_stats, check_service_status,
    generate_portable_backup_sh, restore_from_sh_content, send_account_topic_notification
)
from .states import (
    AdminCustomAccountFSM, AdminUserEditFSM, AdminByIdFSM, AdminAccountEditFSM,
    AdminForceJoinFSM, AdminSettingFSM, AdminBroadcastFSM, AdminRestoreFSM,
    AdminServerFSM, AdminPurgeOverlimitFSM
)

router = Router()

async def _guard_admin(event) -> bool:
    if not await is_user_admin(event.from_user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Akses khusus Administrator!", show_alert=True)
        return False
    return True


async def _render_admin_home(call_or_msg):
    bot_on = (await get_setting("bot_enabled")) == "1"
    status_str = "🟢 AKTIF (ON)" if bot_on else "🔴 NONAKTIF (OFF)"
    text = (
        "👑 <b>Panel Administrator VPN</b>\n"
        f"• <b>Status Bot untuk User:</b> <code>{status_str}</code>\n"
        f"• <b>Waktu Server (WIB):</b> <code>{now_wib().strftime('%Y-%m-%d %H:%M WIB')}</code>\n\n"
        "Pilih menu pengelolaan di bawah ini:"
    )
    kb = admin_menu_kb(bot_on)
    if isinstance(call_or_msg, CallbackQuery):
        await _safe_edit(call_or_msg, text, reply_markup=kb)
    else:
        await call_or_msg.answer(text, reply_markup=kb)

@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    await _render_admin_home(call)
    await call.answer()

@router.callback_query(F.data == "adm_toggle_bot")
async def cb_adm_toggle_bot(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    new_val = "0" if (await get_setting("bot_enabled")) == "1" else "1"
    await set_setting("bot_enabled", new_val)
    await generate_portable_backup_sh()
    await call.answer("🟢 Bot DIAKTIFKAN!" if new_val == "1" else "🔴 Bot DINONAKTIFKAN!", show_alert=True)
    await _render_admin_home(call)

# === MENU PEMBERSIHAN AKUN EXPIRED & MELEBIHI BATAS HARI ===
@router.callback_query(F.data == "adm_purge_menu")
async def cb_adm_purge_menu(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    tr_dur = await get_setting("trial_duration") or "30m"
    def_dur = await get_setting("default_duration") or "7d"
    max_dur = await get_setting("max_duration") or "30d"

    text = (
        "🧹 <b>Pembersihan Akun Expired &amp; Melebihi Batas Hari</b>\n\n"
        f"• <b>Batas Durasi Trial Saat Ini:</b> <code>{tr_dur}</code>\n"
        f"• <b>Batas Hari Buat Akun Default:</b> <code>{def_dur}</code>\n"
        f"• <b>Batas Hari Maksimal:</b> <code>{max_dur}</code>\n"
        f"• <b>Waktu Server Sekarang:</b> <code>{now_wib().strftime('%Y-%m-%d %H:%M:%S WIB')}</code>\n\n"
        "Pilih metode pembersihan akun di bawah ini:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Hapus Semua Akun Expired (Trial & Reguler)", callback_data="adm_purge_exec:expired")],
        [InlineKeyboardButton(text=f"⏱ Hapus Trial Melebihi Batas ({tr_dur})", callback_data="adm_purge_exec:trial_over")],
        [InlineKeyboardButton(text=f"📅 Hapus Akun Reguler Melebihi Batas Hari ({def_dur})", callback_data="adm_purge_exec:reg_over_default")],
        [InlineKeyboardButton(text="🔥 Sapu Bersih Semua yg Expired & Lewat Batas", callback_data="adm_purge_exec:all_over_limit")],
        [InlineKeyboardButton(text="✏️ Hapus Akun Melebihi Durasi/Hari Tertentu (Manual)", callback_data="adm_purge_custom")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_purge_exec:"))
async def cb_adm_purge_exec(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    mode = call.data.split(":", 1)[1]
    await call.answer("⏳ Memeriksa & menghapus akun yang melewati batas...")
    cnt, names = await purge_accounts(bot=call.bot, mode=mode)
    if cnt == 0:
        await call.answer("✅ Tidak ditemukan akun yang melewati batas waktu/hari tersebut.", show_alert=True)
        await cb_adm_purge_menu(call)
        return
    preview = ", ".join(names[:15]) + (f", dan {len(names) - 15} lainnya" if len(names) > 15 else "")
    await _safe_edit(
        call,
        f"✅ <b>Pembersihan Selesai!</b>\n\n• <b>Total Dihapus:</b> <code>{cnt} akun</code>\n• <b>Daftar Akun:</b> <code>{html.escape(preview)}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Kembali ke Menu Pembersihan", callback_data="adm_purge_menu")],
            [InlineKeyboardButton(text="👑 Panel Administrator", callback_data="admin_panel")]
        ])
    )

@router.callback_query(F.data == "adm_purge_custom")
async def cb_adm_purge_custom(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.set_state(AdminPurgeOverlimitFSM.custom_duration)
    await _safe_edit(
        call,
        "✏️ <b>Hapus Akun Melebihi Batas Hari / Durasi Tertentu</b>\n\n"
        "Masukkan batas durasi maksimal dihitung dari tanggal pembuatan (<code>created_at</code>).\n"
        "Contoh input: <code>30m</code>, <code>1d</code>, <code>3d</code>, <code>7d</code>:",
        reply_markup=cancel_kb("admin_panel")
    )
    await call.answer()

@router.message(AdminPurgeOverlimitFSM.custom_duration)
async def fsm_adm_purge_custom(message: Message, state: FSMContext):
    if not await _guard_admin(message):
        return
    dur_inp = (message.text or "").strip().lower()
    if not parse_duration(dur_inp):
        await message.answer("❌ Format durasi tidak valid! Contoh: <code>30m</code>, <code>12h</code>, <code>1d</code>, <code>7d</code>:", reply_markup=cancel_kb("admin_panel"))
        return
    await state.clear()
    cnt, names = await purge_accounts(bot=message.bot, mode="custom", custom_limit_str=dur_inp)
    if cnt == 0:
        await message.answer(f"✅ Tidak ada akun yang melebihi batas durasi <code>{html.escape(dur_inp)}</code>.")
    else:
        await message.answer(f"🗑 <b>Berhasil menghapus {cnt} akun</b> melebihi batas <code>{html.escape(dur_inp)}</code>:\n<code>{html.escape(', '.join(names[:20]))}</code>")
    await _render_admin_home(message)

# === KELOLA MULTI-SERVER VPS + SPEED LIMIT + KUOTA BW + CEK KOMPONEN ===
def _format_inspect_report(srv: dict, ok: bool, info: dict) -> tuple[str, InlineKeyboardMarkup]:
    sid = srv["id"]
    if not ok:
        err_msg = html.escape(info.get("error", "Gagal login SSH"))
        text = (
            f"❌ <b>Gagal Memeriksa Server #{sid} ({html.escape(srv['name'])})</b>\n\n"
            f"• <b>Domain:</b> <code>{html.escape(srv['domain'])}</code>\n"
            f"• <b>IP / Port SSH:</b> <code>{html.escape(srv['ip'])}:{srv['ssh_port']}</code>\n"
            f"• <b>Pesan Error SSH:</b> <code>{err_msg}</code>\n\n"
            f"Silakan perbaiki Password Root atau Port SSH melalui tombol <b>✏️ Edit Login SSH #{sid}</b> di bawah:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✏️ Edit Login SSH #{sid}", callback_data=f"adm_srv_edit:{sid}")],
            [InlineKeyboardButton(text=f"🔄 Coba Cek Ulang #{sid}", callback_data=f"adm_srv_check:{sid}")],
            [InlineKeyboardButton(text="⬅️ Kembali ke Daftar Server", callback_data="adm_servers")]
        ])
        return text, kb

    def _fmt_comp(val: str) -> str:
        if ":" in val:
            installed, active = val.split(":", 1)
            if installed == "yes" and active == "active":
                return "🟢 Sudah Ada & Aktif"
            if installed == "yes":
                return "🟡 Sudah Terinstal (Belum Aktif)"
            return "🔴 Belum Terinstal"
        return "🟢 Sudah Ada / Aktif" if val in ("yes", "active") else "🔴 Belum Ada"

    text = (
        f"🔍 <b>Hasil Cek Komponen Server #{sid} — {html.escape(srv['name'])}</b>\n"
        f"• <b>Domain / IP:</b> <code>{html.escape(srv['domain'])}</code> (<code>{html.escape(srv['ip'])}:{srv['ssh_port']}</code>)\n\n"
        f"📦 <b>Status Layanan di VPS Tambahan Ini:</b>\n"
        f"• <b>Xray-Core:</b> {_fmt_comp(info.get('XRAY', 'no:inactive'))}\n"
        f"• <b>Dropbear SSH:</b> {_fmt_comp(info.get('DROPBEAR', 'no:inactive'))}\n"
        f"• <b>HAProxy Multiplexer:</b> {_fmt_comp(info.get('HAPROXY', 'no:inactive'))}\n"
        f"• <b>Nginx Web/gRPC:</b> {_fmt_comp(info.get('NGINX', 'inactive'))}\n"
        f"• <b>Sertifikat SSL:</b> {_fmt_comp(info.get('SSL', 'no'))}\n\n"
        f"👇 <b>Silakan Pilih Tindakan untuk Server #{sid}:</b>\n"
        f"1️⃣ Jika komponen sudah terinstal, pilih <b>Gunakan yg Sudah Ada (Tanpa Install)</b>.\n"
        f"2️⃣ Jika komponen belum ada / ingin diinstal dari awal, pilih <b>Install Komponen Sekarang</b>."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Gunakan yg Sudah Ada (Tanpa Install)", callback_data=f"adm_srv_use_existing:{sid}")],
        [InlineKeyboardButton(text="🛠 Install Xray, Dropbear, HAProxy & SSL", callback_data=f"adm_srv_install_stack:{sid}")],
        [
            InlineKeyboardButton(text=f"🔄 Cek Ulang #{sid}", callback_data=f"adm_srv_check:{sid}"),
            InlineKeyboardButton(text=f"✏️ Edit Login #{sid}", callback_data=f"adm_srv_edit:{sid}")
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Daftar Server", callback_data="adm_servers")]
    ])
    return text, kb

@router.callback_query(F.data == "adm_servers")
async def cb_adm_servers(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    servers = await get_servers_with_usage(only_active=False)
    lines = [
        "🌐 <b>Kelola Server VPS, Speed Limit &amp; Cek Komponen</b>",
        "<i>Anda dapat membatasi kecepatan (Mbps), kuota trafik (GB), dan batas user untuk masing-masing VPS.</i>\n"
    ]
    btns = [[InlineKeyboardButton(text="➕ Tambah Server VPS Baru", callback_data="adm_srv_add")]]
    for s in servers:
        cap = f"{s['used_users']}/♾" if s["max_users"] == 0 else f"{s['used_users']}/{s['max_users']} User"
        q_gb = float(s.get("quota_gb", 0) or 0)
        u_b = int(s.get("used_bytes", 0) or 0)
        bw_str = f"{format_bytes_human(u_b)} / {'♾' if q_gb <= 0 else str(q_gb)+' GB'}"
        spd_val = int(s.get("speed_mbps", 0) or 0)
        spd_str = "♾ Unlimited" if spd_val <= 0 else f"{spd_val} Mbps"
        loc = "VPS Utama" if s["is_local"] else f"{s['ip']}:{s['ssh_port']}"
        act_ico = "🟢" if s["is_active"] else "🔴[OFF]"
        lines.append(
            f"{act_ico} <b>#{s['id']} {html.escape(s['name'])}</b> (<code>{html.escape(s['domain'])}</code> | {loc})\n"
            f"   ├ User: <b>{cap}</b> | Speed VPS: <code>{spd_str}</code>\n"
            f"   └ Trafik BW: <code>{bw_str}</code>"
        )
        btns.append([
            InlineKeyboardButton(text=f"⚡ Speed VPS #{s['id']}", callback_data=f"adm_srv_speed:{s['id']}"),
            InlineKeyboardButton(text=f"📊 Kuota BW #{s['id']}", callback_data=f"adm_srv_bw:{s['id']}"),
            InlineKeyboardButton(text=f"🔢 Max User #{s['id']}", callback_data=f"adm_srv_quota:{s['id']}")
        ])
        btns.append([
            InlineKeyboardButton(text=f"🔄 Reset BW #{s['id']}", callback_data=f"adm_srv_resetbw:{s['id']}"),
            InlineKeyboardButton(text=f"🔄 On/Off #{s['id']}", callback_data=f"adm_srv_tog:{s['id']}"),
        ])
        btns.append([
            InlineKeyboardButton(text=f"🌐 Ganti Domain #{s['id']}", callback_data=f"adm_srv_domain:{s['id']}"),
        ])
        if not s["is_local"]:
            btns.append([
                InlineKeyboardButton(text=f"🔍 Cek Komponen & Pilihan Install #{s['id']}", callback_data=f"adm_srv_check:{s['id']}")
            ])
            btns.append([
                InlineKeyboardButton(text=f"✏️ Edit Login SSH #{s['id']}", callback_data=f"adm_srv_edit:{s['id']}"),
                InlineKeyboardButton(text=f"🗑 Hapus #{s['id']}", callback_data=f"adm_srv_del:{s['id']}")
            ])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")])
    await _safe_edit(call, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data == "adm_srv_add")
async def cb_adm_srv_add(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.set_state(AdminServerFSM.name)
    await _safe_edit(call, "🌐 <b>Tambah Server VPS Baru</b>\n\nMasukkan <b>Nama Server</b> (contoh: <code>SG-Atlantica</code>):", reply_markup=cancel_kb("admin_panel"))
    await call.answer()

@router.message(AdminServerFSM.name)
async def fsm_srv_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AdminServerFSM.domain)
    await message.answer("Masukkan <b>Domain / Subdomain</b> VPS baru tersebut:", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminServerFSM.domain)
async def fsm_srv_domain(message: Message, state: FSMContext):
    await state.update_data(domain=(message.text or "").strip())
    await state.set_state(AdminServerFSM.ip)
    await message.answer("Masukkan <b>IPv4 Publik</b> VPS baru tersebut:", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminServerFSM.ip)
async def fsm_srv_ip(message: Message, state: FSMContext):
    await state.update_data(ip=(message.text or "").strip())
    await state.set_state(AdminServerFSM.ssh_port)
    await message.answer("Masukkan <b>Port SSH Root</b> VPS baru tersebut (contoh: <code>22</code> atau <code>444</code>):", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminServerFSM.ssh_port)
async def fsm_srv_port(message: Message, state: FSMContext):
    p = (message.text or "22").strip()
    await state.update_data(ssh_port=int(p) if p.isdigit() else 22)
    await state.set_state(AdminServerFSM.ssh_pass)
    await message.answer("Masukkan <b>Password Root SSH</b> VPS baru tersebut:", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminServerFSM.ssh_pass)
async def fsm_srv_pass(message: Message, state: FSMContext):
    await state.update_data(ssh_pass=(message.text or "").strip())
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(AdminServerFSM.max_users)
    await message.answer("Masukkan <b>Batas Maksimal User/Akun</b> di server ini (contoh: <code>50</code>, atau <code>0</code> untuk Unlimited):", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminServerFSM.max_users)
async def fsm_srv_max(message: Message, state: FSMContext):
    m_txt = (message.text or "50").strip()
    max_u = int(m_txt) if m_txt.isdigit() else 50
    data = await state.get_data()
    await state.clear()
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        cur = await db.execute(
            "INSERT INTO servers (name, domain, ip, ssh_port, ssh_user, ssh_pass, max_users, is_local, is_active) VALUES (?, ?, ?, ?, 'root', ?, ?, 0, 1)",
            (data["name"], data["domain"], data["ip"], data["ssh_port"], data["ssh_pass"], max_u)
        )
        new_sid = cur.lastrowid
        await db.commit()

    await generate_portable_backup_sh()
    wait_msg = await message.answer(f"🔍 <b>Memeriksa komponen Xray, Dropbear, HAProxy &amp; SSL di Server #{new_sid} ({html.escape(data['name'])})...</b>")
    srv = await get_server_by_id(new_sid)
    ok, info = await inspect_remote_node(new_sid)
    rep_text, rep_kb = _format_inspect_report(srv, ok, info)
    await wait_msg.edit_text(rep_text, reply_markup=rep_kb)

@router.callback_query(F.data.startswith("adm_srv_check:"))
async def cb_adm_srv_check(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    srv = await get_server_by_id(sid)
    if not srv:
        await call.answer("Server tidak ditemukan.", show_alert=True)
        return
    await call.answer("🔍 Memeriksa status komponen di VPS...")
    ok, info = await inspect_remote_node(sid)
    rep_text, rep_kb = _format_inspect_report(srv, ok, info)
    await _safe_edit(call, rep_text, reply_markup=rep_kb)

@router.callback_query(F.data.startswith("adm_srv_use_existing:"))
async def cb_adm_srv_use_existing(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    srv = await get_server_by_id(sid)
    if not srv:
        return
    await call.answer("⏳ Menyinkronkan tanpa menginstal ulang...")
    ok, msg = await use_existing_remote_node(sid)
    if ok:
        await _safe_edit(
            call,
            f"✅ <b>Server #{sid} ({html.escape(srv['name'])}) Siap Digunakan (Mode Tanpa Install)!</b>\n\n"
            f"• Layanan Xray, Dropbear, HAProxy &amp; SSL yang sudah ada tetap dipertahankan.\n"
            f"• Daftar akun &amp; Banner SSH sudah disinkronkan secara otomatis (Hot-Reload).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Kembali ke Daftar Server", callback_data="adm_servers")]])
        )
    else:
        await _safe_edit(
            call,
            f"❌ <b>Gagal Menyinkronkan Server #{sid}:</b>\n<code>{html.escape(msg)}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Kembali", callback_data=f"adm_srv_check:{sid}")]])
        )

@router.callback_query(F.data.startswith("adm_srv_install_stack:"))
async def cb_adm_srv_install_stack(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    srv = await get_server_by_id(sid)
    if not srv:
        return
    await call.answer("🛠 Memulai instalasi komponen...")
    await _safe_edit(
        call,
        f"⏳ <b>Menginstal Xray, Dropbear 2019, HAProxy, Nginx &amp; SSL pada Server #{sid} ({html.escape(srv['name'])})...</b>\n"
        f"Mohon tunggu ±1-3 menit hingga instalasi selesai."
    )
    ok, msg = await install_remote_node_stack(sid)
    if ok:
        await _safe_edit(
            call,
            f"✅ <b>Instalasi Komponen pada Server #{sid} ({html.escape(srv['name'])}) Selesai!</b>\n"
            f"Seluruh layanan Xray, Dropbear, HAProxy, Banner &amp; SSL sudah aktif dan tersinkronisasi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔍 Lihat Hasil Cek Komponen", callback_data=f"adm_srv_check:{sid}")]])
        )
    else:
        await _safe_edit(
            call,
            f"❌ <b>Instalasi Komponen Gagal pada Server #{sid}:</b>\n<code>{html.escape(msg[-350:])}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Kembali", callback_data=f"adm_srv_check:{sid}")]])
        )

@router.callback_query(F.data.startswith("adm_srv_edit:"))
async def cb_adm_srv_edit(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    srv = await get_server_by_id(sid)
    if not srv:
        await call.answer("Server tidak ditemukan.", show_alert=True)
        return
    await state.set_state(AdminServerFSM.edit_credentials)
    await state.update_data(edit_server_id=sid)
    await _safe_edit(
        call,
        f"✏️ <b>Edit Kredensial Login SSH Server #{sid} ({html.escape(srv['name'])})</b>\n\n"
        f"Kirim data baru dengan format 4 kolom dipisah tanda <code>|</code>:\n"
        f"<code>DOMAIN|IP|PORT_SSH|PASSWORD_ROOT</code>\n\n"
        f"Contoh:\n<code>{html.escape(srv['domain'])}|{html.escape(srv['ip'])}|22|PasswordRootAnda</code>",
        reply_markup=cancel_kb("admin_panel")
    )
    await call.answer()

@router.message(AdminServerFSM.edit_credentials)
async def fsm_srv_edit_credentials(message: Message, state: FSMContext):
    if not await _guard_admin(message):
        return
    raw = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 4 or not parts[2].isdigit():
        await message.answer("❌ <b>Format salah!</b> Gunakan: <code>DOMAIN|IP|PORT_SSH|PASSWORD_ROOT</code>", reply_markup=cancel_kb("admin_panel"))
        return
    data = await state.get_data()
    await state.clear()
    sid = int(data["edit_server_id"])
    dom, ip_addr, port_str, root_pw = parts
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE servers SET domain = ?, ip = ?, ssh_port = ?, ssh_pass = ? WHERE id = ?", (dom, ip_addr, int(port_str), root_pw, sid))
        await db.commit()
    await generate_portable_backup_sh()
    srv = await get_server_by_id(sid)
    ok, info = await inspect_remote_node(sid)
    rep_text, rep_kb = _format_inspect_report(srv, ok, info)
    await message.answer(rep_text, reply_markup=rep_kb)

@router.callback_query(F.data.startswith("adm_srv_quota:"))
async def cb_adm_srv_quota(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    await state.set_state(AdminServerFSM.edit_max_users)
    await state.update_data(edit_server_id=sid)
    await _safe_edit(call, f"🔢 Masukkan <b>Batas Maksimal User</b> baru untuk Server #{sid} (angka misal <code>50</code>, atau <code>0</code> untuk Unlimited):", reply_markup=cancel_kb("admin_panel"))
    await call.answer()

@router.message(AdminServerFSM.edit_max_users)
async def fsm_srv_edit_max(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    sid = data["edit_server_id"]
    m_txt = (message.text or "50").strip()
    max_u = int(m_txt) if m_txt.isdigit() else 50
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE servers SET max_users = ? WHERE id = ?", (max_u, sid))
        await db.commit()
    await generate_portable_backup_sh()
    await message.answer(f"✅ Kuota maksimal Server #{sid} diperbarui menjadi <b>{max_u}</b> user!")
    await _render_admin_home(message)

@router.callback_query(F.data.startswith("adm_srv_speed:"))
async def cb_adm_srv_speed(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    await state.set_state(AdminServerFSM.edit_speed_mbps)
    await state.update_data(edit_server_id=sid)
    await _safe_edit(
        call,
        f"⚡ <b>Atur Batas Kecepatan (Speed Limit) Server VPS #{sid}</b>\n\n"
        "Masukkan kecepatan maksimal untuk seluruh trafik di VPS ini dalam satuan <b>Mbps</b> "
        "(contoh: <code>100</code>, <code>250</code>, <code>500</code>, atau ketik <code>0</code> untuk <b>Unlimited / Full Speed</b>):",
        reply_markup=cancel_kb("admin_panel")
    )
    await call.answer()

@router.message(AdminServerFSM.edit_speed_mbps)
async def fsm_srv_edit_speed(message: Message, state: FSMContext):
    if not await _guard_admin(message):
        return
    data = await state.get_data()
    await state.clear()
    sid = int(data["edit_server_id"])
    txt = (message.text or "0").strip()
    mbps = int(txt) if txt.isdigit() else 0
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE servers SET speed_mbps = ? WHERE id = ?", (mbps, sid))
        await db.commit()
    ok, detail = await set_server_speed_limit(sid, mbps)
    await generate_portable_backup_sh()
    lbl = "Unlimited (Full Speed)" if mbps <= 0 else f"{mbps} Mbps"
    status_str = f"✅ Diterapkan di Kernel ({html.escape(detail)})" if ok else f"⚠️ Gagal menerapkan via SSH ({html.escape(detail)})"
    await message.answer(f"⚡ <b>Speed Limit Server #{sid} diatur ke: {lbl}</b>\nStatus: {status_str}")
    await _render_admin_home(message)

@router.callback_query(F.data.startswith("adm_srv_bw:"))
async def cb_adm_srv_bw(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    await state.set_state(AdminServerFSM.edit_quota_gb)
    await state.update_data(edit_server_id=sid)
    await _safe_edit(
        call,
        f"📊 <b>Atur Batas Kuota Bandwidth Server VPS #{sid} (GB)</b>\n\n"
        "Masukkan total batas kuota bandwidth untuk Server VPS ini dalam satuan <b>GB</b> "
        "(contoh: <code>1000</code> untuk 1 TB, atau ketik <code>0</code> untuk <b>Unlimited</b>):",
        reply_markup=cancel_kb("admin_panel")
    )
    await call.answer()

@router.message(AdminServerFSM.edit_quota_gb)
async def fsm_srv_edit_quota_gb(message: Message, state: FSMContext):
    if not await _guard_admin(message):
        return
    data = await state.get_data()
    await state.clear()
    sid = int(data["edit_server_id"])
    txt = (message.text or "0").strip()
    try:
        q_gb = max(0.0, float(txt))
    except ValueError:
        q_gb = 0.0
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE servers SET quota_gb = ? WHERE id = ?", (q_gb, sid))
        await db.commit()
    await generate_portable_backup_sh()
    await message.answer(f"✅ <b>Kuota Bandwidth Server #{sid} diperbarui menjadi: {'Unlimited' if q_gb <= 0 else str(q_gb)+' GB'}</b>")
    await _render_admin_home(message)

@router.callback_query(F.data.startswith("adm_srv_resetbw:"))
async def cb_adm_srv_resetbw(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE servers SET used_bytes = 0 WHERE id = ?", (sid,))
        await db.commit()
    await call.answer(f"✅ Total pemakaian Bandwidth Server #{sid} berhasil di-reset ke 0 B!", show_alert=True)
    await cb_adm_servers(call)

@router.callback_query(F.data.startswith("adm_srv_tog:"))
async def cb_adm_srv_tog(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE servers SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?", (sid,))
        await db.commit()
    await cb_adm_servers(call)

@router.callback_query(F.data.startswith("adm_srv_domain:"))
async def cb_adm_srv_domain(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    srv = await get_server_by_id(sid)
    if not srv:
        await call.answer("Server tidak ditemukan.", show_alert=True)
        return
    await state.set_state(AdminServerFSM.edit_domain)
    await state.update_data(edit_server_id=sid)
    await _safe_edit(
        call,
        f"🌐 <b>Ganti Domain Server #{sid} ({html.escape(srv['name'])})</b>\n\n"
        f"Domain saat ini: <code>{html.escape(srv['domain'])}</code>\n\n"
        f"Ketik domain baru, contoh:\n<code>clode2.sunekvip.my.id</code>",
        reply_markup=cancel_kb("admin_panel")
    )
    await call.answer()

@router.message(AdminServerFSM.edit_domain)
async def fsm_srv_edit_domain(message: Message, state: FSMContext):
    if not await _guard_admin(message):
        return
    new_dom = (message.text or "").strip().lower()
    try:
        await message.delete()
    except Exception:
        pass
    if not new_dom or any(ch.isspace() for ch in new_dom):
        await message.answer("❌ <b>Domain tidak valid!</b> Jangan gunakan spasi. Coba lagi.", reply_markup=cancel_kb("admin_panel"))
        return
    data = await state.get_data()
    await state.clear()
    sid = int(data["edit_server_id"])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE servers SET domain = ? WHERE id = ?", (new_dom, sid))
        await db.commit()
    await generate_portable_backup_sh()
    await message.answer(
        f"✅ <b>Domain server #{sid} diganti menjadi:</b> <code>{html.escape(new_dom)}</code>"
    )

@router.callback_query(F.data.startswith("adm_srv_del:"))
async def cb_adm_srv_del(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    sid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("DELETE FROM servers WHERE id = ? AND is_local = 0", (sid,))
        await db.commit()
    await generate_portable_backup_sh()
    await call.answer("🗑 Server berhasil dihapus!", show_alert=True)
    await cb_adm_servers(call)

# === FITUR ADMIN LAINNYA ===
@router.callback_query(F.data == "adm_reset_all_limits")
async def cb_adm_reset_all_limits(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    await reset_all_creation_limits()
    await generate_portable_backup_sh()
    await add_log(call.from_user.id, "ADMIN_RESET_ALL_LIMITS", "Reset kuota harian semua user")
    await call.answer("✅ Batasan pembuatan akun harian untuk SEMUA user berhasil di-reset!", show_alert=True)

@router.callback_query(F.data == "adm_unlock_all")
async def cb_adm_unlock_all(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT id, acc_type, username, password_or_uuid, COALESCE(server_id, 1) FROM accounts WHERE is_locked = 1") as cur:
            locked_rows = await cur.fetchall()
        await db.execute("UPDATE accounts SET is_locked = 0, locked_until = '', violations = 0 WHERE is_locked = 1")
        await db.commit()

    for _, acc_type, uname, secret, sid in locked_rows:
        if acc_type == "ssh":
            await lock_ssh_on_server(sid, uname, lock=False, password=secret)
    await sync_xray_by_server()
    await generate_portable_backup_sh()
    await call.answer(f"🔓 {len(locked_rows)} akun yang terkunci berhasil diaktifkan kembali!", show_alert=True)
    await _render_admin_home(call)

@router.callback_query(F.data == "adm_del_all_ask")
async def cb_adm_del_all_ask(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ya, Hapus SEMUA Akun", callback_data="adm_del_all_confirm"), InlineKeyboardButton(text="❌ Batal", callback_data="admin_panel")]
    ])
    await _safe_edit(call, "⚠️ <b>PERINGATAN!</b> Yakin ingin <b>MENGHAPUS SEMUA AKUN USER</b>?", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "adm_del_all_confirm")
async def cb_adm_del_all_confirm(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT acc_type, username, COALESCE(server_id, 1) FROM accounts") as cur:
            rows = await cur.fetchall()
        await db.execute("DELETE FROM accounts")
        await db.commit()

    for acc_type, uname, sid in rows:
        if acc_type == "ssh":
            await delete_ssh_on_server(sid, uname)
    await sync_xray_by_server()
    await generate_portable_backup_sh()
    await call.answer(f"🗑 Seluruh {len(rows)} akun berhasil dihapus bersih!", show_alert=True)
    await _render_admin_home(call)

@router.callback_query(F.data == "adm_online")
async def cb_adm_online(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    ssh_map = get_ssh_active_sessions()
    xray_conn = get_xray_conn_counts(120)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT id, tg_id, acc_type, username, limit_ip, is_locked FROM accounts ORDER BY acc_type, username") as cur:
            rows = await cur.fetchall()

    lines = ["👥 <b>Daftar User Online & Status Limit IP</b>\n"]
    btns = []
    online_count = 0
    for acc_id, tg_id, acc_type, uname, acc_lim, is_locked in rows:
        eff_lim = await get_effective_limit_ip(tg_id, acc_lim)
        lim_str = "♾ Unlimited" if eff_lim == 0 else f"{eff_lim} IP"
        if acc_type == "ssh":
            sess = ssh_map.get(uname, [])
            n_pid = len(sess)
            n_ip = len({ip for _, ip in sess})
            detail = f"Login: <b>{n_pid} PID | {n_ip} IP</b>" if n_pid else "Login: <b>0</b>"
            online = n_pid > 0
        else:
            # Xray WS jalur ini tak membaca X-Real-IP (build v26.10.31) → tak ada
            # Real IP & tak ada PID nyata. Tampilkan status online, tanpa angka.
            online = xray_conn.get(uname, 0) > 0
            detail = "Login: <b>Online</b> (IP tidak tersedia)" if online else "Login: <b>0</b>"
        if online or is_locked:
            online_count += 1
            lock_badge = " 🔒[TERKUNCI]" if is_locked else " 🟢[ONLINE]"
            tail = f" / {lim_str}" if acc_type == "ssh" else ""
            lines.append(f"• <b>{acc_type.upper()}</b> <code>{uname}</code> (ID: <code>{tg_id}</code>)\n  └ {lock_badge} | {detail}{tail}")
            btns.append([InlineKeyboardButton(text=f"⚙️ Kelola: {uname}", callback_data=f"adm_acc_detail:{acc_id}")])

    if online_count == 0:
        lines.append("<i>Tidak ada akun yang sedang online atau terkunci saat ini.</i>")
    btns.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="adm_online"), InlineKeyboardButton(text="🔓 Unlock Semua", callback_data="adm_unlock_all")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")])
    await _safe_edit(call, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data == "adm_accounts")
async def cb_adm_accounts(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT id, acc_type, username, expired_at, limit_ip, is_locked, tg_id, COALESCE(server_id, 1) FROM accounts ORDER BY id DESC LIMIT 30") as cur:
            rows = await cur.fetchall()

    btns = [[InlineKeyboardButton(text="➕ Buat Akun Custom", callback_data="adm_acc_create"), InlineKeyboardButton(text="🧨 Hapus Semua Akun", callback_data="adm_del_all_ask")]]
    for acc_id, acc_type, uname, exp_at, acc_lim, is_locked, owner_id, sid in rows:
        eff_lim = await get_effective_limit_ip(owner_id, acc_lim)
        lim_s = "♾" if eff_lim == 0 else str(eff_lim)
        ico = "🔒" if is_locked else ("🔑" if acc_type == "ssh" else "🔐")
        btns.append([InlineKeyboardButton(text=f"{ico} {uname} ({acc_type.upper()}•Srv#{sid}) | IP:{lim_s} | {exp_at}", callback_data=f"adm_acc_detail:{acc_id}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")])
    await _safe_edit(call, "🔐 <b>Kelola Akun User (30 Terbaru)</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data.startswith("adm_acc_detail:"))
async def cb_adm_acc_detail(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    acc_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, expired_at, limit_ip, is_locked, violations, created_at, COALESCE(server_id, 1), COALESCE(quota_gb, 0), COALESCE(used_bytes, 0), COALESCE(speed_mbps, 0) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return
    owner_id, acc_type, uname, exp_at, acc_lim, is_locked, viol, created_at, sid, q_gb, u_bytes, spd_mbps = row
    srv = await get_server_by_id(sid)
    s_name = srv["name"] if srv else f"Server #{sid}"
    eff_lim = await get_effective_limit_ip(owner_id, acc_lim)
    lim_s = "Unlimited (♾)" if eff_lim == 0 else f"{eff_lim} IP"
    q_s = f"{format_bytes_human(u_bytes)} / {'♾ Unlimited' if float(q_gb or 0) <= 0 else str(q_gb)+' GB'}"
    spd_s = "♾ Unlimited" if int(spd_mbps or 0) <= 0 else f"{spd_mbps} Mbps"
    lock_txt = "🔓 Unlock Akun" if is_locked else "🔒 Lock Akun"

    text = (
        f"🔐 <b>Detail Akun #{acc_id}</b>\n\n"
        f"• <b>Server:</b> <code>{html.escape(s_name)}</code>\n"
        f"• <b>Tipe / User:</b> <code>{acc_type.upper()} / {uname}</code>\n"
        f"• <b>Pemilik (TG ID):</b> <code>{owner_id}</code>\n"
        f"• <b>Tanggal Buat:</b> <code>{created_at or '-'} WIB</code>\n"
        f"• <b>Tanggal Expired:</b> <code>{exp_at} WIB</code>\n"
        f"• <b>Limit IP:</b> <code>{lim_s}</code>\n"
        f"• <b>Kuota Bandwidth:</b> <code>{q_s}</code>\n"
        f"• <b>Batas Kecepatan:</b> <code>{spd_s}</code>\n"
        f"• <b>Status:</b> <code>{'🔒 TERKUNCI' if is_locked else '🟢 AKTIF'}</code> ({viol}x)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Lihat Config", callback_data=f"view_acc:{acc_id}"), InlineKeyboardButton(text="🔢 Atur Limit IP", callback_data=f"adm_acc_lim:{acc_id}")],
        [InlineKeyboardButton(text="📊 Atur Kuota (GB)", callback_data=f"adm_acc_quota:{acc_id}"), InlineKeyboardButton(text="⚡ Atur Speed (Mbps)", callback_data=f"adm_acc_speed:{acc_id}")],
        [InlineKeyboardButton(text="🔄 Reset Pemakaian Kuota", callback_data=f"adm_acc_resetbw:{acc_id}"), InlineKeyboardButton(text="⏳ Perpanjang Aktif", callback_data=f"adm_acc_exp:{acc_id}")],
        [InlineKeyboardButton(text=lock_txt, callback_data=f"adm_acc_togglelock:{acc_id}"), InlineKeyboardButton(text="🗑 Hapus Akun", callback_data=f"adm_acc_del:{acc_id}")],
        [InlineKeyboardButton(text="⬅️ Kembali", callback_data="adm_accounts")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_acc_lim:"))
async def cb_adm_acc_lim(call: CallbackQuery):
    acc_id = int(call.data.split(":")[1])
    await _safe_edit(call, f"🔢 <b>Pilih Limit IP untuk Akun #{acc_id}:</b>", reply_markup=limit_ip_picker_kb(f"set_acclim_{acc_id}", f"adm_acc_detail:{acc_id}"))
    await call.answer()

@router.callback_query(F.data.startswith("set_acclim_"))
async def cb_set_acclim(call: CallbackQuery, state: FSMContext):
    left, val = call.data.split(":")
    acc_id = int(left.replace("set_acclim_", ""))
    if val == "manual":
        await state.set_state(AdminAccountEditFSM.value)
        await state.update_data(acc_id=acc_id, action="limit_ip")
        await _safe_edit(call, "✏️ Masukkan angka Limit IP (1-100, atau 0 untuk Unlimited):", reply_markup=cancel_kb("adm_accounts"))
        await call.answer()
        return

    lim_val = int(val)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT acc_type, username, password_or_uuid, is_locked, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
        await db.execute("UPDATE accounts SET limit_ip = ?, is_locked = 0, locked_until = '' WHERE id = ?", (lim_val, acc_id))
        await db.commit()
    if row and row[3]:
        if row[0] == "ssh":
            await lock_ssh_on_server(row[4], row[1], lock=False, password=row[2])
        else:
            await sync_xray_by_server(row[4])
    await generate_portable_backup_sh()
    await call.answer("✅ Limit IP diperbarui!", show_alert=True)
    call.data = f"adm_acc_detail:{acc_id}"
    await cb_adm_acc_detail(call)

@router.callback_query(F.data.startswith("adm_acc_quota:"))
async def cb_adm_acc_quota(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    acc_id = int(call.data.split(":")[1])
    await state.set_state(AdminAccountEditFSM.value)
    await state.update_data(acc_id=acc_id, action="quota_gb")
    await _safe_edit(
        call,
        f"📊 <b>Atur Kuota Bandwidth Akun #{acc_id} (GB)</b>\n\n"
        "Masukkan batas kuota dalam satuan <b>GB</b> (contoh: <code>10</code>, <code>50</code>, <code>1.5</code>, atau ketik <code>0</code> untuk <b>Unlimited</b>):",
        reply_markup=cancel_kb("adm_accounts")
    )
    await call.answer()

@router.callback_query(F.data.startswith("adm_acc_speed:"))
async def cb_adm_acc_speed(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    acc_id = int(call.data.split(":")[1])
    await state.set_state(AdminAccountEditFSM.value)
    await state.update_data(acc_id=acc_id, action="speed_mbps")
    await _safe_edit(
        call,
        f"⚡ <b>Atur Batas Kecepatan (Speed Limit) Akun #{acc_id}</b>\n\n"
        "Masukkan batas kecepatan dalam satuan <b>Mbps</b> (contoh: <code>10</code>, <code>25</code>, <code>50</code>, atau ketik <code>0</code> untuk <b>Unlimited</b>):",
        reply_markup=cancel_kb("adm_accounts")
    )
    await call.answer()

@router.callback_query(F.data.startswith("adm_acc_resetbw:"))
async def cb_adm_acc_resetbw(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    acc_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT acc_type, username, password_or_uuid, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
        await db.execute("UPDATE accounts SET used_bytes = 0, is_locked = 0, locked_until = '' WHERE id = ?", (acc_id,))
        await db.commit()
    if row:
        if row[0] == "ssh":
            await lock_ssh_on_server(row[3], row[1], lock=False, password=row[2])
        else:
            await sync_xray_by_server(row[3])
    await call.answer("✅ Pemakaian kuota akun di-reset ke 0 B & akun diaktifkan!", show_alert=True)
    call.data = f"adm_acc_detail:{acc_id}"
    await cb_adm_acc_detail(call)

@router.callback_query(F.data.startswith("adm_acc_exp:"))
async def cb_adm_acc_exp(call: CallbackQuery, state: FSMContext):
    acc_id = int(call.data.split(":")[1])
    await state.set_state(AdminAccountEditFSM.value)
    await state.update_data(acc_id=acc_id, action="duration")
    await _safe_edit(call, "⏳ Masukkan durasi masa aktif baru (contoh: <code>30m</code>, <code>1d</code>, <code>7d</code>, <code>30d</code>, atau <code>unlimited</code>):", reply_markup=cancel_kb("adm_accounts"))
    await call.answer()

@router.message(AdminAccountEditFSM.value)
async def fsm_adm_acc_edit_val(message: Message, state: FSMContext):
    if not await _guard_admin(message):
        return
    data = await state.get_data()
    await state.clear()
    acc_id, action = data["acc_id"], data["action"]
    txt = (message.text or "").strip()

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        if action == "limit_ip" and txt.isdigit():
            await db.execute("UPDATE accounts SET limit_ip = ?, is_locked = 0, locked_until = '' WHERE id = ?", (int(txt), acc_id))
        elif action == "quota_gb":
            try:
                q_val = max(0.0, float(txt))
            except ValueError:
                q_val = 0.0
            await db.execute("UPDATE accounts SET quota_gb = ?, is_locked = 0, locked_until = '' WHERE id = ?", (q_val, acc_id))
        elif action == "speed_mbps":
            s_val = int(txt) if txt.isdigit() else 0
            await db.execute("UPDATE accounts SET speed_mbps = ? WHERE id = ?", (max(0, s_val), acc_id))
        elif action == "duration":
            if txt.lower() in ("0", "unlimited", "tanpa expired"):
                new_exp = "Unlimited"
            else:
                td = parse_duration(txt)
                if not td:
                    await message.answer("❌ Format durasi salah.")
                    await _render_admin_home(message)
                    return
                new_exp = (now_wib() + td).strftime("%Y-%m-%d %H:%M:%S")
            await db.execute("UPDATE accounts SET expired_at = ?, is_trial = 0, notified_h1 = 0 WHERE id = ?", (new_exp, acc_id))
        await db.commit()

    await generate_portable_backup_sh()
    await message.answer(f"✅ Data akun #{acc_id} berhasil diperbarui!")
    await _render_admin_home(message)

@router.callback_query(F.data.startswith("adm_acc_togglelock:"))
async def cb_adm_acc_togglelock(call: CallbackQuery):
    acc_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT acc_type, username, password_or_uuid, is_locked, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return
        acc_type, uname, secret, is_locked, sid = row
        new_lock = 0 if is_locked else 1
        await db.execute("UPDATE accounts SET is_locked = ?, locked_until = '', violations = 0 WHERE id = ?", (new_lock, acc_id))
        await db.commit()

    if acc_type == "ssh":
        await lock_ssh_on_server(sid, uname, lock=bool(new_lock), password=secret)
    else:
        await sync_xray_by_server(sid)
    await generate_portable_backup_sh()
    await call.answer("🔓 Akun di-unlock!" if new_lock == 0 else "🔒 Akun dikunci!", show_alert=True)
    call.data = f"adm_acc_detail:{acc_id}"
    await cb_adm_acc_detail(call)

@router.callback_query(F.data.startswith("adm_acc_del:"))
async def cb_adm_acc_del(call: CallbackQuery):
    acc_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT acc_type, username, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))
            await db.commit()
            if row[0] == "ssh":
                await delete_ssh_on_server(row[2], row[1])
            else:
                await sync_xray_by_server(row[2])
    await generate_portable_backup_sh()
    await call.answer("🗑 Akun berhasil dihapus!", show_alert=True)
    await cb_adm_accounts(call)

@router.callback_query(F.data == "adm_acc_create")
async def cb_adm_acc_create(call: CallbackQuery, state: FSMContext):
    servers = await get_servers_with_usage(only_active=False)
    await state.set_state(AdminCustomAccountFSM.server_id)
    rows = [[InlineKeyboardButton(text=f"🌐 #{s['id']} {s['name']} ({s['domain']})", callback_data=f"adm_csrv:{s['id']}")] for s in servers]
    rows.append([InlineKeyboardButton(text="❌ Batal", callback_data="cancel_fsm:admin_panel")])
    await _safe_edit(call, "➕ <b>Buat Akun Custom (Admin)</b>\nPilih Server VPS tujuan:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@router.callback_query(AdminCustomAccountFSM.server_id, F.data.startswith("adm_csrv:"))
async def fsm_adm_csrv(call: CallbackQuery, state: FSMContext):
    await state.update_data(server_id=int(call.data.split(":")[1]))
    await state.set_state(AdminCustomAccountFSM.acc_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 SSH", callback_data="adm_ctype:ssh"), InlineKeyboardButton(text="🔐 Trojan", callback_data="adm_ctype:trojan")],
        [InlineKeyboardButton(text="⚡ VLESS", callback_data="adm_ctype:vless"), InlineKeyboardButton(text="🚀 VMess", callback_data="adm_ctype:vmess")],
        [InlineKeyboardButton(text="❌ Batal", callback_data="cancel_fsm:admin_panel")]
    ])
    await _safe_edit(call, "Pilih jenis akun:", reply_markup=kb)
    await call.answer()

@router.callback_query(AdminCustomAccountFSM.acc_type, F.data.startswith("adm_ctype:"))
async def fsm_adm_ctype(call: CallbackQuery, state: FSMContext):
    ctype = call.data.split(":")[1]
    await state.update_data(acc_type=ctype)
    await state.set_state(AdminCustomAccountFSM.username)
    await _safe_edit(call, f"Masukkan Username / Remarks untuk akun <b>{ctype.upper()}</b>:", reply_markup=cancel_kb("admin_panel"))
    await call.answer()

@router.message(AdminCustomAccountFSM.username)
async def fsm_adm_cuser(message: Message, state: FSMContext):
    await state.update_data(username=(message.text or "").strip().lower())
    data = await state.get_data()
    if data["acc_type"] == "ssh":
        await state.set_state(AdminCustomAccountFSM.password)
        await message.answer("Masukkan Password SSH:", reply_markup=cancel_kb("admin_panel"))
    else:
        await state.set_state(AdminCustomAccountFSM.duration)
        await message.answer("Masukkan Masa Aktif (contoh: <code>30m</code>, <code>1d</code>, <code>7d</code>, <code>30d</code>, atau <code>unlimited</code>):", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminCustomAccountFSM.password)
async def fsm_adm_cpass(message: Message, state: FSMContext):
    await state.update_data(password=(message.text or "").strip())
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(AdminCustomAccountFSM.duration)
    await message.answer("Masukkan Masa Aktif (contoh: <code>30m</code>, <code>1d</code>, <code>7d</code>, <code>30d</code>, atau <code>unlimited</code>):", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminCustomAccountFSM.duration)
async def fsm_adm_cdur(message: Message, state: FSMContext):
    await state.update_data(duration=(message.text or "").strip())
    await state.set_state(AdminCustomAccountFSM.limit_ip)
    await message.answer("Masukkan Limit IP (angka 1-100, atau <code>0</code> untuk Unlimited):", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminCustomAccountFSM.limit_ip)
async def fsm_adm_clim(message: Message, state: FSMContext):
    lim_txt = (message.text or "0").strip()
    lim_val = int(lim_txt) if lim_txt.isdigit() else 0
    data = await state.get_data()
    await state.clear()

    sid = int(data.get("server_id", 1))
    srv = await get_server_by_id(sid)
    s_dom = srv["domain"] if srv else DOMAIN
    ctype, uname, dur_str = data["acc_type"], data["username"], data["duration"]
    td = parse_duration(dur_str)
    now_dt = now_wib()
    created_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    exp_str = (now_dt + td).strftime("%Y-%m-%d %H:%M:%S") if td else "Unlimited"
    exp_disp = format_expired_display(exp_str, is_trial=0)
    uid = message.from_user.id

    if ctype == "ssh":
        secret = data["password"]
        await create_ssh_on_server(sid, uname, secret)
    else:
        secret = str(uuid.uuid4())

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute(
            "INSERT INTO accounts (tg_id, acc_type, username, password_or_uuid, expired_at, limit_ip, is_trial, created_at, server_id) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (uid, ctype, uname, secret, exp_str, lim_val, created_str, sid)
        )
        await db.commit()

    await generate_portable_backup_sh()
    if ctype in ("trojan", "vless", "vmess"):
        await sync_xray_by_server(sid)
        city, isp = await get_isp_info()
        if ctype == "vless":
            await send_multi_part_output(message, build_vless_messages(uname, city, isp, s_dom, secret, lim_val, exp_disp))
        elif ctype == "vmess":
            await send_multi_part_output(message, build_vmess_messages(uname, city, isp, s_dom, secret, lim_val, exp_disp))
        else:
            text, kb = format_trojan_output(uname, city, isp, s_dom, secret, lim_val, exp_disp)
            await message.answer(text, reply_markup=kb)
    else:
        text, kb = format_ssh_output(s_dom, uname, secret, lim_val, exp_disp, show_pass=True)
        await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "adm_users")
async def cb_adm_users(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id, username, full_name, is_banned, custom_quota FROM users ORDER BY created_at DESC LIMIT 20") as cur:
            rows = await cur.fetchall()
    btns = [[InlineKeyboardButton(text="🔍 Atur User via Input ID", callback_data="adm_usr_by_id"), InlineKeyboardButton(text="🔄 Reset Limit Semua", callback_data="adm_reset_all_limits")]]
    for tg_id, uname, fname, banned, c_quota in rows:
        btns.append([InlineKeyboardButton(text=f"{'🚫' if banned else '👤'} {fname or uname or tg_id} ({tg_id})", callback_data=f"adm_usr_detail:{tg_id}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")])
    await _safe_edit(call, "👤 <b>Kelola User & Kuota Harian</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data == "adm_usr_by_id")
async def cb_adm_usr_by_id(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminByIdFSM.tg_id)
    await _safe_edit(call, "🔍 Masukkan <b>ID Telegram User (Angka)</b>:", reply_markup=cancel_kb("admin_panel"))
    await call.answer()

@router.message(AdminByIdFSM.tg_id)
async def fsm_adm_usr_by_id(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("❌ ID harus berupa angka!", reply_markup=cancel_kb("admin_panel"))
        return
    await state.clear()
    target_id = int(txt)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id, username, full_name) VALUES (?, '', 'User via ID')", (target_id,))
        await db.commit()
    text, kb = await _build_user_detail_view(target_id)
    await message.answer(text, reply_markup=kb)

async def _build_user_detail_view(target_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT username, full_name, is_admin, is_banned, custom_quota, custom_duration, custom_limit_ip, COALESCE(custom_quota_window, '') FROM users WHERE tg_id = ?", (target_id,)) as cur:
            row = await cur.fetchone() or ("", "-", 0, 0, -1, "", -1, "")
    uname, fname, is_adm, is_ban, c_q, c_dur, _, c_win = row
    eff_lim = await get_effective_limit_ip(target_id)
    eff_daily = await get_effective_daily_limit(target_id)
    _, eff_win_lbl = await get_effective_quota_window(target_id)
    text = (
        f"👤 <b>Detail User</b>\n• ID: <code>{target_id}</code> ({html.escape(fname or '-')})\n"
        f"• Status: <code>{'BANNED' if is_ban else 'AKTIF'}</code>\n"
        f"• Batas Buat Akun: <code>{eff_daily}x per {eff_win_lbl}</code>\n"
        f"• Masa Aktif Akun: <code>{c_dur or 'Default'}</code>\n"
        f"• Limit IP: <code>{'Unlimited' if eff_lim==0 else eff_lim}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Atur Kuota (Misal 1/7d)", callback_data=f"adm_usr_field:{target_id}:quota"), InlineKeyboardButton(text="🗓 Periode Kuota (3d/7d)", callback_data=f"adm_usr_field:{target_id}:quota_window")],
        [InlineKeyboardButton(text="🔄 Reset Kuota User Ini", callback_data=f"adm_usr_resetlim:{target_id}"), InlineKeyboardButton(text="⏳ Masa Aktif", callback_data=f"adm_usr_field:{target_id}:duration")],
        [InlineKeyboardButton(text="🔢 Limit IP", callback_data=f"adm_usr_lim:{target_id}"), InlineKeyboardButton(text="🚫 Ban/Unban", callback_data=f"adm_usr_ban:{target_id}")],
        [InlineKeyboardButton(text="🗑 Hapus Semua Akun User Ini", callback_data=f"adm_usr_delaccs:{target_id}")],
        [InlineKeyboardButton(text="⬅️ Kembali", callback_data="adm_users")]
    ])
    return text, kb

@router.callback_query(F.data.startswith("adm_usr_detail:"))
async def cb_adm_usr_detail(call: CallbackQuery):
    text, kb = await _build_user_detail_view(int(call.data.split(":")[1]))
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_usr_resetlim:"))
async def cb_adm_usr_resetlim(call: CallbackQuery):
    tid = int(call.data.split(":")[1])
    await reset_user_creation_limit(tid)
    await call.answer("✅ Limit harian di-reset!", show_alert=True)
    text, kb = await _build_user_detail_view(tid)
    await _safe_edit(call, text, reply_markup=kb)

@router.callback_query(F.data.startswith("adm_usr_delaccs:"))
async def cb_adm_usr_delaccs(call: CallbackQuery):
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT acc_type, username, COALESCE(server_id, 1) FROM accounts WHERE tg_id = ?", (tid,)) as cur:
            rows = await cur.fetchall()
        await db.execute("DELETE FROM accounts WHERE tg_id = ?", (tid,))
        await db.commit()
    for acc_type, uname, sid in rows:
        if acc_type == "ssh":
            await delete_ssh_on_server(sid, uname)
    await sync_xray_by_server()
    await call.answer(f"🗑 {len(rows)} akun dihapus!", show_alert=True)

@router.callback_query(F.data.startswith("adm_usr_lim:"))
async def cb_adm_usr_lim(call: CallbackQuery):
    tid = int(call.data.split(":")[1])
    await _safe_edit(call, f"🔢 Pilih Limit IP untuk ID {tid}:", reply_markup=limit_ip_picker_kb(f"set_usrlim_{tid}", f"adm_usr_detail:{tid}"))
    await call.answer()

@router.callback_query(F.data.startswith("set_usrlim_"))
async def cb_set_usrlim(call: CallbackQuery, state: FSMContext):
    left, val = call.data.split(":")
    tid = int(left.replace("set_usrlim_", ""))
    if val == "manual":
        await state.set_state(AdminUserEditFSM.value)
        await state.update_data(target_tg_id=tid, field="limit_ip")
        await _safe_edit(call, "✏️ Masukkan angka Limit IP (0 = Unlimited):", reply_markup=cancel_kb("adm_users"))
        return
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE users SET custom_limit_ip = ? WHERE tg_id = ?", (int(val), tid))
        await db.commit()
    call.data = f"adm_usr_detail:{tid}"
    await cb_adm_usr_detail(call)

@router.callback_query(F.data.startswith("adm_usr_field:"))
async def cb_adm_usr_field(call: CallbackQuery, state: FSMContext):
    _, tid, field = call.data.split(":")
    await state.set_state(AdminUserEditFSM.value)
    await state.update_data(target_tg_id=int(tid), field=field)
    if field == "quota":
        prompt = (
            f"📦 <b>Atur Batas Buat Akun untuk ID {tid}</b>\n\n"
            "• Anda bisa langsung mengetik format <code>JUMLAH/PERIODE</code>, contoh:\n"
            "  - <code>1/7d</code> (1 kali per 7 hari / seminggu)\n"
            "  - <code>1/3d</code> (1 kali per 3 hari)\n"
            "  - <code>2/1d</code> (2 kali sehari)\n"
            "• Atau ketik angka saja (misal <code>1</code>), atau ketik <code>-1</code> untuk kembali ke aturan Default Global:"
        )
    elif field == "quota_window":
        prompt = (
            f"🗓 <b>Atur Periode Reset Kuota untuk ID {tid}</b>\n\n"
            "Masukkan durasi periode (contoh: <code>1d</code>, <code>3d</code>, <code>7d</code>, <code>14d</code>, <code>30d</code>, <code>12h</code>, atau ketik <code>default</code> untuk mengikuti Global):"
        )
    else:
        prompt = f"✏️ Masukkan nilai baru untuk <b>{field}</b>:"
    await _safe_edit(call, prompt, reply_markup=cancel_kb("adm_users"))
    await call.answer()

@router.message(AdminUserEditFSM.value)
async def fsm_adm_usr_val(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    tid, field, val = data["target_tg_id"], data["field"], (message.text or "").strip()
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        if field == "quota":
            if "/" in val:
                q_part, w_part = [x.strip() for x in val.split("/", 1)]
                if q_part.isdigit():
                    _, w_lbl = parse_window_str(w_part)
                    await db.execute("UPDATE users SET custom_quota = ?, custom_quota_window = ? WHERE tg_id = ?", (int(q_part), w_lbl, tid))
            elif val.isdigit() or val == "-1":
                if val == "-1":
                    await db.execute("UPDATE users SET custom_quota = -1, custom_quota_window = '' WHERE tg_id = ?", (tid,))
                else:
                    await db.execute("UPDATE users SET custom_quota = ? WHERE tg_id = ?", (int(val), tid))
        elif field == "quota_window":
            if val.lower() in ("default", "reset", "-1", ""):
                await db.execute("UPDATE users SET custom_quota_window = '' WHERE tg_id = ?", (tid,))
            else:
                _, w_lbl = parse_window_str(val)
                await db.execute("UPDATE users SET custom_quota_window = ? WHERE tg_id = ?", (w_lbl, tid))
        elif field == "duration":
            await db.execute("UPDATE users SET custom_duration = ? WHERE tg_id = ?", (val, tid))
        elif field == "limit_ip" and val.isdigit():
            await db.execute("UPDATE users SET custom_limit_ip = ? WHERE tg_id = ?", (int(val), tid))
        await db.commit()
    await generate_portable_backup_sh()
    text, kb = await _build_user_detail_view(tid)
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("adm_usr_ban:"))
async def cb_adm_usr_ban(call: CallbackQuery):
    tid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE users SET is_banned = CASE WHEN is_banned = 1 THEN 0 ELSE 1 END WHERE tg_id = ?", (tid,))
        await db.commit()
    call.data = f"adm_usr_detail:{tid}"
    await cb_adm_usr_detail(call)

@router.callback_query(F.data == "adm_fjoin")
async def cb_adm_fjoin(call: CallbackQuery):
    enabled = await get_setting("force_join_enabled")
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT id, chat_id, title, invite_link, is_active FROM force_join_chats") as cur:
            rows = await cur.fetchall()
    status_str = "🟢 AKTIF" if enabled == "1" else "🔴 NONAKTIF"
    lines = [f"📌 <b>Wajib Join: {status_str}</b>\n"]
    btns = [[InlineKeyboardButton(text="➕ Tambah", callback_data="fjoin_add"), InlineKeyboardButton(text=f"🔄 On/Off ({status_str})", callback_data="fjoin_toggle_global")]]
    for cid, chat_id, title, link, act in rows:
        lines.append(f"{'✅' if act else '⏸'} <b>{html.escape(title)}</b> (<code>{chat_id}</code>)")
        btns.append([InlineKeyboardButton(text=f"🗑 Hapus {title}", callback_data=f"fjoin_del:{cid}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")])
    await _safe_edit(call, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data == "fjoin_toggle_global")
async def cb_fjoin_toggle_global(call: CallbackQuery):
    await set_setting("force_join_enabled", "0" if (await get_setting("force_join_enabled")) == "1" else "1")
    invalidate_join_cache()
    await cb_adm_fjoin(call)

@router.callback_query(F.data.startswith("fjoin_del:"))
async def cb_fjoin_del_item(call: CallbackQuery):
    cid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("DELETE FROM force_join_chats WHERE id = ?", (cid,))
        await db.commit()
    invalidate_join_cache()
    await cb_adm_fjoin(call)

@router.callback_query(F.data == "fjoin_add")
async def cb_fjoin_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminForceJoinFSM.chat_id)
    await _safe_edit(call, "📌 Masukkan <code>@username</code> channel atau <code>-100xxxx</code> ID grup (Pastikan Bot sudah Admin):", reply_markup=cancel_kb("admin_panel"))
    await call.answer()

@router.message(AdminForceJoinFSM.chat_id)
async def fsm_fjoin_chat_id(message: Message, state: FSMContext):
    chat_id_input = (message.text or "").strip()
    try:
        chat = await message.bot.get_chat(chat_id_input)
    except Exception as e:
        await message.answer(f"❌ Gagal mengakses chat: <code>{e}</code>", reply_markup=cancel_kb("admin_panel"))
        return
    invite = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else "")
    await state.update_data(chat_id=str(chat.id) if not chat.username else f"@{chat.username}", title=chat.title or chat_id_input, default_invite=invite)
    await state.set_state(AdminForceJoinFSM.invite_link)
    await message.answer("Kirim URL Invite Link (atau ketik <code>auto</code>):", reply_markup=cancel_kb("admin_panel"))

@router.message(AdminForceJoinFSM.invite_link)
async def fsm_fjoin_invite(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    inp = (message.text or "").strip()
    link = data["default_invite"] if inp.lower() == "auto" and data["default_invite"] else inp
    if not link.startswith("http"):
        link = f"https://t.me/{data['chat_id'].lstrip('@')}"
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("INSERT OR REPLACE INTO force_join_chats (chat_id, title, invite_link, is_active) VALUES (?, ?, ?, 1)", (data["chat_id"], data["title"], link))
        await db.commit()
    invalidate_join_cache()
    await message.answer("✅ Channel/Grup berhasil ditambahkan!")
    await _render_admin_home(message)

@router.callback_query(F.data == "adm_settings")
async def cb_adm_settings(call: CallbackQuery):
    def_dur = await get_setting("default_duration")
    tr_dur = await get_setting("trial_duration")
    daily_lim = await get_setting("daily_create_limit")
    lim_win = await get_setting("create_limit_window") or "1d"
    g_lim = await get_setting("global_limit_ip")
    def_q = await get_setting("default_quota_gb") or "0"
    def_spd = await get_setting("default_speed_mbps") or "0"
    text = (
        "🎨 <b>Pengaturan Global</b>\n\n"
        f"• <b>Batas Buat Akun User:</b> <code>{daily_lim}x per {lim_win}</code>\n"
        f"• <b>Masa Aktif Default:</b> <code>{def_dur}</code>\n"
        f"• <b>Durasi Trial:</b> <code>{tr_dur}</code>\n"
        f"• <b>Limit IP Global:</b> <code>{'Unlimited' if g_lim=='0' else g_lim+' IP'}</code>\n"
        f"• <b>Kuota BW Default User:</b> <code>{'Unlimited' if def_q=='0' else def_q+' GB'}</code>\n"
        f"• <b>Speed Default User:</b> <code>{'Unlimited' if def_spd=='0' else def_spd+' Mbps'}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Batas Buat (Misal 1/7d)", callback_data="adm_set_key:daily_create_limit"), InlineKeyboardButton(text="🗓 Periode Reset (1d/3d/7d)", callback_data="adm_set_key:create_limit_window")],
        [InlineKeyboardButton(text="⏳ Durasi Aktif Default", callback_data="adm_set_key:default_duration"), InlineKeyboardButton(text="⏱ Durasi Trial", callback_data="adm_set_key:trial_duration")],
        [InlineKeyboardButton(text="📊 Kuota BW Default (GB)", callback_data="adm_set_key:default_quota_gb"), InlineKeyboardButton(text="⚡ Speed Default (Mbps)", callback_data="adm_set_key:default_speed_mbps")],
        [InlineKeyboardButton(text="🔢 Limit IP Global", callback_data="adm_set_glim"), InlineKeyboardButton(text="🖼 Banner SSH (Auto-Sync)", callback_data="adm_set_key:ssh_banner")],
        [InlineKeyboardButton(text="🔔 Notifikasi Grup Topic (ON/OFF)", callback_data="adm_topic_notif")],
        [InlineKeyboardButton(text="📡 Notif Login & Report Online", callback_data="adm_login_notif")],
        [InlineKeyboardButton(text="🎯 Preset Bug ISP", callback_data="adm_bug_presets"), InlineKeyboardButton(text="👋 Teks Welcome", callback_data="adm_set_key:welcome_text")],
        [InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "adm_set_glim")
async def cb_adm_set_glim(call: CallbackQuery):
    await _safe_edit(call, "🔢 Pilih Limit IP Global:", reply_markup=limit_ip_picker_kb("set_glim", "adm_settings"))
    await call.answer()

@router.callback_query(F.data.startswith("set_glim:"))
async def cb_set_glim_val(call: CallbackQuery, state: FSMContext):
    val = call.data.split(":")[1]
    if val == "manual":
        await state.set_state(AdminSettingFSM.value)
        await state.update_data(key="global_limit_ip")
        await _safe_edit(call, "✏️ Masukkan angka Limit IP Global (0 = Unlimited):", reply_markup=cancel_kb("adm_settings"))
        return
    await set_setting("global_limit_ip", val)
    await cb_adm_settings(call)

@router.callback_query(F.data.startswith("adm_set_key:"))
async def cb_adm_set_key(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":")[1]
    await state.set_state(AdminSettingFSM.value)
    await state.update_data(key=key)
    if key == "ssh_banner":
        cur_banner = ""
        if os.path.exists("/etc/issue.net"):
            with open("/etc/issue.net", "r", encoding="utf-8", errors="ignore") as f:
                cur_banner = f.read()
        await _safe_edit(
            call,
            "🖼 <b>Ubah Banner SSH (Auto-Sync ke Semua VPS)</b>\n\n"
            f"Banner saat ini:\n<pre>{html.escape(cur_banner)}</pre>\n\n"
            "Kirim kode HTML Banner baru Anda:",
            reply_markup=cancel_kb("adm_settings")
        )
    else:
        await _safe_edit(call, f"✏️ Masukkan nilai baru untuk <code>{key}</code> (ketik 0 jika ingin Unlimited):", reply_markup=cancel_kb("adm_settings"))
    await call.answer()

@router.message(AdminSettingFSM.value)
async def fsm_adm_setting_val(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    key, val = data["key"], (message.text or "").strip()
    if key == "ssh_banner":
        update_ssh_banner(val)
        sync_res = await sync_banner_all_servers()
        await generate_portable_backup_sh()
        if sync_res:
            info_sync = "\n".join(f"• {html.escape(n)}: {'✅ OK' if ok else '❌ Gagal'}" for n, ok, _ in sync_res)
            await message.answer(f"✅ <b>Banner SSH berhasil diperbarui di VPS Utama &amp; disinkronkan ke VPS Tambahan:</b>\n{info_sync}")
        else:
            await message.answer("✅ <b>Banner SSH berhasil diperbarui di VPS Utama!</b>")
        await _render_admin_home(message)
        return

    if key == "daily_create_limit" and "/" in val:
        q_part, w_part = [x.strip() for x in val.split("/", 1)]
        if q_part.isdigit():
            _, w_lbl = parse_window_str(w_part)
            await set_setting("daily_create_limit", q_part)
            await set_setting("create_limit_window", w_lbl)
    elif key == "create_limit_window":
        _, w_lbl = parse_window_str(val)
        await set_setting("create_limit_window", w_lbl)
    else:
        await set_setting(key, val)
    await generate_portable_backup_sh()
    await message.answer(f"✅ Pengaturan <code>{key}</code> berhasil diperbarui!")
    await _render_admin_home(message)

@router.callback_query(F.data == "adm_stats_legacy")
async def cb_adm_stats(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    st = get_server_stats()
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        u_cnt = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        ban_cnt = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_banned=1")).fetchone())[0]
        tot_acc = (await (await db.execute("SELECT COUNT(*) FROM accounts")).fetchone())[0]
        tr_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE is_trial=1")).fetchone())[0]
        reg_cnt = tot_acc - tr_cnt
        ssh_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type='ssh'")).fetchone())[0]
        trj_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type='trojan'")).fetchone())[0]
        vls_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type='vless'")).fetchone())[0]
        vms_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type='vmess'")).fetchone())[0]
        lck_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE is_locked=1")).fetchone())[0]

    servers = await get_servers_with_usage(only_active=False)
    srv_quota_lines = []
    for s in servers:
        cap = f"{s['used_users']}/♾" if s["max_users"] == 0 else f"{s['used_users']}/{s['max_users']}"
        u_b = format_bytes_human(s.get("used_bytes", 0))
        spd_s = "♾" if int(s.get("speed_mbps", 0) or 0) <= 0 else f"{s['speed_mbps']}Mbps"
        st_ico = "🟢" if s["is_active"] else "🔴"
        srv_quota_lines.append(f"• {st_ico} <b>{html.escape(s['name'])}</b>: <code>{cap} Akun</code> | BW: <code>{u_b}</code> | Spd: <code>{spd_s}</code>")

    srvs = ["xray", "ssh", "dropbear", "haproxy", "nginx", "ws-ssh"]
    srv_lines = [f"• <b>{s.upper()}:</b> {check_service_status(s)}" for s in srvs]

    text = (
        "📊 <b>Statistik Sistem, Akun &amp; Status Service</b>\n\n"
        "👥 <b>Data Pengguna &amp; Akun VPN:</b>\n"
        f"• <b>Total Pengguna Bot:</b> <code>{u_cnt} User</code> (Banned: <code>{ban_cnt}</code>)\n"
        f"• <b>Total Semua Akun:</b> <code>{tot_acc} Akun</code> (Reguler: <code>{reg_cnt}</code> | Trial: <code>{tr_cnt}</code>)\n"
        f"• <b>Akun SSH:</b> <code>{ssh_cnt}</code> | <b>Trojan:</b> <code>{trj_cnt}</code>\n"
        f"• <b>Akun VLESS:</b> <code>{vls_cnt}</code> | <b>VMess:</b> <code>{vms_cnt}</code>\n"
        f"• <b>Akun Terkunci (Limit IP/Kuota):</b> <code>{lck_cnt}</code>\n\n"
        "🌐 <b>Distribusi Akun &amp; Trafik per Server VPS:</b>\n"
        + ("\n".join(srv_quota_lines) if srv_quota_lines else "• -") + "\n\n"
        "🖥 <b>Resource VPS Utama:</b>\n"
        f"• <b>CPU Load:</b> <code>{st['cpu']}</code>\n"
        f"• <b>RAM Usage:</b> <code>{st['ram']}</code>\n"
        f"• <b>Disk Usage:</b> <code>{st['disk']}</code>\n"
        f"• <b>Bandwidth (TX / RX):</b> <code>{st['tx']} / {st['rx']}</code>\n\n"
        "🔧 <b>Status Service Lokal:</b>\n" + "\n".join(srv_lines)
    )
    btns = [
        [
            InlineKeyboardButton(text="🔄 Restart Xray", callback_data="adm_restart:xray"),
            InlineKeyboardButton(text="🔄 Restart Dropbear", callback_data="adm_restart:dropbear")
        ],
        [
            InlineKeyboardButton(text="🔄 Restart HAProxy & Nginx", callback_data="adm_restart:haproxy"),
            InlineKeyboardButton(text="🔄 Restart WS-SSH", callback_data="adm_restart:ws-ssh")
        ],
        [
            InlineKeyboardButton(text="🔐 Perbarui / Force Renew Sertifikat SSL", callback_data="adm_renew_ssl")
        ],
        [
            InlineKeyboardButton(text="🔄 Refresh Statistik", callback_data="adm_stats"),
            InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")
        ]
    ]
    await _safe_edit(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data == "adm_renew_ssl")
async def cb_adm_renew_ssl(call: CallbackQuery):
    await call.answer("⏳ Memperbarui SSL...", show_alert=True)
    await asyncio.to_thread(subprocess.run, ["/usr/local/bin/renew-ssl.sh", "--force"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    await cb_adm_stats(call)

@router.callback_query(F.data.startswith("adm_restart:"))
async def cb_adm_restart_srv(call: CallbackQuery):
    srv = call.data.split(":")[1]
    await asyncio.to_thread(subprocess.run, ["systemctl", "restart", srv], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if srv == "haproxy":
        await asyncio.to_thread(subprocess.run, ["systemctl", "restart", "nginx"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    await call.answer(f"✅ Service {srv} di-restart!", show_alert=True)
    await cb_adm_stats(call)

@router.callback_query(F.data == "adm_backup_legacy")
async def cb_adm_backup(call: CallbackQuery):
    sh_path = await generate_portable_backup_sh()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Download Backup (.sh)", callback_data="adm_dl_backup_sh")],
        [InlineKeyboardButton(text="📤 Upload & Restore (.sh)", callback_data="adm_up_backup_sh")],
        [InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")]
    ])
    await _safe_edit(call, f"💾 <b>Auto Backup &amp; Restore</b>\nLokasi: <code>{sh_path}</code>", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "adm_dl_backup_sh")
async def cb_adm_dl_backup_sh(call: CallbackQuery):
    sh_path = await generate_portable_backup_sh()
    await call.message.answer_document(FSInputFile(sh_path), caption="💾 File Auto-Backup (.sh)")
    await call.answer()

@router.callback_query(F.data == "adm_up_backup_sh")
async def cb_adm_up_backup_sh(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminRestoreFSM.waiting_file)
    await _safe_edit(call, "📤 Kirimkan file <code>backup-data-vpn.sh</code> ke chat ini:", reply_markup=cancel_kb("admin_panel"))
    await call.answer()

@router.message(AdminRestoreFSM.waiting_file)
async def fsm_adm_restore_file(message: Message, state: FSMContext):
    if not message.document:
        await message.answer("❌ Kirimkan file dokumen .sh!", reply_markup=cancel_kb("admin_panel"))
        return
    await state.clear()
    tmp_path = f"/root/restore_{int(datetime.datetime.now().timestamp())}.sh"
    f_info = await message.bot.get_file(message.document.file_id)
    await message.bot.download_file(f_info.file_path, tmp_path)
    with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    ok, msg = await restore_from_sh_content(content)
    await message.answer(f"{'✅' if ok else '❌'} {msg}")
    await _render_admin_home(message)

@router.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBroadcastFSM.message)
    await _safe_edit(call, "📢 Kirim pesan broadcast:", reply_markup=cancel_kb("admin_panel"))
    await call.answer()

@router.message(AdminBroadcastFSM.message)
async def fsm_adm_broadcast_send(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id FROM users") as cur:
            users = await cur.fetchall()
    ok = 0
    for (uid,) in users:
        try:
            await message.bot.send_message(uid, f"📢 <b>Pengumuman Admin:</b>\n\n{message.html_text}")
            ok += 1
        except Exception:
            pass
    await message.answer(f"✅ Broadcast terkirim ke {ok} user.")
    await _render_admin_home(message)

@router.callback_query(F.data == "adm_logs")
async def cb_adm_logs(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id, action, detail, created_at FROM logs ORDER BY id DESC LIMIT 15") as cur:
            rows = await cur.fetchall()
    lines = ["📜 <b>15 Log Aktivitas Terbaru:</b>\n"] + [f"• <code>{dt}</code> | <b>{act}</b> (<code>{uid}</code>): {html.escape(det)}" for uid, act, det, dt in rows]
    await _safe_edit(call, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Kembali", callback_data="admin_panel")]]))
    await call.answer()


# === FITUR NOTIFIKASI KE GRUP TOPIC (ON/OFF & AUTO-DETECT /setlog) ===
@router.message(Command("id", "setlog"))
async def cmd_get_or_set_topic_log(message: Message):
    if not await is_user_admin(message.from_user.id):
        return
    chat_id = str(message.chat.id)
    thread_id = str(message.message_thread_id or 0)
    cmd_txt = (message.text or "").strip().lower()

    if cmd_txt.startswith("/setlog"):
        await set_setting("notif_group_id", chat_id)
        await set_setting("notif_topic_id", thread_id)
        await set_setting("notif_topic_enabled", "1")
        await generate_portable_backup_sh()
        await message.reply(
            "✅ <b>Topic Ini Berhasil Dijadikan Tujuan Notifikasi Bot!</b>\n\n"
            f"• <b>Status Notifikasi:</b> <code>🟢 AKTIF (ON)</code>\n"
            f"• <b>Group Chat ID:</b> <code>{chat_id}</code>\n"
            f"• <b>Topic / Thread ID:</b> <code>{thread_id}</code>\n\n"
            "Setiap ada user yang membuat akun Trial maupun Reguler, laporannya akan dikirim ke topic ini."
        )
    else:
        await message.reply(
            "ℹ️ <b>Informasi ID Chat &amp; Topic</b>\n\n"
            f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
            f"• <b>Topic / Thread ID:</b> <code>{thread_id}</code>\n\n"
            "💡 <i>Ketik <code>/setlog</code> di dalam topic ini jika ingin langsung menjadikannya tempat notifikasi pembuatan akun.</i>"
        )

@router.callback_query(F.data == "adm_topic_notif")
async def cb_adm_topic_notif(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    en = (await get_setting("notif_topic_enabled")) == "1"
    grp_id = await get_setting("notif_group_id") or "-"
    top_id = await get_setting("notif_topic_id") or "0"
    st_str = "🟢 AKTIF (ON)" if en else "🔴 NONAKTIF (OFF)"
    tog_lbl = "🔴 Matikan Notifikasi (OFF)" if en else "🟢 Hidupkan Notifikasi (ON)"

    text = (
        "🔔 <b>Pengaturan Notifikasi Grup Topic</b>\n\n"
        f"• <b>Status Fitur:</b> <code>{st_str}</code>\n"
        f"• <b>Group Chat ID:</b> <code>{html.escape(grp_id)}</code>\n"
        f"• <b>Topic / Thread ID:</b> <code>{html.escape(top_id)}</code> <i>(0 = General / Tanpa Topic)</i>\n\n"
        "💡 <b>Cara Paling Mudah Mengatur Topic:</b>\n"
        "1. Masukkan Bot ke Grup Anda dan jadikan <b>Admin</b>.\n"
        "2. Buka <b>Topic</b> grup yang diinginkan, lalu ketik perintah:\n"
        "   <code>/setlog</code>\n"
        "3. Bot akan otomatis menyimpan ID Grup &amp; ID Topic tersebut serta mengaktifkan notifikasi!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tog_lbl, callback_data="adm_topic_toggle")],
        [
            InlineKeyboardButton(text="✏️ Input ID Grup", callback_data="adm_set_key:notif_group_id"),
            InlineKeyboardButton(text="✏️ Input ID Topic", callback_data="adm_set_key:notif_topic_id")
        ],
        [InlineKeyboardButton(text="🧪 Tes Kirim Notifikasi ke Topic", callback_data="adm_topic_test")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Pengaturan", callback_data="adm_settings")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "adm_topic_toggle")
async def cb_adm_topic_toggle(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("notif_topic_enabled")
    new_val = "0" if cur == "1" else "1"
    await set_setting("notif_topic_enabled", new_val)
    await generate_portable_backup_sh()
    await call.answer("🟢 Notifikasi Topic DIAKTIFKAN!" if new_val == "1" else "🔴 Notifikasi Topic DINONAKTIFKAN!", show_alert=True)
    await cb_adm_topic_notif(call)

@router.callback_query(F.data == "adm_topic_test")
async def cb_adm_topic_test(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    grp_id = (await get_setting("notif_group_id") or "").strip()
    if not grp_id:
        await call.answer("⚠️ Group Chat ID belum diatur! Ketik /setlog di dalam topic grup Anda terlebih dahulu.", show_alert=True)
        return
    prev_en = await get_setting("notif_topic_enabled")
    await set_setting("notif_topic_enabled", "1")
    await send_account_topic_notification(
        call.bot,
        call.from_user.id,
        call.from_user.full_name or "Admin Test",
        "vless",
        "test-akun",
        1,
        now_wib().strftime("%Y-%m-%d %H:%M:%S"),
        is_trial=True,
        dur_label="30m (TEST)"
    )
    await set_setting("notif_topic_enabled", prev_en)
    await call.answer("✅ Pesan tes telah dikirim ke Topic Grup! Silakan cek grup Anda.", show_alert=True)

# === FITUR NOTIFIKASI LOGIN & REPORT ONLINE (DUA FITUR TERPISAH) ===
@router.callback_query(F.data == "adm_login_notif")
async def cb_adm_login_notif(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    notif_ca = (await get_setting("notif_create_account")) == "1"
    notif_exp = (await get_setting("notif_expired_user")) == "1"
    en_login = (await get_setting("login_notif_enabled")) == "1"
    en_login_xray = (await get_setting("login_notif_xray_enabled")) == "1"
    interval = int(await get_setting("login_notif_interval_min") or "0")
    show_ip = (await get_setting("show_ip_in_notif")) == "1"

    def st(b):
        return "🟢 AKTIF" if b else "🔴 NONAKTIF"
    def tg(b):
        return "OFF" if b else "ON"

    text = (
        "📡 <b>Panel Notifikasi</b>\n\n"
        "Atur tiap jenis notifikasi <b>terpisah</b>. Semua hanya terkirim "
        "bila <b>Notifikasi Grup Topic</b> aktif.\n\n"
        f"<b>1) Pembuatan Akun</b> (trial/reguler baru ke topic)\n"
        f"   Status: <code>{st(notif_ca)}</code>\n\n"
        f"<b>2) Expired</b> (pemberitahuan akun expired ke user)\n"
        f"   Status: <code>{st(notif_exp)}</code>\n\n"
        f"<b>3) Login Baru</b> (setiap ada user login ke topic)\n"
        f"   Status: <code>{st(en_login)}</code>\n\n"
        f"<b>4) Report Online</b> (rekap berkala user online ke topic)\n"
        f"   Interval: <code>{'🟢 ' + str(interval) + ' Menit' if interval > 0 else '🔴 NONAKTIF'}</code>\n\n"
        f"<b>Privasi:</b> Tampilkan IP client di notifikasi?\n"
        f"   Status: <code>{'👁 Tampil' if show_ip else '🙈 Disembunyikan'}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"1) Buat Akun  [{tg(notif_ca)}]", callback_data="adm_notif_create")],
        [InlineKeyboardButton(text=f"2) Expired  [{tg(notif_exp)}]", callback_data="adm_notif_expired")],
        [InlineKeyboardButton(text=f"3) Login  [{tg(en_login)}]", callback_data="adm_login_toggle")],
        [InlineKeyboardButton(text=f"   └ Login Xray  [{tg(en_login_xray)}]", callback_data="adm_login_xray_toggle")],
        [InlineKeyboardButton(text=f"4) Report Online  ({interval} mnt)", callback_data="adm_report_interval")],
        [InlineKeyboardButton(text="🧪 Kirim Report Sekarang", callback_data="adm_report_now")],
        [InlineKeyboardButton(text=f"👁 IP: {'Tampil' if show_ip else 'Sembunyi'}", callback_data="adm_toggle_showip")],
        [InlineKeyboardButton(text="⬅️ Kembali", callback_data="adm_topic_notif")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "adm_notif_create")
async def cb_adm_notif_create(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("notif_create_account")
    new_val = "0" if cur == "1" else "1"
    await set_setting("notif_create_account", new_val)
    await generate_portable_backup_sh()
    await call.answer("🟢 Notif Buat Akun DIAKTIFKAN!" if new_val == "1" else "🔴 Notif Buat Akun DINONAKTIFKAN!", show_alert=True)
    await cb_adm_login_notif(call)

@router.callback_query(F.data == "adm_notif_expired")
async def cb_adm_notif_expired(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("notif_expired_user")
    new_val = "0" if cur == "1" else "1"
    await set_setting("notif_expired_user", new_val)
    await generate_portable_backup_sh()
    await call.answer("🟢 Notif Expired DIAKTIFKAN!" if new_val == "1" else "🔴 Notif Expired DINONAKTIFKAN!", show_alert=True)
    await cb_adm_login_notif(call)

@router.callback_query(F.data == "adm_login_toggle")
async def cb_adm_login_toggle(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("login_notif_enabled")
    new_val = "0" if cur == "1" else "1"
    await set_setting("login_notif_enabled", new_val)
    await generate_portable_backup_sh()
    await call.answer("🟢 Notif Login DIAKTIFKAN!" if new_val == "1" else "🔴 Notif Login DINONAKTIFKAN!", show_alert=True)
    await cb_adm_login_notif(call)

@router.callback_query(F.data == "adm_login_xray_toggle")
async def cb_adm_login_xray_toggle(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("login_notif_xray_enabled")
    new_val = "0" if cur == "1" else "1"
    await set_setting("login_notif_xray_enabled", new_val)
    await generate_portable_backup_sh()
    await call.answer("🟢 Notif Login Xray DIAKTIFKAN!" if new_val == "1" else "🔴 Notif Login Xray DINONAKTIFKAN! (IP Xray belum tersedia)", show_alert=True)
    await cb_adm_login_notif(call)

@router.callback_query(F.data == "adm_toggle_showip")
async def cb_adm_toggle_showip(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("show_ip_in_notif")
    new_val = "0" if cur == "1" else "1"
    await set_setting("show_ip_in_notif", new_val)
    await generate_portable_backup_sh()
    await call.answer("👁 IP akan DITAMPILKAN di notifikasi." if new_val == "1" else "🙈 IP akan DISEMBUNYIKAN di notifikasi.", show_alert=True)
    await cb_adm_login_notif(call)

@router.callback_query(F.data == "adm_report_interval")
async def cb_adm_report_interval(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.set_state(AdminSettingFSM.value)
    await state.update_data(key="login_notif_interval_min")
    await _safe_edit(
        call,
        "⏱ Masukkan interval report online dalam <b>menit</b> (contoh: 60 = tiap 1 jam).\n"
        "Ketik <code>0</code> untuk mematikan report.",
        reply_markup=cancel_kb("adm_login_notif")
    )
    await call.answer()

@router.callback_query(F.data == "adm_report_now")
async def cb_adm_report_now(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    from .login_notifier import send_online_report
    grp = (await get_setting("notif_group_id") or "").strip()
    if not grp:
        await call.answer("⚠️ Group Topic belum diatur. Ketik /setlog di topic grup dulu.", show_alert=True)
        return
    prev_en = await get_setting("notif_topic_enabled")
    await set_setting("notif_topic_enabled", "1")
    ok = await send_online_report(call.bot)
    await set_setting("notif_topic_enabled", prev_en)
    await call.answer("✅ Report terkirim ke topic!" if ok else "⚠️ Gagal kirim. Cek koneksi bot.", show_alert=True)

# === SUB-MENU KELOLA PRESET BUG ISP (WILDCARD, SNI, CDN) ===
import re
import html
from aiogram.fsm.context import FSMContext
from .states import AdminBugAddFSM

def _parse_bug_items(raw: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for part in re.split(r"[,\n]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            lbl, dom = part.split("=", 1)
            lbl, dom = lbl.strip(), dom.strip()
        else:
            lbl, dom = part, part
        if dom:
            items.append((lbl, dom))
    return items

def _serialize_bug_items(items: list[tuple[str, str]]) -> str:
    out = []
    for lbl, dom in items:
        out.append(f"{lbl}={dom}" if lbl != dom else dom)
    return ", ".join(out)

def _bug_cat_meta(mode: str) -> tuple[str, str]:
    meta = {
        "wildcard": ("bug_presets_wildcard", "🌐 Wildcard (bug.domain)"),
        "sni": ("bug_presets_sni", "🔒 SNI / SSL (Spoof Bug)"),
        "cdn": ("bug_presets_cdn", "☁️ CDN / WS (Direct)"),
    }
    return meta.get(mode, ("bug_presets_wildcard", "🌐 Wildcard (bug.domain)"))

@router.callback_query(F.data == "adm_bug_presets")
async def cb_adm_bug_presets(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    wc_items = _parse_bug_items(await get_setting("bug_presets_wildcard"))
    sni_items = _parse_bug_items(await get_setting("bug_presets_sni"))
    cdn_items = _parse_bug_items(await get_setting("bug_presets_cdn"))

    wc_str = ", ".join([f"{l} ({d})" if l != d else d for l, d in wc_items]) or "<i>(Kosong)</i>"
    sni_str = ", ".join([f"{l} ({d})" if l != d else d for l, d in sni_items]) or "<i>(Kosong)</i>"
    cdn_str = ", ".join([f"{l} ({d})" if l != d else d for l, d in cdn_items]) or "<i>(Kosong)</i>"

    text = (
        "🎯 <b>Kelola Preset Bug ISP (Tambah &amp; Hapus Bug)</b>\n\n"
        f"🌐 <b>1. Wildcard ({len(wc_items)} bug):</b>\n{wc_str}\n\n"
        f"🔒 <b>2. SNI / SSL ({len(sni_items)} bug):</b>\n{sni_str}\n\n"
        f"☁️ <b>3. CDN / WS ({len(cdn_items)} bug):</b>\n{cdn_str}\n\n"
        "👇 <i>Pilih kategori di bawah untuk <b>Menambah</b> atau <b>Menghapus</b> bug satu per satu:</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌐 Kelola Bug Wildcard ({len(wc_items)})", callback_data="adm_bug_cat:wildcard")],
        [InlineKeyboardButton(text=f"🔒 Kelola Bug SNI / SSL ({len(sni_items)})", callback_data="adm_bug_cat:sni")],
        [InlineKeyboardButton(text=f"☁️ Kelola Bug CDN / WS ({len(cdn_items)})", callback_data="adm_bug_cat:cdn")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Pengaturan", callback_data="adm_settings")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

async def _render_bug_cat_view(mode: str) -> tuple[str, InlineKeyboardMarkup]:
    db_key, title = _bug_cat_meta(mode)
    items = _parse_bug_items(await get_setting(db_key))

    if items:
        lines = [f"  {i+1}. <b>{html.escape(lbl)}</b> → <code>{html.escape(dom)}</code>" for i, (lbl, dom) in enumerate(items)]
        list_txt = "\n".join(lines)
    else:
        list_txt = "  <i>(Belum ada preset bug di kategori ini)</i>"

    text = (
        f"🎯 <b>Kategori: {title}</b>\n\n"
        f"📋 <b>Daftar Bug Saat Ini ({len(items)}):</b>\n{list_txt}\n\n"
        "💡 <i>Ketuk tombol <b>🗑 [Nama Bug]</b> di bawah untuk menghapus bug tersebut secara langsung:</i>"
    )

    btns = []
    row_b = []
    for idx, (lbl, dom) in enumerate(items[:20]):
        row_b.append(InlineKeyboardButton(text=f"🗑 {lbl[:18]}", callback_data=f"adm_bug_del:{mode}:{idx}"))
        if len(row_b) == 2:
            btns.append(row_b)
            row_b = []
    if row_b:
        btns.append(row_b)

    btns.append([
        InlineKeyboardButton(text="➕ Tambah Bug", callback_data=f"adm_bug_add:{mode}"),
        InlineKeyboardButton(text="✏️ Ganti Semua", callback_data=f"adm_set_key:{db_key}")
    ])
    if items:
        btns.append([InlineKeyboardButton(text="🧹 Hapus Semua Bug Kategori Ini", callback_data=f"adm_bug_clr:{mode}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali ke Daftar Kategori", callback_data="adm_bug_presets")])
    return text, InlineKeyboardMarkup(inline_keyboard=btns)

@router.callback_query(F.data.startswith("adm_bug_cat:"))
async def cb_adm_bug_cat(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    mode = call.data.split(":", 1)[1]
    text, kb = await _render_bug_cat_view(mode)
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_bug_del:"))
async def cb_adm_bug_del(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    _, mode, idx_s = call.data.split(":", 2)
    idx = int(idx_s)
    db_key, _ = _bug_cat_meta(mode)
    items = _parse_bug_items(await get_setting(db_key))
    if 0 <= idx < len(items):
        removed_lbl, removed_dom = items.pop(idx)
        await set_setting(db_key, _serialize_bug_items(items))
        await generate_portable_backup_sh()
        await call.answer(f"🗑 Bug '{removed_lbl}' ({removed_dom}) dihapus!", show_alert=False)
    else:
        await call.answer("Bug tidak ditemukan.", show_alert=True)
    text, kb = await _render_bug_cat_view(mode)
    await _safe_edit(call, text, reply_markup=kb)

@router.callback_query(F.data.startswith("adm_bug_clr:"))
async def cb_adm_bug_clr(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    mode = call.data.split(":", 1)[1]
    db_key, title = _bug_cat_meta(mode)
    await set_setting(db_key, "")
    await generate_portable_backup_sh()
    await call.answer(f"🧹 Semua bug pada {title} berhasil dikosongkan!", show_alert=True)
    text, kb = await _render_bug_cat_view(mode)
    await _safe_edit(call, text, reply_markup=kb)

@router.callback_query(F.data.startswith("adm_bug_add:"))
async def cb_adm_bug_add(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    mode = call.data.split(":", 1)[1]
    _, title = _bug_cat_meta(mode)
    await state.set_state(AdminBugAddFSM.value)
    await state.update_data(bug_mode=mode)
    prompt = (
        f"➕ <b>Tambah Preset Bug — {title}</b>\n\n"
        "Masukkan 1 atau beberapa bug baru (pisahkan dengan koma atau baris baru).\n"
        "• <b>Contoh pakai nama:</b> <code>XL Conference=zoom.us</code>\n"
        "• <b>Contoh langsung domain/IP:</b> <code>ava.game.naver.com</code>\n\n"
        "<i>Bug yang Anda ketik akan ditambahkan ke daftar tanpa menghapus bug lama:</i>"
    )
    await _safe_edit(call, prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Batal", callback_data=f"adm_bug_cat:{mode}")]]))
    await call.answer()

@router.message(AdminBugAddFSM.value)
async def fsm_adm_bug_add_val(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.clear()
    mode = data.get("bug_mode", "wildcard")
    db_key, _ = _bug_cat_meta(mode)
    current_items = _parse_bug_items(await get_setting(db_key))
    new_items = _parse_bug_items(message.text or "")
    if not new_items:
        await message.answer("❌ Input bug tidak valid.")
        return
    combined = current_items + new_items
    await set_setting(db_key, _serialize_bug_items(combined))
    await generate_portable_backup_sh()
    text, kb = await _render_bug_cat_view(mode)
    msg_out = f"✅ Berhasil menambahkan <b>{len(new_items)}</b> bug baru!\n\n{text}"
    await message.answer(msg_out, reply_markup=kb)

# === FITUR COMMAND /backup & AUTO-BACKUP KE TELEGRAM ADMIN ===
from aiogram.types import FSInputFile
import aiosqlite
from .config import DB_PATH, now_wib

async def _get_backup_counts() -> tuple[int, int, int, int]:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        u_cnt = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        ssh_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type = 'ssh'")).fetchone())[0]
        xray_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type != 'ssh'")).fetchone())[0]
        srv_cnt = (await (await db.execute("SELECT COUNT(*) FROM servers")).fetchone())[0]
    return u_cnt, ssh_cnt, xray_cnt, srv_cnt


@router.callback_query(F.data == "adm_backup")
async def cb_adm_backup_new(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    text, kb = await _render_adm_backup_menu()
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "adm_autobackup_toggle")
async def cb_adm_autobackup_toggle(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("auto_backup_tg")
    new_val = "0" if cur == "1" else "1"
    await set_setting("auto_backup_tg", new_val)
    await generate_portable_backup_sh()
    st_msg = "🟢 Auto-Backup Terjadwal ke Chat Admin DIAKTIFKAN (Setiap 6 Jam)!" if new_val == "1" else "🔴 Auto-Backup Terjadwal ke Chat Admin DINONAKTIFKAN!"
    await call.answer(st_msg, show_alert=True)
    text, kb = await _render_adm_backup_menu()
    await _safe_edit(call, text, reply_markup=kb)

@router.message(Command("backup"))
async def cmd_quick_backup(message: Message):
    if not await is_user_admin(message.from_user.id):
        return
    sh_path = await generate_portable_backup_sh()
    u_cnt, ssh_cnt, xray_cnt, srv_cnt = await _get_backup_counts()
    wib_str = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")
    cap = (
        "💾 <b>FILE FULL BACKUP BOT VPN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Waktu Backup:</b> <code>{wib_str}</code>\n"
        f"👥 <b>Total Pengguna:</b> <code>{u_cnt} User</code>\n"
        f"🔑 <b>Akun SSH Aktif:</b> <code>{ssh_cnt} Akun</code>\n"
        f"🔐 <b>Akun Xray Aktif:</b> <code>{xray_cnt} Akun</code>\n"
        f"🌐 <b>Total Server VPS:</b> <code>{srv_cnt} Server</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>Cara Restore Saat Rebuild VPS:</b>\n"
        "1. Upload file ini ke menu <b>💾 Backup &amp; Restore</b> di Bot, <i>atau</i>\n"
        "2. Jalankan di terminal: <code>bash backup-data-vpn.sh --restore</code>"
    )
    await message.answer_document(FSInputFile(sh_path, filename="backup-data-vpn.sh"), caption=cap)

# === STATISTIK & RESOURCE LENGKAP KHUSUS PANEL ADMIN ===
from .system_manager import get_cluster_node_stats, check_service_status, format_bytes_human

@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats_full(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    asyncio.create_task(refresh_all_servers_live_stats(force=False))
    servers = await get_servers_with_usage(only_active=False)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        u_total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        acc_total = (await (await db.execute("SELECT COUNT(*) FROM accounts")).fetchone())[0]
        locked_total = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE is_locked = 1")).fetchone())[0]
        srv_bw_rows = await (await db.execute("SELECT COALESCE(server_id, 1), SUM(COALESCE(used_bytes, 0)) FROM accounts GROUP BY COALESCE(server_id, 1)")).fetchall()
    bw_by_srv = {int(r[0]): int(r[1] or 0) for r in srv_bw_rows}

    xray_st = check_service_status("xray")
    ssh_st = check_service_status("ssh")
    drop_st = check_service_status("dropbear")
    hap_st = check_service_status("haproxy")
    ngx_st = check_service_status("nginx")

    srv_blocks = []
    for s in servers:
        sid = int(s["id"])
        nstat = get_cluster_node_stats(sid)
        st_ico = "🟢 Online" if nstat.get("online", True) else "🔴 Offline"
        used_bw = format_bytes_human(bw_by_srv.get(sid, 0))
        cap_str = f"{s['used_users']} / {'♾' if s['max_users'] == 0 else s['max_users']} Akun"

        srv_blocks.append(
            f"🖥 <b>{html.escape(s['name'])}</b> ({st_ico})\n"
            f"├ <b>Domain / IP:</b> <code>{html.escape(s['domain'])} ({html.escape(s['ip'])})</code>\n"
            f"├ <b>CPU / RAM:</b> <code>{nstat['cpu']} | {nstat['ram']}</code>\n"
            f"├ <b>Disk VPS:</b> <code>{nstat['disk']}</code>\n"
            f"├ <b>Kapasitas &amp; Kuota User:</b> <code>{cap_str} | {used_bw}</code>\n"
            f"📊 <b>[ TRAFFIC RATE (LIVE) ]</b>\n"
            f"⬇ <b>IN   :</b> <code>{nstat['in_mbps']}</code>\n"
            f"⬆ <b>OUT  :</b> <code>{nstat['out_mbps']}</code>\n"
            f"📈 <b>[ VNSTAT BANDWIDTH ]</b>\n"
            f"🗓 <b>Day  :</b> <code>{nstat['vnstat_day']}</code>\n"
            f"📆 <b>Mth  :</b> <code>{nstat['vnstat_mth']}</code>"
        )

    text = (
        "📊 <b>STATISTIK SERVER &amp; HARDWARE (PANEL ADMIN)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total User Bot:</b> <code>{u_total}</code> | <b>Total Akun:</b> <code>{acc_total}</code> (🔒 <code>{locked_total}</code>)\n"
        f"⚙️ <b>Service Utama:</b> Xray {xray_st} | SSH {ssh_st} | Dropbear {drop_st}\n"
        f"🌐 <b>Proxy Utama:</b> HAProxy {hap_st} | Nginx {ngx_st}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(srv_blocks)
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh Data & Traffic", callback_data="adm_stats")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

# === MENU KONTROL ON/OFF FITUR KOMUNITAS, PRESET HEALTH CHECK & EDIT WELCOME ===
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from .system_manager import (
    run_auto_heal_and_monitor, check_all_presets_health, remove_dead_presets_from_db
)


_LAST_DEAD_PRESETS_CACHE: set[str] = set()







@router.callback_query(F.data == "adm_feat_toggles")
async def cb_adm_feat_toggles(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    text, kb = await _render_adm_feat_toggles()
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "adm_run_heal_now")
async def cb_adm_run_heal_now(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    await call.answer("⏳ Memeriksa kesehatan seluruh server & service...")
    logs = await run_auto_heal_and_monitor(call.bot, manual_trigger=True)
    text, kb = await _render_adm_feat_toggles()
    report_box = "\n".join(logs) if logs else "Semua server normal."
    await _safe_edit(call, f"{text}\n\n🩺 <b>Hasil Pemeriksaan Langsung:</b>\n{report_box}", reply_markup=kb)

@router.callback_query(F.data == "adm_check_presets_health")
async def cb_adm_check_presets_health(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    await call.answer("⏳ Memindai seluruh preset bug (Wildcard, SNI & CDN)...")
    await _safe_edit(
        call,
        "⏳ <b>Sedang memeriksa kesehatan seluruh Preset Bug ISP...</b>\n"
        "<i>Bot sedang mengetes DNS, Ping, SSL, dan Payload 101 WebSocket pada semua preset yang tersimpan. Mohon tunggu ±3-6 detik...</i>"
    )
    res = await check_all_presets_health()
    global _LAST_DEAD_PRESETS_CACHE
    _LAST_DEAD_PRESETS_CACHE = set(res.get("dead_domains", []))

    text = (
        "🩺 <b>LAPORAN KESEHATAN SEMUA PRESET BUG ISP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Preset Aktif / Sehat:</b> <code>🟢 {res['alive_count']} Bug</code>\n"
        f"• <b>Preset Mati / Timeout:</b> <code>🔴 {res['dead_count']} Bug</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{res['report_html']}"
    )
    btns = []
    if _LAST_DEAD_PRESETS_CACHE:
        btns.append([InlineKeyboardButton(
            text=f"🧹 Hapus Semua Preset yang Mati ({len(_LAST_DEAD_PRESETS_CACHE)} Bug)",
            callback_data="adm_clean_dead_presets"
        )])
    btns.append([
        InlineKeyboardButton(text="🔄 Scan Ulang Preset", callback_data="adm_check_presets_health"),
        InlineKeyboardButton(text="🎯 Kelola Preset", callback_data="adm_bug_presets")
    ])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali ke Kontrol Fitur", callback_data="adm_feat_toggles")])
    await _safe_edit(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@router.callback_query(F.data == "adm_clean_dead_presets")
async def cb_adm_clean_dead_presets(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    global _LAST_DEAD_PRESETS_CACHE
    if not _LAST_DEAD_PRESETS_CACHE:
        await call.answer("ℹ️ Tidak ada preset mati yang perlu dihapus.", show_alert=True)
        return
    removed = await remove_dead_presets_from_db(_LAST_DEAD_PRESETS_CACHE)
    _LAST_DEAD_PRESETS_CACHE.clear()
    await call.answer(f"✅ {removed} preset bug yang mati berhasil dibersihkan!", show_alert=True)
    await cb_adm_check_presets_health(call)

# === WELCOME BERGAMBAR & BROADCAST BERGAMBAR + BUTTON /START ===
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from .system_manager import (
    run_auto_heal_and_monitor, check_all_presets_health, remove_dead_presets_from_db,
    refresh_all_servers_live_stats
)

WELCOME_BANNER_PATH = "/root/tgbot-vpn/welcome_banner.jpg"

class WelcomeEditFSM(StatesGroup):
    waiting_text = State()

class BroadcastMediaFSM(StatesGroup):
    waiting_content = State()

_LAST_DEAD_PRESETS_CACHE: set[str] = set()

@router.callback_query(
    F.data.in_({"adm_welcome", "adm_set_welcome", "adm_edit_welcome", "set_welcome", "edit_welcome"}),
    StateFilter("*")
)
async def cb_adm_edit_welcome_unified(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    cur_wel = (await get_setting("welcome_text")) or (await get_setting("welcome_message")) or ""
    cur_photo = (await get_setting("welcome_photo_id") or "").strip()
    has_img = bool(cur_photo or (os.path.exists(WELCOME_BANNER_PATH) and os.path.getsize(WELCOME_BANNER_PATH) > 0))
    img_badge = "🟢 Aktif (Menggunakan Gambar Banner)" if has_img else "⚪ Tidak Ada Gambar (Hanya Teks)"

    await state.set_state(WelcomeEditFSM.waiting_text)
    await state.update_data(wel_prompt_mid=call.message.message_id)

    text = (
        "📝 <b>UBAH TEKS &amp; GAMBAR WELCOME (/start)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Silakan kirimkan salah satu dari berikut ini:\n"
        "1️⃣ <b>Kirim Gambar/Foto + Caption</b> (untuk memasang Banner Gambar sekaligus mengubah Teks Welcome).\n"
        "2️⃣ <b>Kirim Gambar/Foto Saja</b> (untuk memasang Banner Gambar tanpa mengubah teks saat ini).\n"
        "3️⃣ <b>Kirim Teks Saja</b> (untuk mengubah Teks Welcome saja).\n\n"
        "📌 <b>Placeholder Otomatis:</b> <code>{name}</code>, <code>{id}</code>, <code>{used}/{max}</code>\n"
        f"🖼 <b>Status Gambar Banner:</b> <code>{img_badge}</code>\n\n"
        "📋 <b>Teks Welcome Saat Ini:</b>\n"
        f"<code>{html.escape(cur_wel) if cur_wel else 'Selamat datang di Layanan Manajemen Akun VPN Otomatis.'}</code>"
    )
    btns = []
    if has_img:
        btns.append([InlineKeyboardButton(text="🖼 Hapus Gambar Banner (Gunakan Teks Saja)", callback_data="adm_del_welcome_img")])
    btns.append([InlineKeyboardButton(text="🔄 Reset Teks & Gambar ke Default", callback_data="adm_reset_welcome_default")])
    btns.append([InlineKeyboardButton(text="⬅️ Batal / Kembali ke Panel Admin", callback_data="admin_panel")])

    await _safe_edit(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()


@router.callback_query(F.data == "adm_del_welcome_img", StateFilter("*"))
async def cb_adm_del_welcome_img(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await set_setting("welcome_photo_id", "")
    try:
        if os.path.exists(WELCOME_BANNER_PATH):
            os.remove(WELCOME_BANNER_PATH)
    except Exception:
        pass
    await generate_portable_backup_sh()
    await call.answer("🖼 Gambar Banner Welcome berhasil dihapus!", show_alert=True)
    await cb_adm_edit_welcome_unified(call, state)

@router.callback_query(F.data == "adm_reset_welcome_default", StateFilter("*"))
async def cb_adm_reset_welcome_default(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    default_txt = "Selamat datang di <b>Layanan Manajemen Akun VPN Otomatis</b>."
    for k in ("welcome_text", "welcome_message", "welcome_msg"):
        await set_setting(k, default_txt)
    await set_setting("welcome_photo_id", "")
    try:
        if os.path.exists(WELCOME_BANNER_PATH):
            os.remove(WELCOME_BANNER_PATH)
    except Exception:
        pass
    await generate_portable_backup_sh()
    await state.clear()
    await call.answer("✅ Welcome berhasil di-reset ke default!", show_alert=True)
    await _safe_edit(
        call,
        "✅ <b>Teks &amp; Gambar Welcome telah dikembalikan ke Default!</b>\n\n"
        f"Preview:\n{default_txt}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Ubah Lagi", callback_data="adm_welcome")],
            [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
        ])
    )

@router.message(WelcomeEditFSM.waiting_text)
async def fsm_adm_save_welcome_unified(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id):
        await state.clear()
        return

    data_st = await state.get_data()
    prompt_mid = data_st.get("wel_prompt_mid")

    photo_updated = False
    text_updated = False
    new_wel = ""

    if message.photo:
        best_photo = message.photo[-1]
        await set_setting("welcome_photo_id", best_photo.file_id)
        try:
            await message.bot.download(best_photo, destination=WELCOME_BANNER_PATH)
        except Exception:
            pass
        photo_updated = True
        if message.caption or message.html_text:
            new_wel = (message.html_text or message.caption or "").strip()
            text_updated = bool(new_wel)
    else:
        new_wel = (message.html_text or message.text or "").strip()
        text_updated = bool(new_wel)

    try:
        await message.delete()
    except Exception:
        pass

    if not photo_updated and not text_updated:
        await message.answer("❌ Kirimkan teks atau gambar/foto untuk mengubah tampilan Welcome.")
        return

    if text_updated:
        for k in ("welcome_text", "welcome_message", "welcome_msg"):
            await set_setting(k, new_wel)

    await generate_portable_backup_sh()
    await state.clear()

    cur_wel_saved = (await get_setting("welcome_text")) or ""
    status_lines = []
    if photo_updated:
        status_lines.append("🖼 <b>Gambar Banner Welcome:</b> <code>Berhasil Dipasang!</code>")
    if text_updated:
        status_lines.append("📝 <b>Teks Sambutan Welcome:</b> <code>Berhasil Diperbarui!</code>")

    out_txt = (
        "✅ <b>PENGATURAN WELCOME BERHASIL DISIMPAN!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(status_lines) + "\n\n"
        "📋 <b>Teks Sambutan Aktif:</b>\n"
        f"{cur_wel_saved}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Ketik /start atau klik tombol di bawah untuk melihat hasilnya.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Lihat Hasil (/start)", callback_data="menu_main")],
        [InlineKeyboardButton(text="📝 Ubah Welcome Lagi", callback_data="adm_welcome")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    if prompt_mid:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_mid,
                text=out_txt,
                reply_markup=kb
            )
            return
        except Exception:
            pass
    await message.answer(out_txt, reply_markup=kb)


# === FITUR BROADCAST TEKS & GAMBAR + TOMBOL /START ===
@router.callback_query(F.data.in_({"adm_broadcast", "broadcast", "adm_bc"}), StateFilter("*"))
async def cb_adm_broadcast_media_start(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    await state.set_state(BroadcastMediaFSM.waiting_content)
    text = (
        "📢 <b>BROADCAST PENGUMUMAN (TEKS / GAMBAR)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Kirimkan pesan pengumuman yang ingin disebarkan ke seluruh pengguna bot:\n\n"
        "• 🖼 <b>Gambar + Caption:</b> Kirim foto beserta teks pengumuman di caption.\n"
        "• 📝 <b>Teks Biasa:</b> Ketik langsung isi pengumuman Anda.\n\n"
        "✨ <i>Otomatis menyertakan tombol <b>🚀 Buka Menu Utama (/start)</b> di bagian bawah pesan broadcast.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Batal / Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()


@router.message(BroadcastMediaFSM.waiting_content)
async def fsm_adm_broadcast_media_send(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id):
        await state.clear()
        return

    photo_id = message.photo[-1].file_id if message.photo else ""
    body_html = (message.html_text or message.caption or message.text or "").strip()

    if not photo_id and not body_html:
        await message.answer("❌ Kirimkan teks atau foto yang ingin di-broadcast.")
        return

    await state.clear()
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id FROM users WHERE is_banned = 0") as cur:
            user_ids = [r[0] for r in await cur.fetchall()]

    status_msg = await message.answer(f"⏳ <i>Mengirim broadcast ke {len(user_ids)} pengguna...</i>")

    bc_header = "📢 <b>PENGUMUMAN ADMINISTRATOR</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    full_caption = f"{bc_header}{body_html}" if body_html else "📢 <b>PENGUMUMAN ADMINISTRATOR</b>"
    kb_start_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Buka Menu Utama (/start)", callback_data="menu_main")]
    ])

    ok_cnt, fail_cnt = 0, 0
    for target_uid in user_ids:
        try:
            if photo_id:
                await message.bot.send_photo(
                    chat_id=target_uid,
                    photo=photo_id,
                    caption=full_caption,
                    reply_markup=kb_start_btn
                )
            else:
                await message.bot.send_message(
                    chat_id=target_uid,
                    text=full_caption,
                    reply_markup=kb_start_btn
                )
            ok_cnt += 1
        except Exception:
            fail_cnt += 1
        await asyncio.sleep(0.04)

    await status_msg.edit_text(
        "✅ <b>BROADCAST SELESAI DIKIRIM!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Tipe Broadcast:</b> <code>{'🖼 Gambar + Caption' if photo_id else '📝 Teks'}</code>\n"
        f"• <b>Berhasil Terkirim:</b> <code>{ok_cnt} User</code>\n"
        f"• <b>Gagal / Blokir Bot:</b> <code>{fail_cnt} User</code>\n"
        "• <b>Tombol Bawah:</b> <code>🚀 Buka Menu Utama (/start)</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
        ])
    )

from .menu_tracker import track_bot_msg


for _h in list(router.callback_query.handlers):
    if getattr(_h.callback, "__name__", "") in ("cb_adm_edit_welcome_unified", "cb_adm_broadcast_media_start"):
        router.callback_query.handlers.remove(_h)
        router.callback_query.handlers.insert(0, _h)

for _h in list(router.message.handlers):
    if getattr(_h.callback, "__name__", "") in ("fsm_adm_save_welcome_unified", "fsm_adm_broadcast_media_send"):
        router.message.handlers.remove(_h)
        router.message.handlers.insert(0, _h)

from .menu_tracker import track_bot_msg

async def _safe_edit(call: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    if not call.message:
        return None
    chat_id = call.message.chat.id
    try:
        if call.message.photo:
            sent = await call.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            track_bot_msg(chat_id, sent.message_id)
            try:
                await call.message.delete()
            except Exception:
                pass
            return sent
        res = await call.message.edit_text(text, reply_markup=reply_markup)
        track_bot_msg(chat_id, call.message.message_id)
        return res
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return None
        try:
            sent = await call.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            track_bot_msg(chat_id, sent.message_id)
            try:
                await call.message.delete()
            except Exception:
                pass
            return sent
        except Exception:
            return None

# === OVERRIDE HANDLER BATAS BUAT AKUN GLOBAL & PER-USER (SUPPORT 0 = UNLIMITED) ===
class QuotaLimitEditFSM(StatesGroup):
    waiting_global_limit = State()
    waiting_user_quota = State()

@router.callback_query(
    F.data.in_({"adm_set_daily_limit", "set_daily_limit", "adm_daily_limit", "edit_daily_limit", "adm_limit_create"}),
    StateFilter("*")
)
async def cb_adm_set_global_create_limit(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    cur_lim = await get_setting("daily_create_limit")
    if cur_lim is None or str(cur_lim).strip() == "":
        cur_lim = "2"
    cur_win = await get_setting("create_limit_window") or "1d"
    lim_disp = "♾ Unlimited (0)" if str(cur_lim).strip() in ("0", "unlimited") else f"{cur_lim} Akun"

    await state.set_state(QuotaLimitEditFSM.waiting_global_limit)
    await state.update_data(prompt_mid=call.message.message_id)

    text = (
        "📊 <b>ATUR BATAS BUAT AKUN GLOBAL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Batas Saat Ini:</b> <code>{html.escape(lim_disp)}</code>\n"
        f"• <b>Periode Reset:</b> <code>{html.escape(str(cur_win))}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Ketik jumlah batas pembuatan akun global yang baru:\n"
        "• Ketik <b><code>0</code></b> (atau klik tombol di bawah) untuk <b>♾ Unlimited (Tanpa Batas)</b>.\n"
        "• Ketik angka positif (contoh: <code>1</code>, <code>2</code>, <code>5</code>) untuk membatasi jumlah akun.\n"
        "• Atau ketik format gabungan angka &amp; periode (contoh: <code>1/4d</code> atau <code>2/7d</code>)."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♾ Set Global Unlimited (0)", callback_data="adm_quick_glim:0")],
        [
            InlineKeyboardButton(text="1 Akun", callback_data="adm_quick_glim:1"),
            InlineKeyboardButton(text="2 Akun", callback_data="adm_quick_glim:2"),
            InlineKeyboardButton(text="5 Akun", callback_data="adm_quick_glim:5"),
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_quick_glim:"), StateFilter("*"))
async def cb_adm_quick_glim(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    val = call.data.split(":", 1)[1].strip()
    await set_setting("daily_create_limit", val)
    await generate_portable_backup_sh()
    await state.clear()
    lbl = "♾ Unlimited (Tanpa Batas)" if val == "0" else f"{val} Akun"
    await call.answer(f"✅ Batas buat akun global diatur ke: {lbl}", show_alert=True)
    await _safe_edit(
        call,
        "✅ <b>BATAS BUAT AKUN GLOBAL DIPERBARUI!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Batas Baru:</b> <code>{lbl}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
        ])
    )

@router.message(QuotaLimitEditFSM.waiting_global_limit)
async def fsm_adm_save_global_limit(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip().lower()
    data_st = await state.get_data()
    prompt_mid = data_st.get("prompt_mid")
    try:
        await message.delete()
    except Exception:
        pass

    new_win = None
    if "/" in raw:
        p1, p2 = raw.split("/", 1)
        raw = p1.strip()
        new_win = p2.strip()

    if raw in ("0", "unlimited", "inf", "bebas", "tanpa batas"):
        val_save = "0"
    elif raw.lstrip("-").isdigit():
        num = int(raw)
        val_save = "0" if num <= 0 else str(num)
    else:
        await message.answer("❌ Masukkan angka yang valid (contoh: <code>0</code> untuk Unlimited, atau <code>1</code>, <code>2</code>).")
        return

    await set_setting("daily_create_limit", val_save)
    if new_win:
        await set_setting("create_limit_window", new_win)
    await generate_portable_backup_sh()
    await state.clear()

    cur_win = await get_setting("create_limit_window") or "1d"
    lbl = "♾ Unlimited (Tanpa Batas)" if val_save == "0" else f"{val_save} Akun (Periode: {cur_win})"
    out_txt = (
        "✅ <b>BATAS BUAT AKUN GLOBAL BERHASIL DISIMPAN!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Batas Buat Akun:</b> <code>{html.escape(lbl)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    if prompt_mid:
        try:
            await message.bot.edit_message_text(chat_id=message.chat.id, message_id=prompt_mid, text=out_txt, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(out_txt, reply_markup=kb)

@router.callback_query(
    F.data.startswith(("adm_u_quota:", "adm_user_quota:", "set_u_quota:", "u_quota:", "edit_u_quota:")),
    StateFilter("*")
)
async def cb_adm_edit_user_quota(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    target_uid = int(call.data.split(":", 1)[1])
    await state.clear()
    await state.set_state(QuotaLimitEditFSM.waiting_user_quota)
    await state.update_data(target_uid=target_uid, prompt_mid=call.message.message_id)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT full_name, username, custom_quota FROM users WHERE tg_id = ?", (target_uid,)) as cur:
            u_row = await cur.fetchone()

    fname = u_row[0] if u_row else f"User {target_uid}"
    cq = int(u_row[2]) if (u_row and u_row[2] is not None) else -1
    if cq == 0:
        cq_disp = "♾ Unlimited (0)"
    elif cq > 0:
        cq_disp = f"{cq} Akun"
    else:
        cq_disp = "Ikut Aturan Global (-1)"

    text = (
        "📊 <b>ATUR KUOTA BUAT AKUN USER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>User:</b> <code>{html.escape(fname)}</code> (<code>{target_uid}</code>)\n"
        f"• <b>Kuota Saat Ini:</b> <code>{cq_disp}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Ketik angka batas kuota pembuatan akun untuk user ini:\n"
        "• Ketik <b><code>0</code></b> = <b>♾ Unlimited (Bebas Buat Akun)</b>\n"
        "• Ketik <b><code>-1</code></b> = <b>🔄 Ikut Aturan Global</b>\n"
        "• Ketik angka <b><code>&gt; 0</code></b> (misal <code>3</code>) = Batas khusus user ini."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="♾ Set Unlimited (0)", callback_data=f"adm_quick_uq:{target_uid}:0"),
            InlineKeyboardButton(text="🔄 Ikut Global (-1)", callback_data=f"adm_quick_uq:{target_uid}:-1")
        ],
        [
            InlineKeyboardButton(text="1 Akun", callback_data=f"adm_quick_uq:{target_uid}:1"),
            InlineKeyboardButton(text="3 Akun", callback_data=f"adm_quick_uq:{target_uid}:3"),
            InlineKeyboardButton(text="5 Akun", callback_data=f"adm_quick_uq:{target_uid}:5"),
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_quick_uq:"), StateFilter("*"))
async def cb_adm_quick_uq(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    _, uid_s, val_s = call.data.split(":", 2)
    target_uid = int(uid_s)
    cq_val = int(val_s)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE users SET custom_quota = ? WHERE tg_id = ?", (cq_val, target_uid))
        await db.commit()
    await generate_portable_backup_sh()
    await state.clear()

    if cq_val == 0:
        lbl = "♾ Unlimited (Bebas Buat Akun)"
    elif cq_val < 0:
        lbl = "🔄 Ikut Aturan Global"
    else:
        lbl = f"{cq_val} Akun"

    await call.answer(f"✅ Kuota user {target_uid}: {lbl}", show_alert=True)
    await _safe_edit(
        call,
        "✅ <b>KUOTA USER BERHASIL DIPERBARUI!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ID User:</b> <code>{target_uid}</code>\n"
        f"• <b>Batas Buat Akun:</b> <code>{lbl}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
        ])
    )

@router.message(QuotaLimitEditFSM.waiting_user_quota)
async def fsm_adm_save_user_quota(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id):
        await state.clear()
        return
    data_st = await state.get_data()
    target_uid = int(data_st.get("target_uid", 0))
    prompt_mid = data_st.get("prompt_mid")
    raw = (message.text or "").strip().lower()
    try:
        await message.delete()
    except Exception:
        pass

    if raw in ("0", "unlimited", "inf", "bebas"):
        cq_val = 0
    elif raw in ("-1", "default", "global", "reset"):
        cq_val = -1
    elif raw.lstrip("-").isdigit():
        cq_val = int(raw)
        if cq_val < -1:
            cq_val = -1
    else:
        await message.answer("❌ Masukkan <code>0</code> untuk Unlimited, <code>-1</code> untuk Ikut Global, atau angka positif.")
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE users SET custom_quota = ? WHERE tg_id = ?", (cq_val, target_uid))
        await db.commit()
    await generate_portable_backup_sh()
    await state.clear()

    if cq_val == 0:
        lbl = "♾ Unlimited (Bebas Buat Akun)"
    elif cq_val < 0:
        lbl = "🔄 Ikut Aturan Global"
    else:
        lbl = f"{cq_val} Akun"

    out_txt = (
        "✅ <b>KUOTA USER BERHASIL DISIMPAN!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ID User:</b> <code>{target_uid}</code>\n"
        f"• <b>Batas Buat Akun:</b> <code>{lbl}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    if prompt_mid:
        try:
            await message.bot.edit_message_text(chat_id=message.chat.id, message_id=prompt_mid, text=out_txt, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(out_txt, reply_markup=kb)

for _h in list(router.callback_query.handlers):
    if getattr(_h.callback, "__name__", "") in (
        "cb_adm_set_global_create_limit", "cb_adm_quick_glim",
        "cb_adm_edit_user_quota", "cb_adm_quick_uq",
        "cb_adm_edit_welcome_unified", "cb_adm_broadcast_media_start"
    ):
        router.callback_query.handlers.remove(_h)
        router.callback_query.handlers.insert(0, _h)

for _h in list(router.message.handlers):
    if getattr(_h.callback, "__name__", "") in (
        "fsm_adm_save_global_limit", "fsm_adm_save_user_quota",
        "fsm_adm_save_welcome_unified", "fsm_adm_broadcast_media_send"
    ):
        router.message.handlers.remove(_h)
        router.message.handlers.insert(0, _h)

async def _render_adm_feat_toggles() -> tuple[str, InlineKeyboardMarkup]:
    f_self = (await get_setting("feat_self_manage")) != "0"
    f_bug = (await get_setting("feat_bug_checker")) != "0"
    f_bulk = (await get_setting("feat_bug_bulk")) != "0"
    f_heal = (await get_setting("feat_auto_heal")) != "0"
    f_sub = (await get_setting("feat_smart_sub")) != "0"
    f_lb = (await get_setting("feat_smart_lb")) != "0"
    cd_sec = await get_setting("bug_scan_cooldown") or "20"

    text = (
        "🎛 <b>KONTROL ON / OFF FITUR BOT KOMUNITAS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"1️⃣ <b>Fitur Mandiri User (Pindah/Tukar/Reset):</b> <code>{'🟢 ON' if f_self else '🔴 OFF'}</code>\n"
        f"2️⃣ <b>Pro Bug &amp; Payload Scanner (User):</b> <code>{'🟢 ON' if f_bug else '🔴 OFF'}</code>\n"
        f"3️⃣ <b>Izin Bulk Scan Multi-Bug (User):</b> <code>{'🟢 ON' if f_bulk else '🔴 OFF'}</code> (CD: <code>{cd_sec}s</code>)\n"
        f"4️⃣ <b>Auto-Heal Service &amp; Notif Server:</b> <code>{'🟢 ON' if f_heal else '🔴 OFF'}</code>\n"
        f"5️⃣ <b>🔗 Smart Subscription Link (Auto-Update):</b> <code>{'🟢 ON' if f_sub else '🔴 OFF'}</code>\n"
        f"6️⃣ <b>⚖️ Smart Load-Balancer &amp; Rekomendasi:</b> <code>{'🟢 ON' if f_lb else '🔴 OFF'}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Klik tombol di bawah untuk mengaktifkan (ON) atau menonaktifkan (OFF) masing-masing fitur secara langsung.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🟢 ON' if f_self else '🔴 OFF'} — 1. Fitur Mandiri (Pindah/Tukar/Reset)",
            callback_data="adm_toggle_feat:feat_self_manage"
        )],
        [InlineKeyboardButton(
            text=f"{'🟢 ON' if f_bug else '🔴 OFF'} — 2. Pro Bug & Payload Scanner",
            callback_data="adm_toggle_feat:feat_bug_checker"
        )],
        [InlineKeyboardButton(
            text=f"{'🟢 ON' if f_bulk else '🔴 OFF'} — 3. Izin Bulk Scan Multi-Bug",
            callback_data="adm_toggle_feat:feat_bug_bulk"
        )],
        [InlineKeyboardButton(
            text=f"{'🟢 ON' if f_heal else '🔴 OFF'} — 4. Auto-Heal & Notif Status Server",
            callback_data="adm_toggle_feat:feat_auto_heal"
        )],
        [InlineKeyboardButton(
            text=f"{'🟢 ON' if f_sub else '🔴 OFF'} — 5. Smart Subscription Link",
            callback_data="adm_toggle_feat:feat_smart_sub"
        )],
        [InlineKeyboardButton(
            text=f"{'🟢 ON' if f_lb else '🔴 OFF'} — 6. Smart Load-Balancer Server",
            callback_data="adm_toggle_feat:feat_smart_lb"
        )],
        [InlineKeyboardButton(text="🎨 Ganti Tema Tampilan Bot (Tree-Box / Card)", callback_data="adm_ui_theme")],
        [InlineKeyboardButton(text="🩺 Cek Kesehatan Semua Preset Bug", callback_data="adm_check_presets_health")],
        [InlineKeyboardButton(text="🩺 Cek & Auto-Heal Semua Server Sekarang", callback_data="adm_run_heal_now")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    return text, kb

@router.callback_query(F.data.startswith("adm_toggle_feat:"))
async def cb_adm_toggle_feat(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    key = call.data.split(":", 1)[1]
    allowed_keys = {
        "feat_self_manage": "Fitur Mandiri (Pindah Server/Protokol/Reset)",
        "feat_bug_checker": "Pro Bug & Payload Scanner",
        "feat_bug_bulk": "Bulk Scan Multi-Bug",
        "feat_auto_heal": "Auto-Heal Service & Notif Status Server",
        "feat_smart_sub": "Smart Subscription Link (Auto-Update)",
        "feat_smart_lb": "Smart Load-Balancer & Rekomendasi Server",
    }
    if key not in allowed_keys:
        return
    cur = await get_setting(key)
    new_val = "0" if cur != "0" else "1"
    await set_setting(key, new_val)
    await generate_portable_backup_sh()

    st_lbl = "🟢 DIAKTIFKAN (ON)" if new_val == "1" else "🔴 DINONAKTIFKAN (OFF)"
    await call.answer(f"{allowed_keys[key]}: {st_lbl}", show_alert=True)
    text, kb = await _render_adm_feat_toggles()
    await _safe_edit(call, text, reply_markup=kb)

# === HANDLER BACKUP & RESTORE DATA VPN (SUPPORT .SH & .DB, INSTANT UPLOAD & AUTO-BACKUP) ===
from aiogram.types import FSInputFile, BufferedInputFile
from .system_manager import (
    generate_portable_backup_sh, restore_from_backup_file,
    sync_all_accounts_after_restore, _create_clean_sqlite_snapshot_bytes
)

class RestoreBackupFSM(StatesGroup):
    waiting_file = State()

async def _render_adm_backup_menu() -> tuple[str, InlineKeyboardMarkup]:
    auto_tg = (await get_setting("auto_backup_tg")) == "1"
    st_auto = "🟢 AKTIF (Tiap 6 Jam)" if auto_tg else "🔴 NONAKTIF"

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        u_cnt = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        ssh_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type = 'ssh'")).fetchone())[0]
        xray_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type != 'ssh'")).fetchone())[0]
        srv_cnt = (await (await db.execute("SELECT COUNT(*) FROM servers")).fetchone())[0]

    wib_str = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")
    text = (
        "💾 <b>MANAJEMEN BACKUP &amp; RESTORE DATA VPN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Waktu Server:</b> <code>{wib_str}</code>\n"
        f"👥 <b>Total Pengguna:</b> <code>{u_cnt} User</code>\n"
        f"🔑 <b>Total Akun Aktif:</b> <code>{ssh_cnt} SSH | {xray_cnt} Xray</code>\n"
        f"🖥 <b>Total Server/Node:</b> <code>{srv_cnt} Server</code>\n"
        f"⏰ <b>Auto-Backup Telegram:</b> <code>{st_auto}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>Panduan Backup &amp; Restore:</b>\n"
        "• Klik <b>📥 Download Backup Sekarang</b> untuk mengunduh file <code>backup-data-vpn.sh</code> &amp; <code>vpn_bot.db</code> terbaru.\n"
        "• Klik <b>📤 Restore dari File Backup</b> (atau langsung kirimkan file <code>backup-data-vpn.sh</code> / <code>vpn_bot.db</code> ke chat ini) untuk memulihkan seluruh data &amp; akun."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Download Backup Sekarang (.sh & .db)", callback_data="adm_dl_backup_now")],
        [InlineKeyboardButton(text="📤 Restore dari File Backup (Upload .sh / .db)", callback_data="adm_restore_start")],
        [InlineKeyboardButton(text="🔄 Sinkronkan Ulang Semua Akun ke VPS/Xray", callback_data="adm_resync_all_accounts")],
        [InlineKeyboardButton(
            text=f"⏰ Auto-Backup Telegram (6 Jam): {'🟢 ON' if auto_tg else '🔴 OFF'}",
            callback_data="adm_toggle_autobackup"
        )],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    return text, kb

@router.callback_query(
    F.data.in_({
        "adm_backup", "adm_backup_menu", "adm_backup_restore",
        "backup_menu", "adm_db_backup", "backup_restore"
    }),
    StateFilter("*")
)
async def cb_adm_backup_menu_unified(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    text, kb = await _render_adm_backup_menu()
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(
    F.data.in_({
        "adm_dl_backup_now", "adm_backup_now", "adm_dl_backup",
        "adm_send_backup", "backup_now", "adm_backup_sh"
    }),
    StateFilter("*")
)
async def cb_adm_download_backup_now(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    await call.answer("⏳ Membuat snapshot database & file backup portabel...")
    try:
        sh_path = await generate_portable_backup_sh()
        db_snap_bytes = await asyncio.to_thread(_create_clean_sqlite_snapshot_bytes)

        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            u_cnt = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
            ssh_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type = 'ssh'")).fetchone())[0]
            xray_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type != 'ssh'")).fetchone())[0]

        wib_str = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")
        cap_sh = (
            "📦 <b>FILE BACKUP PORTABEL (backup-data-vpn.sh)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 <b>Waktu:</b> <code>{wib_str}</code>\n"
            f"👥 <b>Total User:</b> <code>{u_cnt} User</code>\n"
            f"🔑 <b>Total Akun:</b> <code>{ssh_cnt} SSH | {xray_cnt} Xray</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <b>Cara Restore:</b>\n"
            "• <b>Via Bot:</b> Kirim/upload kembali file ini ke menu <b>📤 Restore dari File Backup</b>.\n"
            "• <b>Via Terminal VPS:</b> Jalankan <code>bash /root/backup-data-vpn.sh</code>"
        )
        await call.message.answer_document(
            document=FSInputFile(sh_path, filename="backup-data-vpn.sh"),
            caption=cap_sh
        )
        await call.message.answer_document(
            document=BufferedInputFile(db_snap_bytes, filename="vpn_bot.db"),
            caption=f"🗄 <b>Database SQLite Murni (vpn_bot.db)</b> — <code>{wib_str}</code>"
        )
    except Exception as e:
        await call.message.answer(f"❌ <b>Gagal membuat backup:</b> <code>{html.escape(str(e))}</code>")

@router.callback_query(F.data == "adm_toggle_autobackup", StateFilter("*"))
async def cb_adm_toggle_autobackup_unified(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    cur = await get_setting("auto_backup_tg")
    new_val = "0" if cur == "1" else "1"
    await set_setting("auto_backup_tg", new_val)
    await generate_portable_backup_sh()
    lbl = "🟢 DIAKTIFKAN (Tiap 6 Jam)" if new_val == "1" else "🔴 DINONAKTIFKAN"
    await call.answer(f"⏰ Auto-Backup Telegram: {lbl}", show_alert=True)
    text, kb = await _render_adm_backup_menu()
    await _safe_edit(call, text, reply_markup=kb)

@router.callback_query(F.data == "adm_resync_all_accounts", StateFilter("*"))
async def cb_adm_resync_all_accounts(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    await call.answer("🔄 Menyinkronkan seluruh akun SSH & Xray ke server...")
    res = await sync_all_accounts_after_restore()
    await generate_portable_backup_sh()
    text, kb = await _render_adm_backup_menu()
    await _safe_edit(
        call,
        f"{text}\n\n✅ <b>Sinkronisasi Selesai:</b> Seluruh akun SSH (<code>{res.get('ssh_synced', 0)}</code>) &amp; Xray telah disinkronkan ke VPS!",
        reply_markup=kb
    )

@router.callback_query(
    F.data.in_({
        "adm_restore", "adm_restore_start", "adm_restore_db",
        "restore_start", "adm_upload_backup"
    }),
    StateFilter("*")
)
async def cb_adm_restore_start_unified(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    await state.set_state(RestoreBackupFSM.waiting_file)
    await state.update_data(restore_prompt_mid=call.message.message_id)

    text = (
        "📤 <b>RESTORE DATA DARI FILE BACKUP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Silakan kirimkan atau <i>forward</i> file backup Anda ke ruang chat ini sekarang:\n\n"
        "✅ <b>Format yang Didukung:</b>\n"
        "1️⃣ File <b><code>backup-data-vpn.sh</code></b> (Portabel Script Backup)\n"
        "2️⃣ File <b><code>vpn_bot.db</code></b> (Database SQLite Murni)\n\n"
        "⚡ <i>Bot akan otomatis memvalidasi database, memulihkan seluruh User, Pengaturan, Server Node, serta membuat ulang semua akun SSH &amp; Xray secara instan.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Batal / Kembali ke Menu Backup", callback_data="adm_backup_menu")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

async def _process_admin_restore_document(message: Message, state: FSMContext):
    doc = message.document
    if not doc:
        await message.answer("❌ Harap kirimkan <b>File Dokumen</b> (<code>backup-data-vpn.sh</code> atau <code>vpn_bot.db</code>).")
        return

    fname = (doc.file_name or "backup.dat").lower()
    if not fname.endswith((".sh", ".db", ".sqlite", ".sqlite3", ".bak")):
        await message.answer("❌ Format nama file tidak didukung. Kirimkan file <code>backup-data-vpn.sh</code> atau <code>vpn_bot.db</code>.")
        return

    await state.clear()
    wait_msg = await message.answer(f"⏳ <i>Mengunduh &amp; memulihkan data dari</i> <code>{html.escape(doc.file_name or 'backup')}</code>...")

    tmp_in = f"/tmp/uploaded_restore_{int(time.time())}_{os.path.basename(fname)}"
    try:
        await message.bot.download(doc, destination=tmp_in)
        res = await restore_from_backup_file(tmp_in)
    except Exception as e:
        res = {"ok": False, "error": str(e)}
    finally:
        if os.path.exists(tmp_in):
            try:
                os.remove(tmp_in)
            except Exception:
                pass

    if not res.get("ok"):
        kb_err = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Coba Upload Lagi", callback_data="adm_restore_start")],
            [InlineKeyboardButton(text="⬅️ Kembali ke Menu Backup", callback_data="adm_backup_menu")]
        ])
        await wait_msg.edit_text(
            f"❌ <b>GAGAL MELAKUKAN RESTORE:</b>\n<code>{html.escape(str(res.get('error')))}</code>",
            reply_markup=kb_err
        )
        return

    kb_ok = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Menu Backup & Restore", callback_data="adm_backup_menu")],
        [InlineKeyboardButton(text="👑 Panel Administrator", callback_data="admin_panel")],
        [InlineKeyboardButton(text="🚀 Buka Menu Utama (/start)", callback_data="menu_main")]
    ])
    await wait_msg.edit_text(
        "✅ <b>RESTORE DATA &amp; SINKRONISASI AKUN BERHASIL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>File Sumber:</b> <code>{html.escape(doc.file_name or 'backup')}</code>\n"
        f"• <b>Total Pengguna:</b> <code>{res.get('users', 0)} User</code>\n"
        f"• <b>Akun SSH Aktif:</b> <code>{res.get('ssh', 0)} Akun (Disinkronkan ke OS)</code>\n"
        f"• <b>Akun Xray Aktif:</b> <code>{res.get('xray', 0)} Akun (Disinkronkan ke Xray)</code>\n"
        f"• <b>Server Terdaftar:</b> <code>{res.get('servers', 1)} Server</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎉 <i>Seluruh data pengguna, pengaturan bot, serta akun SSH &amp; Xray telah aktif kembali!</i>",
        reply_markup=kb_ok
    )

@router.message(RestoreBackupFSM.waiting_file)
async def fsm_adm_restore_file_upload(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id):
        await state.clear()
        return
    await _process_admin_restore_document(message, state)

@router.message(
    F.document,
    F.func(lambda m: bool(
        m.document and m.document.file_name and (
            m.document.file_name.lower().endswith((".db", ".sqlite", ".sqlite3"))
            or ("backup" in m.document.file_name.lower() and m.document.file_name.lower().endswith(".sh"))
        )
    )),
    StateFilter("*")
)
async def msg_adm_direct_backup_file_drop(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id):
        return
    await _process_admin_restore_document(message, state)

for _h in list(router.callback_query.handlers):
    if getattr(_h.callback, "__name__", "") in (
        "cb_adm_backup_menu_unified", "cb_adm_download_backup_now",
        "cb_adm_toggle_autobackup_unified", "cb_adm_resync_all_accounts",
        "cb_adm_restore_start_unified", "cb_adm_set_global_create_limit",
        "cb_adm_quick_glim", "cb_adm_edit_user_quota", "cb_adm_quick_uq",
        "cb_adm_edit_welcome_unified", "cb_adm_broadcast_media_start"
    ):
        router.callback_query.handlers.remove(_h)
        router.callback_query.handlers.insert(0, _h)

for _h in list(router.message.handlers):
    if getattr(_h.callback, "__name__", "") in (
        "fsm_adm_restore_file_upload", "msg_adm_direct_backup_file_drop",
        "fsm_adm_save_global_limit", "fsm_adm_save_user_quota",
        "fsm_adm_save_welcome_unified", "fsm_adm_broadcast_media_send"
    ):
        router.message.handlers.remove(_h)
        router.message.handlers.insert(0, _h)

# === MENU GANTI TEMA / SKIN TAMPILAN BOT 1-KLIK (PANEL ADMIN) ===
from .ui_theme import get_ui_theme_sync, set_ui_theme_cache, THEME_LABELS, make_capacity_bar

async def _render_adm_ui_theme_menu() -> tuple[str, InlineKeyboardMarkup]:
    cur_theme = (await get_setting("ui_theme") or "tree").strip().lower()
    if cur_theme not in THEME_LABELS:
        cur_theme = "tree"
    set_ui_theme_cache(cur_theme)

    cur_label = THEME_LABELS.get(cur_theme, THEME_LABELS["tree"])
    sample_bar = make_capacity_bar(40, 45, width=10, theme=cur_theme)

    if cur_theme == "tree":
        preview_box = (
            "╭─── 🖥 <b>SERVER UTAMA</b> (🟢 Online)\n"
            "├─ 🌐 <b>Domain   :</b> <code>idhome.suneku.web.id</code>\n"
            f"├─ 👥 <b>Kapasitas:</b> <code>{sample_bar}</code> (<code>40/45</code>)\n"
            "╰─ ⚡ <b>Status   :</b> 🟢 <code>2 Online</code> | 📦 <code>2.03 GB</code>"
        )
    elif cur_theme == "compact":
        preview_box = (
            "🖥 <b>Server Utama</b> (🟢 Online) — 🟢 <code>2 Online</code>\n"
            f"<blockquote expandable>🌐 <b>Domain:</b> <code>idhome.suneku.web.id</code>\n"
            f"👥 <b>Kapasitas:</b> <code>{sample_bar}</code> (<code>40/45</code>)\n"
            "📦 <b>Kuota User:</b> <code>2.03 GB</code></blockquote>"
        )
    else:
        preview_box = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🖥 <b>Server Utama</b> (🟢 Online)\n"
            "🌐 <b>Domain:</b> <code>idhome.suneku.web.id</code>\n"
            f"👥 <b>Kapasitas:</b> <code>{sample_bar}</code> (<code>40/45 Akun</code>)\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )

    text = (
        "🎨 <b>PENGATURAN TEMA &amp; GAYA TAMPILAN BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Tema Aktif Saat Ini:</b> <code>{cur_label}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <b>Preview Tampilan Aktif:</b>\n\n"
        f"{preview_box}\n\n"
        "💡 <i>Pilih salah satu gaya tampilan di bawah ini untuk langsung mengubah desain seluruh menu bot secara real-time:</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'✅ ' if cur_theme == 'tree' else ''}🌲 Tema 1: Cyber Tree-Box & Progress Bar",
            callback_data="adm_set_theme:tree"
        )],
        [InlineKeyboardButton(
            text=f"{'✅ ' if cur_theme == 'compact' else ''}🔽 Tema 2: Minimalist Expandable Card",
            callback_data="adm_set_theme:compact"
        )],
        [InlineKeyboardButton(
            text=f"{'✅ ' if cur_theme == 'classic' else ''}📋 Tema 3: Classic Clean",
            callback_data="adm_set_theme:classic"
        )],
        [
            InlineKeyboardButton(text="📊 Lihat Info Server", callback_data="server_info"),
            InlineKeyboardButton(text="🚀 Lihat /start", callback_data="menu_main")
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Panel Admin", callback_data="admin_panel")]
    ])
    return text, kb

@router.callback_query(F.data == "adm_ui_theme", StateFilter("*"))
async def cb_adm_ui_theme(call: CallbackQuery, state: FSMContext):
    if not await _guard_admin(call):
        return
    await state.clear()
    text, kb = await _render_adm_ui_theme_menu()
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_set_theme:"), StateFilter("*"))
async def cb_adm_set_theme(call: CallbackQuery):
    if not await _guard_admin(call):
        return
    new_theme = call.data.split(":", 1)[1].strip().lower()
    if new_theme not in THEME_LABELS:
        new_theme = "tree"

    await set_setting("ui_theme", new_theme)
    set_ui_theme_cache(new_theme)
    await generate_portable_backup_sh()

    lbl = THEME_LABELS[new_theme]
    await call.answer(f"🎨 Tema diubah ke: {lbl}", show_alert=True)
    text, kb = await _render_adm_ui_theme_menu()
    await _safe_edit(call, text, reply_markup=kb)

for _h in list(router.callback_query.handlers):
    if getattr(_h.callback, "__name__", "") in (
        "cb_adm_ui_theme", "cb_adm_set_theme",
        "cb_adm_backup_menu_unified", "cb_adm_download_backup_now",
        "cb_adm_restore_start_unified"
    ):
        router.callback_query.handlers.remove(_h)
        router.callback_query.handlers.insert(0, _h)