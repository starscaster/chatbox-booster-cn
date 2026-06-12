"""
站点级内容提取规则模块。

每个站点在 SITE_RULES 中注册一个条目，字段说明：
  content_selectors   - 主内容区域定位（优先匹配，按顺序尝试）
  comment_selectors   - 评论区定位
  skip_selectors      - 噪声区域（广告、追踪脚本等，直接删除）
  recommend_selectors - 相关推荐/侧边推荐区域（提取文本后截断保留）
  recommend_limit     - 推荐区域最多保留几条（按 \\n\\n 分块计数）
"""
from urllib.parse import urlparse

# --- bilibili.com ---
BILI_RULE = {
    "content_selectors": [
        '//*[@id="video_page_detail"]',
        '//*[contains(@class, "video-info-container")]',
        '//div[contains(@class, "video-desc")]',
        '//*[contains(@class, "video-toolbar")]',
    ],
    "comment_selectors": [
        '//*[contains(@class, "reply-list")]',
        '//*[@id="comment"]',
        '//*[contains(@class, "comment-container")]',
        '//*[contains(@class, "bb-comment")]',
    ],
    "skip_selectors": [
        '//*[contains(@class, "slide-ad")]',
        '//*[contains(@class, "ad-report")]',
        '//*[contains(@class, "side-bar")]',
        '//aside',
        '//*[contains(@class, "right-container")]',
        '//*[@id="multi_page"]',
    ],
    "recommend_selectors": [
        '//*[contains(@class, "video-page-special")]',
        '//*[contains(@class, "rec-list")]',
        '//*[contains(@class, "recommend")]',
        '//*[contains(@class, "related")]',
    ],
    "recommend_limit": 5,
    "comment_api": True,  # 使用 B 站 WBI 签名 API 获取评论区（替代 DOM 提取）
}

# --- 规则注册表（新增站点在此添加条目）---
SITE_RULES: dict[str, dict] = {
    "bilibili.com": BILI_RULE,
}


def detect_site(url: str) -> str | None:
    """根据 URL hostname 匹配站点规则，返回站点 key 或 None。"""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return None
    for domain in SITE_RULES:
        if domain in hostname:
            return domain
    return None
