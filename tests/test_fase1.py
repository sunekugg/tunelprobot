#!/usr/bin/env python3
"""Self-check ringan untuk Fase 1 (fondasi). Jalankan: python3 tests/test_fase1.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bot.db import DB, fmt_wib, now_wib  # noqa: E402


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp()) / "t.db"
    d = DB(tmp)

    # admin
    d.add_admin(123, "owner", 1)
    assert d.is_admin(123)
    assert not d.is_admin(999)
    assert len(d.list_admins()) == 1

    # account
    now = now_wib()
    assert now.tzname() in ("WIB", "GMT+7", "Asia/Jakarta", None)
    aid = d.add_account(
        username="sunek-test", protocol="ssh", password="pw123",
        host="clode2.sunekvip.my.id", ip_limit=2, duration_days=30,
        created_at=fmt_wib(now), expired_at=fmt_wib(now), owner_id=123, is_trial=0,
    )
    assert aid > 0
    acc = d.get_account_by_username("sunek-test")
    assert acc["ip_limit"] == 2 and acc["status"] == "active"
    assert len(d.list_accounts(123)) == 1

    d.delete_account("sunek-test")
    assert d.get_account_by_username("sunek-test")["status"] == "deleted"
    assert d.list_accounts(123) == []

    # settings & interval
    assert d.get_setting("default_ip_limit") == "2"
    d.set_setting("default_ip_limit", "5")
    assert d.get_setting("default_ip_limit") == "5"
    iv = d.get_interval("notify_expired")
    assert iv and iv["value"] == 60 and iv["unit"] == "minute" and iv["enabled"] == 1
    d.set_interval("notify_expired", 120, "minute", 1)
    assert d.get_interval("notify_expired")["value"] == 120

    # log
    d.add_log("create_account", "ssh sunek-test")
    assert len(d.list_logs(10)) == 1

    print("Fase 1 self-check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
