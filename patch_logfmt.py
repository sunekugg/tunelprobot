#!/usr/bin/env python3
"""Ubah log_format realip supaya mencatat $remote_addr DAN $http_x_real_ip.
Untuk membuktikan Nginx menerima IP klien asli via PROXY protocol.
"""
import shutil, subprocess, sys

p = "/etc/nginx/nginx.conf"
s = open(p).read()
old = "log_format realip '$remote_addr [$time_local] \"$request\" $status $http_user_agent';"
new = "log_format realip '$remote_addr [$time_local] \"$request\" $status realip=$http_x_real_ip ua=$http_user_agent';"
if old in s:
    shutil.copy(p, p + ".bak4")
    open(p, "w").write(s.replace(old, new))
    print("log_format realip diperbarui (menampilkan $http_x_real_ip)")
elif "realip=$http_x_real_ip" in s:
    print("sudah ada")
else:
    print("POLA TIDAK DITEMUKAN")
    sys.exit(1)

r = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
print("nginx -t:", (r.stdout + r.stderr).strip())
if r.returncode != 0:
    print("FAIL, rollback")
    shutil.copy(p + ".bak4", p)
    sys.exit(1)
subprocess.run(["systemctl", "reload", "nginx"])
print("nginx reloaded")
