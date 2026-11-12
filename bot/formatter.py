from __future__ import annotations
import os
import sys
import time
import json
import html
import re
import asyncio
import datetime
import base64
import html
import json
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from .system_manager import get_short_isp
try:
    from aiogram.types import CopyTextButton
    HAS_COPY_BTN = True
except ImportError:
    HAS_COPY_BTN = False

SEP = "————————————————————————————————————"

def _make_copy_btn(label: str, text_to_copy: str, fallback_cb: str = "copy_hint") -> InlineKeyboardButton:
    if HAS_COPY_BTN and 1 <= len(text_to_copy) <= 256:
        return InlineKeyboardButton(text=label, copy_text=CopyTextButton(text=text_to_copy))
    return InlineKeyboardButton(text=label, callback_data=fallback_cb)

def build_vless_messages(remark: str, city: str, isp: str, domain: str, uuid_val: str, limit_ip: int, expired_display: str) -> list[tuple[str, InlineKeyboardMarkup]]:
    isp_short = html.escape(get_short_isp(isp))
    r_esc, c_esc, i_esc, d_esc, u_esc, e_esc = map(html.escape, (remark, city, isp, domain, uuid_val, expired_display))
    lim_str = "Unlimited" if int(limit_ip) == 0 else str(limit_ip)

    info_pre = (
        f"<pre>ID • {isp_short}:\n{SEP}\n               VLESS\n{SEP}\n"
        f"Remarks        : {r_esc}\nCITY           : {c_esc}\nISP            : {i_esc}\n"
        f"Domain         : {d_esc}\nPort TLS       : 443,8443\nPort none TLS  : 80,8080\n"
        f"Port any       : 2052,2053,8880\n"
        f"id             : {u_esc}\nEncryption     : none\nNetwork        : ws,grpc,upgrade\n"
        f"Path ws        : /vless\nserviceName    : vless\nPath upgrade   : /upvless\n"
        f"Limit IP       : {lim_str}\nExpired On     : {e_esc}\n{SEP}</pre>"
    )
    info_kb = InlineKeyboardMarkup(inline_keyboard=[[_make_copy_btn("📋 Salin UUID", uuid_val)]])

    blocks = [
        ("            VLESS WS TLS", f"vless://{uuid_val}@{domain}:443?path=/vless&security=tls&encryption=none&host={domain}&type=ws&sni={domain}#{remark}"),
        ("          VLESS WS NO TLS", f"vless://{uuid_val}@{domain}:80?path=/vless&security=none&encryption=none&host={domain}&type=ws#{remark}"),
        ("             VLESS GRPC", f"vless://{uuid_val}@{domain}:443?mode=gun&security=tls&encryption=none&type=grpc&serviceName=vless&sni={domain}#{remark}"),
        ("         VLESS Upgrade TLS", f"vless://{uuid_val}@{domain}:443?path=/upvless&security=tls&encryption=none&host={domain}&type=httpupgrade&sni={domain}#{remark}"),
        ("        VLESS Upgrade NO TLS", f"vless://{uuid_val}@{domain}:80?path=/upvless&security=none&encryption=none&host={domain}&type=httpupgrade#{remark}"),
    ]

    messages = [(info_pre, info_kb)]
    for idx, (title_line, raw_link) in enumerate(blocks):
        msg_text = f"<pre>{SEP}\n{title_line}\n{SEP}</pre>\n<pre>{html.escape(raw_link)}</pre>"
        rows = [[_make_copy_btn("📋 Salin Link", raw_link)]]
        if idx == len(blocks) - 1:
            rows.append([InlineKeyboardButton(text="⬅️ Kembali ke Menu", callback_data="menu_main")])
        messages.append((msg_text, InlineKeyboardMarkup(inline_keyboard=rows)))
    return messages

def _encode_vmess_json(obj: dict) -> str:
    raw_json = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    link = f"vmess://{base64.b64encode(raw_json.encode('utf-8')).decode('ascii')}"
    if len(link) > 256:
        slim = {k: v for k, v in obj.items() if k not in ("sni", "alpn", "scy", "type") and v != ""}
        raw_json = json.dumps(slim, separators=(",", ":"), ensure_ascii=False)
        link = f"vmess://{base64.b64encode(raw_json.encode('utf-8')).decode('ascii')}"
    if len(link) > 256:
        slim2 = {k: v for k, v in slim.items() if k != "aid"}
        raw_json = json.dumps(slim2, separators=(",", ":"), ensure_ascii=False)
        link = f"vmess://{base64.b64encode(raw_json.encode('utf-8')).decode('ascii')}"
    return link

def build_vmess_messages(remark: str, city: str, isp: str, domain: str, uuid_val: str, limit_ip: int, expired_display: str) -> list[tuple[str, InlineKeyboardMarkup]]:
    isp_short = html.escape(get_short_isp(isp))
    r_esc, c_esc, i_esc, d_esc, u_esc, e_esc = map(html.escape, (remark, city, isp, domain, uuid_val, expired_display))
    lim_str = "Unlimited" if int(limit_ip) == 0 else str(limit_ip)

    info_pre = (
        f"<pre>ID • {isp_short}:\n{SEP}\n               VMESS\n{SEP}\n"
        f"Remarks        : {r_esc}\nCITY           : {c_esc}\nISP            : {i_esc}\n"
        f"Domain         : {d_esc}\nPort TLS       : 443,8443\nPort none TLS  : 80,8080\n"
        f"Port any       : 2052,2053,8880\n"
        f"id             : {u_esc}\nalterId        : 0\nSecurity       : auto\n"
        f"network        : ws,grpc,upgrade\npath ws        : /vmess\nserviceName    : vmess\n"
        f"path upgrade   : /upvmess\nLimit IP       : {lim_str}\nExpired On     : {e_esc}\n{SEP}</pre>"
    )
    info_kb = InlineKeyboardMarkup(inline_keyboard=[[_make_copy_btn("📋 Salin UUID", uuid_val)]])

    blocks = [
        ("            VMESS WS TLS", _encode_vmess_json({"v": "2", "ps": remark, "add": domain, "port": 443, "id": uuid_val, "aid": 0, "scy": "auto", "net": "ws", "type": "none", "host": domain, "path": "/vmess", "tls": "tls", "sni": domain})),
        ("          VMESS WS NO TLS", _encode_vmess_json({"v": "2", "ps": remark, "add": domain, "port": 80, "id": uuid_val, "aid": 0, "scy": "auto", "net": "ws", "type": "none", "host": domain, "path": "/vmess", "tls": ""})),
        ("             VMESS GRPC", _encode_vmess_json({"v": "2", "ps": remark, "add": domain, "port": 443, "id": uuid_val, "aid": 0, "scy": "auto", "net": "grpc", "type": "gun", "host": domain, "path": "vmess", "tls": "tls", "sni": domain})),
        ("         VMESS Upgrade TLS", _encode_vmess_json({"v": "2", "ps": remark, "add": domain, "port": 443, "id": uuid_val, "aid": 0, "scy": "auto", "net": "httpupgrade", "type": "none", "host": domain, "path": "/upvmess", "tls": "tls", "sni": domain})),
        ("        VMESS Upgrade NO TLS", _encode_vmess_json({"v": "2", "ps": remark, "add": domain, "port": 80, "id": uuid_val, "aid": 0, "scy": "auto", "net": "httpupgrade", "type": "none", "host": domain, "path": "/upvmess", "tls": ""})),
    ]

    messages = [(info_pre, info_kb)]
    for idx, (title_line, raw_link) in enumerate(blocks):
        msg_text = f"<pre>{SEP}\n{title_line}\n{SEP}</pre>\n<pre>{html.escape(raw_link)}</pre>"
        rows = [[_make_copy_btn("📋 Salin Link", raw_link)]]
        if idx == len(blocks) - 1:
            rows.append([InlineKeyboardButton(text="⬅️ Kembali ke Menu", callback_data="menu_main")])
        messages.append((msg_text, InlineKeyboardMarkup(inline_keyboard=rows)))
    return messages

async def send_multi_part_output(target_msg: Message, parts: list[tuple[str, InlineKeyboardMarkup]], replace_first: bool = False):
    for idx, (txt, kb) in enumerate(parts):
        if idx == 0 and replace_first:
            try:
                await target_msg.edit_text(txt, reply_markup=kb)
                continue
            except Exception:
                pass
        await target_msg.answer(txt, reply_markup=kb)

def format_trojan_output(remark: str, city: str, isp: str, domain: str, uuid_val: str, limit_ip: int, expired_display: str) -> tuple[str, InlineKeyboardMarkup]:
    isp_short = html.escape(get_short_isp(isp))
    r_esc, c_esc, i_esc, d_esc, u_esc, e_esc = map(html.escape, (remark, city, isp, domain, uuid_val, expired_display))
    lim_str = "Unlimited" if int(limit_ip) == 0 else str(limit_ip)

    ws_link = f"trojan://{uuid_val}@{domain}:443?path=/trojan&security=tls&host={domain}&type=ws&sni={domain}#{remark}"
    grpc_link = f"trojan://{uuid_val}@{domain}:443?mode=gun&security=tls&type=grpc&serviceName=trojan&sni={domain}#{remark}"
    upg_link = f"trojan://{uuid_val}@{domain}:443?path=/uptrojan&security=tls&host={domain}&type=httpupgrade&sni={domain}#{remark}"

    block_info = (
        f"ID • {isp_short}:\n{SEP}\n               TROJAN\n{SEP}\n"
        f"Remarks        : {r_esc}\nCITY           : {c_esc}\nISP            : {i_esc}\n"
        f"Domain         : {d_esc}\nPort TLS       : 443,8443\nPort none TLS  : 80,8080\n"
        f"Port any       : 2052,2053,8880\nKey            : {u_esc}\n"
        f"Network        : ws,grpc,upgrade\nPath ws        : /trojan\nserviceName    : trojan\n"
        f"Path upgrade   : /uptrojan\nLimit IP       : {lim_str}\nExpired On     : {e_esc}\n{SEP}"
    )
    text = (
        f"<pre>{block_info}</pre>\n"
        f"<pre>{SEP}\n            TROJAN WS TLS\n{SEP}</pre>\n<pre>{html.escape(ws_link)}</pre>\n"
        f"<pre>{SEP}\n             TROJAN GRPC\n{SEP}</pre>\n<pre>{html.escape(grpc_link)}</pre>\n"
        f"<pre>{SEP}\n         TROJAN Upgrade TLS\n{SEP}</pre>\n<pre>{html.escape(upg_link)}</pre>"
    )
    rows = [
        [_make_copy_btn("📋 Salin Key / UUID", uuid_val)],
        [_make_copy_btn("📋 Salin Link WS TLS", ws_link)],
        [_make_copy_btn("📋 Salin Link gRPC", grpc_link)],
        [_make_copy_btn("📋 Salin Link HTTPUpgrade", upg_link)],
        [InlineKeyboardButton(text="⬅️ Kembali ke Menu", callback_data="menu_main")]
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

def _slowdns_pubkey() -> str:
    """Public key SlowDNS dari /etc/slowdns/server.pub (hanya key publik, 64 hex)."""
    try:
        with open("/etc/slowdns/server.pub", encoding="utf-8") as f:
            v = f.read().strip()
        return v or "-"
    except Exception:
        return "-"

def format_ssh_output(domain: str, user: str, pwd: str, limit_ip: int, expired_display: str, show_pass: bool = True) -> tuple[str, InlineKeyboardMarkup]:
    d_esc, u_esc, e_esc = map(html.escape, (domain, user, expired_display))
    real_pwd = pwd if show_pass else "********"
    p_esc = html.escape(real_pwd)
    lim_str = "Unlimited" if int(limit_ip) == 0 else str(limit_ip)
    ns_host = domain if domain.startswith("ns-") else f"ns-{domain}"
    pub_key = _slowdns_pubkey()

    line_tls = f"{domain}:443@{user}:{real_pwd}"
    line_ntls = f"{domain}:80@{user}:{real_pwd}"
    line_dns = f"{ns_host}:5300@{user}:{real_pwd}"
    ovpn_url = f"http://{domain}:81/myvpn-config.zip"

    block_info = (
        f"Account Created Successfully\n------------------------------------\n"
        f"HOST            : {d_esc}\nUsername        : {u_esc}\nPassword        : {p_esc}\n"
        f"Limit IP        : {lim_str}\n------------------------------------\n"
        f"Expired On      : {e_esc}\n------------------------------------"
    )
    block_ports = (
        "WS TLS          : 443,8443,2053,2083\nWS Non-TLS      : 80,8080,2052,2082,8880\n"
        "OpenSSH         : 22,444\nDropbear 2019   : 90,109\nSSL/TLS Direct  : 442,443\n"
        "------------------------------------\n"
        f"SlowDNS         : 53,5300\nNS SlowDNS      : {ns_host}\n"
        f"PUBLIC KEY      : {pub_key}\n"
        "UDP-Custom      : 1-65535\n"
        "UDPGW (badvpn)  : 7300\n"
        "OHP + SSH       : 9080\nSquid Proxy     : 3128\n"
        "OpenVPN TCP     : 1194\nOpenVPN SSL     : 442"
    )
    text = (
        f"<pre>{block_info}</pre>\n<pre>{html.escape(block_ports)}</pre>\n"
        f"<pre>{html.escape(line_tls)}</pre>\n<pre>{html.escape(line_ntls)}</pre>\n"
        f"<pre>{html.escape(line_dns)}</pre>\n"
        f"<pre>------------------------------------\n{html.escape(ovpn_url)}\n------------------------------------</pre>"
    )
    rows = []
    if show_pass:
        rows.append([_make_copy_btn("📋 Salin Password", pwd)])
        rows.append([_make_copy_btn("📋 Salin Format TLS (443)", line_tls)])
        rows.append([_make_copy_btn("📋 Salin Format Non-TLS (80)", line_ntls)])
        rows.append([_make_copy_btn("📋 Salin SlowDNS (5300)", line_dns)])
    if pub_key != "-":
        rows.append([_make_copy_btn("📋 Salin Public Key SlowDNS", pub_key)])
    rows.append([InlineKeyboardButton(text="⬅️ Kembali ke Menu", callback_data="menu_main")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

def parse_external_xray_link(raw_text: str) -> dict | None:
    import re
    import json
    import base64
    import urllib.parse

    try:
        txt = (raw_text or "").strip()
        m = re.search(r"(vmess|vless|trojan)://[^\s<>\"']+", txt, re.IGNORECASE)
        if not m:
            return None

        # Jika ada spasi pada nama remark di ujung link, ambil seluruh barisnya
        line_match = re.search(r"(vmess|vless|trojan)://[^\r\n]+", txt, re.IGNORECASE)
        uri = (line_match.group(0) if line_match else m.group(0)).strip()
        proto = m.group(1).lower()

        if proto == "vmess":
            b64_part = uri[8:].strip()
            # Tangani jika ada #fragment di luar base64 vmess
            frag_remark = ""
            if "#" in b64_part:
                b64_part, frag_remark = b64_part.split("#", 1)
                frag_remark = urllib.parse.unquote(frag_remark).strip()

            b64_clean = re.sub(r"[^A-Za-z0-9+/=_=-]", "", b64_part)
            b64_clean += "=" * (-len(b64_clean) % 4)
            raw_json = base64.urlsafe_b64decode(b64_clean.encode("ascii")).decode("utf-8", errors="ignore")
            data = json.loads(raw_json)

            secret = str(data.get("id") or "").strip()
            add = str(data.get("add") or "").strip()
            host = str(data.get("host") or "").strip()
            sni = str(data.get("sni") or "").strip()
            path = str(data.get("path") or "").strip() or "/vmess"
            remark = str(data.get("ps") or frag_remark or "VMess-User").strip()
        else:
            # Pisahkan fragment (#remark) terlebih dahulu agar spasi di remark tidak merusak urlsplit
            if "#" in uri:
                base_uri, frag = uri.split("#", 1)
                remark = urllib.parse.unquote(frag).strip()
            else:
                base_uri = uri
                remark = f"{proto.upper()}-User"

            parsed = urllib.parse.urlsplit(base_uri)
            secret = urllib.parse.unquote(parsed.username or "").strip()
            add = (parsed.hostname or "").strip()
            qs = urllib.parse.parse_qs(parsed.query)
            host = (qs.get("host", [""])[0] or "").strip()
            sni = (qs.get("sni", [""])[0] or "").strip()
            path = urllib.parse.unquote(qs.get("path", [""])[0] or "").strip() or f"/{proto}"

        if not secret or not (add or host or sni):
            return None

        if not path.startswith("/"):
            path = "/" + path

        remark = re.sub(r"-(TLS|NTLS)$", "", remark, flags=re.IGNORECASE).strip() or f"{proto.upper()}-User"
        domain = host or sni or add

        return {
            "proto": proto,
            "secret": secret,
            "domain": domain,
            "orig_add": add or domain,
            "path": path,
            "remark": remark[:28]
        }
    except Exception as e:
        print(f"[PARSE EXT LINK ERROR] {e}")
        return None

# === SING-BOX JSON & QR CODE GENERATOR ===
_INJECT_PAYLOAD_CACHE: dict[str, dict] = {}


def build_singbox_outbound_json(
    proto: str,
    name_tls: str,
    name_ntls: str,
    secret: str,
    address: str,
    host: str,
    sni: str,
    path_raw: str
) -> str:
    import json
    p = proto.lower().strip()

    def _make_node(tag: str, port: int, use_tls: bool) -> dict:
        node = {
            "type": p,
            "tag": tag,
            "server": address,
            "server_port": port,
        }
        if p == "vmess":
            node["uuid"] = secret
            node["security"] = "auto"
            node["alter_id"] = 0
        elif p == "vless":
            node["uuid"] = secret
        else:
            node["password"] = secret

        if use_tls:
            node["tls"] = {
                "enabled": True,
                "server_name": sni,
                "insecure": True,
                "alpn": ["http/1.1"]
            }
        node["transport"] = {
            "type": "ws",
            "path": path_raw,
            "headers": {"Host": host}
        }
        return node

    cfg = {
        "outbounds": [
            _make_node(name_tls, 443, True),
            _make_node(name_ntls, 80, False)
        ]
    }
    return json.dumps(cfg, indent=2, ensure_ascii=False)


def build_injected_xray_html(
    proto: str,
    remark: str,
    secret: str,
    server_name: str,
    domain: str,
    bug: str,
    mode: str = "wildcard",
    acc_id: int = 0,
    custom_path: str = "",
    is_external: bool = False,
    cache_id: str = ""
) -> tuple[str, InlineKeyboardMarkup]:
    import html
    import json
    import base64
    import hashlib
    import urllib.parse

    proto = proto.lower().strip()
    bug = bug.strip() or domain.strip()
    domain = domain.strip()
    path_raw = (custom_path.strip() if custom_path else f"/{proto}")
    if not path_raw.startswith("/"):
        path_raw = "/" + path_raw
    path_enc = urllib.parse.quote(path_raw, safe="")

    if mode == "wildcard":
        address = bug
        subdomain = f"{bug}.{domain}" if not bug.endswith(f".{domain}") and bug != domain else bug
        sni = subdomain
        host = subdomain
        mode_label = "Wildcard (bug.domain)"
    elif mode == "sni":
        address = domain
        subdomain = bug
        sni = bug
        host = bug
        mode_label = "SNI / SSL (Spoof Bug)"
    else:
        address = bug
        subdomain = domain
        sni = domain
        host = domain
        mode_label = "CDN / Direct WS"

    name_tls_raw = f"{remark}-TLS"
    name_ntls_raw = f"{remark}-NTLS"
    name_tls_enc = urllib.parse.quote(name_tls_raw, safe="")
    name_ntls_enc = urllib.parse.quote(name_ntls_raw, safe="")

    if proto == "vmess":
        vmess_tls = {
            "v": "2", "ps": name_tls_raw, "add": address, "port": "443",
            "id": secret, "aid": "0", "scy": "auto", "net": "ws",
            "type": "none", "host": host, "path": path_raw, "tls": "tls", "sni": sni
        }
        vmess_ntls = {
            "v": "2", "ps": name_ntls_raw, "add": address, "port": "80",
            "id": secret, "aid": "0", "scy": "auto", "net": "ws",
            "type": "none", "host": host, "path": path_raw, "tls": "none", "sni": ""
        }
        b64_tls = base64.b64encode(json.dumps(vmess_tls, separators=(",", ":")).encode("utf-8")).decode("ascii")
        b64_ntls = base64.b64encode(json.dumps(vmess_ntls, separators=(",", ":")).encode("utf-8")).decode("ascii")
        uri_tls = f"vmess://{b64_tls}"
        uri_ntls = f"vmess://{b64_ntls}"
        auth_yaml = f"    uuid: {secret}\n    alterId: 0\n    cipher: auto"
    elif proto == "vless":
        uri_tls = (
            f"vless://{secret}@{address}:443?encryption=none&security=tls"
            f"&sni={urllib.parse.quote(sni, safe='')}&type=ws"
            f"&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_tls_enc}"
        )
        uri_ntls = (
            f"vless://{secret}@{address}:80?encryption=none&security=none"
            f"&type=ws&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_ntls_enc}"
        )
        auth_yaml = f"    uuid: {secret}\n    cipher: auto"
    else:
        uri_tls = (
            f"trojan://{secret}@{address}:443?security=tls"
            f"&sni={urllib.parse.quote(sni, safe='')}&type=ws"
            f"&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_tls_enc}"
        )
        uri_ntls = (
            f"trojan://{secret}@{address}:80?security=none"
            f"&type=ws&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_ntls_enc}"
        )
        auth_yaml = f"    password: {secret}"

    yaml_block = (
        "proxies:\n"
        f"  - name: \"{name_tls_raw}\"\n"
        f"    type: {proto}\n"
        f"    server: {address}\n"
        "    port: 443\n"
        f"{auth_yaml}\n"
        "    tls: true\n"
        "    udp: true\n"
        "    skip-cert-verify: true\n"
        f"    servername: {sni}\n"
        "    network: ws\n"
        "    ws-opts:\n"
        f"      path: {path_raw}\n"
        "      headers:\n"
        f"        Host: {host}\n\n"
        f"  - name: \"{name_ntls_raw}\"\n"
        f"    type: {proto}\n"
        f"    server: {address}\n"
        "    port: 80\n"
        f"{auth_yaml}\n"
        "    tls: false\n"
        "    udp: true\n"
        "    skip-cert-verify: true\n"
        f"    servername: {sni}\n"
        "    network: ws\n"
        "    ws-opts:\n"
        f"      path: {path_raw}\n"
        "      headers:\n"
        f"        Host: {host}"
    )

    singbox_block = build_singbox_outbound_json(
        proto, name_tls_raw, name_ntls_raw, secret, address, host, sni, path_raw
    )

    h_part = hashlib.md5(f"{proto}:{remark}:{secret}:{domain}:{bug}:{mode}:{acc_id}:{is_external}".encode()).hexdigest()[:8]
    ckey = cache_id or f"{1 if is_external else 0}_{int(acc_id)}_{h_part}"
    privacy_note = "\n🔒 <i>Privasi: Akun luar ini diproses di RAM &amp; tidak disimpan di bot.</i>\n" if is_external else ""

    from .ui_theme import get_ui_theme_sync
    _theme_cur = get_ui_theme_sync()
    if _theme_cur == "tree":
        _hdr_info = (
            f"╭─── 🎯 <b>CONFIG {proto.upper()} — INJECT BUG &amp; MULTI-CLIENT</b>\n"
            f"├─ 🌐 <b>Server   :</b> <code>{html.escape(server_name)} ({html.escape(domain)})</code>\n"
            f"├─ 🛣 <b>Path WS  :</b> <code>{html.escape(path_raw)}</code>\n"
            f"├─ ⚡ <b>Metode   :</b> <code>{html.escape(mode_label)}</code>\n"
            f"├─ 🎯 <b>Address  :</b> <code>{html.escape(address)}</code>\n"
            f"╰─ 🔒 <b>SNI/Host :</b> <code>{html.escape(subdomain)}</code>"
        )
    else:
        _hdr_info = (
            f"🎯 <b>CONFIG {proto.upper()} — INJECT BUG &amp; MULTI-CLIENT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Server:</b> <code>{html.escape(server_name)} ({html.escape(domain)})</code>\n"
            f"• <b>Path:</b> <code>{html.escape(path_raw)}</code>\n"
            f"• <b>Metode:</b> <code>{html.escape(mode_label)}</code>\n"
            f"• <b>Address (Bug):</b> <code>{html.escape(address)}</code>\n"
            f"• <b>Subdomain (SNI/Host):</b> <code>{html.escape(subdomain)}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
    text = (
        f"{_hdr_info}"
        f"{privacy_note}\n"
        "🔐 <b>1. TLS (Port 443):</b>\n"
        f"<pre><code class=\"language-TLS\">{html.escape(uri_tls)}</code></pre>\n\n"
        "🔓 <b>2. NTLS (Port 80):</b>\n"
        f"<pre><code class=\"language-NTLS\">{html.escape(uri_ntls)}</code></pre>\n\n"
        "📄 <b>3. YAML (Clash / OpenClash / Mihomo):</b>\n"
        f"<pre><code class=\"language-YAML\">{html.escape(yaml_block)}</code></pre>"
    )

    rows_kb = [
        [
            InlineKeyboardButton(text="📦 Sing-box (JSON)", callback_data=f"inj_sb:{ckey}"),
            InlineKeyboardButton(text="📱 QR TLS", callback_data=f"inj_qr:tls:{ckey}"),
            InlineKeyboardButton(text="📱 QR NTLS", callback_data=f"inj_qr:ntls:{ckey}")
        ]
    ]

    if is_external:
        rows_kb.append([
            InlineKeyboardButton(text=("✅ Wildcard" if mode=="wildcard" else "🌐 Wildcard"), callback_data="ext_bquick:wildcard"),
            InlineKeyboardButton(text=("✅ SNI" if mode=="sni" else "🔒 SNI"), callback_data="ext_bquick:sni"),
            InlineKeyboardButton(text=("✅ CDN" if mode=="cdn" else "☁️ CDN"), callback_data="ext_bquick:cdn"),
        ])
        rows_kb.append([
            InlineKeyboardButton(text="🎯 Ganti Bug Lain", callback_data=f"ext_bmode:{mode}"),
            InlineKeyboardButton(text="🔄 Paste Link Baru", callback_data="ext_bug_start")
        ])
        rows_kb.append([InlineKeyboardButton(text="🏠 Menu Utama (Hapus Sesi RAM)", callback_data="menu_main")])
    elif acc_id:
        rows_kb.append([
            InlineKeyboardButton(text=("✅ Wildcard" if mode=="wildcard" else "🌐 Wildcard"), callback_data=f"bug_quick:{acc_id}:wildcard:{bug}"[:64]),
            InlineKeyboardButton(text=("✅ SNI" if mode=="sni" else "🔒 SNI"), callback_data=f"bug_quick:{acc_id}:sni:{bug}"[:64]),
            InlineKeyboardButton(text=("✅ CDN" if mode=="cdn" else "☁️ CDN"), callback_data=f"bug_quick:{acc_id}:cdn:{bug}"[:64]),
        ])
        rows_kb.append([
            InlineKeyboardButton(text="🔗 Link Subscription (Auto-Update)", callback_data=f"sub_link:{acc_id}:{bug[:24]}:{mode}"[:64])
        ])
        rows_kb.append([
            InlineKeyboardButton(text="🎯 Ganti Bug Lain", callback_data=f"bug_menu:{acc_id}"),
            InlineKeyboardButton(text="⬅️ Kembali ke Akun", callback_data="my_accounts")
        ])
    else:
        rows_kb.append([InlineKeyboardButton(text="🏠 Menu Utama", callback_data="menu_main")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows_kb)

    if len(_INJECT_PAYLOAD_CACHE) > 400:
        _INJECT_PAYLOAD_CACHE.clear()
    _INJECT_PAYLOAD_CACHE[ckey] = {
        "proto": proto,
        "remark": remark,
        "secret": secret,
        "server_name": server_name,
        "domain": domain,
        "bug": bug,
        "mode": mode,
        "mode_label": mode_label,
        "address": address,
        "subdomain": subdomain,
        "path_raw": path_raw,
        "uri_tls": uri_tls,
        "uri_ntls": uri_ntls,
        "yaml_block": yaml_block,
        "singbox_block": singbox_block,
        "acc_id": acc_id,
        "is_external": is_external,
        "full_text": text,
        "full_kb": kb
    }
    _save_disk_inject_cache(ckey, _INJECT_PAYLOAD_CACHE[ckey])
    return text, kb

# === SING-BOX JSON, PERSISTENT CACHE & 3-LAYER QR CODE GENERATOR ===
_INJECT_PAYLOAD_CACHE: dict[str, dict] = {}
_DISK_CACHE_FILE = "/tmp/vpn_inject_cache.json"

def _save_disk_inject_cache(ckey: str, data: dict):
    import json
    import os
    try:
        store = {}
        if os.path.exists(_DISK_CACHE_FILE):
            try:
                with open(_DISK_CACHE_FILE, "r", encoding="utf-8") as f:
                    store = json.load(f)
            except Exception:
                store = {}
        serializable = {k: v for k, v in data.items() if k not in ("full_kb", "full_text")}
        store[ckey] = serializable
        if len(store) > 300:
            keys_keep = list(store.keys())[-200:]
            store = {k: store[k] for k in keys_keep}
        with open(_DISK_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f)
    except Exception:
        pass

def get_cached_inject_payload(cache_key: str) -> dict | None:
    import json
    import os
    item = _INJECT_PAYLOAD_CACHE.get(cache_key)
    if item:
        return item
    if os.path.exists(_DISK_CACHE_FILE):
        try:
            with open(_DISK_CACHE_FILE, "r", encoding="utf-8") as f:
                store = json.load(f)
            saved = store.get(cache_key)
            if saved and isinstance(saved, dict) and saved.get("secret"):
                build_injected_xray_html(
                    proto=saved.get("proto", "vless"),
                    remark=saved.get("remark", "User"),
                    secret=saved.get("secret", ""),
                    server_name=saved.get("server_name", "Server Utama"),
                    domain=saved.get("domain", ""),
                    bug=saved.get("bug", ""),
                    mode=saved.get("mode", "wildcard"),
                    acc_id=int(saved.get("acc_id") or 0),
                    custom_path=saved.get("path_raw", ""),
                    is_external=bool(saved.get("is_external", False)),
                    cache_id=cache_key,
                )
                return _INJECT_PAYLOAD_CACHE.get(cache_key)
        except Exception:
            pass
    return None

def generate_qr_image_bytes(data_str: str) -> bytes | None:
    import io
    import subprocess
    import urllib.parse
    import urllib.request

    if not data_str:
        return None

    # Lapis 1: Library Python qrcode (kompatibel Pillow & PyPNG)
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=3,
        )
        qr.add_data(data_str)
        qr.make(fit=True)
        try:
            from qrcode.image.pil import PilImage
            img = qr.make_image(image_factory=PilImage, fill_color="black", back_color="white")
            bio = io.BytesIO()
            img.save(bio, format="PNG")
            raw = bio.getvalue()
            if raw and raw.startswith(b"\x89PNG"):
                return raw
        except Exception:
            bio = io.BytesIO()
            img = qr.make_image()
            img.save(bio)
            raw = bio.getvalue()
            if raw and raw.startswith(b"\x89PNG"):
                return raw
    except Exception as e:
        print(f"[QR LAYER 1 WARN] {e}")

    # Lapis 2: Binary Linux qrencode (sangat cepat & ringan)
    try:
        res = subprocess.run(
            ["qrencode", "-t", "PNG", "-s", "8", "-m", "3", "-o", "-", data_str],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )
        if res.returncode == 0 and res.stdout.startswith(b"\x89PNG"):
            return res.stdout
    except Exception as e:
        print(f"[QR LAYER 2 WARN] {e}")

    # Lapis 3: Fallback HTTP QR API
    try:
        enc = urllib.parse.quote(data_str, safe="")
        url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&margin=12&data={enc}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            if raw and raw.startswith(b"\x89PNG"):
                return raw
    except Exception as e:
        print(f"[QR LAYER 3 WARN] {e}")

    return None