from __future__ import annotations
import datetime
import aiosqlite
from .config import DB_PATH, now_wib
from .db import get_setting
from .system_manager import (
    parse_duration, delete_ssh_on_server, sync_xray_by_server, generate_portable_backup_sh
)

def parse_wib_dt(dt_str: str) -> datetime.datetime | None:
    if not dt_str:
        return None
    cleaned = dt_str.replace("WIB", "").strip()
    if cleaned.lower() in ("unlimited", "0", "-"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None

async def purge_accounts(
    bot=None,
    mode: str = "expired",
    custom_limit_str: str = ""
) -> tuple[int, list[str]]:
    now = now_wib()
    trial_dur_str = await get_setting("trial_duration") or "30m"
    def_dur_str = await get_setting("default_duration") or "7d"
    max_dur_str = await get_setting("max_duration") or "30d"

    trial_td = parse_duration(trial_dur_str) or datetime.timedelta(minutes=30)
    def_td = parse_duration(def_dur_str) or datetime.timedelta(days=7)
    max_td = parse_duration(max_dur_str) or datetime.timedelta(days=30)
    custom_td = parse_duration(custom_limit_str) if custom_limit_str else None

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=10000;")
        async with db.execute(
            "SELECT id, tg_id, acc_type, username, expired_at, created_at, is_trial, COALESCE(server_id, 1) FROM accounts"
        ) as cur:
            rows = await cur.fetchall()

    to_delete = []
    for acc_id, tg_id, acc_type, uname, exp_at, created_at, is_trial, srv_id in rows:
        exp_dt = parse_wib_dt(exp_at)
        created_dt = parse_wib_dt(created_at)
        is_tr = int(is_trial or 0) == 1
        reason = ""

        if mode == "expired":
            if exp_dt is not None:
                if now >= exp_dt:
                    reason = f"Lewat waktu expired ({exp_at} WIB)"
            elif is_tr and created_dt is not None:
                if now >= (created_dt + trial_td):
                    reason = f"Trial melebihi batas {trial_dur_str} (Dibuat: {created_at} WIB)"

        elif mode == "trial_over":
            if is_tr:
                if (exp_dt and now >= exp_dt) or (created_dt and now >= (created_dt + trial_td)):
                    reason = f"Trial melebihi batas {trial_dur_str}"

        elif mode == "reg_over_default":
            if not is_tr:
                if exp_dt and now >= exp_dt:
                    reason = f"Sudah expired ({exp_at} WIB)"
                elif created_dt and now >= (created_dt + def_td):
                    reason = f"Umur akun melebihi batas {def_dur_str} sejak dibuat ({created_at} WIB)"
                elif created_dt and exp_dt and (exp_dt - created_dt) > def_td:
                    reason = f"Masa aktif ({exp_at}) melebihi batas hari default ({def_dur_str})"

        elif mode == "all_over_limit":
            if exp_dt and now >= exp_dt:
                reason = f"Lewat waktu expired ({exp_at} WIB)"
            elif is_tr and created_dt and now >= (created_dt + trial_td):
                reason = f"Trial melebihi batas {trial_dur_str}"
            elif not is_tr and created_dt and now >= (created_dt + def_td):
                reason = f"Akun melebihi batas durasi {def_dur_str} sejak dibuat"
            elif not is_tr and created_dt and exp_dt and (exp_dt - created_dt) > max_td:
                reason = f"Masa aktif melebihi batas maksimal ({max_dur_str})"

        elif mode == "custom" and custom_td:
            if (exp_dt and now >= exp_dt) or (created_dt and now >= (created_dt + custom_td)):
                reason = f"Melebihi batas durasi {custom_limit_str} (Dibuat: {created_at} WIB)"
            elif created_dt and exp_dt and (exp_dt - created_dt) > custom_td:
                reason = f"Masa aktif akun melebihi batas {custom_limit_str}"

        if reason:
            to_delete.append((acc_id, tg_id, acc_type, uname, exp_at, created_at, srv_id, reason))

    if not to_delete:
        return 0, []

    need_xray_servers: set[int] = set()
    deleted_names: list[str] = []
    wib_log_str = now.strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("PRAGMA busy_timeout=10000;")
        for acc_id, tg_id, acc_type, uname, exp_at, created_at, srv_id, reason in to_delete:
            await db.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))
            await db.execute(
                "INSERT INTO logs (tg_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                (tg_id, "EXPIRED_DELETED", f"{acc_type.upper()} {uname} [Srv#{srv_id}] ({reason})", wib_log_str)
            )
            deleted_names.append(f"{uname} ({acc_type.upper()})")
        await db.commit()

    for acc_id, tg_id, acc_type, uname, exp_at, created_at, srv_id, reason in to_delete:
        if acc_type == "ssh":
            await delete_ssh_on_server(srv_id, uname)
        else:
            need_xray_servers.add(srv_id)

    for sid in need_xray_servers:
        await sync_xray_by_server(sid)

    await generate_portable_backup_sh()

    if bot:
        for acc_id, tg_id, acc_type, uname, exp_at, created_at, srv_id, reason in to_delete:
            try:
                await bot.send_message(
                    tg_id,
                    f"⏰ <b>Masa Aktif Akun Berakhir!</b>\n\n"
                    f"• <b>Tipe:</b> <code>{acc_type.upper()}</code>\n"
                    f"• <b>Username:</b> <code>{uname}</code>\n"
                    f"• <b>Tanggal Buat:</b> <code>{created_at or '-'} WIB</code>\n"
                    f"• <b>Tanggal Expired:</b> <code>{exp_at} WIB</code>\n"
                    f"• <b>Keterangan:</b> <code>{reason}</code>\n\n"
                    f"Akun telah dihapus otomatis dari server."
                )
            except Exception:
                pass

    return len(to_delete), deleted_names