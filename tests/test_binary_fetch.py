"""Fetch binary harus atomik: menulis ke path binary yang SEDANG JALAN = ETXTBSY.

Regresi nyata: `wget -qO /usr/local/bin/vaydns-server` gagal "Text file busy"
saat service aktif, jadi upgrade binary dilaporkan gagal walau unduhan benar.
"""
import os, sys, subprocess
sys.path.insert(0, "/root/tunelprobot")
os.environ.setdefault("BOT_TOKEN", "x"); os.environ.setdefault("ADMIN_ID", "1")
from bot import system_manager as sm
import inspect

assert "os.replace(tmp, dst)" in inspect.getsource(sm._fetch_bin_opts), "fetch tidak atomik"

name = "_etxtbsy_probe"
dst = f"/usr/local/bin/{name}"
SRC_BIN = "/root/.hermes/cache/scratch/sdp/slowdns-server"  # ELF statis, tak butuh lib

assert open(SRC_BIN, "rb").read(4) == b"\x7fELF"
subprocess.run(["cp", "-f", "/bin/sleep", dst], check=True)
os.chmod(dst, 0o755)
proc = subprocess.Popen([dst, "30"])          # ELF ter-mmap PROT_EXEC -> terkunci
try:
    try:
        with open(dst, "wb") as f:
            f.write(b"clobber")
        raise AssertionError("harus ETXTBSY, tapi tulis langsung berhasil")
    except OSError as e:
        assert e.errno == 26, e
        print("ETXTBSY terkonfirmasi:", e.strerror)

    real = sm._sh
    # helper menjalankan `wget -qO <tmp> <url>`; ganti dgn unduhan nyata ke <tmp>
    # cmd = `wget -qO <tmp> <url>` -> ambil <tmp> saja sebagai target cp
    sm._sh = lambda cmd, timeout=120: real(
        "cp -f " + SRC_BIN + " " + cmd.split("wget -qO ", 1)[1].split()[0], timeout=timeout)
    try:
        ok = sm._fetch_bin_opts(name, ["http://unused"], force=True)
    finally:
        sm._sh = real
    assert ok, "fetch atomik gagal"
    assert open(dst, "rb").read(4) == b"\x7fELF", "isi bukan ELF"
    assert os.access(dst, os.X_OK), "tidak executable"
    # proses lama masih hidup (inode lama), binary baru sudah di tempat
    assert proc.poll() is None, "proses lama ikut mati"
    print("fetch atomik: OK (proses lama tetap jalan, binary baru terpasang)")
finally:
    proc.kill(); proc.wait()
    for f in os.listdir("/usr/local/bin"):
        if f == name or f.startswith(name + ".new."):
            os.remove("/usr/local/bin/" + f)
    print("cleanup: OK")
