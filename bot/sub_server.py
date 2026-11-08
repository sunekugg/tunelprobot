from __future__ import annotations
import asyncio
import base64
import datetime
import hashlib
import json
import re
import sqlite3
import urllib.parse
import aiosqlite
from aiohttp import web
from .config import BOT_TOKEN, DB_PATH, DOMAIN, now_wib
from .db import get_setting, get_server_by_id

SUB_PORT = 2095
_SUB_RUNNER: web.AppRunner | None = None

def _ensure_sub_pref_table_sync():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sub_preferences (
                acc_id INTEGER PRIMARY KEY,
                bug_host TEXT DEFAULT '',
                bug_mode TEXT DEFAULT 'wildcard',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

_ensure_sub_pref_table_sync()

async def get_sub_pref(acc_id: int) -> dict:
    _ensure_sub_pref_table_sync()
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT bug_host, bug_mode, updated_at FROM sub_preferences WHERE acc_id = ?",
            (int(acc_id),)
        ) as cur:
            row = await cur.fetchone()
    if row:
        return {
            "bug_host": str(row[0] or "").strip(),
            "bug_mode": str(row[1] or "wildcard").strip(),
            "updated_at": str(row[2] or "").strip(),
        }
    return {"bug_host": "", "bug_mode": "wildcard", "updated_at": ""}

async def save_sub_pref(acc_id: int, bug_host: str | None = None, bug_mode: str | None = None) -> dict:
    _ensure_sub_pref_table_sync()
    cur_pref = await get_sub_pref(acc_id)
    b_host = cur_pref["bug_host"] if bug_host is None else bug_host.strip().lower()
    b_mode = cur_pref["bug_mode"] if bug_mode is None else bug_mode.strip().lower()
    ts_now = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        await db.execute(
            """
            INSERT INTO sub_preferences (acc_id, bug_host, bug_mode, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(acc_id) DO UPDATE SET
                bug_host = excluded.bug_host,
                bug_mode = excluded.bug_mode,
                updated_at = excluded.updated_at
            """,
            (int(acc_id), b_host, b_mode, ts_now)
        )
        await db.commit()
    return {"bug_host": b_host, "bug_mode": b_mode, "updated_at": ts_now}

def make_sub_token(acc_id: int, tg_id: int) -> str:
    raw = f"sub_v1:{int(acc_id)}:{int(tg_id)}:{BOT_TOKEN}"
    sig = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]
    return f"{int(acc_id)}_{sig}"

def verify_sub_token(token: str, acc_id: int, tg_id: int) -> bool:
    expected = make_sub_token(acc_id, tg_id)
    return token.strip() == expected

def get_sub_urls(server_domain: str, acc_id: int, tg_id: int, bug: str = "", mode: str = "") -> dict[str, str]:
    tok = make_sub_token(acc_id, tg_id)
    # Gunakan DOMAIN utama bot agar server node kedua (ID-Nusa dll) tetap bisa diakses melalui port 2095 VPS Utama
    host_sub = (DOMAIN or server_domain).strip()
    base = f"http://{host_sub}:{SUB_PORT}/sub/{tok}"
    q_extra = ""
    if bug:
        q_extra = f"&bug={urllib.parse.quote(bug.strip(), safe='')}"
        if mode:
            q_extra += f"&mode={urllib.parse.quote(mode.strip(), safe='')}"
    return {
        "v2ray": f"{base}?target=v2ray{q_extra}",
        "clash": f"{base}?target=clash{q_extra}",
        "singbox": f"{base}?target=singbox{q_extra}",
    }

def _parse_preset_domains(raw_text: str, limit: int = 5) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for part in re.split(r"[,\n]+", raw_text or ""):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            lbl, dom = part.split("=", 1)
            lbl, dom = lbl.strip(), dom.strip().lower()
        else:
            lbl, dom = part.strip(), part.strip().lower()
        if dom and dom not in seen:
            seen.add(dom)
            out.append((lbl, dom))
        if len(out) >= limit:
            break
    return out

def _build_node_entry(
    proto: str,
    tag_prefix: str,
    secret: str,
    domain: str,
    bug: str,
    mode: str,
    use_tls: bool
) -> dict:
    p = proto.lower().strip()
    path_raw = f"/{p}"
    port = 443 if use_tls else 80
    tls_lbl = "TLS" if use_tls else "NTLS"

    if mode == "wildcard":
        address = bug
        subd = f"{bug}.{domain}" if not bug.endswith(f".{domain}") and bug != domain else bug
        sni = subd
        host = subd
    elif mode == "sni":
        address = domain
        sni = bug
        host = bug
    elif mode == "cdn":
        address = bug
        sni = domain
        host = domain
    else:
        address = domain
        sni = domain
        host = domain

    node_name = f"{tag_prefix}-{tls_lbl}"
    name_enc = urllib.parse.quote(node_name, safe="")
    path_enc = urllib.parse.quote(path_raw, safe="")

    if p == "vmess":
        vm_obj = {
            "v": "2", "ps": node_name, "add": address, "port": str(port),
            "id": secret, "aid": "0", "scy": "auto", "net": "ws",
            "type": "none", "host": host, "path": path_raw,
            "tls": "tls" if use_tls else "none",
            "sni": sni if use_tls else ""
        }
        b64_v = base64.b64encode(json.dumps(vm_obj, separators=(",", ":")).encode("utf-8")).decode("ascii")
        uri = f"vmess://{b64_v}"
        auth_yaml = f"    uuid: {secret}\n    alterId: 0\n    cipher: auto"
    elif p == "vless":
        if use_tls:
            uri = f"vless://{secret}@{address}:443?encryption=none&security=tls&sni={urllib.parse.quote(sni, safe='')}&type=ws&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_enc}"
        else:
            uri = f"vless://{secret}@{address}:80?encryption=none&security=none&type=ws&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_enc}"
        auth_yaml = f"    uuid: {secret}\n    cipher: auto"
    else:
        if use_tls:
            uri = f"trojan://{secret}@{address}:443?security=tls&sni={urllib.parse.quote(sni, safe='')}&type=ws&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_enc}"
        else:
            uri = f"trojan://{secret}@{address}:80?security=none&type=ws&host={urllib.parse.quote(host, safe='')}&path={path_enc}#{name_enc}"
        auth_yaml = f"    password: {secret}"

    clash_proxy = (
        f"  - name: \"{node_name}\"\n"
        f"    type: {p}\n"
        f"    server: {address}\n"
        f"    port: {port}\n"
        f"{auth_yaml}\n"
        f"    tls: {'true' if use_tls else 'false'}\n"
        "    udp: true\n"
        "    skip-cert-verify: true\n"
        f"    servername: {sni}\n"
        "    network: ws\n"
        "    ws-opts:\n"
        f"      path: {path_raw}\n"
        "      headers:\n"
        f"        Host: {host}"
    )

    sb_node: dict = {
        "type": p,
        "tag": node_name,
        "server": address,
        "server_port": port,
    }
    if p == "vmess":
        sb_node["uuid"] = secret
        sb_node["security"] = "auto"
        sb_node["alter_id"] = 0
    elif p == "vless":
        sb_node["uuid"] = secret
    else:
        sb_node["password"] = secret

    if use_tls:
        sb_node["tls"] = {
            "enabled": True,
            "server_name": sni,
            "insecure": True,
            "alpn": ["http/1.1"]
        }
    sb_node["transport"] = {
        "type": "ws",
        "path": path_raw,
        "headers": {"Host": host}
    }

    return {
        "name": node_name,
        "uri": uri,
        "clash": clash_proxy,
        "singbox": sb_node
    }

async def build_subscription_payload(acc_id: int, target_kind: str = "v2ray", custom_bug: str = "", custom_mode: str = "") -> dict | None:
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT tg_id, acc_type, username, password_or_uuid, expired_at, COALESCE(quota_gb, 0), COALESCE(used_bytes, 0), COALESCE(is_locked, 0), COALESCE(server_id, 1) FROM accounts WHERE id = ?",
            (int(acc_id),)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        return None

    tg_id, acc_type, uname, secret, exp_str, quota_gb, used_bytes, is_locked, sid = row
    if acc_type == "ssh":
        return None

    pref = await get_sub_pref(int(acc_id))
    use_bug = (custom_bug or pref.get("bug_host") or "").strip().lower()
    use_mode = (custom_mode or pref.get("bug_mode") or "wildcard").strip().lower()

    srv = await get_server_by_id(int(sid))
    srv_name = srv["name"] if srv else f"Server-{sid}"
    domain = srv["domain"] if srv else DOMAIN

    nodes: list[dict] = []

    # 1. Prioritaskan Bug Pilihan User di urutan paling atas
    if use_bug:
        nodes.append(_build_node_entry(acc_type, f"⭐{uname}-{use_mode.upper()}-{use_bug[:14]}", secret, domain, use_bug, use_mode, True))
        if use_mode in ("wildcard", "cdn"):
            nodes.append(_build_node_entry(acc_type, f"⭐{uname}-{use_mode.upper()}-{use_bug[:14]}", secret, domain, use_bug, use_mode, False))

    # 2. Direct Server (Normal TLS & NTLS)
    nodes.append(_build_node_entry(acc_type, f"{uname}-{srv_name}-Direct", secret, domain, domain, "direct", True))
    nodes.append(_build_node_entry(acc_type, f"{uname}-{srv_name}-Direct", secret, domain, domain, "direct", False))

    # 3. Seluruh Preset Wildcard, SNI & CDN
    wc_list = _parse_preset_domains(await get_setting("bug_presets_wildcard") or "", limit=5)
    sni_list = _parse_preset_domains(await get_setting("bug_presets_sni") or "", limit=5)
    cdn_list = _parse_preset_domains(await get_setting("bug_presets_cdn") or "", limit=4)

    for lbl, bdom in wc_list:
        if bdom == use_bug and use_mode == "wildcard":
            continue
        short_lbl = re.sub(r"[^a-zA-Z0-9_-]", "", lbl[:12]) or bdom[:12]
        nodes.append(_build_node_entry(acc_type, f"{uname}-WC-{short_lbl}", secret, domain, bdom, "wildcard", True))
        nodes.append(_build_node_entry(acc_type, f"{uname}-WC-{short_lbl}", secret, domain, bdom, "wildcard", False))

    for lbl, bdom in sni_list:
        if bdom == use_bug and use_mode == "sni":
            continue
        short_lbl = re.sub(r"[^a-zA-Z0-9_-]", "", lbl[:12]) or bdom[:12]
        nodes.append(_build_node_entry(acc_type, f"{uname}-SNI-{short_lbl}", secret, domain, bdom, "sni", True))

    for lbl, bdom in cdn_list:
        if bdom == use_bug and use_mode == "cdn":
            continue
        short_lbl = re.sub(r"[^a-zA-Z0-9_-]", "", lbl[:12]) or bdom[:12]
        nodes.append(_build_node_entry(acc_type, f"{uname}-CDN-{short_lbl}", secret, domain, bdom, "cdn", True))
        nodes.append(_build_node_entry(acc_type, f"{uname}-CDN-{short_lbl}", secret, domain, bdom, "cdn", False))

    if target_kind == "clash":
        proxy_blocks = "\n".join([n["clash"] for n in nodes])
        proxy_names = "\n".join([f"      - \"{n['name']}\"" for n in nodes])
        content = (
            "port: 7890\n"
            "socks-port: 7891\n"
            "allow-lan: true\n"
            "mode: rule\n"
            "log-level: info\n\n"
            "proxies:\n"
            f"{proxy_blocks}\n\n"
            "proxy-groups:\n"
            "  - name: \"🚀 PILIH-BUG-VPN\"\n"
            "    type: select\n"
            "    proxies:\n"
            "      - \"⚡ AUTO-PING-TERBAIK\"\n"
            f"{proxy_names}\n\n"
            "  - name: \"⚡ AUTO-PING-TERBAIK\"\n"
            "    type: url-test\n"
            "    url: http://www.gstatic.com/generate_204\n"
            "    interval: 180\n"
            "    tolerance: 50\n"
            "    proxies:\n"
            f"{proxy_names}\n\n"
            "rules:\n"
            "  - MATCH,🚀 PILIH-BUG-VPN\n"
        )
        filename = f"{uname}-clash-sub.yaml"
        mime = "text/yaml"
    elif target_kind == "singbox":
        tags_all = [n["name"] for n in nodes]
        outbounds_list = [
            {
                "type": "selector",
                "tag": "🚀 PILIH-BUG-VPN",
                "outbounds": ["⚡ AUTO-PING-TERBAIK"] + tags_all,
                "default": "⚡ AUTO-PING-TERBAIK"
            },
            {
                "type": "urltest",
                "tag": "⚡ AUTO-PING-TERBAIK",
                "outbounds": tags_all,
                "url": "http://www.gstatic.com/generate_204",
                "interval": "3m"
            }
        ] + [n["singbox"] for n in nodes] + [
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"}
        ]
        content = json.dumps({"outbounds": outbounds_list}, indent=2, ensure_ascii=False)
        filename = f"{uname}-singbox-sub.json"
        mime = "application/json"
    else:
        raw_uris = "\n".join([n["uri"] for n in nodes])
        content = base64.b64encode(raw_uris.encode("utf-8")).decode("ascii")
        filename = f"{uname}-v2ray-sub.txt"
        mime = "text/plain"

    return {
        "tg_id": int(tg_id),
        "uname": uname,
        "acc_type": acc_type,
        "exp_str": exp_str,
        "quota_gb": float(quota_gb),
        "used_bytes": int(used_bytes),
        "is_locked": int(is_locked),
        "node_count": len(nodes),
        "content": content,
        "filename": filename,
        "mime": mime,
    }

async def _handle_subscription(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "").strip()
    if token == "ping":
        return web.Response(text="PONG", status=200)

    if (await get_setting("feat_smart_sub")) == "0":
        return web.Response(text="Smart Subscription sedang dinonaktifkan oleh Administrator.", status=403)

    if "_" not in token:
        return web.Response(text="Token Subscription tidak valid.", status=404)

    acc_id_str, _ = token.split("_", 1)
    if not acc_id_str.isdigit():
        return web.Response(text="ID Subscription tidak valid.", status=404)

    acc_id = int(acc_id_str)
    q_target = (request.query.get("target") or "").strip().lower()
    q_bug = (request.query.get("bug") or "").strip().lower()
    q_mode = (request.query.get("mode") or "").strip().lower()
    ua = (request.headers.get("User-Agent") or "").lower()

    if not q_target:
        if any(k in ua for k in ("clash", "mihomo", "stash", "surfboard")):
            q_target = "clash"
        elif any(k in ua for k in ("sing-box", "sfa", "sfi", "nekobox")):
            q_target = "singbox"
        else:
            q_target = "v2ray"

    res = await build_subscription_payload(acc_id, target_kind=q_target, custom_bug=q_bug, custom_mode=q_mode)
    if not res:
        return web.Response(text="Akun tidak ditemukan atau sudah expired.", status=404)

    if not verify_sub_token(token, acc_id, res["tg_id"]):
        return web.Response(text="Signature Token Subscription tidak sah.", status=403)

    if res["is_locked"] == 1:
        return web.Response(text="Akun sedang terkunci (Multi-Login / Kuota Habis).", status=403)

    total_bytes = int(res["quota_gb"] * (1024**3)) if res["quota_gb"] > 0 else 0
    exp_ts = 0
    exp_clean = str(res["exp_str"] or "").replace("WIB", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt_e = datetime.datetime.strptime(exp_clean, fmt).replace(
                tzinfo=datetime.timezone(datetime.timedelta(hours=7))
            )
            exp_ts = int(dt_e.timestamp())
            break
        except Exception:
            continue

    userinfo_hdr = f"upload=0; download={res['used_bytes']}; total={total_bytes}; expire={exp_ts}"
    headers = {
        "Subscription-Userinfo": userinfo_hdr,
        "Profile-Update-Interval": "6",
        "Profile-Title": base64.b64encode(f"VPN-{res['uname']}".encode("utf-8")).decode("ascii"),
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Content-Disposition": f'attachment; filename="{res["filename"]}"',
    }
    return web.Response(text=res["content"], content_type=res["mime"], headers=headers)

async def start_subscription_web_server():
    global _SUB_RUNNER
    if _SUB_RUNNER is not None:
        return
    try:
        app = web.Application()
        app.router.add_get("/sub/{token}", _handle_subscription)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", SUB_PORT)
        await site.start()
        _SUB_RUNNER = runner
        print(f"🔗 Smart Subscription Web Server aktif di port {SUB_PORT} (/sub/<token>)")
    except Exception as e:
        print(f"[SMART SUB SERVER WARNING] {e}")