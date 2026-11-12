from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🆓 Trial SSH", callback_data="trial_ssh"), InlineKeyboardButton(text="🆓 Trial Trojan", callback_data="trial_trojan")],
        [InlineKeyboardButton(text="🆓 Trial VLESS", callback_data="trial_vless"), InlineKeyboardButton(text="🆓 Trial VMess", callback_data="trial_vmess")],
        [InlineKeyboardButton(text="🔑 Buat SSH", callback_data="create_ssh"), InlineKeyboardButton(text="🔐 Buat Trojan", callback_data="create_xray:trojan")],
        [InlineKeyboardButton(text="⚡ Buat VLESS", callback_data="create_xray:vless"), InlineKeyboardButton(text="🚀 Buat VMess", callback_data="create_xray:vmess")],
        [InlineKeyboardButton(text="📋 Akun Saya", callback_data="my_accounts"), InlineKeyboardButton(text="🗑 Hapus Akun", callback_data="delete_menu")],
        [InlineKeyboardButton(text="ℹ️ Info Server & Aturan", callback_data="server_info")]
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="👑 Panel Administrator", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_menu_kb(bot_enabled: bool = True) -> InlineKeyboardMarkup:
    bot_status_lbl = "🤖 Status Bot: 🟢 ON (Klik Matikan)" if bot_enabled else "🤖 Status Bot: 🔴 OFF (Klik Hidupkan)"
    rows = [
        [InlineKeyboardButton(text=bot_status_lbl, callback_data="adm_toggle_bot")],
        [InlineKeyboardButton(text="🌐 Kelola Server VPS, Speed & Cek Komponen", callback_data="adm_servers")],
        [InlineKeyboardButton(text="👤 Kelola User & ID", callback_data="adm_users"), InlineKeyboardButton(text="🔐 Kelola Akun, Kuota & Speed", callback_data="adm_accounts")],
        [InlineKeyboardButton(text="🧹 Hapus Akun Expired / Lewat Batas Hari", callback_data="adm_purge_menu")],
        [InlineKeyboardButton(text="👥 Cek User Online", callback_data="adm_online"), InlineKeyboardButton(text="🔓 Unlock Akun Kena Limit", callback_data="adm_unlock_all")],
        [InlineKeyboardButton(text="🔄 Reset Limit Buat Akun (Semua)", callback_data="adm_reset_all_limits"), InlineKeyboardButton(text="🧨 Hapus Semua Akun User", callback_data="adm_del_all_ask")],
        [InlineKeyboardButton(text="🎨 Pengaturan, Durasi, Kuota & Speed", callback_data="adm_settings"), InlineKeyboardButton(text="📌 Kelola Wajib Join", callback_data="adm_fjoin")],
        [InlineKeyboardButton(text="💾 Auto Backup & Restore (.sh)", callback_data="adm_backup"), InlineKeyboardButton(text="📊 Statistik, SSL & Service", callback_data="adm_stats")],
        [InlineKeyboardButton(text="🌐 SlowDNS & UDP-Custom & UDPGW", callback_data="adm_slowdns_udp")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="adm_broadcast"), InlineKeyboardButton(text="📜 Log Aktivitas", callback_data="adm_logs")],
        [InlineKeyboardButton(text="⬅️ Kembali ke Menu User", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cancel_kb(back_cb: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Batal", callback_data=f"cancel_fsm:{back_cb}")]])

def limit_ip_picker_kb(prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 IP", callback_data=f"{prefix}:1"), InlineKeyboardButton(text="2 IP", callback_data=f"{prefix}:2"), InlineKeyboardButton(text="3 IP", callback_data=f"{prefix}:3")],
        [InlineKeyboardButton(text="5 IP", callback_data=f"{prefix}:5"), InlineKeyboardButton(text="10 IP", callback_data=f"{prefix}:10"), InlineKeyboardButton(text="♾ Unlimited", callback_data=f"{prefix}:0")],
        [InlineKeyboardButton(text="✏️ Input Manual (1-100)", callback_data=f"{prefix}:manual")],
        [InlineKeyboardButton(text="⬅️ Kembali", callback_data=back_cb)]
    ])

def server_picker_kb(servers: list[dict], action_prefix: str, back_cb: str = "menu_main") -> InlineKeyboardMarkup:
    rows = []
    for s in servers:
        used = s.get("used_users", 0)
        max_u = s.get("max_users", 0)
        is_full = (max_u > 0 and used >= max_u)
        cap_str = f"{used}/♾" if max_u == 0 else f"{used}/{max_u} User"
        ico = "🔴 [PENUH]" if is_full else "🟢"
        label = f"{ico} {s['name']} ({s['domain']}) [{cap_str}]"
        cb = "srv_full_alert" if is_full else f"pick_srv:{action_prefix}:{s['id']}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    rows.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# === WRAPPER MENU UTAMA & ADMIN ===
_BASE_MAIN_MENU_KB_FN = main_menu_kb

def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = _BASE_MAIN_MENU_KB_FN(is_admin)
    new_rows = []
    has_wc = False
    has_chk = False
    for row in kb.inline_keyboard:
        r_copy = []
        for btn in row:
            cb = getattr(btn, "callback_data", "") or ""
            if cb == "server_info":
                btn = InlineKeyboardButton(text="📊 Data Pemakaian & Info Server", callback_data="server_info")
            if cb == "user_wildcard_list":
                has_wc = True
            if cb == "bug_checker_start":
                has_chk = True
            r_copy.append(btn)
        new_rows.append(r_copy)

    extra_rows = []
    extra_rows.append([
        InlineKeyboardButton(text="🎁 Referral / Undang Teman", callback_data="ref_menu")
    ])
    if not has_wc:
        extra_rows.append([
            InlineKeyboardButton(text="🌐 List Wildcard", callback_data="user_wildcard_list"),
            InlineKeyboardButton(text="🛠 Inject Config Luar", callback_data="ext_bug_start")
        ])
    if not has_chk:
        extra_rows.append([
            InlineKeyboardButton(text="🔍 Cek Status Bug / Host ISP", callback_data="bug_checker_start")
        ])

    if extra_rows:
        if len(new_rows) >= 1:
            new_rows = new_rows[:-1] + extra_rows + [new_rows[-1]]
        else:
            new_rows.extend(extra_rows)
    return InlineKeyboardMarkup(inline_keyboard=new_rows)

if "admin_menu_kb" in globals():
    _BASE_ADMIN_MENU_KB_FN = admin_menu_kb

    def admin_menu_kb(*args, **kwargs) -> InlineKeyboardMarkup:
        kb = _BASE_ADMIN_MENU_KB_FN(*args, **kwargs)
        new_rows = [list(r) for r in kb.inline_keyboard]
        existing = {getattr(b, "callback_data", "") for r in new_rows for b in r}
        if "adm_feat_toggles" not in existing:
            new_rows.insert(0, [InlineKeyboardButton(
                text="🎛 ON/OFF Fitur (Mandiri / Cek Bug / Auto-Heal)",
                callback_data="adm_feat_toggles"
            )])
        return InlineKeyboardMarkup(inline_keyboard=new_rows)

# === OVERRIDE SELECT SERVER KEYBOARD DENGAN SMART LOAD-BALANCER ===
def select_server_kb(servers: list[dict], back_cb: str = "menu_main") -> InlineKeyboardMarkup:
    import sqlite3
    from .config import DB_PATH
    from .system_manager import rank_servers_by_health

    lb_enabled = True
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        row = conn.execute("SELECT value FROM settings WHERE key = 'feat_smart_lb'").fetchone()
        conn.close()
        if row and str(row[0]) == "0":
            lb_enabled = False
    except Exception:
        pass

    ranked = rank_servers_by_health(servers, sort_list=lb_enabled) if lb_enabled else servers
    rows = []

    best_srv = next((s for s in ranked if s.get("is_recommended")), None) if lb_enabled else None
    if lb_enabled and best_srv and len(ranked) >= 2:
        rows.append([InlineKeyboardButton(
            text=f"⚡ Otomatis Server Terbaik ({best_srv['name']} • {best_srv.get('health_score', 100)}%)",
            callback_data=f"srv_pick:{best_srv['id']}"
        )])

    for s in ranked:
        sid = s["id"]
        name = s["name"]
        used = int(s.get("used_users") or 0)
        mx = int(s.get("max_users") or 0)
        cap_lbl = f"{used}/♾" if mx == 0 else f"{used}/{mx}"
        is_full = (mx > 0 and used >= mx)

        if is_full:
            btn_txt = f"🔴 {name} ({cap_lbl} - PENUH)"
        elif lb_enabled and s.get("is_recommended"):
            btn_txt = f"⭐ {name} ({cap_lbl} • Rekomendasi {s.get('health_score', 100)}%)"
        elif lb_enabled and "health_score" in s:
            btn_txt = f"🖥 {name} ({cap_lbl} • Skor {s['health_score']}%)"
        else:
            btn_txt = f"🖥 {name} ({cap_lbl} Akun)"

        rows.append([InlineKeyboardButton(text=btn_txt, callback_data=f"srv_pick:{sid}")])

    rows.append([InlineKeyboardButton(text="⬅️ Kembali", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)