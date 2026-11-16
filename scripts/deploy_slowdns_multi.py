#!/usr/bin/env python3
"""Deploy SlowDNS Multi-Protokol ke VPS tunnel-bot (idempotent)."""
import os
import shlex
import shutil
import subprocess
import sys

MUX_SRC = "/opt/tunnel-bot/scripts/slowdns_mux.py"
MUX_DST = "/usr/local/bin/slowdns_mux.py"
BACKUP = "/root/backup-slowdns-multi"

def sh(cmd, check=True):
    print(f"$ {cmd}")
    r = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0 and check:
        print("STDERR:", r.stderr.strip(), file=sys.stderr)
    return r.returncode, (r.stdout or "")

def main():
    if not os.path.exists(MUX_SRC):
        print("FATAL: mux source belum ada di repo (git pull dulu).", file=sys.stderr)
        sys.exit(1)
    os.makedirs(BACKUP, exist_ok=True)
    sh(f"cp -a /etc/systemd/system/slowdns.service {BACKUP}/slowdns.service.bak 2>/dev/null; true", check=False)

    # 1. Sebar mux
    shutil.copy(MUX_SRC, MUX_DST)
    os.chmod(MUX_DST, 0o755)
    print(f"mux -> {MUX_DST}")

    # 2. Tulis unit mux
    mux_unit = """[Unit]
Description=SlowDNS Multi-Protokol Multiplexer (SSH + Xray WS)
After=network.target
Before=slowdns.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/slowdns_mux.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
    with open("/etc/systemd/system/vpn-slowdns-mux.service", "w") as f:
        f.write(mux_unit)

    # 3. Regenerate slowdns.service via bot (satu sumber kode)
    regen = (
        "from bot import system_manager as sm\n"
        "dom = sm.DOMAIN\n"
        "if not dom:\n"
        "    for line in open('/etc/tunnel-bot/config.env'):\n"
        "        if line.startswith('DOMAIN='):\n"
        "            dom = line.split('=', 1)[1].strip()\n"
        "sm._write('/etc/systemd/system/slowdns.service', sm._slowdns_unit(dom))\n"
        "print('slowdns ns-domain:', dom)\n"
    )
    sh(f"cat > /tmp/_regen_sd.py <<'PYEOF'\n{regen}\nPYEOF")
    rc, _ = sh("cd /opt/tunnel-bot && /opt/tunnel-bot/.venv/bin/python /tmp/_regen_sd.py")
    if rc != 0:
        print("FATAL: gagal regenerate slowdns.service", file=sys.stderr)
        sys.exit(1)
    sh("grep -E 'ExecStart|After|Wants' /etc/systemd/system/slowdns.service")

    # 4. Reload + start mux dulu, lalu restart slowdns
    sh("systemctl daemon-reload")
    sh("systemctl enable vpn-slowdns-mux.service")
    rc, _ = sh("systemctl restart vpn-slowdns-mux.service")
    if rc != 0:
        print("FATAL: mux gagal start", file=sys.stderr); sys.exit(1)
    rc, _ = sh("systemctl restart slowdns.service")
    if rc != 0:
        print("FATAL: slowdns gagal restart — ROLLBACK", file=sys.stderr)
        sh(f"cp {BACKUP}/slowdns.service.bak /etc/systemd/system/slowdns.service; systemctl daemon-reload; systemctl restart slowdns", check=False)
        sys.exit(1)

    print("=== status ===")
    for s in ("vpn-slowdns-mux", "slowdns", "xray", "dropbear", "nginx", "tunnel-bot"):
        _, st = sh(f"systemctl is-active {s}.service", check=False)
        print(f"{s}: {st.strip()}")
    sh("ss -lntp | grep -E ':(10017|10015|5300)' ; ss -ulnp | grep ':5300'")

if __name__ == "__main__":
    main()
