"""
新闻重要度评分器 - 使用 DeepSeek Chat API 对新闻条目打 1-10 分
"""
import json
import os
from typing import Optional
import requests
from loguru import logger
from scanners.base import NewsRecord


SYSTEM_PROMPT = """你是一个加密货币和宏观经济投资者的新闻重要性评估助手。
对输入的新闻列表，从投资决策角度评估每条新闻的重要性（1-10整数分）：
- 9-10：重大政策变化（央行决议、战争爆发）、系统性风险事件、历史性价格突破
- 7-8：重要经济数据发布（CPI/NFP/GDP）、重大监管动态、主流机构重仓消息
- 4-6：一般市场动态、行业新闻、技术分析
- 1-3：无关噪音、娱乐性内容、重复报道

只返回一个 JSON 整数数组，顺序与输入一致，不要有任何其他文字。
示例输出：[8, 3, 5, 9, 2]"""


class NewsScorer:
    """使用 DeepSeek Chat API 批量对新闻打分"""

    BATCH_SIZE = 20
    API_URL = "https://api.deepseek.com/v1/chat/completions"
    MODEL = "deepseek-chat"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
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

        for i in range(0, len(records), self.BATCH_SIZE):
            batch = records[i: i + self.BATCH_SIZE]
            batch_scores = self._score_single_batch(batch)
            results.extend(batch_scores)

        return results

    def _score_single_batch(self, batch: list[NewsRecord]) -> list[Optional[int]]:
        """对单批次（≤20条）打分"""
        items = [
            {
                "title": (r.title or "")[:200],
                "content": (r.content or "")[:200],
            }
            for r in batch
        ]
        user_content = json.dumps(items, ensure_ascii=False)

        try:
            raw = self._call_api(user_content)
            scores = json.loads(raw)
            if not isinstance(scores, list) or len(scores) != len(batch):
                raise ValueError(f"返回长度不匹配: 期望 {len(batch)}, 实际 {len(scores)}")
            return [max(1, min(10, int(s))) if s is not None else None for s in scores]
        except Exception as e:
            logger.warning(f"[NewsScorer] 批次打分失败，返回 None: {e}")
            return [None] * len(batch)

    def _call_api(self, user_content: str) -> str:
        """调用 DeepSeek Chat API，返回模型回复的文本"""
        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_tokens": 200,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            self.API_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
