#!/usr/bin/env python3
p = "/etc/nginx/nginx.conf"
s = open(p).read()
old = "\tset_real_ip_from 131.0.72.0/22;\n\treal_ip_header CF-Connecting-IP;"
new = "\tset_real_ip_from 131.0.72.0/22;\n\t# haproxy (L4) meneruskan stream dari Cloudflare ke nginx dgn\n\t# $remote_addr=127.0.0.1, tapi header CF-Connecting-IP tetap ada di stream.\n\t# Tanpa ini real_ip tak mengubah $remote_addr dan IP user tak terlihat.\n\tset_real_ip_from 127.0.0.1;\n\treal_ip_header CF-Connecting-IP;"
if old in s:
    open(p, "w").write(s.replace(old, new))
    print("patched: set_real_ip_from 127.0.0.1 ditambah")
else:
    print("NOT found")
