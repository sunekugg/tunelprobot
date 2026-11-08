#!/usr/bin/env python3
"""Self-check Fase 5: menus, render account, admin gate. Tanpa jaringan.

python3 tests/test_fase5.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bot import menus as M  # noqa: E402
from bot.db import DB  # noqa: E402
from bot.config import config  # noqa: E402
from bot.accounts import create_account  # noqa: E402
from bot.main import _render_account  # noqa: E402


def test_menus() -> None:
    kb = M.user_menu(is_admin=False)
    # hitung tombol admin TIDAK muncul untuk user biasa
    data = kb.to_dict()
    rows = data.get("inline_keyboard", [])
    all_texts = [btn["text"] for row in rows for btn in row]
    assert not any("Panel Administrator" in t for t in all_texts)
    # admin
    kba = M.user_menu(is_admin=True)
    all_texts_a = [btn["text"] for row in kba.to_dict()["inline_keyboard"] for btn in row]
    assert any("Panel Administrator" in t for t in all_texts_a)
    # admin_menu ada tombol "Back to Menu User"
    kbm = M.admin_menu()
    admin_texts = [btn["text"] for row in kbm.to_dict()["inline_keyboard"] for btn in row]
    assert any("Kembali ke Menu User" in t for t in admin_texts)


def test_render(d: DB) -> None:
    config.set("DOMAIN", "clode2.sunekvip.my.id")
    ok, msg, data = create_account(d, 123, "ssh", "sunek-x1", "pw12345", days=5)
    assert ok, msg
    rendered = _render_account(d, data)
    assert "sunek-x1" in rendered and "clode2.sunekvip.my.id" in rendered
    assert data["host"]


def main() -> int:
    d = DB(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    test_menus()
    test_render(d)
    print("Fase 5 self-check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
