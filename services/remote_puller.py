"""远程数据周期 (remote_data_cycle) 的实现。

整个"远程数据相关的工作"被打包成一个 APScheduler job:
  pull (SFTP) -> sector_scan -> (phase 4: factor_compute)
都依赖 puller 拉到的新文件，所以串行跑在同一个 job 内。这个 job 跟 fast_scan /
hourly_summary / 其它 job 用各自的 max_instances=1 锁并行执行，彼此不阻塞。

为什么不用独立守护线程:
- 多一个线程多一份生命周期管理（start/stop/锁）。
- APScheduler 已经在管所有 job，统一 lifecycle 更简单。
- sector_scan 和 factor_compute 都是 puller 的下游，串行跑更直观。
- 测试时直接调函数就行，不用 spin 起线程。

Phase 1 拉的 dataset（够板块计算 + symbol 映射）:
- preprocess_1h_resample/{offset}/market_pivot_spot_{year}.pkl
- preprocess_1h_resample/{offset}/market_pivot_swap_{year}.pkl
- exginfo/spot_swap_matches.pkl

Phase 4 会在同一个 cycle 末尾加 factor_compute（按需订阅币的 1h candle + 因子）。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from services import remote_fs


# ============================================================
# 配置
# ============================================================
# 该值用于 APScheduler IntervalTrigger 注册 cycle 频率（api/app.py + run.py 里读）。
POLL_INTERVAL_SECONDS = int(os.getenv("REMOTE_PULLER_POLL_SECONDS", "60"))
REMOTE_OFFSET = os.getenv("REMOTE_OFFSET", "30m")  # BMAC 的 hour offset 子目录


# ============================================================
# 待拉数据集声明
# ============================================================
@dataclass
class DatasetSpec:
    name: str
    ready_dir: str
    ready_prefix: str
    resolve_pkl_rel: callable  # type: ignore[type-arg]


def _market_pivot_spot_pkl(cutoff_ts: int) -> str:
    """cutoff_ts 是 .ready 的 unix epoch。年份从 cutoff_ts 取最稳。"""
    year = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).year
    return f"preprocess_1h_resample/{REMOTE_OFFSET}/market_pivot_spot_{year}.pkl"


def _market_pivot_swap_pkl(cutoff_ts: int) -> str:
    year = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).year
    return f"preprocess_1h_resample/{REMOTE_OFFSET}/market_pivot_swap_{year}.pkl"


def _spot_swap_matches_pkl(cutoff_ts: int) -> str:
    return "exginfo/spot_swap_matches.pkl"


PHASE1_DATASETS: list[DatasetSpec] = [
    DatasetSpec(
        name="market_pivot_spot",
        ready_dir=f"preprocess_1h_resample/{REMOTE_OFFSET}/",
        ready_prefix="market_pivot_spot",
        resolve_pkl_rel=_market_pivot_spot_pkl,
    ),
    DatasetSpec(
        name="market_pivot_swap",
        ready_dir=f"preprocess_1h_resample/{REMOTE_OFFSET}/",
        ready_prefix="market_pivot_swap",
        resolve_pkl_rel=_market_pivot_swap_pkl,
    ),
    DatasetSpec(
        name="spot_swap_matches",
        ready_dir="exginfo/",
        ready_prefix="spot_swap_matches",
        resolve_pkl_rel=_spot_swap_matches_pkl,
    ),
]

# 拉到这些 dataset 后，触发下游的 sector_scan。
# Phase 4 会再加一组对应 factor_compute 的 dataset 名。
PIVOT_DATASETS_TRIGGERING_SCAN = {"market_pivot_spot", "market_pivot_swap"}


# ============================================================
# 运行状态（供 /status / 监控读）
# ============================================================
@dataclass
class DatasetStatus:
    name: str
    last_cutoff_ts: Optional[int] = None
    last_pull_at: Optional[datetime] = None  # UTC naive
    last_error: Optional[str] = None
    consecutive_errors: int = 0


@dataclass
class PullerStatus:
    last_cycle_at: Optional[datetime] = None
    cycle_count: int = 0
    datasets: dict[str, DatasetStatus] = field(default_factory=dict)


# ============================================================
# RemotePuller — 状态容器 + cycle 入口
# ============================================================
class RemotePuller:
    """状态容器。
    cycle() 是 APScheduler job 入口；并发安全靠 APScheduler 的
    max_instances=1 保证（cycle 永远不会并行自己）。
    status() 给 API/监控用，因可能从其它线程调用，加锁保护读。
    """

    def __init__(self, datasets: list[DatasetSpec]):
        self._datasets = datasets
        self._status = PullerStatus(
            datasets={d.name: DatasetStatus(name=d.name) for d in datasets}
        )
        self._status_lock = threading.Lock()

    # ---- 入口 ----
    def cycle(self) -> dict:
        """跑一轮 pull → 下游处理（sector_scan，将来 factor_compute）。

        返回 stats dict 供日志/测试用。
        异常都被吞掉（防止一次失败让整个 job 死掉），通过 status() 暴露错误。
        """
        with self._status_lock:
            self._status.cycle_count += 1
            self._status.last_cycle_at = datetime.now(timezone.utc).replace(tzinfo=None)

        stats: dict = {"pulled": [], "errors": []}
        pivot_was_updated = False

        # 1) 顺序拉每个 dataset
        for spec in self._datasets:
            try:
                pulled = self._pull_if_newer(spec)
                if pulled:
                    stats["pulled"].append(spec.name)
                    if spec.name in PIVOT_DATASETS_TRIGGERING_SCAN:
                        pivot_was_updated = True
            except Exception as exc:
                logger.exception("dataset {} 拉取异常: {}", spec.name, exc)
                stats["errors"].append(spec.name)
                with self._status_lock:
                    ds = self._status.datasets[spec.name]
                    ds.last_error = repr(exc)
                    ds.consecutive_errors += 1

        # 2) 下游：板块计算（pivot 有新数据才跑）
        if pivot_was_updated:
            stats["sector_scan"] = self._run_sector_scan()

        # 3) 下游（Phase 4 占位）：单币因子计算
        # if any pulled in PER_SYMBOL_DATASETS_TRIGGERING_FACTOR:
        #     stats["factor_compute"] = self._run_factor_compute()

        return stats

    # ---- 下游 ----
    def _run_sector_scan(self) -> dict:
        """拉到新 pivot 后跑 SectorScanner.scan() 同步写 sector_returns。"""
        try:
            # 延迟 import 避免循环依赖
            from scanners.sector_scanner import SectorScanner
            result = SectorScanner().scan()
            logger.info("remote_data_cycle 触发 sector_scan: {}", result)
            return result
        except Exception as exc:
            logger.exception("sector_scan 失败: {}", exc)
            return {"error": repr(exc)}

    # ---- 拉取单个 dataset ----
    def _pull_if_newer(self, spec: DatasetSpec) -> bool:
        """拉取 spec 对应的 pkl 如果有新 cutoff。返回 True 仅当实际下载了新文件。"""
        # 1) 找最新 .ready cutoff
        result = remote_fs.find_latest_ready(spec.ready_dir, spec.ready_prefix)
        if result is None:
            logger.debug("dataset {} 暂无 .ready", spec.name)
            return False
        ready_name, cutoff_ts = result

        # 2) 跟内存里上次记录比较
        with self._status_lock:
            ds = self._status.datasets[spec.name]
            last = ds.last_cutoff_ts

        if last is not None and cutoff_ts <= last:
            return False  # 不新，跳过

        # 3) 触发拉取（remote_fs.pull 自己还会做 mtime/size 二次校验）
        pkl_rel = spec.resolve_pkl_rel(cutoff_ts)
        local = remote_fs.pull(pkl_rel)
        if local is None:
            logger.warning("dataset {} pkl 拉取失败 (ready={}, pkl_rel={})",
                           spec.name, ready_name, pkl_rel)
            with self._status_lock:
                ds = self._status.datasets[spec.name]
                ds.last_error = "pull returned None"
                ds.consecutive_errors += 1
            return False

        # 4) 记录成功
        with self._status_lock:
            ds = self._status.datasets[spec.name]
            ds.last_cutoff_ts = cutoff_ts
            ds.last_pull_at = datetime.now(timezone.utc).replace(tzinfo=None)
            ds.last_error = None
            ds.consecutive_errors = 0
        logger.info("dataset {} 已更新到 cutoff={} ({})",
                    spec.name, cutoff_ts,
                    datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat())
        return True

    # ---- 公共查询 ----
    def status(self) -> PullerStatus:
        with self._status_lock:
            return PullerStatus(
                last_cycle_at=self._status.last_cycle_at,
                cycle_count=self._status.cycle_count,
                datasets={k: DatasetStatus(**v.__dict__) for k, v in self._status.datasets.items()},
            )


# ============================================================
# 模块单例 + 顶层入口
# ============================================================
_puller: Optional[RemotePuller] = None
_puller_lock = threading.Lock()


def get_puller() -> RemotePuller:
    """获取全局唯一 RemotePuller 实例。"""
    global _puller
    with _puller_lock:
        if _puller is None:
            _puller = RemotePuller(PHASE1_DATASETS)
        return _puller


def run_remote_data_cycle() -> dict:
    """APScheduler job 入口：跑一轮远程数据周期（pull → 下游处理）。

    由 IntervalTrigger(seconds=POLL_INTERVAL_SECONDS) 定时驱动，
    max_instances=1 + coalesce=True 保证不会自我重叠。
    """
    return get_puller().cycle()


def get_status() -> PullerStatus:
    return get_puller().status()
