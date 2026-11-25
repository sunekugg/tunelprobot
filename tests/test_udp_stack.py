"""Self-check Fase UDP: unit/conf/iptables + dispatch callback panel SlowDNS.
Tanpa framework. Jalankan: python3 tests/test_udp_stack.py
"""
import os
import sys
import re
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_ID", "1")

from bot import system_manager as sm  # noqa: E402
from _sd_pin import pin_binary  # noqa: E402

# --- 1. unit slowdns: wire VayDNS native, upstream = mux Multi-Protokol, :5300, ns- ---
# VayDNS >=0.2.7: -dnstt-compat memaksa record-type=txt dan mengabaikan
# -record-type, jadi default TIDAK memakai compat (lihat references vaydns).
with pin_binary(sm, "vaydns"):
    u = sm._slowdns_unit("contoh.com")
    sm.SLOWDNS_COMPAT = True
    try:
        compat_u = sm._slowdns_unit("contoh.com")
    finally:
        sm.SLOWDNS_COMPAT = False
assert "-dnstt-compat" in compat_u and "-record-type" not in compat_u, compat_u
assert ":5300" in u, u
assert "-upstream 127.0.0.1:10017" in u, u
assert "-mtu 1232" in u, u
assert "-dnstt-compat" not in u, u
assert f"-clientid-size {sm.SLOWDNS_CLIENTID_SIZE}" in u, u
assert f"-record-type {sm.SLOWDNS_RECORD_TYPE}" in u, u
assert "-domain ns-contoh.com" in u, u
# mux Multi-Protokol wajib ada & slowdns bergantung padanya
mu = sm._slowdns_mux_unit()
assert "slowdns_mux.py" in mu, mu
assert "ExecStart=/usr/bin/python3 /usr/local/bin/slowdns_mux.py" in mu, mu
assert "vpn-slowdns-mux.service" in u and "After=vpn-slowdns-mux.service" in u, u

# --- 2. udpgw :7300 publik (bukan loopback) ---
g = sm._UDPGW_UNIT
assert "--listen-addr 0.0.0.0:7300" in g, g
assert sm.UDPGW_PORT == 7300

# --- 3. udp-custom exclude 53,5300 (jangan rebut port slowdns) ---
c = sm._UDP_CUSTOM_UNIT
assert "-exclude 53,5300" in c, c
assert '"listen": ":36712"' in sm._UDP_CUSTOM_CONF, sm._UDP_CUSTOM_CONF

# --- 4. iptables: 7300 ACCEPT harus SEBELUM catch-all 1:65535 (blok -A) ---
ip = sm._iptables_script()
i_gw = ip.index('-A PREROUTING -i "$IFACE" -p udp --dport 7300 -j ACCEPT')
i_all = ip.index('-A PREROUTING -i "$IFACE" -p udp --dport 1:65535 -j REDIRECT')
assert i_gw < i_all, "ACCEPT 7300 harus sebelum catch-all 1:65535"
assert "-F PREROUTING" not in ip, "jangan flush PREROUTING"
assert "iptables -C INPUT -p udp --dport $P" in ip
assert "iptables-save" in ip
# 53/5300 di-ACCEPT sebelum redirect 53 -> 5300
assert ip.index('--dport 5300 -j ACCEPT') < ip.index('--dport 53 -j REDIRECT')

# --- 5. NS helper ---
assert sm.get_slowdns_ns_from_host("contoh.com") == "ns-contoh.com"
assert sm.get_slowdns_ns_from_host("ns-contoh.com") == "ns-contoh.com"
assert sm.get_slowdns_ns_from_host("") == "-"

# --- 6. formatter: pub-key, NS, UDPGW 7300, lane SlowDNS ---
from bot.formatter import format_ssh_output  # noqa: E402
txt, kb = format_ssh_output("contoh.com", "sunek-x", "p4ss", 2, "2026-12-01")
assert "UDPGW (badvpn)  : 7300" in txt, txt
assert "7100-7600" not in txt, "port lama masih muncul"
assert "NS SlowDNS      : ns-contoh.com" in txt, txt
assert "PUBLIC KEY" in txt
assert "ns-contoh.com:5300@sunek-x:p4ss" in txt, txt

# --- 7. callback panel terdaftar di router nyata ---
from bot.handlers_admin import router  # noqa: E402
seen = {getattr(h.callback, "__name__", "?") for h in router.callback_query.handlers}
assert "cb_adm_slowdns_udp" in seen, seen
assert "cb_sd_act" in seen, seen

# callback_data yang dipakai tombol harus cocok prefix handler
from bot.keyboards import admin_menu_kb  # noqa: E402
cbs = [b.callback_data for row in admin_menu_kb().inline_keyboard for b in row]
assert "adm_slowdns_udp" in cbs, cbs

print("OK test_udp_stack")
