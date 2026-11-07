"""Konfigurasi tunnel-bot.

Baca/tulis /etc/tunnel-bot/config.env. Pada mode development (tanpa file di
/etc), fallback ke `.env` di root repo agar bisa dijalankan/diuji lokal tanpa
root. Seluruh nilai sensitif (token, ADMIN_IDS, key) TIDAK di-commit ke repo.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Runtime root: VPS → /etc/tunnel-bot; dev (non-root) → folder repo.
if os.geteuid() == 0:
    CONFIG_DIR = Path("/etc/tunnel-bot")
    DATA_DIR = CONFIG_DIR
    ENV_PATH = CONFIG_DIR / "config.env"
    DB_PATH = CONFIG_DIR / "data.db"
else:
    CONFIG_DIR = REPO_ROOT
    DATA_DIR = REPO_ROOT
    ENV_PATH = REPO_ROOT / ".env"
    DB_PATH = REPO_ROOT / "data.db"

LOG_PATH = Path("/var/log/tunnel-bot.log")

DEFAULT_CONFIG = {
    "TOKEN": "",
    "ADMIN_IDS": "",
    "DOMAIN": "",
    "GROUP_ID": "",
    "TOPIC_BUAT_AKUN": "",
    "TOPIC_EXPIRED": "",
    "TOPIC_LOGIN": "",
    "CHANNEL_WAJIB_JOIN": "",
    "REPO_URL": "",
    "PREFIKS_USERNAME": "sunek-",
    "TZ": "Asia/Jakarta",
}


class Config:
    """Pembungkus dict config.env yang menyimpan default + nilai env asli."""

    def __init__(self, env_path: Path = ENV_PATH) -> None:
        self.path = env_path
        self._data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        """Baca file env (jika ada), tanpa menimpa yang sudah ada."""
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            self._data[key.strip()] = val.strip()

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._data.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = str(self._data.get(key, "")).strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off", ""):
            return False
        return default

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def save(self) -> None:
        """Tulis ke file env. Buat direktori bila perlu; jangan pernah simpan
        nilai kosong untuk key sensitif tanpa peringatan."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k}={v}" for k, v in self._data.items()]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def admin_ids(self) -> list[int]:
        raw = self.get("ADMIN_IDS", "")
        out: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out


# Singleton yang dipakai modul lain.
config = Config()
