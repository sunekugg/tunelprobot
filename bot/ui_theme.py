from __future__ import annotations
import sqlite3
import time
from .config import DB_PATH

_THEME_CACHE = {"val": "tree", "ts": 0.0}

THEME_LABELS = {
    "tree": "🌲 Cyber Tree-Box & Progress Bar",
    "compact": "🔽 Minimalist Expandable Card",
    "classic": "📋 Classic Clean",
}

def set_ui_theme_cache(theme_code: str):
    code = (theme_code or "tree").strip().lower()
    if code not in THEME_LABELS:
        code = "tree"
    _THEME_CACHE["val"] = code
    _THEME_CACHE["ts"] = time.time()

def get_ui_theme_sync() -> str:
    now_t = time.time()
    if (now_t - float(_THEME_CACHE.get("ts", 0.0))) < 10.0 and _THEME_CACHE.get("val"):
        return str(_THEME_CACHE["val"])
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        row = conn.execute("SELECT value FROM settings WHERE key = 'ui_theme'").fetchone()
        conn.close()
        val = (row[0] if row and row[0] else "tree").strip().lower()
        if val not in THEME_LABELS:
            val = "tree"
        _THEME_CACHE["val"] = val
        _THEME_CACHE["ts"] = now_t
        return val
    except Exception:
        return str(_THEME_CACHE.get("val") or "tree")

def make_capacity_bar(used: int, max_val: int, width: int = 10, theme: str = "") -> str:
    t = theme or get_ui_theme_sync()
    u = max(0, int(used or 0))
    m = int(max_val or 0)

    if t == "compact":
        ch_fill, ch_empty = "■", "□"
    elif t == "classic":
        ch_fill, ch_empty = "█", "░"
    else:
        ch_fill, ch_empty = "▰", "▱"

    if m <= 0:
        bar_str = ch_fill * width
        return f"[{bar_str}] ♾"

    ratio = min(1.0, max(0.0, u / float(m)))
    pct = int(round(ratio * 100))
    filled = int(round(ratio * width))
    filled = min(width, max(0, filled))
    bar_str = (ch_fill * filled) + (ch_empty * (width - filled))
    return f"[{bar_str}] {pct}%"