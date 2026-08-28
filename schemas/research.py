from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.news import NewsItemSchema


class ResearchEventItem(BaseModel):
    id: int
    name: str
    status: str
    event_type: str = "macro"                # macro / crypto（web3 二期A）
    display_no: int = 0                      # 人看的序号：每种类型各从 1 排；id 才是内部标识
    coins: list[str] = Field(default_factory=list)   # 加密事件的派生币种（读时算，宏观恒空）
    gate_keywords: str | None = None
    created_from: str
    merged_into_id: int | None = None
    closed_reason: str | None = None
    evidence_count: int = 0
    today_new: int = 0
    yesterday_new: int = 0
    badge_count: int = 0
    market_count: int = 0                    # 关联 Polymarket 市场数(2026-08-28,卡片小徽章)
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
    event_type: str = "macro"                # macro / crypto（web3 二期A）


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
    display_no: int = 0                      # 人看的序号（各类型自排）
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


class SweepRequest(BaseModel):
    """AI 梳理(2026-08-13 design):按钮触发,同步长调用。"""
    event_type: str = "macro"                # macro / crypto,各池各梳各的
    dry_run: bool = False                    # True = 只看模型提案,不落库(验收用)


class SweepCreatedEvent(BaseModel):
    id: int = 0
    display_no: int = 0
    name: str
    news_count: int = 0
    why: str = ""


class SweepNewsBrief(BaseModel):
    """提案成员快讯摘要:让人不点进快讯页也能判断该不该立。"""
    id: int
    t: str = ""                              # 北京无关:直接是 UTC 存储时刻 MM-DD HH:MM
    title: str = ""


class SweepProposal(BaseModel):
    """梳理提案(2026-08-15 提案制):只描述、不落库;采纳时原样传回 apply。"""
    name: str
    keywords: list[str] = Field(default_factory=list)
    news_ids: list[int] = Field(default_factory=list)
    why: str = ""
    news: list[SweepNewsBrief] = Field(default_factory=list)


class SweepResponse(BaseModel):
    event_type: str
    scanned: int = 0                         # 本次喂给模型的未挂快讯条数
    truncated: bool = False                  # 超 RESEARCH_SWEEP_MAX_NEWS 被截断(不静默)
    proposals: list[SweepProposal] = Field(default_factory=list)   # 新事件提案,待人采纳
    attached: int = 0                        # 已自动补挂到现有事件的证据条数
    skipped_new_events: int = 0              # 模型提案超上限被丢弃的个数(不静默)
    vetoed: int = 0                          # 撞否决清单(用户删过的同名主题)被拦的个数
    duration_seconds: float = 0.0            # LLM 思考耗时
    dry_run: bool = False


class SweepApplyRequest(BaseModel):
    """采纳勾选的提案(签字环节):news 摘要字段可原样带回,后端忽略。"""
    event_type: str = "macro"
    events: list[SweepProposal] = Field(default_factory=list)


class SweepApplyResponse(BaseModel):
    event_type: str
    created: list[SweepCreatedEvent] = Field(default_factory=list)
    skipped_existing: list[str] = Field(default_factory=list)   # 同名活跃事件,跳过未立


class MarketSweepRequest(BaseModel):
    """找市场提案(spec 2026-08-28 §3):run 全程不落库,天然演练,无需 dry_run。"""
    event_type: str = "macro"
    event_id: int | None = None              # 传了=单事件按钮/找后继;不传=整线


class MarketProposal(BaseModel):
    event_id: int
    event_name: str = ""
    slug: str
    title: str = ""
    current_probability: float | None = None   # 单市场取 Yes;多子市场为空(spec §4)
    market_count: int = 1
    volume: float | None = None
    end_date: str = ""
    confidence: float | None = None
    reason: str = ""


class MarketSweepResponse(BaseModel):
    event_type: str
    scanned_events: int = 0
    searched_terms: int = 0
    candidates: int = 0
    dropped_price_targets: int = 0           # 被剔的价格目标类(不静默)
    proposals: list[MarketProposal] = Field(default_factory=list)
    duration_seconds: float = 0.0


class MarketSweepApplyRequest(BaseModel):
    """采纳勾选提案(签字环节):前端把提案勾选子集原样传回,无状态。"""
    event_type: str = "macro"
    items: list[MarketProposal] = Field(default_factory=list)


class MarketSweepApplyResponse(BaseModel):
    event_type: str
    added: list[str] = Field(default_factory=list)
    revived: list[str] = Field(default_factory=list)
    linked: int = 0
    skipped: list[str] = Field(default_factory=list)


class AttachMarketRequest(BaseModel):
    tracked_id: int


class DetachMarketRequest(BaseModel):
    reason: str | None = None


class DeleteEventResponse(BaseModel):
    """删除=软删除(2026-08-13):墓碑保 display_no,证据摘下退回缓冲区。"""
    id: int
    deleted: bool = True
    links_freed: int = 0                     # 退回缓冲区的证据条数
