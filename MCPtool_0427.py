import asyncio
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from _dep_checker import ensure_deps
ensure_deps({
    "aiohttp": "aiohttp",
    "lxml": "lxml",
})

import aiohttp
from lxml import html as lxml_html
from lxml.etree import ParseError


_SCRIPT_DIR = Path(__file__).resolve().parent


class _WebpageLocale:
    _instance = None
    _data = None

    @classmethod
    def _detect_locale(cls) -> str:
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if (lang_id & 0x3FF) == 4:
                return "zh"
            return "en"
        except Exception:
            return "en"

    @classmethod
    def get(cls, key: str, **kwargs) -> str:
        if cls._data is None:
            locale = cls._detect_locale()
            path = _SCRIPT_DIR / "locale" / f"{locale}.json"
            if not path.exists():
                path = _SCRIPT_DIR / "locale" / "en.json"
            with open(path, encoding="utf-8") as f:
                cls._data = json.load(f).get("webpage_fetch", {})
        text = cls._data.get(key, key)
        if kwargs:
            try:
                return text.format(**kwargs)
            except KeyError:
                return text
        return text


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text_chunks: list[str] = []
        self._skip_tags = {"script", "style", "noscript", "head", "meta", "link", "title"}
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "div", "tr", "blockquote"):
            self._text_chunks.append("\n")
        elif tag == "td":
            self._text_chunks.append(" | ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        stripped = data.strip()
        if stripped:
            self._text_chunks.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._text_chunks)


CONTENT_SELECTORS = [
    '//article[contains(@class, "markdown-body")]',
    '//*[contains(@class, "markdown-body")]',
    '//article',
    '//main',
    '//*[@role="main"]',
    '//*[contains(@class, "post-content")]',
    '//*[contains(@class, "entry-content")]',
    '//*[contains(@class, "article-content")]',
    '//*[contains(@class, "content")]',
]

IGNORE_CONTAINER = './/*[@id="readme"]//*[contains(@class, "markdown-body")]'

HEADER_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
NEWLINE_TAGS = {"p", "br", "li", "div", "tr", "blockquote"}


def _lxml_extract_text(html_text: str) -> str:
    try:
        tree = lxml_html.fromstring(html_text)
    except ParseError:
        return ""

    container = _find_content_container(tree)

    if container is None:
        body = tree.xpath('//body')
        container = body[0] if body else tree

    text = _tree_to_text(container)
    return text.strip()


def _find_content_container(tree):
    for selector in CONTENT_SELECTORS:
        elements = tree.xpath(selector)
        for el in elements:
            if el.tag == "main":
                return el
            if el.tag == "article":
                return el
            text = (el.text or "") + "".join(el.xpath(".//text()"))
            if len(text.strip()) > 200:
                if not el.xpath(IGNORE_CONTAINER):
                    return el
    return None


def _tree_to_text(node) -> str:
    parts = []
    _walk(node, parts)
    raw = "".join(parts)
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _walk(node, parts):
    if node.tag in ("script", "style", "noscript", "head", "meta", "link", "title", "nav", "footer", "header"):
        return
    if node.tag in HEADER_TAGS:
        parts.append("\n")
    text = (node.text or "").strip()
    if text and node.tag not in ("html", "body"):
        parts.append(text)
    for child in node:
        tag = child.tag if hasattr(child, "tag") else None
        if tag in NEWLINE_TAGS:
            parts.append("\n")
        elif tag == "td":
            parts.append(" | ")
        _walk(child, parts)
    if node.tag in HEADER_TAGS:
        parts.append("\n")
    tail = (node.tail or "").strip()
    if tail and node.tag not in ("html", "body"):
        parts.append(tail)


def _extract_text_from_html(html: str) -> str:
    text = _lxml_extract_text(html)
    if text:
        return text

    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return html
    raw = extractor.get_text()
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


async def fetch_webpage(
    url: str,
    timeout: int = 15,
    max_chars: int = 8000,
) -> str:
    """
    Open an HTTP/HTTPS webpage and return the plain text content.

    Args:
        url: The webpage URL (must start with http:// or https://)
        timeout: Request timeout in seconds (default 15)
        max_chars: Maximum characters to return (default 8000)

    Returns:
        Extracted plain text content with source URL and status information.
    """
    if not url.startswith(("http://", "https://")):
        return _WebpageLocale.get("invalid_url", url=url)

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
                max_redirects=5,
            ) as resp:
                if resp.status != 200:
                    return _WebpageLocale.get("status_not_200",
                                              url=url, code=resp.status, reason=resp.reason)

                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return _WebpageLocale.get("unsupported_content_type",
                                              url=url, code=resp.status, type=content_type)

                raw_body = await resp.text(errors="replace")

        text = _extract_text_from_html(raw_body)

        if not text:
            return _WebpageLocale.get("empty_content", url=url, code=resp.status)

        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True

        result = _WebpageLocale.get("success_header",
                                     url=url, code=resp.status, length=len(text))
        if truncated:
            result += _WebpageLocale.get("truncated_suffix")
        result += f"\n\n{text}"

        return result

    except aiohttp.ClientError as e:
        return _WebpageLocale.get("network_error", error=str(e))
    except asyncio.TimeoutError:
        return _WebpageLocale.get("timeout", timeout=timeout)
    except Exception as e:
        return _WebpageLocale.get("parse_error", type=type(e).__name__, message=str(e))


import sys
import json
import argparse
import tkinter as tk
from tkinter import ttk


def parse_args():
    parser = argparse.ArgumentParser(description="通用审批/确认弹窗（子进程模式）")
    parser.add_argument("--title", default="确认操作", help="窗口标题")
    parser.add_argument("--message", required=True, help="显示的消息内容")
    parser.add_argument("--type", default="confirm",
                        choices=["confirm", "choice", "input"],
                        help="弹窗类型: confirm=确认/取消, choice=选项列表, input=文本输入")
    parser.add_argument("--choices", default="", help="选项列表，逗号分隔（仅 type=choice 时使用）")
    parser.add_argument("--timeout", type=int, default=180,
                        help="超时秒数（默认180，<=0 为不限时）")
    parser.add_argument("--default", default="", help="默认值（choice 时为首选项，input 时为预填文本）")
    return parser.parse_args()


def build_confirm(root, message, timeout):
    result = {"status": "cancelled", "value": ""}
    seconds_left = [timeout]

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    msg_label = ttk.Label(frame, text=message, wraplength=480, justify="left", font=("Microsoft YaHei", 10))
    msg_label.pack(pady=(0, 15))

    countdown_label = ttk.Label(frame, text="", foreground="gray")
    if timeout > 0:
        countdown_label.config(text=f"⏱ {timeout} 秒后自动取消")
    countdown_label.pack()

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(pady=(10, 0))

    def on_approve():
        result["status"] = "approved"
        result["value"] = "confirmed"
        root.destroy()

    def on_cancel():
        root.destroy()

    approve_btn = ttk.Button(btn_frame, text="✓ 确认", command=on_approve, width=12)
    approve_btn.pack(side="left", padx=5)

    cancel_btn = ttk.Button(btn_frame, text="✗ 取消", command=on_cancel, width=12)
    cancel_btn.pack(side="left", padx=5)

    if timeout > 0:
        def tick():
            seconds_left[0] -= 1
            if seconds_left[0] <= 0:
                result["status"] = "timeout"
                root.destroy()
            else:
                countdown_label.config(text=f"⏱ {seconds_left[0]} 秒后自动取消")
                root.after(1000, tick)
        root.after(1000, tick)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.bind("<Return>", lambda e: on_approve())
    root.bind("<Escape>", lambda e: on_cancel())
    return result


def build_choice(root, message, choices, timeout, default):
    result = {"status": "cancelled", "value": ""}
    seconds_left = [timeout]

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    msg_label = ttk.Label(frame, text=message, wraplength=480, justify="left", font=("Microsoft YaHei", 10))
    msg_label.pack(pady=(0, 10))

    choice_var = tk.StringVar(value=default if default in choices else choices[0])

    combo = ttk.Combobox(frame, textvariable=choice_var, values=choices,
                         state="readonly", font=("Microsoft YaHei", 10), width=40)
    combo.pack(pady=(0, 10))

    countdown_label = ttk.Label(frame, text="", foreground="gray")
    if timeout > 0:
        countdown_label.config(text=f"⏱ {timeout} 秒后自动取消")
    countdown_label.pack()

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(pady=(10, 0))

    def on_approve():
        result["status"] = "approved"
        result["value"] = choice_var.get()
        root.destroy()

    def on_cancel():
        root.destroy()

    approve_btn = ttk.Button(btn_frame, text="✓ 确认选择", command=on_approve, width=12)
    approve_btn.pack(side="left", padx=5)

    cancel_btn = ttk.Button(btn_frame, text="✗ 取消", command=on_cancel, width=12)
    cancel_btn.pack(side="left", padx=5)

    if timeout > 0:
        def tick():
            seconds_left[0] -= 1
            if seconds_left[0] <= 0:
                result["status"] = "timeout"
                root.destroy()
            else:
                countdown_label.config(text=f"⏱ {seconds_left[0]} 秒后自动取消")
                root.after(1000, tick)
        root.after(1000, tick)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.bind("<Escape>", lambda e: on_cancel())
    return result


def build_input(root, message, timeout, default):
    result = {"status": "cancelled", "value": ""}
    seconds_left = [timeout]

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    msg_label = ttk.Label(frame, text=message, wraplength=480, justify="left", font=("Microsoft YaHei", 10))
    msg_label.pack(pady=(0, 10))

    entry = ttk.Entry(frame, font=("Microsoft YaHei", 10), width=50)
    if default:
        entry.insert(0, default)
    entry.pack(pady=(0, 10))
    entry.focus_set()

    countdown_label = ttk.Label(frame, text="", foreground="gray")
    if timeout > 0:
        countdown_label.config(text=f"⏱ {timeout} 秒后自动取消")
    countdown_label.pack()

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(pady=(10, 0))

    def on_approve():
        result["status"] = "approved"
        result["value"] = entry.get().strip()
        root.destroy()

    def on_cancel():
        root.destroy()

    approve_btn = ttk.Button(btn_frame, text="✓ 提交", command=on_approve, width=12)
    approve_btn.pack(side="left", padx=5)

    cancel_btn = ttk.Button(btn_frame, text="✗ 取消", command=on_cancel, width=12)
    cancel_btn.pack(side="left", padx=5)

    if timeout > 0:
        def tick():
            seconds_left[0] -= 1
            if seconds_left[0] <= 0:
                result["status"] = "timeout"
                root.destroy()
            else:
                countdown_label.config(text=f"⏱ {seconds_left[0]} 秒后自动取消")
                root.after(1000, tick)
        root.after(1000, tick)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.bind("<Return>", lambda e: on_approve())
    root.bind("<Escape>", lambda e: on_cancel())
    return result


def _load_dialog_ui(locale: str) -> dict:
    path = _SCRIPT_DIR / "locale" / f"{locale}.json"
    if not path.exists():
        path = _SCRIPT_DIR / "locale" / "en.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("ui", {})


def main():
    args = parse_args()

    timeout = args.timeout if args.timeout > 0 else 0
    choices = [c.strip() for c in args.choices.split(",") if c.strip()] if args.choices else []

    ui_cfg = _load_dialog_ui(_WebpageLocale._detect_locale())

    root = tk.Tk()
    root.title(args.title)
    root.attributes("-topmost", True)
    root.resizable(False, False)

    try:
        root.iconbitmap(default="")
    except Exception:
        pass

    if args.type == "choice":
        if not choices:
            print(json.dumps({"status": "error", "value": ui_cfg.get("error_no_options", "At least one option is required")}, ensure_ascii=False))
            sys.exit(1)
        result = build_choice(root, args.message, choices, timeout, args.default)
    elif args.type == "input":
        result = build_input(root, args.message, timeout, args.default)
    else:
        result = build_confirm(root, args.message, timeout)

    root.geometry("")
    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    root.geometry(f"+{x}+{y}")

    root.mainloop()

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
