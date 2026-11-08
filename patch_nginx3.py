#!/usr/bin/env python3
p = "/etc/nginx/nginx.conf"
s = open(p).read()
# log_format realip: catat SEMUA request (tak hanya 101), tanpa membedakan path.
# Endpoint VPN (/trojan /vless /vmess dsb) akan terus ter-update tiap request
# klien, sehingga IP asli user tetap tampak walau koneksi WS masih hidup.
old = "\tlog_format realip '$remote_addr [$time_local] \"$request\" $status $http_user_agent';\n\taccess_log /var/log/nginx/realip.log realip;"
new = "\tlog_format realip '$remote_addr [$time_local] \"$request\" $status $http_user_agent';\n\taccess_log /var/log/nginx/realip.log realip;\n\taccess_log /var/log/nginx/realip_all.log realip;"
if old in s:
    # Sudah ada log realip; cukup pastikan ada juga realip_all untuk semua request
    if "realip_all.log" not in s:
        open(p, "w").write(s.replace(old, new))
        print("patched: realip_all.log ditambah")
    else:
        print("realip_all.log sudah ada")
else:
    print("NOT found: blok realip tak ditemukan")
