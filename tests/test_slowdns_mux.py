"""Self-check SlowDNS Multi-Protokol mux routing (tanpa framework).

Jalankan: python3 tests/test_slowdns_mux.py
Menguji keputusan rute byte-pertama mux — tidak menyentuh jaringan.
"""
import importlib.util
import os
import pathlib

MUX = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "slowdns_mux.py"


def _load():
    spec = importlib.util.spec_from_file_location("slowdns_mux_test", MUX)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    m = _load()
    tls = bytes([0x16, 0x03, 0x01, 0x01, 0x00]) + os.urandom(256)
    vmess = bytes([1]) + os.urandom(15)          # AEAD: byte kontrol + acak
    vless = bytes([0x00]) + os.urandom(15)       # 0x00 + UUID
    cases = [
        (b"SSH-2.0-OpenSSH\r\n", "dropbear-ssh"),
        (b"GET /vmess HTTP/1.1\r\n\r\n", "xray-vmess"),
        (b"GET /vless HTTP/1.1\r\n\r\n", "xray-vless"),
        (b"GET /trojan HTTP/1.1\r\n\r\n", "xray-trojan"),
        (b"GET / HTTP/1.1\r\n\r\n", "dropbear-101"),
        (tls, "tls"),
        (vmess, "xray-tcp"),
        (vless, "xray-tcp"),
    ]
    for data, want in cases:
        got = m._route_from_bytes(data)
        assert got == want, f"{data[:16]!r} -> {got}, want {want}"
    # kontrol negatif: TLS palsu (rec_len tak wajar) TIDAK boleh jadi tls
    bad = bytes([0x16, 0x03, 0xff, 0xff, 0xff]) + os.urandom(20)
    assert m._route_from_bytes(bad) != "tls"
    # HTTP prefix HARUS menang atas uji biner (GET / punya \r di byte 16)
    assert m._route_from_bytes(b"GET / HTTP/1.1\r\n" + os.urandom(20)) == "dropbear-101"
    # target backend
    assert m.XRAY_WS == {"vmess": 10007, "vless": 10004, "trojan": 10001}
    print("test_slowdns_mux: OK")


if __name__ == "__main__":
    main()
