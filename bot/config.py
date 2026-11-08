import os
import datetime
from dotenv import load_dotenv

load_dotenv()  # cwd .env (dev)
# Lokasi config produksi tunnel-bot (/etc/tunnel-bot/config.env)
_ENV_PATHS = ["/etc/tunnel-bot/config.env", os.path.expanduser("~/.tunnel-bot.env")]
for _p in _ENV_PATHS:
    if os.path.exists(_p):
        load_dotenv(_p, override=False)
        break

BOT_TOKEN = os.getenv("BOT_TOKEN", os.getenv("TOKEN", "")).strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or 0)
# Daftar admin tambahan (dipisah koma) selain ADMIN_ID master
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DOMAIN = os.getenv("DOMAIN", "localhost").strip()
# Prefix otomatis username akun (misal "sunek-"); kosongkan untuk nonaktif
USERNAME_PREFIX = os.getenv("PREFIKS_USERNAME", "sunek-").strip()

def apply_prefix(name: str) -> str:
    """Tambahkan USERNAME_PREFIX bila belum ada. Contoh: user -> sunek-user."""
    if not USERNAME_PREFIX or name.startswith(USERNAME_PREFIX):
        return name
    return USERNAME_PREFIX + name
DB_PATH = os.getenv("DB_PATH", "/root/tgbot-vpn/vpn_bot.db")
XRAY_CONFIG = os.getenv("XRAY_CONFIG", "/usr/local/etc/xray/config.json")
XRAY_ACCESS_LOG = os.getenv("XRAY_ACCESS_LOG", "/var/log/xray/access.log")
AUTO_BACKUP_SH = "/root/backup-data-vpn.sh"

WIB_TZ = datetime.timezone(datetime.timedelta(hours=7))

def now_wib() -> datetime.datetime:
    return datetime.datetime.now(WIB_TZ).replace(tzinfo=None)

RESERVED_USERNAMES = {
    "root", "admin", "ubuntu", "debian", "daemon", "bin", "sys", "sync",
    "games", "man", "lp", "mail", "news", "uucp", "proxy", "www-data",
    "backup", "list", "irc", "gnats", "nobody", "systemd-network",
    "systemd-resolve", "syslog", "messagebus", "uuidd", "sshd", "haproxy",
    "dropbear", "squid", "xray", "postgres", "mysql", "test"
}