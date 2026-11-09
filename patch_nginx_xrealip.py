#!/usr/bin/env python3
"""Tambah X-Real-IP ke semua location proxy_pass WS/HTTPUpgrade di nginx vpn config.
Xray trustedXForwardedFor=['X-Real-IP'] cek kehadiran header ini; IP dibaca dari XFF.
"""
import re, shutil, subprocess, sys

CFG = "/etc/nginx/conf.d/vpn-multiplex.conf"
s = open(CFG).read()

# Untuk tiap location dgn proxy_pass & X-Forwarded-For tapi belum X-Real-IP
def add_xreal(m):
    block = m.group(0)
    if "proxy_pass http://" in block and "X-Forwarded-For" in block and "X-Real-IP" not in block:
        return block.replace(
            "proxy_set_header X-Forwarded-For $remote_addr; }",
            "proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Real-IP $remote_addr; }"
        )
    return block

new = re.sub(r"location [^{]+\{[^}]*proxy_pass[^}]*\}", add_xreal, s)
open(CFG + ".new", "w").write(new)

# validasi
r = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
print("nginx -t:", (r.stdout + r.stderr).strip())
if r.returncode != 0:
    print("FAIL, batal")
    sys.exit(1)
shutil.move(CFG + ".new", CFG)
print("OK, X-Real-IP ditambahkan")
