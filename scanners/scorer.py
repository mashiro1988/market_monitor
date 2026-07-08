"""
新闻重要度评分器 - 使用 DeepSeek API 对新闻条目打 1-10 分。
"""
import json
import re
import time
from datetime import datetime, timezone
from typing import Optional
import requests
from loguru import logger
from scanners.base import NewsRecord
import config


SCORING_SYSTEM_PROMPT = """你是一个面向资本市场短线价格波动的新闻重要性标注器。
你的任务不是判断新闻是否“宏观上重要”，而是判断它在发布后短时间内引发可交易资产价格波动的可能性和强度。

请对每条新闻返回 1-10 的 importance 整数分：
10 = 极可能立即引发大幅跨资产波动，例如央行意外决议、战争/制裁/系统性金融风险、重大监管突发、关键通胀/就业数据显著偏离预期。
8-9 = 很可能引发明显波动，例如重要官员意外表态、重大公司/ETF/交易所/稳定币事件、能源供给冲击、重要经济数据或政策消息。
6-7 = 可能引发局部或中等波动，例如行业监管进展、重要机构观点、市场风险偏好变化、资产相关资金流消息。
4-5 = 信息有市场相关性，但通常需要其他因素配合才会影响价格。
1-3 = 噪音、重复、回顾性报道、无明确交易资产映射，或对价格波动影响很弱。

评分时优先考虑：
- 是否突发、是否超预期、是否有明确时间点。
- 是否影响 BTC、ETH、主要加密资产、美股指数、美元、美债收益率、黄金、原油等价格。
- 是否来自政策、监管、流动性、宏观数据、地缘风险、交易基础设施、重大机构资金行为。
- 新闻若是 Jin10 已带 source_important 标志，也只能作为参考，不要机械复制。
- 语言可以是中文或英文，必须按原文语义判断。

只返回 JSON，不要 Markdown，不要解释性正文。
格式：
{
  "items": [
    {
      "idx": 0,
      "importance": 1-10,
      "reason": "不超过40字，说明可能影响价格波动的核心原因"
    }
  ]
}
必须覆盖所有输入 idx，每个 idx 恰好出现一次。"""


class NewsScorer:
    """使用 DeepSeek API 批量对新闻打分"""

    DEFAULT_BATCH_SIZE = 8
    API_URL = "https://api.deepseek.com/chat/completions"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key if api_key is not None else config.DEEPSEEK_API_KEY
        self.model = model or config.DEEPSEEK_MODEL
        self.batch_size = max(1, int(getattr(config, "DEEPSEEK_BATCH_SIZE", self.DEFAULT_BATCH_SIZE)))
        self.connect_timeout = float(getattr(config, "DEEPSEEK_CONNECT_TIMEOUT", 10))
        self.read_timeout = float(getattr(config, "DEEPSEEK_READ_TIMEOUT", 45))
        self.max_retries = max(0, int(getattr(config, "DEEPSEEK_MAX_RETRIES", 1)))
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.info("[NewsScorer] DEEPSEEK_API_KEY 未配置，跳过 LLM 打分")

    def score_batch(self, records: list[NewsRecord]) -> list[Optional[int]]:
        """
        批量对新闻打分。返回与输入等长的分数列表。
        无法打分的条目返回 None（保留原始 importance）。
        """
        if not self.enabled or not records:
            return [None] * len(records)

        results: list[Optional[int]] = []

        for i in range(0, len(records), self.batch_size):
            batch = records[i: i + self.batch_size]
            batch_scores = self._score_single_batch(batch)
            results.extend(batch_scores)

        return results

    def enrich_batch(self, records: list[NewsRecord]) -> list[NewsRecord]:
        """为新闻记录补充 llm_importance、reason、model、scored_at。"""
        if not self.enabled or not records:
            return records

        for i in range(0, len(records), self.batch_size):
            batch = records[i: i + self.batch_size]
            scored = self._score_single_batch_structured(batch)
            scored_at = datetime.now(timezone.utc).replace(tzinfo=None)
            for record, score in zip(batch, scored):
                if score.get("importance") is None:
                    continue
                record.llm_importance = score["importance"]
                record.llm_importance_reason = score.get("reason")
                record.llm_model = self.model
                record.llm_scored_at = scored_at
        return records

    def _score_single_batch(self, batch: list[NewsRecord]) -> list[Optional[int]]:
        """对单批次（≤20条）打分，兼容旧测试期望的整数列表返回。"""
        scored = self._score_single_batch_structured(batch)
        return [item.get("importance") for item in scored]

    def _score_single_batch_structured(self, batch: list[NewsRecord]) -> list[dict]:
        """对单批次（≤20条）打分，返回结构化结果。"""
        items = [
            {
                "idx": idx,
                "source": r.source,
                "source_important": bool(r.importance) if r.source == "jin10" else None,
                "language": r.language,
                "published_at": r.published_at.isoformat(sep=" ") if r.published_at else None,
                "title": (r.title or "")[:200],
                "content": (r.content or "")[:500],
            }
            for idx, r in enumerate(batch)
        ]
        user_content = f"共{len(items)}条，必须返回恰好{len(items)}个 items。\n{json.dumps(items, ensure_ascii=False)}"

        try:
            raw = self._call_api(user_content, system_prompt=SCORING_SYSTEM_PROMPT, max_tokens=1800)
            payload = self._loads_json(raw, "score")
            if isinstance(payload, list):
                raw_items = [{"idx": i, "importance": s, "reason": None} for i, s in enumerate(payload)]
            elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
                raw_items = payload["items"]
            else:
                raise ValueError(f"返回结构不合法: {type(payload)}")

            by_idx: dict[int, dict] = {}
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                idx = item.get("idx")
                if not isinstance(idx, int) or idx < 0 or idx >= len(batch) or idx in by_idx:
                    continue
                score = item.get("importance")
                if score is None:
                    by_idx[idx] = {"importance": None, "reason": item.get("reason")}
                    continue
                try:
                    importance = max(1, min(10, int(score)))
                except (TypeError, ValueError):
                    by_idx[idx] = {"importance": None, "reason": item.get("reason")}
                    continue
                by_idx[idx] = {
                    "importance": importance,
                    "reason": (item.get("reason") or "")[:120] or None,
                }

            return [by_idx.get(i, {"importance": None, "reason": None}) for i in range(len(batch))]
        except Exception as e:
            logger.warning(f"[NewsScorer] 批次打分失败，返回 None: {e}")
            return [{"importance": None, "reason": None} for _ in batch]

    @staticmethod
    def _loads_json(raw: str, task_name: str):
        """解析 DeepSeek JSON 输出；兼容偶发代码块或前后缀文本。"""
        if not raw or not raw.strip():
            raise ValueError(f"{task_name} 返回空内容")

        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
            if match:
                return json.loads(match.group(1))
            preview = text[:300].replace("\n", "\\n")
            raise ValueError(f"{task_name} 返回非 JSON 内容: {preview}")

    def _call_api(self, user_content: str, system_prompt: str = SCORING_SYSTEM_PROMPT, max_tokens: int = 200) -> str:
        """调用 DeepSeek ChatCompletions API，返回模型回复文本。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    self.API_URL,
                    json=payload,
                    headers=headers,
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                if resp.status_code >= 400:
                    preview = resp.text[:300].replace("\n", "\\n")
                    raise requests.HTTPError(f"{resp.status_code} {preview}", response=resp)
                body = resp.json()
                content = body["choices"][0]["message"].get("content", "")
                if not content or not content.strip():
                    message = body["choices"][0].get("message", {})
                    reasoning = (message.get("reasoning_content") or "")[:300].replace("\n", "\\n")
                    raise ValueError(f"DeepSeek 返回空 message.content，reasoning_content 预览: {reasoning}")
                return content.strip()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError, KeyError, json.JSONDecodeError) as e:
                last_error = e
                if attempt >= self.max_retries:
                    break
                wait_seconds = 1.5 * (attempt + 1)
                logger.warning(
                    f"[NewsScorer] DeepSeek 调用失败，{wait_seconds:.1f}s 后重试 "
                    f"({attempt + 1}/{self.max_retries}): {e}"
                )
                time.sleep(wait_seconds)

        raise last_error or RuntimeError("DeepSeek 调用失败")
