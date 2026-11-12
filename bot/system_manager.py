from __future__ import annotations
import os
import sys
import logging
import time
import json
import html
import re
import asyncio
import datetime
import asyncio
import base64
import datetime
import html
import json
import os
import pwd
import re
import shlex
import subprocess
import time
import aiohttp
import psutil
import aiosqlite
from .config import DB_PATH, XRAY_CONFIG, XRAY_ACCESS_LOG, AUTO_BACKUP_SH, DOMAIN, ADMIN_ID, now_wib
from .db import get_server_by_id, get_servers_with_usage, get_setting

_ISP_CACHE = {"city": "Jakarta", "isp": "Cloud ISP", "ts": 0}
SSH_KEY_PRIV = "/root/.ssh/id_ed25519_vpn"
SSH_KEY_PUB = "/root/.ssh/id_ed25519_vpn.pub"

# Cache global untuk sesi online & resource seluruh server (Lokal + Remote Nodes)
_CLUSTER_ONLINE_CACHE: dict = {
    "ssh_by_srv": {},     # {server_id: {username: [ip1, ip2]}}
    "xray_by_srv": {},    # {server_id: {username: [ip1, ip2]}}
    "node_stats": {},     # {server_id: {"ram": ..., "rx_gb": ..., "tx_gb": ..., "online": True}}
    "ts": 0.0
}

def format_bytes_human(num_bytes: int | float) -> str:
    b = float(num_bytes or 0)
    if b < 1024:
        return f"{int(b)} B"
    if b < 1024**2:
        return f"{b / 1024:.2f} KB"
    if b < 1024**3:
        return f"{b / (1024**2):.2f} MB"
    return f"{b / (1024**3):.2f} GB"

async def get_isp_info() -> tuple[str, str]:
    now = time.time()
    if now - _ISP_CACHE["ts"] < 3600 and _ISP_CACHE["ts"] > 0:
        return _ISP_CACHE["city"], _ISP_CACHE["isp"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://ip-api.com/json/", timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    _ISP_CACHE["city"] = data.get("city") or "Jakarta"
                    _ISP_CACHE["isp"] = data.get("isp") or "Cloud Provider"
                    _ISP_CACHE["ts"] = now
    except Exception:
        pass
    return _ISP_CACHE["city"], _ISP_CACHE["isp"]

def get_short_isp(isp: str) -> str:
    cleaned = re.sub(r"\b(PT\.?|CV\.?|Inc\.?|LLC|Ltd\.?|Pte\.?|tbk\.?|Indonesia|Nusantara|Corporation|Corp\.?)\b", "", isp, flags=re.IGNORECASE).strip(" ,.-")
    parts = [p for p in cleaned.split() if p]
    return " ".join(parts[:2]) if parts else (isp.strip() or "CLOUD")

def parse_duration(dur_str: str) -> datetime.timedelta | None:
    if not dur_str:
        return None
    s = dur_str.strip().lower()
    if s in ("0", "unlimited", "tanpa expired", "inf", "permanent"):
        return None
    m = re.match(r"^(\d+)\s*(m|min|mnt|menit|h|jam|hour|d|day|hari)?$", s)
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2) or "d"
    if val <= 0:
        return None
    if unit in ("m", "min", "mnt", "menit"):
        return datetime.timedelta(minutes=val)
    if unit in ("h", "jam", "hour"):
        return datetime.timedelta(hours=val)
    return datetime.timedelta(days=val)

def format_trial_duration_label(dur_str: str) -> str:
    s = (dur_str or "30m").strip().lower()
    m = re.match(r"^(\d+)\s*(m|min|mnt|menit|h|jam|hour|d|day|hari)?$", s)
    if not m:
        return "30 Minute"
    val = int(m.group(1))
    unit = m.group(2) or "m"
    if unit in ("m", "min", "mnt", "menit"):
        return f"{val} Minute"
    if unit in ("h", "jam", "hour"):
        return f"{val} Hour"
    return f"{val} Day"

def format_expired_display(expired_at: str, is_trial: int, trial_dur_str: str = "30m") -> str:
    if not expired_at or expired_at.lower() == "unlimited":
        return "Unlimited"
    clean_exp = expired_at.replace("WIB", "").strip()
    if int(is_trial) == 1:
        return f"{clean_exp} WIB ({format_trial_duration_label(trial_dur_str)})"
    return f"{clean_exp} WIB"

def system_user_exists(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False

def create_os_ssh_user(username: str, password: str) -> bool:
    try:
        if not system_user_exists(username):
            subprocess.run(["useradd", "-M", "-s", "/bin/false", username], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        proc = subprocess.Popen(["chpasswd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.communicate(input=f"{username}:{password}\n".encode("utf-8"), timeout=10)
        subprocess.run(["usermod", "-U", "-e", "", username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        # Dropbear menolak user tanpa home dir (Error changing directory → sesi
        # langsung drop → tidak pernah terdeteksi online). Shell tetap /bin/false
        # (tunneling saja, tak interaktif), tapi harus ada $HOME supaya sesi hidup.
        try:
            home = f"/home/{username}"
            subprocess.run(["mkdir", "-p", home], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            subprocess.run(["chown", f"{username}:{username}", home], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            subprocess.run(["chmod", "750", home], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        except Exception:
            pass
        if proc.returncode != 0:
            logging.error("create_os_ssh_user: chpasswd returncode %s utk user %s", proc.returncode, username)
        return proc.returncode == 0
    except Exception as e:
        stderr = ""
        if hasattr(e, "stderr") and isinstance(getattr(e, "stderr"), bytes):
            stderr = e.stderr.decode("utf-8", "ignore")[:200]
        logging.error("create_os_ssh_user EXC utk %s: %s | stderr=%s", username, e, stderr)
        return False

def lock_os_ssh_user(username: str, lock: bool = True, password: str = ""):
    try:
        if lock:
            subprocess.run(["usermod", "-L", username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            subprocess.run(["pkill", "-9", "-u", username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        else:
            subprocess.run(["usermod", "-U", username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if password:
                proc = subprocess.Popen(["chpasswd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc.communicate(input=f"{username}:{password}\n".encode("utf-8"), timeout=10)
    except Exception:
        pass

def delete_os_ssh_user(username: str):
    try:
        subprocess.run(["usermod", "-L", "-s", "/usr/sbin/nologin", username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        subprocess.run(["pkill", "-9", "-u", username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        subprocess.run(["userdel", "-r", "-f", username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except Exception:
        pass

def _ensure_local_ssh_keypair():
    if not os.path.exists(SSH_KEY_PRIV):
        os.makedirs("/root/.ssh", mode=0o700, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", SSH_KEY_PRIV, "-C", "tgbot-vpn-master"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
        )

def _clear_plaintext_ssh_pass_db_sync(server_id: int):
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("UPDATE servers SET ssh_pass = '[SSH-KEY]' WHERE id = ? AND is_local = 0", (server_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def _run_remote_ssh_detail_sync(srv: dict, remote_cmd: str, timeout_sec: int = 30) -> tuple[bool, str]:
    try:
        if srv.get("is_local", 1) == 1:
            res = subprocess.run(["bash", "-c", remote_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec)
            return res.returncode == 0, (res.stdout or res.stderr or "OK").strip()

        _ensure_local_ssh_keypair()
        ip_clean = str(srv.get("ip", "")).strip()
        port_clean = str(srv.get("ssh_port", 22)).strip()
        user_clean = str(srv.get("ssh_user", "root")).strip() or "root"
        raw_pass = str(srv.get("ssh_pass", "")).strip()

        # 1. Prioritaskan SSH Key (Ed25519) tanpa password
        if os.path.exists(SSH_KEY_PRIV):
            key_cmd = [
                "ssh", "-i", SSH_KEY_PRIV,
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-o", "ConnectTimeout=6",
                "-p", port_clean,
                f"{user_clean}@{ip_clean}",
                remote_cmd
            ]
            res_k = subprocess.run(key_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec)
            if res_k.returncode == 0:
                if raw_pass and raw_pass != "[SSH-KEY]" and srv.get("id"):
                    _clear_plaintext_ssh_pass_db_sync(int(srv["id"]))
                return True, (res_k.stdout or "OK").strip()

        # 2. Fallback ke sshpass jika SSH Key belum terpasang di node, sekaligus pasang SSH Key otomatis
        if not raw_pass or raw_pass == "[SSH-KEY]":
            return False, "Koneksi SSH Key gagal dan password root tidak tersedia."

        env = os.environ.copy()
        env["SSHPASS"] = raw_pass
        pub_key_str = ""
        if os.path.exists(SSH_KEY_PUB):
            with open(SSH_KEY_PUB, "r", encoding="utf-8") as pf:
                pub_key_str = pf.read().strip()

        install_key_prefix = ""
        if pub_key_str:
            pub_q = shlex.quote(pub_key_str)
            install_key_prefix = (
                f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                f"(grep -qxF {pub_q} ~/.ssh/authorized_keys 2>/dev/null || echo {pub_q} >> ~/.ssh/authorized_keys) && "
                f"chmod 600 ~/.ssh/authorized_keys; "
            )

        cmd = [
            "sshpass", "-e", "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "PubkeyAuthentication=no",
            "-o", "ConnectTimeout=8",
            "-p", port_clean,
            f"{user_clean}@{ip_clean}",
            install_key_prefix + remote_cmd
        ]
        res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec)
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if res.returncode == 0 and pub_key_str and srv.get("id"):
            _clear_plaintext_ssh_pass_db_sync(int(srv["id"]))
        return res.returncode == 0, (out if res.returncode == 0 else (err or out or f"Exit code {res.returncode}"))
    except Exception as e:
        return False, str(e)

def _run_remote_ssh_sync(srv: dict, remote_cmd: str) -> bool:
    ok, _ = _run_remote_ssh_detail_sync(srv, remote_cmd, timeout_sec=30)
    return ok

def update_ssh_banner(banner_html: str) -> bool:
    try:
        lines = [line.strip() for line in banner_html.splitlines() if line.strip()]
        formatted = [ln if ln.endswith("<br>") else ln + "<br>" for ln in lines]
        final_html = "\n".join(formatted) if formatted else banner_html
        with open("/etc/issue.net", "w", encoding="utf-8") as f:
            f.write(final_html + "\n")
        subprocess.run(["systemctl", "reload", "ssh"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        subprocess.run(["systemctl", "restart", "dropbear"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        return True
    except Exception:
        return False

async def sync_banner_to_server(srv: dict) -> tuple[bool, str]:
    if not os.path.exists("/etc/issue.net"):
        return False, "File /etc/issue.net belum ada di VPS Utama."
    if srv.get("is_local", 1) == 1:
        return True, "Lokal OK"
    with open("/etc/issue.net", "rb") as f:
        b64_banner = base64.b64encode(f.read()).decode("ascii")
    remote_cmd = (
        f"echo '{b64_banner}' | base64 -d > /etc/issue.net && "
        "chmod 644 /etc/issue.net && "
        "sed -i '/^#*Banner /d' /etc/ssh/sshd_config && "
        "echo 'Banner /etc/issue.net' >> /etc/ssh/sshd_config && "
        "(systemctl reload ssh 2>/dev/null || systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true) && "
        "(systemctl restart dropbear 2>/dev/null || true) && "
        "echo 'Banner OK'"
    )
    return await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, remote_cmd, 25)

async def sync_banner_all_servers() -> list[tuple[str, bool, str]]:
    servers = await get_servers_with_usage(only_active=False)
    results = []
    for srv in servers:
        if srv.get("is_local", 1) == 0:
            ok, msg = await sync_banner_to_server(srv)
            results.append((srv["name"], ok, msg))
    return results

async def set_server_speed_limit(server_id: int, mbps: int) -> tuple[bool, str]:
    srv = await get_server_by_id(server_id)
    if not srv:
        return False, "Server tidak ditemukan."
    if srv.get("is_local", 1) == 1:
        res = await asyncio.to_thread(
            subprocess.run,
            ["/usr/bin/python3", "/usr/local/bin/vpn-bw-manager.py", "server_speed", str(mbps)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15
        )
        return res.returncode == 0, (res.stdout or res.stderr or "OK").strip()

    with open("/usr/local/bin/vpn-bw-manager.py", "rb") as f:
        b64_mgr = base64.b64encode(f.read()).decode("ascii")
    remote_cmd = (
        f"echo '{b64_mgr}' | base64 -d > /usr/local/bin/vpn-bw-manager.py && "
        "chmod +x /usr/local/bin/vpn-bw-manager.py && "
        f"python3 /usr/local/bin/vpn-bw-manager.py server_speed {int(mbps)}"
    )
    return await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, remote_cmd, 25)

async def sync_ssh_bw_rules_on_server(server_id: int):
    srv = await get_server_by_id(server_id)
    if not srv:
        return
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT username, COALESCE(speed_mbps, 0), acc_type FROM accounts WHERE COALESCE(server_id, 1) = ? AND is_locked = 0",
            (server_id,)
        ) as cur:
            rows = await cur.fetchall()
    users_list = [{"u": r[0], "mbps": r[1], "type": ("ssh" if r[2] == "ssh" else "xray")} for r in rows]
    b64_payload = base64.b64encode(json.dumps(users_list).encode("utf-8")).decode("ascii")

    if srv.get("is_local", 1) == 1:
        await asyncio.to_thread(
            subprocess.run,
            ["/usr/bin/python3", "/usr/local/bin/vpn-bw-manager.py", "sync_bw_b64", b64_payload],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
        )
    else:
        with open("/usr/local/bin/vpn-bw-manager.py", "rb") as f:
            b64_mgr = base64.b64encode(f.read()).decode("ascii")
        remote_cmd = (
            f"echo '{b64_mgr}' | base64 -d > /usr/local/bin/vpn-bw-manager.py && "
            "chmod +x /usr/local/bin/vpn-bw-manager.py && "
            f"python3 /usr/local/bin/vpn-bw-manager.py sync_bw_b64 {b64_payload}"
        )
        await asyncio.to_thread(_run_remote_ssh_sync, srv, remote_cmd)



async def inspect_remote_node(server_id: int) -> tuple[bool, dict]:
    srv = await get_server_by_id(server_id)
    if not srv:
        return False, {"error": "Server tidak ditemukan di database."}
    check_script = (
        "XRAY_ACT=$(systemctl is-active xray 2>/dev/null || echo inactive); "
        "XRAY_BIN=$([ -f /usr/local/bin/xray ] || [ -f /usr/bin/xray ] && echo yes || echo no); "
        "DROP_ACT=$(systemctl is-active dropbear 2>/dev/null || echo inactive); "
        "DROP_BIN=$(command -v dropbear >/dev/null 2>&1 || [ -f /usr/sbin/dropbear ] && echo yes || echo no); "
        "HAP_ACT=$(systemctl is-active haproxy 2>/dev/null || echo inactive); "
        "HAP_BIN=$(command -v haproxy >/dev/null 2>&1 || [ -f /usr/sbin/haproxy ] && echo yes || echo no); "
        "NGX_ACT=$(systemctl is-active nginx 2>/dev/null || echo inactive); "
        "SSL_OK=$([ -s /etc/xray/certs/fullchain.pem ] || [ -s /etc/xray/xray.crt ] && echo yes || echo no); "
        "echo \"XRAY=${XRAY_BIN}:${XRAY_ACT}|DROPBEAR=${DROP_BIN}:${DROP_ACT}|HAPROXY=${HAP_BIN}:${HAP_ACT}|NGINX=${NGX_ACT}|SSL=${SSL_OK}\""
    )
    ok, raw = await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, check_script, 20)
    if not ok:
        return False, {"error": raw}
    info = {}
    for item in raw.splitlines()[-1].split("|"):
        if "=" in item:
            k, v = item.split("=", 1)
            info[k.strip()] = v.strip()
    return True, info

async def use_existing_remote_node(server_id: int) -> tuple[bool, str]:
    srv = await get_server_by_id(server_id)
    if not srv:
        return False, "Server tidak ditemukan."
    if srv.get("is_local", 1) == 1:
        await sync_xray_clients()
        return True, "Server Utama (Lokal) disinkronkan."
    prep_cmd = (
        "timedatectl set-timezone Asia/Jakarta 2>/dev/null || ln -sf /usr/share/zoneinfo/Asia/Jakarta /etc/localtime; "
        "systemctl stop tgbot-vpn 2>/dev/null || true; "
        "systemctl disable tgbot-vpn 2>/dev/null || true; "
        "rm -f /etc/cron.d/vpn-auto-expired /usr/local/bin/check-vpn-expired.py; "
        "grep -qxF '/bin/false' /etc/shells || echo '/bin/false' >> /etc/shells; "
        "grep -qxF '/usr/sbin/nologin' /etc/shells || echo '/usr/sbin/nologin' >> /etc/shells; "
        "echo 'Mode Existing Aktif (SSH Key Ed25519 Terpasang)'"
    )
    ok, msg = await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, prep_cmd, 25)
    if ok:
        await sync_xray_by_server(server_id)
        await sync_banner_to_server(srv)
    return ok, msg

async def install_remote_node_stack(server_id: int) -> tuple[bool, str]:
    srv = await get_server_by_id(server_id)
    if not srv:
        return False, "Server tidak ditemukan."
    if not os.path.exists("/usr/local/bin/node-agent-setup.sh"):
        return False, "Template /usr/local/bin/node-agent-setup.sh tidak ditemukan."
    with open("/usr/local/bin/node-agent-setup.sh", "rb") as f:
        b64_script = base64.b64encode(f.read()).decode("ascii")
    dom_q = shlex.quote(srv.get("domain", "localhost"))
    remote_cmd = (
        f"echo '{b64_script}' | base64 -d > /root/node-agent-setup.sh && "
        f"chmod +x /root/node-agent-setup.sh && "
        f"bash /root/node-agent-setup.sh {dom_q}"
    )
    ok, msg = await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, remote_cmd, 600)
    if ok:
        await sync_xray_by_server(server_id)
        await sync_banner_to_server(srv)
    return ok, msg

async def create_ssh_on_server(server_id: int, username: str, password: str) -> bool:
    srv = await get_server_by_id(server_id)
    if not srv or srv.get("is_local", 1) == 1:
        ok = await asyncio.to_thread(create_os_ssh_user, username, password)
        await sync_ssh_bw_rules_on_server(1)
        return ok
    u_q = shlex.quote(username)
    p_q = shlex.quote(f"{username}:{password}")
    cmd = (
        "systemctl stop tgbot-vpn 2>/dev/null || true; "
        "rm -f /etc/cron.d/vpn-auto-expired; "
        "grep -qxF '/bin/false' /etc/shells || echo '/bin/false' >> /etc/shells; "
        f"(id {u_q} >/dev/null 2>&1 || useradd -M -s /bin/false {u_q}) && "
        f"echo {p_q} | chpasswd && usermod -s /bin/false -U -e '' {u_q}"
    )
    ok = await asyncio.to_thread(_run_remote_ssh_sync, srv, cmd)
    await sync_ssh_bw_rules_on_server(server_id)
    return ok

async def delete_ssh_on_server(server_id: int, username: str):
    srv = await get_server_by_id(server_id)
    if not srv or srv.get("is_local", 1) == 1:
        await asyncio.to_thread(delete_os_ssh_user, username)
        return
    u_q = shlex.quote(username)
    await asyncio.to_thread(
        _run_remote_ssh_sync,
        srv,
        f"usermod -L -s /usr/sbin/nologin {u_q} 2>/dev/null || true; pkill -9 -u {u_q} 2>/dev/null || true; userdel -r -f {u_q} 2>/dev/null || true"
    )

async def lock_ssh_on_server(server_id: int, username: str, lock: bool = True, password: str = ""):
    srv = await get_server_by_id(server_id)
    if not srv or srv.get("is_local", 1) == 1:
        await asyncio.to_thread(lock_os_ssh_user, username, lock, password)
        return
    u_q = shlex.quote(username)
    if lock:
        await asyncio.to_thread(_run_remote_ssh_sync, srv, f"usermod -L {u_q}; pkill -9 -u {u_q} || true")
    else:
        await asyncio.to_thread(_run_remote_ssh_sync, srv, f"usermod -U {u_q}")

def _write_local_xray_sync(trojan_clients: list, vless_clients: list, vmess_clients: list):
    try:
        payload_b64 = base64.b64encode(json.dumps({"trojan": trojan_clients, "vless": vless_clients, "vmess": vmess_clients}).encode("utf-8")).decode("ascii")
        subprocess.run(["/usr/bin/python3", "/usr/local/bin/xray-hot-sync.py", "--b64", payload_b64], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    except Exception as e:
        print(f"[ERROR] Gagal hot-sync Xray lokal: {e}")

async def sync_xray_clients():
    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        async with db.execute(
            "SELECT acc_type, username, password_or_uuid FROM accounts WHERE COALESCE(server_id, 1) = 1 AND acc_type IN ('trojan', 'vless', 'vmess') AND is_locked = 0"
        ) as cur:
            rows = await cur.fetchall()

    trojan_c = [{"password": r[2], "email": r[1]} for r in rows if r[0] == "trojan"]
    vless_c = [{"id": r[2], "email": r[1]} for r in rows if r[0] == "vless"]
    vmess_c = [{"id": r[2], "alterId": 0, "email": r[1]} for r in rows if r[0] == "vmess"]
    await asyncio.to_thread(_write_local_xray_sync, trojan_c, vless_c, vmess_c)
    await sync_ssh_bw_rules_on_server(1)

async def sync_xray_by_server(server_id: int | None = None):
    servers = [await get_server_by_id(server_id)] if server_id else await get_servers_with_usage(only_active=False)
    hot_script_b64 = ""
    if os.path.exists("/usr/local/bin/xray-hot-sync.py"):
        with open("/usr/local/bin/xray-hot-sync.py", "rb") as f:
            hot_script_b64 = base64.b64encode(f.read()).decode("ascii")

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        for srv in servers:
            if not srv:
                continue
            sid = srv["id"]
            if srv.get("is_local", 1) == 1:
                await sync_xray_clients()
                continue
            async with db.execute(
                "SELECT acc_type, username, password_or_uuid FROM accounts WHERE COALESCE(server_id, 1) = ? AND acc_type IN ('trojan', 'vless', 'vmess') AND is_locked = 0",
                (sid,)
            ) as cur:
                rows = await cur.fetchall()

            trojan_c = [{"password": r[2], "email": r[1]} for r in rows if r[0] == "trojan"]
            vless_c = [{"id": r[2], "email": r[1]} for r in rows if r[0] == "vless"]
            vmess_c = [{"id": r[2], "alterId": 0, "email": r[1]} for r in rows if r[0] == "vmess"]
            payload_b64 = base64.b64encode(json.dumps({"trojan": trojan_c, "vless": vless_c, "vmess": vmess_c}).encode("utf-8")).decode("ascii")

            remote_cmd = (
                "systemctl stop tgbot-vpn 2>/dev/null || true; "
                "systemctl disable tgbot-vpn 2>/dev/null || true; "
                "rm -f /etc/cron.d/vpn-auto-expired /usr/local/bin/check-vpn-expired.py; "
                f"echo '{hot_script_b64}' | base64 -d > /usr/local/bin/xray-hot-sync.py && "
                "chmod +x /usr/local/bin/xray-hot-sync.py && "
                f"python3 /usr/local/bin/xray-hot-sync.py --b64 {payload_b64}"
            )
            await asyncio.to_thread(_run_remote_ssh_sync, srv, remote_cmd)
            await sync_ssh_bw_rules_on_server(sid)



def get_server_stats() -> dict:
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    return {
        "cpu": f"{cpu:.1f}%",
        "ram": f"{ram.used // (1024**2)} MB / {ram.total // (1024**2)} MB ({ram.percent}%)",
        "disk": f"{disk.used // (1024**3)} GB / {disk.total // (1024**3)} GB ({disk.percent}%)",
        "tx": f"{net.bytes_sent / (1024**3):.2f} GB",
        "rx": f"{net.bytes_recv / (1024**3):.2f} GB"
    }

def check_service_status(srv: str) -> str:
    try:
        res = subprocess.run(["systemctl", "is-active", srv], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        return "🟢 Aktif" if res.stdout.strip() == "active" else "🔴 Mati"
    except Exception:
        return "🔴 Mati"

# === SLOWDNS (VayDNS) + UDP-CUSTOM + UDPGW (badvpn) ===
SLOWDNS_DIR = "/etc/slowdns"
SLOWDNS_PUB = "/etc/slowdns/server.pub"
SLOWDNS_KEY = "/etc/slowdns/server.key"
SLOWDNS_PORT = 5300
UDPC_PORT = 36712
UDPGW_PORT = 7300

def _sh(cmd: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(["bash", "-c", cmd], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:
        return 1, str(e)

def get_slowdns_pubkey() -> str:
    try:
        with open(SLOWDNS_PUB, encoding="utf-8") as f:
            return f.read().strip() or "-"
    except Exception:
        return "-"

def get_slowdns_ns() -> str:
    d = (DOMAIN or "").strip()
    if not d:
        return "-"
    return d if d.startswith("ns-") else f"ns-{d}"

def get_slowdns_ns_from_host(host: str) -> str:
    h = (host or "").strip()
    if not h:
        return "-"
    return h if h.startswith("ns-") else f"ns-{h}"

def _slowdns_unit(domain: str) -> str:
    return (
        "[Unit]\nDescription=SlowDNS dnstt Server\nAfter=network.target\n\n"
        "[Service]\n"
        f"ExecStart=/usr/local/bin/vaydns-server -udp :{SLOWDNS_PORT} -privkey-file {SLOWDNS_KEY} "
        f"-domain ns-{domain} -upstream 127.0.0.1:90 -record-type txt -dnstt-compat\n"
        "Restart=on-failure\nRestartSec=3\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )

_UDP_CUSTOM_UNIT = (
    "[Unit]\nDescription=UDP Custom (HTTP Custom)\nAfter=network.target\n\n"
    "[Service]\nWorkingDirectory=/etc/udp\n"
    f"ExecStart=/usr/local/bin/udp-custom server -exclude 53,{SLOWDNS_PORT}\n"
    "Restart=on-failure\nRestartSec=3\n\n"
    "[Install]\nWantedBy=multi-user.target\n"
)

_UDPGW_UNIT = (
    "[Unit]\nDescription=BadVPN UDP Gateway (udpgw)\nAfter=network.target\n\n"
    "[Service]\nType=simple\n"
    f"ExecStart=/usr/local/bin/udpgw --listen-addr 0.0.0.0:{UDPGW_PORT} "
    "--max-clients 200 --max-connections-for-client 6 --udp-mtu 65507\n"
    "Restart=on-failure\nRestartSec=3\n\n"
    "[Install]\nWantedBy=multi-user.target\n"
)

_UDP_CUSTOM_CONF = (
    "{\n"
    f'  "listen": ":{UDPC_PORT}",\n'
    '  "stream_buffer": 33554432,\n'
    '  "receive_buffer": 83886080,\n'
    '  "auth": { "mode": "passwords" }\n'
    "}\n"
)

def _iptables_script() -> str:
    """Rule NAT/INPUT idempotent. 7300 di-ACCEPT SEBELUM catch-all 1:65535,
    kalau tidak paket UDPGW ikut ter-REDIRECT ke udp-custom dan badvpn mati."""
    return (
        "#!/bin/bash\n"
        'IFACE=$(ip route get 8.8.8.8 2>/dev/null | awk \'{print $5; exit}\')\n'
        '[ -z "$IFACE" ] && IFACE="eth0"\n'
        "# hapus hanya rule milik kita, jangan flush PREROUTING (bisa buang rule lain)\n"
        f"iptables -t nat -D PREROUTING -i \"$IFACE\" -p udp --dport {SLOWDNS_PORT} -j ACCEPT 2>/dev/null\n"
        f"iptables -t nat -D PREROUTING -i \"$IFACE\" -p udp --dport 53 -j REDIRECT --to-ports {SLOWDNS_PORT} 2>/dev/null\n"
        f"iptables -t nat -D PREROUTING -i \"$IFACE\" -p udp --dport {UDPGW_PORT} -j ACCEPT 2>/dev/null\n"
        f"iptables -t nat -D PREROUTING -i \"$IFACE\" -p udp --dport 1:65535 -j REDIRECT --to-ports {UDPC_PORT} 2>/dev/null\n"
        "# urutan WAJIB: pengecualian dulu, catch-all terakhir\n"
        f"iptables -t nat -A PREROUTING -i \"$IFACE\" -p udp --dport {SLOWDNS_PORT} -j ACCEPT\n"
        f"iptables -t nat -A PREROUTING -i \"$IFACE\" -p udp --dport 53 -j REDIRECT --to-ports {SLOWDNS_PORT}\n"
        f"iptables -t nat -A PREROUTING -i \"$IFACE\" -p udp --dport {UDPGW_PORT} -j ACCEPT\n"
        f"iptables -t nat -A PREROUTING -i \"$IFACE\" -p udp --dport 1:65535 -j REDIRECT --to-ports {UDPC_PORT}\n"
        f"for P in 53 {SLOWDNS_PORT} {UDPC_PORT} {UDPGW_PORT}; do\n"
        "  iptables -C INPUT -p udp --dport $P -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p udp --dport $P -j ACCEPT\n"
        "done\n"
        "mkdir -p /etc/iptables\n"
        "iptables-save > /etc/iptables/rules.v4 2>/dev/null || true\n"
    )

def _write(path: str, content: str, mode: int = 0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, mode)

def _fetch_bin(name: str, urls: list[str], timeout: int = 120) -> bool:
    """Unduh binary ke /usr/local/bin/<name>; sukses bila ELF valid."""
    dst = f"/usr/local/bin/{name}"
    if os.path.exists(dst):
        try:
            with open(dst, "rb") as f:
                if f.read(4) == b"\x7fELF":
                    return True
        except Exception:
            pass
        os.remove(dst)
    for u in urls:
        rc, _ = _sh(f"wget -qO {shlex.quote(dst)} {shlex.quote(u)}", timeout=timeout)
        if os.path.exists(dst):
            try:
                with open(dst, "rb") as f:
                    if f.read(4) == b"\x7fELF":
                        os.chmod(dst, 0o755)
                        return True
            except Exception:
                pass
            os.remove(dst)
    return False

def _arch() -> str:
    m = (os.uname().machine or "").lower()
    return "arm64" if m in ("aarch64", "arm64") else "amd64"

def install_udp_stack() -> str:
    """Pasang vaydns-server + udpgw + udp-custom, unit systemd, iptables.
    Idempotent: file/unit ditulis ulang, service di-enable."""
    lines = []
    _sh(f"mkdir -p {SLOWDNS_DIR} /etc/udp /etc/iptables")
    arch = _arch()
    ok_v = _fetch_bin("vaydns-server", [
        f"https://raw.githubusercontent.com/sunekugg/tunelv2/main/bin/vaydns-server-linux-{arch}",
        f"https://github.com/net2share/vaydns/releases/download/v0.2.8/vaydns-server-linux-{arch}",
    ])
    lines.append(f"vaydns-server: {'OK' if ok_v else 'GAGAL unduh'}")
    ok_g = _fetch_bin("udpgw", [
        f"https://raw.githubusercontent.com/sunekugg/tunelv2/main/bin/udpgw-linux-{arch}",
        "https://raw.githubusercontent.com/Midas-XYZ/tunnel/main/udpgw",
    ])
    lines.append(f"udpgw: {'OK' if ok_g else 'GAGAL unduh'}")
    if arch == "arm64":
        uc_urls = ["https://raw.githubusercontent.com/fisabiliyusri/Mantap/main/udp-custom/udp-custom-linux-arm"]
    else:
        uc_urls = [
            "https://raw.githubusercontent.com/http-custom/udp-custom/main/bin/udp-custom-linux-amd64",
            "https://ghfast.top/https://raw.githubusercontent.com/http-custom/udp-custom/main/bin/udp-custom-linux-amd64",
        ]
    ok_uc = _fetch_bin("udp-custom", uc_urls)
    lines.append(f"udp-custom: {'OK' if ok_uc else 'GAGAL unduh'}")

    if ok_v:
        _sh("ln -sf /usr/local/bin/vaydns-server /usr/local/bin/dnstt-server")

    # key SlowDNS hanya dibuat sekali — regenerate lewat tombol panel
    if ok_v and not os.path.exists(SLOWDNS_PUB):
        _sh(f"/usr/local/bin/vaydns-server -gen-key -privkey-file {SLOWDNS_KEY} "
            f"-pubkey-file {SLOWDNS_PUB}")
        if os.path.exists(SLOWDNS_KEY):
            os.chmod(SLOWDNS_KEY, 0o600)
        lines.append(f"public key: {'dibuat' if os.path.exists(SLOWDNS_PUB) else 'gagal'}")

    _write("/etc/udp/config.json", _UDP_CUSTOM_CONF)
    _write("/etc/systemd/system/slowdns.service", _slowdns_unit(DOMAIN))
    _write("/etc/systemd/system/udp-custom.service", _UDP_CUSTOM_UNIT)
    _write("/etc/systemd/system/udpgw.service", _UDPGW_UNIT)
    _write("/etc/iptables/apply-slowdns-udp.sh", _iptables_script(), 0o755)
    _write("/etc/systemd/system/iptables-restore.service",
           "[Unit]\nDescription=Restore iptables NAT rules\nAfter=network.target\n\n"
           "[Service]\nType=oneshot\nExecStart=/etc/iptables/apply-slowdns-udp.sh\n\n"
           "[Install]\nWantedBy=multi-user.target\n")

    # :53 harus bebas dari systemd-resolved
    _sh("systemctl stop systemd-resolved 2>/dev/null; systemctl disable systemd-resolved 2>/dev/null; "
        "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf")

    _sh("systemctl daemon-reload")
    _sh("/etc/iptables/apply-slowdns-udp.sh")
    svcs = []
    if ok_v:
        svcs.append("slowdns")
    if ok_g:
        svcs.append("udpgw")
    if ok_uc:
        svcs.append("udp-custom")
    svcs.append("iptables-restore")
    _sh("systemctl enable --now " + " ".join(svcs) + " 2>&1")
    _sh("systemctl restart " + " ".join(svcs) + " 2>&1")
    return " | ".join(lines)

def restart_udp_services(which: str) -> str:
    """which: slowdns | udp-custom | udpgw | all"""
    if which == "all":
        _sh("/etc/iptables/apply-slowdns-udp.sh")
        rc, out = _sh("systemctl restart slowdns udp-custom udpgw iptables-restore 2>&1")
        return "iptables + slowdns + udp-custom + udpgw di-restart." if rc == 0 else f"Gagal: {out[:200]}"
    rc, out = _sh(f"systemctl restart {shlex.quote(which)} 2>&1")
    return f"{which} di-restart." if rc == 0 else f"Gagal restart {which}: {out[:200]}"

def regen_slowdns_key() -> tuple[bool, str]:
    if not os.path.exists("/usr/local/bin/vaydns-server"):
        return False, "vaydns-server belum terpasang — pakai tombol Pasang Stack UDP dulu."
    for p in (SLOWDNS_KEY, SLOWDNS_PUB):
        if os.path.exists(p):
            os.remove(p)
    _sh(f"/usr/local/bin/vaydns-server -gen-key -privkey-file {SLOWDNS_KEY} -pubkey-file {SLOWDNS_PUB}")
    if os.path.exists(SLOWDNS_KEY):
        os.chmod(SLOWDNS_KEY, 0o600)
    if not os.path.exists(SLOWDNS_PUB):
        return False, "Gagal generate public key baru."
    _sh("systemctl restart slowdns 2>&1")
    return True, get_slowdns_pubkey()

def regen_slowdns_ns() -> tuple[bool, str]:
    ns = get_slowdns_ns()
    if ns == "-":
        return False, "DOMAIN belum diset di config.env."
    _write("/etc/systemd/system/slowdns.service", _slowdns_unit(ns[3:] if ns.startswith("ns-") else ns))
    _sh("systemctl daemon-reload; systemctl restart slowdns 2>&1")
    return True, ns


async def _dynamic_restore_table(db, table_name: str, rows: list[dict], clear_first: bool = True) -> int:
    if clear_first:
        await db.execute(f"DELETE FROM {table_name}")
    if not rows:
        return 0
    async with db.execute(f"PRAGMA table_info({table_name})") as cur:
        pragma_rows = await cur.fetchall()
    valid_cols = [r[1] for r in pragma_rows]
    if not valid_cols:
        return 0

    count = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        cols = [c for c in valid_cols if c in r]
        if not cols:
            continue
        col_names = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        vals = [r[c] for c in cols]
        await db.execute(
            f"INSERT OR REPLACE INTO {table_name} ({col_names}) VALUES ({placeholders})",
            vals
        )
        count += 1
    return count


async def restore_from_sh_content(content_text: str) -> tuple[bool, str]:
    m = re.search(r'BACKUP_B64="([A-Za-z0-9+/=\s]+)"', content_text)
    if not m:
        return False, "Format file backup .sh tidak valid (BACKUP_B64 tidak ditemukan)."
    try:
        b64_clean = re.sub(r"\s+", "", m.group(1))
        raw = base64.b64decode(b64_clean).decode("utf-8")
        data = json.loads(raw)
        exported_at = data.get("exported_at_wib", "-")

        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            # 1. Cek koneksi Server Node tambahan: Jika mati, langsung SKIP (jangan pindah ke Server 1)
            remote_srvs_raw = [s for s in data.get("remote_servers", []) if int(s.get("is_local", 0)) == 0]
            valid_remote_servers = []
            dead_server_ids: set[int] = set()

            for srv in remote_srvs_raw:
                sid = int(srv["id"])
                ok_test, _ = await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, "echo 'ALIVE'", 4)
                if ok_test:
                    srv["is_active"] = 1
                    valid_remote_servers.append(srv)
                else:
                    print(f"[RESTORE] Server Node #{sid} ({srv.get('name')}) mati/tidak terjangkau -> Di-skip.")
                    dead_server_ids.add(sid)

            # 2. Restore hanya Server Node yang hidup
            await db.execute("DELETE FROM servers WHERE is_local = 0")
            srv_restored = await _dynamic_restore_table(db, "servers", valid_remote_servers, clear_first=False)

            # 3. Restore Users & Pastikan Admin Utama tetap admin
            usr_restored = await _dynamic_restore_table(db, "users", data.get("users", []), clear_first=False)
            await db.execute("UPDATE users SET is_admin = 1 WHERE tg_id = ?", (ADMIN_ID,))

            # 4. Restore Creation History, Force Join & Settings
            hist_restored = await _dynamic_restore_table(db, "creation_history", data.get("creation_history", []), clear_first=True)
            await _dynamic_restore_table(db, "force_join_chats", data.get("force_join_chats", []), clear_first=True)
            await _dynamic_restore_table(db, "settings", data.get("settings", []), clear_first=False)

            # 5. Filter akun: Hanya restore akun milik Server 1 dan Server Node yang hidup (Skip akun dari node mati)
            active_server_ids = {1} | {int(s["id"]) for s in valid_remote_servers}
            raw_accounts = data.get("accounts", [])
            valid_accounts = []
            skipped_accounts_count = 0

            for a in raw_accounts:
                orig_sid = int(a.get("server_id") or 1)
                if orig_sid in dead_server_ids or orig_sid not in active_server_ids:
                    skipped_accounts_count += 1
                    continue
                valid_accounts.append(a)

            await _dynamic_restore_table(db, "accounts", valid_accounts, clear_first=True)
            await db.commit()

        # 6. Buat ulang sistem SSH & Xray di Server 1 dan Server Node yang aktif
        ssh_restored, xray_restored = 0, 0
        affected_servers: set[int] = {1}

        for a in valid_accounts:
            sid = int(a.get("server_id") or 1)
            affected_servers.add(sid)
            if a.get("acc_type") == "ssh":
                await create_ssh_on_server(sid, a["username"], a["password_or_uuid"])
                if int(a.get("is_locked", 0)) == 1:
                    await lock_ssh_on_server(sid, a["username"], lock=True)
                ssh_restored += 1
            else:
                xray_restored += 1

        if data.get("ssh_banner"):
            update_ssh_banner(data["ssh_banner"])
            await sync_banner_all_servers()

        await sync_xray_by_server()
        for sid in affected_servers:
            await sync_ssh_bw_rules_on_server(sid)

        await generate_portable_backup_sh()
        summary = (
            f"Backup ({exported_at}) berhasil dipulihkan!\n"
            f"• Akun SSH: {ssh_restored} akun\n"
            f"• Akun Xray (VLESS/VMess/Trojan): {xray_restored} akun\n"
            f"• Data User: {usr_restored} user (Riwayat Kuota: {hist_restored})\n"
            f"• Server Node Aktif: {srv_restored} server\n"
        )
        if dead_server_ids or skipped_accounts_count > 0:
            summary += f"⏭ Dilewati (Server Node Mati): { len(dead_server_ids) } server ({skipped_accounts_count} akun tidak di-restore).\n"
        summary += "• Tanggal Buat, Tanggal Expired, Kuota GB & Preset Bug telah kembali sesuai aslinya."
        return True, summary
    except Exception as e:
        return False, f"Gagal memproses restore: {e}"

async def send_account_topic_notification(
    bot,
    tg_id: int,
    full_name: str,
    acc_type: str,
    username: str,
    server_id: int,
    expired_at: str,
    is_trial: bool = False,
    dur_label: str = ""
):
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        # Toggle notif pembuatan akun ke topic
        if (await get_setting("notif_create_account")) != "1":
            return
        if (await get_setting("notif_topic_enabled")) != "1":
            return
        grp_id_str = (await get_setting("notif_group_id") or "").strip()
        if not grp_id_str:
            return
        topic_id_str = (await get_setting("notif_topic_id") or "0").strip()

        chat_id = int(grp_id_str) if grp_id_str.lstrip("-").isdigit() else grp_id_str
        thread_id = int(topic_id_str) if topic_id_str.isdigit() and int(topic_id_str) > 0 else None

        srv = await get_server_by_id(server_id)
        s_name = srv["name"] if srv else f"Server #{server_id}"
        s_dom = srv["domain"] if srv else DOMAIN
        used_u = srv.get("used_users", 0) if srv else 0
        max_u = srv.get("max_users", 0) if srv else 0
        cap_str = f"{used_u} / {'♾ Unlimited' if max_u == 0 else str(max_u)+' User'}"

        def_q = await get_setting("default_quota_gb") or "0"
        def_spd = await get_setting("default_speed_mbps") or "0"
        q_str = "Unlimited" if float(def_q) <= 0 or is_trial else f"{def_q} GB"
        spd_str = "Unlimited" if int(def_spd) <= 0 or is_trial else f"{def_spd} Mbps"

        uid_s = str(tg_id)
        masked_uid = uid_s[:4] + "****" + uid_s[-2:] if len(uid_s) > 6 else uid_s
        masked_uname = (username[:4] + "***") if len(username) > 4 else (username[:2] + "***")
        safe_name = html.escape((full_name or "User VPN")[:25])

        kat_badge = "🆓 AKUN TRIAL" if is_trial else "🔑 AKUN REGULER"
        proto_map = {
            "ssh": "SSH & Dropbear (WS/TLS)",
            "trojan": "TROJAN (WS/gRPC/Upgrade)",
            "vless": "VLESS (WS/gRPC/Upgrade)",
            "vmess": "VMESS (WS/gRPC/Upgrade)"
        }
        proto_str = proto_map.get(acc_type.lower(), acc_type.upper())
        wib_now = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")

        me = await bot.get_me()
        bot_url = f"https://t.me/{me.username}"

        text = (
            "🔔 <b>NOTIFIKASI PEMBUATAN AKUN BARU</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Pengguna:</b> <code>{safe_name}</code> (<code>{masked_uid}</code>)\n"
            f"🏷 <b>Kategori:</b> <code>{kat_badge}</code>\n"
            f"⚡ <b>Protokol:</b> <code>{proto_str}</code>\n"
            f"🔖 <b>Username:</b> <code>{html.escape(masked_uname)}</code>\n"
            f"🌐 <b>Server VPS:</b> <code>{html.escape(s_name)} ({html.escape(s_dom)})</code>\n"
            f"📊 <b>Kapasitas Server:</b> <code>{cap_str}</code>\n"
            f"⏳ <b>Masa Aktif:</b> <code>{html.escape(dur_label)} (Exp: {html.escape(expired_at)})</code>\n"
            f"📦 <b>Kuota / Speed:</b> <code>{q_str} / {spd_str}</code>\n"
            f"🕒 <b>Waktu Buat:</b> <code>{wib_now}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Ingin membuat akun VPN gratis &amp; cepat? Klik tombol di bawah!</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Buat Akun VPN di Bot", url=bot_url)]
        ])
        kwargs = {"chat_id": chat_id, "text": text, "reply_markup": kb}
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        await bot.send_message(**kwargs)
    except Exception as e:
        print(f"[TOPIC NOTIF ERROR] {e}")

# === ENGINE FITUR KOMUNITAS: KICK SESI, BUG CHECKER & AUTO-HEAL ===
import socket
import ssl
from .db import set_setting

_NODE_HEALTH_STATE: dict[int, dict] = {}

async def kick_user_sessions_on_server(server_id: int, acc_type: str, username: str, secret: str = "") -> tuple[bool, str]:
    srv = await get_server_by_id(server_id)
    if acc_type == "ssh":
        u_q = shlex.quote(username)
        cmd = f"usermod -U {u_q} 2>/dev/null || true; pkill -9 -u {u_q} 2>/dev/null || true"
        if not srv or srv.get("is_local", 1) == 1:
            await asyncio.to_thread(
                subprocess.run, ["bash", "-c", cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
            )
        else:
            await asyncio.to_thread(_run_remote_ssh_sync, srv, cmd)
        _CLUSTER_ONLINE_CACHE.get("ssh_by_srv", {}).get(server_id, {}).pop(username, None)
        return True, "Seluruh sesi SSH yang menggantung berhasil diputus."
    else:
        _CLUSTER_ONLINE_CACHE.get("xray_by_srv", {}).get(server_id, {}).pop(username, None)
        await sync_xray_by_server(server_id)
        return True, "Sesi Xray berhasil di-refresh dan disinkronkan ulang."


async def _send_status_alert_to_topic_and_admin(bot, html_text: str):
    try:
        if (await get_setting("notif_topic_enabled")) == "1":
            grp_id_str = (await get_setting("notif_group_id") or "").strip()
            topic_id_str = (await get_setting("notif_topic_id") or "0").strip()
            if grp_id_str:
                chat_id = int(grp_id_str) if grp_id_str.lstrip("-").isdigit() else grp_id_str
                thread_id = int(topic_id_str) if topic_id_str.isdigit() and int(topic_id_str) > 0 else None
                kwargs = {"chat_id": chat_id, "text": html_text}
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                await bot.send_message(**kwargs)
    except Exception as e:
        print(f"[AUTO-HEAL TOPIC ERROR] {e}")

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=html_text)
    except Exception:
        pass

async def run_auto_heal_and_monitor(bot, manual_trigger: bool = False) -> list[str]:
    logs_report: list[str] = []
    wib_str = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")

    srv_local = await get_server_by_id(1)
    s1_name = srv_local["name"] if srv_local else "Server Utama"
    s1_dom = srv_local["domain"] if srv_local else DOMAIN

    core_services = ["xray", "ssh", "dropbear", "haproxy", "nginx"]
    dead_local = []
    healed_local = []

    for svc in core_services:
        if "Mati" in check_service_status(svc):
            dead_local.append(svc)
            await asyncio.to_thread(
                subprocess.run, ["systemctl", "restart", svc],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
            )
            if svc == "xray":
                await sync_xray_clients()
            await asyncio.sleep(1)
            if "Aktif" in check_service_status(svc):
                healed_local.append(svc)

    if dead_local:
        status_after = "🟢 Semua Service Kembali Normal!" if len(healed_local) == len(dead_local) else f"⚠️ Pulih Sebagian ({', '.join(healed_local) or 'Gagal'})"
        alert_txt = (
            "🩺 <b>LAPORAN AUTO-HEAL SERVICE SERVER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥 <b>Server:</b> <code>{html.escape(s1_name)} ({html.escape(s1_dom)})</code>\n"
            f"⚠️ <b>Service Terhenti:</b> <code>{', '.join(dead_local)}</code>\n"
            "🔧 <b>Tindakan Bot:</b> Otomatis melakukan <i>restart</i> &amp; <i>hot-sync</i> ulang.\n"
            f"✅ <b>Status Saat Ini:</b> <code>{status_after}</code>\n"
            f"🕒 <b>Waktu:</b> <code>{wib_str}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        await _send_status_alert_to_topic_and_admin(bot, alert_txt)
        logs_report.append(f"🖥 <b>{html.escape(s1_name)}:</b> Auto-Heal dijalankan pada <code>{', '.join(dead_local)}</code> ({status_after})")
    else:
        logs_report.append(f"🖥 <b>{html.escape(s1_name)}:</b> 🟢 Semua service (Xray, SSH, Dropbear, HAProxy, Nginx) normal.")

    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM servers WHERE is_local = 0") as cur:
            remote_nodes = [dict(r) for r in await cur.fetchall()]

    hidden_raw = await get_setting("auto_hidden_servers") or ""
    hidden_set = {int(x) for x in hidden_raw.split(",") if x.strip().isdigit()}

    for node in remote_nodes:
        sid = int(node["id"])
        n_name = node["name"]
        n_dom = node["domain"]
        st_mem = _NODE_HEALTH_STATE.setdefault(sid, {"fails": 0, "down_since": None, "is_down": False})

        heal_cmd = (
            "HEALED=''; "
            "for s in xray ssh dropbear haproxy nginx; do "
            "  if ! systemctl is-active --quiet $s 2>/dev/null; then "
            "    systemctl restart $s 2>/dev/null || true; "
            "    if systemctl is-active --quiet $s 2>/dev/null; then HEALED=\"$HEALED $s\"; fi; "
            "  fi; "
            "done; "
            "echo \"ALIVE|${HEALED}\""
        )
        ok_ssh, out_ssh = await asyncio.to_thread(_run_remote_ssh_detail_sync, node, heal_cmd, 15)

        if not ok_ssh:
            st_mem["fails"] += 1
            if st_mem["fails"] >= 2 and not st_mem["is_down"]:
                st_mem["is_down"] = True
                st_mem["down_since"] = time.time()
                if int(node.get("is_active", 1)) == 1:
                    hidden_set.add(sid)
                    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                        await db.execute("UPDATE servers SET is_active = 0 WHERE id = ?", (sid,))
                        await db.commit()
                    await set_setting("auto_hidden_servers", ",".join(str(x) for x in sorted(hidden_set)))

                down_txt = (
                    "🚨 <b>INFO STATUS SERVER — GANGGUAN (OFFLINE)</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🖥 <b>Server:</b> <code>{html.escape(n_name)} ({html.escape(n_dom)})</code>\n"
                    "🔴 <b>Status:</b> <code>Tidak Merespons (Connection Timeout)</code>\n"
                    f"🕒 <b>Waktu Terdeteksi:</b> <code>{wib_str}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "ℹ️ <i>Server ini disembunyikan sementara dari menu pembuatan akun hingga koneksi stabil. Pengguna dapat menggunakan fitur <b>🔀 Pindah Server</b> di menu Akun Saya.</i>"
                )
                await _send_status_alert_to_topic_and_admin(bot, down_txt)
            logs_report.append(f"🖥 <b>{html.escape(n_name)}:</b> 🔴 Offline / Timeout (Gagal #{st_mem['fails']})")
        else:
            if st_mem["is_down"] or sid in hidden_set:
                down_sec = int(time.time() - (st_mem["down_since"] or time.time()))
                down_min = max(1, down_sec // 60)
                st_mem["is_down"] = False
                st_mem["fails"] = 0
                st_mem["down_since"] = None
                if sid in hidden_set:
                    hidden_set.discard(sid)
                    async with aiosqlite.connect(DB_PATH, timeout=15) as db:
                        await db.execute("UPDATE servers SET is_active = 1 WHERE id = ?", (sid,))
                        await db.commit()
                    await set_setting("auto_hidden_servers", ",".join(str(x) for x in sorted(hidden_set)))

                await sync_xray_by_server(sid)
                await sync_ssh_bw_rules_on_server(sid)

                up_txt = (
                    "🟢 <b>INFO STATUS SERVER — KEMBALI NORMAL (ONLINE)</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🖥 <b>Server:</b> <code>{html.escape(n_name)} ({html.escape(n_dom)})</code>\n"
                    "✅ <b>Status:</b> <code>Online &amp; Seluruh Akun Telah Disinkronkan</code>\n"
                    f"⏱ <b>Durasi Gangguan:</b> <code>~{down_min} Menit</code>\n"
                    f"🕒 <b>Waktu Pulih:</b> <code>{wib_str}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🚀 <i>Server kini telah dibuka kembali di menu pembuatan akun!</i>"
                )
                await _send_status_alert_to_topic_and_admin(bot, up_txt)
                logs_report.append(f"🖥 <b>{html.escape(n_name)}:</b> 🟢 Pulih Kembali (Online &amp; Disinkronkan)")
            else:
                st_mem["fails"] = 0
                last_line = out_ssh.splitlines()[-1] if out_ssh else ""
                healed_part = last_line.split("|", 1)[1].strip() if "|" in last_line else ""
                if healed_part:
                    await sync_xray_by_server(sid)
                    heal_node_txt = (
                        "🩺 <b>LAPORAN AUTO-HEAL SERVICE NODE</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🖥 <b>Server Node:</b> <code>{html.escape(n_name)} ({html.escape(n_dom)})</code>\n"
                        f"⚠️ <b>Service Di-restart Otomatis:</b> <code>{html.escape(healed_part)}</code>\n"
                        "✅ <b>Status Saat Ini:</b> <code>🟢 Kembali Normal &amp; Tersinkronisasi</code>\n"
                        f"🕒 <b>Waktu:</b> <code>{wib_str}</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    await _send_status_alert_to_topic_and_admin(bot, heal_node_txt)
                    logs_report.append(f"🖥 <b>{html.escape(n_name)}:</b> 🩺 Auto-Heal merestart <code>{html.escape(healed_part)}</code>")
                else:
                    logs_report.append(f"🖥 <b>{html.escape(n_name)}:</b> 🟢 Online &amp; Semua Service Normal")

    return logs_report

# === ENGINE PRO BUG SCANNER 101 WS & PRESET HEALTH CHECK ===
async def _probe_ws_upgrade_101(target_ip: str, ws_host_header: str, use_tls: bool = False, sni_host: str = "") -> tuple[bool, str]:
    port = 443 if use_tls else 80
    ssl_ctx = None
    if use_tls:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        conn_coro = asyncio.open_connection(
            target_ip, port,
            ssl=ssl_ctx,
            server_hostname=(sni_host or ws_host_header) if use_tls else None
        )
        reader, writer = await asyncio.wait_for(conn_coro, timeout=3.5)
        req = (
            f"GET /vless HTTP/1.1\r\n"
            f"Host: {ws_host_header}\r\n"
            "User-Agent: Mozilla/5.0 (Linux; Android 14)\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(req.encode("utf-8"))
        await asyncio.wait_for(writer.drain(), timeout=2.5)
        first_line_bytes = await asyncio.wait_for(reader.readline(), timeout=3.5)
        writer.close()
        first_line = first_line_bytes.decode("utf-8", errors="ignore").strip()
        if "101" in first_line:
            return True, "🔥 101 Switching Protocols (TEMBUS!)"
        m_code = re.search(r"HTTP/\d\.\d\s+(\d+\s+.*)", first_line)
        code_str = m_code.group(1).strip() if m_code else (first_line[:32] or "No Response")
        return False, f"🔴 {code_str}"
    except Exception as e:
        return False, f"🔴 {type(e).__name__}"

async def check_bug_host_isp(raw_host: str, target_domain: str = "") -> dict:
    clean = (raw_host or "").strip().lower()
    clean = re.sub(r"^https?://", "", clean)
    clean = clean.split("/")[0].split("?")[0].split(":")[0].strip()

    if not clean or not re.match(r"^[a-z0-9.-]{3,120}$", clean):
        return {"ok": False, "host": clean, "error": "Format domain atau IP tidak valid."}

    if not target_domain:
        try:
            srvs = await get_servers_with_usage(only_active=True)
            target_domain = srvs[0]["domain"] if srvs else DOMAIN
        except Exception:
            target_domain = DOMAIN

    ips: list[str] = []
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, clean, None, socket.AF_INET)
        for info in infos:
            ip_addr = info[4][0]
            if ip_addr not in ips:
                ips.append(ip_addr)
    except Exception:
        pass

    if not ips:
        return {"ok": False, "host": clean, "error": f"Host <code>{html.escape(clean)}</code> tidak ditemukan atau tidak memiliki IPv4 aktif."}

    primary_ip = ips[0]

    ping_ms = 0
    t0 = time.perf_counter()
    try:
        _, w_p = await asyncio.wait_for(asyncio.open_connection(primary_ip, 443), timeout=3.0)
        ping_ms = int((time.perf_counter() - t0) * 1000)
        w_p.close()
    except Exception:
        try:
            t0 = time.perf_counter()
            _, w_p = await asyncio.wait_for(asyncio.open_connection(primary_ip, 80), timeout=3.0)
            ping_ms = int((time.perf_counter() - t0) * 1000)
            w_p.close()
        except Exception:
            ping_ms = -1

    asn_str = "Unknown Network"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as sess:
            async with sess.get(f"http://ip-api.com/json/{primary_ip}?fields=status,isp,org,as") as resp:
                if resp.status == 200:
                    j_as = await resp.json()
                    if j_as.get("status") == "success":
                        as_code = j_as.get("as") or ""
                        isp_n = j_as.get("isp") or j_as.get("org") or ""
                        asn_str = f"{as_code} ({isp_n})".strip() if as_code else isp_n
    except Exception:
        pass

    http_status = "🔴 Timeout / Tertutup"
    https_status = "🔴 Timeout / Tertutup"
    server_hdr = "-"
    cf_ray = ""
    tls_ok = False
    tls_ver = "-"
    alpn_str = "-"
    sans_list: list[str] = []

    timeout_cfg = aiohttp.ClientTimeout(total=4.5)
    headers_req = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0"}

    try:
        async with aiohttp.ClientSession(timeout=timeout_cfg) as sess:
            async with sess.get(f"http://{clean}/", headers=headers_req, allow_redirects=False) as resp:
                http_status = f"🟢 {resp.status} {resp.reason}"
                if resp.headers.get("Server"):
                    server_hdr = resp.headers.get("Server")
                if resp.headers.get("CF-Ray"):
                    cf_ray = resp.headers.get("CF-Ray")
    except Exception as e:
        http_status = f"🔴 {type(e).__name__}"

    try:
        ctx_cert = ssl.create_default_context()
        ctx_cert.set_alpn_protocols(["h2", "http/1.1"])
        _, w_s = await asyncio.wait_for(
            asyncio.open_connection(primary_ip, 443, ssl=ctx_cert, server_hostname=clean),
            timeout=3.5
        )
        ssl_obj = w_s.get_extra_info("ssl_object")
        if ssl_obj:
            tls_ok = True
            tls_ver = ssl_obj.version() or "TLS"
            alpn_str = ssl_obj.selected_alpn_protocol() or "http/1.1"
            peercert = ssl_obj.getpeercert() or {}
            for k_san, v_san in peercert.get("subjectAltName", ()):
                if k_san == "DNS" and v_san not in sans_list:
                    sans_list.append(v_san)
        w_s.close()
    except Exception:
        try:
            ctx_noverify = ssl.create_default_context()
            ctx_noverify.check_hostname = False
            ctx_noverify.verify_mode = ssl.CERT_NONE
            ctx_noverify.set_alpn_protocols(["h2", "http/1.1"])
            _, w_s = await asyncio.wait_for(
                asyncio.open_connection(primary_ip, 443, ssl=ctx_noverify, server_hostname=clean),
                timeout=3.5
            )
            ssl_obj = w_s.get_extra_info("ssl_object")
            tls_ok = True
            if ssl_obj:
                tls_ver = ssl_obj.version() or "TLS (Self-Signed)"
                alpn_str = ssl_obj.selected_alpn_protocol() or "http/1.1"
            w_s.close()
        except Exception:
            pass

    ssl_ctx_aio = ssl.create_default_context()
    ssl_ctx_aio.check_hostname = False
    ssl_ctx_aio.verify_mode = ssl.CERT_NONE
    try:
        conn = aiohttp.TCPConnector(ssl=ssl_ctx_aio)
        async with aiohttp.ClientSession(connector=conn, timeout=timeout_cfg) as sess:
            async with sess.get(f"https://{clean}/", headers=headers_req, allow_redirects=False) as resp:
                https_status = f"🟢 {resp.status} {resp.reason}"
                tls_ok = True
                if resp.headers.get("Server") and server_hdr == "-":
                    server_hdr = resp.headers.get("Server")
                if resp.headers.get("CF-Ray") and not cf_ray:
                    cf_ray = resp.headers.get("CF-Ray")
    except Exception as e2:
        if tls_ok:
            https_status = f"🟢 Port 443 Terbuka ({tls_ver})"
        else:
            https_status = f"🔴 {type(e2).__name__}"

    wc_host = f"{clean}.{target_domain}" if not clean.endswith(f".{target_domain}") and clean != target_domain else clean
    ws_wc_ok, ws_wc_status = await _probe_ws_upgrade_101(primary_ip, wc_host, use_tls=False)
    if not ws_wc_ok and tls_ok:
        ws_wc_ok, ws_wc_status = await _probe_ws_upgrade_101(primary_ip, wc_host, use_tls=True, sni_host=wc_host)

    ws_cdn_ok, ws_cdn_status = await _probe_ws_upgrade_101(primary_ip, target_domain, use_tls=False)
    if not ws_cdn_ok and tls_ok:
        ws_cdn_ok, ws_cdn_status = await _probe_ws_upgrade_101(primary_ip, target_domain, use_tls=True, sni_host=target_domain)

    cf_prefixes = (
        "104.16.", "104.17.", "104.18.", "104.19.", "104.20.", "104.21.",
        "104.22.", "104.24.", "104.25.", "104.26.", "104.27.", "172.64.",
        "172.67.", "162.159.", "188.114.", "198.41."
    )
    is_cf = (
        "cloudflare" in server_hdr.lower()
        or "cloudflare" in asn_str.lower()
        or bool(cf_ray)
        or any(ip.startswith(cf_prefixes) for ip in ips)
    )

    if ws_wc_ok or is_cf:
        best_mode = "wildcard"
    elif ws_cdn_ok:
        best_mode = "cdn"
    else:
        best_mode = "sni"

    return {
        "ok": True,
        "host": clean,
        "ips": ips[:5],
        "ping_ms": ping_ms,
        "asn": asn_str,
        "server": server_hdr,
        "cf_ray": cf_ray,
        "is_cf": is_cf,
        "http_status": http_status,
        "https_status": https_status,
        "tls_ok": tls_ok,
        "tls_ver": tls_ver,
        "alpn": alpn_str,
        "sans": sans_list[:8],
        "sans_total": len(sans_list),
        "target_domain": target_domain,
        "ws_wc_ok": ws_wc_ok,
        "ws_wc_status": ws_wc_status,
        "ws_cdn_ok": ws_cdn_ok,
        "ws_cdn_status": ws_cdn_status,
        "best_mode": best_mode
    }

async def bulk_check_bug_hosts(raw_text: str, max_hosts: int = 6) -> list[dict]:
    tokens = re.split(r"[\s,;]+", (raw_text or "").strip())
    hosts: list[str] = []
    for t in tokens:
        c = re.sub(r"^https?://", "", t.strip().lower()).split("/")[0].split(":")[0]
        if c and re.match(r"^[a-z0-9.-]{3,120}$", c) and c not in hosts:
            hosts.append(c)
        if len(hosts) >= max_hosts:
            break

    if not hosts:
        return []

    srvs = await get_servers_with_usage(only_active=True)
    t_dom = srvs[0]["domain"] if srvs else DOMAIN
    sem = asyncio.Semaphore(4)

    async def _worker(h: str) -> dict:
        async with sem:
            return await check_bug_host_isp(h, target_domain=t_dom)

    return list(await asyncio.gather(*[_worker(h) for h in hosts]))

async def check_all_presets_health() -> dict:
    srvs = await get_servers_with_usage(only_active=True)
    t_dom = srvs[0]["domain"] if srvs else DOMAIN

    cat_keys = {
        "wildcard": ("🌐 Wildcard", "bug_presets_wildcard"),
        "sni": ("🔒 SNI / SSL", "bug_presets_sni"),
        "cdn": ("☁️ CDN / WS", "bug_presets_cdn"),
    }
    parsed_cats: dict[str, list[tuple[str, str]]] = {}
    unique_domains: set[str] = set()

    for mode, (_, db_key) in cat_keys.items():
        raw = await get_setting(db_key) or ""
        items = []
        for part in re.split(r"[,\n]+", raw):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                lbl, dom = part.split("=", 1)
                lbl, dom = lbl.strip(), dom.strip().lower()
            else:
                lbl, dom = part.strip(), part.strip().lower()
            if dom:
                items.append((lbl, dom))
                unique_domains.add(dom)
        parsed_cats[mode] = items

    sem = asyncio.Semaphore(5)
    scan_map: dict[str, dict] = {}

    async def _scan_one(d: str):
        async with sem:
            scan_map[d] = await check_bug_host_isp(d, target_domain=t_dom)

    await asyncio.gather(*[_scan_one(d) for d in unique_domains])

    report_lines: list[str] = []
    dead_domains: set[str] = set()
    alive_count = 0
    dead_count = 0

    for mode, (title, _) in cat_keys.items():
        items = parsed_cats.get(mode, [])
        report_lines.append(f"<b>{title} ({len(items)} Preset):</b>")
        if not items:
            report_lines.append("  <i>(Kosong)</i>")
            continue
        for lbl, dom in items:
            res = scan_map.get(dom, {})
            if not res.get("ok"):
                dead_domains.add(dom)
                dead_count += 1
                report_lines.append(f"  🔴 <code>{html.escape(dom)}</code> — <b>Mati / NXDOMAIN</b>")
                continue

            is_alive = (
                res.get("ws_wc_ok")
                or res.get("ws_cdn_ok")
                or res.get("tls_ok")
                or res.get("http_status", "").startswith("🟢")
            )
            if is_alive:
                alive_count += 1
                if res.get("ws_wc_ok") or res.get("ws_cdn_ok"):
                    badge = f"🔥 101 WS OK ({res.get('ping_ms', 0)}ms)"
                elif res.get("is_cf"):
                    badge = f"🟢 CF Aktif ({res.get('ping_ms', 0)}ms)"
                elif res.get("tls_ok"):
                    badge = f"🟢 SSL/SNI OK ({res.get('ping_ms', 0)}ms)"
                else:
                    badge = f"🟢 HTTP Aktif ({res.get('ping_ms', 0)}ms)"
                report_lines.append(f"  ✅ <code>{html.escape(dom)}</code> — {badge}")
            else:
                dead_domains.add(dom)
                dead_count += 1
                report_lines.append(f"  🔴 <code>{html.escape(dom)}</code> — <b>Timeout / Port Tertutup</b>")
        report_lines.append("")

    return {
        "report_html": "\n".join(report_lines).strip(),
        "alive_count": alive_count,
        "dead_count": dead_count,
        "dead_domains": sorted(list(dead_domains))
    }

async def remove_dead_presets_from_db(dead_domains: set[str]) -> int:
    if not dead_domains:
        return 0
    removed = 0
    for db_key in ("bug_presets_wildcard", "bug_presets_sni", "bug_presets_cdn"):
        raw = await get_setting(db_key) or ""
        kept = []
        for part in re.split(r"[,\n]+", raw):
            part = part.strip()
            if not part:
                continue
            dom = part.split("=", 1)[1].strip().lower() if "=" in part else part.lower()
            if dom in dead_domains:
                removed += 1
            else:
                kept.append(part)
        await set_setting(db_key, "\n".join(kept))
    await generate_portable_backup_sh()
    return removed

# === LIVE MULTI-SERVER TRAFFIC, VNSTAT & USAGE SYNC ENGINE ===

_REFRESH_CLUSTER_LOCK = asyncio.Lock()



def get_cluster_node_stats(server_id: int) -> dict:
    cached = _CLUSTER_ONLINE_CACHE.get("node_stats", {}).get(server_id, {})
    if server_id == 1:
        st = get_server_stats()
        return {
            "online": True,
            "ram": st["ram"],
            "cpu": st["cpu"],
            "disk": st["disk"],
            "tx": st["tx"],
            "rx": st["rx"],
            "in_mbps": cached.get("in_mbps", "0.00 Mbps"),
            "out_mbps": cached.get("out_mbps", "0.00 Mbps"),
            "vnstat_day": cached.get("vnstat_day", "0.00 MiB"),
            "vnstat_mth": cached.get("vnstat_mth", st["rx"]),
        }
    if not cached:
        return {
            "online": True,
            "ram": "Siap",
            "cpu": "-",
            "disk": "-",
            "tx": "0.00 GB",
            "rx": "0.00 GB",
            "in_mbps": "0.00 Mbps",
            "out_mbps": "0.00 Mbps",
            "vnstat_day": "0.00 MiB",
            "vnstat_mth": "0.00 MiB"
        }
    return {
        "online": bool(cached.get("online", True)),
        "ram": cached.get("ram", "-"),
        "cpu": "-",
        "disk": cached.get("disk", "-"),
        "tx": f"{cached.get('tx_gb', 0.0):.2f} GB",
        "rx": f"{cached.get('rx_gb', 0.0):.2f} GB",
        "in_mbps": cached.get("in_mbps", "0.00 Mbps"),
        "out_mbps": cached.get("out_mbps", "0.00 Mbps"),
        "vnstat_day": cached.get("vnstat_day", "0.00 MiB"),
        "vnstat_mth": cached.get("vnstat_mth", "0.00 MiB"),
    }

_SYS_USERS = {"root", "sshd", "dropbear", "nobody", "daemon", "www-data"}

def _read_ipmap() -> dict[str, dict]:
    """Baca /run/ws_ssh_ipmap.json → {port_ws_lokal: {ip, ts}} (IP asli klien)."""
    try:
        with open("/run/ws_ssh_ipmap.json") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_dropbear_auth_map() -> dict[str, tuple[str, str]]:
    """Baca log dropbear (journal + auth.log/syslog fallback) → {pid_dropbear: (user, remote_ip)}.

    Format journal: 'dropbear[168568]: [168568] Nov 09 05:16:40 Password auth succeeded for 'user' from 1.2.3.4:5678'
    (ada timestamp & PID ganda). Pola: dropbear\\[(\\d+)\\]: .*Password auth succeeded for '([^']+)' from (\\S+)$
    """
    pid_map: dict[str, tuple[str, str]] = {}
    ipmap = _read_ipmap()  # {port_ws_lokal: {ip, ts}} — sumber IP asli klien

    def resolve_ip(raw_ip: str, raw_port: str | None) -> str:
        # Journal mencatat peer dropbear = 127.0.0.1:<port_ws>. <port_ws> adalah
        # key di ipmap yang menyimpan IP asli klien (di-set ws-ssh saat meneruskan).
        ip = raw_ip.strip("[]")
        if ip in ("127.0.0.1", "::1") and raw_port and raw_port in ipmap:
            real = ipmap[raw_port].get("ip")
            if real:
                return real
        return ip

    # Sumber 1: journalctl -u dropbear (dropbear unit log ke journal, bukan auth.log)
    # Format -o cat: "[290053] Nov 09 09:41:03 Password auth succeeded for 'u' from 1.2.3.4:5678"
    #   atau format syslog: "host dropbear[290053]: ..." — tangani keduanya.
    pat = re.compile(
        r"(?:dropbear)?\[(\d+)\].*?Password auth succeeded for '([^']+)' from (\S+?)(?::(\d+))?\s*$"
    )
    try:
        out = subprocess.check_output(
            ["journalctl", "-u", "dropbear", "-n", "300", "--no-pager", "-o", "cat"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
        for ln in out.splitlines():
            m = pat.search(ln)
            if m:
                ip = resolve_ip(m.group(3), m.group(4))
                pid_map[m.group(1)] = (m.group(2), ip)
    except Exception:
        pass
    # Sumber 2 (fallback): auth.log / syslog
    for log_p in ("/var/log/auth.log", "/var/log/syslog"):
        if not os.path.exists(log_p):
            continue
        try:
            with open(log_p, "rb") as f:
                f.seek(0, os.SEEK_END)
                fsize = f.tell()
                f.seek(max(0, fsize - 120000), os.SEEK_SET)
                lines = f.read().decode("utf-8", errors="ignore").splitlines()[-1000:]
            for ln in lines:
                if "dropbear[" not in ln:
                    continue
                m = pat.search(ln)
                if m:
                    ip = resolve_ip(m.group(3), m.group(4))
                    pid_map[m.group(1)] = (m.group(2), ip)
        except Exception:
            pass
    return pid_map

def _read_ps_user_maps() -> tuple[dict[str, str], dict[str, list[str]]]:
    """ps -eo sekali → (pid→user VPN, ppid→[user anak]). Meniru installer.

    Menyelesaikan 'sshd: user [priv/net]' (proses milik root) dengan user tujuan."""
    pid_user_map: dict[str, str] = {}
    ppid_children_map: dict[str, list[str]] = {}
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid=,ppid=,user:32=,args="], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        for pline in out.splitlines():
            parts = pline.strip().split(None, 3)
            if len(parts) < 3:
                continue
            p_id, pp_id, p_usr = parts[0], parts[1], parts[2].strip()
            # ps user:32 masih bisa berakhir '+' kalau >32 char (jarang) — tolak
            if p_usr.endswith("+"):
                p_usr = p_usr[:-1]
            p_args = parts[3] if len(parts) > 3 else ""
            resolved = ""
            if p_usr not in _SYS_USERS:
                resolved = p_usr
            else:
                m = re.search(r"sshd:\s*([a-zA-Z0-9_-]+)", p_args)
                if m and m.group(1) not in _SYS_USERS:
                    resolved = m.group(1)
            if resolved:
                pid_user_map[p_id] = resolved
                ppid_children_map.setdefault(pp_id, []).append(resolved)
    except Exception:
        pass
    return pid_user_map, ppid_children_map

def get_ssh_active_sessions() -> dict[str, list[tuple[int, str]]]:
    """Sesi SSH/dropbear aktif → {username: [(pid, remote_ip)]}.

    Hanya menghitung PID yang punya koneksi TCP ESTABLISHED (baris ss -tnp),
    dan tiap PID dihitung SEKALI (dedupe) supaya 1 HP = 1 sesi, bukan berkali-
    kali karena PID kebetulan ada di auth-log + ps sekaligus.
    """
    result: dict[str, list[tuple[int, str]]] = {}
    db_map = _read_dropbear_auth_map()
    pid_user_map, ppid_children_map = _read_ps_user_maps()
    ipmap = _read_ipmap()  # baca sekali, dipakai berulang di loop

    def resolve_user(pid_str: str) -> str | None:
        # Prioritas: user VPN non-root > user dari auth-log.
        u = pid_user_map.get(pid_str)
        if u and u not in _SYS_USERS:
            return u
        u2, _ip = db_map.get(pid_str, ("", ""))
        if u2 and u2 not in _SYS_USERS:
            return u2
        for child_u in ppid_children_map.get(pid_str, []):
            if child_u and child_u not in _SYS_USERS:
                return child_u
        return None

    try:
        out = subprocess.check_output(["ss", "-tnp"], text=True, stderr=subprocess.DEVNULL, timeout=5)
        for line in out.splitlines():
            if "ESTAB" not in line:
                continue
            # Sesi login SSH asli = socket yang LOCAL port-nya 90/109 (dropbear
            # menerima dari ws-ssh). Koneksi outbound dari user (tunnel/socks ke
            # Google/Telegram) punya local port lain — JANGAN dihitung sbg sesi.
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr, peer_addr = parts[3], parts[4]
            m_local = re.search(r":(\d+)$", local_addr)
            if not m_local or m_local.group(1) not in ("90", "109"):
                continue
            peer_ip = peer_addr.rsplit(":", 1)[0].strip("[]")
            peer_port = peer_addr.rsplit(":", 1)[-1]
            seen: set[int] = set()
            for pid_str in re.findall(r"pid=(\d+)", line):
                u = resolve_user(pid_str)
                if not u:
                    continue
                pid = int(pid_str)
                if pid in seen:
                    continue
                seen.add(pid)
                # IP asli klien: peer dropbear = 127.0.0.1:<port_ws> → ipmap[<port_ws>]
                real = ipmap.get(peer_port, {}).get("ip")
                use_ip = real if real else peer_ip
                result.setdefault(u, []).append((pid, use_ip))
    except Exception:
        pass

    for sid, node_map in _CLUSTER_ONLINE_CACHE.get("ssh_by_srv", {}).items():
        if not isinstance(node_map, dict):
            continue
        for uname, ip_list in node_map.items():
            existing_ips = {ip for _, ip in result.get(uname, [])}
            for ip in ip_list:
                if ip not in existing_ips:
                    result.setdefault(uname, []).append((0, ip))
    return result

# === FAST NON-BLOCKING LOG READER & MULTI-NODE COLLECTOR ===
_REFRESH_CLUSTER_LOCK = asyncio.Lock()
_REMOTE_MGR_UPLOADED: set[int] = set()

def get_xray_active_ips(window_seconds: int = 120) -> dict[str, set[str]]:
    active: dict[str, set[str]] = {}
    if os.path.exists(XRAY_ACCESS_LOG):
        now = datetime.datetime.now()
        try:
            with open(XRAY_ACCESS_LOG, "rb") as f:
                f.seek(0, os.SEEK_END)
                fsize = f.tell()
                # Jika file log > 15MB, pangkas otomatis agar tidak membebani RAM/Disk
                if fsize > 15 * 1024 * 1024:
                    f.seek(max(0, fsize - 200000), os.SEEK_SET)
                    tail_bytes = f.read()
                    try:
                        with open(XRAY_ACCESS_LOG, "wb") as wf:
                            wf.write(tail_bytes)
                    except Exception:
                        pass
                    lines = tail_bytes.decode("utf-8", errors="ignore").splitlines()[-1500:]
                else:
                    f.seek(max(0, fsize - 150000), os.SEEK_SET)
                    lines = f.read().decode("utf-8", errors="ignore").splitlines()[-1500:]

            for line in lines:
                if "email:" not in line:
                    continue
                m_time = re.match(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", line)
                # FAIL-CLOSED: baris access.log Xray itu multi-baris (timestamp hanya
                # di baris pertama request; baris lanjutan 'accepted' tak ber-timestamp).
                # Kalau baris tak ber-timestamp dan kita tetap menghitung, baris basi
                # ikut dihitung online → Xray 'notifikasi padahal offline'. Jadi wajib
                # ber-timestamp dan dalam window.
                if not m_time:
                    continue
                try:
                    log_dt = datetime.datetime.strptime(m_time.group(1), "%Y/%m/%d %H:%M:%S")
                    if abs((now - log_dt).total_seconds()) > window_seconds:
                        continue
                except Exception:
                    continue
                m_email = re.search(r"email:\s*([a-zA-Z0-9_-]+)", line)
                # IP client di 'accepted tcp:IP:port' / 'accepted udp:IP:port'.
                # 'from 127.0.0.1' selalu internal (haproxy/nginx), jangan dipakai.
                m_ip = re.search(r"accepted\s+tcp:([0-9a-fA-F\.:]+):\d+", line) or \
                       re.search(r"accepted\s+udp:([0-9a-fA-F\.:]+):\d+", line)
                if m_email and m_ip:
                    ip = m_ip.group(1).strip("[]")
                    if ip not in ("127.0.0.1", "::1", "0.0.0.0"):
                        active.setdefault(m_email.group(1), set()).add(ip)
        except Exception:
            pass

    for sid, node_map in _CLUSTER_ONLINE_CACHE.get("xray_by_srv", {}).items():
        if int(sid) == 1 or not isinstance(node_map, dict):
            continue
        for uname, ip_list in node_map.items():
            for ip in ip_list:
                active.setdefault(uname, set()).add(ip)
    return active


def get_xray_conn_counts(window_seconds: int = 120) -> dict[str, int]:
    """Jumlah koneksi nyata per username dari access.log dalam window.

    Xray single-process (tanpa PID per koneksi); jumlah koneksi accepted adalah
    proxy paling dekat untuk "jumlah sesi/PID" yang bisa diverifikasi dari log.
    """
    counts: dict[str, int] = {}
    if not os.path.exists(XRAY_ACCESS_LOG):
        return counts
    now = datetime.datetime.now()
    try:
        with open(XRAY_ACCESS_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(max(0, fsize - 150000), os.SEEK_SET)
            lines = f.read().decode("utf-8", errors="ignore").splitlines()[-1500:]
    except Exception:
        return counts
    for line in lines:
        if "email:" not in line or "accepted" not in line:
            continue
        # FAIL-CLOSED (sama dgn get_xray_active_ips): baris multi-baris tanpa
        # timestamp wajib di-skip agar baris basi tidak terhitung sebagai
        # koneksi aktif → cegah notif Xray padahal offline.
        m_time = re.match(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", line)
        if not m_time:
            continue
        try:
            log_dt = datetime.datetime.strptime(m_time.group(1), "%Y/%m/%d %H:%M:%S")
            if abs((now - log_dt).total_seconds()) > window_seconds:
                continue
        except Exception:
            continue
        m_email = re.search(r"email:\s*([a-zA-Z0-9_-]+)", line)
        if m_email:
            counts[m_email.group(1)] = counts.get(m_email.group(1), 0) + 1
    return counts


def get_haproxy_client_ips(window_seconds: int = 120) -> dict[str, set[str]]:
    """IP HP asli user dari /var/log/nginx/realip.log.

    Jalur: HP → Cloudflare (meneruskan CF-Connecting-IP) → haproxy (TLS offload,
    L4) → nginx (real_ip_header CF-Connecting-IP → $remote_addr = IP asli).
    Format baris realip.log: '<ip> [<time_local>] "GET /path HTTP/1.1" <status> <UA>'
    """
    ips: set[str] = set()
    log_p = "/var/log/nginx/realip.log"
    if not os.path.exists(log_p):
        return {}
    try:
        from datetime import timedelta, timezone
        now_dt = datetime.datetime.now(timezone(timedelta(hours=7)))
        cutoff = now_dt - timedelta(seconds=window_seconds)
        with open(log_p, "rb") as f:
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(max(0, fsize - 300000), os.SEEK_SET)
            lines = f.read().decode("utf-8", errors="ignore").splitlines()[-3000:]
        for ln in lines:
            # '<ip> [08/Nov/2026:22:12:13 +0700] "GET /trojan HTTP/1.1" 404 UA'
            # Ambil request endpoint VPN (/trojan|/vless|/vmess) — status apa pun.
            # WebSocket connect 101 cuma sekali; request berikutnya (health-check,
            # latency probe) tetap nulis log → IP asli user selalu terlihat.
            m_req = re.search(r'"(?:GET|POST|PUT|ACL) ([^ "]+) HTTP', ln)
            if not m_req:
                continue
            # Endpoint VPN = path "/" (ws-ssh). Scanner pakai /robots.txt,
            # /actuator/env, /.env, /.git/config dll.
            path = m_req.group(1)
            if path != "/":
                continue
            # Status 101 = WebSocket handshake sukses (koneksi VPN nyata).
            # Scanner datang dgn 400/499/404 (di-reject). Ini saringan utama.
            if '" 101 ' not in ln:
                continue
            ua = ""
            m_ua = re.search(r'HTTP/\d+\.\d+" \d{3} (.*)$', ln)
            if m_ua:
                ua = m_ua.group(1)
            if re.search(r"(Go-http-client|Infrawatch|SearchBot|scanner|bot|crawler|Spider|curl)", ua, re.I):
                continue
            m_time = re.search(r"\[(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) [+-]\d{4}\]", ln)
            if m_time:
                try:
                    log_dt = datetime.datetime.strptime(
                        f"{m_time.group(1)} {m_time.group(2)} {m_time.group(3)} {m_time.group(4)}:{m_time.group(5)}:{m_time.group(6)}",
                        "%d %b %Y %H:%M:%S"
                    ).replace(tzinfo=timezone(timedelta(hours=7)))
                    if log_dt < cutoff or log_dt > now_dt:
                        continue
                except Exception:
                    pass
            # IP asli = kolom pertama
            m_ip = re.match(r"^([0-9a-fA-F\.:]+)\s+", ln)
            if m_ip:
                ip = m_ip.group(1)
                if ip not in ("127.0.0.1", "::1", "0.0.0.0"):
                    ips.add(ip)
    except Exception:
        pass
    return {"clients": ips}

async def collect_bandwidth_from_server(srv: dict) -> dict[str, int]:
    sid = int(srv["id"])
    if srv.get("is_local", 1) == 1:
        res = await asyncio.to_thread(
            subprocess.run,
            ["/usr/bin/python3", "/usr/local/bin/vpn-bw-manager.py", "collect"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8
        )
        try:
            payload = json.loads(res.stdout.strip().splitlines()[-1])
            _CLUSTER_ONLINE_CACHE["ssh_by_srv"][sid] = payload.get("ssh_online", {})
            _CLUSTER_ONLINE_CACHE["xray_by_srv"][sid] = payload.get("xray_online", {})
            st = payload.get("sys_stats", {})
            st["online"] = True
            _CLUSTER_ONLINE_CACHE["node_stats"][sid] = st
            _CLUSTER_ONLINE_CACHE["ts"] = time.time()
            return payload.get("accounts", {})
        except Exception:
            return {}
    else:
        # Upload vpn-bw-manager.py hanya sekali saat pertama kali konek (hemat waktu SSH)
        upload_prefix = ""
        if sid not in _REMOTE_MGR_UPLOADED and os.path.exists("/usr/local/bin/vpn-bw-manager.py"):
            with open("/usr/local/bin/vpn-bw-manager.py", "rb") as f:
                b64_mgr = base64.b64encode(f.read()).decode("ascii")
            upload_prefix = (
                f"echo '{b64_mgr}' | base64 -d > /usr/local/bin/vpn-bw-manager.py && "
                "chmod +x /usr/local/bin/vpn-bw-manager.py; "
            )

        remote_cmd = upload_prefix + "python3 /usr/local/bin/vpn-bw-manager.py collect"
        ok, out = await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, remote_cmd, 8)
        if not ok or not out:
            _REMOTE_MGR_UPLOADED.discard(sid)
            prev_st = _CLUSTER_ONLINE_CACHE.get("node_stats", {}).get(sid, {})
            prev_st["online"] = False
            prev_st["in_mbps"] = "0.00 Mbps"
            prev_st["out_mbps"] = "0.00 Mbps"
            _CLUSTER_ONLINE_CACHE["node_stats"][sid] = prev_st
            return {}
        try:
            _REMOTE_MGR_UPLOADED.add(sid)
            payload = json.loads(out.strip().splitlines()[-1])
            _CLUSTER_ONLINE_CACHE["ssh_by_srv"][sid] = payload.get("ssh_online", {})
            _CLUSTER_ONLINE_CACHE["xray_by_srv"][sid] = payload.get("xray_online", {})
            st = payload.get("sys_stats", {})
            st["online"] = True
            _CLUSTER_ONLINE_CACHE["node_stats"][sid] = st
            _CLUSTER_ONLINE_CACHE["ts"] = time.time()
            return payload.get("accounts", {})
        except Exception:
            _REMOTE_MGR_UPLOADED.discard(sid)
            return {}

async def refresh_all_servers_live_stats(force: bool = False):
    if _REFRESH_CLUSTER_LOCK.locked():
        return
    now_t = time.time()
    if not force and (now_t - float(_CLUSTER_ONLINE_CACHE.get("ts", 0))) < 10.0 and _CLUSTER_ONLINE_CACHE.get("node_stats"):
        return

    async with _REFRESH_CLUSTER_LOCK:
        servers = await get_servers_with_usage(only_active=False)
        if not servers:
            return

        async def _poll_one(s: dict):
            sid = int(s["id"])
            deltas = await collect_bandwidth_from_server(s)
            if deltas:
                try:
                    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
                        for uname, add_b in deltas.items():
                            if int(add_b or 0) > 0:
                                await db.execute(
                                    "UPDATE accounts SET used_bytes = COALESCE(used_bytes, 0) + ? WHERE username = ? AND COALESCE(server_id, 1) = ?",
                                    (int(add_b), uname, sid)
                                )
                        await db.commit()
                except Exception as e:
                    print(f"[BW DB UPDATE ERROR] {e}")

        await asyncio.gather(*[_poll_one(s) for s in servers if int(s.get("is_active", 1)) == 1 or int(s.get("is_local", 0)) == 1])
        _CLUSTER_ONLINE_CACHE["ts"] = time.time()

# === SMART LOAD-BALANCER ENGINE ===
def rank_servers_by_health(servers: list[dict], sort_list: bool = False) -> list[dict]:
    if not servers:
        return []
    enriched = []
    best_sid = None
    best_score = -1

    for s in servers:
        sc = dict(s)
        sid = int(sc.get("id") or 1)
        nstat = get_cluster_node_stats(sid)
        is_online = bool(nstat.get("online", True))
        used_u = int(sc.get("used_users") or 0)
        max_u = int(sc.get("max_users") or 0)

        if not is_online:
            score = 0
            has_slot = False
        elif max_u > 0 and used_u >= max_u:
            score = 5
            has_slot = False
        else:
            has_slot = True
            cap_ratio = (used_u / max_u) if max_u > 0 else min(1.0, used_u / 80.0)
            ram_ratio = 0.25
            m_ram = re.search(r"\((\d+(?:\.\d+)?)%\)", str(nstat.get("ram") or ""))
            if m_ram:
                try:
                    ram_ratio = float(m_ram.group(1)) / 100.0
                except Exception:
                    pass
            score = max(10, min(100, int(100 - (cap_ratio * 65.0) - (ram_ratio * 25.0))))

        sc["health_score"] = score
        sc["has_slot"] = has_slot
        sc["is_recommended"] = False
        enriched.append(sc)

        if has_slot and score > best_score:
            best_score = score
            best_sid = sid

    for sc in enriched:
        if best_sid is not None and int(sc["id"]) == best_sid:
            sc["is_recommended"] = True

    if sort_list:
        enriched.sort(key=lambda x: (1 if x.get("has_slot") else 0, x.get("health_score", 0)), reverse=True)
    return enriched

# === ENGINE PORTABLE BACKUP & RESTORE (WAL-SAFE, SUPPORT .SH & .DB, AUTO-RESYNC) ===
def _create_clean_sqlite_snapshot_bytes() -> bytes:
    import sqlite3
    snap_path = "/tmp/vpn_bot_snap_clean.db"
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(snap_path + ext):
            try:
                os.remove(snap_path + ext)
            except Exception:
                pass

    src = sqlite3.connect(DB_PATH, timeout=30)
    try:
        src.execute("PRAGMA wal_checkpoint(PASSIVE);")
    except Exception:
        pass
    dst = sqlite3.connect(snap_path, timeout=30)
    src.backup(dst)
    dst.execute("PRAGMA journal_mode=DELETE;")
    dst.commit()
    dst.close()
    src.close()

    with open(snap_path, "rb") as f:
        data = f.read()
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(snap_path + ext):
            try:
                os.remove(snap_path + ext)
            except Exception:
                pass
    return data

def _generate_portable_backup_sh_sync() -> str:
    import sqlite3
    out_sh = "/root/backup-data-vpn.sh"
    db_bytes = _create_clean_sqlite_snapshot_bytes()
    db_b64 = base64.b64encode(db_bytes).decode("ascii")

    env_path = "/root/tgbot-vpn/.env"
    env_b64 = ""
    if os.path.exists(env_path):
        try:
            with open(env_path, "rb") as ef:
                env_b64 = base64.b64encode(ef.read()).decode("ascii")
        except Exception:
            pass

    banner_path = "/root/tgbot-vpn/welcome_banner.jpg"
    banner_b64 = ""
    if os.path.exists(banner_path) and os.path.getsize(banner_path) > 0:
        try:
            with open(banner_path, "rb") as bf:
                banner_b64 = base64.b64encode(bf.read()).decode("ascii")
        except Exception:
            pass

    conn = sqlite3.connect(DB_PATH, timeout=15)
    cur = conn.cursor()
    try:
        u_cnt = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        ssh_cnt = cur.execute("SELECT COUNT(*) FROM accounts WHERE acc_type = 'ssh'").fetchone()[0]
        xray_cnt = cur.execute("SELECT COUNT(*) FROM accounts WHERE acc_type != 'ssh'").fetchone()[0]
        srv_cnt = cur.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    except Exception:
        u_cnt, ssh_cnt, xray_cnt, srv_cnt = 0, 0, 0, 1
    conn.close()

    ts_str = now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")

    lines = [
        "#!/usr/bin/env bash",
        "# ======================================================================",
        "# PORTABLE BACKUP & AUTO-RESTORE DATA VPN BOT",
        f"# Waktu Backup : {ts_str}",
        f"# Statistik    : {u_cnt} Users | {ssh_cnt} SSH | {xray_cnt} Xray | {srv_cnt} Server",
        "# Cara Pakai   : bash /root/backup-data-vpn.sh (atau upload ke Bot)",
        "# ======================================================================",
        "set -e",
        "",
        "mkdir -p /root/tgbot-vpn",
        "systemctl stop tgbot-vpn 2>/dev/null || true",
        "rm -f /root/tgbot-vpn/vpn_bot.db-wal /root/tgbot-vpn/vpn_bot.db-shm",
        "",
        "# === BEGIN_SQLITE_DB_B64 ===",
        f'DB_B64="{db_b64}"',
        "# === END_SQLITE_DB_B64 ===",
        f'ENV_B64="{env_b64}"',
        f'BANNER_B64="{banner_b64}"',
        "",
        'echo "$DB_B64" | base64 -d > /root/tgbot-vpn/vpn_bot.db',
        'if [[ -n "$ENV_B64" && ! -f /root/tgbot-vpn/.env ]]; then',
        '    echo "$ENV_B64" | base64 -d > /root/tgbot-vpn/.env',
        "fi",
        'if [[ -n "$BANNER_B64" ]]; then',
        '    echo "$BANNER_B64" | base64 -d > /root/tgbot-vpn/welcome_banner.jpg',
        "fi",
        "",
        "PY_BIN=\"/root/tgbot-vpn/venv/bin/python\"",
        "if [[ ! -x \"$PY_BIN\" ]]; then PY_BIN=\"python3\"; fi",
        "",
        "\"$PY_BIN\" - << 'EOF_RESTORE_RUNNER'",
        "import asyncio, os, sqlite3, subprocess, sys",
        "sys.path.insert(0, '/root/tgbot-vpn')",
        "conn = sqlite3.connect('/root/tgbot-vpn/vpn_bot.db', timeout=20)",
        "conn.execute('PRAGMA journal_mode=WAL;')",
        "conn.commit()",
        "conn.close()",
        "try:",
        "    from .db import init_db",
        "    from .system_manager import sync_all_accounts_after_restore",
        "    async def _run():",
        "        await init_db()",
        "        await sync_all_accounts_after_restore()",
        "    asyncio.run(_run())",
        "except Exception as e:",
        "    print(f'[RESTORE SYNC INFO] {e}')",
        "EOF_RESTORE_RUNNER",
        "",
        "systemctl restart xray 2>/dev/null || true",
        "systemctl restart tgbot-vpn 2>/dev/null || true",
        f'echo "✅ Restore Data VPN Selesai! ({u_cnt} User, {ssh_cnt} SSH, {xray_cnt} Xray)"',
        ""
    ]
    with open(out_sh, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.chmod(out_sh, 0o755)
    return out_sh

async def generate_portable_backup_sh() -> str:
    return await asyncio.to_thread(_generate_portable_backup_sh_sync)

async def sync_all_accounts_after_restore() -> dict:
    async with aiosqlite.connect(DB_PATH, timeout=20) as db:
        async with db.execute(
            "SELECT username, password_or_uuid, COALESCE(is_locked, 0), COALESCE(server_id, 1), COALESCE(speed_mbps, 0) FROM accounts WHERE acc_type = 'ssh'"
        ) as cur:
            ssh_rows = await cur.fetchall()

    servers = await get_servers_with_usage(only_active=False)
    srv_map = {int(s["id"]): s for s in servers}

    # 1. Buat ulang akun SSH di Server Utama & Node
    ssh_by_srv: dict[int, list] = {}
    for uname, pwd_str, is_locked, sid, spd in ssh_rows:
        ssh_by_srv.setdefault(int(sid or 1), []).append((uname, pwd_str, int(is_locked or 0), int(spd or 0)))

    for sid, acc_list in ssh_by_srv.items():
        srv = srv_map.get(sid)
        is_local = (not srv) or int(srv.get("is_local", 0)) == 1 or sid == 1
        cmds = []
        for uname, pwd_str, is_locked, _ in acc_list:
            safe_u = re.sub(r"[^a-zA-Z0-9_-]", "", str(uname))
            if not safe_u:
                continue
            p_b64 = base64.b64encode(f"{safe_u}:{pwd_str}".encode("utf-8")).decode("ascii")
            cmds.append(
                f"id -u {safe_u} >/dev/null 2>&1 || useradd -M -N -s /bin/false {safe_u}; "
                f"echo '{p_b64}' | base64 -d | chpasswd; "
                + (f"usermod -L {safe_u} 2>/dev/null || true" if is_locked == 1 else f"usermod -U {safe_u} 2>/dev/null || true")
            )
        if not cmds:
            continue
        full_sh = " ; ".join(cmds)
        if is_local:
            await asyncio.to_thread(subprocess.run, ["bash", "-c", full_sh], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            await sync_ssh_bw_rules_on_server(1)
        elif srv and int(srv.get("is_active", 1)) == 1:
            await asyncio.to_thread(_run_remote_ssh_detail_sync, srv, full_sh, 30)
            await sync_ssh_bw_rules_on_server(sid)

    # 2. Sinkronkan ulang seluruh akun Xray (VLESS, VMess, Trojan) ke semua server
    await sync_xray_clients()
    return {"ssh_synced": len(ssh_rows)}

def _extract_sqlite_bytes_from_file(file_path: str) -> tuple[bytes | None, bytes | None, str]:
    with open(file_path, "rb") as f:
        raw = f.read()

    if not raw:
        return None, None, "File kosong (0 bytes)."

    # 1. Jika file yang diupload adalah file SQLite mentah (.db / .sqlite)
    if raw.startswith(b"SQLite format 3\x00"):
        return raw, None, ""

    text = raw.decode("utf-8", errors="ignore")
    banner_bytes = None

    m_ban = re.search(r'BANNER_B64=["\']([A-Za-z0-9+/=\s]+)["\']', text)
    if m_ban:
        b_clean = re.sub(r"\s+", "", m_ban.group(1))
        if b_clean:
            try:
                banner_bytes = base64.b64decode(b_clean)
            except Exception:
                banner_bytes = None

    # 2. Cari variabel DB_B64="..." atau heredoc base64 di dalam backup-data-vpn.sh
    candidates: list[str] = []
    for pat in (
        r'DB_B64=["\']([A-Za-z0-9+/=\s]{60,})["\']',
        r'SQLITE_B64=["\']([A-Za-z0-9+/=\s]{60,})["\']',
        r'BACKUP_DB_B64=["\']([A-Za-z0-9+/=\s]{60,})["\']',
        r'(U1FMaXRlIGZvcm1hdCAz[A-Za-z0-9+/=\s]{60,})',
    ):
        for m in re.finditer(pat, text):
            candidates.append(m.group(1))

    for cand in candidates:
        c_clean = re.sub(r"\s+", "", cand)
        try:
            decoded = base64.b64decode(c_clean)
            if decoded.startswith(b"SQLite format 3\x00"):
                return decoded, banner_bytes, ""
        except Exception:
            continue

    # 3. Fallback jika file .sh menggunakan perintah bash/sqlite3 langsung tanpa variabel DB_B64
    if "sqlite3" in text or "vpn_bot.db" in text or "INSERT" in text:
        return b"__LEGACY_SH__", banner_bytes, ""

    return None, None, "Format file tidak dikenali. Pastikan Anda mengirim file backup-data-vpn.sh atau vpn_bot.db yang valid."

def _apply_sqlite_restore_sync(db_bytes: bytes, uploaded_sh_path: str = "", banner_bytes: bytes | None = None) -> dict:
    import sqlite3
    import shutil

    if banner_bytes:
        try:
            with open("/root/tgbot-vpn/welcome_banner.jpg", "wb") as bf:
                bf.write(banner_bytes)
        except Exception:
            pass

    if db_bytes == b"__LEGACY_SH__" and uploaded_sh_path:
        # Eksekusi bagian restore dari script lama tanpa mematikan proses bot yang sedang berjalan
        with open(uploaded_sh_path, "r", encoding="utf-8", errors="ignore") as sf:
            sh_lines = sf.readlines()
        safe_lines = [
            ln for ln in sh_lines
            if "systemctl" not in ln and "pkill" not in ln and "EOF_RESTORE_RUNNER" not in ln
        ]
        tmp_sh = "/tmp/run_legacy_restore.sh"
        with open(tmp_sh, "w", encoding="utf-8") as wf:
            wf.write("".join(safe_lines))
        for ext in ("-wal", "-shm"):
            if os.path.exists(DB_PATH + ext):
                try:
                    os.remove(DB_PATH + ext)
                except Exception:
                    pass
        subprocess.run(["bash", tmp_sh], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        try:
            os.remove(tmp_sh)
        except Exception:
            pass
    else:
        tmp_restore = "/tmp/vpn_restore_incoming.db"
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(tmp_restore + ext):
                try:
                    os.remove(tmp_restore + ext)
                except Exception:
                    pass
        with open(tmp_restore, "wb") as f:
            f.write(db_bytes)

        # Validasi keutuhan file SQLite sebelum ditimpakan
        chk_conn = sqlite3.connect(tmp_restore, timeout=15)
        res_chk = chk_conn.execute("PRAGMA integrity_check;").fetchone()
        if not res_chk or str(res_chk[0]).lower() != "ok":
            chk_conn.close()
            raise ValueError(f"File database rusak (integrity_check: {res_chk})")

        # Salin secara aman menggunakan SQLite Online Backup API ke DB_PATH (100% aman untuk mode WAL)
        try:
            dst_conn = sqlite3.connect(DB_PATH, timeout=30)
            chk_conn.backup(dst_conn)
            dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            dst_conn.commit()
            dst_conn.close()
            chk_conn.close()
        except Exception:
            chk_conn.close()
            for ext in ("-wal", "-shm"):
                if os.path.exists(DB_PATH + ext):
                    try:
                        os.remove(DB_PATH + ext)
                    except Exception:
                        pass
            shutil.copy2(tmp_restore, DB_PATH)

        for ext in ("", "-wal", "-shm"):
            if os.path.exists(tmp_restore + ext):
                try:
                    os.remove(tmp_restore + ext)
                except Exception:
                    pass

    # Hitung statistik hasil restore
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("UPDATE users SET custom_quota = -1 WHERE custom_quota IS NULL;")
    conn.commit()
    cur = conn.cursor()
    u_cnt = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    ssh_cnt = cur.execute("SELECT COUNT(*) FROM accounts WHERE acc_type = 'ssh'").fetchone()[0]
    xray_cnt = cur.execute("SELECT COUNT(*) FROM accounts WHERE acc_type != 'ssh'").fetchone()[0]
    srv_cnt = cur.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    conn.close()

    return {
        "users": u_cnt,
        "ssh": ssh_cnt,
        "xray": xray_cnt,
        "servers": srv_cnt,
    }

async def restore_from_backup_file(file_path: str) -> dict:
    db_bytes, banner_bytes, err_msg = await asyncio.to_thread(_extract_sqlite_bytes_from_file, file_path)
    if not db_bytes:
        return {"ok": False, "error": err_msg}

    try:
        from .db import init_db
        stats = await asyncio.to_thread(_apply_sqlite_restore_sync, db_bytes, file_path, banner_bytes)
        await init_db()
        await sync_all_accounts_after_restore()
        await generate_portable_backup_sh()
        return {"ok": True, **stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}