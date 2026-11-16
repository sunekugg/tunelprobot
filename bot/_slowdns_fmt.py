"""Helper SlowDNS Multi-Protokol (Xray) — dipakai formatter VMess/VLESS/Trojan.

Satu NS domain + satu Public Key melayani SSH & semua proto Xray. Jalur DNS tak
sanggup double-TLS, jadi Xray-over-SlowDNS = WebSocket Non-TLS (security=none),
Address = NS domain, path = /<proto>.
"""
import base64 as _b64
import json as _json
import html as _html

# Samakan dengan SEP di formatter.py (em-dash) agar garis pemisah output rata,
# bukan campuran '————' dan '----' yang bikin tampilan acak.
SEP = "————————————————————————————————————"


def slowdns_pubkey() -> str:
    try:
        with open("/etc/slowdns/server.pub", encoding="utf-8") as f:
            return f.read().strip() or "-"
    except Exception:
        return "-"


def slowdns_ns_host(domain: str) -> str:
    d = (domain or "").strip()
    if not d:
        return "-"
    return d if d.startswith("ns-") else f"ns-{d}"


def slowdns_link(proto: str, ns_host: str, domain: str, secret: str, remark: str) -> str:
    """URI klien untuk Xray di atas SlowDNS (WebSocket Non-TLS)."""
    proto = (proto or "trojan").lower().strip()
    path = f"/{proto}"
    tag = f"{remark}-SlowDNS"
    if proto == "vmess":
        vm = {"v": "2", "ps": tag, "add": ns_host, "port": "80", "id": secret,
              "aid": "0", "scy": "auto", "net": "ws", "type": "none",
              "host": domain, "path": path, "tls": ""}
        return "vmess://" + _b64.b64encode(
            _json.dumps(vm, separators=(",", ":")).encode()).decode()
    if proto == "vless":
        return (f"vless://{secret}@{ns_host}:80?path={path}&security=none"
                f"&encryption=none&host={domain}&type=ws&headerType=none#{tag}")
    return (f"trojan://{secret}@{ns_host}:80?path={path}&security=none"
            f"&host={domain}&type=ws&headerType=none#{tag}")


def slowdns_block(proto: str, domain: str) -> str:
    """Blok info <pre> untuk output akun Xray saat SlowDNS aktif."""
    ns_host = slowdns_ns_host(domain)
    pub = slowdns_pubkey()
    return (
        f"{SEP}\n        SlowDNS Multi-Protokol\n{SEP}\n"
        f"NS Domain      : {_html.escape(ns_host)}\n"
        f"PUBLIC KEY     : {_html.escape(pub)}\n"
        f"DNS Port       : 53 / 5300 (UDP)\n"
        f"Mode           : WebSocket Non-TLS (NTLS)"
    )
