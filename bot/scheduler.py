"""Penjadwal tunnel-bot: menjalankan tugas berkala sesuai interval DB.

Interval diambil dari tabel `intervals` (menit/jam/hari, on/off). Tugas:
- notify_expired: kirim notifikasi expired ke topic + user
- notify_login: notifikasi user login ke topic
- check_online: cek user online (enforce PID)
- auto_delete_expired: hapus akun expired otomatis
- auto_heal: panggil scripts/health.sh
- auto_backup: panggil scripts/backup.sh
- check_bug: cek status bug / host ISP
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections import defaultdict

from .db import DB
from . import notify as N

log = logging.getLogger(__name__)

# peta nama interval -> fungsi async (ctx, db)
TASKS: dict[str, str] = {
    "notify_expired": "_task_notify_expired",
    "notify_login": "_task_notify_login",
    "check_online": "_task_check_online",
    "auto_delete_expired": "_task_auto_delete_expired",
    "auto_heal": "_task_auto_heal",
    "auto_backup": "_task_auto_backup",
    "check_bug": "_task_check_bug",
}


def _interval_seconds(value: int, unit: str) -> int:
    mul = {"minute": 60, "hour": 3600, "day": 86400}
    return max(1, value) * mul.get(unit, 60)


async def _task_notify_expired(ctx, d: DB) -> None:
    from .accounts import list_expired
    rows = list_expired(d)
    if not rows:
        return
    by_proto: dict = defaultdict(list)
    for r in rows:
        by_proto[r["protocol"]].append(r)
    N.notify_expired_group(ctx, d, dict(by_proto))
    for r in rows:
        N.notify_expired_user(ctx, d, r)
    log.info("notify_expired: %d akun", len(rows))


async def _task_notify_login(ctx, d: DB) -> None:
    # placeholder: jumlah PID per protokol dibaca dari scripts/health/cat /etc/passwd.
    # Di tahap ini kirim ringkasan kosong bila tak ada data.
    N.notify_login(ctx, d, {})


async def _task_check_online(ctx, d: DB) -> None:
    # enforce batas PID login utk SSH (sederhana, placeholder implementasi penuh
    # di evaluasi PID dropbear/sshd).
    pass


async def _task_auto_delete_expired(ctx, d: DB) -> None:
    from .accounts import list_expired
    for r in list_expired(d):
        d.delete_account(r["username"])
        log.info("auto-hapus expired: %s", r["username"])


async def _task_auto_heal(ctx, d: DB) -> None:
    try:
        subprocess.run(["/opt/tunnel-bot/scripts/health.sh"], timeout=120)
    except Exception as e:  # noqa: BLE001
        log.warning("auto_heal gagal: %s", e)


async def _task_auto_backup(ctx, d: DB) -> None:
    try:
        subprocess.run(["/usr/local/bin/tunnel", "backup"], timeout=180)
    except Exception as e:  # noqa: BLE001
        log.warning("auto_backup gagal: %s", e)


async def _task_check_bug(ctx, d: DB) -> None:
    # placeholder: cek status bug / host ISP (diimplementasi saat vps.py).
    pass


async def run_due(ctx, d: DB) -> None:
    """Jalankan tugas yg waktunya tiba, sesuai interval DB (gate last_run)."""
    for task, fn_name in TASKS.items():
        if not d.should_run(task):
            continue
        fn = globals().get(fn_name)
        if not fn:
            continue
        try:
            await fn(ctx, d)
            d.touch_interval(task)
        except Exception as e:  # noqa: BLE001
            log.warning("tugas %s gagal: %s", task, e)


async def loop(ctx, d: DB, stop: asyncio.Event) -> None:
    """Loop utama scheduler: cek setiap 60 detik."""
    while not stop.is_set():
        try:
            await run_due(ctx, d)
        except Exception as e:  # noqa: BLE001
            log.warning("loop scheduler error: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
