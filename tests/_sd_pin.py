"""Pin deteksi binary SlowDNS di dalam test (tanpa framework).

Generator unit memilih binary dari keberadaan file di disk. Di mesin yang punya
keduanya, assert flag vaydns akan gagal hanya karena `slowdns-server` ada.
Pakai `pin_binary(sm, "vaydns")` sebagai context manager.

    from _sd_pin import pin_binary
    with pin_binary(sm, "vaydns"):
        u = sm._slowdns_unit("contoh.com")
"""
import contextlib


@contextlib.contextmanager
def pin_binary(sm, which: str):
    path = sm.SLOWDNS_BIN_MULTI if which == "slowdns" else sm.SLOWDNS_BIN_VAYDNS
    real = sm.os.path.exists
    sm.os.path.exists = lambda p, _r=real: (p == path) or (_r(p) if p != sm.SLOWDNS_BIN_MULTI
                                                          and p != sm.SLOWDNS_BIN_VAYDNS else False)
    try:
        yield
    finally:
        sm.os.path.exists = real
