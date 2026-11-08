#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys
import tempfile

def sync_hot(payload_json: str):
    desired = json.loads(payload_json)
    cfg_path = "/usr/local/etc/xray/config.json" if os.path.exists("/usr/local/etc/xray/config.json") else "/etc/xray/config.json"
    if not os.path.exists(cfg_path):
        return

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    xray_bin = "/usr/local/bin/xray" if os.path.exists("/usr/local/bin/xray") else "/usr/bin/xray"
    api_addr = "127.0.0.1:10085"

    need_restart = False
    if "api" not in cfg or cfg["api"].get("listen") != api_addr:
        cfg["api"] = {
            "tag": "api",
            "listen": api_addr,
            "services": ["HandlerService", "LoggerService", "StatsService"]
        }
        need_restart = True

    if "stats" not in cfg:
        cfg["stats"] = {}
        need_restart = True

    pol = cfg.setdefault("policy", {})
    lv0 = pol.setdefault("levels", {}).setdefault("0", {})
    if not lv0.get("statsUserUplink") or not lv0.get("statsUserDownlink"):
        lv0["statsUserUplink"] = True
        lv0["statsUserDownlink"] = True
        need_restart = True

    ops_remove = []
    ops_add = []

    for inb in cfg.get("inbounds", []):
        proto = inb.get("protocol")
        if proto not in ("trojan", "vless", "vmess"):
            continue

        tag = inb.get("tag")
        if not tag:
            net = inb.get("streamSettings", {}).get("network", "ws")
            tag = f"{proto}-{net}"
            inb["tag"] = tag
            need_restart = True

        old_clients = inb.get("settings", {}).get("clients", [])
        old_map = {c.get("email"): c for c in old_clients if c.get("email")}

        new_clients = desired.get(proto, [])
        for c in new_clients:
            c["level"] = 0
        new_map = {c.get("email"): c for c in new_clients if c.get("email")}

        for email in old_map:
            if email not in new_map:
                ops_remove.append((tag, email))

        added_list = []
        for email, c_obj in new_map.items():
            if email not in old_map or old_map[email].get("id") != c_obj.get("id") or old_map[email].get("password") != c_obj.get("password"):
                if email in old_map:
                    ops_remove.append((tag, email))
                added_list.append(c_obj)

        if added_list:
            inb_copy = json.loads(json.dumps(inb))
            inb_copy.setdefault("settings", {})["clients"] = added_list
            ops_add.append(inb_copy)

        inb.setdefault("settings", {})["clients"] = new_clients

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    is_active = subprocess.run(["systemctl", "is-active", "--quiet", "xray"]).returncode == 0
    has_adu = False
    if os.path.exists(xray_bin):
        res_help = subprocess.run([xray_bin, "help", "api"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        has_adu = "adu" in (res_help.stdout + res_help.stderr)

    if not is_active or need_restart or not has_adu:
        subprocess.run(["systemctl", "restart", "xray"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return

    for tag, email in ops_remove:
        subprocess.run(
            [xray_bin, "api", "rmu", f"-server={api_addr}", f"-tag={tag}", email],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    if ops_add:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump({"inbounds": ops_add}, tf)
            tmp_name = tf.name
        try:
            subprocess.run(
                [xray_bin, "api", "adu", f"-server={api_addr}", tmp_name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
            )
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--b64":
        raw_str = base64.b64decode(sys.argv[2]).decode("utf-8")
        sync_hot(raw_str)
    elif len(sys.argv) > 1:
        sync_hot(sys.argv[1])