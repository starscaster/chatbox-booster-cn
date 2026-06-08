#Example file from https://github.com/jlowin/fastmcp
import os
import asyncio
import xml.etree.ElementTree as ET
from typing import List, Optional
from datetime import datetime
import io
import sys
import json
import subprocess
from pathlib import Path

# ----- 依赖检查 -----
from _dep_checker import ensure_deps
ensure_deps({
    "aiohttp": "aiohttp",
    "fastmcp": "fastmcp",
    "pypdf": "pypdf",
    "ddgs": "ddgs",
})

import aiohttp
from fastmcp import FastMCP
from pypdf import PdfReader
from ddgs import DDGS
from ddgs_quality_evaluator import _evaluate_ddgs_quality, _ai_evaluate_quality
from MCPtool_0427 import fetch_webpage

_SCRIPT_DIR = Path(__file__).resolve().parent


def _detect_locale() -> str:
    try:
        import ctypes
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lang_id & 0x3FF) == 4:
            return "zh"
        return "en"
    except Exception:
        return "en"


def _load_locale_section(locale: str, section: str) -> dict:
    path = _SCRIPT_DIR / "locale" / f"{locale}.json"
    if not path.exists():
        path = _SCRIPT_DIR / "locale" / "en.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(section, {})


_locale_code = _detect_locale()
_SERVER_LOCALE = _load_locale_section(_locale_code, "server")
_SERPER_LOCALE = _load_locale_section(_locale_code, "search_serper")
_ARXIV_LOCALE = _load_locale_section(_locale_code, "search_arxiv")
_PDF_LOCALE = _load_locale_section(_locale_code, "search_pdf")
_DDGS_LOCALE = _load_locale_section(_locale_code, "search_ddgs")


def _load_config():
    path = _SCRIPT_DIR / "config.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_CONFIG = _load_config()


def _get_proxy(key: str) -> str:
    env_key = f"PROXY_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val if env_val.strip().lower() not in ("", "none", "null", "false") else None
    val = _CONFIG.get("proxy", {}).get(key)
    return val if val else None


def _fmt(d: dict, key: str, **kwargs) -> str:
    text = d.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    return text

# 初始化FastMCP实例
mcp = FastMCP("MySearchServer")

# ----- 工具 1: 通用联网搜索 (使用 Serper API) -----
@mcp.tool(output_schema=None)
async def Serper_web_search(query: str, max_results: int = 5) -> str:
    """
    需要配置API，备用引擎。

    参数:
        query: keyword
        max_results: 返回最大结果数 (default 5)
    """
    api_key = os.getenv("SERPER_API_KEY") or _CONFIG.get("api", {}).get("serper", {}).get("api_key")
    if not api_key:
        return _fmt(_SERPER_LOCALE, "missing_key")

    url = os.getenv("SERPER_API_URL") or _CONFIG.get("api", {}).get("serper", {}).get("url", "https://google.serper.dev/search")
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    payload = {
        "q": query,
        "num": max_results
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    return _fmt(_SERPER_LOCALE, "request_failed", code=resp.status)
                data = await resp.json()

                if "organic" not in data:
                    return _fmt(_SERPER_LOCALE, "no_results")

                results = []
                for item in data.get("organic", [])[:max_results]:
                    title = item.get("title", _fmt(_SERPER_LOCALE, "no_title"))
                    link = item.get("link", "#")
                    snippet = item.get("snippet", _fmt(_SERPER_LOCALE, "no_snippet"))
                    results.append(f"### {title}\n{_fmt(_SERPER_LOCALE, 'link_label', link=link)}\n{snippet}\n")

                return f"{_fmt(_SERPER_LOCALE, 'header')}\n" + "\n".join(results) if results else _fmt(_SERPER_LOCALE, "no_results")
    except Exception as e:
        return _fmt(_SERPER_LOCALE, "error", error=str(e))

# ----- 工具2: arXiv学术论文搜索 -----
@mcp.tool()
async def arxiv_search(query: str, max_results: int = 5) -> str:
    """
    学术搜索专用工具，搜索学术论文。

    参数:
        query: 搜索关键词
        max_results: 返回最大结果数 (default 5)
    """
    from urllib.parse import quote
    import aiohttp
    import xml.etree.ElementTree as ET

    # arXiv API 请求 — 使用 HTTPS 避免代理劫持
    url = "https://export.arxiv.org/api/query"

    # arXiv API 查询格式要求：关键词需要 URL 编码，且建议加 all: 前缀
    search_query = f"all:{query}"
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending"
    }

    # 设置超时，避免代理拖死连接
    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params, proxy=None) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return _fmt(_ARXIV_LOCALE, "request_failed", code=resp.status, response=body[:200])

                data = await resp.text()

                if not data.strip().startswith("<?xml") and not data.strip().startswith("<feed"):
                    return _fmt(_ARXIV_LOCALE, "non_xml_content", content=data[:200])

                import re
                data = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', data)

                root = ET.fromstring(data)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}

                results = []
                for entry in root.findall('atom:entry', ns)[:max_results]:
                    title_el = entry.find('atom:title', ns)
                    title = title_el.text.strip() if title_el is not None and title_el.text else _fmt(_ARXIV_LOCALE, "no_title")

                    summary_el = entry.find('atom:summary', ns)
                    summary = summary_el.text.strip() if summary_el is not None and summary_el.text else _fmt(_ARXIV_LOCALE, "no_summary")

                    link_el = entry.find('atom:id', ns)
                    link = link_el.text.strip() if link_el is not None and link_el.text else "#"

                    published_el = entry.find('atom:published', ns)
                    published = published_el.text.strip() if published_el is not None and published_el.text else _fmt(_ARXIV_LOCALE, "no_date")

                    summary = ' '.join(summary.split())

                    results.append(
                        f"### {title}\n"
                        f"{_fmt(_ARXIV_LOCALE, 'date_label', date=published)}\n"
                        f"{_fmt(_ARXIV_LOCALE, 'link_label', link=link)}\n"
                        f"{_fmt(_ARXIV_LOCALE, 'summary_label', summary=summary[:200])}...\n"
                    )

                return f"{_fmt(_ARXIV_LOCALE, 'header')}\n" + "\n".join(results) if results else _fmt(_ARXIV_LOCALE, "no_results")

    except ET.ParseError as e:
        return _fmt(_ARXIV_LOCALE, "xml_parse_error", error=str(e), content=data[:300] if 'data' in dir() else 'N/A')
    except Exception as e:
        return _fmt(_ARXIV_LOCALE, "error", error=str(e))

# ----- 工具 3: PDF 解释器 -----
@mcp.tool()
async def pdf_reader(pdf_url: str, max_pages: int = 5, timeout: int = 30) -> str:
    """
    从 URL 下载并解析 PDF 文件，提取文本内容。
    适用于 arXiv 论文 PDF 链接或其他公开 PDF 文档。

    参数:
        pdf_url: PDF 文件的 URL 链接
        max_pages: 最大读取页数 (默认 5，避免返回过多内容和超时)
        timeout: 下载超时时间 (秒，默认 30 秒)
    """
    try:
        import asyncio
        from asyncio import TimeoutError
        
        async with asyncio.timeout(timeout):
            async with aiohttp.ClientSession() as session:
                async with session.get(pdf_url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status != 200:
                        return _fmt(_PDF_LOCALE, "download_failed", code=resp.status)
                    
                    pdf_data = await resp.read()
                    
                    pdf_file = io.BytesIO(pdf_data)
                    reader = PdfReader(pdf_file)
                    
                    total_pages = len(reader.pages)
                    pages_to_read = min(max_pages, total_pages)
                    
                    extracted_text = []
                    for i in range(pages_to_read):
                        page = reader.pages[i]
                        text = page.extract_text()
                        if text:
                            extracted_text.append(f"--- {_fmt(_PDF_LOCALE, 'page_label', num=i+1)} ---\n{text.strip()}")
                    
                    if not extracted_text:
                        return _fmt(_PDF_LOCALE, "no_text")
                    
                    result = f"{_fmt(_PDF_LOCALE, 'header')}\n"
                    result += f"**{_fmt(_PDF_LOCALE, 'total_pages', count=total_pages)}**\n"
                    result += f"**{_fmt(_PDF_LOCALE, 'pages_read', count=pages_to_read)}**\n\n"
                    result += "\n\n".join(extracted_text)
                    
                    return result
    except asyncio.TimeoutError:
        return _fmt(_PDF_LOCALE, "timeout", timeout=timeout)
    except aiohttp.ClientError as e:
        return _fmt(_PDF_LOCALE, "network_error", error=str(e))
    except Exception as e:
        return _fmt(_PDF_LOCALE, "error", error=str(e))
 




@mcp.tool(output_schema=None)
async def DDGS_web_search_V20(
    query: str, 
    max_results: int = 5,
    region: str = "wt-wt",
    min_quality_score: float = 30.0,
    ai_evaluate: bool = False,
    intent: str = ""
) -> str:
    """
    DuckDuckGo网络搜索,覆盖面更广,智能排序结果。

    参数:
        query: keyword
        max_results: 返回最大结果数 (default 5)
        region: 搜索地域-语言代码，格式 {国家}-{语言} (recommend default wt-wt 无限制/引擎自动匹配)    
                限制地区/语言（限制地区可能导致代理不稳定）:
        min_quality_score: 过滤低于此分数的结果 (default 30,when ai_evaluate = True,该值不生效)
        ai_evaluate: 是否启用LLM模型进行内容筛选 (default False),涉及专业性较强的问题时建议开启
        intent: ai_evaluate为True时,评估时的侧重点描述,区别于query的搜索关键词,告知assistant你希望获取什么或过滤什么。例如"需要技术教程"vs"需要学术论文","经典著作"vs"最新咨询","权威来源优先"or"识别可能的假新闻"
    """
    def _search_sync():
        """同步搜索函数，将在异步上下文中通过线程池执行"""
        ddgs_proxy = _get_proxy("ddgs")
        with DDGS(proxy=ddgs_proxy) as ddgs:
            results = list(ddgs.text(
                query=query,
                region=region,
                safesearch="off",
                max_results=actual_request_count
            ))
            return results

    try:
        # 实际请求数量为 max_results * 2 + 2，为质量筛选留出余量
        actual_request_count = max_results * 2 + 2
        
        # 使用asyncio.to_thread在线程池中执行同步的DuckDuckGo搜索
        # 避免阻塞事件循环
        results = await asyncio.to_thread(_search_sync)

        if not results:
            return _fmt(_DDGS_LOCALE, "no_results")

        overall_assessment = ""
        if ai_evaluate:
            min_quality_score = 50.0
            ai_scored, overall_assessment = _ai_evaluate_quality(results, query, intent)
            scored_results = []
            for s in ai_scored:
                if s['index'] < len(results):
                    item = results[s['index']]
                    scored_results.append({
                        'title': item.get("title", _fmt(_DDGS_LOCALE, "no_title")),
                        'link': item.get("href", "#"),
                        'body': item.get("body", _fmt(_DDGS_LOCALE, "no_body")),
                        'quality_score': float(s['quality_score']),
                        'result_type': s['result_type'],
                        'original_index': s['index']
                    })
        else:
            scored_results = []
            for i, item in enumerate(results):
                title = item.get("title", _fmt(_DDGS_LOCALE, "no_title"))
                link = item.get("href", "#")
                body = item.get("body", _fmt(_DDGS_LOCALE, "no_body"))
                quality_score, result_type = _evaluate_ddgs_quality(title, body, link, query)
                scored_results.append({
                    'title': title,
                    'link': link,
                    'body': body,
                    'quality_score': float(quality_score),
                    'result_type': result_type,
                    'original_index': i
                })
        
        # 按质量分数降序排序
        scored_results.sort(key=lambda x: x['quality_score'], reverse=True)
        
        # 过滤掉低于最低质量分数的结果
        qualified_results = [r for r in scored_results if r['quality_score'] > min_quality_score]
        
        # 保留前 max_results 个结果
        top_results = qualified_results[:max_results]
        
        formatted_results = []
        for idx, item in enumerate(top_results, 1):
            type_label = _fmt(_DDGS_LOCALE, "type_specific") if item['result_type'] == "specific_article" else _fmt(_DDGS_LOCALE, "type_aggregation")

            if item['quality_score'] >= 80.0:
                quality_badge = _fmt(_DDGS_LOCALE, "quality_label_high")
            elif item['quality_score'] >= 60.0:
                quality_badge = _fmt(_DDGS_LOCALE, "quality_label_medium")
            else:
                quality_badge = _fmt(_DDGS_LOCALE, "quality_label_low")

            formatted_results.append(
                f"### {idx}. [{item['original_index']}] {item['title']}\n"
                f"{quality_badge} | {type_label} | {_fmt(_DDGS_LOCALE, 'quality_score')}: {item['quality_score']}\n"
                f"{_fmt(_DDGS_LOCALE, 'link_label', link=item['link'])}\n"
                f"{item['body']}\n"
            )

        if not formatted_results:
            return overall_assessment + _fmt(_DDGS_LOCALE, "no_qualified")

        output = f"{_fmt(_DDGS_LOCALE, 'header')}\n"
        if ai_evaluate and overall_assessment:
            output += f"> **{_fmt(_DDGS_LOCALE, 'overall_label')}**：{overall_assessment}\n\n"
        output += f"*{_fmt(_DDGS_LOCALE, 'filter_summary', total=len(results), top=len(top_results))}*\n\n"
        output += "\n".join(formatted_results)

        return output
    except Exception as e:
        return _fmt(_DDGS_LOCALE, "error", error=str(e))

# ----- 工具 5: HTTP 网页抓取 (fetch URL content) -----
@mcp.tool(output_schema=None)
async def fetch_webpage_tool(
    url: str,
    timeout: int = 20,
    max_tokens: int = 15000,
    text_only: bool = True,
) -> str:
    """
    V4.1
    打开并抓取 HTTP/HTTPS 网页内容。

    参数:
        url: 网页 URL（必须以 http:// 或 https:// 开头）
        text_only: 是否仅返回纯文本。True=仅正文；False=保留时间作者等元信息、链接和评论区（默认 True）。
    """
    return await fetch_webpage(url, timeout=timeout, max_tokens=max_tokens, text_only=text_only)

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


@mcp.tool(output_schema=None)
def get_date() -> str:
    """
    调用此工具确认当前系统时间，避免提供过时信息
    校准时间后再进行操作
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



import re


def _parse_inquiry_items(inquiries: str, inquiry_type: str):
    """
    从 inquiries 中拆分问题列表和选项。

    自动识别以下格式：
      1️⃣ **业务类型**：实物商品/虚拟商品/其他？
      1. 你的小程序主要卖什么？
      1) 问题文本
      1、问题文本
      A. 选项X  B. 选项Y  C. 选项Z

    示例:
      inquiries="1. 产品类型？\n2. 预算？\n3. 时间？"
      → [{"q":"产品类型？"},{"q":"预算？"},{"q":"时间？"}]

      inquiries="1. 类型？A.实物 B.虚拟 C.服务"
      inquiry_type="multiple_options"
      → [{"q":"类型？","options":["实物","虚拟","服务"]}]
    """
    text = inquiries.strip()
    q_pattern = re.compile(
        r'(?:^|\n)\s*(?:\d+[️⃣⑩❶❷❸❹❺❻❼❽❾❿]'
        r'|\d+\s*[\.、．。)]\s*'
        r'|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*'
        r'|[A-Fa-f]\s*[\.、．)]\s*)'
        r'(?:\*{1,2}(.+?)\*{1,2}|(.+?))'
        r'(?=(?:\n\s*(?:\d+[️⃣⑩❶❷❸❹❺❻❼❽❾❿]'
        r'|\d+\s*[\.、．。)]'
        r'|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]'
        r'|[A-Fa-f]\s*[\.、．)])|\n\s*$|\Z))',
        re.DOTALL,
    )

    raw = q_pattern.findall(text)
    if not raw:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) <= 1:
            return [{"q": text.strip()}]
        items = []
        for line in lines:
            line = re.sub(r'^[#\-\*\s]+', '', line).strip()
            line = re.sub(r'\*{1,2}', '', line)
            if line:
                items.append({"q": line})
        return items or [{"q": text.strip()}]

    items = []
    for bold, plain in raw:
        q = (bold or plain).strip()
        q = re.sub(r'^[：:]\s*', '', q)
        q = re.sub(r'\s+', ' ', q)
        items.append({"q": q})

    if inquiry_type == "multiple_options":
        opt_pattern = re.compile(r'[A-Fa-f]\s*[\.、．)]\s*(.+?)(?=\s*[A-Fa-f]\s*[\.、．)]|\s*$)', re.DOTALL)
        for item in items:
            opts = opt_pattern.findall(item["q"])
            opts = [o.strip().rstrip(";；,") for o in opts if o.strip()]
            if opts:
                main = re.sub(r'[A-Fa-f]\s*[\.、．)].*$', '', item["q"]).strip()
                main = re.sub(r'[：:]\s*$', '', main)
                item["q"] = main
                item["options"] = opts

    return items


def _invoke_dialog(extra_args: list[str], timeout: int) -> str:
    L = _SERVER_LOCALE
    dialog_script = _SCRIPT_DIR / "approve_dialog.py"
    if not dialog_script.exists():
        return L["script_missing"]

    locale = _detect_locale()
    cmd = [sys.executable, str(dialog_script), "--locale", locale] + extra_args

    popen_env = os.environ.copy()
    popen_env.setdefault("PYTHONIOENCODING", "utf-8")
    popen_kwargs = {
        "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE, "text": True, "encoding": "utf-8",
        "errors": "replace", "env": popen_env,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except Exception as exc:
        return L["start_failed"].format(error=exc)

    try:
        out, err = proc.communicate(timeout=(timeout + 15) if timeout > 0 else None)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.communicate()
        return L["timeout"].format(timeout=timeout)

    output = (out or "").strip()
    if not output:
        return L["no_response"].format(stderr=(err or "").strip() or L["no_output"])

    try:
        payload = json.loads(output.splitlines()[-1])
    except json.JSONDecodeError:
        return L["invalid_response"].format(raw=output[:200])

    status = payload.get("status")
    value = str(payload.get("value", "")).strip()
    if status == "timeout":  return L["timeout"].format(timeout=timeout)
    if status == "cancelled": return L["cancelled"]
    if status == "selected":  return L["selected"].format(value=value) if value else L["confirmed"]
    if status == "submitted": return value if value else L["submitted_empty"]
    if status == "error":     return L["dialog_error"].format(value=(value or L["unknown"]))
    return L["unknown"].format(data=payload)


@mcp.tool(output_schema=None)
def interactive_dialog_UA(title: str, message: str, timeout: int = 120, default: str = "否") -> str:
    """
    用户审批对话框。三个按钮：是 / 否 / 取消。
    用于敏感操作确认。

    参数:
        title: 窗口标题
        message: 提示内容
        timeout: 超时秒数（默认 120）
        default: 默认聚焦按钮（默认 "否"）
    """
    return _invoke_dialog(["--type", "ua", "--title", title, "--message", message,
                           "--timeout", str(timeout), "--default", default], timeout)


@mcp.tool(output_schema=None)
def interactive_dialog_input(title: str, message: str, timeout: int = 120, default: str = "") -> str:
    """
    单行文本输入对话框。
    用于向用户征集简短信息。

    参数:
        title: 窗口标题
        message: 提示内容
        timeout: 超时秒数（默认 120）
        default: 预填文本（默认空）
    """
    return _invoke_dialog(["--type", "input", "--title", title, "--message", message,
                           "--timeout", str(timeout), "--default", default], timeout)


@mcp.tool(output_schema=None)
def interactive_dialog_inquiry(
    title: str,
    message: str,
    timeout: int = 300,
    default: str = "",
    inquires: str = "",
    inquiry_type: str = "single_question",
    other: str = "disable",
    remarks: str = "disable"
) -> str:
    """
    结构化信息征集表单。
    自动从 inquires 中拆分问题（可选拆选项），生成问答表单。

    参数:
        title: 窗口标题
        message: 提示信息说明（仅展示，不参与问题拆分）
        timeout: 超时秒数（默认 300）
        default: 预填默认值（仅对第一题生效）
        inquires: 问题描述。支持以下格式自动拆分：
                 · "1. 问题A？\n2. 问题B？\n3. 问题C？"
                 · "1️⃣ **标签**：描述内容"
                 · "A. 选项X  B. 选项Y  C. 选项Z"（需 inquiry_type=multiple_options）
                 留空时回退使用 message 拆分
        inquiry_type: single_question=单题 / multiple_question=多题 / multiple_options=多选项
        other: enable=每题追加"其他"输入框
        remarks: enable=末尾追加备注输入框

    示例:
        inquiry_type="multiple_question", inquires="1. 产品类型？\n2. 预算？\n3. 上线时间？"
    """
    source = inquires or message
    questions = _parse_inquiry_items(source, inquiry_type)
    questions_json = json.dumps(questions, ensure_ascii=False)
    return _invoke_dialog(["--type", "inquiry", "--title", title, "--message", message,
                           "--timeout", str(timeout), "--default", default,
                           "--questions", questions_json, "--other", other,
                           "--remarks", remarks, "--inquiry_type", inquiry_type], timeout)


if __name__ == "__main__":
    mcp.run()
