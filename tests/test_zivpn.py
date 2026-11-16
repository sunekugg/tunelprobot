"""Self-check protokol ZiVPN (UDP). Tanpa framework.
Jalankan: .venv/bin/python tests/test_zivpn.py
"""
import os
import sys
import json
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_ID", "1")

from bot import system_manager as sm  # noqa: E402
from bot.formatter import format_zivpn_output  # noqa: E402

# --- 1. konstanta ---
assert sm.ZIVPN_BIN == "/usr/local/bin/zivpn"
assert sm.ZIVPN_CONFIG == "/etc/zivpn/config.json"
assert sm.ZIVPN_LISTEN_PORT == 5667

# --- 2. regex password: ZiVPN hanya alfanumerik (tanda '-' DITOLAK) ---
assert sm._ZIVPN_PASS_RE.match("avita28")
assert sm._ZIVPN_PASS_RE.match("sunektrial12345")
assert not sm._ZIVPN_PASS_RE.match("sunek-trial12345"), "tanda '-' harus ditolak"
assert not sm._ZIVPN_PASS_RE.match("ab"), "min 3 karakter"

# --- 3. inline python remote: add/remove idempoten, tak pernah kosong ---
tmpd = tempfile.mkdtemp()
cfg_path = os.path.join(tmpd, "config.json")
sm.ZIVPN_CONFIG = cfg_path
json.dump({"listen": ":5667", "obfs": "zivpn",
           "auth": {"mode": "passwords", "config": ["zivpn"]}}, open(cfg_path, "w"))


def _run(action, pw):
    r = subprocess.run(["bash", "-c", sm._zivpn_remote_py(action, pw)],
                       capture_output=True, text=True)
    return r.returncode == 0


def _conf():
    return json.load(open(cfg_path))["auth"]["config"]


assert _run("add", "sunektrial12345")
assert _conf() == ["zivpn", "sunektrial12345"], _conf()
assert _run("add", "sunektrial12345"), "duplikat harus idempoten"
assert _conf() == ["zivpn", "sunektrial12345"], _conf()
assert _run("add", "avita28")
assert _conf() == ["zivpn", "sunektrial12345", "avita28"], _conf()
assert _run("remove", "sunektrial12345")
assert _conf() == ["zivpn", "avita28"], _conf()
assert _run("remove", "avita28")
assert _conf() == ["zivpn"], "tidak boleh kosong, sisakan default 'zivpn'"
assert not _run("add", "sunek-trial"), "password dgn '-' harus ditolak"

# --- 4. iptables: ZiVPN SEBELUM catch-all udp-custom ---
ip = sm._iptables_script()
i_z = ip.index('-I PREROUTING 1 -i "$IFACE" -p udp --dport 6000:19999 -j REDIRECT --to-ports 5667')
i_all = ip.index('-A PREROUTING -i "$IFACE" -p udp --dport 1:65535 -j REDIRECT')
i_gw = ip.index('-A PREROUTING -i "$IFACE" -p udp --dport 7300 -j ACCEPT')
assert i_z < i_all, "ZiVPN 6000:19999 HARUS di atas catch-all 1:65535 (-I 1)"
assert "-A PREROUTING" not in ip[:i_z], "ZiVPN di-INSERT di posisi teratas"
assert '"$IFACE"' in ip and '\\"' not in ip, "escaping shell script rusak"
assert "-F PREROUTING" not in ip, "jangan flush PREROUTING"
for p in ("5667", "6000:19999"):
    assert p in ip.split("for P in")[1], f"INPUT ACCEPT utk {p} wajib ada"

# --- 5. output box persis sesuai SPEK ---
txt, kb = format_zivpn_output("avita28", "sensei.srver.my.id", "Bogor",
                              "PT Dewa Bisnis Digital", "09 February 2026 11:57")
spec = (
    "<b>Success:</b>\n<pre><code>CREATE AKUN ZIVPN\n"
    "┌────────────────────────┐\n"
    "│ Host        : sensei.srver.my.id\n"
    "│ CITY        : Bogor\n"
    "│ ISP         : PT Dewa Bisnis Digital\n"
    "│ Pass        : avita28\n"
    "│ Expire      : 09 February 2026 11:57\n"
    "└────────────────────────┘</code></pre>"
)
assert txt == spec, f"MISMATCH:\n{txt}\n--- vs ---\n{spec}"
labels = [b.text for r in kb.inline_keyboard for b in r]
assert any("Salin Password" in l for l in labels), labels

# --- 6. tombol menu utama terdaftar ---
from bot.keyboards import main_menu_kb  # noqa: E402
cbs = [b.callback_data for row in main_menu_kb().inline_keyboard for b in row]
assert "create:zivpn:0" in cbs, cbs
assert "create:zivpn:1" in cbs, cbs

# --- 7. handler zivpn terdaftar di router user ---
from bot.handlers_user import router as user_router  # noqa: E402
seen_msg = {getattr(h.callback, "__name__", "?") for h in user_router.message.handlers}
assert "fsm_zivpn_password" in seen_msg, seen_msg

print("OK test_zivpn")
