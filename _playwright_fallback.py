import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from _dep_checker import ensure_deps

ensure_deps({
    "aiohttp": "aiohttp",
    "patchright": "patchright",
})

import aiohttp

_SCRIPT_DIR = Path(__file__).resolve().parent

PORT_FILE = _SCRIPT_DIR / ".playwright_port"
PID_FILE = _SCRIPT_DIR / ".playwright_pid"

_SERVICE_URL = None


def _get_service_url():
    global _SERVICE_URL
    if _SERVICE_URL is not None:
        return _SERVICE_URL

    if not PORT_FILE.exists():
        return None

    try:
        port = int(PORT_FILE.read_text().strip())
        _SERVICE_URL = f"http://127.0.0.1:{port}"
        return _SERVICE_URL
    except Exception:
        return None


async def _health_check():
    url = _get_service_url()
    if not url:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{url}/health",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


def _start_service():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    service_script = str(_SCRIPT_DIR / "playwright_service.py")

    proc = subprocess.Popen(
        [sys.executable, service_script],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    for _ in range(40):
        time.sleep(0.25)
        if PORT_FILE.exists():
            try:
                port = int(PORT_FILE.read_text().strip())
                _SERVICE_URL = f"http://127.0.0.1:{port}"
                return True
            except Exception:
                pass

    return False


async def ensure_playwright_service():
    if await _health_check():
        return

    if not _start_service():
        return

    for _ in range(30):
        await asyncio.sleep(0.5)
        if await _health_check():
            return


async def _fetch_via_playwright(url: str, timeout: int = 30):
    base_url = _get_service_url()
    if not base_url:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/fetch",
                json={"url": url, "timeout": timeout * 1000},
                timeout=aiohttp.ClientTimeout(total=timeout + 15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
    except Exception:
        return None


SPA_PATTERNS = [
    re.compile(r'<div\s+id=["\'](__next|__nuxt|app|root)["\'][^>]*>\s*</div>', re.I),
    re.compile(r'<script\s+[^>]*src=["\'][^"\']*/(bundle|main|app|chunk)[^"\']*\.js["\']', re.I),
    re.compile(r'window\.__NEXT_DATA__'),
    re.compile(r'window\.__NUXT__'),
    re.compile(r'<noscript>[^<]*enable\s*JavaScript', re.I),
    re.compile(r'<noscript>[^<]*请\s*启用\s*JavaScript', re.I),
    re.compile(r'<div\s+id=["\']app["\'][^>]*></div>', re.I),
    re.compile(r'<script\s+type=["\']module["\']', re.I),
]


def is_spa_shell(html_text: str, extracted_text: str) -> bool:
    if len(extracted_text.strip()) > 500:
        return False

    for pattern in SPA_PATTERNS:
        if pattern.search(html_text):
            return True

    if len(html_text) > 5000 and len(extracted_text.strip()) < 200:
        return True

    body_match = re.search(r'<body[^>]*>(.*?)</body>', html_text, re.DOTALL | re.I)
    if body_match:
        body_text = re.sub(r'<[^>]+>', '', body_match.group(1)).strip()
        body_text = re.sub(r'\s+', ' ', body_text)
        if len(body_text) < 100:
            return True

    return False


async def playwright_fallback_fetch(
    url: str,
    raw_html: str,
    extracted_text: str,
    timeout: int = 15,
    text_only: bool = True,
    force: bool = False,
):
    if not force and not is_spa_shell(raw_html, extracted_text):
        return None

    await ensure_playwright_service()

    result = await _fetch_via_playwright(url, timeout=timeout)
    if result is None:
        return None

    if not result.get("ok"):
        return None

    pw_html = result.get("html", "")
    if not pw_html:
        return None

    from MCPtool_0427 import _extract_text_from_html

    text = _extract_text_from_html(pw_html, text_only=text_only)
    if not text:
        return None

    return text
