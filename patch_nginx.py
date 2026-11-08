#!/usr/bin/env python3
p = "/etc/nginx/nginx.conf"
lines = open(p).readlines()
out = []
inserted = False
MARK = "access_log /var/log/nginx/access.log;"
for line in lines:
    if not inserted and MARK in line and line.strip().startswith("access_log"):
        # ganti baris access_log default dgn blok real IP + log realip
        block = (
            "\t# Logging real IP klien asli (Cloudflare aware)\n"
            "\tset_real_ip_from 173.245.48.0/20;\n"
            "\tset_real_ip_from 103.21.244.0/22;\n"
            "\tset_real_ip_from 103.22.200.0/22;\n"
            "\tset_real_ip_from 103.31.4.0/22;\n"
            "\tset_real_ip_from 141.101.64.0/18;\n"
            "\tset_real_ip_from 108.162.192.0/18;\n"
            "\tset_real_ip_from 190.93.240.0/20;\n"
            "\tset_real_ip_from 188.114.96.0/20;\n"
            "\tset_real_ip_from 197.234.240.0/22;\n"
            "\tset_real_ip_from 198.41.128.0/17;\n"
            "\tset_real_ip_from 162.158.0.0/15;\n"
            "\tset_real_ip_from 104.16.0.0/13;\n"
            "\tset_real_ip_from 104.24.0.0/14;\n"
            "\tset_real_ip_from 172.64.0.0/13;\n"
            "\tset_real_ip_from 131.0.72.0/22;\n"
            "\treal_ip_header CF-Connecting-IP;\n"
            "\treal_ip_recursive on;\n"
            "\tlog_format realip '$remote_addr [$time_local] \"$request\" $status $http_user_agent';\n"
            "\taccess_log /var/log/nginx/realip.log realip;\n"
            "\taccess_log /var/log/nginx/access.log;\n"
        )
        out.append(block)
        inserted = True
    else:
        out.append(line)
open(p, "w").writelines(out)
print("patched" if inserted else "NOT inserted")
