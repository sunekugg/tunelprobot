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
    get_ssh_active_sessions, get_xray_active_ips, get_xray_conn_counts, get_haproxy_client_ips,
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

        # Notifikasi login punya saklar terpisah utk SSH & Xray. Default OFF: Real
        # IP Xray belum tersedia (build v26.10.31 tak baca XFF), jadi Xray matikan
        # biar tak spam baris "dari •••" tanpa makna.
        if (await get_setting("login_notif_ssh_enabled")) != "1":
            cur_snap["ssh"] = set()
        if (await get_setting("login_notif_xray_enabled")) != "1":
            cur_snap["xray"] = set()

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
                    # Tampilkan username saja — IP SSH tak diminta & IP Xray tak tersedia
                    # (build v26.10.31 tak baca XFF), jadi tak ada "dari <ip>".
                    lines.append(f"  • <code>{html.escape(uname)}</code>")
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


async def _get_online_xray_by_proto() -> dict[str, dict[str, int]]:
    """Protokol → {username → set(target_ip)} dari xray access log (window 60s).

    realip.log tak memuat username, jadi tak bisa memetakan IP asli → user.
    Gunakan target IP unik yang diakses user dalam window pendek (60 detik):
    1 HP browsing menghasilkan 3-8 target unik — angka realistis, bukan 25.
    Window panjang mendeteksi target lama (DNS/CDN) yang bikin nIP membengkak.
    """
    xray_users = get_xray_active_ips(window_seconds=60)
    if not xray_users:
        return {}

    # Xray single-process → proxy "jumlah PID" = koneksi accepted dalam window.
    conn_counts = get_xray_conn_counts(window_seconds=60)

    by_proto: dict[str, dict[str, int]] = {"vmess": {}, "vless": {}, "trojan": {}}
    try:
        async with aiosqlite.connect(DB_PATH, timeout=15) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT username, acc_type FROM accounts WHERE acc_type != 'ssh'") as cur:
                proto_of = {str(r["username"]): r["acc_type"] for r in await cur.fetchall()}
    except Exception:
        proto_of = {}

    for uname in xray_users.keys():
        p = proto_of.get(uname, "vless")
        if p not in by_proto:
            p = "vless"
        # Real IP klien tak tersedia: Xray access.log `from` tetap 127.0.0.1 meski
        # nginx kirim X-Real-IP + trustedXForwardedFor=['X-Real-IP'] (build v26.10.31).
        # Kirim (n_conn, None) — jumlah koneksi nyata + IP unavailable. Upgrade path:
        # xray build yang benar membaca XFF, atau PROXY protocol langsung ke Xray.
        n_conn = conn_counts.get(uname, 1)
        by_proto[p][uname] = n_conn
    return by_proto


def _render_online_block(domain: str, isp: str, sections: list[tuple[str, list[tuple[str, str]]]],
                         total: int) -> str:
    """Render satu blok report mono. sections: [(label, [(username, tail)])]."""
    body: list[tuple[str, str]] = []
    for label, rows in sections:
        body.append((label, ""))
        for uname, tail in rows:
            body.append((uname, tail))
        body.append(("", ""))

    name_w = max([len(n) for n, _ in body if n and not n.startswith("Users Login")] or [0])
    name_w = min(max(name_w, 8), 40)

    out = [
        "DOMAIN : " + domain,
        "ISP    : " + isp,
        "",
    ]
    for name, tail in body:
        if not name:
            out.append("")
        elif name.startswith("Users Login"):
            out.append(name)
        else:
            out.append(f"{name.ljust(name_w)} {tail}".rstrip())
    out.append(f"Total : {total}")
    return "<pre>" + html.escape("\n".join(out)) + "</pre>"


async def build_online_reports() -> list[str]:
    """Bangun report online TERPISAH: satu pesan SSH, satu pesan Xray.

    Return list pesan (0-2 item). Tiap protokol Xray punya pesan sendiri.
    """
    ssh_sess = get_ssh_active_sessions()
    # Jumlah koneksi aktif per-user Xray (proxy terdekat utk "PID" — Xray single-process)
    conn_counts = get_xray_conn_counts(window_seconds=60)
    xray_by_proto = await _get_online_xray_by_proto()
    acc_map = await _get_all_accounts_map()

    ssh_users = sorted(u for u in ssh_sess if u in acc_map)

    xray_lines_by_proto: dict[str, list[str]] = {}
    for proto, m in xray_by_proto.items():
        for uname in m.keys():
            if uname in acc_map:
                xray_lines_by_proto.setdefault(proto, []).append(uname)

    domain = _get_server_hostname()
    isp = await _get_isp_cached()
    messages: list[str] = []

    # Pesan 1: SSH (jumlah PID sesi login; tanpa IP)
    if ssh_users:
        rows = []
        for uname in ssh_users:
            n_pid = len(ssh_sess[uname])
            rows.append((uname, f"{n_pid} PID"))
        messages.append(_render_online_block(domain, isp, [("Users Login SSH", rows)], len(ssh_users)))

    # Pesan 2..n: tiap protokol Xray (jumlah koneksi aktif; IP & PID tak tersedia —
    # Xray single-process tanpa PID per-sesi, jadi proxy terdekat = koneksi accepted).
    proto_label = {"vmess": "Users Login VMESS", "vless": "Users Login VLESS", "trojan": "Users Login TROJAN"}
    for proto in ("vmess", "vless", "trojan"):
        users = xray_lines_by_proto.get(proto)
        if users:
            rows = []
            for u in sorted(users):
                n_conn = conn_counts.get(u, 1)
                rows.append((u, f"{n_conn} conn"))
            messages.append(_render_online_block(domain, isp,
                                                 [(proto_label[proto], rows)], len(users)))

    return messages


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
        reports = await build_online_reports()
        if not reports:
            # Tidak ada user online → jangan kirim apa-apa ke topic
            return False
        # Tiap jenis (SSH / tiap Xray proto) dikirim sebagai pesan terpisah.
        for report in reports:
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
