"""
价格扫描器 - 编排各价格数据源，统一存储到 PriceSnapshot
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from loguru import logger
from database import get_session
from models.price import PriceSnapshot
from scanners.base import BaseSource, PriceRecord
from scanners.sources.yfinance_source import YFinancePriceSource
from scanners.sources.okx_source import OkxPriceSource
from scanners.sources.coingecko_source import CoinGeckoPriceSource
from scanners.sources.cnbc_bond_source import CnbcBondQuoteSource
import config


class PriceScanner:
    """价格扫描器 - 5分钟频率采集所有资产价格"""

    def __init__(self):
        self.yfinance = YFinancePriceSource()
        self.okx = OkxPriceSource()              # 加密货币主源：OKX 合约优先，现货补位
        self.coingecko = CoinGeckoPriceSource()  # 加密货币备用源：OKX 缺失时使用实时价
        self.cnbc_bonds = CnbcBondQuoteSource()   # 美/日债收益率：CNBC 行情 API（海外可达）

    def scan(self) -> list[PriceRecord]:
        """执行一次完整的价格扫描"""
        all_records: list[PriceRecord] = []
        scan_time = datetime.now(timezone.utc).replace(tzinfo=None)

        # 1. yfinance: 股指、期货、亚洲指数、商品、部分债券
        all_records.extend(self._fetch_safe(self.yfinance))

        # 2. 加密货币：OKX 先查合约 5m K 线，合约不存在则现货 5m K 线；缺失品种再用 CoinGecko 实时价兜底
        crypto_records = self._fetch_safe(self.okx)
        expected_crypto = set(config.PRICE_SOURCES.get("crypto", {}).keys())
        fetched_crypto = {r.name for r in crypto_records}
        missing_crypto = sorted(expected_crypto - fetched_crypto)
        if missing_crypto:
            logger.warning(f"[PriceScanner] OKX 缺失 {missing_crypto}，降级到 CoinGecko 实时价")
            crypto_records.extend(self._fetch_coingecko_symbols(missing_crypto))
        all_records.extend(crypto_records)

        # 3. CNBC: 美债/日债 2Y/10Y 盘中收益率（海外可达，替代东方财富）
        all_records.extend(self._fetch_safe(self.cnbc_bonds))

        # 写入数据库
        self._save_records(all_records, scan_time)

        logger.info(f"[PriceScanner] 扫描完成，共 {len(all_records)} 条价格记录")
        return all_records

    def backfill_missing_history(
        self,
        max_hours: int | None = None,
        end_time: datetime | None = None,
    ) -> list[PriceRecord]:
        """
        回补最近最多 72 小时的 5m 价格历史。

        yfinance / OKX 可以按历史 K 线回补；CoinGecko 与 CNBC quote 只有当前价口径，
        不伪造历史点。重复的 (symbol, timestamp) 会在写入时跳过。
        """
        requested_hours = int(config.PRICE_BACKFILL_MAX_HOURS if max_hours is None else max_hours)
        window_hours = min(max(requested_hours, 0), 72)
        if window_hours <= 0:
            logger.info("[PriceBackfill] 已禁用（window_hours <= 0）")
            return []

        if end_time is None:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)
        elif end_time.tzinfo is not None:
            end_time = end_time.astimezone(timezone.utc).replace(tzinfo=None)
        start_time = end_time - timedelta(hours=window_hours)

        logger.info(
            f"[PriceBackfill] 开始回补 {start_time.isoformat()} - {end_time.isoformat()} UTC "
            f"（最多 {window_hours} 小时）"
        )

        return self.backfill_range(start_time, end_time)

    def backfill_range(self, start_time: datetime, end_time: datetime) -> list[PriceRecord]:
        """回补指定 UTC 时间段内的 yfinance/OKX 5m 历史 K 线。"""
        if start_time.tzinfo is not None:
            start_time = start_time.astimezone(timezone.utc).replace(tzinfo=None)
        if end_time.tzinfo is not None:
            end_time = end_time.astimezone(timezone.utc).replace(tzinfo=None)
        if end_time <= start_time:
            logger.info("[PriceBackfill] 回补区间为空，跳过")
            return []

        logger.info(
            f"[PriceBackfill] 回补区间 {start_time.isoformat()} - {end_time.isoformat()} UTC"
        )

        all_records: list[PriceRecord] = []

        yfinance_records = self.yfinance.fetch_history(start_time, end_time)
        inserted_yfinance = self._save_records(yfinance_records, end_time)
        all_records.extend(yfinance_records)
        logger.info(
            f"[PriceBackfill] yfinance 返回 {len(yfinance_records)} 条，新增 {inserted_yfinance} 条"
        )

        okx_records = self.okx.fetch_history(start_time, end_time)
        inserted_okx = self._save_records(okx_records, end_time)
        all_records.extend(okx_records)
        logger.info(
            f"[PriceBackfill] OKX 返回 {len(okx_records)} 条，新增 {inserted_okx} 条"
        )

        logger.info(
            "[PriceBackfill] 回补完成；CNBC 债券与 CoinGecko 备用源无历史 5m K 线，"
            "只会由常规扫描写入当前报价"
        )
        return all_records

    def _fetch_safe(self, source: BaseSource) -> list[PriceRecord]:
        """安全调用数据源，捕获异常"""
        try:
            logger.info(f"[PriceScanner] 采集 {source.name}...")
            records = source.fetch()
            logger.info(f"[PriceScanner] {source.name} 返回 {len(records)} 条记录")
            return records
        except Exception as e:
            logger.error(f"[PriceScanner] {source.name} 采集失败: {e}")
            return []

    def _fetch_coingecko_symbols(self, symbols: list[str]) -> list[PriceRecord]:
        try:
            records = self.coingecko.fetch_symbols(symbols)
            logger.info(f"[PriceScanner] coingecko_realtime 返回 {len(records)} 条记录")
            return records
        except Exception as e:
            logger.error(f"[PriceScanner] coingecko_realtime 采集失败: {e}")
            return []

    def _save_records(self, records: list[PriceRecord], scan_time: datetime) -> int:
        """将价格记录写入数据库；重复 (symbol, timestamp) 记录则跳过"""
        if not records:
            return 0

        session = get_session()
        inserted = 0
        try:
            grouped: dict[str, list[tuple[PriceRecord, datetime]]] = defaultdict(list)
            for r in records:
                snap_ts = r.timestamp if r.timestamp else scan_time
                if snap_ts.tzinfo is not None:
                    snap_ts = snap_ts.astimezone(timezone.utc).replace(tzinfo=None)
                grouped[r.symbol].append((r, snap_ts))

            for symbol, symbol_records in grouped.items():
                symbol_records.sort(key=lambda item: item[1])
                min_ts = symbol_records[0][1]
                max_ts = symbol_records[-1][1]

                existing_rows = session.query(
                    PriceSnapshot.timestamp,
                    PriceSnapshot.price,
                    PriceSnapshot.source,
                ).filter(
                    PriceSnapshot.symbol == symbol,
                    PriceSnapshot.timestamp >= min_ts,
                    PriceSnapshot.timestamp <= max_ts,
                ).all()
                existing_meta = {ts: (price, src) for ts, price, src in existing_rows}
                existing_timestamps = set(existing_meta)

                prev = session.query(PriceSnapshot).filter(
                    PriceSnapshot.symbol == symbol,
                    PriceSnapshot.timestamp < min_ts,
                ).order_by(PriceSnapshot.timestamp.desc()).first()
                last_price = prev.price if prev else None

                for r, snap_ts in symbol_records:
                    if snap_ts in existing_timestamps:
                        ex_price, ex_source = existing_meta[snap_ts]
                        incoming_is_real = not r.source.startswith(config.GAPFILL_SOURCE)
                        existing_is_gapfill = bool(ex_source) and ex_source.startswith(config.GAPFILL_SOURCE)
                        if incoming_is_real and existing_is_gapfill:
                            # 真实覆盖同槽合成：取 ORM 行原地更新（不能 add，否则撞唯一索引整批回滚）
                            row = session.query(PriceSnapshot).filter_by(symbol=symbol, timestamp=snap_ts).first()
                            if row is not None:
                                prev_price = r.prev_price
                                change_pct = r.change_pct
                                if prev_price is None and last_price is not None:
                                    prev_price = last_price
                                    if prev_price:
                                        change_pct = ((r.price - prev_price) / abs(prev_price)) * 100
                                row.asset_class = r.asset_class
                                row.name = r.name
                                row.price = r.price
                                row.prev_price = prev_price
                                row.change_pct = change_pct
                                row.volume = r.volume
                                row.source = r.source
                                existing_meta[snap_ts] = (r.price, r.source)
                                last_price = r.price          # 链推进到真实价
                                inserted += 1
                            continue
                        # 既有真实 / 入库为合成 → 维持原跳过逻辑
                        if ex_price is not None:
                            last_price = ex_price
                        continue

                    prev_price = r.prev_price
                    change_pct = r.change_pct
                    if prev_price is None and last_price is not None:
                        prev_price = last_price
                        if prev_price != 0:
                            change_pct = ((r.price - prev_price) / abs(prev_price)) * 100

                    snapshot = PriceSnapshot(
                        timestamp=snap_ts,
                        asset_class=r.asset_class,
                        symbol=r.symbol,
                        name=r.name,
                        price=r.price,
                        prev_price=prev_price,
                        change_pct=change_pct,
                        volume=r.volume,
                        source=r.source,
                    )
                    session.add(snapshot)
                    existing_timestamps.add(snap_ts)
                    last_price = r.price
                    inserted += 1

            session.commit()
            return inserted
        except Exception as e:
            session.rollback()
            logger.error(f"[PriceScanner] 保存失败: {e}")
            return 0
        finally:
            session.close()
