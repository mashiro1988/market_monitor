"""休市补点编排：判休市、维护锚点、按比率合成 perp 代理价写库。

设计见 docs/superpowers/specs/2026-06-28-okx-gapfill-market-overview-design.md。
run() 显式接收 session（依赖注入，便于测试）；perp 取价复用 OkxPriceSource。"""
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy.orm import Session
import config
from models.price import PriceSnapshot
from models.gapfill_anchor import GapfillAnchor


class GapFiller:
    def __init__(self):
        self.mapping = config.ONCHAIN_GAPFILL
        self.source = config.GAPFILL_SOURCE
        self.staleness = timedelta(minutes=config.GAPFILL_STALENESS_MINUTES)
        self.perp_fresh = timedelta(minutes=config.GAPFILL_PERP_FRESH_MINUTES)
        self.step_pct = config.GAPFILL_STEP_PCT
        self.seam_pct = config.GAPFILL_SEAM_PCT

    def run(self, session: Session, okx_source, scan_time: datetime) -> int:
        if not config.GAPFILL_ENABLED or config.GAPFILL_STALENESS_MINUTES <= 0:
            return 0
        mapping = self.mapping
        if not mapping:
            return 0
        bars = okx_source.fetch_instrument_bars([m["okx_inst"] for m in mapping.values()])
        written = 0
        for symbol, m in mapping.items():
            try:
                written += self._handle(session, symbol, m["okx_inst"], bars.get(m["okx_inst"]) or [], scan_time)
            except Exception as e:
                logger.error(f"[GapFiller] {symbol} 失败: {type(e).__name__}: {e}")
        session.commit()
        return written

    def _latest_real(self, session: Session, symbol: str, scan_time: datetime):
        """最近真实快照：排除合成 source、排除未来戳，按时间降序取一。"""
        return (
            session.query(PriceSnapshot)
            .filter(
                PriceSnapshot.symbol == symbol,
                ~PriceSnapshot.source.like(f"{self.source}%"),
                PriceSnapshot.timestamp <= scan_time,
            )
            .order_by(PriceSnapshot.timestamp.desc())
            .first()
        )

    @staticmethod
    def _perp_at(bars, ts: datetime):
        """取 bar_end == ts 的 perp close；命中失败时在 ±5min 内取最近一根。"""
        exact = [b for b in bars if b.bar_end == ts]
        if exact:
            return exact[0].close
        near = [b for b in bars if abs((b.bar_end - ts).total_seconds()) <= 300]
        if near:
            return min(near, key=lambda b: abs((b.bar_end - ts).total_seconds())).close
        return None

    def _handle(self, session, symbol, inst_id, bars, scan_time) -> int:
        fresh = [b for b in bars if timedelta(0) <= scan_time - b.bar_end <= self.perp_fresh]
        if not fresh:
            logger.warning(f"[GapFiller] {symbol} perp {inst_id} 无新鲜 bar，跳过")
            return 0
        latest = fresh[-1]
        real = self._latest_real(session, symbol, scan_time)
        if real is None:
            return 0
        if scan_time - real.timestamp <= self.staleness:
            self._maybe_update_anchor(session, symbol, real, bars)   # live：维护锚点，不补
            return 0
        return self._fill(session, symbol, latest, real)             # 休市：补点

    def _maybe_update_anchor(self, session, symbol, real, bars):
        anchor = session.get(GapfillAnchor, symbol)
        if anchor is not None and real.timestamp <= anchor.real_ts:
            return                                                   # 真实 bar 未推进
        perp = self._perp_at(bars, real.timestamp)
        if perp is None:
            if anchor is not None and (datetime.utcnow() - anchor.updated_at) > timedelta(minutes=30):
                logger.warning(f"[GapFiller] {symbol} 锚点超 30min 未对齐更新")
            return
        if anchor is None:
            session.add(GapfillAnchor(symbol=symbol, real_ts=real.timestamp,
                                      real_close=real.price, perp_price=perp))
        else:
            anchor.real_ts = real.timestamp
            anchor.real_close = real.price
            anchor.perp_price = perp

    def _fill(self, session, symbol, latest, real) -> int:
        anchor = session.get(GapfillAnchor, symbol)
        if anchor is None or not anchor.perp_price or not anchor.real_close:
            return 0
        # 同槽已有（含上一轮合成或回补真实）→ 不重复写、不覆盖
        if session.query(PriceSnapshot).filter_by(symbol=symbol, timestamp=latest.bar_end).first() is not None:
            return 0
        synthetic = latest.close * (anchor.real_close / anchor.perp_price)
        prev = (
            session.query(PriceSnapshot)
            .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp < latest.bar_end)
            .order_by(PriceSnapshot.timestamp.desc())
            .first()
        )
        prev_price = prev.price if prev else None
        # 步进防呆：单根 5m 相对上一点跳变 > STEP_PCT → 坏价，跳过
        if prev_price and abs(synthetic / prev_price - 1) > self.step_pct:
            logger.warning(f"[GapFiller] {symbol} 合成单步跳变过大({synthetic:.2f} vs {prev_price:.2f})，跳过")
            return 0
        # 首点 seam 防呆：补点段第一根（上一点是真实）应≈最近真实收盘
        if (prev is None or not prev.source.startswith(self.source)) and real.price:
            if abs(synthetic / real.price - 1) > self.seam_pct:
                logger.warning(f"[GapFiller] {symbol} 补点首点 seam 过大，疑似坏锚点，跳过")
                return 0
        change_pct = ((synthetic - prev_price) / prev_price * 100) if prev_price else None
        session.add(PriceSnapshot(
            timestamp=latest.bar_end, asset_class=real.asset_class, symbol=symbol,
            name=real.name, price=synthetic, prev_price=prev_price,
            change_pct=change_pct, source=self.source,
        ))
        return 1
