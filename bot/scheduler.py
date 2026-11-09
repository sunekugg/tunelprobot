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
import aiosqlite
from aiogram import Bot
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from .config import DB_PATH, ADMIN_ID, now_wib
from .db import (
    get_setting, get_servers_with_usage, add_log
)
from .system_manager import (
    refresh_all_servers_live_stats,
    lock_ssh_on_server, delete_ssh_on_server, sync_xray_by_server,
    generate_portable_backup_sh, format_bytes_human, run_auto_heal_and_monitor
)

async def _periodic_tg_auto_backup(bot: Bot):
    await asyncio.sleep(45)
    while True:
        try:
            if (await get_setting("auto_backup_tg")) == "1":
                sh_path = await generate_portable_backup_sh()
                async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                    u_cnt = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
                    ssh_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type = 'ssh'")).fetchone())[0]
                    xray_cnt = (await (await db.execute("SELECT COUNT(*) FROM accounts WHERE acc_type != 'ssh'")).fetchone())[0]
                wib_str = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")
                cap = (
                    "⏰ <b>AUTO-BACKUP DATABASE VPN (TERJADWAL)</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 <b>Waktu:</b> <code>{wib_str}</code>\n"
                    f"👥 <b>Total User:</b> <code>{u_cnt}</code>\n"
                    f"🔑 <b>Akun Aktif:</b> <code>{ssh_cnt} SSH | {xray_cnt} Xray</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>Simpan file ini untuk restore otomatis saat rebuild VPS.</i>"
                )
                await bot.send_document(
                    chat_id=ADMIN_ID,
                    document=FSInputFile(sh_path, filename="backup-data-vpn.sh"),
                    caption=cap
                )
        except Exception as e:
            print(f"[AUTO-BACKUP LOOP ERROR] {e}")
        await asyncio.sleep(6 * 3600)

async def _periodic_auto_heal_loop(bot: Bot):
    await asyncio.sleep(60)
    while True:
        try:
            if (await get_setting("feat_auto_heal")) == "1":
                await run_auto_heal_and_monitor(bot, manual_trigger=False)
        except Exception as e:
            print(f"[AUTO-HEAL LOOP ERROR] {e}")
        await asyncio.sleep(120)

async def scheduler_loop(bot: Bot):
    asyncio.create_task(_periodic_tg_auto_backup(bot))
    asyncio.create_task(_periodic_auto_heal_loop(bot))
    from .login_notifier import periodic_online_report_loop
    asyncio.create_task(periodic_online_report_loop(bot))

    # Pasang daftar command /start & /menu di tombol samping kolom chat Telegram
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="🚀 Buka Menu Utama Bot"),
            BotCommand(command="menu", description="📋 Tampilkan Menu Utama"),
        ])
    except Exception:
        pass

    # Beri jeda 3 detik saat startup agar bot langsung merespons /start tanpa tertahan
    await asyncio.sleep(3)

    kb_start_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Buka Menu Utama (/start)", callback_data="menu_main")]
    ])

    while True:
        try:
            # 1. Tarik bandwidth, vnStat, dan daftar IP aktif dari semua server secara non-blocking
            await refresh_all_servers_live_stats(force=True)

            # 1b. (notifikasi login per-event tidak lagi dikirim ke topic — hanya report berkala)
            # (ssh_sessions/xray_ips untuk deteksi multi-login sudah tak dibutuhkan —
            # fitur penegakan multi-login dihapus.)

            now_dt = now_wib().replace(tzinfo=None)
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

            servers_list = await get_servers_with_usage(only_active=False)
            srv_map = {int(s["id"]): s["name"] for s in servers_list}

            async with aiosqlite.connect(DB_PATH, timeout=20) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM accounts") as cur:
                    all_accounts = [dict(r) for r in await cur.fetchall()]

            expired_ids: list[int] = []
            affected_xray_srvs: set[int] = set()
            backup_needed = False

            # 2. Periksa Akun Expired, Buka Kunci Otomatis & Cek Kuota GB
            for acc in all_accounts:
                aid = int(acc["id"])
                uid = int(acc["tg_id"])
                atype = acc["acc_type"]
                uname = acc["username"]
                secret = acc["password_or_uuid"]
                exp_str = str(acc.get("expired_at") or "").replace("WIB", "").strip()
                is_locked = int(acc.get("is_locked") or 0)
                locked_until = str(acc.get("locked_until") or "").strip()
                sid = int(acc.get("server_id") or 1)
                q_gb = float(acc.get("quota_gb") or 0)
                u_bytes = int(acc.get("used_bytes") or 0)
                created_at = str(acc.get("created_at") or "-")
                s_name = srv_map.get(sid, f"Server #{sid}")

                # A. Cek Masa Aktif (Expired)
                if exp_str and exp_str.lower() != "unlimited":
                    exp_dt = None
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                        try:
                            exp_dt = datetime.datetime.strptime(exp_str, fmt)
                            break
                        except Exception:
                            continue
                    if exp_dt and now_dt >= exp_dt:
                        expired_ids.append(aid)
                        if atype == "ssh":
                            await delete_ssh_on_server(sid, uname)
                        else:
                            affected_xray_srvs.add(sid)
                        try:
                            # Toggle notif expired ke user
                            if (await get_setting("notif_expired_user")) == "1":
                                msg = (
                                    "⏰ <b>Pemberitahuan Akun Expired</b>\n\n"
                                    f"• <b>Akun:</b> <code>{html.escape(uname)}</code> ({atype.upper()})\n"
                                    f"• <b>Server:</b> <code>{html.escape(s_name)}</code>\n"
                                    f"• <b>Tanggal Buat:</b> <code>{html.escape(created_at)} WIB</code>\n"
                                    f"• <b>Tanggal Expired:</b> <code>{html.escape(exp_str)} WIB</code>\n"
                                    f"• <b>Keterangan:</b> Lewat waktu expired ({now_str} WIB)\n\n"
                                    "Akun telah dihapus otomatis dari server."
                                )
                                await bot.send_message(chat_id=uid, text=msg, reply_markup=kb_start_btn)
                        except Exception:
                            pass
                        continue

                # B. Cek Kuota Bandwidth Habis
                if q_gb > 0 and u_bytes >= int(q_gb * (1024**3)) and is_locked == 0:
                    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                        await db.execute(
                            "UPDATE accounts SET is_locked = 1, locked_until = 'QUOTA_EXCEEDED' WHERE id = ?",
                            (aid,)
                        )
                        await db.commit()
                    if atype == "ssh":
                        await lock_ssh_on_server(sid, uname, lock=True)
                    else:
                        affected_xray_srvs.add(sid)
                    backup_needed = True
                    try:
                        await bot.send_message(
                            chat_id=uid,
                            text=(
                                "📦 <b>Pemberitahuan Kuota Bandwidth Habis</b>\n\n"
                                f"• <b>Akun:</b> <code>{html.escape(uname)}</code> ({atype.upper()})\n"
                                f"• <b>Pemakaian:</b> <code>{format_bytes_human(u_bytes)} / {q_gb:g} GB</code>\n\n"
                                "Akun Anda telah dikunci otomatis karena melewati batas kuota."
                            ),
                            reply_markup=kb_start_btn
                        )
                    except Exception:
                        pass
                    continue

                # (Buka-kunci otomatis hukuman multi-login dihapus — fitur multi-login
                # sudah dihapus. Kuota tetap dikunci 'QUOTA_EXCEEDED' sampai admin reset.)

            if expired_ids:
                async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                    placeholders = ",".join(["?"] * len(expired_ids))
                    await db.execute(f"DELETE FROM accounts WHERE id IN ({placeholders})", expired_ids)
                    await db.commit()
                backup_needed = True

            # 3. (Penegakan Limit IP Multi-Login telah dihapus atas permintaan user —
            # laporan penggunaan tetap dikirim ke topic grup.)

            # 4. Penegakan Kuota Bandwidth per-Server (GB)
            # Total pemakaian server = jumlah used_bytes seluruh akun di server tsb
            # (sama dgn cara panel statistik menjumlahkannya). Jika melewati
            # servers.quota_gb (>0), matikan server (is_active=0) agar tak bisa
            # dibuat akun baru & beri tahu admin. Server =0 berarti Unlimited.
            srv_rows = await get_servers_with_usage(only_active=False)
            srv_totals: dict[int, int] = {}
            async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                async with db.execute(
                    "SELECT COALESCE(server_id, 1), SUM(COALESCE(used_bytes, 0)) FROM accounts GROUP BY COALESCE(server_id, 1)"
                ) as cur:
                    for r in await cur.fetchall():
                        srv_totals[int(r[0])] = int(r[1] or 0)
                for s in srv_rows:
                    sq = int(s["id"])
                    srv_quota_gb = float(s.get("quota_gb") or 0)
                    if srv_quota_gb <= 0:
                        continue
                    srv_used = srv_totals.get(sq, 0)
                    if srv_used >= int(srv_quota_gb * (1024**3)):
                        await db.execute("UPDATE servers SET is_active = 0 WHERE id = ?", (sq,))
                        await db.commit()
                        affected_xray_srvs.add(sq)
                        try:
                            await bot.send_message(
                                chat_id=ADMIN_ID,
                                text=(
                                    "🛑 <b>Kuota Bandwidth Server Habis</b>\n\n"
                                    f"• <b>Server:</b> <code>{html.escape(str(s['name']))}</code>\n"
                                    f"• <b>Pemakaian:</b> <code>{format_bytes_human(srv_used)} / {srv_quota_gb:g} GB</code>\n\n"
                                    "Server dimatikan otomatis (tidak bisa menerima akun baru) karena melewati batas kuota bandwidth."
                                ),
                            )
                        except Exception:
                            pass

            for x_sid in affected_xray_srvs:
                await sync_xray_by_server(x_sid)

            if backup_needed:
                await generate_portable_backup_sh()

        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")

        await asyncio.sleep(20)