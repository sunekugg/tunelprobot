#!/usr/bin/env python3
"""ws-ssh: WebSocket/SSH multiplexer ke Dropbear.

- 10015: WebSocket (dari nginx 10080, path /) — terima IP dari header X-Real-IP
- 9080, 9088: publik langsung (Tanpa PROXY) — peer name asli
- 10016: PROXY protocol v1 (dari HAProxy jalur SSH-) — parse baris PROXY

Tulis peta {port_lokal_dropbear: {"ip": ip_asli, "ts": epoch}} ke
/run/ws_ssh_ipmap.json supaya Dropbear (yang melihat 127.0.0.1:<port>)
bisa dipetakan ke IP klien asli. Tulis atomik, hapus saat koneksi putus.
"""
import asyncio
import json
import os
import re
import time

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 109
IPMAP_PATH = "/run/ws_ssh_ipmap.json"
IPMAP_LOCK = asyncio.Lock()

# Header dipercaya hanya dari loopback (dari nginx) agar tak bisa dipalsukan
# dari luar lewat port publik 9080/9088.


def _ipmap_read() -> dict:
    try:
        with open(IPMAP_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


async def _ipmap_write(data: dict):
    async with IPMAP_LOCK:
        tmp = IPMAP_PATH + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, IPMAP_PATH)
        except Exception:
            pass


async def _ipmap_add(local_port: int, ip: str):
    data = _ipmap_read()
    data[str(local_port)] = {"ip": ip, "ts": int(time.time())}
    await _ipmap_write(data)


async def _ipmap_del(local_port: int):
    data = _ipmap_read()
    if str(local_port) in data:
        del data[str(local_port)]
        await _ipmap_write(data)


def _clear_ipmap():
    try:
        with open(IPMAP_PATH, "w") as f:
            json.dump({}, f)
    except Exception:
        pass


async def pipe(r, w):
    try:
        while not r.at_eof():
            d = await r.read(8192)
            if not d:
                break
            w.write(d)
            await w.drain()
    except Exception:
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def handle(cr, cw, source_desc):
    # source_desc: {"ip": ip_asli, "mode": "header"|"peer"|"proxy"}
    tw = None
    dropbear_port = None
    try:
        fc = await asyncio.wait_for(cr.read(4096), timeout=15)
        if not fc:
            cw.close()
            return
        tr, tw = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
        dropbear_port = tw.get_extra_info("sockname")[1]

        # Simpan IP asli ke peta (server Dropbear melihat 127.0.0.1:<dropbear_port>)
        client_ip = source_desc.get("ip", "127.0.0.1")
        if client_ip and client_ip not in ("127.0.0.1", "::1"):
            await _ipmap_add(dropbear_port, client_ip)

        if fc.startswith(b"SSH-"):
            tw.write(fc)
            await tw.drain()
        else:
            cw.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            await cw.drain()
            if b"SSH-" in fc:
                tw.write(fc[fc.find(b"SSH-"):])
                await tw.drain()
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))
    except Exception:
        try:
            cw.close()
        except Exception:
            pass
        if tw:
            try:
                tw.close()
            except Exception:
                pass
    finally:
        if dropbear_port is not None:
            await _ipmap_del(dropbear_port)


async def handle_plain(cr, cw):
    """Port publik 9080/9088 — peer name = IP asli."""
    peer = cr.get_extra_info("peername")
    ip = peer[0] if peer else "127.0.0.1"
    await handle(cr, cw, {"ip": ip, "mode": "peer"})


async def handle_ws(cr, cw):
    """Port 10015 — dari nginx, baca header X-Real-IP dari HTTP request awal."""
    peer = cr.get_extra_info("peername")
    ip = peer[0] if peer else "127.0.0.1"
    # Baca seluruh header (sampai \r\n\r\n) atau chunk besar pertama.
    buf = b""
    try:
        while b"\r\n\r\n" not in buf:
            chunk = await asyncio.wait_for(cr.read(4096), timeout=15)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
    except Exception:
        pass
    if not buf:
        cw.close()
        return
    # Kalau peer loopback dan ada header X-Real-IP, percayai itu.
    if ip in ("127.0.0.1", "::1"):
        m = re.search(rb"X-Real-IP:\s*([0-9a-fA-F\.:]+)", buf)
        if m:
            ip = m.group(1).decode()
    # Balas 101 untuk upgrade WebSocket (jangan teruskan header ke Dropbear).
    if b"SSH-" in buf:
        await handle_with_first_chunk(cr, cw, buf[buf.find(b"SSH-"):], ip)
    else:
        cw.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        await cw.drain()
        # Sisa chunk setelah \r\n\r\n adalah payload SSH (kalau ada)
        idx = buf.find(b"\r\n\r\n")
        payload = buf[idx + 4:] if idx != -1 else b""
        await handle_with_first_chunk(cr, cw, payload, ip)


async def handle_with_first_chunk(cr, cw, first_chunk, ip):
    tw = None
    dropbear_port = None
    try:
        tr, tw = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
        dropbear_port = tw.get_extra_info("sockname")[1]
        if first_chunk and ip and ip not in ("127.0.0.1", "::1"):
            await _ipmap_add(dropbear_port, ip)
        if first_chunk:
            tw.write(first_chunk)
            await tw.drain()
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))
    except Exception:
        try:
            cw.close()
        except Exception:
            pass
        if tw:
            try:
                tw.close()
            except Exception:
                pass
    finally:
        if dropbear_port is not None:
            await _ipmap_del(dropbear_port)


async def handle_proxy_v1(cr, cw):
    """Port 10016 — HAProxy jalur SSH- dgn send-proxy v1."""
    peer = cr.get_extra_info("peername")
    ip = peer[0] if peer else "127.0.0.1"
    # PROXY v1 line: "PROXY TCP4 <src_ip> <dst_ip> <src_port> <dst_port>\r\n"
    try:
        first = await asyncio.wait_for(cr.readuntil(b"\r\n"), timeout=5)
    except Exception:
        cw.close()
        return
    if first.startswith(b"PROXY "):
        parts = first.decode("ascii", errors="ignore").split()
        if len(parts) >= 6:
            ip = parts[2]
    # Teruskan SSH- handshake setelah PROXY line
    fc = await asyncio.wait_for(cr.read(4096), timeout=15)
    if not fc:
        cw.close()
        return
    try:
        tr, tw = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
        dropbear_port = tw.get_extra_info("sockname")[1]
        if ip and ip not in ("127.0.0.1", "::1"):
            await _ipmap_add(dropbear_port, ip)
        tw.write(fc)
        await tw.drain()
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))
    except Exception:
        try:
            cw.close()
        except Exception:
            pass
        if tw:
            try:
                tw.close()
            except Exception:
                pass


async def main():
    _clear_ipmap()
    srvs = [
        asyncio.start_server(handle_ws, "0.0.0.0", 10015),
        asyncio.start_server(handle_plain, "0.0.0.0", 9080),
        asyncio.start_server(handle_plain, "0.0.0.0", 9088),
        asyncio.start_server(handle_proxy_v1, "127.0.0.1", 10016),
    ]
    await asyncio.gather(*(s.serve_forever() for s in await asyncio.gather(*srvs)))


if __name__ == "__main__":
    asyncio.run(main())
