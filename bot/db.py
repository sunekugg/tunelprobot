from __future__ import annotations
import datetime
import re
import time
import aiosqlite
from .config import DB_PATH, ADMIN_ID, ADMIN_IDS, DOMAIN, now_wib

DEFAULT_SETTINGS = {
    "bot_enabled": "1",
    "default_duration": "7d",
    "max_duration": "30d",
    "trial_duration": "30m",
    "daily_create_limit": "2",
    "create_limit_window": "1d",
    "global_limit_ip": "2",
    "default_quota_gb": "0",
    "default_speed_mbps": "0",
    "violation_action": "lock_temp",
    "lock_duration_min": "15",
    "force_join_enabled": "0",
    "feat_self_manage": "1",
    "feat_bug_checker": "1",
    "feat_auto_heal": "1",
    "feat_smart_sub": "1",
    "feat_smart_lb": "1",
    "ui_theme": "tree",
    "auto_hidden_servers": "",
    "auto_backup_tg": "1",
    "bug_presets_wildcard": "Line Game=ava.game.naver.com, XL Vidio=quiz.vidio.com, XL Viu=zaintest.vuclip.com, Grab=grab.com",
    "bug_presets_sni": "Zoom Edu=zoom.us, Ruangguru=bimbel.ruangguru.com, Ilmupedia=ilmupedia.co.id, Netflix=cache.netflix.com",
    "bug_presets_cdn": "CF Indo=104.18.2.2, CF SG=162.159.128.7, Grab CDN=grab.com, Vidio CDN=quiz.vidio.com",
    "bug_presets": "ava.game.naver.com,quiz.vidio.com,grab.com,zoom.us,cache.netflix.com",
    "notif_topic_enabled": "0",
    "notif_group_id": "",
    "notif_topic_id": "0",
    "force_join_text": "⚠️ <b>Wajib Bergabung!</b>\n\nSilakan bergabung ke semua grup/channel di bawah ini terlebih dahulu untuk menggunakan layanan bot:",
    "welcome_text": "👋 Selamat datang di <b>Panel Layanan Akun SSH, Trojan, VLESS &amp; VMess</b>.\nGunakan tombol menu di bawah untuk mengelola akun Anda."
}

def parse_window_str(win_str: str) -> tuple[int, str]:
    """Konversi string durasi (misal 3d, 7d, 1w, 12h) menjadi (detik, label)."""
    s = (win_str or "1d").strip().lower()
    m = re.match(r"^(\d+)\s*(m|min|mnt|menit|h|jam|hour|d|day|hari|w|week|minggu)?$", s)
    if not m:
        return 86400, "1d"
    val = max(1, int(m.group(1)))
    unit = m.group(2) or "d"
    if unit in ("m", "min", "mnt", "menit"):
        return val * 60, f"{val}m"
    if unit in ("h", "jam", "hour"):
        return val * 3600, f"{val}h"
    if unit in ("w", "week", "minggu"):
        return val * 7 * 86400, f"{val * 7}d"
    return val * 86400, f"{val}d"

async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=10000;")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            custom_quota INTEGER DEFAULT -1,
            custom_quota_window TEXT DEFAULT '',
            custom_duration TEXT DEFAULT '',
            custom_limit_ip INTEGER DEFAULT -1,
            trial_date TEXT DEFAULT '',
            trial_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            acc_type TEXT,
            username TEXT UNIQUE,
            password_or_uuid TEXT,
            expired_at TEXT,
            limit_ip INTEGER DEFAULT -1,
            is_trial INTEGER DEFAULT 0,
            is_locked INTEGER DEFAULT 0,
            locked_until TEXT DEFAULT '',
            violations INTEGER DEFAULT 0,
            notified_h1 INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            server_id INTEGER DEFAULT 1,
            quota_gb REAL DEFAULT 0,
            used_bytes INTEGER DEFAULT 0,
            speed_mbps INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            domain TEXT,
            ip TEXT DEFAULT '127.0.0.1',
            ssh_port INTEGER DEFAULT 22,
            ssh_user TEXT DEFAULT 'root',
            ssh_pass TEXT DEFAULT '',
            max_users INTEGER DEFAULT 100,
            is_local INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            quota_gb REAL DEFAULT 0,
            used_bytes INTEGER DEFAULT 0,
            speed_mbps INTEGER DEFAULT 0
        )""")

        for alter_sql in [
            "ALTER TABLE users ADD COLUMN custom_quota_window TEXT DEFAULT ''",
            "ALTER TABLE accounts ADD COLUMN server_id INTEGER DEFAULT 1",
            "ALTER TABLE accounts ADD COLUMN quota_gb REAL DEFAULT 0",
            "ALTER TABLE accounts ADD COLUMN used_bytes INTEGER DEFAULT 0",
            "ALTER TABLE accounts ADD COLUMN speed_mbps INTEGER DEFAULT 0",
            "ALTER TABLE servers ADD COLUMN quota_gb REAL DEFAULT 0",
            "ALTER TABLE servers ADD COLUMN used_bytes INTEGER DEFAULT 0",
            "ALTER TABLE servers ADD COLUMN speed_mbps INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(alter_sql)
            except Exception:
                pass

        async with db.execute("SELECT COUNT(*) FROM servers WHERE is_local = 1") as cur:
            cnt = (await cur.fetchone())[0]
        if cnt == 0:
            await db.execute(
                "INSERT INTO servers (id, name, domain, ip, is_local, max_users, is_active) VALUES (1, 'Server Utama', ?, '127.0.0.1', 1, 100, 1)",
                (DOMAIN,)
            )
        else:
            await db.execute("UPDATE servers SET domain = ? WHERE is_local = 1", (DOMAIN,))

        await db.execute("""
        CREATE TABLE IF NOT EXISTS creation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            acc_type TEXT,
            username TEXT,
            created_ts INTEGER,
            created_wib TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS force_join_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE,
            title TEXT,
            invite_link TEXT,
            is_active INTEGER DEFAULT 1
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            action TEXT,
            detail TEXT,
            created_at TEXT
        )""")
        for k, v in DEFAULT_SETTINGS.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        master = ADMIN_ID or (ADMIN_IDS[0] if ADMIN_IDS else 0)
        if master:
            await db.execute(
                "INSERT OR IGNORE INTO users (tg_id, username, full_name, is_admin) VALUES (?, 'admin', 'Master Admin', 1)",
                (master,)
            )
            await db.execute("UPDATE users SET is_admin = 1 WHERE tg_id = ?", (master,))
        for extra in ADMIN_IDS:
            if extra and extra != master:
                await db.execute("INSERT OR IGNORE INTO users (tg_id, username, full_name, is_admin) VALUES (?, '', '', 1)", (extra,))
                await db.execute("UPDATE users SET is_admin = 1 WHERE tg_id = ?", (extra,))
        await db.commit()

async def get_servers_with_usage(only_active: bool = True) -> list[dict]:
    query = (
        "SELECT id, name, domain, ip, ssh_port, ssh_user, ssh_pass, max_users, is_local, is_active, "
        "COALESCE(quota_gb, 0) AS quota_gb, COALESCE(used_bytes, 0) AS used_bytes, COALESCE(speed_mbps, 0) AS speed_mbps "
        "FROM servers"
    )
    if only_active:
        query += " WHERE is_active = 1"
    query += " ORDER BY id ASC"
    result = []
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query) as cur:
            rows = await cur.fetchall()
        for r in rows:
            d = dict(r)
            async with db.execute("SELECT COUNT(*) FROM accounts WHERE COALESCE(server_id, 1) = ?", (d["id"],)) as c_cur:
                d["used_users"] = (await c_cur.fetchone())[0]
            result.append(d)
    return result

async def get_server_by_id(server_id: int) -> dict | None:
    servers = await get_servers_with_usage(only_active=False)
    for s in servers:
        if s["id"] == int(server_id):
            return s
    return servers[0] if servers else None

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else DEFAULT_SETTINGS.get(key, "")

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()

async def add_log(tg_id: int, action: str, detail: str):
    wib_str = now_wib().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("INSERT INTO logs (tg_id, action, detail, created_at) VALUES (?, ?, ?, ?)", (tg_id, action, detail, wib_str))
        await db.commit()

async def ensure_user(tg_id: int, username: str = "", full_name: str = "") -> dict:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        db.row_factory = aiosqlite.Row
        is_adm = 1 if tg_id == ADMIN_ID else 0
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, full_name, is_admin) VALUES (?, ?, ?, ?)",
            (tg_id, username or "", full_name or "", is_adm)
        )
        await db.execute(
            "UPDATE users SET username = ?, full_name = ? WHERE tg_id = ?",
            (username or "", full_name or "", tg_id)
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row)

async def is_user_admin(tg_id: int) -> bool:
    if tg_id == ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT is_admin FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return bool(row and row[0] == 1)

async def get_effective_limit_ip(tg_id: int, acc_limit_ip: int = -1) -> int:
    if acc_limit_ip is not None and int(acc_limit_ip) >= 0:
        return int(acc_limit_ip)
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT custom_limit_ip FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0] is not None and int(row[0]) >= 0:
                return int(row[0])
    global_lim = await get_setting("global_limit_ip")
    return int(global_lim) if global_lim.isdigit() else 2

async def get_effective_daily_limit(tg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT custom_quota FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0] is not None and int(row[0]) >= 0:
                return int(row[0])
    q = await get_setting("daily_create_limit")
    return int(q) if str(q).isdigit() else 2

async def get_effective_quota_window(tg_id: int) -> tuple[int, str]:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT custom_quota_window FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                return parse_window_str(row[0])
    g_win = await get_setting("create_limit_window") or "1d"
    return parse_window_str(g_win)


async def record_account_creation(tg_id: int, acc_type: str, username: str):
    now_ts = int(time.time())
    wib_str = now_wib().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute(
            "INSERT INTO creation_history (tg_id, acc_type, username, created_ts, created_wib) VALUES (?, ?, ?, ?, ?)",
            (tg_id, acc_type, username, now_ts, wib_str)
        )
        await db.commit()

async def reset_all_creation_limits():
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("DELETE FROM creation_history")
        await db.execute("UPDATE users SET trial_count = 0, trial_date = ''")
        await db.commit()

async def reset_user_creation_limit(tg_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute("DELETE FROM creation_history WHERE tg_id = ?", (tg_id,))
        await db.execute("UPDATE users SET trial_count = 0, trial_date = '' WHERE tg_id = ?", (tg_id,))
        await db.commit()

async def get_effective_duration(tg_id: int) -> str:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute("SELECT custom_duration FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                return row[0]
    return await get_setting("default_duration")

# === OVERRIDE QUOTA ROLLING WINDOW STRICT (SUPPORT 0 = UNLIMITED) ===

# === OVERRIDE QUOTA ROLLING WINDOW STRICT (SUPPORT 0 = UNLIMITED) ===

# === OVERRIDE QUOTA ROLLING WINDOW STRICT (BEBAS TIMEZONE ERROR & SUPPORT 0 = UNLIMITED) ===
def _parse_quota_and_window(limit_raw: str, window_raw: str) -> tuple[int, datetime.timedelta | None, str]:
    l_str = str(limit_raw if limit_raw is not None else "2").strip().lower()
    w_str = str(window_raw if window_raw is not None else "1d").strip().lower()

    if "/" in l_str:
        p1, p2 = l_str.split("/", 1)
        l_str = p1.strip()
        w_str = p2.strip()
    elif "/" in w_str:
        p1, p2 = w_str.split("/", 1)
        if p1.strip().isdigit():
            l_str = p1.strip()
        w_str = p2.strip()

    if l_str in ("0", "unlimited", "tanpa batas", "inf", "bebas", "♾"):
        max_lim = 0
    else:
        cleaned = re.sub(r"[^0-9-]", "", l_str)
        try:
            max_lim = int(cleaned) if cleaned != "" else 2
        except Exception:
            max_lim = 2
        if max_lim < 0:
            max_lim = 0

    if w_str in ("0", "unlimited", "tanpa reset", "permanent", "inf", "lifetime"):
        return max_lim, None, "Tanpa Reset"

    m = re.match(r"^(\d+)\s*(m|min|mnt|menit|h|jam|hour|d|day|hari|w|week|minggu)?$", w_str)
    if not m:
        return max_lim, datetime.timedelta(days=1), "1d"

    val = max(1, int(m.group(1)))
    unit = m.group(2) or "d"
    if unit in ("m", "min", "mnt", "menit"):
        return max_lim, datetime.timedelta(minutes=val), f"{val}m"
    if unit in ("h", "jam", "hour"):
        return max_lim, datetime.timedelta(hours=val), f"{val}h"
    if unit in ("w", "week", "minggu"):
        return max_lim, datetime.timedelta(days=val * 7), f"{val * 7}d"
    return max_lim, datetime.timedelta(days=val), f"{val}d"

def _parse_any_wib_dt(val_str: str, val_ts: int | float | None, wib_tz=None) -> datetime.datetime | None:
    tz_wib = datetime.timezone(datetime.timedelta(hours=7))
    if val_ts and int(val_ts) > 1000000000:
        try:
            return datetime.datetime.fromtimestamp(int(val_ts), tz=tz_wib).replace(tzinfo=None)
        except Exception:
            pass
    s = str(val_str or "").replace("WIB", "").strip()
    if not s:
        return None
    if s.isdigit() and int(s) > 1000000000:
        try:
            return datetime.datetime.fromtimestamp(int(s), tz=tz_wib).replace(tzinfo=None)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).replace(tzinfo=None)
        except Exception:
            continue
    return None

async def check_daily_creation_quota(tg_id: int) -> tuple[bool, int, int, str]:
    # Gunakan naive datetime WIB secara konsisten agar tidak pernah bentrok offset-naive vs offset-aware
    wib_now = now_wib().replace(tzinfo=None)

    global_lim_raw = await get_setting("daily_create_limit")
    if global_lim_raw is None or str(global_lim_raw).strip() == "":
        global_lim_raw = "2"
    window_raw = await get_setting("create_limit_window") or "1d"
    max_lim, window_td, win_label = _parse_quota_and_window(global_lim_raw, window_raw)

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        # custom_quota: -1 atau None = Ikut Global | 0 = Unlimited User | >0 = Kuota Khusus User
        async with db.execute("SELECT custom_quota FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            u_row = await cur.fetchone()
            if u_row and u_row[0] is not None:
                try:
                    cq = int(u_row[0])
                    if cq == 0:
                        max_lim = 0
                    elif cq > 0:
                        max_lim = cq
                except Exception:
                    pass

        try:
            async with db.execute("PRAGMA table_info(creation_history)") as cur:
                ch_cols = [r[1] for r in await cur.fetchall()]
        except Exception:
            ch_cols = []

        events_map: dict[str, datetime.datetime] = {}

        if ch_cols:
            has_at = "created_at" in ch_cols
            has_ts = "created_ts" in ch_cols
            has_un = "username" in ch_cols
            sel_at = "created_at" if has_at else "''"
            sel_ts = "created_ts" if has_ts else "0"
            sel_un = "username" if has_un else "rowid"
            q_hist = f"SELECT rowid, {sel_un}, {sel_at}, {sel_ts} FROM creation_history WHERE tg_id = ?"
            async with db.execute(q_hist, (tg_id,)) as cur:
                for rid, uname, c_at, c_ts in await cur.fetchall():
                    dt = _parse_any_wib_dt(c_at, c_ts)
                    if dt:
                        key = f"u:{uname}" if uname else f"r:{rid}"
                        if key not in events_map or dt > events_map[key]:
                            events_map[key] = dt

        async with db.execute("SELECT id, username, created_at FROM accounts WHERE tg_id = ?", (tg_id,)) as cur:
            for aid, uname, c_at in await cur.fetchall():
                dt = _parse_any_wib_dt(c_at, 0)
                if dt:
                    key = f"u:{uname}" if uname else f"a:{aid}"
                    if key not in events_map or dt > events_map[key]:
                        events_map[key] = dt

    if window_td is None:
        valid_dts = sorted(events_map.values())
        used = len(valid_dts)
        if max_lim <= 0:
            return True, used, 0, "♾ Unlimited"
        return (used < max_lim), used, max_lim, "Tanpa Reset"

    cutoff_dt = wib_now - window_td
    valid_dts = sorted([dt for dt in events_map.values() if dt >= cutoff_dt])
    used = len(valid_dts)

    if max_lim <= 0:
        return True, used, 0, "♾ Unlimited"

    if valid_dts:
        next_reset_dt = valid_dts[0] + window_td
        reset_str = next_reset_dt.strftime("%Y-%m-%d %H:%M WIB")
    else:
        reset_str = f"Per {win_label}"

    return (used < max_lim), used, max_lim, reset_str