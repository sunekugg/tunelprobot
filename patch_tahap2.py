#!/usr/bin/env python3
"""Tahap 2: Xray menampilkan IP asli.

- Nginx vpn-multiplex.conf: tambah proxy_set_header X-Forwarded-For $remote_addr;
  ke SEMUA location proxy_pass WS/HTTPUpgrade (bukan gRPC).
- Xray config.json: tambah streamSettings.socketSettings.trustedXForwardedFor:
  ["127.0.0.1"] untuk inbound WS & HTTPUpgrade. gRPC dilewati (tak bawa XFF).
- Validasi: xray run -test, nginx -t. Restart nginx & xray.
"""
import json
import shutil
import subprocess
import sys

XRAY_CFG = "/usr/local/etc/xray/config.json"
NGINX_VPN = "/etc/nginx/conf.d/vpn-multiplex.conf"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


# --- 1. Nginx: tambah XFF ke setiap location proxy_pass (WS/HTTPUpgrade), bukan gRPC ---
ng = open(NGINX_VPN).read()
# Tambah X-Forwarded-For ke proxy_pass location saja (skip grpc_pass).
lines = ng.splitlines()
out = []
for line in lines:
    out.append(line)
    # Baris proxy_pass (bukan grpc_pass) — tambahkan XFF jika belum ada
    if "proxy_pass http://127.0.0.1:1000" in line and "X-Forwarded-For" not in line:
        # Tambah di baris yang sama sebelum closing `}`
        new_line = line.replace(
            "proxy_set_header Host $host; }",
            "proxy_set_header Host $host; proxy_set_header X-Forwarded-For $remote_addr; }",
        )
        out[-1] = new_line

# Tambah juga di `location /` (ws-ssh)
new_out = []
for line in out:
    if "proxy_pass http://127.0.0.1:10015" in line and "X-Forwarded-For" not in line:
        new_out.append(line.replace(
            "proxy_set_header Host $host; }",
            "proxy_set_header Host $host; proxy_set_header X-Forwarded-For $remote_addr; }",
        ))
    else:
        new_out.append(line)
open(NGINX_VPN, "w").write("\n".join(new_out) + "\n")
print("NGINX: X-Forwarded-For ditambah ke semua location WS/HTTPUpgrade")

r = sh("nginx -t")
print("nginx -t:", (r.stdout + r.stderr).strip())
if r.returncode != 0:
    sh(f"cp /root/backup-realip-*/etc/nginx/conf.d/vpn-multiplex.conf {NGINX_VPN}")
    print("NGINX FAIL, rollback")
    sys.exit(1)

# --- 2. Xray: tambahkan socketSettings.trustedXForwardedFor ---
with open(XRAY_CFG) as f:
    cfg = json.load(f)

changed = 0
for inbound in cfg.get("inbounds", []):
    ss = inbound.setdefault("streamSettings", {})
    sock = ss.setdefault("socketSettings", {})
    # Hanya tambahkan pada WS & HTTPUpgrade (skip gRPC — tak bawa XFF)
    network = ss.get("network", "")
    if network in ("ws", "httpupgrade"):
        sock["trustedXForwardedFor"] = ["127.0.0.1"]
        changed += 1

if changed == 0:
    print("XRAY: tak ada inbound ws/httpupgrade untuk diubah")
else:
    shutil.copy(XRAY_CFG, XRAY_CFG + ".bak2")
    with open(XRAY_CFG, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"XRAY: {changed} inbound ws/httpupgrade diberi trustedXForwardedFor")

r = sh("xray run -test -c " + XRAY_CFG + " 2>&1")
print("xray -test:", r.stdout.strip() or r.stderr.strip())
if r.returncode != 0:
    print("XRAY -test FAIL, restore dari .bak2")
    shutil.copy(XRAY_CFG + ".bak2", XRAY_CFG)
    sys.exit(1)

# --- 3. Restart nginx lalu xray ---
sh("systemctl reload nginx")
sh("systemctl restart xray")
print("reloaded nginx, restarted xray")
