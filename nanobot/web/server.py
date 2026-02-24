"""Lightweight async web server for Nanobot chat UI.

Uses Python's built-in asyncio for HTTP and manual WebSocket (RFC 6455),
all on a single port. Handles GET, HEAD, and WebSocket upgrade requests.
"""

from __future__ import annotations

import asyncio
import hashlib
import base64
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop

_PKG_STATIC = Path(__file__).parent / "static"
_APP_STATIC = Path("/app/nanobot/web/static")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _find_static_dir() -> Path:
    for d in (_PKG_STATIC, _APP_STATIC):
        if d.is_dir() and (d / "index.html").exists():
            return d
    return _PKG_STATIC


def _load_index() -> bytes:
    static = _find_static_dir()
    index = static / "index.html"
    if index.exists():
        return index.read_bytes()
    logger.warning("index.html not found, using fallback")
    return _FALLBACK.encode()


_FALLBACK = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Nanobot</title>
<style>body{background:#0f0f13;color:#e4e4ef;font-family:sans-serif;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.c{text-align:center}h1{color:#6c5ce7}p{color:#8888a0}</style>
</head><body><div class="c"><h1>Nanobot AI</h1>
<p>Web UI assets not found. Service is running.</p></div></body></html>"""


class WebServer:
    """HTTP + WebSocket server on a single port using raw asyncio."""

    def __init__(self, agent: AgentLoop, host: str = "0.0.0.0", port: int = 8080):
        self.agent = agent
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None
        self._index: bytes = b""

    async def start(self) -> None:
        self._index = _load_index()
        logger.info("Static dir: {} (exists={})", _find_static_dir(), _find_static_dir().is_dir())
        logger.info("Index HTML: {} bytes", len(self._index))

        self._server = await asyncio.start_server(
            self._handle_connection, self.host, self.port,
        )
        logger.info("Web UI + WS serving on http://{}:{}", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ── Connection handler ──

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        is_ws = False
        try:
            # Read HTTP headers (up to 16KB)
            header_data = b""
            while b"\r\n\r\n" not in header_data and len(header_data) < 16384:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=10.0)
                if not chunk:
                    return
                header_data += chunk

            end = header_data.find(b"\r\n\r\n")
            if end == -1:
                return

            raw_headers = header_data[:end + 4]
            request_line, headers = self._parse_request(raw_headers)
            parts = request_line.split(" ", 2)
            if len(parts) < 2:
                return
            method = parts[0]
            path = parts[1]

            logger.debug("HTTP {} {} | Upgrade={}", method, path, headers.get("upgrade", ""))

            # WebSocket upgrade
            if headers.get("upgrade", "").lower() == "websocket" and method == "GET":
                is_ws = True
                await self._ws_upgrade(reader, writer, headers)
                return

            # Regular HTTP (GET or HEAD)
            if method in ("GET", "HEAD"):
                status, ct, body = self._route(path)
                await self._send_http(writer, status, ct, body if method == "GET" else b"")
            else:
                await self._send_http(writer, 405, "text/plain; charset=utf-8", b"Method Not Allowed")

        except asyncio.TimeoutError:
            try:
                await self._send_http(writer, 408, "text/plain; charset=utf-8", b"Request Timeout")
            except Exception:
                pass
        except Exception as e:
            logger.debug("Connection error: {}", e)
            try:
                await self._send_http(writer, 500, "text/plain; charset=utf-8", b"Internal Server Error")
            except Exception:
                pass
        finally:
            # Only close for HTTP — WS manages its own lifecycle
            if not is_ws:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

    # ── HTTP routing ──

    def _route(self, path: str) -> tuple[int, str, bytes]:
        if path == "/health":
            return 200, "text/plain; charset=utf-8", b"ok"
        if path in ("/", "/index.html"):
            return 200, "text/html; charset=utf-8", self._index
        # Static files
        safe = path.lstrip("/").replace("..", "")
        static = _find_static_dir()
        fp = static / safe
        if fp.is_file() and str(fp).startswith(str(static)):
            ct = CONTENT_TYPES.get(fp.suffix, "application/octet-stream")
            return 200, ct, fp.read_bytes()
        return 404, "text/plain; charset=utf-8", b"Not Found"

    @staticmethod
    async def _send_http(writer: asyncio.StreamWriter, status: int, ct: str, body: bytes) -> None:
        phrases = {200: "OK", 404: "Not Found", 405: "Method Not Allowed",
                   408: "Request Timeout", 500: "Internal Server Error"}
        header = (
            f"HTTP/1.1 {status} {phrases.get(status, 'OK')}\r\n"
            f"Content-Type: {ct}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()
        writer.write(header + body)
        await writer.drain()

    @staticmethod
    def _parse_request(raw: bytes) -> tuple[str, dict[str, str]]:
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\r\n")
        request_line = lines[0]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k.strip().lower()] = v.strip()
            elif ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return request_line, headers

    # ── WebSocket upgrade + chat ──

    async def _ws_upgrade(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                          headers: dict[str, str]) -> None:
        key = headers.get("sec-websocket-key", "")
        if not key:
            logger.warning("WS upgrade missing Sec-WebSocket-Key")
            await self._send_http(writer, 400, "text/plain; charset=utf-8", b"Missing Sec-WebSocket-Key")
            writer.close()
            return

        # Compute accept key per RFC 6455
        MAGIC = "258EAFA5-E914-47DA-95CA-5AB9F171BFED"
        accept = base64.b64encode(hashlib.sha1((key + MAGIC).encode()).digest()).decode()

        upgrade_resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode()
        writer.write(upgrade_resp)
        await writer.drain()

        session_id = f"web:{uuid.uuid4().hex[:8]}"
        logger.info("Web chat connected: {}", session_id)

        try:
            while True:
                frame = await self._ws_read_frame(reader)
                if frame is None:
                    break
                opcode, data = frame
                if opcode == 0x8:  # Close
                    await self._ws_send(writer, 0x8, data[:2] if data else b"")
                    break
                if opcode == 0x9:  # Ping
                    await self._ws_send(writer, 0xA, data)
                    continue
                if opcode == 0x1:  # Text
                    try:
                        msg = json.loads(data.decode())
                        text = msg.get("text", "").strip()
                        if not text:
                            continue

                        await self._ws_send_json(writer, {"type": "typing"})

                        response = await self.agent.process_direct(
                            content=text,
                            session_key=session_id,
                            channel="web",
                            chat_id=session_id,
                        )
                        await self._ws_send_json(writer, {
                            "type": "message",
                            "text": response or "I didn't have a response for that.",
                        })
                    except json.JSONDecodeError:
                        await self._ws_send_json(writer, {"type": "error", "text": "Invalid message."})
                    except Exception as e:
                        logger.error("Chat error: {}", e)
                        await self._ws_send_json(writer, {"type": "error", "text": f"Error: {e}"})
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        finally:
            logger.info("Web chat disconnected: {}", session_id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ── WebSocket frame helpers (RFC 6455) ──

    @staticmethod
    async def _ws_read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
        try:
            b0, b1 = await reader.readexactly(2)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None

        opcode = b0 & 0x0F
        masked = b1 & 0x80
        length = b1 & 0x7F

        if length == 126:
            raw = await reader.readexactly(2)
            length = int.from_bytes(raw, "big")
        elif length == 127:
            raw = await reader.readexactly(8)
            length = int.from_bytes(raw, "big")

        if masked:
            mask = await reader.readexactly(4)
            data = bytearray(await reader.readexactly(length))
            for i in range(length):
                data[i] ^= mask[i % 4]
            return opcode, bytes(data)
        else:
            data = await reader.readexactly(length)
            return opcode, data

    @staticmethod
    async def _ws_send(writer: asyncio.StreamWriter, opcode: int, data: bytes) -> None:
        frame = bytearray()
        frame.append(0x80 | opcode)  # FIN + opcode
        length = len(data)
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.append(126)
            frame.extend(length.to_bytes(2, "big"))
        else:
            frame.append(127)
            frame.extend(length.to_bytes(8, "big"))
        frame.extend(data)
        writer.write(bytes(frame))
        await writer.drain()

    async def _ws_send_json(self, writer: asyncio.StreamWriter, obj: dict) -> None:
        await self._ws_send(writer, 0x1, json.dumps(obj).encode())
