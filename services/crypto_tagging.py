# -*- coding: utf-8 -*-
"""加密新闻打分（web3 二期A design §2）：一次调用出四件套。

与宏观口径**完全分开**：宏观提示词按"对 BTC/纳指的宏观冲击"校准，喂加密新闻会把
"小币上合约"这类币圈关键事打成低分，还会反向污染已校准的宏观标注池。

四件套 = 重要性（对币圈整体 1-10）+ 方向（对币圈整体）+ 币圈事务判定（语义闸，
决定这条新闻进不进加密事件池）+ 提及币种（二期B 异动归因的反查地基）。
**不判每个币单独的方向**——归因场景里币价自己会说话，不需要模型多嘴。
"""
from __future__ import annotations

import json
import re

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.crypto import NewsCoin
from models.news import NewsItem
from services.deepseek_client import call_deepseek_chat
from services.time_utils import utc_now_naive

CRYPTO_TAG_SYSTEM_PROMPT = (
    "你是加密市场新闻标注器。对每条新闻给出四项判断：\n\n"
    "1. importance（1-10 整数）：这条新闻对**加密市场整体**引发可交易价格波动的可能性与强度。\n"
    "   10=极可能立即引发全市场大幅波动（重大监管落地、头部交易所/稳定币暴雷、ETF 重大进展、国家级政策）；\n"
    "   8-9=很可能引发明显波动（头部资产上新/下架、重要机构大额动作、知名协议被盗、宏观政策对加密的直接表态）；\n"
    "   6-7=局部或中等波动（单个项目重大更新、二线资产上所、生态基金、大额解锁）；\n"
    "   4-5=有市场相关性但通常需其他因素配合才影响价格；\n"
    "   1-3=噪音、重复、行情回顾、纯观点。\n"
    "2. direction：相对**加密市场整体**的应然影响，三选一：利多 / 利空 / 中性。\n"
    "3. is_crypto_affair（true/false）：这条新闻本身是不是**加密行业内部的事**。\n"
    "   加密媒体常转载纯宏观新闻（美联储决议、CPI、地缘冲突、美股财报）——那些一律 false；\n"
    "   加密行业自己的监管/ETF/交易所/协议/项目/链上/融资事件 → true。\n"
    "4. coins：新闻**实际在讨论**的加密资产代码列表，大写，如 [\"BTC\",\"SOL\"]。\n"
    "   只填真正被讨论的标的，不填顺带提及的背景资产；没有就给 []。\n"
    "   用交易所通用代码（比特币→BTC、以太坊→ETH）；拿不准代码的项目不要硬编。\n\n"
    "只返回 JSON，不要 Markdown：\n"
    '{"items": [{"id": int, "importance": 1-10, "direction": "利多", '
    '"is_crypto_affair": true, "coins": ["BTC"], "reason": "不超过40字"}]}\n'
    "每条输入新闻在 items 里有且仅有一项，id 严格对应输入。"
)

# 版本戳：每次实质修改提示词时更新（与挂接侧同款约定）。
CRYPTO_TAG_PROMPT_VERSION = "crypto-tag-v1-20260809"

# 合法代码形状：2-15 位大写字母数字。中文项目名、句子、纯符号一律挡掉。
_COIN_RE = re.compile(r"^[A-Z0-9]{2,15}$")


def _build_payload(news_list: list[NewsItem]) -> str:
    items = [{
        "id": n.id,
        "source": n.source,
        "title": (n.title or "")[:160],
        "content": (n.content or "")[:200],
    } for n in news_list]
    return f"共 {len(items)} 条新闻。\n{json.dumps({'news': items}, ensure_ascii=False)}"


def _call_crypto_tagger(user_content: str) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法打加密标")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": CRYPTO_TAG_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 加密打标返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 加密打标返回空 content")
    return result.content


def _normalize_coins(raw) -> list[str]:
    """归一化成大写代码并去重保序；形状不合法的一律丢弃。

    存代码字符串本身、**不限于币安全集**：币安没上的币照存，"能不能在币安交易"
    是读侧拿 symbol 全集现算的标记（上下架会变，不冻结进库）。"""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        code = item.strip().upper()
        if _COIN_RE.match(code) and code not in out:
            out.append(code)
    return out


def _parse_response(raw: str, valid_ids: set[int]) -> dict[int, dict]:
    """防幻觉：id 必须在本批、importance 必须 1-10 整数、direction 必须合法枚举、
    is_crypto_affair 必须是真布尔。任一不合法整条丢弃（不盖章，下轮重试）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"加密打标返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("加密打标返回缺少 items 列表")

    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            nid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if nid not in valid_ids:
            continue
        try:
            importance = int(item.get("importance"))
        except (TypeError, ValueError):
            continue
        if not 1 <= importance <= 10:
            continue
        direction = item.get("direction")
        if direction not in config.NEWS_DIRECTIONS:
            continue
        affair = item.get("is_crypto_affair")
        if not isinstance(affair, bool):
            continue
        reason = item.get("reason")
        out[nid] = {
            "importance": importance,
            "direction": direction,
            "is_crypto_affair": affair,
            "coins": _normalize_coins(item.get("coins")),
            "reason": (str(reason)[:200] if reason else None),
        }
    return out


def tag_crypto_batch(session: Session, news_list: list[NewsItem]) -> int:
    """对一批加密新闻打四件套并落库，返回成功条数。"""
    news_list = [n for n in news_list if n is not None]
    if not news_list:
        return 0
    parsed = _parse_response(_call_crypto_tagger(_build_payload(news_list)),
                             {int(n.id) for n in news_list})
    now = utc_now_naive()
    by_id = {int(n.id): n for n in news_list}
    for nid, tags in parsed.items():
        n = by_id.get(nid)
        if n is None:
            continue
        n.llm_importance = tags["importance"]
        n.llm_importance_reason = tags["reason"]
        n.llm_model = config.DEEPSEEK_MODEL
        n.llm_scored_at = now
        n.news_direction = tags["direction"]
        n.is_crypto_affair = tags["is_crypto_affair"]
        n.tagged_at = now
        # 币种整组替换：重打标时旧行必须清掉，否则残留上一轮的判定
        session.query(NewsCoin).filter(NewsCoin.news_id == nid).delete(synchronize_session=False)
        for coin in tags["coins"]:
            session.add(NewsCoin(news_id=nid, coin=coin))
    session.commit()
    return len(parsed)


def tag_untagged_crypto(session: Session, limit: int = 200,
                        batch_size: int | None = None) -> int:
    """给未打标的加密新闻分片打四件套。

    加密线**不看 traditional_open**（7×24 市场，"传统市场开没开"对它没有意义）——
    这与宏观打标的取数条件不同，是有意为之。"""
    batch_size = int(batch_size or config.DEEPSEEK_BATCH_SIZE)
    todo = (session.query(NewsItem)
            .filter(NewsItem.market == "crypto", NewsItem.tagged_at.is_(None))
            .order_by(NewsItem.timestamp.desc())
            .limit(max(1, limit)).all())
    total = 0
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        try:
            total += tag_crypto_batch(session, chunk)
        except Exception as exc:            # 单片失败不阻断后续，不盖章下轮重试
            logger.error(f"[CryptoTag] 分片打标失败（{len(chunk)} 条）: {exc}")
    return total
