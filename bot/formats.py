"""Format output akun SSH/VMess/VLESS/Trojan — PERSIS Bagian 12 blueprint.

Setiap output diakhiri tombol "📋 Salin" (ditambahkan di layer bot).
{expired_text} berisi tanggal+jam asli WIB, mis. "2026-11-04 12:28 WIB".
Tidak ada IP VPS di output. {host} = domain.
"""

from __future__ import annotations

import json
import base64

# placeholder nama proto utk header
PROTO_HEADERS = {
    "ssh": "SSH",
    "vmess": "VMESS",
    "vless": "VLESS",
    "trojan": "TROJAN",
}


def _line(ch: str = "—", n: int = 36) -> str:
    return ch * n


def durasi_text(days: int) -> str:
    """'30 Days' / '1 Day' / '5 Hour' sesuai contoh."""
    if days <= 0:
        return "0 Days"
    if days == 1:
        return "1 Day"
    if days < 1:
        # durasi jam (trial)
        return f"{int(days * 24)} Hour"
    return f"{days} Days"


def format_ssh(username: str, password: str, host: str, days: int, pubkey: str = "") -> str:
    return f"""   Account Created Successfully
{_line()}
HOST            : {host}
NameServer      : ns-{host}
Username        : {username}
Password        : {password}
PUB Key         : {pubkey}
{_line()}
Expired         : {durasi_text(days)}
{_line()}
TLS             : 443,8443
None TLS        : 80,8080
Any             : 2082,2083,8880
OpenSSH         : 444
Dropbear        : 90
{_line()}
SlowDNS         : 53,5300
UDP-Custom      : 1-65535
OHP + SSH       : 9080
Squid Proxy     : 3128
UDPGW           : 7100-7600
OpenVPN TCP     : 80,1194
OpenVPN SSL     : 443
OpenVPN UDP     : 25000
OpenVPN DNS     : 53
OHP + OVPN      : 9088

{host}:443@{username}:{password}

{host}:80@{username}:{password}

{_line()}
http://{host}:81/myvpn-config.zip
{_line()}
"""


# ---- helper base64 ----
def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def vmess_json(remarks: str, host: str, uuid: str, port: int, net: str,
              path: str = "", service_name: str = "", tls: bool = False) -> str:
    cfg = {
        "v": "2", "ps": remarks, "add": host, "port": str(port),
        "id": uuid, "aid": "0", "scy": "auto", "net": net, "type": "none",
        "host": host, "path": path, "tls": "tls" if tls else "none",
    }
    if net == "grpc":
        cfg.pop("path", None)
        cfg["serviceName"] = service_name
    if net == "httpupgrade":
        # path dipakai (upgrade path), tapi biarkan path utk upgrade
        pass
    return f"vmess://{_b64(json.dumps(cfg, separators=(',',':')))}"


def format_vmess(remarks: str, host: str, uuid: str, isp: str, city: str,
                 expired_text: str) -> str:
    """Lihat 12.2 — 5 link: WS TLS, WS NOTLS, GRPC, Upgrade TLS, Upgrade NOTLS."""
    ws_tls = vmess_json(remarks, host, uuid, 443, "ws", "/vmess", tls=True)
    ws_nt = vmess_json(remarks, host, uuid, 80, "ws", "/vmess", tls=False)
    grpc = vmess_json(remarks, host, uuid, 443, "grpc", service_name="vmess", tls=True)
    up_tls = vmess_json(remarks, host, uuid, 443, "httpupgrade", "/upvmess", tls=True)
    up_nt = vmess_json(remarks, host, uuid, 80, "httpupgrade", "/upvmess", tls=False)
    return f"""{_line()}
               VMESS
{_line()}
Remarks        : {remarks}
CITY           : {city}
ISP            : {isp}
Domain         : {host}
Port TLS       : 443,8443
Port none TLS  : 80,8080
Port any       : 2052,2053,8880
id             : {uuid}
alterId        : 0
Security       : auto
network        : ws,grpc,upgrade
path ws        : /vmess - /whatever
serviceName    : vmess
path upgrade   : /upvmess
Expired On     : {expired_text}
{_line()}
           VMESS WS TLS
{_line()}
{ws_tls}
{_line()}
          VMESS WS NO TLS
{_line()}
{ws_nt}
{_line()}
             VMESS GRPC
{_line()}
{grpc}
{_line()}
         VMESS Upgrade TLS
{_line()}
{up_tls}
{_line()}
        VMESS Upgrade NO TLS
{_line()}
{up_nt}
{_line()}
"""


def format_vless(remarks: str, host: str, uuid: str, isp: str, city: str,
                 expired_text: str) -> str:
    """Lihat 12.3 — 5 link."""
    ws_tls = f"vless://{uuid}@{host}:443?path=/vless&security=tls&encryption=none&host={host}&type=ws&sni={host}#{remarks}"
    # untuk contoh blueprint path=/vless (tanpa encode slash tetap valid utk banyak client,
    # tapi path di vless biasa disarankan URL-encode "/vless" -> /vless. Ikuti isi blueprint.
    ws_nt = f"vless://{uuid}@{host}:80?path=/vless&security=none&encryption=none&host={host}&type=ws#{remarks}"
    grpc = f"vless://{uuid}@{host}:443?mode=gun&security=tls&encryption=none&type=grpc&serviceName=vless&sni={host}#{remarks}"
    up_tls = f"vless://{uuid}@{host}:443?path=/upvless&security=tls&encryption=none&host={host}&type=httpupgrade&sni={host}#{remarks}"
    up_nt = f"vless://{uuid}@{host}:80?path=/upvless&security=none&encryption=none&host={host}&type=httpupgrade#{remarks}"
    return f"""{_line()}
               VLESS
{_line()}
Remarks        : {remarks}
CITY           : {city}
ISP            : {isp}
Domain         : {host}
Port TLS       : 443,8443
Port none TLS  : 80,8080
Port any       : 2052,2053,8880
id             : {uuid}
Encryption     : none
Network        : ws,grpc,upgrade
Path ws        : /vless
serviceName    : vless
Path upgrade   : /upvless
Expired On     : {expired_text}
{_line()}
            VLESS WS TLS
{_line()}
{ws_tls}
{_line()}
          VLESS WS NO TLS
{_line()}
{ws_nt}
{_line()}
             VLESS GRPC
{_line()}
{grpc}
{_line()}
          VLESS Upgrade TLS
{_line()}
{up_tls}
{_line()}
        VLESS Upgrade NO TLS
{_line()}
{up_nt}
{_line()}
"""


def format_trojan(remarks: str, host: str, key: str, isp: str, city: str,
                  expired_text: str) -> str:
    """Lihat 12.4 — 3 link."""
    ws = f"trojan://{key}@{host}:443?path=/trojan&security=tls&host={host}&type=ws&sni={host}#{remarks}"
    grpc = f"trojan://{key}@{host}:443?mode=gun&security=tls&type=grpc&serviceName=trojan&sni={host}#{remarks}"
    up = f"trojan://{key}@{host}:443?path=/uptrojan&security=tls&host={host}&type=httpupgrade&sni={host}#{remarks}"
    return f"""{_line()}
               TROJAN
{_line()}
Remarks      : {remarks}
CITY         : {city}
ISP          : {isp}
Domain       : {host}
Port         : 443,8443
Port any     : 2052,2053,8880
Key          : {key}
Network      : ws,grpc,upgrade
Path ws      : /trojan
serviceName  : trojan
Path upgrade : /uptrojan
{_line()}
           TROJAN WS TLS
{_line()}
{ws}
{_line()}
            TROJAN GRPC
{_line()}
{grpc}
{_line()}
         TROJAN Upgrade TLS
{_line()}
{up}
{_line()}
Expired On  : {expired_text}
{_line()}
"""
