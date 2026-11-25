"""RED: SlowDNS harus mendukung record type non-TXT untuk resolver binary terbaru."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_ID", "1")

from bot import system_manager as sm  # noqa: E402
from _sd_pin import pin_binary  # noqa: E402


def test_unit_memakai_record_type_bukan_dnstt_compat():
    with pin_binary(sm, "vaydns"):
        sm.SLOWDNS_RECORD_TYPE = "cname"
        try:
            u = sm._slowdns_unit("contoh.com")
        finally:
            sm.SLOWDNS_RECORD_TYPE = "txt"
    assert "-dnstt-compat" not in u, u
    assert "-record-type cname" in u, u
    assert "-clientid-size 2" in u, u


def test_unit_mode_compat_tetap_dipakai_jika_diminta():
    with pin_binary(sm, "vaydns"):
        sm.SLOWDNS_COMPAT = True
        try:
            u = sm._slowdns_unit("contoh.com")
        finally:
            sm.SLOWDNS_COMPAT = False
    assert "-dnstt-compat" in u, u
    assert "-record-type" not in u, u


def test_record_type_divalidasi():
    # Input case dinormalkan, bukan ditolak: "TXT" -> "txt". Yang ditolak hanya
    # tipe di luar himpunan tutup (cegah salah ketik = unit rusak).
    assert sm.normalize_record_type("TXT") == "txt"
    assert sm.normalize_record_type("  CNAME ") == "cname"
    for bad in ("any", "cname;rm -rf /", "", "txt " + "x", "null;nc"):
        try:
            sm.normalize_record_type(bad)
        except ValueError:
            continue
        raise AssertionError(f"harus ditolak: {bad!r}")


def test_profil_klien_menampilkan_record_type():
    from bot.formatter import format_hc_block
    block = format_hc_block("contoh.com", "u", "a" * 64, record_type="cname")
    assert "Record Type    : cname" in block, block


if __name__ == "__main__":
    tests = [test_unit_memakai_record_type_bukan_dnstt_compat,
             test_unit_mode_compat_tetap_dipakai_jika_diminta,
             test_record_type_divalidasi,
             test_profil_klien_menampilkan_record_type]
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}", file=sys.stderr)
            sys.exit(1)
