"""Logika akun tunnel-bot: buat, trial, hapus, validasi username/password,
limit IP, batas buat akun, expired. Semua waktu WIB.
"""

from __future__ import annotations

import random
import secrets
import uuid
import string

from .db import DB, fmt_wib, now_wib
from .config import config

PREFIX = "sunek-"
PROTOCOLS = ("ssh", "vmess", "vless", "trojan")

# ---- validation ----
def is_valid_username(u: str) -> bool:
    """Huruf kecil, angka, minus; 4-20 karakter; belum dipakai; prefix wajib."""
    if not (4 <= len(u) <= 20):
        return False
    if not u.startswith(PREFIX) or u == PREFIX:
        return False
    if not all(c in string.ascii_lowercase + string.digits + "-" for c in u):
        return False
    return True


def is_valid_password(p: str) -> bool:
    """Minimal 6 karakter, huruf & angka, tanpa spasi."""
    if len(p) < 6 or " " in p:
        return False
    return any(c.isalpha() for c in p) and any(c.isdigit() for c in p)


def acak_username() -> str:
    return PREFIX + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def acak_password(length: int = 8) -> str:
    # pastikan ada huruf & angka
    alpha = string.ascii_lowercase + string.ascii_uppercase
    num = string.digits
    p = [random.choice(alpha), random.choice(num)]
    p += [random.choice(alpha + num) for _ in range(max(0, length - 2))]
    random.shuffle(p)
    return "".join(p)


def acak_uuid() -> str:
    return str(uuid.uuid4())


def acak_key() -> str:
    return secrets.token_urlsafe(16)


# ---- helpers ----
def duration_from_days(days: int):
    from datetime import timedelta
    return timedelta(days=days)


def compute_expiry(days: int) -> str:
    """Waktu WIB sekarang + days. Return string Y-m-d H:M WIB."""
    return fmt_wib(now_wib() + duration_from_days(days))


def _valid_target(d: DB, owner: int) -> str | None:
    """Batas buat akun per user per periode (setting create_batch_days, create_batch_limit).
    Return pesan error atau None."""
    days = int(d.get_setting("create_batch_days", "3") or 3)
    limit = int(d.get_setting("create_batch_limit", "1") or 1)
    since = fmt_wib(now_wib() - duration_from_days(days))
    rows = d._conn.execute(
        "SELECT COUNT(*) c FROM accounts WHERE owner_id=? AND created_at>=?",
        (owner, since),
    ).fetchone()
    if rows["c"] >= limit:
        return f"Batas buat akun tercapai ({limit} per {days} hari). Coba lagi nanti."
    return None


def sisa_kuota_text(d: DB, owner: int) -> str:
    days = int(d.get_setting("create_batch_days", "3") or 3)
    limit = int(d.get_setting("create_batch_limit", "1") or 1)
    since = fmt_wib(now_wib() - duration_from_days(days))
    cur = d._conn.execute(
        "SELECT COUNT(*) c FROM accounts WHERE owner_id=? AND created_at>=?",
        (owner, since),
    ).fetchone()["c"]
    return f"{cur}/{limit} (reset {days} hari)"

# ---- create ----
def create_account(d: DB, owner: int, protocol: str, username: str,
                   password: str | None = None, days: int | None = None,
                   trial: bool = False) -> tuple[bool, str, dict]:
    """Return (ok, pesan, data). data berisi key utk format output."""
    if protocol not in PROTOCOLS:
        return False, "Protokol tidak dikenal.", {}
    if not trial:
        err = _valid_target(d, owner)
        if err:
            return False, err, {}
    if not trial and not is_valid_username(username):
        return False, "Username tidak valid (huruf kecil/angka/minus, 4-20, awalan sunek-).", {}
    if not trial and d.get_account_by_username(username):
        return False, f"Username {username} sudah dipakai.", {}

    if days is None:
        days = int(d.get_setting("duration_days", "30") or 30)
    if trial:
        days = int(d.get_setting("trial_days", "1") or 1)
        # trial selalu random
        username = acak_username()
        password = acak_password()

    if protocol == "ssh":
        if not password:
            return False, "Password SSH wajib diisi.", {}
        if not is_valid_password(password):
            return False, "Password minimal 6 karakter, huruf & angka, tanpa spasi.", {}
    elif protocol == "vmess":
        password = acak_uuid()
    elif protocol == "vless":
        password = acak_uuid()
    elif protocol == "trojan":
        password = acak_key()

    host = config.get("DOMAIN", "")
    ip_limit = int(d.get_setting("default_ip_limit", "2") or 2)
    pid_limit = int(d.get_setting("pid_limit", "3") or 3)

    created = fmt_wib(now_wib())
    expired = compute_expiry(days)
    data = {
        "username": username, "password": password, "host": host,
        "days": days, "expired_text": expired, "protocol": protocol,
        "ip_limit": ip_limit, "pid_limit": pid_limit,
    }
    try:
        d.add_account(
            username=username, protocol=protocol, password=password,
            host=host, ip_limit=ip_limit, pid_limit=pid_limit,
            duration_days=days, created_at=created, expired_at=expired,
            owner_id=owner, is_trial=1 if trial else 0, status="active",
        )
    except Exception as e:
        return False, f"Gagal simpan: {e}", {}
    return True, f"✅ Akun {protocol} dibuat", data


def delete_account(d: DB, username: str) -> tuple[bool, str]:
    """Hapus akun dari DB. Penghapusan user sistem/xray dilakukan scripts (Fase selanjutnya)."""
    acc = d.get_account_by_username(username)
    if not acc or acc["status"] == "deleted":
        return False, "Akun tidak ditemukan."
    d.delete_account(username)
    return True, f"✅ Akun {username} dihapus."


def list_expired(d: DB) -> list:
    """Akun aktif yg expired_at <= sekarang (WIB)."""
    now = fmt_wib(now_wib())
    return d._conn.execute(
        "SELECT * FROM accounts WHERE status='active' AND expired_at <= ?",
        (now,),
    ).fetchall()
