#!/usr/bin/env python3
"""Self-check Fase 3–4: akun & format output. python3 tests/test_fase34.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import bot.formats as F  # noqa: E402
from bot.accounts import (acak_password, acak_username, create_account,  # noqa: E402
                          is_valid_password, is_valid_username, list_expired)
from bot.db import DB  # noqa: E402


def test_validation() -> None:
    assert is_valid_username("sunek-ab12")
    assert not is_valid_username("Abc123")
    assert not is_valid_username("sunek-")
    assert not is_valid_username("sunek-@b")
    assert not is_valid_username("sunek-abcdefghijklmnopqrstuv")
    assert is_valid_password("abc12345")
    assert not is_valid_password("abc")
    assert not is_valid_password("abcdef")
    assert not is_valid_password("ab cd12")
    assert not is_valid_password("123456")
    assert is_valid_username(acak_username())
    assert is_valid_password(acak_password())


def test_accounts() -> None:
    d = DB(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    ok, msg, data = create_account(d, 123, "ssh", "sunek-abc", "pw12345", days=30)
    assert ok, msg
    assert data["username"] == "sunek-abc"
    assert data["protocol"] == "ssh"

    # trial harus acak + durasi trial_days
    ok, msg, data = create_account(d, 123, "vmess", "apaaja", trial=True)
    assert ok, msg
    assert data["protocol"] == "vmess"
    assert data["username"] != "apaaja"
    assert data["days"] == 1

    # limit buat akun (1 per 3 hari)
    ok, msg, data = create_account(d, 123, "ssh", "sunek-xyz", "pw12345")
    assert not ok

    # duplikat
    ok, msg, data = create_account(d, 124, "ssh", "sunek-abc", "pw12345")
    assert not ok

    # expired
    d.add_account(
        username="sunek-exp", protocol="ssh", password="x", duration_days=0,
        created_at="2020-01-01 00:00 WIB", expired_at="2000-01-01 00:00 WIB",
        owner_id=9, is_trial=0,
    )
    assert len(list_expired(d)) == 1


def test_formats() -> None:
    exp = "2026-11-04 12:28 WIB"
    s = F.format_ssh("sunek-abc", "pw12345", "clode2.sunekvip.my.id", 30)
    assert "HOST            : clode2.sunekvip.my.id" in s
    assert "clode2.sunekvip.my.id:443@sunek-abc:pw12345" in s
    assert "http://clode2.sunekvip.my.id:81/myvpn-config.zip" in s
    assert "Dropbear        : 90" in s
    assert F.durasi_text(30) == "30 Days" and F.durasi_text(0) == "0 Days"

    v = F.format_vmess("rm", "host.foo", "uuid-1", "ISP A", "Bogor", exp)
    assert "vmess://" in v and "VMESS WS TLS" in v

    vl = F.format_vless("rm2", "host.foo", "uuid-2", "ISP A", "Bogor", exp)
    assert "vless://uuid-2@host.foo:443?path=/vless&security=tls" in vl
    assert "type=httpupgrade" in vl

    tr = F.format_trojan("rm3", "host.foo", "key-3", "ISP A", "Bogor", exp)
    assert "trojan://key-3@host.foo:443?path=/trojan" in tr and "mode=gun" in tr


def main() -> int:
    test_validation()
    test_accounts()
    test_formats()
    print("Fase 3-4 self-check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
