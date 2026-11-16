#!/usr/bin/env python3
"""SlowDNS Multi-Protokol Multiplexer (tunnel-bot).

Satu mux untuk seluruh jalur SlowDNS (1 NS domain, 1 Public Key). dnstt/vaydns-server
mendekripsi paket DNS lalu mengirim stream TCP mentah ke sini; mux memutuskan tujuan
dari byte-stream pertama:

    "SSH-"                     -> Dropbear :90                    (SSH langsung)
    HTTP "GET /vmess"          -> Xray inbound WS vmess :10007
    HTTP "GET /vless"          -> Xray inbound WS vless :10004
    HTTP "GET /trojan"         -> Xray inbound WS trojan :10001
    HTTP lain (WS umum)        -> balas 101 Switching -> Dropbear :90
    0x16 0x03 (TLS ClientHello)-> nginx TLS terminator :10445    (SSH SSL / Xray TLS)
    biner lain / klien diam    -> Dropbear :90                    (SSH bicara lebih dulu)

Catatan arsitektur: Xray WS di VPS ini = trojan 10001 / vless 10004 / vmess 10007
(semua network=ws). Port diambil dari tabel di bawah, JANGAN di-hardcode di logika.
Urutan pemeriksaan penting: HTTP prefix HARUS sebelum uji biner, karena
"GET / HTTP/1.1\\r\\n" punya byte non-printable di posisi ke-16 (\\r).
"""
import asyncio
import logging

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 10017

DROPBEAR_HOST = "127.0.0.1"
DROPBEAR_PORT = 90

# Xray inbound WebSocket per protokol (dari /usr/local/etc/xray/config.json)
XRAY_WS = {
    "vmess": 10007,
    "vless": 10004,
    "trojan": 10001,
}

# TLS terminator (nginx) utk SSH SSL / Xray di atas TLS — jalur PROXY v2.
TLS_BACKEND_HOST = "127.0.0.1"
TLS_BACKEND_PORT = 10445

# Klien SSH menunggu banner server (tidak kirim apa pun lebih dulu). Tanpa batas
# ini handshake SSH via SlowDNS deadlock.
SSH_SILENCE_TIMEOUT = 1.5
HTTP_READ_TIMEOUT = 5.0

HTTP_METHODS = (b"GET ", b"POST ", b"PUT ", b"HEAD ", b"OPTIONS ",
                b"CONNECT ", b"PATCH ", b"DELETE ", b"TRACE ")

log = logging.getLogger("slowdns-mux")


def _looks_like_tls(initial: bytes) -> bool:
    """TLS ClientHello: 0x16 0x03 + record length (byte 3-4) wajar utk ukuran data.
    VMess/VLESS/Trojan TCP mentah juga diawali byte acak/biner, tapi rekaman TLS
    punya length 5-16384 yang mendekati jumlah byte nyata -> bedakan dari situ."""
    if initial[:1] != b"\x16" or initial[1:2] not in (b"\x03", b"\x02", b"\x00"):
        return False
    if len(initial) < 5:
        return False
    rec_len = (initial[3] << 8) | initial[4]
    return 5 <= rec_len <= 16384 and rec_len <= max(len(initial), 5) + 8


def _route_from_bytes(initial: bytes) -> str:
    """Kembalikan label rute dari stream awal. Dipanggil SETELAH header cukup terbaca."""
    for m in HTTP_METHODS:
        if initial.startswith(m):
            lower = initial.lower()
            for proto, _port in XRAY_WS.items():
                # cocokkan path /<proto> di request line ATAU header path
                if (b" /" + proto.encode() + b" " in initial[:512]
                        or b" /" + proto.encode() + b"?" in initial[:512]
                        or b" /" + proto.encode() + b"\r\n" in initial[:512]
                        or b"path=/" + proto.encode() in initial
                        or b" /" + proto.encode() + b"?" in lower[:512]):
                    return f"xray-{proto}"
            return "dropbear-101"  # HTTP WS umum
    if initial.startswith(b"SSH-"):
        return "dropbear-ssh"
    if _looks_like_tls(initial):
        return "tls"  # TLS ClientHello (SSH SSL / Xray TLS)
    # VMess AEAD: 16 byte pertama acak (bukan magic byte). Klien SSH diam 1.5s ->
    # dropbear; payload biner yang bicara lebih dulu = VMess/Trojan-TCP mentah.
    head = initial[:16]
    if any(b < 0x09 or (0x0e <= b <= 0x1f) for b in head):
        return "xray-tcp"
    return "dropbear-ssh"


async def _target_for(label: str) -> tuple[str, int]:
    if label == "tls":
        return TLS_BACKEND_HOST, TLS_BACKEND_PORT
    if label.startswith("xray-"):
        proto = label.split("-", 1)[1]
        if proto in XRAY_WS:
            return "127.0.0.1", XRAY_WS[proto]
        # VPS ini tak punya inbound Xray TCP-mentah (cuma ws/grpc/upgrade); payload
        # biner yang bukan TLS tak bisa diarahkan ke backend WS (protokol beda) dan
        # MENGACAUKAN sesi SSH bila diteruskan ke dropbear. Drop koneksi.
        return "", 0
    return DROPBEAR_HOST, DROPBEAR_PORT


def _probe_len(label: str) -> int:
    """Berapa byte minimal utk memutuskan rute (sebelum diputuskan diam = SSH)."""
    if label == "dropbear-ssh":
        return 0
    return 4096


async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await r.read(65536)
            if not chunk:
                break
            w.write(chunk)
            await w.drain()
    except (ConnectionError, asyncio.TimeoutError, OSError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def _bridge(cr: asyncio.StreamReader, cw: asyncio.StreamWriter,
                  tr: asyncio.StreamReader, tw: asyncio.StreamWriter,
                  first: bytes) -> None:
    """Kirim byte awal (kecuali kalau kita sudah membalas sendiri) lalu pipe dua arah:
    cr->tw (klien ke backend) dan tr->cw (backend ke klien)."""
    if first:
        tw.write(first)
        await tw.drain()
    await asyncio.gather(_pipe(tr, cw), _pipe(cr, tw), return_exceptions=True)
    for w in (cw, tw):
        try:
            w.close()
        except Exception:
            pass


async def handle(client_reader: asyncio.StreamReader,
                 client_writer: asyncio.StreamWriter) -> None:
    peer = client_writer.get_extra_info("peername")
    peer_ip = peer[0] if peer else "?"
    buf = bytearray()
    label = None
    replied = b""

    # 1) SSH klien diam -> dropbear duluan.
    try:
        first = await asyncio.wait_for(client_reader.read(4096), SSH_SILENCE_TIMEOUT)
    except asyncio.TimeoutError:
        first = b""
    if first:
        buf += first
        label = _route_from_bytes(bytes(buf))

    # 2) Kalau rute butuh byte lebih / belum terbaca HTTP penuh, baca lanjut.
    if label in (None, "dropbear-ssh"):
        pass  # keputusan final: dropbear
    else:
        need_more = label.endswith("101") or label.startswith("xray-")
        if need_more and b"\r\n\r\n" not in bytes(buf):
            try:
                extra = await asyncio.wait_for(
                    client_reader.read(4096), HTTP_READ_TIMEOUT)
                if extra:
                    buf += extra
                    label = _route_from_bytes(bytes(buf))
            except asyncio.TimeoutError:
                pass

    if label is None:
        label = "dropbear-ssh"

    log.info("mux %s route=%s first=%r", peer_ip, label, bytes(buf[:24]))

    # 3) HTTP WS umum: balas 101 sendiri lalu pipe ke Dropbear.
    if label == "dropbear-101":
        replied = (b"HTTP/1.1 101 Switching Protocols\r\n"
                   b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        try:
            client_writer.write(replied)
            await client_writer.drain()
        except (ConnectionError, OSError):
            client_writer.close()
            return

    thost, tport = await _target_for(label)
    if not thost:
        log.info("mux %s label=%s tanpa backend TCP-mentah -> drop", peer_ip, label)
        client_writer.close()
        return
    try:
        tr, tw = await asyncio.open_connection(thost, tport)
    except OSError as e:
        log.error("mux %s target %s:%s gagal: %s", peer_ip, thost, tport, e)
        client_writer.close()
        return

    # Byte awal diteruskan kecuali yang sudah dikonsumsi utk balasan 101.
    await _bridge(client_reader, client_writer, tr, tw,
                  bytes(buf) if not replied else b"")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s slowdns-mux %(levelname)s %(message)s")
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    log.info("SlowDNS mux listening on %s:%s", LISTEN_HOST, LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
