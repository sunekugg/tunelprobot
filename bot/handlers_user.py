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
import random
import re
import string
import uuid
import aiosqlite
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from .config import DB_PATH, DOMAIN, RESERVED_USERNAMES, now_wib, apply_prefix, USERNAME_PREFIX
from .db import (
    ensure_user, is_user_admin, get_setting, get_effective_limit_ip,
    check_daily_creation_quota, record_account_creation, get_effective_duration, add_log,
    get_servers_with_usage, get_server_by_id
)
from .keyboards import main_menu_kb, cancel_kb, server_picker_kb
from .force_join import check_user_membership, invalidate_join_cache
from .formatter import (
    format_trojan_output, format_ssh_output,
    build_vless_messages, build_vmess_messages, send_multi_part_output, build_injected_xray_html
)
from .system_manager import (
    get_isp_info, parse_duration, format_expired_display, system_user_exists,
    create_ssh_on_server, delete_ssh_on_server, sync_xray_by_server, get_server_stats,
    format_bytes_human, generate_portable_backup_sh, send_account_topic_notification
)
from .states import CreateXrayFSM, CreateSSHFSM, BugInjectFSM

router = Router()


async def _build_user_header(tg_id: int) -> str:
    welcome = await get_setting("welcome_text")
    _, used, max_lim, reset_str = await check_daily_creation_quota(tg_id)
    lim_ip = await get_effective_limit_ip(tg_id)
    lim_str = "Unlimited" if lim_ip == 0 else f"{lim_ip} IP"
    dur_str = await get_effective_duration(tg_id)
    wib_now = now_wib().strftime("%Y-%m-%d %H:%M WIB")
    return (
        f"{welcome}\n\n🕒 <b>Waktu Server:</b> <code>{wib_now}</code>"
        f"\n📊 <b>Batas Buat Akun:</b> <code>{used}/{max_lim}</code> (Reset: <code>{reset_str}</code>)"
        f"\n⏳ <b>Masa Aktif Akun:</b> <code>{dur_str}</code> | <b>Limit IP:</b> <code>{lim_str}</code>"
    )



@router.callback_query(F.data == "copy_hint")
async def cb_copy_hint(call: CallbackQuery):
    await call.answer("📋 Ketuk langsung pada teks di dalam kotak kode di atas untuk menyalin.", show_alert=True)

@router.callback_query(F.data.startswith("cancel_fsm:"))
async def cb_cancel_fsm(call: CallbackQuery, state: FSMContext):
    await state.clear()
    back_target = call.data.split(":", 1)[1]
    if back_target == "admin_panel":
        from .keyboards import admin_menu_kb
        bot_on = (await get_setting("bot_enabled")) == "1"
        await _safe_edit(call, "👑 <b>Panel Administrator</b>", reply_markup=admin_menu_kb(bot_on))
    else:
        await cb_menu_main(call, state)
    await call.answer("Dibatalkan.")

@router.callback_query(F.data == "verify_force_join")
async def cb_verify_force_join(call: CallbackQuery, state: FSMContext):
    invalidate_join_cache(call.from_user.id)
    is_ok, missing = await check_user_membership(call.bot, call.from_user.id, force_refresh=True)
    if not is_ok:
        await call.answer(f"Kamu belum join: {', '.join(t for t, _ in missing)}", show_alert=True)
        return
    await call.answer("✅ Verifikasi berhasil! Selamat datang.")
    await cb_menu_main(call, state)

@router.callback_query(F.data == "srv_full_alert")
async def cb_srv_full_alert(call: CallbackQuery):
    await call.answer("⚠️ Server ini sudah mencapai batas maksimal user! Silakan pilih server lain.", show_alert=True)

@router.callback_query(F.data.in_({"trial_ssh", "trial_trojan", "trial_vless", "trial_vmess", "create_ssh", "create_xray:trojan", "create_xray:vless", "create_xray:vmess"}))
async def cb_choose_server_first(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    ok, used, max_lim, reset_str = await check_daily_creation_quota(uid)
    if not ok and not await is_user_admin(uid):
        await call.answer(f"⚠️ Batas pembuatan akun Anda ({used}/{max_lim}) sudah penuh!\nReset otomatis pada: {reset_str}", show_alert=True)
        return
    servers = await get_servers_with_usage(only_active=True)
    act = call.data.replace(":", "_")
    await _safe_edit(
        call,
        "🌐 <b>Pilih Server VPS Tujuan:</b>\nSilakan pilih server yang tersedia di bawah ini (setiap server memiliki kuota maksimal user):",
        reply_markup=server_picker_kb(servers, act, "menu_main")
    )
    await call.answer()

@router.callback_query(F.data.startswith("pick_srv:"))
async def cb_picked_server(call: CallbackQuery, state: FSMContext):
    _, act, sid_str = call.data.split(":", 2)
    sid = int(sid_str)
    srv = await get_server_by_id(sid)
    if not srv:
        await call.answer("Server tidak ditemukan.", show_alert=True)
        return
    if srv["max_users"] > 0 and srv["used_users"] >= srv["max_users"]:
        await call.answer("⚠️ Kuota user pada server ini sudah penuh! Silakan pilih server lain.", show_alert=True)
        return

    s_dom = srv["domain"]
    await state.update_data(server_id=sid, server_domain=s_dom, server_name=srv["name"])

    if act.startswith("trial_"):
        proto = act.replace("trial_", "")
        uid = call.from_user.id
        ok, used, max_lim, reset_str = await check_daily_creation_quota(uid)
        if not ok and not await is_user_admin(uid):
            await call.answer(f"⚠️ Kuota pembuatan akun Anda ({used}/{max_lim}) habis! Reset: {reset_str}", show_alert=True)
            return

        await call.answer("⏳ Sedang membuat akun trial...")
        trial_dur_str = await get_setting("trial_duration") or "30m"
        rand_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        now_dt = now_wib()
        created_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        td = parse_duration(trial_dur_str) or datetime.timedelta(minutes=30)
        exp_str = (now_dt + td).strftime("%Y-%m-%d %H:%M:%S")
        exp_disp = format_expired_display(exp_str, is_trial=1, trial_dur_str=trial_dur_str)
        lim_ip = await get_effective_limit_ip(uid)

        if proto in ("trojan", "vless", "vmess"):
            uname = apply_prefix(f"trial-{rand_str}")
            u_uuid = str(uuid.uuid4())
            async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                await db.execute(
                    "INSERT INTO accounts (tg_id, acc_type, username, password_or_uuid, expired_at, limit_ip, is_trial, created_at, server_id) VALUES (?, ?, ?, ?, ?, -1, 1, ?, ?)",
                    (uid, proto, uname, u_uuid, exp_str, created_str, sid)
                )
                await db.commit()
            await record_account_creation(uid, proto, uname)
            await sync_xray_by_server(sid)
            await generate_portable_backup_sh()
            city, isp = await get_isp_info()
            await add_log(uid, f"CREATE_TRIAL_{proto.upper()}", f"Remarks: {uname} (Srv #{sid}, Exp: {exp_str})")
            asyncio.create_task(send_account_topic_notification(call.bot, uid, call.from_user.full_name, proto, uname, sid, exp_str, is_trial=True, dur_label=trial_dur_str))

            if proto == "vless":
                await send_multi_part_output(call.message, build_vless_messages(uname, city, isp, s_dom, u_uuid, lim_ip, exp_disp), replace_first=True)
            elif proto == "vmess":
                await send_multi_part_output(call.message, build_vmess_messages(uname, city, isp, s_dom, u_uuid, lim_ip, exp_disp), replace_first=True)
            else:
                text, kb = format_trojan_output(uname, city, isp, s_dom, u_uuid, lim_ip, exp_disp)
                await _safe_edit(call, text, reply_markup=kb)
        else:
            uname_ssh = apply_prefix(f"trial{rand_str}")
            pwd_ssh = "".join(random.choices(string.ascii_letters + string.digits, k=6))
            if not await create_ssh_on_server(sid, uname_ssh, pwd_ssh):
                await _safe_edit(call, "❌ Gagal membuat akun SSH pada server tujuan (Cek koneksi SSH di Panel Admin).", reply_markup=main_menu_kb(await is_user_admin(uid)))
                return
            async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                await db.execute(
                    "INSERT INTO accounts (tg_id, acc_type, username, password_or_uuid, expired_at, limit_ip, is_trial, created_at, server_id) VALUES (?, 'ssh', ?, ?, ?, -1, 1, ?, ?)",
                    (uid, uname_ssh, pwd_ssh, exp_str, created_str, sid)
                )
                await db.commit()
            await record_account_creation(uid, "ssh", uname_ssh)
            await generate_portable_backup_sh()
            text, kb = format_ssh_output(s_dom, uname_ssh, pwd_ssh, lim_ip, exp_disp, show_pass=True)
            await add_log(uid, "CREATE_TRIAL_SSH", f"User: {uname_ssh} (Srv #{sid}, Exp: {exp_str})")
            asyncio.create_task(send_account_topic_notification(call.bot, uid, call.from_user.full_name, "ssh", uname_ssh, sid, exp_str, is_trial=True, dur_label=trial_dur_str))
            await _safe_edit(call, text, reply_markup=kb)

    elif act == "create_ssh":
        await state.set_state(CreateSSHFSM.username)
        await state.update_data(started_at=datetime.datetime.now().timestamp())
        await _safe_edit(
            call,
            f"🔑 <b>Buat Akun SSH — {html.escape(srv['name'])} ({html.escape(s_dom)})</b>\n\n"
            f"Langkah 1/2: Masukkan <b>Username SSH</b>:\n• Hanya huruf kecil dan angka (<code>a-z0-9</code>), 3 - 16 karakter\n"
            f"• Prefix <code>{USERNAME_PREFIX}</code> ditambahkan otomatis (misal <code>abc</code> → <code>{USERNAME_PREFIX}abc</code>)",
            reply_markup=cancel_kb()
        )
        await call.answer()

    elif act.startswith("create_xray_"):
        proto = act.replace("create_xray_", "")
        await state.set_state(CreateXrayFSM.remark)
        await state.update_data(proto=proto, started_at=datetime.datetime.now().timestamp())
        await _safe_edit(
            call,
            f"🔐 <b>Buat Akun {proto.upper()} — {html.escape(srv['name'])} ({html.escape(s_dom)})</b>\n\n"
            f"Masukkan nama akun (<b>remarks</b>):\n• Huruf kecil, angka (<code>a-z0-9</code>), 3 - 16 karakter\n"
            f"• Prefix <code>{USERNAME_PREFIX}</code> ditambahkan otomatis (misal <code>abc</code> → <code>{USERNAME_PREFIX}abc</code>)",
            reply_markup=cancel_kb()
        )
        await call.answer()

@router.message(CreateXrayFSM.remark)
async def fsm_xray_remark(message: Message, state: FSMContext):
    data = await state.get_data()
    if datetime.datetime.now().timestamp() - data.get("started_at", 0) > 120:
        await state.clear()
        await message.answer("⏱ Waktu input habis (timeout 2 menit).", reply_markup=main_menu_kb(await is_user_admin(message.from_user.id)))
        return

    proto = data.get("proto", "vless")
    sid = int(data.get("server_id", 1))
    s_dom = data.get("server_domain", DOMAIN)
    # user ketik tanpa prefix; bot tambahkan prefix otomatis
    raw_remark = (message.text or "").strip().lower()
    if not re.match(r"^[a-z0-9]{3,16}$", raw_remark):
        await message.answer("❌ <b>Format nama akun tidak valid!</b> Gunakan huruf kecil & angka (3-16 karakter):", reply_markup=cancel_kb())
        return
    remark = apply_prefix(raw_remark)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT 1 FROM accounts WHERE username = ?", (remark,)) as cur:
            if await cur.fetchone():
                await message.answer("❌ Nama akun tersebut sudah dipakai. Silakan masukkan nama lain:", reply_markup=cancel_kb())
                return

    srv = await get_server_by_id(sid)
    if srv and srv["max_users"] > 0 and srv["used_users"] >= srv["max_users"]:
        await state.clear()
        await message.answer("⚠️ Maaf, kuota server tersebut baru saja penuh.")
        return

    uid = message.from_user.id
    ok, used, max_lim, reset_str = await check_daily_creation_quota(uid)
    if not ok and not await is_user_admin(uid):
        await state.clear()
        await message.answer(f"⚠️ Batas pembuatan akun ({used}/{max_lim}) habis. Reset pada: <code>{reset_str}</code>")
        return

    await state.clear()
    u_uuid = str(uuid.uuid4())
    dur_str = await get_effective_duration(uid)
    td = parse_duration(dur_str)
    now_dt = now_wib()
    created_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    exp_str = (now_dt + td).strftime("%Y-%m-%d %H:%M:%S") if td else "Unlimited"
    exp_disp = format_expired_display(exp_str, is_trial=0)
    lim_ip = await get_effective_limit_ip(uid)
    # Speed default akun = speed batas VPS tujuan (per-server), fallback ke setting global.
    srv_row = await get_server_by_id(sid)
    def_q = float(await get_setting("default_quota_gb") or 0)
    def_spd = int((srv_row.get("speed_mbps") if srv_row else 0) or 0) or int(await get_setting("default_speed_mbps") or 0)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute(
            "INSERT INTO accounts (tg_id, acc_type, username, password_or_uuid, expired_at, limit_ip, is_trial, created_at, server_id, quota_gb, speed_mbps) VALUES (?, ?, ?, ?, ?, -1, 0, ?, ?, ?, ?)",
            (uid, proto, remark, u_uuid, exp_str, created_str, sid, def_q, def_spd)
        )
        await db.commit()

    await record_account_creation(uid, proto, remark)
    await sync_xray_by_server(sid)
    await generate_portable_backup_sh()
    city, isp = await get_isp_info()
    await add_log(uid, f"CREATE_{proto.upper()}", f"Remarks: {remark} (Srv #{sid}, Buat: {created_str}, Exp: {exp_str})")
    asyncio.create_task(send_account_topic_notification(message.bot, uid, message.from_user.full_name, proto, remark, sid, exp_str, is_trial=False, dur_label=dur_str))

    if proto == "vless":
        await send_multi_part_output(message, build_vless_messages(remark, city, isp, s_dom, u_uuid, lim_ip, exp_disp), replace_first=False)
    elif proto == "vmess":
        await send_multi_part_output(message, build_vmess_messages(remark, city, isp, s_dom, u_uuid, lim_ip, exp_disp), replace_first=False)
    else:
        text, kb = format_trojan_output(remark, city, isp, s_dom, u_uuid, lim_ip, exp_disp)
        await message.answer(text, reply_markup=kb)

@router.message(CreateSSHFSM.username)
async def fsm_ssh_username(message: Message, state: FSMContext):
    data = await state.get_data()
    if datetime.datetime.now().timestamp() - data.get("started_at", 0) > 120:
        await state.clear()
        await message.answer("⏱ Waktu input habis (timeout 2 menit).", reply_markup=main_menu_kb(await is_user_admin(message.from_user.id)))
        return

    # user ketik tanpa prefix; bot tambahkan prefix otomatis
    raw_uname = (message.text or "").strip().lower()
    if not re.match(r"^[a-z0-9]{3,16}$", raw_uname):
        await message.answer("❌ <b>Username tidak valid!</b> Gunakan huruf kecil dan angka (3-16 karakter):", reply_markup=cancel_kb())
        return
    uname = apply_prefix(raw_uname)
    if uname in RESERVED_USERNAMES or system_user_exists(uname):
        await message.answer("❌ Username tersebut tidak diizinkan atau sudah terdaftar:", reply_markup=cancel_kb())
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT 1 FROM accounts WHERE username = ?", (uname,)) as cur:
            if await cur.fetchone():
                await message.answer("❌ Username sudah digunakan. Pilih username lain:", reply_markup=cancel_kb())
                return

    await state.update_data(username=uname, started_at=datetime.datetime.now().timestamp())
    await state.set_state(CreateSSHFSM.password)
    await message.answer(f"✅ Username: <code>{uname}</code>\n\nLangkah 2/2: Masukkan <b>Password SSH</b> (4 - 32 karakter, tanpa spasi):", reply_markup=cancel_kb())

@router.message(CreateSSHFSM.password)
async def fsm_ssh_password(message: Message, state: FSMContext):
    pwd_val = message.text or ""
    try:
        await message.delete()
    except Exception:
        pass
    data = await state.get_data()
    if " " in pwd_val or not (4 <= len(pwd_val) <= 32):
        await message.answer("❌ <b>Password tidak valid!</b> Harus 4-32 karakter tanpa spasi:", reply_markup=cancel_kb())
        return

    uname = data["username"]
    s_dom = data.get("server_domain", DOMAIN)
    s_name = data.get("server_name", "Server Utama")
    uid = message.from_user.id
    dur_str = await get_effective_duration(uid)
    lim_ip = await get_effective_limit_ip(uid)
    lim_str = "Unlimited" if lim_ip == 0 else str(lim_ip)

    await state.update_data(password=pwd_val, started_at=datetime.datetime.now().timestamp())
    await state.set_state(CreateSSHFSM.confirm)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Konfirmasi Buat Akun", callback_data="confirm_create_ssh"), InlineKeyboardButton(text="❌ Batal", callback_data="cancel_fsm:menu_main")]
    ])
    await message.answer(
        "📋 <b>Ringkasan Pembuatan Akun SSH</b>\n\n"
        f"• <b>Server:</b> <code>{html.escape(s_name)} ({html.escape(s_dom)})</code>\n"
        f"• <b>Username:</b> <code>{html.escape(uname)}</code>\n"
        f"• <b>Password:</b> <code>{html.escape(pwd_val)}</code>\n"
        f"• <b>Masa Aktif:</b> <code>{dur_str}</code>\n"
        f"• <b>Limit IP:</b> <code>{lim_str}</code>\n\n"
        "Tekan <b>Konfirmasi</b> untuk membuat akun sekarang:",
        reply_markup=kb
    )

@router.callback_query(CreateSSHFSM.confirm, F.data == "confirm_create_ssh")
async def cb_confirm_ssh(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uname, pwd_val = data["username"], data["password"]
    sid = int(data.get("server_id", 1))
    s_dom = data.get("server_domain", DOMAIN)
    uid = call.from_user.id

    dur_str = await get_effective_duration(uid)
    td = parse_duration(dur_str)
    now_dt = now_wib()
    created_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    exp_str = (now_dt + td).strftime("%Y-%m-%d %H:%M:%S") if td else "Unlimited"
    exp_disp = format_expired_display(exp_str, is_trial=0)
    lim_ip = await get_effective_limit_ip(uid)
    # Speed default akun = speed batas VPS tujuan (per-server), fallback ke setting global.
    srv_row = await get_server_by_id(sid)
    def_q = float(await get_setting("default_quota_gb") or 0)
    def_spd = int((srv_row.get("speed_mbps") if srv_row else 0) or 0) or int(await get_setting("default_speed_mbps") or 0)

    if not await create_ssh_on_server(sid, uname, pwd_val):
        await _safe_edit(call, "❌ Gagal membuat akun SSH pada server tujuan. Hubungi Admin.", reply_markup=main_menu_kb(await is_user_admin(uid)))
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute(
            "INSERT INTO accounts (tg_id, acc_type, username, password_or_uuid, expired_at, limit_ip, is_trial, created_at, server_id, quota_gb, speed_mbps) VALUES (?, 'ssh', ?, ?, ?, -1, 0, ?, ?, ?, ?)",
            (uid, uname, pwd_val, exp_str, created_str, sid, def_q, def_spd)
        )
        await db.commit()

    await record_account_creation(uid, "ssh", uname)
    await generate_portable_backup_sh()
    text, kb = format_ssh_output(s_dom, uname, pwd_val, lim_ip, exp_disp, show_pass=True)
    await add_log(uid, "CREATE_SSH", f"Username: {uname} (Srv #{sid}, Buat: {created_str}, Exp: {exp_str})")
    asyncio.create_task(send_account_topic_notification(call.bot, uid, call.from_user.full_name, "ssh", uname, sid, exp_str, is_trial=False, dur_label=dur_str))
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer("✅ Akun SSH berhasil dibuat!")


@router.callback_query(F.data.startswith("view_acc:"))
async def cb_view_acc(call: CallbackQuery):
    acc_id = int(call.data.split(":")[1])
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, expired_at, limit_ip, is_trial, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()

    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    owner_id, acc_type, uname, secret, exp_at, acc_lim, is_trial, sid = row
    srv = await get_server_by_id(sid)
    s_dom = srv["domain"] if srv else DOMAIN
    eff_lim = await get_effective_limit_ip(owner_id, acc_lim)
    trial_dur_str = await get_setting("trial_duration") or "30m"
    exp_disp = format_expired_display(exp_at, is_trial=is_trial, trial_dur_str=trial_dur_str)
    await call.answer()

    if acc_type == "vless":
        city, isp = await get_isp_info()
        await send_multi_part_output(call.message, build_vless_messages(uname, city, isp, s_dom, secret, eff_lim, exp_disp), replace_first=True)
    elif acc_type == "vmess":
        city, isp = await get_isp_info()
        await send_multi_part_output(call.message, build_vmess_messages(uname, city, isp, s_dom, secret, eff_lim, exp_disp), replace_first=True)
    elif acc_type == "trojan":
        city, isp = await get_isp_info()
        text, kb = format_trojan_output(uname, city, isp, s_dom, secret, eff_lim, exp_disp)
        await _safe_edit(call, text, reply_markup=kb)
    else:
        text, kb = format_ssh_output(s_dom, uname, secret, eff_lim, exp_disp, show_pass=(uid == owner_id or adm))
        await _safe_edit(call, text, reply_markup=kb)

@router.callback_query(F.data == "delete_menu")
async def cb_delete_menu(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT id, acc_type, username FROM accounts WHERE tg_id = ?", (call.from_user.id,)) as cur:
            rows = await cur.fetchall()
    if not rows:
        await call.answer("Anda belum memiliki akun untuk dihapus.", show_alert=True)
        return
    btns = [[InlineKeyboardButton(text=f"🗑 Hapus {r[2]} ({r[1].upper()})", callback_data=f"usr_del_ask:{r[0]}")] for r in rows]
    btns.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data="menu_main")])
    await _safe_edit(call, "🗑 <b>Pilih akun yang ingin dihapus:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data.startswith("usr_del_ask:"))
async def cb_usr_del_ask(call: CallbackQuery):
    acc_id = int(call.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ya, Hapus", callback_data=f"usr_del_yes:{acc_id}"), InlineKeyboardButton(text="❌ Batal", callback_data="delete_menu")]
    ])
    await _safe_edit(call, "⚠️ <b>Apakah Anda yakin ingin menghapus akun ini secara permanen?</b>", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("usr_del_yes:"))
async def cb_usr_del_yes(call: CallbackQuery):
    acc_id = int(call.data.split(":")[1])
    uid = call.from_user.id
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT acc_type, username, COALESCE(server_id, 1) FROM accounts WHERE id = ? AND tg_id = ?", (acc_id, uid)) as cur:
            row = await cur.fetchone()
        if not row:
            await call.answer("Akun tidak ditemukan.", show_alert=True)
            return
        acc_type, uname, sid = row
        await db.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))
        await db.commit()

    if acc_type == "ssh":
        await delete_ssh_on_server(sid, uname)
    else:
        await sync_xray_by_server(sid)
    await generate_portable_backup_sh()
    await add_log(uid, "DELETE_ACCOUNT", f"Deleted {acc_type}: {uname} (Srv #{sid})")
    await call.answer(f"🗑 Akun {uname} berhasil dihapus.", show_alert=True)
    await cb_my_accounts(call)

@router.callback_query(F.data == "server_info_legacy")
async def cb_server_info(call: CallbackQuery):
    city, isp = await get_isp_info()
    st = get_server_stats()
    lim_ip = await get_effective_limit_ip(call.from_user.id)
    lim_str = "Unlimited" if lim_ip == 0 else f"{lim_ip} IP"
    _, used, max_lim, reset_str = await check_daily_creation_quota(call.from_user.id)
    servers = await get_servers_with_usage(only_active=True)
    srv_lines = [f"  • <b>{html.escape(s['name'])}</b> (<code>{html.escape(s['domain'])}</code>) — Kuota: <code>{s['used_users']}/{'♾' if s['max_users']==0 else s['max_users']}</code>" for s in servers]

    text = (
        "ℹ️ <b>Informasi Server & Aturan Layanan</b>\n\n"
        f"• <b>Domain Utama:</b> <code>{DOMAIN}</code>\n"
        f"• <b>Lokasi / ISP:</b> <code>{city} / {isp}</code>\n"
        f"• <b>Waktu Server:</b> <code>{now_wib().strftime('%Y-%m-%d %H:%M WIB')}</code>\n"
        f"• <b>CPU / RAM:</b> <code>{st['cpu']} | {st['ram']}</code>\n"
        f"• <b>Limit IP Anda:</b> <code>{lim_str}</code>\n"
        f"• <b>Kuota Buat Akun:</b> <code>{used}/{max_lim}</code> (Reset: <code>{reset_str}</code>)\n\n"
        "🌐 <b>Daftar Server Tersedia:</b>\n" + ("\n".join(srv_lines) if srv_lines else "-")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Kembali", callback_data="menu_main")]])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("bug_menu:"))
async def cb_bug_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    acc_id = int(call.data.split(":")[1])
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id, acc_type, username, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return
    _, acc_type, uname, sid = row
    if acc_type not in ("vless", "vmess", "trojan"):
        await call.answer("Fitur ini khusus untuk akun VMess, VLESS, dan Trojan.", show_alert=True)
        return
    srv = await get_server_by_id(sid)
    s_dom = srv["domain"] if srv else DOMAIN

    text = (
        f"🎯 <b>Pilih Metode Inject Bug — {html.escape(uname)} ({acc_type.upper()})</b>\n"
        f"🌐 <b>Server:</b> <code>{html.escape(s_dom)}</code>\n\n"
        "Silakan pilih metode pemasangan bug yang ingin Anda gunakan:\n"
        "1️⃣ <b>Wildcard (bug.domain):</b> Address = <code>bug</code>, SNI &amp; Host = <code>bug.domain</code>\n"
        "2️⃣ <b>SNI / SSL (Spoof):</b> Address = <code>domain</code>, SNI &amp; Host = <code>bug</code>\n"
        "3️⃣ <b>CDN / Direct WS:</b> Address = <code>bug</code>, SNI &amp; Host = <code>domain</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Mode Wildcard (bug.domain)", callback_data=f"bug_mode:{acc_id}:wildcard")],
        [InlineKeyboardButton(text="2️⃣ Mode SNI / SSL (Spoof Bug)", callback_data=f"bug_mode:{acc_id}:sni")],
        [InlineKeyboardButton(text="3️⃣ Mode CDN / WS (Direct)", callback_data=f"bug_mode:{acc_id}:cdn")],
        [InlineKeyboardButton(text="⚡ Tampilkan Tanpa Bug (Default)", callback_data=f"bug_quick:{acc_id}:cdn:{s_dom}"[:64])],
        [InlineKeyboardButton(text="⬅️ Kembali ke Akun Saya", callback_data="my_accounts")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

async def _get_bug_list_by_mode(mode: str) -> list[tuple[str, str]]:
    key_map = {
        "wildcard": "bug_presets_wildcard",
        "sni": "bug_presets_sni",
        "cdn": "bug_presets_cdn",
    }
    raw = await get_setting(key_map.get(mode, "bug_presets_wildcard"))
    if not raw:
        raw = await get_setting("bug_presets") or "ava.game.naver.com,quiz.vidio.com,zoom.us"
    items: list[tuple[str, str]] = []
    for part in re.split(r"[,\n]+", raw):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            lbl, dom = part.split("=", 1)
            lbl, dom = lbl.strip(), dom.strip()
        else:
            lbl, dom = part, part
        if dom:
            items.append((lbl[:28], dom))
    return items

@router.callback_query(F.data.startswith("bug_mode:"))
async def cb_bug_mode(call: CallbackQuery, state: FSMContext):
    _, acc_id_s, mode = call.data.split(":", 2)
    acc_id = int(acc_id_s)
    presets = await _get_bug_list_by_mode(mode)

    mode_titles = {
        "wildcard": "🌐 WILDCARD (bug.domain)",
        "sni": "🔒 SNI / SSL (Spoof Bug)",
        "cdn": "☁️ CDN / WS (Direct)"
    }
    btns = []
    row_b = []
    for idx, (lbl, dom) in enumerate(presets[:16]):
        btn_txt = f"🎯 {lbl}" if lbl == dom else f"🎯 {lbl} ({dom[:14]})"
        row_b.append(InlineKeyboardButton(text=btn_txt, callback_data=f"bug_idx:{acc_id}:{mode}:{idx}"))
        if len(row_b) == 2:
            btns.append(row_b)
            row_b = []
    if row_b:
        btns.append(row_b)

    btns.append([InlineKeyboardButton(text="✏️ Ketik Bug / IP Manual", callback_data=f"bug_custom:{acc_id}:{mode}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali Pilih Metode", callback_data=f"bug_menu:{acc_id}")])

    await _safe_edit(
        call,
        f"🎯 <b>Pilih Bug — Metode {mode_titles.get(mode, mode.upper())}</b>\n\n"
        "Pilih salah satu preset bug khusus di bawah ini atau tekan <b>Ketik Bug / IP Manual</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
    )
    await call.answer()

@router.callback_query(F.data.startswith("bug_idx:"))
async def cb_bug_idx(call: CallbackQuery):
    _, acc_id_s, mode, idx_s = call.data.split(":", 3)
    acc_id, idx = int(acc_id_s), int(idx_s)
    presets = await _get_bug_list_by_mode(mode)
    if idx < 0 or idx >= len(presets):
        await call.answer("Preset bug tidak ditemukan.", show_alert=True)
        return
    _, bug = presets[idx]
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return
    _, acc_type, uname, secret, sid = row
    srv = await get_server_by_id(sid)
    s_dom = srv["domain"] if srv else DOMAIN
    s_name = srv["name"] if srv else "Server Utama"

    text, kb = build_injected_xray_html(acc_type, uname, secret, s_name, s_dom, bug, mode=mode, acc_id=acc_id)
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer("✅ Config berhasil di-generate!")

@router.callback_query(F.data.startswith("bug_quick:"))
async def cb_bug_quick(call: CallbackQuery):
    _, acc_id_s, mode, bug = call.data.split(":", 3)
    acc_id = int(acc_id_s)
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return
    _, acc_type, uname, secret, sid = row
    srv = await get_server_by_id(sid)
    s_dom = srv["domain"] if srv else DOMAIN
    s_name = srv["name"] if srv else "Server Utama"

    text, kb = build_injected_xray_html(acc_type, uname, secret, s_name, s_dom, bug, mode=mode, acc_id=acc_id)
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer("✅ Config berhasil di-generate!")

@router.callback_query(F.data.startswith("bug_custom:"))
async def cb_bug_custom(call: CallbackQuery, state: FSMContext):
    _, acc_id_s, mode = call.data.split(":", 2)
    await state.set_state(BugInjectFSM.custom_bug)
    await state.update_data(acc_id=int(acc_id_s), mode=mode)
    await _safe_edit(
        call,
        f"✏️ <b>Masukkan Domain / IP Bug (Metode: {mode.upper()})</b>\n\n"
        "Ketik domain atau IP bug operator Anda (contoh: <code>ava.game.naver.com</code> atau <code>104.18.2.2</code>):",
        reply_markup=cancel_kb("menu_main")
    )
    await call.answer()

@router.message(BugInjectFSM.custom_bug)
async def fsm_bug_custom_input(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    acc_id = int(data.get("acc_id", 0))
    mode = data.get("mode", "wildcard")
    bug = (message.text or "").strip().lower()
    if not re.match(r"^[a-z0-9.-]{3,100}$", bug):
        await message.answer("❌ Format bug tidak valid! Hanya gunakan huruf, angka, titik, atau strip.")
        return

    uid = message.from_user.id
    adm = await is_user_admin(uid)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await message.answer("❌ Akun tidak ditemukan.")
        return
    _, acc_type, uname, secret, sid = row
    srv = await get_server_by_id(sid)
    s_dom = srv["domain"] if srv else DOMAIN
    s_name = srv["name"] if srv else "Server Utama"

    text, kb = build_injected_xray_html(acc_type, uname, secret, s_name, s_dom, bug, mode=mode, acc_id=acc_id)
    await message.answer(text, reply_markup=kb)

# === FITUR STATELESS INJECT BUG CONFIG LUAR (TANPA SIMPAN DATA) ===
from .states import ExternalBugFSM
from .formatter import parse_external_xray_link, build_injected_xray_html

async def _get_ext_bug_list(mode: str) -> list[tuple[str, str]]:
    key_map = {
        "wildcard": "bug_presets_wildcard",
        "sni": "bug_presets_sni",
        "cdn": "bug_presets_cdn",
    }
    raw = await get_setting(key_map.get(mode, "bug_presets_wildcard")) or ""
    items: list[tuple[str, str]] = []
    for part in re.split(r"[,\n]+", raw):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            lbl, dom = part.split("=", 1)
            lbl, dom = lbl.strip(), dom.strip()
        else:
            lbl, dom = part, part
        if dom:
            items.append((lbl[:28], dom))
    return items

@router.callback_query(F.data == "ext_bug_start")
async def cb_ext_bug_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ExternalBugFSM.waiting_link)
    text = (
        "🛠 <b>Inject Bug &amp; Convert Clash (Config Luar)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Anda dapat menggunakan daftar <b>Preset Bug</b> di bot ini untuk meng-inject akun <b>VMess, VLESS, atau Trojan</b> milik Anda sendiri (dari luar bot).\n\n"
        "🔒 <b>Jaminan Privasi:</b>\n"
        "• Akun Anda <b>TIDAK disimpan</b> di database bot maupun di preset.\n"
        "• Pesan link yang Anda kirim akan langsung dihapus otomatis setelah dibaca di RAM.\n\n"
        "📋 <b>Silakan tempel (paste) link</b> <code>vmess://...</code>, <code>vless://...</code>, atau <code>trojan://...</code> Anda sekarang:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Batal / Kembali", callback_data="menu_main")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.message(ExternalBugFSM.waiting_link)
@router.message(F.text.regexp(r"(?i)^(vmess|vless|trojan)://"))
async def fsm_ext_bug_receive_link(message: Message, state: FSMContext):
    raw_txt = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass

    try:
        parsed = parse_external_xray_link(raw_txt)
        if not parsed:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Batal", callback_data="menu_main")]])
            await message.answer(
                "❌ <b>Format link tidak dikenali!</b>\n"
                "Pastikan Anda mengirim link utuh yang diawali <code>vmess://</code>, <code>vless://</code>, atau <code>trojan://</code>:",
                reply_markup=kb
            )
            return

        await state.set_state(ExternalBugFSM.ready)
        await state.update_data(ext_acc=parsed, ext_bug=parsed["domain"], ext_mode="wildcard")

        text = (
            f"✅ <b>Akun {parsed['proto'].upper()} Berhasil Dibaca di RAM!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Nama Akun:</b> <code>{html.escape(parsed['remark'])}</code>\n"
            f"• <b>Domain Server:</b> <code>{html.escape(parsed['domain'])}</code>\n"
            f"• <b>Path WS:</b> <code>{html.escape(parsed['path'])}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔒 <i>Data akun di atas hanya berada di sesi memori sementara.</i>\n\n"
            "👇 <b>Pilih Metode Pasang Bug:</b>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="1️⃣ Mode Wildcard (bug.domain)", callback_data="ext_bmode:wildcard")],
            [InlineKeyboardButton(text="2️⃣ Mode SNI / SSL (Spoof Bug)", callback_data="ext_bmode:sni")],
            [InlineKeyboardButton(text="3️⃣ Mode CDN / WS (Direct)", callback_data="ext_bmode:cdn")],
            [InlineKeyboardButton(text="⚡ Tampilkan Config Default + YAML Clash", callback_data="ext_bquick:cdn")],
            [InlineKeyboardButton(text="🏠 Menu Utama (Hapus Sesi)", callback_data="menu_main")]
        ])
        await message.answer(text, reply_markup=kb)
    except Exception as e:
        await message.answer(f"⚠️ Gagal membaca link: <code>{html.escape(str(e))}</code>")

@router.callback_query(F.data == "ext_bug_pick_mode")
async def cb_ext_bug_pick_mode(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    parsed = data.get("ext_acc")
    if not parsed:
        await call.answer("⚠️ Sesi RAM sudah habis/dihapus. Silakan tempel ulang link Anda.", show_alert=True)
        await cb_ext_bug_start(call, state)
        return
    await state.set_state(ExternalBugFSM.ready)
    text = (
        f"🎯 <b>Pilih Metode Inject Bug — {html.escape(parsed['remark'])} ({parsed['proto'].upper()})</b>\n"
        f"🌐 <b>Domain Server:</b> <code>{html.escape(parsed['domain'])}</code>\n"
        f"🛣 <b>Path:</b> <code>{html.escape(parsed['path'])}</code>\n\n"
        "Silakan pilih metode pemasangan bug:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Mode Wildcard (bug.domain)", callback_data="ext_bmode:wildcard")],
        [InlineKeyboardButton(text="2️⃣ Mode SNI / SSL (Spoof Bug)", callback_data="ext_bmode:sni")],
        [InlineKeyboardButton(text="3️⃣ Mode CDN / WS (Direct)", callback_data="ext_bmode:cdn")],
        [InlineKeyboardButton(text="🔄 Paste Link Baru", callback_data="ext_bug_start")],
        [InlineKeyboardButton(text="🏠 Menu Utama (Hapus Sesi)", callback_data="menu_main")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("ext_bmode:"))
async def cb_ext_bmode(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    parsed = data.get("ext_acc")
    if not parsed:
        await call.answer("⚠️ Sesi RAM sudah habis/dihapus. Silakan tempel ulang link Anda.", show_alert=True)
        await cb_ext_bug_start(call, state)
        return

    mode = call.data.split(":", 1)[1]
    await state.set_state(ExternalBugFSM.ready)
    await state.update_data(ext_mode=mode)
    presets = await _get_ext_bug_list(mode)

    mode_titles = {
        "wildcard": "🌐 WILDCARD (bug.domain)",
        "sni": "🔒 SNI / SSL (Spoof Bug)",
        "cdn": "☁️ CDN / WS (Direct)"
    }
    btns = []
    row_b = []
    for idx, (lbl, dom) in enumerate(presets[:16]):
        btn_txt = f"🎯 {lbl}" if lbl == dom else f"🎯 {lbl} ({dom[:14]})"
        row_b.append(InlineKeyboardButton(text=btn_txt, callback_data=f"ext_bidx:{mode}:{idx}"))
        if len(row_b) == 2:
            btns.append(row_b)
            row_b = []
    if row_b:
        btns.append(row_b)

    btns.append([InlineKeyboardButton(text="✏️ Ketik Bug / IP Manual", callback_data=f"ext_bcustom:{mode}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali Pilih Metode", callback_data="ext_bug_pick_mode")])

    await _safe_edit(
        call,
        f"🎯 <b>Pilih Preset Bug — Metode {mode_titles.get(mode, mode.upper())}</b>\n"
        f"🌐 <b>Server Akun:</b> <code>{html.escape(parsed['domain'])}</code>\n\n"
        "Pilih salah satu preset bug di bawah ini atau ketik manual:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
    )
    await call.answer()

@router.callback_query(F.data.startswith("ext_bidx:"))
async def cb_ext_bidx(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    parsed = data.get("ext_acc")
    if not parsed:
        await call.answer("⚠️ Sesi RAM sudah habis/dihapus. Silakan tempel ulang link Anda.", show_alert=True)
        await cb_ext_bug_start(call, state)
        return

    _, mode, idx_s = call.data.split(":", 2)
    idx = int(idx_s)
    presets = await _get_ext_bug_list(mode)
    if idx < 0 or idx >= len(presets):
        await call.answer("Preset bug tidak ditemukan.", show_alert=True)
        return
    _, bug = presets[idx]
    await state.update_data(ext_bug=bug, ext_mode=mode)

    text, kb = build_injected_xray_html(
        proto=parsed["proto"],
        remark=parsed["remark"],
        secret=parsed["secret"],
        server_name="External Server",
        domain=parsed["domain"],
        bug=bug,
        mode=mode,
        custom_path=parsed["path"],
        is_external=True
    )
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer("✅ Config berhasil di-inject!")

@router.callback_query(F.data.startswith("ext_bquick:"))
async def cb_ext_bquick(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    parsed = data.get("ext_acc")
    if not parsed:
        await call.answer("⚠️ Sesi RAM sudah habis/dihapus. Silakan tempel ulang link Anda.", show_alert=True)
        await cb_ext_bug_start(call, state)
        return

    mode = call.data.split(":", 1)[1]
    bug = data.get("ext_bug") or parsed["domain"]
    await state.update_data(ext_mode=mode)

    text, kb = build_injected_xray_html(
        proto=parsed["proto"],
        remark=parsed["remark"],
        secret=parsed["secret"],
        server_name="External Server",
        domain=parsed["domain"],
        bug=bug,
        mode=mode,
        custom_path=parsed["path"],
        is_external=True
    )
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer(f"✅ Beralih ke mode {mode.upper()}!")

@router.callback_query(F.data.startswith("ext_bcustom:"))
async def cb_ext_bcustom(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("ext_acc"):
        await call.answer("⚠️ Sesi RAM sudah habis/dihapus. Silakan tempel ulang link Anda.", show_alert=True)
        await cb_ext_bug_start(call, state)
        return
    mode = call.data.split(":", 1)[1]
    await state.set_state(ExternalBugFSM.custom_bug)
    await state.update_data(ext_mode=mode)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Batal", callback_data=f"ext_bmode:{mode}")]])
    await _safe_edit(
        call,
        f"✏️ <b>Ketik Bug Manual (Metode: {mode.upper()})</b>\n\n"
        "Masukkan domain atau IP bug operator Anda (contoh: <code>ava.game.naver.com</code>):\n"
        "<i>(Bug yang Anda ketik tidak akan disimpan ke daftar preset bot)</i>",
        reply_markup=kb
    )
    await call.answer()

@router.message(ExternalBugFSM.custom_bug)
async def fsm_ext_bcustom_input(message: Message, state: FSMContext):
    data = await state.get_data()
    parsed = data.get("ext_acc")
    if not parsed:
        await state.clear()
        await message.answer("⚠️ Sesi telah berakhir. Silakan ulangi dari menu utama.")
        return

    bug = (message.text or "").strip().lower()
    if not re.match(r"^[a-z0-9.-]{3,100}$", bug):
        await message.answer("❌ Format domain/IP bug tidak valid! Hanya gunakan huruf, angka, titik, atau strip.")
        return

    mode = data.get("ext_mode", "wildcard")
    await state.set_state(ExternalBugFSM.ready)
    await state.update_data(ext_bug=bug)

    text, kb = build_injected_xray_html(
        proto=parsed["proto"],
        remark=parsed["remark"],
        secret=parsed["secret"],
        server_name="External Server",
        domain=parsed["domain"],
        bug=bug,
        mode=mode,
        custom_path=parsed["path"],
        is_external=True
    )
    await message.answer(text, reply_markup=kb)

# === FITUR MENU USER: LIST WILDCARD & DATA PEMAKAIAN SERVER ===
from .system_manager import (
    get_cluster_node_stats, get_ssh_active_sessions, get_xray_active_ips, format_trial_duration_label
)

def _format_window_indo(win_str: str) -> str:
    s = (win_str or "1d").strip().lower()
    if s in ("0", "unlimited", "tanpa reset", "permanent"):
        return "Tanpa Reset"
    m = re.match(r"^(\d+)\s*(m|min|mnt|menit|h|jam|hour|d|day|hari)?$", s)
    if not m:
        return s
    val = int(m.group(1))
    unit = m.group(2) or "d"
    if unit in ("m", "min", "mnt", "menit"):
        return f"{val} Menit"
    if unit in ("h", "jam", "hour"):
        return f"{val} Jam"
    return f"{val} Hari"

@router.callback_query(F.data.startswith("user_wildcard_list"))
async def cb_user_wildcard_list(call: CallbackQuery):
    wc_bugs = await _get_ext_bug_list("wildcard")

    wc_lines = []
    for i, (lbl, bdom) in enumerate(wc_bugs, start=1):
        if lbl.lower() != bdom.lower():
            wc_lines.append(f"{i}. <b>{html.escape(lbl)}</b> : <code>{html.escape(bdom)}</code>")
        else:
            wc_lines.append(f"{i}. <code>{html.escape(bdom)}</code>")

    text = (
        "🌐 <b>LIST BUG WILDCARD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        + ("\n".join(wc_lines) if wc_lines else "<i>(Belum ada daftar wildcard yang diset oleh Admin)</i>")
        + "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Ketuk pada teks kode di atas untuk menyalin bug wildcard.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Data Pemakaian Server", callback_data="server_info"),
            InlineKeyboardButton(text="🛠 Inject Config", callback_data="ext_bug_start")
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Menu Utama", callback_data="menu_main")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()


# === HANDLER FITUR KOMUNITAS: KELOLA MANDIRI & CEK BUG ISP ===
import time
from .states import BugCheckerFSM, SelfManagePassFSM
from .system_manager import (
    kick_user_sessions_on_server, check_bug_host_isp, delete_ssh_on_server,
    lock_ssh_on_server, format_bytes_human
)

_SELF_ACTION_COOLDOWN: dict[tuple[int, str], float] = {}

def _check_self_cooldown(uid: int, action_key: str, cooldown_sec: int = 180) -> tuple[bool, int]:
    now = time.time()
    last = _SELF_ACTION_COOLDOWN.get((uid, action_key), 0.0)
    if now - last < cooldown_sec:
        return False, int(cooldown_sec - (now - last))
    _SELF_ACTION_COOLDOWN[(uid, action_key)] = now
    return True, 0

@router.callback_query(F.data == "my_accounts")
async def cb_my_accounts(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    servers_list = await get_servers_with_usage(only_active=False)
    srv_map = {int(s["id"]): s["name"] for s in servers_list}
    self_on = (await get_setting("feat_self_manage")) == "1" or await is_user_admin(uid)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT id, acc_type, username, expired_at, is_trial, is_locked, COALESCE(server_id, 1), COALESCE(quota_gb, 0), COALESCE(used_bytes, 0), COALESCE(speed_mbps, 0) FROM accounts WHERE tg_id = ? ORDER BY id DESC",
            (uid,)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await _safe_edit(
            call,
            "📋 <b>Daftar Akun Saya</b>\n\nAnda belum memiliki akun aktif. Silakan buat akun melalui menu utama.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Kembali ke Menu Utama", callback_data="menu_main")]])
        )
        await call.answer()
        return

    ssh_online = get_ssh_active_sessions()
    xray_online = get_xray_active_ips()
    lim_ip = await get_effective_limit_ip(uid)
    lim_str = "♾" if lim_ip == 0 else str(lim_ip)

    lines = ["📋 <b>Daftar Akun Aktif Anda:</b>\n"]
    buttons = []
    for idx, (acc_id, acc_type, uname, exp, is_trial, is_locked, sid, q_gb, u_bytes, spd) in enumerate(rows, 1):
        badge = "🆓 Trial" if is_trial else "🔑 Reguler"
        srv_name = srv_map.get(int(sid), f"Server #{sid}")
        ip_list = [ip for _, ip in ssh_online.get(uname, [])] if acc_type == "ssh" else list(xray_online.get(uname, set()))
        if is_locked == 1:
            st_txt = "🔒 Terkunci"
        elif ip_list:
            st_txt = f"🟢 Online ({len(ip_list)}/{lim_str} IP)"
        else:
            st_txt = f"⚪ Offline (0/{lim_str} IP)"

        q_txt = f"{format_bytes_human(u_bytes)} / {'♾' if float(q_gb or 0) <= 0 else str(q_gb)+' GB'}"
        spd_txt = "♾" if int(spd or 0) <= 0 else f"{spd} Mbps"
        lines.append(
            f"{idx}. <b>{html.escape(uname)}</b> [<code>{acc_type.upper()}</code> - {badge}]\n"
            f"   ├ Server: <code>{html.escape(srv_name)}</code> | Status: {st_txt}\n"
            f"   ├ Kuota: <code>{q_txt}</code> | Speed: <code>{spd_txt}</code>\n"
            f"   └ Exp: <code>{html.escape(exp)}</code>"
        )
        row_btn = [InlineKeyboardButton(text=f"📄 Config: {uname}", callback_data=f"view_acc:{acc_id}")]
        if acc_type in ("vless", "vmess", "trojan"):
            row_btn.append(InlineKeyboardButton(text="🎯 Inject Bug", callback_data=f"bug_menu:{acc_id}"))
        if self_on:
            row_btn.append(InlineKeyboardButton(text="🛠 Kelola", callback_data=f"usr_manage:{acc_id}"))
        buttons.append(row_btn)

    buttons.append([
        InlineKeyboardButton(text="🔄 Refresh Status", callback_data="my_accounts"),
        InlineKeyboardButton(text="⬅️ Menu Utama", callback_data="menu_main")
    ])
    await _safe_edit(call, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data.startswith("usr_manage:"))
async def cb_usr_manage(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if (await get_setting("feat_self_manage")) != "1" and not await is_user_admin(call.from_user.id):
        await call.answer("⚠️ Fitur Kelola Akun Mandiri sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return

    acc_id = int(call.data.split(":", 1)[1])
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, expired_at, is_locked, COALESCE(server_id, 1), COALESCE(quota_gb, 0), COALESCE(used_bytes, 0), COALESCE(speed_mbps, 0) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()

    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, acc_type, uname, secret, exp_at, is_locked, sid, q_gb, u_bytes, spd = row
    srv = await get_server_by_id(sid)
    s_name = srv["name"] if srv else f"Server #{sid}"
    s_dom = srv["domain"] if srv else DOMAIN

    ssh_on = get_ssh_active_sessions()
    xray_on = get_xray_active_ips()
    ip_list = [ip for _, ip in ssh_on.get(uname, [])] if acc_type == "ssh" else list(xray_on.get(uname, set()))
    lim_ip = await get_effective_limit_ip(row[0])
    lim_str = "Unlimited" if lim_ip == 0 else f"{lim_ip} IP"

    if is_locked == 1:
        st_str = "🔒 Terkunci (Limit IP / Kuota)"
    elif ip_list:
        st_str = f"🟢 Online ({len(ip_list)}/{lim_str})"
    else:
        st_str = f"⚪ Offline (0/{lim_str})"

    q_str = f"{format_bytes_human(u_bytes)} / {'♾ Unlimited' if float(q_gb) <= 0 else str(q_gb)+' GB'}"

    text = (
        "🛠 <b>KELOLA AKUN MANDIRI (TANPA POTONG KUOTA)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Username:</b> <code>{html.escape(uname)}</code>\n"
        f"• <b>Protokol:</b> <code>{acc_type.upper()}</code>\n"
        f"• <b>Server Saat Ini:</b> <code>{html.escape(s_name)} ({html.escape(s_dom)})</code>\n"
        f"• <b>Status Sesi:</b> <code>{st_str}</code>\n"
        f"• <b>Pemakaian Kuota:</b> <code>{q_str}</code>\n"
        f"• <b>Expired:</b> <code>{html.escape(exp_at)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>Pilihan Fitur Mandiri:</b>\n"
        "• <b>🧹 Reset Sesi:</b> Putus IP yang nyangkut saat berpindah jaringan.\n"
        "• <b>🔑 Ganti UUID/Pass:</b> Ganti kredensial akun jika bocor.\n"
        "• <b>⚡ Tukar Protokol:</b> Ubah VLESS ⇄ VMess ⇄ Trojan.\n"
        "• <b>🔀 Pindah Server:</b> Pindah ke server lain (masa aktif &amp; kuota tetap)."
    )

    btns = [
        [
            InlineKeyboardButton(text="🧹 Reset Sesi / IP", callback_data=f"usr_act_kick:{acc_id}"),
            InlineKeyboardButton(text="🔑 Ganti UUID / Pass", callback_data=f"usr_act_cred:{acc_id}")
        ]
    ]
    row_2 = [InlineKeyboardButton(text="🔀 Pindah Server", callback_data=f"usr_act_mig:{acc_id}")]
    if acc_type in ("vless", "vmess", "trojan"):
        row_2.insert(0, InlineKeyboardButton(text="⚡ Tukar Protokol", callback_data=f"usr_act_proto:{acc_id}"))
    btns.append(row_2)

    row_3 = [InlineKeyboardButton(text="📄 Lihat Config", callback_data=f"view_acc:{acc_id}")]
    if acc_type in ("vless", "vmess", "trojan"):
        row_3.append(InlineKeyboardButton(text="🎯 Inject Bug / Clash", callback_data=f"bug_menu:{acc_id}"))
    btns.append(row_3)
    btns.append([InlineKeyboardButton(text="⬅️ Kembali ke Akun Saya", callback_data="my_accounts")])

    await _safe_edit(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data.startswith("usr_act_kick:"))
async def cb_usr_act_kick(call: CallbackQuery, state: FSMContext):
    if (await get_setting("feat_self_manage")) != "1" and not await is_user_admin(call.from_user.id):
        await call.answer("⚠️ Fitur ini sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return
    acc_id = int(call.data.split(":", 1)[1])
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    ok_cd, wait_s = _check_self_cooldown(uid, f"kick_{acc_id}", cooldown_sec=45)
    if not ok_cd and not adm:
        await call.answer(f"⏳ Mohon tunggu {wait_s} detik sebelum me-reset sesi lagi.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, is_locked, COALESCE(server_id, 1), COALESCE(quota_gb, 0), COALESCE(used_bytes, 0) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, acc_type, uname, secret, is_locked, sid, q_gb, u_bytes = row
    quota_exceeded = (float(q_gb or 0) > 0 and int(u_bytes or 0) >= int(float(q_gb) * (1024**3)))

    await kick_user_sessions_on_server(sid, acc_type, uname, secret)

    unlocked_note = ""
    if is_locked == 1 and not quota_exceeded:
        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            await db.execute("UPDATE accounts SET is_locked = 0, locked_until = '' WHERE id = ?", (acc_id,))
            await db.commit()
        if acc_type == "ssh":
            await lock_ssh_on_server(sid, uname, lock=False, password=secret)
        else:
            await sync_xray_by_server(sid)
        unlocked_note = " & Kunci Limit IP dibuka kembali!"

    await call.answer(f"✅ Sesi '{uname}' berhasil di-reset{unlocked_note}", show_alert=True)
    await cb_usr_manage(call, state)

@router.callback_query(F.data.startswith("usr_act_cred:"))
async def cb_usr_act_cred(call: CallbackQuery, state: FSMContext):
    if (await get_setting("feat_self_manage")) != "1" and not await is_user_admin(call.from_user.id):
        await call.answer("⚠️ Fitur ini sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return
    acc_id = int(call.data.split(":", 1)[1])
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, acc_type, uname, sid = row
    if acc_type == "ssh":
        await state.set_state(SelfManagePassFSM.waiting_pass)
        await state.update_data(acc_id=acc_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Batal", callback_data=f"usr_manage:{acc_id}")]
        ])
        await _safe_edit(
            call,
            f"🔑 <b>Ganti Password SSH — {html.escape(uname)}</b>\n\n"
            "Ketik <b>Password Baru</b> yang Anda inginkan (4 - 32 karakter, tanpa spasi):",
            reply_markup=kb
        )
        await call.answer()
    else:
        ok_cd, wait_s = _check_self_cooldown(uid, f"cred_{acc_id}", cooldown_sec=120)
        if not ok_cd and not adm:
            await call.answer(f"⏳ Mohon tunggu {wait_s} detik sebelum mengganti UUID lagi.", show_alert=True)
            return
        new_uuid = str(uuid.uuid4())
        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            await db.execute("UPDATE accounts SET password_or_uuid = ? WHERE id = ?", (new_uuid, acc_id))
            await db.commit()
        await sync_xray_by_server(sid)
        await generate_portable_backup_sh()
        await call.answer("✅ UUID baru berhasil dibuat & diaktifkan!", show_alert=True)
        await _safe_edit(
            call,
            f"✅ <b>UUID AKUN {acc_type.upper()} BERHASIL DIGANTI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Username:</b> <code>{html.escape(uname)}</code>\n"
            f"• <b>UUID Baru:</b> <code>{new_uuid}</code>\n"
            "• <b>Jatah Kuota Buat Akun:</b> <code>Tidak Terpotong</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Silakan ambil config terbaru Anda melalui tombol di bawah:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="📄 Lihat Config Baru", callback_data=f"view_acc:{acc_id}"),
                    InlineKeyboardButton(text="🎯 Inject Bug / Clash", callback_data=f"bug_menu:{acc_id}")
                ],
                [InlineKeyboardButton(text="⬅️ Kembali ke Kelola Akun", callback_data=f"usr_manage:{acc_id}")]
            ])
        )

@router.message(SelfManagePassFSM.waiting_pass)
async def fsm_usr_new_ssh_pass(message: Message, state: FSMContext):
    data = await state.get_data()
    acc_id = int(data.get("acc_id", 0))
    new_pwd = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass

    if " " in new_pwd or not (4 <= len(new_pwd) <= 32):
        await message.answer("❌ Password tidak valid! Harus 4-32 karakter tanpa spasi.")
        return

    uid = message.from_user.id
    adm = await is_user_admin(uid)
    await state.clear()

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id, username, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await message.answer("❌ Akun tidak ditemukan.")
        return

    _, uname, sid = row
    if not await create_ssh_on_server(sid, uname, new_pwd):
        await message.answer("❌ Gagal memperbarui password di server.")
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE accounts SET password_or_uuid = ? WHERE id = ?", (new_pwd, acc_id))
        await db.commit()
    await generate_portable_backup_sh()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Lihat Detail & Config SSH", callback_data=f"view_acc:{acc_id}")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Kelola Akun", callback_data=f"usr_manage:{acc_id}")]
    ])
    await message.answer(
        f"✅ <b>Password SSH Berhasil Diperbarui!</b>\n"
        f"• <b>Username:</b> <code>{html.escape(uname)}</code>\n"
        f"• <b>Password Baru:</b> <code>{html.escape(new_pwd)}</code>",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("usr_act_proto:"))
async def cb_usr_act_proto(call: CallbackQuery):
    if (await get_setting("feat_self_manage")) != "1" and not await is_user_admin(call.from_user.id):
        await call.answer("⚠️ Fitur ini sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return
    acc_id = int(call.data.split(":", 1)[1])
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id, acc_type, username FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, cur_proto, uname = row
    btns = []
    for p in ("vless", "vmess", "trojan"):
        if p != cur_proto:
            btns.append([InlineKeyboardButton(text=f"⚡ Ubah ke {p.upper()}", callback_data=f"usr_set_proto:{acc_id}:{p}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data=f"usr_manage:{acc_id}")])

    await _safe_edit(
        call,
        f"⚡ <b>Tukar Protokol Mandiri — {html.escape(uname)}</b>\n"
        f"Protokol saat ini: <b>{cur_proto.upper()}</b>\n\n"
        "Pilih protokol tujuan di bawah ini (Masa aktif, UUID, dan sisa kuota GB tetap sama tanpa memotong kuota buat akun):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
    )
    await call.answer()

@router.callback_query(F.data.startswith("usr_set_proto:"))
async def cb_usr_set_proto(call: CallbackQuery):
    if (await get_setting("feat_self_manage")) != "1" and not await is_user_admin(call.from_user.id):
        await call.answer("⚠️ Fitur ini sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return
    _, acc_id_s, new_proto = call.data.split(":", 2)
    acc_id = int(acc_id_s)
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    if new_proto not in ("vless", "vmess", "trojan"):
        return

    ok_cd, wait_s = _check_self_cooldown(uid, f"proto_{acc_id}", cooldown_sec=120)
    if not ok_cd and not adm:
        await call.answer(f"⏳ Mohon tunggu {wait_s} detik sebelum menukar protokol lagi.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, expired_at, COALESCE(server_id, 1), COALESCE(quota_gb, 0), COALESCE(used_bytes, 0) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, old_proto, uname, exp_at, sid, q_gb, u_bytes = row
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE accounts SET acc_type = ? WHERE id = ?", (new_proto, acc_id))
        await db.commit()

    await sync_xray_by_server(sid)
    await generate_portable_backup_sh()
    q_str = f"{format_bytes_human(u_bytes)} / {'♾ Unlimited' if float(q_gb) <= 0 else str(q_gb)+' GB'}"

    text = (
        "✅ <b>BERHASIL TUKAR PROTOKOL AKUN!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Username:</b> <code>{html.escape(uname)}</code>\n"
        f"• <b>Perubahan:</b> <code>{old_proto.upper()}</code> → <b><code>{new_proto.upper()}</code></b>\n"
        f"• <b>Sisa Kuota:</b> <code>{q_str}</code> <i>(Tetap)</i>\n"
        f"• <b>Expired:</b> <code>{html.escape(exp_at)}</code> <i>(Tetap)</i>\n"
        "• <b>Jatah Kuota Buat Akun:</b> <code>Tidak Terpotong</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📄 Lihat Config Baru", callback_data=f"view_acc:{acc_id}"),
            InlineKeyboardButton(text="🎯 Inject Bug / Clash", callback_data=f"bug_menu:{acc_id}")
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Kelola Akun", callback_data=f"usr_manage:{acc_id}")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer(f"✅ Protokol diubah ke {new_proto.upper()}!")

@router.callback_query(F.data.startswith("usr_act_mig:"))
async def cb_usr_act_mig(call: CallbackQuery):
    if (await get_setting("feat_self_manage")) != "1" and not await is_user_admin(call.from_user.id):
        await call.answer("⚠️ Fitur ini sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return
    acc_id = int(call.data.split(":", 1)[1])
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id, username, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, uname, cur_sid = row
    servers = await get_servers_with_usage(only_active=True)
    avail = [s for s in servers if int(s["id"]) != int(cur_sid)]

    if not avail:
        await call.answer("ℹ️ Saat ini belum ada server tujuan lain yang aktif.", show_alert=True)
        return

    btns = []
    for s in avail:
        cap_s = f"{s['used_users']}/{'♾' if s['max_users'] == 0 else s['max_users']}"
        btns.append([InlineKeyboardButton(text=f"🖥 Pindah ke {s['name']} ({cap_s})", callback_data=f"usr_do_mig:{acc_id}:{s['id']}")])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data=f"usr_manage:{acc_id}")])

    await _safe_edit(
        call,
        f"🔀 <b>Pindah Server Mandiri — {html.escape(uname)}</b>\n\n"
        "Pilih server tujuan di bawah ini. Masa aktif, sisa kuota GB, dan UUID/Password Anda langsung dipindahkan tanpa memotong kuota buat akun:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
    )
    await call.answer()

@router.callback_query(F.data.startswith("usr_do_mig:"))
async def cb_usr_do_mig(call: CallbackQuery):
    if (await get_setting("feat_self_manage")) != "1" and not await is_user_admin(call.from_user.id):
        await call.answer("⚠️ Fitur ini sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return
    _, acc_id_s, new_sid_s = call.data.split(":", 2)
    acc_id, new_sid = int(acc_id_s), int(new_sid_s)
    uid = call.from_user.id
    adm = await is_user_admin(uid)

    ok_cd, wait_s = _check_self_cooldown(uid, f"mig_{acc_id}", cooldown_sec=300)
    if not ok_cd and not adm:
        await call.answer(f"⏳ Mohon tunggu {wait_s} detik sebelum pindah server lagi.", show_alert=True)
        return

    target_srv = await get_server_by_id(new_sid)
    if not target_srv or int(target_srv.get("is_active", 0)) == 0:
        await call.answer("❌ Server tujuan tidak tersedia.", show_alert=True)
        return
    if target_srv["max_users"] > 0 and target_srv["used_users"] >= target_srv["max_users"]:
        await call.answer("⚠️ Server tujuan sudah penuh!", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, expired_at, is_locked, COALESCE(server_id, 1), COALESCE(quota_gb, 0), COALESCE(used_bytes, 0) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, acc_type, uname, secret, exp_at, is_locked, old_sid, q_gb, u_bytes = row
    if int(old_sid) == new_sid:
        await call.answer("Akun sudah berada di server tersebut.", show_alert=True)
        return

    old_srv = await get_server_by_id(old_sid)
    old_name = old_srv["name"] if old_srv else f"Server #{old_sid}"

    await call.answer("⏳ Memindahkan akun ke server baru...")
    if acc_type == "ssh":
        if not await create_ssh_on_server(new_sid, uname, secret):
            await call.answer("❌ Gagal membuat akun SSH di server tujuan.", show_alert=True)
            return
        if is_locked == 1:
            await lock_ssh_on_server(new_sid, uname, lock=True)
        await delete_ssh_on_server(old_sid, uname)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("UPDATE accounts SET server_id = ? WHERE id = ?", (new_sid, acc_id))
        await db.commit()

    if acc_type in ("vless", "vmess", "trojan"):
        await sync_xray_by_server(old_sid)
        await sync_xray_by_server(new_sid)
    else:
        await sync_ssh_bw_rules_on_server(old_sid)
        await sync_ssh_bw_rules_on_server(new_sid)

    await generate_portable_backup_sh()
    q_str = f"{format_bytes_human(u_bytes)} / {'♾ Unlimited' if float(q_gb) <= 0 else str(q_gb)+' GB'}"

    text = (
        "✅ <b>BERHASIL PINDAH SERVER &amp; UPDATE CONFIG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Username:</b> <code>{html.escape(uname)}</code> ({acc_type.upper()})\n"
        f"• <b>Perubahan:</b> <code>{html.escape(old_name)}</code> → <b><code>{html.escape(target_srv['name'])}</code></b>\n"
        f"• <b>Domain Baru:</b> <code>{html.escape(target_srv['domain'])}</code>\n"
        f"• <b>Sisa Kuota:</b> <code>{q_str}</code> <i>(Tetap)</i>\n"
        f"• <b>Expired:</b> <code>{html.escape(exp_at)}</code> <i>(Tetap)</i>\n"
        "• <b>Jatah Kuota Buat Akun:</b> <code>Tidak Terpotong</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    row_cfg = [InlineKeyboardButton(text="📄 Lihat Config Server Baru", callback_data=f"view_acc:{acc_id}")]
    if acc_type in ("vless", "vmess", "trojan"):
        row_cfg.append(InlineKeyboardButton(text="🎯 Inject Bug / Clash", callback_data=f"bug_menu:{acc_id}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        row_cfg,
        [InlineKeyboardButton(text="⬅️ Kembali ke Kelola Akun", callback_data=f"usr_manage:{acc_id}")]
    ])
    await _safe_edit(call, text, reply_markup=kb)

# === HANDLER PRO BUG SCANNER, SING-BOX JSON, QR CODE & SMOOTH /START ===
import asyncio
import html
import json
import re
import time
import aiosqlite
from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, ReplyKeyboardRemove
)
from .config import DB_PATH, DOMAIN, ADMIN_ID, ADMIN_IDS
from .db import (
    get_setting, set_setting, is_user_admin, check_daily_creation_quota,
    get_effective_limit_ip, get_effective_duration, get_server_by_id,
    get_servers_with_usage
)
from .keyboards import main_menu_kb
from .states import BugCheckerFSM
from .formatter import (
    build_injected_xray_html, get_cached_inject_payload, generate_qr_image_bytes,
    parse_external_xray_link
)
from .system_manager import (
    check_bug_host_isp, bulk_check_bug_hosts, generate_portable_backup_sh
)

_USER_SCAN_LOCKS: dict[int, asyncio.Lock] = {}
_USER_SCAN_LAST_TS: dict[int, float] = {}
_USER_LAST_SCANNED_BUG: dict[int, tuple[str, str]] = {}
_LAST_MENU_MSG_MAP: dict[int, int] = {}
_TEMP_QR_MSG_MAP: dict[int, list[int]] = {}


def _get_scan_lock(uid: int) -> asyncio.Lock:
    lk = _USER_SCAN_LOCKS.get(uid)
    if lk is None:
        lk = asyncio.Lock()
        _USER_SCAN_LOCKS[uid] = lk
    return lk

async def _ensure_user_db(uid: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute(
            """
            INSERT INTO users (tg_id, username, full_name, is_admin)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
            """,
            (uid, username or "", full_name or "", 1 if (int(uid) == int(ADMIN_ID) or int(uid) in ADMIN_IDS) else 0)
        )
        await db.commit()

async def _cleanup_temp_qrs(bot, chat_id: int):
    qrs = _TEMP_QR_MSG_MAP.pop(chat_id, [])
    for mid in qrs:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass










@router.callback_query(F.data == "close_qr_msg")
async def cb_close_qr_msg(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()

@router.callback_query(F.data == "bug_checker_start")
async def cb_bug_checker_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    if (await get_setting("feat_bug_checker")) != "1" and not adm:
        await call.answer("⚠️ Fitur Pro Bug Scanner sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return

    bulk_on = (await get_setting("feat_bug_bulk")) == "1" or adm
    cd_sec = int(await get_setting("bug_scan_cooldown") or 20)

    await state.set_state(BugCheckerFSM.waiting_host)
    await state.update_data(prompt_msg_id=call.message.message_id)
    _LAST_MENU_MSG_MAP[call.message.chat.id] = call.message.message_id

    bulk_note = (
        "• <b>Mode Bulk Scan (Multi-Bug):</b> <code>🟢 Aktif</code> (Kirim hingga 6 domain sekaligus dipisahkan spasi/enter).\n"
        if bulk_on else
        "• <b>Mode Scan:</b> <code>Single Host</code> (1 domain per pengecekan).\n"
    )
    text = (
        "🔍 <b>PRO BUG &amp; PAYLOAD SCANNER ISP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Fitur ini menguji secara langsung apakah suatu <b>Domain / IP Bug</b> dapat tembus <b>WebSocket 101 Switching Protocols</b> ke server VPN bot ini, memeriksa ASN, Ping, serta membongkar Subdomain SSL (SANs).\n\n"
        "🛡 <b>Aturan Anti-Spam:</b>\n"
        f"{bulk_note}"
        f"• <b>Jeda Cooldown User:</b> <code>{cd_sec} Detik / Scan</code>\n\n"
        "📌 <b>Ketik 1 atau beberapa Domain / IP Bug sekarang:</b>\n"
        "Contoh: <code>quiz.vidio.com</code> atau daftar bug ke bawah."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Kembali ke Menu Utama", callback_data="menu_main")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer()

@router.message(BugCheckerFSM.waiting_host)
async def fsm_bug_checker_scan(message: Message, state: FSMContext):
    uid = message.from_user.id
    chat_id = message.chat.id
    adm = await is_user_admin(uid)

    data_st = await state.get_data()
    prompt_mid = data_st.get("prompt_msg_id")

    raw_input = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass

    if (await get_setting("feat_bug_checker")) != "1" and not adm:
        await state.clear()
        await message.answer("⚠️ Fitur Pro Bug Scanner sedang dinonaktifkan oleh Administrator.")
        return

    lock = _get_scan_lock(uid)
    if lock.locked():
        return

    cd_sec = int(await get_setting("bug_scan_cooldown") or 20)
    now_t = time.time()
    last_t = _USER_SCAN_LAST_TS.get(uid, 0.0)
    if not adm and (now_t - last_t) < cd_sec:
        rem = int(cd_sec - (now_t - last_t))
        await message.answer(f"🛡 <b>Anti-Spam Cooldown:</b> Mohon tunggu <code>{rem} detik</code> lagi.")
        return

    tokens = [t for t in re.split(r"[\s,;]+", raw_input) if t.strip()]
    if not tokens:
        return

    bulk_allowed = (await get_setting("feat_bug_bulk")) == "1" or adm

    async with lock:
        _USER_SCAN_LAST_TS[uid] = time.time()
        await state.clear()

        wait_msg = None
        if prompt_mid:
            try:
                await message.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=prompt_mid,
                    text=f"⏳ <i>Memindai DNS, ASN, SSL SANs &amp; Simulasi Payload 101 WS pada</i> <code>{html.escape(tokens[0][:50])}</code>..."
                )
                wait_msg = prompt_mid
            except Exception:
                wait_msg = None

        if wait_msg is None:
            sent_w = await message.answer(
                f"⏳ <i>Memindai DNS, ASN, SSL SANs &amp; Simulasi Payload 101 WS pada</i> <code>{html.escape(tokens[0][:50])}</code>..."
            )
            wait_msg = sent_w.message_id
        _LAST_MENU_MSG_MAP[chat_id] = wait_msg

        if len(tokens) > 1 and bulk_allowed:
            max_h = 12 if adm else 6
            results = await bulk_check_bug_hosts(raw_input, max_hosts=max_h)
            if not results:
                await message.bot.edit_message_text(chat_id=chat_id, message_id=wait_msg, text="❌ Tidak ada format domain/IP yang valid.")
                return

            lines = [
                f"📡 <b>HASIL PRO BULK SCAN ({len(results)} HOST)</b>",
                "━━━━━━━━━━━━━━━━━━━━━━"
            ]
            first_good_host = None
            first_good_mode = "wildcard"

            for idx, r in enumerate(results, 1):
                h_name = html.escape(r.get("host", "-"))
                if not r.get("ok"):
                    lines.append(f"{idx}. 🔴 <code>{h_name}</code> — <b>Mati / NXDOMAIN</b>")
                    continue

                if not first_good_host:
                    first_good_host = r["host"]
                    first_good_mode = r.get("best_mode", "wildcard")

                p_ms = f"{r['ping_ms']}ms" if r.get("ping_ms", -1) >= 0 else "Timeout"
                wc_tag = "🔥101" if r.get("ws_wc_ok") else ("☁️CF" if r.get("is_cf") else "❌")
                cdn_tag = "🔥101" if r.get("ws_cdn_ok") else "❌"
                sni_tag = "🟢SSL" if r.get("tls_ok") else "❌"
                rec_lbl = r.get("best_mode", "sni").upper()

                lines.append(
                    f"{idx}. <b><code>{h_name}</code></b> (⚡<code>{p_ms}</code>)\n"
                    f"   ├ IP: <code>{r['ips'][0]}</code> | Server: <code>{html.escape(r['server'][:18])}</code>\n"
                    f"   └ Tes: [WC: {wc_tag}] [CDN: {cdn_tag}] [SNI: {sni_tag}] 👉 <b>{rec_lbl}</b>"
                )

            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <i>Ketik 1 domain saja jika ingin melihat bedah detail SSL SANs &amp; ASN lengkap.</i>")

            btns = []
            if first_good_host:
                _USER_LAST_SCANNED_BUG[uid] = (first_good_host, first_good_mode)
                btns.append([InlineKeyboardButton(
                    text=f"⚡ Inject '{first_good_host[:18]}' ke Akun Saya",
                    callback_data=f"scan_use_myacc:{first_good_mode}:{first_good_host[:32]}"
                )])
            btns.append([
                InlineKeyboardButton(text="🔍 Scan Bug Lagi", callback_data="bug_checker_start"),
                InlineKeyboardButton(text="🏠 Menu Utama", callback_data="menu_main")
            ])
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=wait_msg,
                text="\n".join(lines),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
            )
            return

        single_host = tokens[0]
        res = await check_bug_host_isp(single_host)

        if not res.get("ok"):
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Coba Host Lain", callback_data="bug_checker_start")],
                [InlineKeyboardButton(text="⬅️ Menu Utama", callback_data="menu_main")]
            ])
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=wait_msg,
                text=f"❌ <b>Gagal Memindai Bug:</b>\n{res.get('error')}",
                reply_markup=kb
            )
            return

        host = res["host"]
        best_mode = res.get("best_mode", "wildcard")
        _USER_LAST_SCANNED_BUG[uid] = (host, best_mode)

        ips_str = ", ".join([f"<code>{ip}</code>" for ip in res["ips"]])
        ping_str = f"⚡ {res['ping_ms']} ms" if res.get("ping_ms", -1) >= 0 else "Timeout"
        asn_str = html.escape(res.get("asn") or "-")
        srv_hdr = html.escape(res.get("server") or "-")
        cf_badge = f"✅ Cloudflare (CF-Ray: {html.escape(res['cf_ray'])})" if res["cf_ray"] else ("✅ Jaringan Cloudflare" if res["is_cf"] else "❌ Non-Cloudflare")
        ssl_info = f"{res['tls_ver']} | ALPN: {res['alpn']}" if res["tls_ok"] else "Tidak Aktif"

        if res.get("sans"):
            sans_preview = ", ".join([f"<code>{html.escape(s)}</code>" for s in res["sans"][:5]])
            extra_cnt = res.get("sans_total", 0) - len(res["sans"][:5])
            if extra_cnt > 0:
                sans_preview += f" <i>(+{extra_cnt} lainnya)</i>"
        else:
            sans_preview = "<i>Tidak ada SANs terdeteksi</i>"

        if res["ws_wc_ok"]:
            rec_wc = "✅ <b>WILDCARD:</b> <b>100% TEMBUS (101 WS OK)</b> ke Server Bot!"
        elif res["is_cf"]:
            rec_wc = "✅ <b>WILDCARD:</b> <b>Cocok!</b> (Berada di jaringan Cloudflare)."
        else:
            rec_wc = "❌ <b>WILDCARD:</b> Tidak cocok (Bukan proxy Cloudflare)."

        if res["ws_cdn_ok"]:
            rec_cdn = "✅ <b>CDN DIRECT:</b> <b>100% TEMBUS (101 WS OK)</b> sebagai Address!"
        elif res["is_cf"]:
            rec_cdn = "⚠️ <b>CDN DIRECT:</b> Cloudflare aktif, namun respons WS bukan 101."
        else:
            rec_cdn = "❌ <b>CDN DIRECT:</b> Tidak bisa meneruskan trafik WS ke VPS."

        if res["tls_ok"]:
            rec_sni = f"✅ <b>SNI / SSL:</b> <b>100% Cocok!</b> Gunakan <code>SNI = {html.escape(host)}</code>."
        else:
            rec_sni = "❌ <b>SNI / SSL:</b> Port 443 tidak mendukung handshake TLS."

        out_txt = (
            "🔍 <b>HASIL PRO SCAN BUG &amp; SIMULASI PAYLOAD</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 <b>Host / Bug:</b> <code>{html.escape(host)}</code>\n"
            f"📡 <b>IPv4 Address:</b> {ips_str}\n"
            f"🏢 <b>Jaringan ASN:</b> <code>{asn_str}</code> (<code>{ping_str}</code>)\n"
            f"🖥 <b>Server Header:</b> <code>{srv_hdr}</code>\n"
            f"🛡 <b>Status CDN:</b> <code>{cf_badge}</code>\n"
            f"🔐 <b>Info SSL/TLS:</b> <code>{html.escape(ssl_info)}</code>\n"
            f"🧩 <b>Subdomain SSL:</b> {sans_preview}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧪 <b>[ SIMULASI PAYLOAD KE {html.escape(res['target_domain'].upper())} ]</b>\n"
            f"• <b>HTTP Biasa (80):</b> <code>{html.escape(res['http_status'])}</code>\n"
            f"• <b>HTTPS / TLS (443):</b> <code>{html.escape(res['https_status'])}</code>\n"
            f"• <b>Tes WS Wildcard:</b> <code>{html.escape(res['ws_wc_status'])}</code>\n"
            f"• <b>Tes WS Direct CDN:</b> <code>{html.escape(res['ws_cdn_status'])}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <b>Kesimpulan &amp; Rekomendasi:</b>\n"
            f"• {rec_wc}\n"
            f"• {rec_cdn}\n"
            f"• {rec_sni}"
        )

        h_short = host[:34]
        btns = [
            [
                InlineKeyboardButton(text=f"⚡ Langsung Inject ({best_mode.upper()})", callback_data=f"scan_use_myacc:{best_mode}:{h_short}"),
                InlineKeyboardButton(text="🛠 Inject Config Luar", callback_data="ext_bug_start")
            ]
        ]
        if adm:
            btns.append([
                InlineKeyboardButton(text="➕ Simpan Wildcard", callback_data=f"adm_save_scan_bug:wildcard:{h_short}"),
                InlineKeyboardButton(text="➕ Simpan SNI", callback_data=f"adm_save_scan_bug:sni:{h_short}"),
                InlineKeyboardButton(text="➕ Simpan CDN", callback_data=f"adm_save_scan_bug:cdn:{h_short}"),
            ])
        btns.append([
            InlineKeyboardButton(text="🔍 Scan Bug Lain", callback_data="bug_checker_start"),
            InlineKeyboardButton(text="🏠 Menu Utama", callback_data="menu_main")
        ])
        await message.bot.edit_message_text(
            chat_id=chat_id,
            message_id=wait_msg,
            text=out_txt,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
        )

@router.callback_query(F.data.startswith("scan_use_myacc"))
async def cb_scan_use_myacc(call: CallbackQuery):
    uid = call.from_user.id
    parts = call.data.split(":")
    last_info = _USER_LAST_SCANNED_BUG.get(uid)

    if len(parts) >= 3 and parts[2]:
        best_mode = parts[1]
        bug_host = parts[2]
        _USER_LAST_SCANNED_BUG[uid] = (bug_host, best_mode)
    elif last_info:
        bug_host, best_mode = last_info
    elif call.message and call.message.text:
        m_h = re.search(r"Host / Bug:\s*([a-zA-Z0-9.-]+)", call.message.text)
        bug_host = m_h.group(1).strip().lower() if m_h else ""
        best_mode = "sni" if "SNI / SSL: 100%" in call.message.text else "wildcard"
        if bug_host:
            _USER_LAST_SCANNED_BUG[uid] = (bug_host, best_mode)
    else:
        bug_host, best_mode = "", "wildcard"

    if not bug_host:
        await call.answer("⚠️ Data hasil scan sudah habis, silakan scan ulang.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT id, acc_type, username FROM accounts WHERE tg_id = ? AND acc_type IN ('vless', 'vmess', 'trojan') AND is_locked = 0 ORDER BY id DESC",
            (uid,)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await call.answer("⚠️ Anda belum memiliki akun Xray (VLESS/VMess/Trojan) aktif.", show_alert=True)
        return

    btns = []
    for acc_id, atype, uname in rows[:10]:
        btns.append([InlineKeyboardButton(
            text=f"⚡ Pasang ke {uname} ({atype.upper()} - {best_mode.upper()})",
            callback_data=f"scan_apply_acc:{acc_id}:{best_mode}:{bug_host[:30]}"
        )])
    btns.append([InlineKeyboardButton(text="⬅️ Kembali ke Menu Utama", callback_data="menu_main")])

    await _safe_edit(
        call,
        f"⚡ <b>LANGSUNG INJECT BUG HASIL SCAN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Bug Host:</b> <code>{html.escape(bug_host)}</code>\n"
        f"• <b>Metode Terbaik:</b> <code>{best_mode.upper()}</code>\n\n"
        "Pilih akun Xray Anda di bawah ini untuk langsung menghasilkan Config TLS, NTLS, Clash YAML, Sing-box JSON &amp; QR Code:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
    )
    await call.answer()

@router.callback_query(F.data.startswith("scan_apply_acc:"))
async def cb_scan_apply_acc(call: CallbackQuery):
    uid = call.from_user.id
    parts = call.data.split(":")
    acc_id = int(parts[1])
    mode = parts[2] if len(parts) > 2 else "wildcard"
    bug_from_cb = parts[3] if len(parts) > 3 else ""

    last_info = _USER_LAST_SCANNED_BUG.get(uid)
    bug_host = bug_from_cb or (last_info[0] if last_info else "")
    if not bug_host:
        await call.answer("⚠️ Sesi bug hasil scan sudah habis.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or (row[0] != uid and not await is_user_admin(uid)):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    _, proto, remark, secret, sid = row
    srv = await get_server_by_id(sid)
    s_name = srv["name"] if srv else f"Server #{sid}"
    s_dom = srv["domain"] if srv else DOMAIN

    text, kb = build_injected_xray_html(
        proto=proto,
        remark=remark,
        secret=secret,
        server_name=s_name,
        domain=s_dom,
        bug=bug_host,
        mode=mode,
        acc_id=acc_id
    )
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer(f"✅ Bug {bug_host} berhasil di-inject!")

@router.callback_query(F.data.startswith("adm_save_scan_bug:"))
async def cb_adm_save_scan_bug(call: CallbackQuery):
    uid = call.from_user.id
    if not await is_user_admin(uid):
        await call.answer("⛔ Khusus Administrator.", show_alert=True)
        return

    parts = call.data.split(":")
    mode = parts[1] if len(parts) > 1 else "wildcard"
    host_from_cb = parts[2].strip() if len(parts) > 2 else ""

    last_info = _USER_LAST_SCANNED_BUG.get(uid)
    bug_host = host_from_cb or (last_info[0] if last_info else "")

    if not bug_host and call.message and call.message.text:
        m_h = re.search(r"Host / Bug:\s*([a-zA-Z0-9.-]+)", call.message.text)
        if m_h:
            bug_host = m_h.group(1).strip().lower()

    if not bug_host:
        await call.answer("⚠️ Nama bug tidak ditemukan, silakan scan ulang.", show_alert=True)
        return

    key_map = {
        "wildcard": "bug_presets_wildcard",
        "sni": "bug_presets_sni",
        "cdn": "bug_presets_cdn",
    }
    db_key = key_map.get(mode, "bug_presets_wildcard")
    cur_raw = await get_setting(db_key) or ""
    existing = [p.strip() for p in re.split(r"[,\n]+", cur_raw) if p.strip()]
    existing_doms = {p.split("=", 1)[1].strip().lower() if "=" in p else p.lower() for p in existing}

    if bug_host.lower() in existing_doms:
        await call.answer(f"ℹ️ Bug '{bug_host}' sudah ada di daftar Preset {mode.upper()}!", show_alert=True)
        return

    existing.append(bug_host)
    await set_setting(db_key, "\n".join(existing))
    await generate_portable_backup_sh()

    if call.message and call.message.reply_markup:
        new_kb_rows = []
        for row in call.message.reply_markup.inline_keyboard:
            new_row = []
            for btn in row:
                if getattr(btn, "callback_data", "") == call.data:
                    new_row.append(InlineKeyboardButton(text=f"✅ Tersimpan ({mode.upper()})", callback_data=call.data))
                else:
                    new_row.append(btn)
            new_kb_rows.append(new_row)
        try:
            await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_kb_rows))
        except Exception:
            pass

    await call.answer(f"✅ Bug '{bug_host}' berhasil disimpan ke Preset {mode.upper()}!", show_alert=True)

# === SMOOTH /START (PHOTO + TEXT), STATUS BARU & SYNC SERVER INFO ===
from aiogram.types import FSInputFile
from .system_manager import refresh_all_servers_live_stats

WELCOME_BANNER_PATH = "/root/tgbot-vpn/welcome_banner.jpg"









# Pastikan /start dan menu_main selalu berada di prioritas paling atas (index 0)



from .menu_tracker import (
    get_chat_nav_lock, cleanup_previous_menus, track_bot_msg, untrack_bot_msg
)

from .menu_tracker import (
    get_chat_nav_lock, cleanup_previous_menus, track_bot_msg, untrack_bot_msg
)

async def _safe_edit(call: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    if not call.message:
        return None
    chat_id = call.message.chat.id
    old_mid = call.message.message_id
    try:
        # Jika pesan sebelumnya adalah Foto, KIRIM pesan teks baru DULU, baru hapus foto lama
        if call.message.photo:
            sent = await call.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            track_bot_msg(chat_id, sent.message_id)
            try:
                await call.message.delete()
            except Exception:
                pass
            return sent
        res = await call.message.edit_text(text, reply_markup=reply_markup)
        track_bot_msg(chat_id, old_mid)
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

async def _send_welcome_screen(bot, chat_id: int, uid: int, full_name: str, edit_call: CallbackQuery | None = None):
    text, kb = await _build_welcome_text_and_kb(uid, full_name, escape_custom=False)
    photo_id = (await get_setting("welcome_photo_id") or "").strip()
    has_local_banner = os.path.exists(WELCOME_BANNER_PATH) and os.path.getsize(WELCOME_BANNER_PATH) > 0

    # 1. Jika menggunakan Foto Banner: KIRIM foto baru DULU, baru hapus pesan lama
    if photo_id or has_local_banner:
        sent = None
        try:
            photo_arg = photo_id if photo_id else FSInputFile(WELCOME_BANNER_PATH)
            sent = await bot.send_photo(chat_id=chat_id, photo=photo_arg, caption=text, reply_markup=kb)
        except Exception:
            try:
                text_safe, kb = await _build_welcome_text_and_kb(uid, full_name, escape_custom=True)
                if has_local_banner:
                    sent = await bot.send_photo(chat_id=chat_id, photo=FSInputFile(WELCOME_BANNER_PATH), caption=text_safe, reply_markup=kb)
            except Exception:
                sent = None

        if sent:
            track_bot_msg(chat_id, sent.message_id)
            if edit_call and edit_call.message and edit_call.message.message_id != sent.message_id:
                try:
                    await edit_call.message.delete()
                except Exception:
                    pass
            return sent

    # 2. Jika mode Teks dan berasal dari tombol kembali (pesan teks biasa): Edit langsung di tempat
    if edit_call and edit_call.message and not edit_call.message.photo:
        try:
            res = await edit_call.message.edit_text(text, reply_markup=kb)
            track_bot_msg(chat_id, edit_call.message.message_id)
            return edit_call.message
        except Exception as e:
            if "message is not modified" in str(e).lower():
                track_bot_msg(chat_id, edit_call.message.message_id)
                return edit_call.message
            try:
                text_safe, kb = await _build_welcome_text_and_kb(uid, full_name, escape_custom=True)
                await edit_call.message.edit_text(text_safe, reply_markup=kb)
                track_bot_msg(chat_id, edit_call.message.message_id)
                return edit_call.message
            except Exception:
                pass

    # 3. Kirim pesan teks baru TERLEBIH DAHULU, baru hapus pesan lama (jika ada)
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
    except Exception:
        text_safe, kb = await _build_welcome_text_and_kb(uid, full_name, escape_custom=True)
        sent = await bot.send_message(chat_id=chat_id, text=text_safe, reply_markup=kb)

    track_bot_msg(chat_id, sent.message_id)
    if edit_call and edit_call.message and edit_call.message.message_id != sent.message_id:
        try:
            await edit_call.message.delete()
        except Exception:
            pass
    return sent





# === HANDLER SMART SUBSCRIPTION LINK (AUTO-UPDATE CLASH / SING-BOX / V2RAYNG) ===
from .sub_server import get_sub_urls
from .system_manager import rank_servers_by_health



from .ui_theme import get_ui_theme_sync, make_capacity_bar

async def _build_welcome_text_and_kb(uid: int, full_name: str, escape_custom: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    adm = await is_user_admin(uid)
    _, used, max_lim, reset_str = await check_daily_creation_quota(uid)
    lim_ip = await get_effective_limit_ip(uid)
    lim_str = "Unlimited" if lim_ip == 0 else f"{lim_ip} IP"
    dur_str = await get_effective_duration(uid)
    win_raw = (await get_setting("create_limit_window") or "1d").strip()
    if "/" in win_raw:
        win_raw = win_raw.split("/", 1)[1].strip()

    if max_lim <= 0:
        reset_disp = "♾ Unlimited"
        quota_line_str = f"♾ Unlimited (Terpakai: {used})"
        max_str_placeholder = "♾"
    else:
        if str(reset_str).lower().startswith("per "):
            reset_disp = f"Periode {reset_str[4:].strip()}"
        elif used == 0:
            reset_disp = f"Periode {win_raw}"
        else:
            reset_disp = f"Periode {win_raw} • {reset_str}"
        quota_line_str = f"{used}/{max_lim} (Reset: {reset_disp})"
        max_str_placeholder = str(max_lim)

    custom_wel = ""
    for w_key in ("welcome_text", "welcome_message", "welcome_msg"):
        val = (await get_setting(w_key) or "").strip()
        if val:
            custom_wel = val
            break

    safe_name = html.escape(full_name or "Pengguna")
    if custom_wel:
        base_wel = html.escape(custom_wel) if escape_custom else custom_wel
        wel_body = (
            base_wel
            .replace("{name}", safe_name)
            .replace("{full_name}", safe_name)
            .replace("{id}", str(uid))
            .replace("{used}", str(used))
            .replace("{max}", max_str_placeholder)
            .replace("{reset}", str(reset_disp))
        )
    else:
        wel_body = "Selamat datang di <b>Layanan Manajemen Akun VPN Otomatis</b>."

    wib_str = now_wib().strftime("%Y-%m-%d %H:%M WIB")
    theme = get_ui_theme_sync()

    if theme == "tree":
        status_box = (
            "╭─── 📊 <b>STATUS &amp; HAK AKSES ANDA</b>\n"
            f"├─ 🕒 <b>Waktu Server:</b> <code>{wib_str}</code>\n"
            f"├─ 📊 <b>Batas Buat Akun:</b> <code>{quota_line_str}</code>\n"
            f"╰─ ⏳ <b>Masa Aktif Akun:</b> <code>{dur_str}</code> | <b>Limit IP:</b> <code>{lim_str}</code>"
        )
    elif theme == "compact":
        status_box = (
            "<blockquote>📊 <b>STATUS &amp; HAK AKSES ANDA</b>\n"
            f"🕒 <b>Waktu Server:</b> <code>{wib_str}</code>\n"
            f"📊 <b>Batas Buat Akun:</b> <code>{quota_line_str}</code>\n"
            f"⏳ <b>Masa Aktif Akun:</b> <code>{dur_str}</code> | <b>Limit IP:</b> <code>{lim_str}</code></blockquote>"
        )
    else:
        status_box = (
            "📊 <b>Status &amp; Hak Akses Anda:</b>\n"
            f"🕒 <b>Waktu Server:</b> <code>{wib_str}</code>\n"
            f"📊 <b>Batas Buat Akun:</b> <code>{quota_line_str}</code>\n"
            f"⏳ <b>Masa Aktif Akun:</b> <code>{dur_str}</code> | <b>Limit IP:</b> <code>{lim_str}</code>"
        )

    text = (
        f"👋 Halo, <b>{safe_name}</b>!\n"
        f"{wel_body}\n\n"
        f"{status_box}\n\n"
        "Silakan pilih menu di bawah ini:"
    )
    return text, main_menu_kb(adm)

@router.callback_query(F.data.in_({"server_info", "server_info_refresh"}))
async def cb_server_info_enhanced(call: CallbackQuery):
    uid = call.from_user.id
    is_force = (call.data == "server_info_refresh")
    if is_force:
        await call.answer("🔄 Menyinkronkan Traffic & vnStat terbaru...")
        await refresh_all_servers_live_stats(force=True)
    else:
        await call.answer()
        asyncio.create_task(refresh_all_servers_live_stats(force=False))

    lim_ip = await get_effective_limit_ip(uid)
    lim_str = "Unlimited" if lim_ip == 0 else f"{lim_ip} IP / Akun"
    _, used, max_lim, _ = await check_daily_creation_quota(uid)
    servers = await get_servers_with_usage(only_active=True)

    win_raw = await get_setting("create_limit_window") or "1d"
    win_label = _format_window_indo(win_raw)
    reg_dur = await get_effective_duration(uid)
    reg_dur_label = "Unlimited" if reg_dur.lower() in ("0", "unlimited") else _format_window_indo(reg_dur)
    trial_dur_raw = await get_setting("trial_duration") or "30m"
    trial_dur_label = _format_window_indo(trial_dur_raw)
    def_q_gb = float(await get_setting("default_quota_gb") or 0)
    def_q_str = "Unlimited" if def_q_gb <= 0 else f"{def_q_gb:g} GB"
    def_spd = int(await get_setting("default_speed_mbps") or 0)
    def_spd_str = "Unlimited" if def_spd <= 0 else f"{def_spd} Mbps"
    lock_min = await get_setting("lock_duration_minutes") or "15"
    max_viol = await get_setting("max_violations") or "3"

    srv_bw_map: dict[int, int] = {}
    srv_proto_map: dict[int, dict[str, int]] = {}
    acc_srv_owner: dict[str, int] = {}

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT COALESCE(server_id, 1), acc_type, username, COALESCE(used_bytes, 0) FROM accounts WHERE is_locked = 0"
        ) as cur:
            for sid, atype, uname, ubytes in await cur.fetchall():
                sid = int(sid or 1)
                srv_bw_map[sid] = srv_bw_map.get(sid, 0) + int(ubytes or 0)
                pmap = srv_proto_map.setdefault(sid, {"ssh": 0, "vless": 0, "vmess": 0, "trojan": 0})
                if atype in pmap:
                    pmap[atype] += 1
                acc_srv_owner[uname] = sid

    ssh_on = get_ssh_active_sessions()
    xray_on = get_xray_active_ips()
    online_per_srv: dict[int, int] = {}
    for uname, sid_u in acc_srv_owner.items():
        has_ssh = bool(ssh_on.get(uname))
        has_xray = bool(xray_on.get(uname))
        if has_ssh or has_xray:
            online_per_srv[sid_u] = online_per_srv.get(sid_u, 0) + 1

    theme = get_ui_theme_sync()
    wib_now_str = now_wib().strftime("%Y-%m-%d %H:%M WIB")
    srv_blocks = []

    for s in servers:
        sid = int(s["id"])
        nstat = get_cluster_node_stats(sid)
        st_ico = "🟢 Online" if nstat.get("online", True) else "🔴 Offline"
        u_cnt = int(s.get("used_users") or 0)
        m_cnt = int(s.get("max_users") or 0)
        max_u_lbl = "♾" if m_cnt == 0 else str(m_cnt)
        cap_num_str = f"{u_cnt}/{max_u_lbl}"
        bar_str = make_capacity_bar(u_cnt, m_cnt, width=10, theme=theme)
        pmap = srv_proto_map.get(sid, {"ssh": 0, "vless": 0, "vmess": 0, "trojan": 0})
        used_bw_str = format_bytes_human(srv_bw_map.get(sid, 0))
        on_cnt = online_per_srv.get(sid, 0)
        s_title = html.escape(s["name"].upper())
        s_dom = html.escape(s["domain"])

        if theme == "tree":
            srv_blocks.append(
                f"╭─── 🖥 <b>{s_title}</b> ({st_ico})\n"
                f"├─ 🌐 <b>Domain   :</b> <code>{s_dom}</code>\n"
                f"├─ 👥 <b>Kapasitas:</b> <code>{bar_str}</code> (<code>{cap_num_str} Akun</code>)\n"
                f"├─ ⚡ <b>Status   :</b> 🟢 <code>{on_cnt} Online</code> | 📦 <code>{used_bw_str}</code>\n"
                f"├─ 🔑 <b>Protokol :</b> <code>SSH:{pmap['ssh']} • VL:{pmap['vless']} • VM:{pmap['vmess']} • TR:{pmap['trojan']}</code>\n"
                f"├─ 📊 <b>Live Rate:</b> ⬇ <code>{nstat['in_mbps']}</code> | ⬆ <code>{nstat['out_mbps']}</code>\n"
                f"╰─ 📈 <b>Bandwidth:</b> 🗓 <code>{nstat['vnstat_day']}</code> (Day) | 📆 <code>{nstat['vnstat_mth']}</code> (Mth)"
            )
        elif theme == "compact":
            srv_blocks.append(
                f"🖥 <b>{html.escape(s['name'])}</b> ({st_ico}) — 🟢 <code>{on_cnt} Online</code>\n"
                f"<blockquote expandable>🌐 <b>Domain:</b> <code>{s_dom}</code>\n"
                f"👥 <b>Kapasitas:</b> <code>{bar_str}</code> (<code>{cap_num_str} Akun</code>)\n"
                f"📦 <b>Kuota User:</b> <code>{used_bw_str}</code>\n"
                f"🔑 <b>Akun:</b> <code>SSH:{pmap['ssh']} | VL:{pmap['vless']} | VM:{pmap['vmess']} | TR:{pmap['trojan']}</code>\n"
                f"📊 <b>Live Rate:</b> ⬇ <code>{nstat['in_mbps']}</code> | ⬆ <code>{nstat['out_mbps']}</code>\n"
                f"📈 <b>vnStat:</b> Day <code>{nstat['vnstat_day']}</code> | Mth <code>{nstat['vnstat_mth']}</code></blockquote>"
            )
        else:
            srv_blocks.append(
                f"🖥 <b>{html.escape(s['name'])}</b> ({st_ico})\n"
                f"🌐 <b>Domain:</b> <code>{s_dom}</code>\n"
                f"👥 <b>Kapasitas:</b> <code>{bar_str}</code> (<code>{cap_num_str} Akun</code> • 🟢 <code>{on_cnt} Online</code>)\n"
                f"📦 <b>Pemakaian Kuota User:</b> <code>{used_bw_str}</code>\n"
                f"🔑 <b>Rincian Akun:</b> <code>SSH:{pmap['ssh']} | VL:{pmap['vless']} | VM:{pmap['vmess']} | TR:{pmap['trojan']}</code>\n\n"
                f"📊 <b>[ TRAFFIC RATE (LIVE) ]</b>\n"
                f"⬇ <b>IN   :</b> <code>{nstat['in_mbps']}</code>\n"
                f"⬆ <b>OUT  :</b> <code>{nstat['out_mbps']}</code>\n\n"
                f"📈 <b>[ VNSTAT BANDWIDTH ]</b>\n"
                f"🗓 <b>Day  :</b> <code>{nstat['vnstat_day']}</code>\n"
                f"📆 <b>Mth  :</b> <code>{nstat['vnstat_mth']}</code>"
            )

    if max_lim <= 0:
        q_rule_val = f"♾ Unlimited (Terpakai: {used}/♾)"
    else:
        q_rule_val = f"Maks {max_lim} Akun / {win_label} (Terpakai: {used}/{max_lim})"

    if theme == "tree":
        rules_block = (
            "╭─── 📜 <b>ATURAN &amp; KETENTUAN SERVER</b>\n"
            f"├─ • <b>Jatah Buat Akun:</b> <code>{q_rule_val}</code>\n"
            f"├─ • <b>Masa Aktif:</b> <code>{reg_dur_label}</code> | <b>Trial:</b> <code>{trial_dur_label}</code>\n"
            f"├─ • <b>Kuota &amp; Speed:</b> <code>{def_q_str}</code> | <code>{def_spd_str}</code>\n"
            f"├─ • <b>Batas Login:</b> <code>{lim_str}</code> (Auto-Lock <code>{lock_min}m</code>, Maks <code>{max_viol}x</code>)\n"
            "╰─ • <b>Larangan Keras:</b> <code>Dilarang Torrent, DDoS, Spamming &amp; Multi-Login!</code>"
        )
        body_joined = "\n\n".join(srv_blocks) if srv_blocks else "<i>Belum ada server aktif.</i>"
        text = (
            "📊 <b>DATA PEMAKAIAN &amp; INFORMASI SERVER</b>\n"
            f"🕒 <b>Waktu Server:</b> <code>{wib_now_str}</code>\n\n"
            f"{body_joined}\n\n"
            f"{rules_block}"
        )
    elif theme == "compact":
        rules_block = (
            "📜 <b>ATURAN &amp; KETENTUAN SERVER</b>\n"
            f"<blockquote expandable>• <b>Jatah Buat Akun:</b> <code>{q_rule_val}</code>\n"
            f"• <b>Masa Aktif Reguler:</b> <code>{reg_dur_label}</code> | <b>Trial:</b> <code>{trial_dur_label}</code>\n"
            f"• <b>Kuota Bandwidth:</b> <code>{def_q_str}</code> | <b>Speed Limit:</b> <code>{def_spd_str}</code>\n"
            f"• <b>Batas Login (Limit IP):</b> <code>{lim_str}</code>\n"
            f"• <b>Sanksi Multi-Login:</b> <code>Auto-Lock {lock_min} Menit (Maks {max_viol}x Pelanggaran)</code>\n"
            "• <b>Larangan Keras:</b> <code>Dilarang Torrent, DDoS, Spamming &amp; Multi-Login!</code></blockquote>"
        )
        body_joined = "\n\n".join(srv_blocks) if srv_blocks else "<i>Belum ada server aktif.</i>"
        text = (
            "📊 <b>DATA PEMAKAIAN &amp; INFORMASI SERVER</b>\n"
            f"🕒 <b>Waktu Server:</b> <code>{wib_now_str}</code>\n\n"
            f"{body_joined}\n\n"
            f"{rules_block}"
        )
    else:
        rules_block = (
            "📜 <b>[ ATURAN &amp; KETENTUAN SERVER ]</b>\n"
            f"• <b>Jatah Buat Akun:</b> <code>{q_rule_val}</code>\n"
            f"• <b>Masa Aktif Reguler:</b> <code>{reg_dur_label}</code> | <b>Trial:</b> <code>{trial_dur_label}</code>\n"
            f"• <b>Kuota Bandwidth:</b> <code>{def_q_str}</code> | <b>Speed Limit:</b> <code>{def_spd_str}</code>\n"
            f"• <b>Batas Login (Limit IP):</b> <code>{lim_str}</code>\n"
            f"• <b>Sanksi Multi-Login:</b> <code>Auto-Lock {lock_min} Menit (Maks {max_viol}x Pelanggaran)</code>\n"
            "• <b>Larangan Keras:</b> <code>Dilarang Torrent, DDoS, Spamming &amp; Multi-Login!</code>"
        )
        text = (
            "📊 <b>DATA PEMAKAIAN &amp; INFORMASI SERVER</b>\n"
            f"🕒 <b>Waktu Server:</b> <code>{wib_now_str}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            + ("\n━━━━━━━━━━━━━━━━━━━━━━\n\n".join(srv_blocks) if srv_blocks else "<i>Belum ada server aktif.</i>\n")
            + "\n━━━━━━━━━━━━━━━━━━━━━━\n"
            + rules_block
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Refresh Traffic", callback_data="server_info_refresh"),
            InlineKeyboardButton(text="🌐 List Wildcard", callback_data="user_wildcard_list")
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Menu Utama", callback_data="menu_main")]
    ])
    await _safe_edit(call, text, reply_markup=kb)

# === HANDLER /START NORMAL, QR CODE ANTI-GAGAL & FITUR UPDATE SUBSCRIPTION ===
from .sub_server import (
    get_sub_urls, get_sub_pref, save_sub_pref, build_subscription_payload
)
from .system_manager import rank_servers_by_health, sync_xray_by_server

async def cmd_start(message: Message, state: FSMContext | None = None):
    uid = message.from_user.id if message.from_user else message.chat.id
    chat_id = message.chat.id
    full_name = (message.from_user.full_name if message.from_user else "") or "Pengguna"
    uname = (message.from_user.username if message.from_user else "") or ""

    if state:
        try:
            await state.clear()
        except Exception:
            pass

    await _ensure_user_db(uid, uname, full_name)
    # Langsung kirim menu /start baru tanpa menghapus pesan atau menu sebelumnya
    await _send_welcome_screen(message.bot, chat_id, uid, full_name, edit_call=None)

async def cb_menu_main(call: CallbackQuery, state: FSMContext | None = None):
    uid = call.from_user.id
    chat_id = call.message.chat.id if call.message else uid
    full_name = call.from_user.full_name or "Pengguna"

    if state:
        try:
            await state.clear()
        except Exception:
            pass

    await _ensure_user_db(uid, call.from_user.username or "", full_name)
    await _send_welcome_screen(call.bot, chat_id, uid, full_name, edit_call=call)
    try:
        await call.answer()
    except Exception:
        pass

def _recover_inject_item_from_msg(msg_text: str, ckey: str) -> dict | None:
    if not msg_text:
        return None
    try:
        is_ext = False
        acc_id = 0
        ck_parts = ckey.split("_")
        if len(ck_parts) >= 3 and ck_parts[0].isdigit() and ck_parts[1].isdigit():
            is_ext = (int(ck_parts[0]) == 1)
            acc_id = int(ck_parts[1])

        m_srv = re.search(r"Server\s*:\s*(.+?)\s*\(([^)]+)\)", msg_text)
        srv_name = m_srv.group(1).strip() if m_srv else "Server Utama"
        dom = m_srv.group(2).strip() if m_srv else DOMAIN

        m_met = re.search(r"Metode\s*:\s*(.+)", msg_text)
        met_raw = (m_met.group(1).lower() if m_met else "")
        if "wildcard" in met_raw:
            mode = "wildcard"
        elif "sni" in met_raw:
            mode = "sni"
        else:
            mode = "cdn"

        m_addr = re.search(r"Address(?:\s*\(Bug\))?\s*:\s*(\S+)", msg_text)
        m_sni = re.search(r"(?:Subdomain\s*\(SNI/Host\)|SNI\s*/\s*Host|SNI/Host)\s*:\s*(\S+)", msg_text)
        addr_val = m_addr.group(1).strip() if m_addr else dom
        sni_val = m_sni.group(1).strip() if m_sni else dom
        bug = sni_val if mode == "sni" else addr_val

        if '"outbounds"' in msg_text:
            j_start = msg_text.find("{")
            j_end = msg_text.rfind("}")
            if j_start != -1 and j_end != -1:
                j_obj = json.loads(msg_text[j_start:j_end + 1])
                ob = j_obj.get("outbounds", [{}])[0]
                proto = ob.get("type", "vless")
                tag = ob.get("tag", "User-TLS")
                remark = re.sub(r"-(TLS|NTLS)$", "", tag, flags=re.IGNORECASE)
                secret = ob.get("uuid") or ob.get("password") or ""
                path_raw = ob.get("transport", {}).get("path", f"/{proto}")
                if secret:
                    build_injected_xray_html(
                        proto=proto, remark=remark, secret=secret,
                        server_name=srv_name, domain=dom, bug=bug,
                        mode=mode, acc_id=acc_id, custom_path=path_raw,
                        is_external=is_ext, cache_id=ckey
                    )
                    return get_cached_inject_payload(ckey)

        parsed = parse_external_xray_link(msg_text)
        if parsed:
            build_injected_xray_html(
                proto=parsed["proto"], remark=parsed["remark"], secret=parsed["secret"],
                server_name=srv_name, domain=dom, bug=bug,
                mode=mode, acc_id=acc_id, custom_path=parsed["path"],
                is_external=is_ext, cache_id=ckey
            )
            return get_cached_inject_payload(ckey)
    except Exception as e:
        print(f"[RECOVER CACHE ERROR] {e}")
    return None

async def _resolve_inject_item_Guaranteed(call: CallbackQuery, ckey: str) -> dict | None:
    item = get_cached_inject_payload(ckey)
    if item:
        return item
    if call.message and (call.message.text or call.message.caption):
        item = _recover_inject_item_from_msg(call.message.text or call.message.caption or "", ckey)
        if item:
            return item

    # Fallback langsung ke SQLite jika acc_id tercatat di ckey
    ck_parts = ckey.split("_")
    if len(ck_parts) >= 2 and ck_parts[1].isdigit() and int(ck_parts[1]) > 0:
        acc_id = int(ck_parts[1])
        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            async with db.execute(
                "SELECT acc_type, username, password_or_uuid, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
                (acc_id,)
            ) as cur:
                row = await cur.fetchone()
        if row:
            proto, remark, secret, sid = row
            srv = await get_server_by_id(int(sid))
            s_name = srv["name"] if srv else f"Server #{sid}"
            s_dom = srv["domain"] if srv else DOMAIN
            pref = await get_sub_pref(acc_id)
            b_host = pref.get("bug_host") or s_dom
            b_mode = pref.get("bug_mode") or "wildcard"
            build_injected_xray_html(
                proto=proto, remark=remark, secret=secret,
                server_name=s_name, domain=s_dom, bug=b_host,
                mode=b_mode, acc_id=acc_id, cache_id=ckey
            )
            return get_cached_inject_payload(ckey)
    return None

@router.callback_query(F.data.startswith("inj_sb:"))
async def cb_inj_singbox(call: CallbackQuery):
    ckey = call.data.split(":", 1)[1]
    item = await _resolve_inject_item_Guaranteed(call, ckey)
    if not item:
        await call.answer("⚠️ Sesi config sudah berganti, silakan pilih ulang bug.", show_alert=True)
        return

    text = (
        f"╭─── 📦 <b>CONFIG SING-BOX / NEKOBOX ({item['proto'].upper()})</b>\n"
        f"├─ 🌐 <b>Server   :</b> <code>{html.escape(item['server_name'])} ({html.escape(item['domain'])})</code>\n"
        f"├─ ⚡ <b>Metode   :</b> <code>{html.escape(item['mode_label'])}</code>\n"
        f"├─ 🎯 <b>Address  :</b> <code>{html.escape(item['address'])}</code>\n"
        f"╰─ 🔒 <b>SNI/Host :</b> <code>{html.escape(item['subdomain'])}</code>\n\n"
        "📋 <b>JSON Outbounds (TLS 443 &amp; NTLS 80):</b>\n"
        f"<pre><code class=\"language-json\">{html.escape(item['singbox_block'])}</code></pre>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 QR TLS (443)", callback_data=f"inj_qr:tls:{ckey}"),
            InlineKeyboardButton(text="📱 QR NTLS (80)", callback_data=f"inj_qr:ntls:{ckey}")
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Tampilan TLS / Clash YAML", callback_data=f"inj_back:{ckey}")]
    ])
    await _safe_edit(call, text, reply_markup=kb)
    await call.answer("📦 Format Sing-box JSON ditampilkan!")

@router.callback_query(F.data.startswith("inj_back:"))
async def cb_inj_back_full(call: CallbackQuery, state: FSMContext):
    ckey = call.data.split(":", 1)[1]
    item = await _resolve_inject_item_Guaranteed(call, ckey)
    if item and item.get("full_text") and item.get("full_kb"):
        await _safe_edit(call, item["full_text"], reply_markup=item["full_kb"])
        await call.answer("⬅️ Kembali ke tampilan TLS / Clash YAML")
        return
    await call.answer("ℹ️ Kembali ke daftar akun Anda...")
    await cb_my_accounts(call, state)

@router.callback_query(F.data.startswith("inj_qr:"))
async def cb_inj_qrcode(call: CallbackQuery):
    _, kind, ckey = call.data.split(":", 2)
    item = await _resolve_inject_item_Guaranteed(call, ckey)
    if not item:
        await call.answer("⚠️ Sesi QR sudah berganti, silakan pilih ulang bug.", show_alert=True)
        return

    uri_str = item["uri_tls"] if kind == "tls" else item["uri_ntls"]
    port_lbl = "TLS (Port 443)" if kind == "tls" else "NTLS (Port 80)"

    png_bytes = await asyncio.to_thread(generate_qr_image_bytes, uri_str)
    if not png_bytes:
        await call.answer("❌ Gagal membuat gambar QR Code.", show_alert=True)
        return

    await call.answer(f"📱 Menampilkan QR Code {port_lbl}...")
    cap = (
        f"╭─── 📱 <b>QR CODE {item['proto'].upper()} — {port_lbl}</b>\n"
        f"├─ 🔑 <b>Akun    :</b> <code>{html.escape(item['remark'])}</code>\n"
        f"├─ ⚡ <b>Metode  :</b> <code>{html.escape(item['mode_label'])}</code>\n"
        f"├─ 🎯 <b>Address :</b> <code>{html.escape(item['address'])}</code>\n"
        f"╰─ 🔒 <b>SNI/Host:</b> <code>{html.escape(item['subdomain'])}</code>\n\n"
        "💡 <i>Scan QR ini langsung di aplikasi v2rayNG, NekoBox, atau Streisand.</i>"
    )
    kb_qr = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Tutup QR Code", callback_data="close_qr_msg")]
    ])
    await call.message.answer_photo(
        photo=BufferedInputFile(png_bytes, filename=f"qr-{item['remark']}-{kind}.png"),
        caption=cap,
        reply_markup=kb_qr
    )

async def _render_smart_sub_screen(acc_id: int, uid: int, adm: bool, bug_override: str | None = None, mode_override: str | None = None) -> tuple[str | None, InlineKeyboardMarkup | None, str]:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, expired_at, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (int(acc_id),)
        ) as cur:
            row = await cur.fetchone()

    if not row or (int(row[0]) != uid and not adm):
        return None, None, "⚠️ Akun tidak ditemukan."

    owner_id, acc_type, uname, exp_str, sid = row
    if acc_type == "ssh":
        return None, None, "ℹ️ Fitur Smart Subscription khusus untuk akun Xray (VLESS / VMess / Trojan)."

    if bug_override is not None:
        pref = await save_sub_pref(acc_id, bug_host=bug_override, bug_mode=(mode_override or "wildcard"))
    else:
        pref = await get_sub_pref(acc_id)
        if not pref.get("updated_at"):
            pref = await save_sub_pref(acc_id)

    b_host = pref.get("bug_host") or ""
    b_mode = pref.get("bug_mode") or "wildcard"
    upd_at = pref.get("updated_at") or now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")

    srv = await get_server_by_id(int(sid))
    s_name = srv["name"] if srv else f"Server #{sid}"
    s_dom = srv["domain"] if srv else DOMAIN

    urls = get_sub_urls(s_dom, acc_id, int(owner_id), bug=b_host, mode=(b_mode if b_host else ""))
    sub_info = await build_subscription_payload(acc_id, target_kind="v2ray", custom_bug=b_host, custom_mode=b_mode)
    node_cnt = sub_info["node_count"] if sub_info else 0

    bug_disp = f"{b_host} ({b_mode.upper()})" if b_host else "Otomatis Semua Preset Bug Aktif"

    text = (
        "╭─── 🔗 <b>SMART SUBSCRIPTION (AUTO-UPDATE)</b>\n"
        f"├─ 🔑 <b>Akun         :</b> <code>{html.escape(uname)}</code> ({acc_type.upper()})\n"
        f"├─ 🖥 <b>Server Aktif :</b> <code>{html.escape(s_name)} ({html.escape(s_dom)})</code>\n"
        f"├─ 🎯 <b>Bug Prioritas:</b> <code>{html.escape(bug_disp)}</code>\n"
        f"├─ 📡 <b>Total Node   :</b> <code>{node_cnt} Variasi Node (TLS/NTLS + Preset)</code>\n"
        f"├─ 🔄 <b>Last Update  :</b> <code>{html.escape(upd_at)}</code>\n"
        f"╰─ ⏳ <b>Expired      :</b> <code>{html.escape(str(exp_str))}</code>\n\n"
        "📱 <b>1. Universal / v2rayNG / Streisand (Base64):</b>\n"
        f"<pre><code class=\"language-url\">{html.escape(urls['v2ray'])}</code></pre>\n\n"
        "📄 <b>2. OpenClash / Mihomo / Clash (YAML):</b>\n"
        f"<pre><code class=\"language-url\">{html.escape(urls['clash'])}</code></pre>\n\n"
        "📦 <b>3. Sing-box / NekoBox / SFI (JSON):</b>\n"
        f"<pre><code class=\"language-url\">{html.escape(urls['singbox'])}</code></pre>\n\n"
        "💡 <i>Klik <b>🔄 Update Subscription Sekarang</b> untuk menyinkronkan perubahan server/protokol/bug terbaru, atau unduh file config siap pakai di bawah ini:</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Update Subscription Sekarang", callback_data=f"sub_refresh:{acc_id}")],
        [
            InlineKeyboardButton(text="🎯 Atur Bug Subscription", callback_data=f"sub_bug_menu:{acc_id}"),
            InlineKeyboardButton(text="🛠 Kelola Mandiri", callback_data=f"self_mgr:{acc_id}")
        ],
        [
            InlineKeyboardButton(text="📥 File Clash (.yaml)", callback_data=f"sub_dl:clash:{acc_id}"),
            InlineKeyboardButton(text="📥 File Sing-box (.json)", callback_data=f"sub_dl:singbox:{acc_id}")
        ],
        [
            InlineKeyboardButton(text="📱 QR Sub v2rayNG", callback_data=f"sub_qr:v2ray:{acc_id}"),
            InlineKeyboardButton(text="📱 QR Sub Sing-box", callback_data=f"sub_qr:singbox:{acc_id}")
        ],
        [InlineKeyboardButton(text="⬅️ Kembali ke Akun Saya", callback_data="my_accounts")]
    ])
    return text, kb, ""

@router.callback_query(F.data.startswith("sub_link:"))
async def cb_show_smart_sub_link(call: CallbackQuery):
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    if (await get_setting("feat_smart_sub")) == "0" and not adm:
        await call.answer("⚠️ Fitur Smart Subscription Link sedang dinonaktifkan oleh Administrator.", show_alert=True)
        return

    parts = call.data.split(":")
    acc_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    bug_opt = parts[2] if len(parts) > 2 and parts[2] else None
    mode_opt = parts[3] if len(parts) > 3 and parts[3] else None

    text, kb, err = await _render_smart_sub_screen(acc_id, uid, adm, bug_override=bug_opt, mode_override=mode_opt)
    if not text:
        await call.answer(err, show_alert=True)
        return

    await _safe_edit(call, text, reply_markup=kb)
    await call.answer("🔗 Menu Smart Subscription ditampilkan!")

@router.callback_query(F.data.startswith("sub_refresh:"))
async def cb_sub_refresh_now(call: CallbackQuery):
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    acc_id = int(call.data.split(":", 1)[1])

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT tg_id, COALESCE(server_id, 1) FROM accounts WHERE id = ?", (acc_id,)) as cur:
            row = await cur.fetchone()
    if not row or (int(row[0]) != uid and not adm):
        await call.answer("⚠️ Akun tidak ditemukan.", show_alert=True)
        return

    sid = int(row[1])
    # Sinkronkan ulang akun ke Xray server & perbarui waktu update subscription
    await sync_xray_by_server(sid)
    await save_sub_pref(acc_id)

    text, kb, err = await _render_smart_sub_screen(acc_id, uid, adm)
    if text:
        await _safe_edit(call, text, reply_markup=kb)
    await call.answer("✅ Subscription berhasil di-update & disinkronkan ke Server Xray!", show_alert=True)

@router.callback_query(F.data.startswith("sub_bug_menu:"))
async def cb_sub_bug_menu(call: CallbackQuery):
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    acc_id = int(call.data.split(":", 1)[1])

    pref = await get_sub_pref(acc_id)
    cur_b = pref.get("bug_host") or "Otomatis Semua Preset"
    cur_m = pref.get("bug_mode") or "wildcard"

    btns = [
        [InlineKeyboardButton(text="🔄 Otomatis Semua Preset Bug (Default)", callback_data=f"sub_set_bug:{acc_id}:all:")],
    ]
    cat_map = [
        ("wildcard", "🌐 WC", await get_setting("bug_presets_wildcard") or ""),
        ("sni", "🔒 SNI", await get_setting("bug_presets_sni") or ""),
        ("cdn", "☁️ CDN", await get_setting("bug_presets_cdn") or ""),
    ]
    for mode_k, badge, raw_list in cat_map:
        row_b = []
        for part in re.split(r"[,\n]+", raw_list):
            part = part.strip()
            if not part:
                continue
            lbl, dom = (part.split("=", 1) if "=" in part else (part, part))
            lbl, dom = lbl.strip(), dom.strip().lower()
            if not dom:
                continue
            cb_d = f"sub_set_bug:{acc_id}:{mode_k}:{dom[:28]}"
            row_b.append(InlineKeyboardButton(text=f"{badge}: {lbl[:14]}", callback_data=cb_d[:64]))
            if len(row_b) == 2:
                btns.append(row_b)
                row_b = []
        if row_b:
            btns.append(row_b)

    btns.append([InlineKeyboardButton(text="⬅️ Kembali ke Smart Subscription", callback_data=f"sub_link:{acc_id}")])

    text = (
        "🎯 <b>ATUR BUG PRIORITAS SMART SUBSCRIPTION</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Bug Aktif Saat Ini:</b> <code>{html.escape(cur_b)} ({html.escape(cur_m.upper())})</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Pilih salah satu <b>Preset Bug</b> di bawah ini untuk dijadikan prioritas utama di dalam link Subscription Anda.\n\n"
        "💡 <i>Setelah memilih bug di bawah, URL Subscription Anda tidak berubah dan otomatis memuat bug baru tersebut!</i>"
    )
    await _safe_edit(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await call.answer()

@router.callback_query(F.data.startswith("sub_set_bug:"))
async def cb_sub_set_bug(call: CallbackQuery):
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    parts = call.data.split(":", 3)
    acc_id = int(parts[1])
    mode_k = parts[2] if len(parts) > 2 else "wildcard"
    dom_v = parts[3] if len(parts) > 3 else ""

    if mode_k == "all" or not dom_v:
        await save_sub_pref(acc_id, bug_host="", bug_mode="wildcard")
        msg_alert = "✅ Subscription diatur ke: Otomatis Semua Preset Bug!"
    else:
        await save_sub_pref(acc_id, bug_host=dom_v, bug_mode=mode_k)
        msg_alert = f"✅ Bug Prioritas Subscription diubah ke: {dom_v} ({mode_k.upper()})!"

    text, kb, _ = await _render_smart_sub_screen(acc_id, uid, adm)
    if text:
        await _safe_edit(call, text, reply_markup=kb)
    await call.answer(msg_alert, show_alert=True)

@router.callback_query(F.data.startswith("sub_dl:"))
async def cb_sub_download_file(call: CallbackQuery):
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    _, target_kind, acc_id_s = call.data.split(":", 2)
    acc_id = int(acc_id_s)

    res = await build_subscription_payload(acc_id, target_kind=target_kind)
    if not res or (res["tg_id"] != uid and not adm):
        await call.answer("⚠️ Akun tidak ditemukan.", show_alert=True)
        return

    await call.answer(f"📥 Mengirim file {res['filename']}...")
    cap = (
        f"📦 <b>FILE SMART SUBSCRIPTION ({target_kind.upper()})</b>\n"
        f"• <b>Akun:</b> <code>{html.escape(res['uname'])}</code> ({res['acc_type'].upper()})\n"
        f"• <b>Total Variasi Node:</b> <code>{res['node_count']} Node</code>\n"
        "💡 <i>Impor file ini langsung ke OpenClash / Mihomo / Sing-box / NekoBox.</i>"
    )
    await call.message.answer_document(
        document=BufferedInputFile(res["content"].encode("utf-8"), filename=res["filename"]),
        caption=cap
    )

@router.callback_query(F.data.startswith("sub_qr:"))
async def cb_show_smart_sub_qr(call: CallbackQuery):
    uid = call.from_user.id
    adm = await is_user_admin(uid)
    _, target_kind, acc_id_s = call.data.split(":", 2)
    acc_id = int(acc_id_s)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (acc_id,)
        ) as cur:
            row = await cur.fetchone()

    if not row or (int(row[0]) != uid and not adm):
        await call.answer("Akun tidak ditemukan.", show_alert=True)
        return

    owner_id, acc_type, uname, sid = row
    srv = await get_server_by_id(int(sid))
    s_dom = srv["domain"] if srv else DOMAIN
    pref = await get_sub_pref(acc_id)
    b_host = pref.get("bug_host") or ""
    b_mode = pref.get("bug_mode") or "wildcard"

    urls = get_sub_urls(s_dom, acc_id, int(owner_id), bug=b_host, mode=(b_mode if b_host else ""))
    target_url = urls.get(target_kind, urls["v2ray"])

    png_bytes = await asyncio.to_thread(generate_qr_image_bytes, target_url)
    if not png_bytes:
        await call.answer("❌ Gagal membuat gambar QR Code.", show_alert=True)
        return

    await call.answer(f"📱 Menampilkan QR Subscription ({target_kind.upper()})...")
    cap = (
        f"╭─── 📱 <b>QR SMART SUBSCRIPTION ({target_kind.upper()})</b>\n"
        f"├─ 🔑 <b>Akun:</b> <code>{html.escape(uname)}</code> ({acc_type.upper()})\n"
        f"╰─ 🔗 <b>URL :</b> <code>{html.escape(target_url)}</code>\n\n"
        "💡 <i>Scan QR ini di menu Subscription v2rayNG, Streisand, atau Sing-box.</i>"
    )
    kb_qr = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Tutup QR Code", callback_data="close_qr_msg")]
    ])
    await call.message.answer_photo(
        photo=BufferedInputFile(png_bytes, filename=f"sub-{uname}-{target_kind}.png"),
        caption=cap,
        reply_markup=kb_qr
    )

for _h in list(router.callback_query.handlers):
    if getattr(_h.callback, "__name__", "") in (
        "cb_menu_main", "cb_inj_singbox", "cb_inj_back_full", "cb_inj_qrcode",
        "cb_show_smart_sub_link", "cb_sub_refresh_now", "cb_sub_bug_menu",
        "cb_sub_set_bug", "cb_sub_download_file", "cb_show_smart_sub_qr"
    ):
        router.callback_query.handlers.remove(_h)
        router.callback_query.handlers.insert(0, _h)