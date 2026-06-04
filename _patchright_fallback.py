import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent

_PORT_FILE = _SCRIPT_DIR / ".playwright_port"
_PID_FILE = _SCRIPT_DIR / ".playwright_pid"

_MAX_START_WAIT = 25
_REQUEST_TIMEOUT = 45
_SERVICE_SCRIPT = _SCRIPT_DIR / "playwright_service.py"


def _ensure_deps():
    try:
        import patchright
    except ImportError:
        print("[pw-fallback] Patchright not installed, skipping browser fallback", flush=True)
        return False
    return True


def _kill_service():
    if _PID_FILE.exists():
        try:
            pid = int(_PID_FILE.read_text().strip())
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                )
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            _PID_FILE.unlink(missing_ok=True)
            _PORT_FILE.unlink(missing_ok=True)
        except Exception:
            pass


async def _check_port(port):
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=1.0,
        )
        writer.close()
        return True
    except Exception:
        return False


async def _health_check(port):
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=3.0,
        )
        req = f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        writer.write(req.encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(4096), timeout=3.0)
        writer.close()
        header_end = raw.find(b"\r\n\r\n")
        if header_end == -1:
            return False
        body = raw[header_end + 4:]
        data = json.loads(body.decode("utf-8", errors="replace"))
        return data.get("status") == "ok"
    except Exception:
        return False


async def _ensure_service():
    if not _ensure_deps():
        return False, "patchright not installed"

    if _PORT_FILE.exists():
        try:
            port = int(_PORT_FILE.read_text().strip())
            if await _health_check(port):
                return True, ""
        except Exception:
            pass

    _kill_service()

    if not _SERVICE_SCRIPT.exists():
        return False, f"service script not found: {_SERVICE_SCRIPT}"

    try:
        proc = subprocess.Popen(
            [sys.executable, str(_SERVICE_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        start = time.time()
        port_ready = False
        while time.time() - start < _MAX_START_WAIT:
            await asyncio.sleep(0.5)
            if proc.poll() is not None:
                stdout_data = proc.stdout.read() if proc.stdout else ""
                stderr_data = proc.stderr.read() if proc.stderr else ""
                return False, f"service exited code={proc.returncode}: {stderr_data[:300] or stdout_data[:300]}"

            if not port_ready and _PORT_FILE.exists():
                try:
                    port = int(_PORT_FILE.read_text().strip())
                    if await _health_check(port):
                        port_ready = True
                except Exception:
                    pass

            if port_ready:
                return True, ""

        if proc.poll() is None:
            proc.kill()
            return False, f"startup timeout after {_MAX_START_WAIT}s"
        return False, f"unknown startup failure"
    except Exception as e:
        return False, str(e)[:300]


def _should_fallback(html_text: str, extracted_text: str) -> bool:
    if len(extracted_text.strip()) > 500:
        return False

    spa_patterns = (
        r'<div\s+id=["\'](__next|__nuxt|app|root)["\']',
        r'<script\s+[^>]*src=["\'][^"\']*/(bundle|main|app|chunk)[^"\']*\.js["\']',
        r'window\.__NEXT_DATA__',
        r'window\.__NUXT__',
        r'<noscript>[^<]*enable\s*JavaScript',
        r'<noscript>[^<]*启用\s*JavaScript',
        r'<div\s+id=["\']root["\']\s*></div>',
    )
    html_lower = html_text.lower()
    for pattern in spa_patterns:
        if re.search(pattern, html_lower):
            return True

    if len(html_text) > 5000 and len(extracted_text.strip()) < 200:
        return True

    if len(extracted_text.strip()) < 50 and len(html_text) > 1000:
        return True

    return False


async def _request_fetch(url, timeout=30):
    port_file = _SCRIPT_DIR / ".playwright_port"
    if not port_file.exists():
        return None

    port = int(port_file.read_text().strip())

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=5.0,
        )

        body = json.dumps({
            "url": url,
            "timeout": timeout,
            "wait_until": "networkidle",
        }).encode("utf-8")

        req = (
            f"POST /fetch HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body

        writer.write(req)
        await writer.drain()

        raw = b""
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=_REQUEST_TIMEOUT)
            if not chunk:
                break
            raw += chunk
            if b"\r\n\r\n" in raw:
                break

        header_end = raw.find(b"\r\n\r\n")
        if header_end == -1:
            writer.close()
            return None

        body_bytes = raw[header_end + 4:]
        content_length = 0
        header_text = raw[:header_end].decode("utf-8", errors="replace")
        for line in header_text.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

        while len(body_bytes) < content_length:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=10)
            if not chunk:
                break
            body_bytes += chunk

        writer.close()
        return json.loads(body_bytes.decode("utf-8", errors="replace"))
    except Exception as e:
        return {"_error": str(e)[:300]}


async def fetch_with_browser(url: str, timeout: int = 30):
    ok, err_msg = await _ensure_service()
    if not ok:
        return {"_error": err_msg}

    return await _request_fetch(url, timeout=timeout)
