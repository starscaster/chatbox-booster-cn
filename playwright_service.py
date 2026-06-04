import asyncio
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent

_PORT_FILE = _SCRIPT_DIR / ".playwright_port"
_PID_FILE = _SCRIPT_DIR / ".playwright_pid"

IDLE_TIMEOUT = 600
HARD_TIMEOUT = 1800
MAX_CONCURRENT = 5

_LAUNCH_LOCK = asyncio.Lock()
_active_tasks: set[asyncio.Task] = set()
_last_access = time.time()
_browser = None
_playwright = None
_semaphore: asyncio.Semaphore = None

CHANNEL_CHROME = "chrome"
CHANNEL_EDGE = "msedge"


def _find_system_browser():
    chrome_paths = [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        os.path.expandvars("%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe"),
    ]
    for p in chrome_paths:
        if os.path.isfile(p):
            return CHANNEL_CHROME, None

    edge_paths = [
        "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        os.path.expandvars("%PROGRAMFILES(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"),
    ]
    for p in edge_paths:
        if os.path.isfile(p):
            return CHANNEL_EDGE, p

    return None, None


_BROWSER_CHANNEL, _BROWSER_EXECUTABLE = _find_system_browser()
_USER_DATA_DIR = None


def _kill_process_tree():
    pid = os.getpid()
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


def _cleanup_files():
    for f in (_PORT_FILE, _PID_FILE):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass


async def _shutdown():
    global _browser, _playwright, _USER_DATA_DIR
    try:
        if _browser:
            await _browser.close()
            _browser = None
    except Exception:
        pass
    try:
        if _playwright:
            await _playwright.stop()
            _playwright = None
    except Exception:
        pass
    if _USER_DATA_DIR:
        try:
            shutil.rmtree(_USER_DATA_DIR, ignore_errors=True)
        except Exception:
            pass
        _USER_DATA_DIR = None
    _cleanup_files()
    _kill_process_tree()


async def _idle_watchdog():
    while True:
        await asyncio.sleep(30)
        elapsed = time.time() - _last_access
        if elapsed > IDLE_TIMEOUT and not _active_tasks:
            print(f"[watchdog] Idle {elapsed:.0f}s, shutting down...", flush=True)
            await _shutdown()
            return
        if elapsed > HARD_TIMEOUT:
            print(f"[watchdog] Hard timeout {elapsed:.0f}s, force shutdown...", flush=True)
            await _shutdown()
            return


async def _launch_browser():
    global _browser, _playwright, _USER_DATA_DIR
    async with _LAUNCH_LOCK:
        if _browser is not None:
            return
        from patchright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _USER_DATA_DIR = tempfile.mkdtemp(prefix="pw_profile_")

        launch_kwargs = {
            "headless": False,
            "no_viewport": False,
            "viewport": {"width": 1920, "height": 1080},
            "user_data_dir": _USER_DATA_DIR,
            "args": [
                "--disable-extensions",
                "--disable-sync",
                "--disable-default-apps",
                "--disable-translate",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-breakpad",
                "--disable-hang-monitor",
                "--mute-audio",
                "--hide-scrollbars",
                "--no-first-run",
                "--disable-features=TranslateUI,OptimizationHints",
            ],
        }

        if _BROWSER_CHANNEL == CHANNEL_CHROME:
            launch_kwargs["channel"] = "chrome"
            print("[pw] Launching persistent Chrome via channel", flush=True)
        elif _BROWSER_CHANNEL == CHANNEL_EDGE and _BROWSER_EXECUTABLE:
            launch_kwargs["executable_path"] = _BROWSER_EXECUTABLE
            print(f"[pw] Launching persistent Edge: {_BROWSER_EXECUTABLE}", flush=True)
        else:
            print("[pw] Launching persistent bundled Chromium", flush=True)

        _browser = await _playwright.chromium.launch_persistent_context(**launch_kwargs)

        print(f"[pw] Browser launched with persistent profile", flush=True)
        warmup = await _browser.new_page()
        await warmup.goto("about:blank")
        await warmup.close()


async def _block_unnecessary(page):
    pass


async def handle_health(request):
    return 200, {"status": "ok", "channel": _BROWSER_CHANNEL}


async def handle_fetch(data):
    url = data.get("url", "")
    timeout = data.get("timeout", 30)
    wait_until = data.get("wait_until", "networkidle")

    if not url:
        return 400, {"error": "url is required"}

    async with _semaphore:
        page = None
        try:
            page = await _browser.new_page()

            await page.goto(url, wait_until=wait_until, timeout=timeout * 1000)

            await asyncio.sleep(random.uniform(0.3, 0.8))

            status = 200
            html_content = await page.content()

            text_content = await page.evaluate("""() => {
                const clone = document.documentElement.cloneNode(true);
                const skips = clone.querySelectorAll(
                    'script, style, noscript, nav, footer, iframe, svg, img, video, audio, canvas'
                );
                skips.forEach(el => el.remove());
                return clone.textContent || '';
            }""")

            text_content = text_content.strip() if text_content else ""
            html_content = html_content.strip() if html_content else ""

            return 200, {
                "status": status,
                "url": page.url,
                "title": await page.title(),
                "html": html_content,
                "text": text_content,
            }

        except Exception as e:
            return 502, {"error": f"Page load failed: {e}"}
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass


async def _handle_client(reader, writer):
    global _last_access
    try:
        raw = b""
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=60)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 10 * 1024 * 1024:
                break
            if b"\r\n\r\n" in raw:
                break

        headers_end = raw.find(b"\r\n\r\n")
        if headers_end == -1:
            writer.close()
            return

        header_bytes = raw[:headers_end]
        body_bytes = raw[headers_end + 4:]

        header_text = header_bytes.decode("utf-8", errors="replace")
        lines = header_text.split("\r\n")
        if not lines:
            writer.close()
            return

        first_line = lines[0]
        parts = first_line.split(" ")
        method = parts[0]
        path = parts[1] if len(parts) > 1 else "/"

        content_length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

        while len(body_bytes) < content_length:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=30)
            if not chunk:
                break
            body_bytes += chunk

        _last_access = time.time()

        if method == "GET" and path == "/health":
            status, response = await handle_health(None)
        elif method == "POST" and path == "/fetch":
            try:
                data = json.loads(body_bytes.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                data = {}
            status, response = await handle_fetch(data)
        else:
            status, response = 404, {"error": "not found"}

        resp_body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        resp = (
            f"HTTP/1.1 {status} OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(resp_body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + resp_body
        writer.write(resp)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    if not _BROWSER_CHANNEL and not _BROWSER_EXECUTABLE:
        print("[pw] FATAL: No Chromium browser found on system!", flush=True)
        sys.exit(1)

    port = None
    for _ in range(20):
        candidate = random.randint(20000, 50000)
        try:
            test_sock = socket.socket()
            test_sock.bind(("127.0.0.1", candidate))
            test_sock.close()
        except OSError:
            continue

        if _PORT_FILE.exists():
            try:
                old_port = int(_PORT_FILE.read_text().strip())
                test_sock = socket.socket()
                test_sock.settimeout(0.5)
                test_sock.connect(("127.0.0.1", old_port))
                test_sock.close()
                port = old_port
                print(f"[pw] Reusing existing port {port}", flush=True)
                break
            except Exception:
                pass

        if port is None:
            port = candidate
            _PORT_FILE.write_text(str(port))
            break

    if port is None:
        print("[pw] FATAL: No available port", flush=True)
        sys.exit(1)

    _PID_FILE.write_text(str(os.getpid()))
    print(f"[pw] Service starting on 127.0.0.1:{port}", flush=True)
    print(f"[pw] Browser: {_BROWSER_CHANNEL or _BROWSER_EXECUTABLE}", flush=True)

    try:
        await _launch_browser()
    except Exception as e:
        print(f"[pw] FATAL: Browser launch failed: {e}", flush=True)
        traceback.print_exc()
        _cleanup_files()
        sys.exit(1)

    server = await asyncio.start_server(_handle_client, "127.0.0.1", port)

    asyncio.create_task(_idle_watchdog())

    print(f"[pw] Ready. Idle timeout: {IDLE_TIMEOUT}s, Hard timeout: {HARD_TIMEOUT}s", flush=True)

    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        await _shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
