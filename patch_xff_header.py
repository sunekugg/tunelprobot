#!/usr/bin/env python3
"""Koreksi trustedXForwardedFor: ganti ["127.0.0.1"] -> ["X-Real-IP"].

Dokumentasi Xray: array berisi NAMA HEADER untuk cek kehadiran; jika ada,
Xray pakai nilai pertama X-Forwarded-For sebagai remote IP.
Sumber: https://xtls.github.io/en/config/transports/sockopt.html
"""
import json, shutil, subprocess, sys

XRAY_CFG = "/usr/local/etc/xray/config.json"
with open(XRAY_CFG) as f:
    cfg = json.load(f)

changed = 0
for inb in cfg.get("inbounds", []):
    ss = inb.get("streamSettings", {})
    if ss.get("network") in ("ws", "httpupgrade"):
        sock = ss.setdefault("socketSettings", {})
        if sock.get("trustedXForwardedFor") == ["127.0.0.1"]:
            sock["trustedXForwardedFor"] = ["X-Real-IP"]
            changed += 1

if changed == 0:
    print("Tidak ada perubahan (sudah X-Real-IP atau tak ada field)")
else:
    shutil.copy(XRAY_CFG, XRAY_CFG + ".bak3")
    with open(XRAY_CFG, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Diubah: {changed} inbound")

# Validasi
r = subprocess.run(["xray", "run", "-test", "-c", XRAY_CFG], capture_output=True, text=True)
out = (r.stdout + r.stderr).strip()
print("xray -test:", out[-300:])
if r.returncode != 0:
    print("FAIL, rollback")
    shutil.copy(XRAY_CFG + ".bak3", XRAY_CFG)
    sys.exit(1)
