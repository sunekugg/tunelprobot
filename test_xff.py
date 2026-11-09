#!/usr/bin/env python3
"""Uji: apakah trustedXForwardedFor mengubah field `from` di access log Xray.

Kirim request WS ke Xray via IP publik VPS (lewat HAProxy 443 -> nginx -> xray)
dengan X-Forwarded-For yang dibawa nginx ($remote_addr = IP klien). Lalu cek
baris terbaru access.log Xray utk lihat field `from`.
"""
import socket
import ssl
import time

HOST = "id-biznett.suneku.web.id"
# Ambil IP publik VPS dulu
import subprocess
vps_ip = subprocess.check_output(["curl", "-s", "ifconfig.me"]).decode().strip()
print(f"VPS public IP: {vps_ip}")

# Kirim WS handshake ke 443 (HAProxy) path /trojan
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
try:
    s = socket.create_connection((vps_ip, 443), timeout=8)
    w = ctx.wrap_socket(s, server_hostname=HOST)
    req = (
        f"GET /trojan HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()
    w.sendall(req)
    time.sleep(1)
    resp = w.recv(4096)
    print(f"HTTP response: {resp[:120]}")
    w.close()
except Exception as e:
    print(f"conn err: {e}")

# Cek access.log terbaru
time.sleep(2)
r = subprocess.run(
    ["ssh", "-o", "StrictHostKeyChecking=no", "root@127.0.0.1", "tail -5 /var/log/xray/access.log"],
    capture_output=True, text=True
)
print("access.log tail:", r.stdout[-500:])
