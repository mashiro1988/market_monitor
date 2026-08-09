# -*- coding: utf-8 -*-
"""币安官方公告源（web3 二期A design §1.1）。

上新/下架/合约上市是"某币为什么突然拉起来"命中率最高的官方口径，且是官方发布=可审计。
接口是币安站点的 CMS 接口（无服务承诺、可能改版）：失败上抛由 NewsScanner 记源错误，
与 FinancialJuice 同款处置。releaseDate 是毫秒 Unix 时间戳（UTC，无需时区换算）。
"""
from datetime import datetime

import requests
from loguru import logger

import config
from scanners.base import BaseSource, NewsRecord

ARTICLE_URL_PREFIX = "https://www.binance.com/en/support/announcement/"


class BinanceAnnouncementSource(BaseSource):
    """币安公告（只订 config.BINANCE_ANN_CATALOGS 里的目录，营销活动类不要）。"""

    name = "binance_ann"

    def __init__(self, page_size: int | None = None):
        cfg = config.CRYPTO_NEWS_SOURCES["binance_ann"]
        self.api_url = cfg["api_url"]
        self.page_size = int(page_size or cfg["page_size"])
        self.catalogs = dict(config.BINANCE_ANN_CATALOGS)

    def _get_catalog(self, catalog_id: int) -> list[dict]:
        resp = requests.get(
            self.api_url,
            params={"type": 1, "pageNo": 1, "pageSize": self.page_size,
                    "catalogId": catalog_id},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0 Safari/537.36",
                     "Accept": "application/json"},
            timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, 20),
            proxies=config.proxies(),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"币安公告 HTTP {resp.status_code}: {str(resp.text)[:200]}")
        body = resp.json()
        if body.get("code") != "000000":
            raise RuntimeError(f"币安公告接口错误: {body.get('message') or body}")
        catalogs = ((body.get("data") or {}).get("catalogs") or [])
        out: list[dict] = []
        for cat in catalogs:
            out.extend(cat.get("articles") or [])
        return out

    def fetch(self) -> list[NewsRecord]:
        records: list[NewsRecord] = []
        for catalog_id, label in self.catalogs.items():
            for art in self._get_catalog(catalog_id):
                title = (art.get("title") or "").strip()
                if not title:
                    continue
                released = art.get("releaseDate")
                published = datetime.utcfromtimestamp(released / 1000) if released else None
                records.append(NewsRecord(
                    source=self.name,
                    source_id=str(art.get("id") or ""),
                    title=f"[{label}] {title}"[:500],
                    content=None,
                    url=f"{ARTICLE_URL_PREFIX}{art.get('code')}" if art.get("code") else None,
                    language="en",
                    published_at=published,
                    market="crypto",
                ))
        logger.info(f"[BinanceAnn] 取回 {len(records)} 条公告")
        return records
