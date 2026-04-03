"""
数据采集模块 - 从各种API获取数据
"""
import os
import ccxt
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone
import requests
import re
from loguru import logger
from fredapi import Fred
import config
from database import SessionLocal, StockIndex, BondRate, EconomicData, CryptoData, MarketNews

class DataCollector:
    def __init__(self):
        self.session = SessionLocal()
        # 代理由 config.py 自动检测并设置环境变量
        if config.PROXY:
            os.environ.setdefault("HTTP_PROXY", config.PROXY)
            os.environ.setdefault("HTTPS_PROXY", config.PROXY)
    
    def collect_stock_indices(self):
        """采集股票指数数据（简洁版）"""
        logger.info("开始采集股票指数数据...")
        for name, symbol in config.DATA_SOURCES["stock_symbols"].items():
            try:
                hist = yf.Ticker(symbol).history(
                    period="2d", interval="1d", prepost=False, auto_adjust=True, back_adjust=False
                )
                if hist.empty:
                    logger.warning(f"{name} ({symbol}) 无法获取历史数据，跳过")
                    continue
                latest = hist.iloc[-1]
                prev = hist.iloc[-2]
                close_price = latest["Close"]
                prev_close = prev["Close"]
                change_pct = ((close_price - prev_close) / prev_close) * 100
                # 规范化为当日00:00的UTC-naive时间，确保删除与写入键一致
                record_dt = datetime.combine(hist.index[-1].date(), datetime.min.time())
                self.session.query(StockIndex).filter(
                    StockIndex.date == record_dt, StockIndex.symbol == symbol
                ).delete()
                self.session.add(StockIndex(
                    date=record_dt, symbol=symbol, name=name,
                    prev_close=prev_close, close=close_price, change_pct=change_pct
                ))
                logger.info(f"{name}: {close_price:.2f} ({change_pct:+.2f}%) [{record_dt.date()}]")
            except Exception as e:
                logger.error(f"{name} ({symbol}) 获取失败: {e}")
        self.session.commit()
    
    def collect_bond_rates(self):
        """采集债券利率数据（优先FRED: DGS10/DGS2；无FRED时10Y回退^TNX/10）"""
        logger.info("开始采集债券利率数据...")

        rates: dict[str, tuple[float, datetime]] = {}

        # 使用 FRED（DGS10 / DGS2）
        if config.FRED_API_KEY:
            try:
                fred = Fred(api_key=config.FRED_API_KEY)
                fred_map = {"US_10Y": "DGS10", "US_2Y": "DGS2"}
                for rate_type, series in fred_map.items():
                    try:
                        s = fred.get_series(series).dropna()
                        value = float(s.iloc[-1])
                        data_date = s.index[-1].date()
                        record_dt = datetime.combine(data_date, datetime.min.time())
                        rates[rate_type] = (value, record_dt)

                        self.session.query(BondRate).filter(
                            BondRate.date == record_dt, BondRate.rate_type == rate_type
                        ).delete()
                        self.session.add(BondRate(date=record_dt, rate_type=rate_type, value=value))
                        logger.info(f"{rate_type} (FRED {series}): {value:.2f}% [{data_date}]")
                    except Exception as e:
                        logger.error(f"FRED 获取 {series} 失败: {e}")
            except Exception as e:
                logger.error(f"初始化 FRED 失败")

        # 计算利差
        if "US_10Y" in rates and "US_2Y" in rates:
            v10, dt10 = rates["US_10Y"]
            v2, dt2 = rates["US_2Y"]
            if dt10 == dt2:
                spread = v10 - v2
                self.session.query(BondRate).filter(
                    BondRate.date == dt10, BondRate.rate_type == "SPREAD"
                ).delete()
                self.session.add(BondRate(date=dt10, rate_type="SPREAD", value=spread))
                logger.info(f"SPREAD(10Y-2Y): {spread:.2f} [{dt10.date()}]")

        self.session.commit()
    
    def collect_crypto_data(self):
        """采集加密货币数据 - 获取当天北京时间早上7点的收盘数据"""
        logger.info("开始采集加密货币数据...")
        
        # 在函数内部创建交易所连接
        try:
            BINANCE_CONFIG = {}
            if config.PROXY:
                BINANCE_CONFIG['proxies'] = {'http': config.PROXY, 'https': config.PROXY}
            exchange = ccxt.binance(BINANCE_CONFIG)
            # exchange.proxies = BINANCE_CONFIG['proxies']
        except Exception as e:
            logger.error(f"初始化交易所失败: {e}")
            return
    
        for symbol, binance_symbol in config.DATA_SOURCES["crypto_symbols"].items():
            try:
                # 使用小时K线，回溯120小时
                params = {
                    'symbol': binance_symbol,  # 交易币对，如 BTCUSDT
                    'interval': '1h',          # 1小时K线
                    'limit': 48                 # 回溯4天
                }
            
                # 使用币安的私有函数获取K线数据
                response = exchange.fapiPublicGetKlines(params=params)
                
                if response and len(response) > 0:
                    # 参考文档整理数据
                    df = pd.DataFrame(response, dtype=float)
                    df.rename(columns={0: 'MTS', 1: 'open', 2: 'high',
                                     3: 'low', 4: 'close', 5: 'volume'}, inplace=True)
                    df['candle_begin_time'] = pd.to_datetime(df['MTS'], unit='ms')
                    
                    # 目标：
                    # - close: UTC 前一天23:00的那根1小时K线的收盘价
                    # - pre_close: UTC 前两天23:00的那根1小时K线的收盘价
                    now_utc = datetime.utcnow()
                    # 需要先转成北京时间
                    prev_utc = now_utc + timedelta(hours=8) 
                    prev1_utc = prev_utc.replace(hour=7, minute=0, second=0, microsecond=0)
                    prev2_utc = prev1_utc - timedelta(days=1)
                    # 转换为timestamp时，prev1_utc是北京时间，ts_today_7是UTC时间戳。
                    ts_today_7 = int(prev1_utc.timestamp() * 1000)
                    ts_yest_7 = int(prev2_utc.timestamp() * 1000)

                    row_today = df[df['MTS'] == ts_today_7]
                    row_yest = df[df['MTS'] == ts_yest_7]
                    if row_today.empty or row_yest.empty:
                        logger.warning(f"{symbol} 未匹配到北京时间07:00的小时K线（today={prev1_utc.date()}）")
                        continue

                    close = row_today.iloc[0]['close']
                    prev_close = row_yest.iloc[0]['close']
                    pct_change = ((close - prev_close) / prev_close) * 100 if prev_close else 0.0
                    volume = row_today.iloc[0]['volume']
                    candle_begin_time = pd.to_datetime(ts_today_7, unit='ms')  # UTC 起始时间

                    # 删除同一时间的旧记录并写入
                    self.session.query(CryptoData).filter(
                        CryptoData.date == candle_begin_time,
                        CryptoData.symbol == symbol
                    ).delete()
                    self.session.add(CryptoData(
                        date=candle_begin_time,
                        symbol=symbol,
                        price=close,
                        change_pct=pct_change,
                        volume=volume
                    ))
                    logger.info(
                        f"{symbol}: ${close:,.2f} ({pct_change:+.2f}%) "
                        f"[UTC: {candle_begin_time.strftime('%Y-%m-%d %H:%M')}]"
                    )
                else:
                    logger.warning(f"{symbol} 无有效K线数据")                    
            except Exception as e:
                logger.error(f"采集 {symbol} 数据失败: {e}")
        
        self.session.commit()
    
    def collect_market_news(self):
        """使用华尔街见闻JSON接口采集“只看重要”的全球快讯，并按北京时间6:00时间窗过滤"""
        logger.info("开始采集重要财经新闻（华尔街见闻JSON）...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        endpoints = [
            ("https://api.wallstreetcn.com/apiv1/content/lives", {"channel": "global", "client": "pc", "limit": 200, "important": "true"}),
        ]

        # 计算北京时间时间窗：前一天07:00 <= t < 今天07:00
        now_utc = datetime.utcnow()
        prev_utc = now_utc + timedelta(hours=8) 
        prev1_utc = prev_utc.replace(hour=7, minute=0, second=0, microsecond=0)
        prev2_utc = prev1_utc - timedelta(days=1)
        ts_high = int(prev1_utc.timestamp())
        ts_low = int(prev2_utc.timestamp())

        items = []
        used_endpoint = None
        for url, params in endpoints:
            try:
                r = requests.get(url, headers=headers, params=params, timeout=15)
                j = r.json()
                data = j.get("data")
                arr = data.get("items")
                if arr:
                    items = arr
                    used_endpoint = url
                    break
            except Exception as e:
                logger.warning(f"请求失败: {url}, {e}")
                continue

        if not items:
            logger.info("未获取到重要快讯（JSON接口可能变更）")
            return

        saved = 0
        for it in items:
            try:
                ts = it.get("display_time")
                ts = int(ts)
                if not (ts_low <= ts < ts_high):
                    continue

                utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                naive_utc = utc_dt.replace(tzinfo=None)

                raw_title = it.get("title").strip()
                raw_content = it.get("content_text")
                content_text = re.sub(r"<[^>]+>", " ", str(raw_content))
                content_text = " ".join(content_text.split())
                title = raw_title

                # 来源字段区分：按接口和频道标注
                channel = it.get("channels")
                if isinstance(channel, list):
                    channel = ",".join(map(str, channel))
                source = f"wallstreetcn:{'api' if used_endpoint else 'unknown'}:{channel}"

                # 去重：同时间同标题
                self.session.query(MarketNews).filter(
                    MarketNews.date == naive_utc,
                    MarketNews.title == title
                ).delete()
                self.session.add(MarketNews(
                    date=naive_utc,
                    category=str(channel) or "global",
                    title=title[:200],
                    content=content_text,
                    source=source
                ))
                saved += 1
            except Exception:
                continue
        self.session.commit()
        logger.info(f"已写入重要快讯 {saved} 条（来源：wallstreetcn JSON，仅北京时间 \
                    {prev2_utc.strftime('%Y-%m-%d %H:%M')} 至 {prev1_utc.strftime('%Y-%m-%d %H:%M')}）")    
    
    def collect_economic_data(self):
        """采集经济数据（需要FRED API密钥）"""
        logger.info("开始采集经济数据...")
        
        if not config.FRED_API_KEY:
            logger.warning("未配置FRED API密钥，跳过经济数据采集")
            return
        
        try:
            fred = Fred(api_key=config.FRED_API_KEY)
            
            for indicator, fred_id in config.DATA_SOURCES["fred_indicators"].items():
                try:
                    # 获取最新数据
                    data = fred.get_series(fred_id).dropna()
                    if not data.empty:
                        if fred_id == "CPIAUCSL" or fred_id == "GDP":
                            latest_value = (data.iloc[-1] - data.iloc[-2]) / data.iloc[-2]
                            latest_date = data.index[-1]

                        if fred_id == "UNRATE":
                            latest_value = data.iloc[-1]
                            latest_date = data.index[-1]

                        # 删除已有数据
                        self.session.query(EconomicData).filter(
                            EconomicData.date == latest_date,
                            EconomicData.indicator == indicator
                        ).delete()
                        
                        # 保存数据
                        econ_data = EconomicData(
                            date=latest_date,
                            indicator=indicator,
                            actual=latest_value,
                        )
                        self.session.add(econ_data)
                        logger.info(f"已采集 {indicator}: {latest_value:.2f}% {latest_date}")
                        
                except Exception as e:
                    logger.error(f"采集 {indicator} 数据失败: {e}")
            
            self.session.commit()
            
        except ImportError:
            logger.error("fredapi 未安装，无法采集经济数据")
        except Exception as e:
            logger.error(f"采集经济数据失败: {e}")
    
    def collect_all_data(self):
        """采集所有数据"""
        logger.info("开始采集所有数据...")
        
        try:
            self.collect_stock_indices()
            self.collect_bond_rates()
            self.collect_crypto_data()
            self.collect_market_news()
            self.collect_economic_data()
            logger.info("所有数据采集完成！")
        except Exception as e:
            logger.error(f"数据采集过程中出错: {e}")
        finally:
            self.session.close()

if __name__ == "__main__":
    # 测试数据采集
    collector = DataCollector()
    collector.collect_all_data()