"""Fitur Notifikasi Login & Rekap Online ke Topic Grup.

Dua fitur terpisah, masing-masing dapat di-ON/OFF dari panel admin:
1. **login_notif_enabled** — Notifikasi setiap ada login baru (SSH/VMess/VLESS/Trojan) ke topic.
2. **login_notif_interval_min** — Rekap berkala jumlah user online ke topic (0 = off).

Setting tambahan:
- **show_ip_in_notif** — Tampilkan/sembunyikan IP client di notifikasi (privasi).
"""
from __future__ import annotations
import html
import asyncio
import aiosqlite
from datetime import datetime
from aiogram import Bot

from .config import DB_PATH, DOMAIN, now_wib
from .db import get_setting, get_servers_with_usage
from .system_manager import (
    get_ssh_active_sessions, get_xray_active_ips, get_haproxy_client_ips,
    _CLUSTER_ONLINE_CACHE,
)
async def _send_to_topic(bot: Bot, text: str) -> None:
    """Kirim teks ke topic grup log (jika diaktifkan)."""
    try:
        if (await get_setting("notif_topic_enabled")) != "1":
            return
        grp = (await get_setting("notif_group_id") or "").strip()
        if not grp:
            return
        top = (await get_setting("notif_topic_id") or "0").strip()
        chat_id = int(grp) if grp.lstrip("-").isdigit() else grp
        thread_id = int(top) if top.isdigit() and int(top) > 0 else None
        kwargs = {"chat_id": chat_id, "text": text}
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        await bot.send_message(**kwargs)
    except Exception as e:
        print(f"[LOGIN NOTIFIER TOPIC ERROR] {e}")


def _get_server_hostname() -> str:
    """Ambil domain publik server (bukan IP, sesuai blueprint §0.10)."""
    if DOMAIN and DOMAIN.strip() and DOMAIN.strip() != "localhost":
        return DOMAIN.strip()
    try:
        import subprocess
        return (subprocess.check_output(["hostname", "-f"], text=True, stderr=subprocess.DEVNULL)
                .strip() or "server")
    except Exception:
        return "server"


async def _get_isp_cached() -> str:
    try:
        from .system_manager import get_isp_info
        _, isp = await get_isp_info()
        return isp or "Tidak diketahui"
    except Exception:
        return "Tidak diketahui"


async def _get_all_accounts_map() -> dict[str, dict]:
    """Ambil semua akun aktif (belum expired) dari DB → map username → info."""
    now_dt = now_wib().replace(tzinfo=None)
    acc_map: dict[str, dict] = {}

    def _is_expired(exp_raw: str) -> bool:
        exp = (exp_raw or "").replace("WIB", "").strip()
        if not exp or exp.lower() == "unlimited":
            return False
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(exp, fmt) <= now_dt
            except Exception:
                continue
        return False  # tak bisa parse → anggap belum expired

    try:
        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, tg_id, acc_type, username, expired_at, server_id FROM accounts"
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            uname = (r["username"] or "").strip()
            if not uname:
                continue
            if _is_expired(r["expired_at"]):
                continue
            acc_map[uname] = r
    except Exception as e:
        print(f"[LOGIN NOTIFIER DB ERROR] {e}")
    return acc_map


# ============================================================
# FITUR 1: Notifikasi login baru (tiap username+IP muncul)
# ============================================================

_prev_online_snapshot: dict[str, set[tuple[str, str]]] = {}
# _prev_online_snapshot[proto] = {(username, ip), ...}


def _get_online_snapshot(ssh_sess: dict, xray_ips: dict) -> dict[str, set[tuple[str, str]]]:
    snap: dict[str, set[tuple[str, str]]] = {"ssh": set(), "xray": set()}
    for uname, sess_list in ssh_sess.items():
        for _, ip in sess_list:
            snap["ssh"].add((uname, ip))
    for uname, ips in xray_ips.items():
        for ip in ips:
            snap["xray"].add((uname, ip))
    return snap


async def detect_and_notify_new_logins(bot: Bot, ssh_sess: dict, xray_ips: dict) -> None:
    """Bandingkan snapshot online dengan loop sebelumnya. Kirim notif login baru ke topic."""
    global _prev_online_snapshot
    try:
        if (await get_setting("login_notif_enabled")) != "1":
            _prev_online_snapshot = {}
            return
        if (await get_setting("notif_topic_enabled")) != "1":
            return

        cur_snap = _get_online_snapshot(ssh_sess, xray_ips)
        show_ip = (await get_setting("show_ip_in_notif")) == "1"

        proto_label = {"ssh": "SSH/Dropbear", "xray": "VMess/VLESS/Trojan (Xray)"}
        new_logins: dict[str, list[tuple[str, str]]] = {}
        for proto in ("ssh", "xray"):
            prev = _prev_online_snapshot.get(proto, set())
            diff = cur_snap.get(proto, set()) - prev
            if diff:
                new_logins[proto] = sorted(diff)

        # Jika snapshot pertama kali (kosong) — jangan spam semua sebagai "login baru"
        if _prev_online_snapshot:
            acc_map = await _get_all_accounts_map()
            for proto, items in new_logins.items():
                label = proto_label.get(proto, proto.upper())
                lines = []
                for uname, ip in items:
                    if uname not in acc_map:
                        continue
                    ip_str = f"<code>{html.escape(ip)}</code>" if show_ip else "<code>•••</code>"
                    lines.append(f"  • <code>{html.escape(uname)}</code>  dari  {ip_str}")
                if lines:
                    wib = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")
                    text = (
                        f"🔑 <b>Login Terdeteksi — {label}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        + "\n".join(lines)
                        + f"\n🕒 <b>Waktu:</b> <code>{wib}</code>"
                    )
                    await _send_to_topic(bot, text)

        _prev_online_snapshot = cur_snap
    except Exception as e:
        print(f"[LOGIN DETECT ERROR] {e}")


# ============================================================
# FITUR 2: Rekap berkala jumlah user online
# ============================================================

async def _get_used_bytes_map() -> dict[str, int]:
    """username → used_bytes (untuk tampilan pemakaian di report xray)."""
    m: dict[str, int] = {}
    try:
        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT username, used_bytes FROM accounts WHERE acc_type != 'ssh'"
            ) as cur:
                for r in await cur.fetchall():
                    m[str(r["username"])] = int(r["used_bytes"] or 0)
    except Exception:
        pass
    return m


async def _get_online_xray_by_proto() -> dict[str, dict[str, set[str]]]:
    """Protokol → {username → set(target_ip)} dari xray access log (window 60s).

    realip.log tak memuat username, jadi tak bisa memetakan IP asli → user.
    Gunakan target IP unik yang diakses user dalam window pendek (60 detik):
    1 HP browsing menghasilkan 3-8 target unik — angka realistis, bukan 25.
    Window panjang mendeteksi target lama (DNS/CDN) yang bikin nIP membengkak.
    """
    xray_users = get_xray_active_ips(window_seconds=60)
    if not xray_users:
        return {}

    by_proto: dict[str, dict[str, set[str]]] = {"vmess": {}, "vless": {}, "trojan": {}}
    try:
        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT username, acc_type FROM accounts WHERE acc_type != 'ssh'") as cur:
                proto_of = {str(r["username"]): r["acc_type"] for r in await cur.fetchall()}
    except Exception:
        proto_of = {}

    for uname, target_ips in xray_users.items():
        p = proto_of.get(uname, "vless")
        if p not in by_proto:
            p = "vless"
        by_proto[p][uname] = set(target_ips)
    return by_proto


async def build_online_report() -> str | None:
    """Bangun teks rekap user online (SSH + Xray). Return None jika kosong.

    Format mengikuti contoh pemilik:
        DOMAIN :
        <domain>
        ISP    :
        <isp>
        Users Login SSH
        ajii 1 PID
        alyx 1 PID
        Users Login VMESS
        bernat 2.7GB 1IP | 19330
        Total : 1
    """
    ssh_sess = get_ssh_active_sessions()
    xray_by_proto = await _get_online_xray_by_proto()

    acc_map = await _get_all_accounts_map()

    # Hanya akun aktif (belum expired) + sedang online sekarang
    ssh_users: list[str] = []
    for uname, sess_list in ssh_sess.items():
        if uname in acc_map:
            ssh_users.append(uname)
    ssh_users.sort()

    xray_lines_by_proto: dict[str, list[str]] = {}
    for proto, m in xray_by_proto.items():
        for uname, ips in m.items():
            if uname not in acc_map:
                continue
            n = len(ips)
            xray_lines_by_proto.setdefault(proto, []).append(f"{html.escape(uname)} {n}IP")
        xray_lines_by_proto[proto] = sorted(xray_lines_by_proto.get(proto, []))

    total_online = len(ssh_users) + sum(len(v) for v in xray_lines_by_proto.values())
    if total_online == 0:
        return None

    domain = _get_server_hostname()
    isp = await _get_isp_cached()

    lines = [
        "<b>DOMAIN :</b>",
        f"{html.escape(domain)}",
        "<b>ISP    :</b>",
        f"{html.escape(isp)}",
    ]

    if ssh_users:
        lines.append("<b>Users Login SSH</b>")
        for uname in ssh_users:
            lines.append(f"{html.escape(uname)} {len([s for s in ssh_sess[uname]])} PID")
        lines.append("")

    proto_label = {"vmess": "Users Login VMESS", "vless": "Users Login VLESS", "trojan": "Users Login TROJAN"}
    for proto in ("vmess", "vless", "trojan"):
        if xray_lines_by_proto.get(proto):
            lines.append(f"<b>{proto_label[proto]}</b>")
            lines.extend(xray_lines_by_proto[proto])
            lines.append("")

    lines.append(f"<b>Total : {total_online}</b>")
    return "\n".join(lines)


def _xray_port_for(proto: str) -> int:
    """Port inbound xray per protokol (dari tag konvensi installer)."""
    port_map = {"vmess": 10007, "vless": 10004, "trojan": 10001}
    return port_map.get(proto, 10004)


async def send_online_report(bot: Bot) -> bool:
    """Kirim rekap online ke topic HANYA jika ada user yang sedang online.

    Return True jika ada user online & terkirim; False jika kosong / gagal kirim.
    """
    try:
        if (await get_setting("notif_topic_enabled")) != "1":
            return False
        report = await build_online_report()
        if report is None:
            # Tidak ada user online → jangan kirim apa-apa ke topic
            return False
        await _send_to_topic(bot, report)
        return True
    except Exception as e:
        print(f"[ONLINE REPORT ERROR] {e}")
        return False


async def periodic_online_report_loop(bot: Bot) -> None:
    """Loop berkala kirim rekap online sesuai interval (menit)."""
    last_sent_ts: float = 0.0
    while True:
        try:
            interval_min = int(await get_setting("login_notif_interval_min") or "0")
            if interval_min <= 0 or (await get_setting("notif_topic_enabled")) != "1":
                last_sent_ts = 0.0
                await asyncio.sleep(60)
                continue
            now_ts = asyncio.get_event_loop().time()
            if now_ts - last_sent_ts >= interval_min * 60:
                await send_online_report(bot)
                last_sent_ts = now_ts
            await asyncio.sleep(30)
        except Exception as e:
            print(f"[ONLINE REPORT LOOP ERROR] {e}")
            await asyncio.sleep(60)
