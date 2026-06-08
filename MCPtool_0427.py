import asyncio
import json
import re
from html.parser import HTMLParser
from pathlib import Path

# ----- 依赖检查 -----
from _dep_checker import ensure_deps
ensure_deps({
    "curl_cffi": "curl_cffi",
    "lxml": "lxml",
    "tiktoken": "tiktoken",
})

import tiktoken
from curl_cffi import requests as curl_requests
from lxml import html as lxml_html
from lxml.etree import ParseError


_SCRIPT_DIR = Path(__file__).resolve().parent

_TOKENIZER = None


def _get_tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = tiktoken.get_encoding("cl100k_base")
    return _TOKENIZER


def _count_tokens(text: str) -> int:
    encoder = _get_tokenizer()
    return len(encoder.encode(text))


def _truncate_by_tokens(text: str, max_tokens: int) -> str:
    # 使用 OpenAI 的 cl100k_base 编码器
    # 按 token 数量截断文本
    encoder = _get_tokenizer()
    token_ids = encoder.encode(text)
    if len(token_ids) <= max_tokens:
        return text
    truncated_ids = token_ids[:max_tokens]
    return encoder.decode(truncated_ids)


SAFE_REMOVE_PATTERNS = [
    re.compile(r'^\[\d+\]$'),
    re.compile(r'^\(\d+\)$'),
    re.compile(r'^\d+\.$'),
    re.compile(r'^Fig\.\s*\d+$'),
    re.compile(r'^Table\s*\d+$'),
]


def _clean_reference_noise(text: str) -> str:
    words = text.split()
    cleaned: list[str] = []
    for w in words:
        core = w.rstrip(".,;:!?)")
        if any(p.match(core) for p in SAFE_REMOVE_PATTERNS):
            suff = w[len(core):]
            if suff:
                cleaned.append(suff)
            continue
        cleaned.append(w)
    result = " ".join(cleaned)
    result = re.sub(r'\s([.,;:!?)])', r'\1', result)
    result = re.sub(r'[,;]\s*[,;]', ',', result)
    return result


_JSON_TEXT_KEYS = {"content", "text", "title", "description", "body", "summary",
                   "message", "own_text", "excerpt_title", "excerpt", "subject",
                   "name", "question_title", "answer_content", "headline"}


_JSON_SKIP_KEYS = {
    "id", "type", "state", "url", "href", "source_pin_id",
    "created", "updated", "is_deleted", "self_create",
    "view_permission", "comment_permission", "can_top", "is_top",
    "is_admin_close_repin", "admin_closed_comment",
    "meet_reaction_guide_conditions",
    "like_count", "comment_count", "repin_count", "reaction_count",
    "favorite_count", "favlists_count", "page_view_count", "voteup_count",
    "thumbnail", "width", "height", "is_watermark", "watermark_url",
    "original_url", "is_gif", "is_long", "text_link_type", "fold_type",
    "content_html", "url_token", "avatar_url", "avatar_url_template",
    "badge", "badge_v2", "user_type", "is_org", "is_advertiser",
}


def _strip_html_from_text(text: str) -> str:
    if not re.search(r'<[^>]+>', text):
        return text
    return re.sub(r'<[^>]+>', '', text)


def _dedup_texts(texts: list[str]) -> list[str]:
    if len(texts) <= 1:
        return texts
    result: list[str] = []
    for t in texts:
        tn = re.sub(r'\s+', '', t)
        if len(tn) < 8:
            continue
        dup = False
        for i, existing in enumerate(result):
            en = re.sub(r'\s+', '', existing)
            if tn == en:
                dup = True
                break
            if len(tn) > len(en) and en in tn:
                result[i] = t
                dup = True
                break
            if len(en) >= len(tn) and tn in en:
                dup = True
                break
        if not dup:
            result.append(t)
    return result


def _extract_text_from_json(data, text_only: bool = True) -> str:

    def _walk(obj, depth=0):
        if depth > 20:
            return
        if isinstance(obj, str):
            if len(obj.strip()) >= 8:
                yield _strip_html_from_text(obj)
        elif isinstance(obj, dict):
            extracted_any = False
            for key in _JSON_TEXT_KEYS:
                if key in obj:
                    val = obj[key]
                    if isinstance(val, str) and len(val.strip()) >= 8:
                        extracted_any = True
                        yield _strip_html_from_text(val)
                    elif isinstance(val, list):
                        extracted_any = True
                        for item in val:
                            yield from _walk(item, depth + 1)
                    elif isinstance(val, dict):
                        extracted_any = True
                        yield from _walk(val, depth + 1)
            if not extracted_any:
                for key, val in obj.items():
                    if key not in _JSON_SKIP_KEYS:
                        yield from _walk(val, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                yield from _walk(item, depth + 1)

    texts = _dedup_texts(list(_walk(data)))
    if not texts:
        return ""
    return "\n\n".join(texts)


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


_VOID_TAGS = {"meta", "link", "br", "hr", "img", "input", "area", "base", "col",
              "embed", "param", "source", "track", "wbr"}


class _TextExtractor(HTMLParser):
    def __init__(self, text_only: bool = False):
        super().__init__()
        self._text_chunks: list[str] = []
        self._skip_tags = {"script", "style", "noscript", "head", "meta", "link", "title"}
        self._skip_depth = 0
        self._text_only = text_only
        self._in_link = False
        self._link_href = ""
        self._link_text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            if tag in _VOID_TAGS:
                return
            self._skip_depth += 1
            return
        if tag == "a" and self._skip_depth == 0:
            if not self._text_only:
                attrs_dict = dict(attrs)
                self._link_href = attrs_dict.get("href", "").strip()
            self._in_link = True
            self._link_text_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            if tag in _VOID_TAGS:
                return
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "a":
            if self._skip_depth == 0 and self._link_href:
                link_text = " ".join(self._link_text_chunks).strip()
                if link_text:
                    self._text_chunks.append(f"[{link_text}]({self._link_href})")
                else:
                    self._text_chunks.append(f"<{self._link_href}>")
            elif self._skip_depth == 0:
                self._text_chunks.extend(self._link_text_chunks)
            self._in_link = False
            self._link_href = ""
            self._link_text_chunks = []
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
            if self._in_link:
                self._link_text_chunks.append(stripped)
            else:
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


COMMENT_SELECTORS = [
    '//*[@id="comments"]',
    '//*[contains(@class, "comments-area")]',
    '//*[contains(@class, "comment-list")]',
    '//*[contains(@class, "comment-section")]',
    '//section[contains(@class, "comments")]',
    '//div[contains(@class, "comments")]',
    '//*[@id="disqus_thread"]',
]


def _extract_page_metadata(tree) -> str:
    meta_parts: list[str] = []

    title_els = tree.xpath('//title')
    title_text = ""
    if title_els:
        title_text = (title_els[0].text or "").strip()
        if title_text:
            meta_parts.append(f"**{_WebpageLocale.get('meta_title')}**: {title_text}")

    desc_els = tree.xpath('//meta[@name="description"]/@content')
    if desc_els and desc_els[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_description')}**: {desc_els[0].strip()}")

    kw_els = tree.xpath('//meta[@name="keywords"]/@content')
    if kw_els and kw_els[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_keywords')}**: {kw_els[0].strip()}")

    og_title = tree.xpath('//meta[@property="og:title"]/@content')
    if og_title and og_title[0].strip() and og_title[0].strip() != title_text:
        meta_parts.append(f"**{_WebpageLocale.get('meta_og_title')}**: {og_title[0].strip()}")

    og_desc = tree.xpath('//meta[@property="og:description"]/@content')
    if og_desc and og_desc[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_og_description')}**: {og_desc[0].strip()}")

    og_site = tree.xpath('//meta[@property="og:site_name"]/@content')
    if og_site and og_site[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_site')}**: {og_site[0].strip()}")

    og_type = tree.xpath('//meta[@property="og:type"]/@content')
    if og_type and og_type[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_type')}**: {og_type[0].strip()}")

    pub_time = tree.xpath('//meta[@property="article:published_time"]/@content')
    if pub_time and pub_time[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_published')}**: {pub_time[0].strip()}")

    mod_time = tree.xpath('//meta[@property="article:modified_time"]/@content')
    if mod_time and mod_time[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_modified')}**: {mod_time[0].strip()}")

    author_els = tree.xpath(
        '//meta[@property="article:author"]/@content | //meta[@name="author"]/@content'
    )
    if author_els and author_els[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_author')}**: {author_els[0].strip()}")

    canonical = tree.xpath('//link[@rel="canonical"]/@href')
    if canonical and canonical[0].strip():
        meta_parts.append(f"**{_WebpageLocale.get('meta_canonical')}**: {canonical[0].strip()}")

    time_els = tree.xpath('//time[@datetime]')
    for t in time_els[:3]:
        dt = (t.get("datetime") or "").strip()
        txt = "".join(t.itertext()).strip()
        if dt:
            meta_parts.append(f"**{_WebpageLocale.get('meta_time')}**: {txt} ({dt})" if txt else
                            f"**{_WebpageLocale.get('meta_time')}**: {dt}")

    if meta_parts:
        return "\n".join(meta_parts) + "\n\n---\n\n"
    return ""


def _extract_comments(tree, main_container) -> str:
    for selector in COMMENT_SELECTORS:
        elements = tree.xpath(selector)
        for el in elements:
            if main_container is not None:
                if el is main_container:
                    continue
                try:
                    is_inside_main = False
                    for ancestor in el.iterancestors():
                        if ancestor is main_container:
                            is_inside_main = True
                            break
                    if is_inside_main:
                        continue
                except Exception:
                    pass
            text = _tree_to_text(el)
            if len(text.strip()) > 50:
                return f"## {_WebpageLocale.get('meta_comments_header')}\n\n{text.strip()}"
    return ""


def _lxml_extract_text(html_text: str, text_only: bool = False) -> str:
    try:
        tree = lxml_html.fromstring(html_text)
    except ParseError:
        return ""

    container = _find_content_container(tree)

    if container is None:
        body = tree.xpath('//body')
        container = body[0] if body else tree

    text = _tree_to_text(container, text_only=text_only)

    if not text_only:
        comment_text = _extract_comments(tree, container)
        if comment_text:
            text = text.strip() + "\n\n---\n\n" + comment_text

        metadata = _extract_page_metadata(tree)
        if metadata:
            text = metadata + text

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


def _tree_to_text(node, text_only: bool = False) -> str:
    parts = []
    _walk(node, parts, text_only)
    raw = "".join(parts)
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _walk(node, parts, text_only: bool = False):
    if node.tag in ("script", "style", "noscript", "head", "meta", "link", "title", "nav"):
        return
    if node.tag == "a":
        if text_only:
            link_text = "".join(node.itertext()).strip()
            if link_text:
                parts.append(link_text)
        else:
            href = (node.get("href") or "").strip()
            link_text = "".join(node.itertext()).strip()
            if href:
                if link_text:
                    parts.append(f"[{link_text}]({href})")
                else:
                    parts.append(f"<{href}>")
            elif link_text:
                parts.append(link_text)
        tail = (node.tail or "").strip()
        if tail and node.tag not in ("html", "body"):
            parts.append(tail)
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
        _walk(child, parts, text_only)
    if node.tag in HEADER_TAGS:
        parts.append("\n")
    tail = (node.tail or "").strip()
    if tail and node.tag not in ("html", "body"):
        parts.append(tail)


def _extract_text_from_html(html: str, text_only: bool = False) -> str:
    text = _lxml_extract_text(html, text_only=text_only)
    if text:
        return text

    extractor = _TextExtractor(text_only=text_only)
    try:
        extractor.feed(html)
    except Exception:
        return html
    raw = extractor.get_text()
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


async def _fetch_via_browser_fallback(url, timeout, text_only, max_tokens, raw_html="", extracted_text="", force=True):
    """Try to fetch via Patchright browser. Returns result string or None."""
    try:
        from _playwright_fallback import playwright_fallback_fetch
        browser_text = await playwright_fallback_fetch(
            url=url,
            raw_html=raw_html,
            extracted_text=extracted_text,
            timeout=timeout,
            text_only=text_only,
            force=force,
        )
        if not browser_text:
            return None
        tokens = _count_tokens(browser_text)
        if tokens < 4000:
            result = _WebpageLocale.get("success_header", code=200)
            result += f"\n\n{browser_text}"
            return result
        if tokens <= max_tokens:
            result = _WebpageLocale.get("token_length", tokens=tokens, length=len(browser_text))
            result += f"\n\n{browser_text}"
            return result
        truncated_text = _truncate_by_tokens(browser_text, max_tokens)
        truncated_tokens = _count_tokens(truncated_text)
        pct = round((1 - truncated_tokens / tokens) * 100)
        result = _WebpageLocale.get("token_truncated", tokens=truncated_tokens, pct=pct, length=len(truncated_text))
        result += f"\n\n{truncated_text}"
        return result
    except Exception:
        return None


async def fetch_webpage(
    url: str,
    timeout: int = 15,
    max_tokens: int = 15000,
    text_only: bool = True,
) -> str:
    """
    Open an HTTP/HTTPS webpage and return the plain text content.

    Args:
        url: The webpage URL (must start with http:// or https://)
        timeout: Request timeout in seconds (default 15)
        max_tokens: Maximum tokens to return (default 15000)
        text_only: If True, return plain text only (no metadata, links, comments).
                   If False, include page metadata, preserve URLs in markdown format,
                   and extract comment sections. (default True)

    Returns:
        Extracted plain text content with source URL and status information.
    """
    if not url.startswith(("http://", "https://")):
        return _WebpageLocale.get("invalid_url", url=url)

    _BROWSER_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    _IMPERSONATE_CHAIN = ["chrome131", "chrome124", "firefox133"]

    last_error = ""
    raw_body = ""
    resp_status = 0

    for impersonate_target in _IMPERSONATE_CHAIN:
        try:
            async with curl_requests.AsyncSession() as session:
                resp = await session.get(
                    url,
                    impersonate=impersonate_target,
                    headers=_BROWSER_HEADERS,
                    timeout=timeout,
                )
                resp_status = resp.status_code
                raw_body = resp.text

                if resp_status == 403:
                    # 403 → immediately try browser fallback
                    last_error = _WebpageLocale.get(
                        "403_browser_fallback",
                        url=url,
                    )
                    browser_text = await _fetch_via_browser_fallback(url, timeout, text_only, max_tokens, raw_html=raw_body, force=True)
                    if browser_text:
                        return browser_text
                    # browser fallback failed, try next impersonate
                    continue

                if resp_status != 200:
                    return _WebpageLocale.get(
                        "status_not_200",
                        url=url,
                        code=resp_status,
                        reason=resp.reason,
                    )

                break

        except curl_requests.RequestsError as e:
            last_error = str(e)
            continue
        except asyncio.TimeoutError:
            return _WebpageLocale.get("timeout", timeout=timeout)

    else:
        # All impersonate + browser fallback attempts failed
        if last_error:
            return last_error
        return _WebpageLocale.get("parse_error", type="403", message="all attempts failed")

    content_type = resp.headers.get("Content-Type", "")
    is_json = "application/json" in content_type
    if not is_json and "text/html" not in content_type and "text/plain" not in content_type:
        return _WebpageLocale.get("unsupported_content_type",
                                  url=url, code=resp_status, type=content_type)

    if is_json:
        try:
            data = json.loads(raw_body)
            text = _extract_text_from_json(data, text_only=text_only)
        except json.JSONDecodeError:
            text = raw_body
    else:
        text = _extract_text_from_html(raw_body, text_only=text_only)

    # --- Patchright / Playwright fallback for SPA pages or empty results ---
    _needs_fallback = (not text) and ("text/html" in content_type)
    if _needs_fallback:
        browser_result = await _fetch_via_browser_fallback(url, timeout, text_only, max_tokens, raw_html=raw_body, extracted_text=text or "", force=False)
        if browser_result:
            return browser_result

    if not text:
        return _WebpageLocale.get("empty_content", url=url, code=resp_status)

    if text_only:
        text = _clean_reference_noise(text)

    tokens = _count_tokens(text)

    if tokens < 4000:
        result = _WebpageLocale.get("success_header", code=resp_status)
        result += f"\n\n{text}"
        return result

    if tokens <= max_tokens:
        result = _WebpageLocale.get("token_length", tokens=tokens, length=len(text))
        result += f"\n\n{text}"
        return result

    truncated_text = _truncate_by_tokens(text, max_tokens)
    truncated_tokens = _count_tokens(truncated_text)
    pct = round((1 - truncated_tokens / tokens) * 100)

    result = _WebpageLocale.get("token_truncated",
                                 tokens=truncated_tokens,
                                 pct=pct,
                                 length=len(truncated_text))
    result += f"\n\n{truncated_text}"
    return result



