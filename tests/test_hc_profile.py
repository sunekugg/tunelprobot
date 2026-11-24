#!/usr/bin/env python3
"""Self-check profil SlowDNS HC (klien HTTP Custom) + alamat bind.

Pola acuan (VPS yang sudah jalan):
    /opt/a4n-slowdnshc/slowdns -udp IP:53 -privkey-file KEY TUNNEL 127.0.0.1:90
    profile.json {"ns": "ns-sd.HOST", "domain": "dns-sd.HOST", "host": "HOST"}

Klien HTTP Custom membaca:
    DNS           : HOST:53          <- A record HOST -> IP VPS
    NS            : dns-sd.HOST      <- domain yang di-delegasi NS
    Public Key    : <64 hex>
    SSH Host:Port : HOST
    Mode          : Otomatis
"""
import re
import sys

HOST_RE = re.compile(r"^(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
FAKE_PUB = "07b947a0aafdba1c3a60229f4db59ce1c8bde8081a694b5d153e92d3826a9619"


# --- profil klien -----------------------------------------------------------

def hc_profile(host: str, tunnel: str, key: str) -> dict:
    """host = A-record yang resolve ke IP VPS, tunnel = domain delegasi NS.
    Keduanya dinormalkan ke huruf kecil tanpa titik akhir."""
    host = (host or "").strip().lower().rstrip(".")
    tunnel = (tunnel or "").strip().lower().rstrip(".")
    key = (key or "").strip()
    if not HOST_RE.match(host):
        raise ValueError("host tidak valid")
    if not HOST_RE.match(tunnel):
        raise ValueError("tunnel tidak valid")
    if not KEY_RE.match(key):
        raise ValueError("pubkey harus hex 64 digit")
    return {"dns": f"{host}:53", "ns": tunnel, "key": key, "host": host}


def render_hc(profile: dict) -> str:
    return (
        "SlowdnsHC / HTTP Custom (build 940+ disarankan)\n"
        "Mode SSH      : Standard → SlowDNS\n"
        "Tipe          : Otomatis\n"
        f"DNS           : {profile['dns']}\n"
        f"NS            : {profile['ns']}\n"
        f"Public Key    : {profile['key']}\n"
        f"SSH Host:Port : {profile['host']}"
    )


def parse_hc(text: str) -> dict:
    """Parse balik profil — cermin cara klien membaca barisnya."""
    out = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip()
        value = value.strip()
        if label == "DNS":
            host, _, port = value.rpartition(":")
            if port != "53":
                raise ValueError("DNS wajib port 53")
            out["host"] = host
        elif label == "NS":
            out["ns"] = value
        elif label == "Public Key":
            out["key"] = value
        elif label == "SSH Host:Port":
            out["ssh"] = value
    return out


def test_roundtrip():
    p = hc_profile("atha.example.com", "dns-sd.atha.example.com", FAKE_PUB)
    back = parse_hc(render_hc(p))
    assert back["host"] == "atha.example.com", back
    assert back["ns"] == "dns-sd.atha.example.com", back
    assert back["key"] == FAKE_PUB, back


def test_normalizes_upper_and_trailing_dot():
    p = hc_profile("ATHA.Example.COM.", "DNS-SD.atha.example.com.", FAKE_PUB)
    assert p["host"] == "atha.example.com", p
    assert p["ns"] == "dns-sd.atha.example.com", p


def test_rejects_bad_key():
    for bad in ("abc123", "", "z" * 64, FAKE_PUB + "0"):
        try:
            hc_profile("a.example.com", "b.example.com", bad)
        except ValueError:
            continue
        raise AssertionError(f"harus gagal utk key={bad!r}")


def test_rejects_bad_host():
    for bad in ("", "no-dot", "sp ace.example.com", "-bad.example.com"):
        try:
            hc_profile(bad, "b.example.com", FAKE_PUB)
        except ValueError:
            continue
        raise AssertionError(f"harus gagal utk host={bad!r}")


def test_render_has_all_labels():
    text = render_hc(hc_profile("atha.example.com", "dns-sd.atha.example.com", FAKE_PUB))
    for label in ("DNS", "NS", "Public Key", "SSH Host:Port", "Otomatis", "SlowDNS"):
        assert label in text, f"label {label!r} hilang"


# --- alamat bind slowdns ----------------------------------------------------

def slowdns_listen_addr(public_ip: str, port: int = 53) -> str:
    """Alamat bind slowdns. Pola acuan: IP publik langsung di port 53,
    bukan '*:5300' + REDIRECT iptables (pola lama tunnel-bot).
    Wajib IPv4 global agar bisa jadi NS authoritative."""
    import ipaddress
    addr = ipaddress.IPv4Address(public_ip)
    if not addr.is_global:
        raise ValueError("perlu IPv4 publik")
    if not (1 <= port <= 65535):
        raise ValueError("port tidak valid")
    return f"{addr}:{port}"


def test_listen_addr_direct_ip53():
    assert slowdns_listen_addr("103.155.224.145") == "103.155.224.145:53"


def test_listen_addr_rejects_non_global():
    for bad in ("0.0.0.0", "127.0.0.1", "10.0.0.1", "169.254.1.1", "192.168.1.1"):
        try:
            slowdns_listen_addr(bad)
        except ValueError:
            continue
        raise AssertionError(f"harus gagal utk {bad}")


def test_listen_addr_port_validation():
    for bad_port in (0, -1, 65536):
        try:
            slowdns_listen_addr("103.155.224.145", bad_port)
        except ValueError:
            continue
        raise AssertionError(f"port {bad_port} harus ditolak")


if __name__ == "__main__":
    tests = [
        test_roundtrip, test_normalizes_upper_and_trailing_dot,
        test_rejects_bad_key, test_rejects_bad_host, test_render_has_all_labels,
        test_listen_addr_direct_ip53, test_listen_addr_rejects_non_global,
        test_listen_addr_port_validation,
    ]
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}", file=sys.stderr)
            sys.exit(1)
    print(f"\n{len(tests)} test pass.")
