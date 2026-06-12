"""
Bilibili WBI 签名 & 评论 API 模块。

提供：
  - bv2av()          BV 号 → AV 号 (oid) 转换
  - extract_bv()     从 URL 提取 BV 号
  - fetch_comments() 通过 WBI 签名接口获取评论区数据（纯文本，无需 Cookie）
"""
import asyncio
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import quote

from _dep_checker import ensure_deps

ensure_deps({
    "aiohttp": "aiohttp",
})

import aiohttp

# --- WBI 签名常量 ---

MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

# --- WBI 密钥缓存 ---
_wbi_keys: tuple[str, str] | None = None
_wbi_keys_ts: float = 0.0

_WBI_CACHE_TTL = 3600  # 1 小时


async def _get_wbi_keys(session: aiohttp.ClientSession) -> tuple[str, str]:
    """获取每日轮换的 WBI 密钥对 (img_key, sub_key)。无需登录。"""
    global _wbi_keys, _wbi_keys_ts
    now = time.time()
    if _wbi_keys and (now - _wbi_keys_ts) < _WBI_CACHE_TTL:
        return _wbi_keys

    try:
        async with session.get(
            "https://api.bilibili.com/x/web-interface/nav",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
    except Exception:
        if _wbi_keys:
            return _wbi_keys  # 使用过期缓存兜底
        return "", ""

    wbi_img = data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")

    try:
        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    except Exception:
        if _wbi_keys:
            return _wbi_keys
        return "", ""

    _wbi_keys = (img_key, sub_key)
    _wbi_keys_ts = now
    return _wbi_keys


def _gen_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _sign_params(params: dict, mixin_key: str) -> tuple[str, int]:
    """对请求参数做 WBI 签名，返回 (w_rid, wts)。"""
    wts = int(time.time())
    params = {**params, "wts": str(wts)}

    parts = []
    for k in sorted(params.keys()):
        v = str(params[k])
        v = re.sub(r"[!'()*]", "", v)
        parts.append(f"{quote(k, safe='')}={quote(v, safe='')}")

    query = "&".join(parts)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return w_rid, wts


# --- BV → oid 获取 ---

_VIEW_API = "https://api.bilibili.com/x/web-interface/view"


def extract_bv(url: str) -> str | None:
    m = re.search(r"/video/(BV[a-zA-Z0-9]+)", url)
    if m:
        return m.group(1)
    # 也支持 /BVxxx 短链
    m = re.search(r"/(BV[a-zA-Z0-9]{10})", url)
    if m:
        return m.group(1)
    return None


async def _get_oid(session: aiohttp.ClientSession, bv: str, timeout: int = 10) -> str:
    """通过 /x/web-interface/view API 获取视频的 aid（oid）。"""
    try:
        async with session.get(
            f"{_VIEW_API}?bvid={bv}",
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json(content_type=None)
            aid = data.get("data", {}).get("aid")
            if aid:
                return str(aid)
            return ""
    except Exception:
        return ""


# --- 评论获取 ---

_COMMENT_API = "https://api.bilibili.com/x/v2/reply/wbi/main"


async def fetch_comments(
    url: str,
    max_replies: int = 20,
    timeout: int = 10,
) -> str:
    """
    通过 WBI 签名接口获取 B 站视频评论区纯文本。

    Args:
        url:           视频页面 URL（含 BV 号）
        max_replies:   最多获取多少条一级评论
        timeout:       请求超时秒数

    Returns:
        格式化的评论文本；失败或没有评论时返回空字符串。
    """
    bv = extract_bv(url)
    if not bv:
        return ""

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.bilibili.com/",
    }

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        oid = await _get_oid(session, bv, timeout=timeout)
        if not oid:
            return ""

        img_key, sub_key = await _get_wbi_keys(session)
        if not img_key or not sub_key:
            return ""

        mixin_key = _gen_mixin_key(img_key, sub_key)

        params = {
            "oid": str(oid),
            "type": "1",
            "mode": "3",
            "pagination_str": '{"offset":""}',
            "plat": "1",
            "web_location": "1315875",
        }

        w_rid, wts = _sign_params(params, mixin_key)
        params["w_rid"] = w_rid
        params["wts"] = str(wts)

        try:
            async with session.get(
                _COMMENT_API,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()
        except Exception:
            return ""

        if data.get("code") != 0:
            return ""

        result = data.get("data", {})
        replies = result.get("replies") or []

        if not replies:
            return ""

        lines = []
        for reply in replies[:max_replies]:
            member = reply.get("member", {})
            uname = member.get("uname", "匿名")
            content = reply.get("content", {}).get("message", "")
            like = reply.get("like", 0)
            ctime = reply.get("ctime", 0)
            time_str = (
                datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M")
                if ctime else ""
            )

            if not content.strip():
                continue

            line = f"{uname}（{like}赞 {time_str}）：{content}"
            lines.append(line)

        if not lines:
            return ""

        return "## 评论区\n\n" + "\n\n".join(lines)
