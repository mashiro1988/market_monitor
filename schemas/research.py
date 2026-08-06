from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.news import NewsItemSchema


class ResearchEventItem(BaseModel):
    id: int
    name: str
    status: str
    gate_keywords: str | None = None
    created_from: str
    merged_into_id: int | None = None
    closed_reason: str | None = None
    evidence_count: int = 0
    today_new: int = 0
    yesterday_new: int = 0
    badge_count: int = 0
    days_since_last: int | None = None
    last_evidence_at: str | None = None      # naive UTC isoformat(项目惯例,无 Z)
    last_evidence_bj: str | None = None      # 北京时间字符串,卡片直接显示(勿用上一行拼时间)


class ResearchEventsResponse(BaseModel):
    items: list[ResearchEventItem] = Field(default_factory=list)


class ResearchEventCreateRequest(BaseModel):
    name: str
    news_ids: list[int] = Field(default_factory=list)
    gate_keywords: str | None = None
    created_from: str = "manual"             # annotation / manual


class ResearchEventPatchRequest(BaseModel):
    """改名/关键词/关闭/重开/合并,一个 PATCH 承载(spec §9.3);全部可选,传了才动。"""
    name: str | None = None
    gate_keywords: str | None = None
    keywords_backscan: bool = False          # 改关键词时勾选:追溯回扫 72h(spec §5.1)
    status: str | None = None                # "closed"(带 closed_reason)/ "active"(重开)
    closed_reason: str | None = None
    merge_into_id: int | None = None


class SuggestKeywordsRequest(BaseModel):
    name: str
    news_ids: list[int] = Field(default_factory=list)


class SuggestKeywordsResponse(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class BackscanRequest(BaseModel):
    days: float = 3.0


class BackscanResponse(BaseModel):
    cleared: int


class ObsResult(BaseModel):
    status: str                              # pending / no_data / ok
    net_pct: float | None = None
    actual_minutes: float | None = None
    start: float | None = None
    end: float | None = None


class DriverBadge(BaseModel):
    symbol: str
    change_pct: float | None = None


class LinkBrief(BaseModel):
    id: int
    link_source: str
    auto_event_id: int | None = None
    confidence: float | None = None
    prompt_version: str | None = None
    detached: bool = False


class TimelineItem(BaseModel):
    news: NewsItemSchema
    obs: ObsResult
    obs_symbol: str
    driver_badge: DriverBadge | None = None
    score_miss: bool = False
    link: LinkBrief


class TimelineEventHead(BaseModel):
    id: int
    name: str
    status: str
    gate_keywords: str | None = None
    created_from: str
    closed_reason: str | None = None
    merged_into_id: int | None = None


class TimelineResponse(BaseModel):
    event: TimelineEventHead
    items: list[TimelineItem] = Field(default_factory=list)
    pending_relink: int = 0                  # >0 → 前端显示"回扫进行中(剩 N 条)"
    total: int = 0                           # 筛选后总条数(分页控件算页数用)
    page: int = 1
    page_size: int = 0


class LinkCreateRequest(BaseModel):
    event_id: int
    news_id: int


class LinkPatchRequest(BaseModel):
    event_id: int | None = None              # 传了 = 改归属
    detached: bool | None = None             # True = 摘下
    detach_reason: str | None = None


class LinkResponse(BaseModel):
    id: int
    event_id: int
    news_id: int
    link_source: str
    detached: bool


class NewsEventLinkBrief(BaseModel):
    link_id: int
    event_id: int
    event_name: str
    event_status: str


class NewsLinksResponse(BaseModel):
    items: list[NewsEventLinkBrief] = Field(default_factory=list)


class BufferResponse(BaseModel):
    items: list[NewsItemSchema] = Field(default_factory=list)


class RevivalItem(BaseModel):
    news: NewsItemSchema
    event_id: int
    event_name: str


class RevivalResponse(BaseModel):
    items: list[RevivalItem] = Field(default_factory=list)


class ResearchStats(BaseModel):
    gated_processed_today: int = 0
    auto_linked_today: int = 0
    link_rate: float | None = None
    corrected_today: int = 0
    correction_rate: float | None = None
    pending_relink: int = 0
