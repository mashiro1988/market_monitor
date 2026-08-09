# -*- coding: utf-8 -*-
"""BlockBeats Pro API 快讯源（web3 二期A design §1.1）。

老的 open-api/open-flash 已软下线：匿名请求恒返回 {"status":0,"data":[]}（2026-08-09
服务器实探），文档没写但接口体系已换代。现走 Pro API：api-key 请求头认证。

两个坑写在这里省得再踩：
1. create_time 是**北京时间**字符串，入库前减 8 小时转 UTC naive（与 Jin10 同款）；
2. content 是富文本 HTML，直接入库会把标签喂进打分提示词，必须去标签。
"""
import html
import re
from datetime import datetime, timedelta

import requests
from loguru import logger

import config
from scanners.base import BaseSource, NewsRecord

_TAG_RE = re.compile(r"<[^>]+>")
BEIJING_OFFSET = timedelta(hours=8)


def _strip_html(raw: str | None) -> str:
    """去标签 + 反转义 + 压空白。"""
    if not raw:
        return ""
    return " ".join(html.unescape(_TAG_RE.sub(" ", raw)).split())


def _parse_beijing(text: str | None) -> datetime | None:
    """北京时间字符串 → UTC naive。兼容老接口的 Unix 秒字符串。"""
    if not text:
        return None
    raw = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt) - BEIJING_OFFSET
        except ValueError:
            continue
    if raw.isdigit():
        return datetime.utcfromtimestamp(int(raw))
    return None


class BlockBeatsSource(BaseSource):
    """BlockBeats 快讯（取全量中文档，不只取 important 档）。"""

    name = "blockbeats"

    def __init__(self, api_key: str | None = None, page_size: int | None = None,
                 max_pages: int | None = None):
        cfg = config.CRYPTO_NEWS_SOURCES["blockbeats"]
        self.api_key = config.BLOCKBEATS_API_KEY if api_key is None else api_key
        self.api_url = cfg["api_url"]
        self.page_size = int(page_size or cfg["page_size"])
        self.max_pages = int(max_pages or cfg["max_pages"])
        self.lang = cfg["lang"]

    def _get_page(self, page: int) -> list[dict]:
        """取一页。接口层错误一律抛——空数组是"没有新内容"的合法语义，
        把失败也伪装成空数组正是老接口坑了我们一次的地方。"""
        resp = requests.get(
            self.api_url,
            params={"page": page, "size": self.page_size, "lang": self.lang},
            headers={"api-key": self.api_key, "Accept": "application/json"},
            timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, 20),
            proxies=config.proxies(),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"BlockBeats HTTP {resp.status_code}: {str(resp.text)[:200]}")
        body = resp.json()
        if body.get("status") != 0:
            raise RuntimeError(f"BlockBeats 接口错误: {body.get('message') or body}")
        data = body.get("data") or {}
        items = data.get("data") if isinstance(data, dict) else data
        return items or []

    def fetch(self) -> list[NewsRecord]:
        if not self.api_key:
            raise RuntimeError("BLOCKBEATS_API_KEY 未配置，无法采集 BlockBeats")
        records: list[NewsRecord] = []
        for page in range(1, self.max_pages + 1):
            items = self._get_page(page)
            if not items:
                break                      # 空页即止，不白跑后续页
            for item in items:
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                records.append(NewsRecord(
                    source=self.name,
                    source_id=str(item.get("id") or ""),
                    title=title,
                    content=_strip_html(item.get("content")),
                    url=item.get("link") or item.get("url") or None,
                    language="zh",
                    published_at=_parse_beijing(item.get("create_time")),
                    market="crypto",
                ))
        logger.info(f"[BlockBeats] 取回 {len(records)} 条快讯")
        return records
