"""RED: SlowDNS harus mendukung resolver binary terbaru (VayDNS >=0.2.7).

- Mode default: pakai `-record-type` + `-clientid-size`, TANPA `-dnstt-compat`.
- Mode compat (`SLOWDNS_COMPAT=1`): pakai `-dnstt-compat`, record-type TIDAK
  boleh muncul (VayDNS memaksanya ke txt).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_ID", "1")

from bot import system_manager as sm  # noqa: E402
from _sd_pin import pin_binary  # noqa: E402


def test_compat_mode_absent_record_type():
    with pin_binary(sm, "vaydns"):
        sm.SLOWDNS_COMPAT = True
        try:
            unit = sm._slowdns_unit("example.com")
        finally:
            sm.SLOWDNS_COMPAT = False
    assert "-dnstt-compat" in unit, unit
    assert "-record-type" not in unit, unit


if __name__ == "__main__":
    for fn in (test_compat_mode_absent_record_type,):
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            sys.exit(1)
