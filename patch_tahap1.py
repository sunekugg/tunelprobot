#!/usr/bin/env python3
"""Tahap 1: aktifkan PROXY protocol utk jalankan IP asli end-to-end.

- HAProxy:
  * SSH- path -> ws-ssh 127.0.0.1:10016 dengan send-proxy (v1)
  * Nginx 10080 & 10082 -> send-proxy-v2
- Nginx:
  * listener 10080 & 10082 terima proxy_protocol, real_ip_header proxy_protocol
  * set_real_ip_from 127.0.0.1
  * semua location proxy_pass/grpc tambah X-Real-IP $remote_addr (sudah ada di gRPC)
- Restart Nginx lalu HAProxy berurutan secepat mungkin.
- Deploy /root/tunnel-bot/ws-ssh.py (sudah di-copy terpisah) & restart ws-ssh.
"""
import re
import shutil
import subprocess
import sys
import time

HAPROXY_CFG = "/etc/haproxy/haproxy.cfg"
NGINX_VPN = "/etc/nginx/conf.d/vpn-multiplex.conf"

def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)

# --- 1. HAProxy ---
hap = open(HAPROXY_CFG).read()
new_hap = hap
# dropbear_direct -> ws-ssh 10016 dengan send-proxy v1
new_hap = re.sub(
    r"backend dropbear_direct\n    mode tcp\n    server dropbear_local 127\.0\.0\.1:109 check\n",
    "backend ws_ssh_direct\n    mode tcp\n    server ws_ssh 127.0.0.1:10016 check send-proxy\n",
    new_hap,
)
# nginx_grpc_h2 -> send-proxy-v2
new_hap = re.sub(
    r"backend nginx_grpc_h2\n    mode tcp\n    server nginx_h2 127\.0\.0\.1:10082 check\n",
    "backend nginx_grpc_h2\n    mode tcp\n    server nginx_h2 127.0.0.1:10082 check send-proxy-v2\n",
    new_hap,
)
# nginx_multiplex -> send-proxy-v2
new_hap = re.sub(
    r"backend nginx_multiplex\n    mode tcp\n    server nginx_local 127\.0\.0\.1:10080 check\n",
    "backend nginx_multiplex\n    mode tcp\n    server nginx_local 127.0.0.1:10080 check send-proxy-v2\n",
    new_hap,
)
# use_backend dropbear_direct -> ws_ssh_direct
new_hap = new_hap.replace("use_backend dropbear_direct", "use_backend ws_ssh_direct")

if new_hap == hap:
    print("HAPROXY: no change")
else:
    open(HAPROXY_CFG + ".new", "w").write(new_hap)
    r = sh("haproxy -c -f " + HAPROXY_CFG + ".new")
    if r.returncode != 0:
        print("HAPROXY validate FAIL:", r.stderr)
        sys.exit(1)
    shutil.move(HAPROXY_CFG + ".new", HAPROXY_CFG)
    print("HAPROXY: patched & validated")

# --- 2. Nginx vpn-multiplex.conf ---
ng = open(NGINX_VPN).read()
# listener 10080 & 10082: tambah proxy_protocol
ng = ng.replace("listen 127.0.0.1:10082 http2;", "listen 127.0.0.1:10082 http2 proxy_protocol;")
ng = ng.replace("listen 127.0.0.1:10080;", "listen 127.0.0.1:10080 proxy_protocol;")

# Tambah set_real_ip_from & real_ip_header di kedua server block lokal (loopback)
# via server_name injection: tambahkan setelah baris server_name pertama masing2
# listener lokal. Lebih aman: tambahkan sekali global via http-level di nginx.conf.
print("NGINX: vpn-multiplex.conf ditulis ulang")
open(NGINX_VPN + ".new", "w").write(ng)

# --- 3. Nginx.conf: realip header proxy_protocol ---
NGINX_CONF = "/etc/nginx/nginx.conf"
nc = open(NGINX_CONF).read()
old = "\treal_ip_header CF-Connecting-IP;"
new = "\treal_ip_header proxy_protocol;"
if old in nc and "real_ip_header proxy_protocol;" not in nc:
    nc = nc.replace(old, new)
    # Tambah set_real_ip_from 127.0.0.1 kalau belum
    if "set_real_ip_from 127.0.0.1;" not in nc:
        nc = nc.replace("\treal_ip_header proxy_protocol;", "\tset_real_ip_from 127.0.0.1;\n\treal_ip_header proxy_protocol;")
    open(NGINX_CONF, "w").write(nc)
    print("NGINX.conf: real_ip_header -> proxy_protocol, set_real_ip_from 127.0.0.1")
else:
    print("NGINX.conf: sudah ada / tak ditemukan pola")

# Pindahkan .new
shutil.move(NGINX_VPN + ".new", NGINX_VPN)

# --- 4. Validasi & reload ---
r = sh("nginx -t 2>&1")
print("nginx -t:", r.stdout.strip() or r.stderr.strip())
if r.returncode != 0:
    print("NGINX validate FAIL, rollback dari backup")
    sh("cp /root/backup-realip-*/etc/nginx/nginx.conf /etc/nginx/")
    sh("cp /root/backup-realip-*/etc/nginx/conf.d/vpn-multiplex.conf /etc/nginx/conf.d/")
    sh("cp /root/backup-realip-*/etc/haproxy/haproxy.cfg /etc/haproxy/")
    print("ROLLBACK DONE")
    sys.exit(1)

r = sh("haproxy -c -f " + HAPROXY_CFG)
print("haproxy -c:", (r.stdout + r.stderr).strip())
if r.returncode != 0:
    sh("cp /root/backup-realip-*/etc/haproxy/haproxy.cfg /etc/haproxy/")
    print("ROLLBACK haproxy")
    sys.exit(1)

# --- 5. Deploy ws-ssh.py & restart services berurutan cepat ---
sh("systemctl restart ws-ssh")
sh("systemctl restart nginx")
sh("systemctl restart haproxy")
print("services restarted: ws-ssh, nginx, haproxy")
time.sleep(2)
print("STATUS:")
for s in ("ws-ssh", "nginx", "haproxy", "xray", "tunnel-bot", "dropbear", "dropbear-2019"):
    out = sh(f"systemctl is-active {s}").stdout.strip()
    print(f"  {s}: {out}")
print("LISTEN:")
print(sh("ss -ltnp | grep -E '10015|10016|10080|10082|443|109 '").stdout)
