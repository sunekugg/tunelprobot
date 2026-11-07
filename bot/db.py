"""SQLite untuk tunnel-bot.

Tabel: accounts, admins, settings, intervals, topics, logs, vps, components, backups.
Zona waktu akun (created_at / expired_at) selalu WIB (Asia/Jakarta) sesuai blueprint.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DB_PATH

TZ_WIB = ZoneInfo("Asia/Jakarta")


def now_wib() -> datetime:
    return datetime.now(TZ_WIB)


def fmt_wib(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M WIB")


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    protocol TEXT NOT NULL CHECK (protocol IN ('ssh','vmess','vless','trojan')),
    password TEXT,
    uuid TEXT,
    key TEXT,
    host TEXT NOT NULL DEFAULT '',
    ip_limit INTEGER NOT NULL DEFAULT 0,
    ip_used INTEGER NOT NULL DEFAULT 0,
    pid_limit INTEGER NOT NULL DEFAULT 0,
    duration_days INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,   -- WIB
    expired_at TEXT NOT NULL,   -- WIB
    owner_id INTEGER,           -- Telegram user id
    is_trial INTEGER NOT NULL DEFAULT 0,
    is_locked INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','deleted'))
);
CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_id);
CREATE INDEX IF NOT EXISTS idx_accounts_expired ON accounts(expired_at);

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    name TEXT DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS intervals (
    task TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'minute' CHECK (unit IN ('minute','hour','day')),
    enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS topics (
    name TEXT PRIMARY KEY,
    group_id TEXT NOT NULL DEFAULT '',
    topic_id TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    event TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    ssh_user TEXT NOT NULL DEFAULT '',
    ssh_port INTEGER NOT NULL DEFAULT 22,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS components (
    name TEXT PRIMARY KEY,
    installed INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0
);
"""

DEFAULT_INTERVALS = [
    ("notify_expired", 60, "minute", 1),
    ("notify_login", 5, "minute", 1),
    ("check_online", 5, "minute", 0),
    ("auto_delete_expired", 1440, "minute", 0),
    ("auto_heal", 30, "minute", 1),
    ("auto_backup", 1440, "minute", 1),
    ("check_bug", 1, "day", 0),
]

DEFAULT_SETTINGS = {
    "duration_days": "30",
    "quota_days": "3",
    "pid_limit": "3",
    "default_ip_limit": "2",
    "create_batch_days": "3",
    "create_batch_limit": "1",
    "welcome_photo": "",
    "welcome_text": "Welcome {nama}! Waktu server: {waktu_server}.",
}


class DB:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.init()

    def init(self) -> None:
        self._conn.executescript(SCHEMA)
        for task, value, unit, enabled in DEFAULT_INTERVALS:
            self._conn.execute(
                "INSERT OR IGNORE INTO intervals (task, value, unit, enabled) VALUES (?,?,?,?)",
                (task, value, unit, enabled),
            )
        for key, val in DEFAULT_SETTINGS.items():
            self._conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
                (key, val),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- generic settings ---
    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    # --- intervals ---
    def get_interval(self, task: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM intervals WHERE task=?", (task,)
        ).fetchone()

    def set_interval(self, task: str, value: int, unit: str, enabled: int) -> None:
        self._conn.execute(
            "INSERT INTO intervals (task, value, unit, enabled) VALUES (?,?,?,?) "
            "ON CONFLICT(task) DO UPDATE SET value=excluded.value, "
            "unit=excluded.unit, enabled=excluded.enabled",
            (task, value, unit, enabled),
        )
        self._conn.commit()

    # --- admin ---
    def add_admin(self, user_id: int, name: str = "", is_primary: int = 0) -> None:
        ts = fmt_wib(now_wib())
        self._conn.execute(
            "INSERT OR REPLACE INTO admins (user_id, name, is_primary, created_at) VALUES (?,?,?,?)",
            (user_id, name, is_primary, ts),
        )
        self._conn.commit()

    def remove_admin(self, user_id: int) -> None:
        self._conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        self._conn.commit()

    def is_admin(self, user_id: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        ).fetchone() is not None

    def list_admins(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM admins ORDER BY is_primary DESC").fetchall()

    # --- topics ---
    def set_topic(self, name: str, group_id: str, topic_id: str, enabled: int = 1) -> None:
        self._conn.execute(
            "INSERT INTO topics (name, group_id, topic_id, enabled) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET group_id=excluded.group_id, "
            "topic_id=excluded.topic_id, enabled=excluded.enabled",
            (name, group_id, topic_id, enabled),
        )
        self._conn.commit()

    def get_topic(self, name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM topics WHERE name=?", (name,)
        ).fetchone()

    # --- accounts ---
    def add_account(self, **kw) -> int:
        cols = ",".join(kw.keys())
        ph = ",".join("?" for _ in kw)
        cur = self._conn.execute(
            f"INSERT INTO accounts ({cols}) VALUES ({ph})", tuple(kw.values())
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_account_by_username(self, username: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM accounts WHERE username=?", (username,)
        ).fetchone()

    def list_accounts(self, owner_id: int | None = None) -> list[sqlite3.Row]:
        if owner_id is None:
            return self._conn.execute(
                "SELECT * FROM accounts WHERE status != 'deleted' ORDER BY id DESC"
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM accounts WHERE owner_id=? AND status != 'deleted' ORDER BY id DESC",
            (owner_id,),
        ).fetchall()

    def delete_account(self, username: str) -> None:
        self._conn.execute(
            "UPDATE accounts SET status='deleted' WHERE username=?", (username,)
        )
        self._conn.commit()

    # --- logs ---
    def add_log(self, event: str, detail: str = "", level: str = "info") -> None:
        ts = fmt_wib(now_wib())
        self._conn.execute(
            "INSERT INTO logs (ts, level, event, detail) VALUES (?,?,?,?)",
            (ts, level, event, detail),
        )
        self._conn.commit()

    def list_logs(self, limit: int = 100) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

