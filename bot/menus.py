"""Keyboard menu user & admin tunnel-bot (blueprint Bagian 8)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ---- callback data constants ----
C = {
    # user
    "TRIAL_SSH": "trial_ssh",
    "TRIAL_VMESS": "trial_vmess",
    "TRIAL_VLESS": "trial_vless",
    "TRIAL_TROJAN": "trial_trojan",
    "CREATE_SSH": "create_ssh",
    "CREATE_VMESS": "create_vmess",
    "CREATE_VLESS": "create_vless",
    "CREATE_TROJAN": "create_trojan",
    "MY_ACCOUNTS": "my_accounts",
    "DEL_ACCOUNT": "del_account",
    "USAGE": "usage",
    "CHECK_BUG": "check_bug",
    "ADMIN_PANEL": "admin_panel",
    "BACK_USER": "back_user",
    # admin
    "ADM_TOGGLE": "adm_toggle",
    "ADM_BOT_STATUS": "adm_bot_status",
    "ADM_VPS": "adm_vps",
    "ADM_MANAGE_USERS": "adm_users",
    "ADM_MANAGE_ACCOUNTS": "adm_accounts",
    "ADM_DEL_EXPIRED": "adm_del_expired",
    "ADM_CHECK_ONLINE": "adm_online",
    "ADM_UNLOCK": "adm_unlock",
    "ADM_RESET_LIMIT": "adm_reset_limit",
    "ADM_DEL_ALL": "adm_del_all",
    "ADM_SETTINGS": "adm_settings",
    "ADM_INTERVALS": "adm_intervals",
    "ADM_TOPICS": "adm_topics",
    "ADM_JOIN": "adm_join",
    "ADM_WELCOME": "adm_welcome",
    "ADM_BACKUP": "adm_backup",
    "ADM_STATS": "adm_stats",
    "ADM_BROADCAST": "adm_broadcast",
    "ADM_LOGS": "adm_logs",
    "ADM_DOMAIN": "adm_domain",
    "ADM_BANNER": "adm_banner",
}


def user_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🆓 Trial SSH", callback_data=C["TRIAL_SSH"]),
         InlineKeyboardButton("🆓 Trial VMess", callback_data=C["TRIAL_VMESS"])],
        [InlineKeyboardButton("🆓 Trial VLESS", callback_data=C["TRIAL_VLESS"]),
         InlineKeyboardButton("🆓 Trial Trojan", callback_data=C["TRIAL_TROJAN"])],
        [InlineKeyboardButton("🖥 Buat SSH", callback_data=C["CREATE_SSH"]),
         InlineKeyboardButton("🔐 Buat VMess", callback_data=C["CREATE_VMESS"])],
        [InlineKeyboardButton("⚡ Buat VLESS", callback_data=C["CREATE_VLESS"]),
         InlineKeyboardButton("🔒 Buat Trojan", callback_data=C["CREATE_TROJAN"])],
        [InlineKeyboardButton("📋 Akun Saya", callback_data=C["MY_ACCOUNTS"]),
         InlineKeyboardButton("🗑️ Hapus Akun", callback_data=C["DEL_ACCOUNT"])],
        [InlineKeyboardButton("📊 Data Pemakaian & Info Server", callback_data=C["USAGE"])],
        [InlineKeyboardButton("🔍 Cek Status Bug / Host ISP", callback_data=C["CHECK_BUG"])],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("📡 Panel Administrator", callback_data=C["ADMIN_PANEL"])])
    return InlineKeyboardMarkup(rows)


def admin_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎛️ ON/OFF Fitur", callback_data=C["ADM_TOGGLE"]),
         InlineKeyboardButton("🤖 Status Bot (ON/OFF)", callback_data=C["ADM_BOT_STATUS"])],
        [InlineKeyboardButton("🌐 Kelola Server VPS, Speed & Cek Komponen", callback_data=C["ADM_VPS"]),
         InlineKeyboardButton("🧌 Kelola User & ID", callback_data=C["ADM_MANAGE_USERS"])],
        [InlineKeyboardButton("🔑 Kelola Akun, Kuota & Speed", callback_data=C["ADM_MANAGE_ACCOUNTS"]),
         InlineKeyboardButton("🧹 Hapus Akun Expired / Lewat Batas", callback_data=C["ADM_DEL_EXPIRED"])],
        [InlineKeyboardButton("🩻 Cek User Online", callback_data=C["ADM_CHECK_ONLINE"]),
         InlineKeyboardButton("🔓 Unlock Akun Kena Limit", callback_data=C["ADM_UNLOCK"])],
        [InlineKeyboardButton("🔄 Reset Limit Buat Akun (semua user)", callback_data=C["ADM_RESET_LIMIT"]),
         InlineKeyboardButton("🧨 Hapus Semua Akun User", callback_data=C["ADM_DEL_ALL"])],
        [InlineKeyboardButton("🎨 Pengaturan (durasi, kuota, batas)", callback_data=C["ADM_SETTINGS"]),
         InlineKeyboardButton("⏱️ Atur Interval", callback_data=C["ADM_INTERVALS"])],
        [InlineKeyboardButton("📣 Kelola Topic Notifikasi", callback_data=C["ADM_TOPICS"]),
         InlineKeyboardButton("📌 Kelola Wajib Join", callback_data=C["ADM_JOIN"])],
        [InlineKeyboardButton("🖼️ Kelola Welcome", callback_data=C["ADM_WELCOME"]),
         InlineKeyboardButton("💾 Auto Backup & Restore (.sh)", callback_data=C["ADM_BACKUP"])],
        [InlineKeyboardButton("📊 Statistik, SSL & Service", callback_data=C["ADM_STATS"]),
         InlineKeyboardButton("📢 Broadcast", callback_data=C["ADM_BROADCAST"])],
        [InlineKeyboardButton("📜 Log Aktivitas", callback_data=C["ADM_LOGS"]),
         InlineKeyboardButton("🌍 Ganti Domain", callback_data=C["ADM_DOMAIN"])],
        [InlineKeyboardButton("🎨 Pasang Banner HTML", callback_data=C["ADM_BANNER"]),
         InlineKeyboardButton("⬅️ Kembali ke Menu User", callback_data=C["BACK_USER"])],
    ]
    return InlineKeyboardMarkup(rows)


def copy_keyboard() -> InlineKeyboardMarkup:
    """Tombol '📋 Salin' (callback bayangan; diisi logika salin di layer handler)."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("📋 Salin", callback_data="copy_account")]])


def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Join", url="https://t.me/")],
            [InlineKeyboardButton("Cek Ulang", callback_data="join_check")],
        ]
    )
