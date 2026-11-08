#!/usr/bin/env python3
import base64
import datetime
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time

XRAY_CONFIG = "/usr/local/etc/xray/config.json"
XRAY_ACCESS_LOG = "/var/log/xray/access.log"
STATE_FILE = "/var/lib/vpn-bw-state.json"
RULE_HASH_FILE = "/var/lib/vpn-bw-rule.hash"

def run_cmd(cmd: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode, (res.stdout or res.stderr or "").strip()
    except Exception as e:
        return 1, str(e)

def get_default_iface() -> str:
    code, out = run_cmd(["ip", "route", "show", "default"], timeout=3)
    if code == 0 and out:
        m = re.search(r"\bdev\s+(\S+)", out)
        if m:
            return m.group(1)
    return "eth0"

def format_iec_bytes(num_bytes: float) -> str:
    b = max(0.0, float(num_bytes or 0.0))
    if b < 1024**3:
        return f"{b / (1024**2):.2f} MiB"
    if b < 1024**4:
        return f"{b / (1024**3):.2f} GiB"
    return f"{b / (1024**4):.2f} TiB"

def read_net_dev_totals() -> tuple[int, int]:
    def_ifc = get_default_iface()
    rx_def, tx_def = 0, 0
    rx_all, tx_all = 0, 0
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()[2:]
        for nl in lines:
            if ":" not in nl:
                continue
            ifc, rest = nl.split(":", 1)
            ifc = ifc.strip()
            if ifc == "lo" or ifc.startswith(("docker", "veth", "br-", "virbr")):
                continue
            cols = rest.split()
            r_v, t_v = int(cols[0]), int(cols[8])
            rx_all += r_v
            tx_all += t_v
            if ifc == def_ifc:
                rx_def, tx_def = r_v, t_v
    except Exception:
        pass
    if rx_def > 0 or tx_def > 0:
        return rx_def, tx_def
    return rx_all, tx_all

def get_vnstat_day_month(state: dict, cur_rx: int, cur_tx: int) -> tuple[str, str]:
    now_dt = datetime.datetime.now()
    day_key = now_dt.strftime("%Y-%m-%d")
    mth_key = now_dt.strftime("%Y-%m")
    total_now = cur_rx + cur_tx

    bw_track = state.setdefault("bw_track", {})
    if "last_total" not in bw_track:
        bw_track["last_total"] = total_now
        bw_track["day_key"] = day_key
        bw_track["day_bytes"] = 0
        bw_track["mth_key"] = mth_key
        bw_track["mth_bytes"] = 0

    last_total = int(bw_track.get("last_total", total_now))
    delta = (total_now - last_total) if total_now >= last_total else total_now
    bw_track["last_total"] = total_now

    if bw_track.get("day_key") != day_key:
        bw_track["day_key"] = day_key
        bw_track["day_bytes"] = max(0, delta)
    else:
        bw_track["day_bytes"] = int(bw_track.get("day_bytes", 0)) + max(0, delta)

    if bw_track.get("mth_key") != mth_key:
        bw_track["mth_key"] = mth_key
        bw_track["mth_bytes"] = max(0, delta)
    else:
        bw_track["mth_bytes"] = int(bw_track.get("mth_bytes", 0)) + max(0, delta)

    vn_day_b = 0
    vn_mth_b = 0

    if shutil.which("vnstat"):
        code, out = run_cmd(["vnstat", "--json"], timeout=3)
        if code == 0 and out:
            try:
                vdata = json.loads(out)
                ifaces = vdata.get("interfaces", [])
                if ifaces:
                    def_ifc = get_default_iface()
                    chosen = next((i for i in ifaces if i.get("name") == def_ifc or i.get("id") == def_ifc), None)
                    if not chosen:
                        chosen = max(
                            ifaces,
                            key=lambda x: int(x.get("traffic", {}).get("total", {}).get("rx", 0)) + int(x.get("traffic", {}).get("total", {}).get("tx", 0))
                        )
                    tr = chosen.get("traffic", {})
                    days = tr.get("day") or tr.get("days") or []
                    months = tr.get("month") or tr.get("months") or []
                    v_ver = str(vdata.get("vnstatversion", "2")).split(".")[0]
                    mult = 1024 if v_ver == "1" else 1
                    if days:
                        d_last = days[-1]
                        vn_day_b = (int(d_last.get("rx", 0)) + int(d_last.get("tx", 0))) * mult
                    if months:
                        m_last = months[-1]
                        vn_mth_b = (int(m_last.get("rx", 0)) + int(m_last.get("tx", 0))) * mult
            except Exception:
                pass

    final_day = max(vn_day_b, int(bw_track.get("day_bytes", 0)))
    final_mth = max(vn_mth_b, int(bw_track.get("mth_bytes", 0)), final_day)
    return format_iec_bytes(final_day), format_iec_bytes(final_mth)

def get_local_xray_active_ips(window_seconds: int = 120) -> dict[str, list[str]]:
    active: dict[str, set[str]] = {}
    if not os.path.exists(XRAY_ACCESS_LOG):
        return {}
    now = datetime.datetime.now()
    try:
        with open(XRAY_ACCESS_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(max(0, fsize - 180000), os.SEEK_SET)
            lines = f.read().decode("utf-8", errors="ignore").splitlines()[-1500:]
        for line in lines:
            if "email:" not in line:
                continue
            m_time = re.match(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", line)
            if m_time:
                try:
                    log_dt = datetime.datetime.strptime(m_time.group(1), "%Y/%m/%d %H:%M:%S")
                    if abs((now - log_dt).total_seconds()) > window_seconds:
                        continue
                except Exception:
                    pass
            m_email = re.search(r"email:\s*([a-zA-Z0-9_-]+)", line)
            m_ip = re.search(r"from\s+(?:tcp:)?([0-9a-fA-F\.:]+):\d+", line)
            if m_email and m_ip:
                ip = m_ip.group(1).strip("[]")
                if ip not in ("127.0.0.1", "::1", "0.0.0.0"):
                    active.setdefault(m_email.group(1), set()).add(ip)
    except Exception:
        pass
    return {k: sorted(list(v)) for k, v in active.items()}

def _get_dropbear_pid_user_map() -> dict[str, tuple[str, str]]:
    pid_map: dict[str, tuple[str, str]] = {}
    for log_p in ("/var/log/auth.log", "/var/log/syslog"):
        if not os.path.exists(log_p):
            continue
        try:
            with open(log_p, "rb") as f:
                f.seek(0, os.SEEK_END)
                fsize = f.tell()
                f.seek(max(0, fsize - 120000), os.SEEK_SET)
                lines = f.read().decode("utf-8", errors="ignore").splitlines()[-1000:]
            for ln in lines:
                if "dropbear[" not in ln:
                    continue
                m = re.search(r"dropbear\[(\d+)\]:\s*Password auth succeeded for '([^']+)' from ([0-9a-fA-F\.:]+):", ln)
                if m:
                    pid_map[m.group(1)] = (m.group(2), m.group(3).strip("[]"))
        except Exception:
            pass
    return pid_map

def get_local_ssh_active_ips() -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    db_map = _get_dropbear_pid_user_map()
    code, out = run_cmd(["ss", "-tnp"], timeout=3)
    if code != 0 or not out:
        return {}
    SystemUsers = {"root", "sshd", "dropbear", "nobody", "daemon", "www-data"}

    # Ambil seluruh tabel proses cukup dengan 1x panggilan ps (sangat ringan & cepat)
    pid_user_map: dict[str, str] = {}
    ppid_children_map: dict[str, list[str]] = {}
    _, ps_out = run_cmd(["ps", "-eo", "pid=,ppid=,user=,args="], timeout=3)
    for pline in ps_out.splitlines():
        parts = pline.strip().split(None, 3)
        if len(parts) < 3:
            continue
        p_id, pp_id, p_usr = parts[0], parts[1], parts[2]
        p_args = parts[3] if len(parts) > 3 else ""
        resolved_u = ""
        if p_usr not in SystemUsers:
            resolved_u = p_usr
        else:
            m_sshd = re.search(r"sshd:\s*([a-zA-Z0-9_-]+)", p_args)
            if m_sshd and m_sshd.group(1) not in SystemUsers:
                resolved_u = m_sshd.group(1)
        if resolved_u:
            pid_user_map[p_id] = resolved_u
            ppid_children_map.setdefault(pp_id, []).append(resolved_u)

    for line in out.splitlines():
        if "ESTAB" not in line or ("sshd" not in line and "dropbear" not in line):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        remote_ip = parts[4].rsplit(":", 1)[0].strip("[]")
        for pid_str in re.findall(r"pid=(\d+)", line):
            if pid_str in db_map:
                u_db, ip_db = db_map[pid_str]
                if u_db and u_db not in SystemUsers:
                    result.setdefault(u_db, set()).add(ip_db if remote_ip in ("127.0.0.1", "::1") else remote_ip)
            if pid_str in pid_user_map:
                result.setdefault(pid_user_map[pid_str], set()).add(remote_ip)
            for child_u in ppid_children_map.get(pid_str, []):
                result.setdefault(child_u, set()).add(remote_ip)
    return {k: sorted(list(v)) for k, v in result.items()}

def apply_server_speed(mbps: int):
    iface = get_default_iface()
    run_cmd(["tc", "qdisc", "del", "dev", iface, "root"])
    try:
        if os.path.exists(RULE_HASH_FILE):
            os.remove(RULE_HASH_FILE)
    except Exception:
        pass
    if mbps <= 0:
        print(f"Server speed limit pada {iface} dinonaktifkan (Unlimited).")
        return
    run_cmd(["tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "10"])
    run_cmd(["tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:10", "htb", "rate", f"{mbps}mbit", "ceil", f"{mbps}mbit"])
    print(f"Server speed limit pada {iface} diset ke {mbps} Mbps.")

def sync_bw_rules(users_list: list[dict], force: bool = False):
    xray_ips_map = get_local_xray_active_ips(window_seconds=180)
    hash_payload = json.dumps({"u": users_list, "x": xray_ips_map}, sort_keys=True)
    cur_hash = hashlib.md5(hash_payload.encode("utf-8")).hexdigest()

    if not force and os.path.exists(RULE_HASH_FILE):
        try:
            with open(RULE_HASH_FILE, "r", encoding="utf-8") as hf:
                if hf.read().strip() == cur_hash:
                    return
        except Exception:
            pass

    iface = get_default_iface()
    run_cmd(["iptables", "-t", "mangle", "-N", "VPN_BW_OUT"])
    code, _ = run_cmd(["iptables", "-t", "mangle", "-C", "OUTPUT", "-j", "VPN_BW_OUT"])
    if code != 0:
        run_cmd(["iptables", "-t", "mangle", "-I", "OUTPUT", "1", "-j", "VPN_BW_OUT"])

    run_cmd(["iptables", "-t", "mangle", "-N", "VPN_BW_IN"])
    run_cmd(["iptables", "-t", "mangle", "-F", "VPN_BW_IN"])
    code_in, _ = run_cmd(["iptables", "-t", "mangle", "-C", "PREROUTING", "-j", "VPN_BW_IN"])
    if code_in != 0:
        run_cmd(["iptables", "-t", "mangle", "-I", "PREROUTING", "1", "-j", "VPN_BW_IN"])

    _, existing_out = run_cmd(["iptables", "-t", "mangle", "-S", "VPN_BW_OUT"])
    existing_uids = set(re.findall(r"--uid-owner\s+(\d+)", existing_out))

    has_speed_limit = any(int(u.get("mbps") or 0) > 0 for u in users_list)
    tc_lines = []
    if has_speed_limit:
        run_cmd(["tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "999"])
        tc_lines.append(f"class replace dev {iface} parent 1: classid 1:999 htb rate 1000mbit ceil 1000mbit")

    mark_id = 100
    for item in users_list:
        uname = str(item.get("u") or "").strip()
        mbps = int(item.get("mbps") or 0)
        acc_type = str(item.get("type") or "ssh").lower()
        if not uname:
            continue

        if acc_type == "ssh":
            try:
                uid = pwd.getpwnam(uname).pw_uid
            except KeyError:
                continue
            mark_id += 1
            if mark_id > 900:
                break
            if str(uid) not in existing_uids:
                run_cmd(["iptables", "-t", "mangle", "-A", "VPN_BW_OUT", "-m", "owner", "--uid-owner", str(uid), "-j", "MARK", "--set-mark", str(mark_id)])
            if mbps > 0:
                tc_lines.append(f"class replace dev {iface} parent 1: classid 1:{mark_id} htb rate {mbps}mbit ceil {mbps}mbit")
                tc_lines.append(f"filter replace dev {iface} parent 1:0 protocol ip prio 1 handle {mark_id} fw flowid 1:{mark_id}")
        else:
            if mbps > 0:
                active_ips = xray_ips_map.get(uname, [])
                if not active_ips:
                    continue
                mark_id += 1
                if mark_id > 900:
                    break
                tc_lines.append(f"class replace dev {iface} parent 1: classid 1:{mark_id} htb rate {mbps}mbit ceil {mbps}mbit")
                tc_lines.append(f"filter replace dev {iface} parent 1:0 protocol ip prio 1 handle {mark_id} fw flowid 1:{mark_id}")
                for cip in active_ips[:6]:
                    if ":" not in cip:
                        run_cmd(["iptables", "-t", "mangle", "-A", "VPN_BW_IN", "-s", cip, "-j", "MARK", "--set-mark", str(mark_id)])

    # Eksekusi seluruh rule tc sekaligus dengan 1x panggilan tc -batch
    if tc_lines:
        try:
            proc = subprocess.Popen(["tc", "-batch", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            proc.communicate(input="\n".join(tc_lines) + "\n", timeout=5)
        except Exception:
            pass

    try:
        os.makedirs(os.path.dirname(RULE_HASH_FILE), exist_ok=True)
        with open(RULE_HASH_FILE, "w", encoding="utf-8") as hf:
            hf.write(cur_hash)
    except Exception:
        pass

def collect_usage() -> dict:
    rx_start, tx_start = read_net_dev_totals()
    t_start = time.time()

    deltas: dict[str, int] = {}
    state = {"ssh_prev": {}}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    ssh_prev = state.get("ssh_prev", {})
    new_ssh_prev = {}

    code, out = run_cmd(["iptables", "-t", "mangle", "-L", "VPN_BW_OUT", "-v", "-n", "-x"], timeout=3)
    if code == 0 and out:
        for line in out.splitlines():
            m = re.search(r"^\s*\d+\s+(\d+)\s+.*owner UID match (\d+)", line)
            if m:
                b_cnt = int(m.group(1))
                uid = int(m.group(2))
                try:
                    uname = pwd.getpwuid(uid).pw_name
                except KeyError:
                    continue
                prev_val = int(ssh_prev.get(uname, 0))
                diff = (b_cnt - prev_val) if b_cnt >= prev_val else b_cnt
                if diff > 0:
                    deltas[uname] = deltas.get(uname, 0) + diff
                new_ssh_prev[uname] = b_cnt

    state["ssh_prev"] = new_ssh_prev

    xray_bin = "/usr/local/bin/xray" if os.path.exists("/usr/local/bin/xray") else "/usr/bin/xray"
    if os.path.exists(xray_bin):
        code_x, out_x = run_cmd([xray_bin, "api", "statsquery", "--server=127.0.0.1:10085", "-reset=true"], timeout=3)
        if code_x == 0 and out_x:
            try:
                data_x = json.loads(out_x)
                for st in data_x.get("stat", []):
                    name = st.get("name", "")
                    val = int(st.get("value", 0))
                    m_u = re.match(r"^user>>>([^>]+)>>>traffic>>>(uplink|downlink)$", name)
                    if m_u and val > 0:
                        u_email = m_u.group(1)
                        deltas[u_email] = deltas.get(u_email, 0) + val
            except Exception:
                pass

    ssh_online = get_local_ssh_active_ips()
    xray_online = get_local_xray_active_ips(window_seconds=120)

    # Hitung Live Traffic Rate tanpa tidur panjang (Zero-Sleep jika ada sampel <45s)
    prev_rate_info = state.get("rate_sample", {})
    dt_prev = t_start - float(prev_rate_info.get("ts", 0)) if prev_rate_info else 999.0

    if 0.8 <= dt_prev <= 45.0:
        rx_end, tx_end = read_net_dev_totals()
        t_end = time.time()
        dt_use = max(0.5, t_end - float(prev_rate_info["ts"]))
        p_rx = int(prev_rate_info.get("rx", rx_end))
        p_tx = int(prev_rate_info.get("tx", tx_end))
        in_mbps = round(((rx_end - p_rx) * 8.0) / (dt_use * 1_000_000.0), 2) if rx_end >= p_rx else 0.0
        out_mbps = round(((tx_end - p_tx) * 8.0) / (dt_use * 1_000_000.0), 2) if tx_end >= p_tx else 0.0
    else:
        elapsed = time.time() - t_start
        if elapsed < 0.2:
            time.sleep(0.2 - elapsed)
        rx_end, tx_end = read_net_dev_totals()
        t_end = time.time()
        dt_use = max(0.1, t_end - t_start)
        in_mbps = round(((rx_end - rx_start) * 8.0) / (dt_use * 1_000_000.0), 2) if rx_end >= rx_start else 0.0
        out_mbps = round(((tx_end - tx_start) * 8.0) / (dt_use * 1_000_000.0), 2) if tx_end >= tx_start else 0.0

    state["rate_sample"] = {"rx": rx_end, "tx": tx_end, "ts": t_end}
    vn_day, vn_mth = get_vnstat_day_month(state, rx_end, tx_end)

    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass

    sys_stats = {
        "in_mbps": f"{in_mbps:.2f} Mbps",
        "out_mbps": f"{out_mbps:.2f} Mbps",
        "vnstat_day": vn_day,
        "vnstat_mth": vn_mth,
        "rx_gb": round(rx_end / (1024**3), 2),
        "tx_gb": round(tx_end / (1024**3), 2),
    }
    try:
        with open("/proc/meminfo", "r") as f:
            mem_raw = f.read()
        mt = int(re.search(r"MemTotal:\s+(\d+)", mem_raw).group(1)) // 1024
        ma = int(re.search(r"MemAvailable:\s+(\d+)", mem_raw).group(1)) // 1024
        mu = mt - ma
        mp = round((mu / mt) * 100, 1) if mt > 0 else 0
        sys_stats["ram"] = f"{mu} MB / {mt} MB ({mp}%)"
    except Exception:
        sys_stats["ram"] = "-"

    try:
        du = shutil.disk_usage("/")
        dp = round((du.used / du.total) * 100, 1) if du.total > 0 else 0
        sys_stats["disk"] = f"{du.used // (1024**3)} GB / {du.total // (1024**3)} GB ({dp}%)"
    except Exception:
        sys_stats["disk"] = "-"

    return {
        "accounts": deltas,
        "ssh_online": ssh_online,
        "xray_online": xray_online,
        "sys_stats": sys_stats
    }

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"accounts": {}}))
        return
    cmd = sys.argv[1]
    if cmd == "server_speed" and len(sys.argv) >= 3:
        apply_server_speed(int(sys.argv[2]))
    elif cmd in ("sync_ssh_b64", "sync_bw_b64") and len(sys.argv) >= 3:
        raw = base64.b64decode(sys.argv[2]).decode("utf-8")
        sync_bw_rules(json.loads(raw))
    elif cmd == "collect":
        print(json.dumps(collect_usage()))

if __name__ == "__main__":
    main()