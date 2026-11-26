"""Pin deteksi binary SlowDNS di dalam test (tanpa framework).

Generator unit memilih binary dari detect_slowdns_binary() (env + keberadaan
file). Di mesin yang punya keduanya, assert flag vaydns akan gagal hanya karena
`slowdns-server` ada. Pakai `pin_binary(sm, "vaydns")` sebagai context manager.

    from _sd_pin import pin_binary
    with pin_binary(sm, "vaydns"):
        u = sm._slowdns_unit("contoh.com")

pin_binary juga memaksa SLOWDNS_MODE="mux" (unit legacy dgn -domain/-upstream),
karena default instalasi baru = direct (multi-record, :53 -> :90).
"""
import contextlib
import os


@contextlib.contextmanager
def pin_binary(sm, which: str):
    path = sm.SLOWDNS_BIN_MULTI if which == "slowdns" else sm.SLOWDNS_BIN_VAYDNS
    real_exists, real_compat = sm.os.path.exists, sm.SLOWDNS_COMPAT
    real_mode = sm.SLOWDNS_MODE
    old_forced = os.environ.get("SLOWDNS_BINARY")
    # Force eksplisit: SLOWDNS_COMPAT=1 dari config.env bisa mengalihkan pilihan
    os.environ["SLOWDNS_BINARY"] = which
    sm.SLOWDNS_MODE = "mux"
    sm.os.path.exists = lambda p, _r=real_exists: (p == path) or (
        _r(p) if p not in (sm.SLOWDNS_BIN_MULTI, sm.SLOWDNS_BIN_VAYDNS) else False)
    try:
        yield
    finally:
        sm.os.path.exists = real_exists
        sm.SLOWDNS_COMPAT = real_compat
        sm.SLOWDNS_MODE = real_mode
        if old_forced is None:
            os.environ.pop("SLOWDNS_BINARY", None)
        else:
            os.environ["SLOWDNS_BINARY"] = old_forced
