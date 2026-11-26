"""SlowDNS: pemasangan vaydns-server wajib rilis RESMI, bukan build mirror.

Skill: mirror personal bisa menyajikan build `dev` (lebih besar & lebih baru
dari rilis bertag), flag semantics-nya tak menentu. Test ini mengunci:
  - URL resmi dipakai dan checksum v0.2.8 ada untuk tiap arch
  - `dev` ditolak sebagai versi yang sah
  - unit tidak pernah memuat -dnstt-compat bersama -record-type
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_ID", "1")

from bot import system_manager as sm  # noqa: E402
from _sd_pin import pin_binary  # noqa: E402


def test_checksum_resmi_ada_untuk_semua_arch():
    assert sm._VAYDNS_SHA256["amd64"] == "2db606ab570743950ed5c67af2181199b7c619e5eb793bf6265e9a34729890d3"
    assert sm._VAYDNS_SHA256["arm64"] == "8755e68cc7a0b725da78d811f90670c7042375ad5a9abef400bf6fc1c8af959b"
    assert sm.VAYDNS_VERSION == "0.2.8"


def test_installer_tidak_memakai_mirror_personal():
    import inspect
    src = inspect.getsource(sm.install_vaydns_server)
    assert "net2share/vaydns/releases/download" in src, src
    assert "sunekugg/tunelv2" not in src, "mirror personal masih dipakai: " + src
    # build dev dari mirror tidak boleh lolos
    src_ver = inspect.getsource(sm._vaydns_version)
    assert "dev" in src_ver.lower()


def test_multi_installer_checksum_and_fetch():
    import hashlib
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"slowdns test binary")
        f.flush()
        assert sm._md5_of(f.name) == hashlib.md5(b"slowdns test binary").hexdigest()

    old_exists = sm.os.path.exists
    old_fetch, old_md5 = sm._fetch_bin_opts, sm._md5_of
    try:
        sm.os.path.exists = lambda p: False
        fetched = []
        sm._fetch_bin_opts = lambda name, urls, **kw: (fetched.append((name, urls, kw)), True)[1]
        sm._md5_of = lambda path: sm._SLOWDNS_MULTI_MD5
        ok, msg = sm.install_slowdns_multi_server("amd64")
        assert ok and msg == "slowdns-server multi-record", msg
        assert fetched[0][0] == "slowdns-server"
        assert fetched[0][1] == ["https://raw.githubusercontent.com/sunekugg/tunelv2/main/bin/slowdns-server-linux-amd64"]
        assert fetched[0][2] == {"force": True}
    finally:
        sm.os.path.exists = old_exists
        sm._fetch_bin_opts, sm._md5_of = old_fetch, old_md5


def test_unit_pilih_binary_dan_flag_yang_cocok():
    old_exists = sm.os.path.exists
    try:
        sm.os.path.exists = lambda p: p == sm.SLOWDNS_BIN_MULTI
        multi = sm._slowdns_unit("contoh.com")
        assert "slowdns-server -udp :5300" in multi, multi
        assert "-record-type auto ns-contoh.com 127.0.0.1:10017" in multi, multi
        assert "-domain " not in multi and "-dnstt-compat" not in multi, multi

        sm.os.path.exists = lambda p: p == sm.SLOWDNS_BIN_VAYDNS
        sm.SLOWDNS_COMPAT = False
        native = sm._slowdns_unit("contoh.com")
        assert "vaydns-server" in native and "-domain ns-contoh.com" in native, native
        assert "-clientid-size 2 -record-type txt" in native, native
        sm.SLOWDNS_COMPAT = True
        compat = sm._slowdns_unit("contoh.com")
        assert "-dnstt-compat" in compat and "-record-type" not in compat, compat
    finally:
        sm.os.path.exists = old_exists
        sm.SLOWDNS_COMPAT = False


def test_deteksi_binary_precedence():
    """Kehadiran file tak boleh menentukan wire. Compat harus mengunci vaydns
    (multi-record tak punya -dnstt-compat), SLOWDNS_BINARY menimpa."""
    old_env, old_compat = os.getenv("SLOWDNS_BINARY", ""), sm.SLOWDNS_COMPAT
    real = sm.os.path.exists
    try:
        sm.os.path.exists = lambda p: p == sm.SLOWDNS_BIN_MULTI  # keduanya "ada"
        sm.SLOWDNS_COMPAT = True
        os.environ.pop("SLOWDNS_BINARY", None)
        assert sm.detect_slowdns_binary() == "vaydns", \
            "compat=1 tapi multi-record terpilih: klien dnstt lama akan putus"

        os.environ["SLOWDNS_BINARY"] = "slowdns"
        assert sm.detect_slowdns_binary() == "slowdns", "SLOWDNS_BINARY tak menimpa"

        os.environ.pop("SLOWDNS_BINARY", None)
        sm.SLOWDNS_COMPAT = False
        assert sm.detect_slowdns_binary() == "slowdns", "multi-record (sudah dipasang)"

        sm.os.path.exists = lambda p: p == sm.SLOWDNS_BIN_VAYDNS
        assert sm.detect_slowdns_binary() == "vaydns", "fallback vaydns"
    finally:
        sm.os.path.exists = real
        sm.SLOWDNS_COMPAT = old_compat
        if old_env:
            os.environ["SLOWDNS_BINARY"] = old_env
        else:
            os.environ.pop("SLOWDNS_BINARY", None)


def test_udp_stack_memakai_installer_resmi():
    import inspect
    src = inspect.getsource(sm.install_udp_stack)
    assert "install_vaydns_server(" in src, src
    assert "vaydns-server-linux-" not in src, "URL hardcode masih ada: " + src


def test_flag_saling_eksklusif():
    """-dnstt-compat dan -record-type tak boleh satu ExecStart (terverifikasi
    di binary v0.2.8: compat memaksa txt dan mengabaikan record-type)."""
    old_exists = sm.os.path.exists
    try:
        sm.os.path.exists = lambda p: p == sm.SLOWDNS_BIN_VAYDNS
        for compat in (True, False):
            sm.SLOWDNS_COMPAT = compat
            try:
                u = sm._slowdns_unit("contoh.com")
            finally:
                sm.SLOWDNS_COMPAT = False
            if compat:
                assert "-dnstt-compat" in u and "-record-type" not in u, u
            else:
                assert "-record-type" in u and "-dnstt-compat" not in u, u
    finally:
        sm.os.path.exists = old_exists


def test_binary_terpasang_jika_ada():
    """Bila binary sudah ada di mesin ini, versinya harus rilis, bukan dev."""
    if not os.path.exists("/usr/local/bin/vaydns-server"):
        print("  (skip: binary belum terpasang)")
        return
    ver = sm._vaydns_version()
    assert ver != "dev", f"binary build dev terpasang: {ver!r}"
    assert ver, "binary tak menjawab -v"


if __name__ == "__main__":
    tests = [test_checksum_resmi_ada_untuk_semua_arch,
             test_installer_tidak_memakai_mirror_personal,
             test_multi_installer_checksum_and_fetch,
             test_unit_pilih_binary_dan_flag_yang_cocok,
             test_deteksi_binary_precedence,
             test_udp_stack_memakai_installer_resmi,
             test_flag_saling_eksklusif,
             test_binary_terpasang_jika_ada]
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}", file=sys.stderr)
            sys.exit(1)
