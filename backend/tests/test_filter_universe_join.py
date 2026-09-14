"""P0 回归：`filter_universe` 必须真的 join 上 universe_daily。

背景（2026-09-14 修复的 P0 缺陷）
--------------------------------
`filter_universe` 此前手拼分区路径：

    DATA_ROOT / "universe_daily" / "symbol=__all__" / f"year={trade_date[:4]}.parquet"

而磁盘上的真实文件名由 `parquet_store.path_for_year` 决定，带压缩后缀：

    DATA_ROOT / "universe_daily" / "symbol=__all__" / "year=YYYY.snappy.parquet"

两者永不相等 → `uni_path.exists()` 恒 False → join 从未发生。后果三条：
1. `GET /api/v1/screener` 的 pool_size 退化成 pred 行数，ST/停牌标的直接进榜
   （未校验数据被当成已校验结果）；
2. `market._build_recommend` 用 `require_universe=True` → 永久
   `ERR_DATA_EMPTY: ... 无可交易股票池快照`，每日推荐榜不可用；
3. `alerts._load_latest_predictions` → `degraded="universe_snapshot_missing"`，
   score_topk 预警静默跳过。

本文件把「join 真的发生了」固化成断言：只要有人把路径改回手拼写法，
第 1 个用例立刻失败；只要 join 结果被绕过（例如改成 require_universe=False
的静默降级），第 2/3 个用例失败。

分区数据由**生产同款写入器** `parquet_store.write_partition` 落盘（同目录布局、
同 `.snappy.parquet` 后缀），因此不需要联网、也不依赖仓库里的生产 parquet
（conftest 已把 DATA_ROOT 隔离到临时目录，CI 的 DATA_ROOT 是 ./data/test_parquet）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import ERR_DATA_EMPTY, AQPException  # noqa: E402
from app.data.parquet_store import path_for_year, write_partition  # noqa: E402
from app.data.screening import filter_universe  # noqa: E402

DAY = date(2026, 9, 7)
NEXT_DAY = date(2026, 9, 8)   # 故意不种 universe 行 —— 用于反向断言

# symbol -> (pred_score, is_st, is_halted, board)
# 000752.SZ / 600005.SH 拿的是**最高**两个预测分：修复前它们必然占据榜单前二，
# 修复后必须整体消失。这样断言不依赖顺序，只依赖"在不在"。
_PRED_ROWS = {
    "600001.SH": (0.90, False, False, "main"),
    "600002.SH": (0.80, False, False, "main"),
    "600003.SH": (0.70, False, False, "chinext_star"),
    "000752.SZ": (0.95, True, False, "main"),            # ST（生产真实 ST 标的）
    "600005.SH": (0.85, False, True, "main"),            # 停牌
}
_CLEAN = {"600001.SH", "600002.SH", "600003.SH"}
# 注：600003.SH 的 board 标为 chinext_star 纯属测试构造，用于验证
# 「板块过滤走的是 universe 的 board 列」而非 symbol 前缀兜底。
_NOT_IN_UNIVERSE_DAY = NEXT_DAY


@pytest.fixture(scope="module")
def universe_root(tmp_path_factory):
    """私有 DATA_ROOT + 用生产写入器种下的 universe_daily 年分区。"""
    mp = pytest.MonkeyPatch()
    private_root = tmp_path_factory.mktemp("filter_universe_join")
    mp.setattr(get_settings(), "DATA_ROOT", private_root)

    syms = list(_PRED_ROWS)
    uni = pl.DataFrame({
        "date": [DAY] * len(syms),
        "symbol": syms,
        "name": [f"测试{s}" for s in syms],
        "board": [_PRED_ROWS[s][3] for s in syms],
        "is_st": [_PRED_ROWS[s][1] for s in syms],
        "list_date": [None] * len(syms),
        "days_since_list": [None] * len(syms),
        "is_halted": [_PRED_ROWS[s][2] for s in syms],
        "limit_pct": [10.0] * len(syms),
        "limit_up": [None] * len(syms),
        "limit_down": [None] * len(syms),
        "industry": ["测试行业"] * len(syms),
        "open": [None] * len(syms),
        "high": [None] * len(syms),
        "low": [None] * len(syms),
        "close": [10.0] * len(syms),
        "volume": [1000.0] * len(syms),
    })
    write_partition("universe_daily", "__all__", date(2026, 1, 1), uni,
                    dedup_keys=("date", "symbol"))
    yield private_root
    mp.undo()


def _pred(day: date) -> pl.DataFrame:
    syms = list(_PRED_ROWS)
    return pl.DataFrame({
        "date": [day] * len(syms),
        "symbol": syms,
        "pred_score": [_PRED_ROWS[s][0] for s in syms],
    })


# ---------------- 1) 路径必须命中生产写入器约定 ----------------
def test_universe_partition_path_matches_writer_convention(universe_root):
    """路径断言：`path_for_year` 命中，而旧的「手拼无后缀」路径必须不存在。

    这是本缺陷的最小守卫：改回手拼路径 → legacy 断言那一侧的 exists 变 True、
    同时正确路径读不到 → 用例失败。
    """
    good = path_for_year("universe_daily", "__all__", 2026)
    assert good.exists() is True, f"universe 分区未命中: {good}"
    assert good.name == "year=2026.snappy.parquet", good.name

    legacy = (get_settings().DATA_ROOT / "universe_daily" / "symbol=__all__"
              / "year=2026.parquet")
    assert legacy.exists() is False, (
        f"legacy 手拼路径不应存在（若存在说明有人按旧写法落了文件）: {legacy}")

    # 分区内容确实含 DAY 那天的行
    uni = pl.read_parquet(good).with_columns(pl.col("date").cast(pl.String))
    assert uni.filter(pl.col("date") == DAY.isoformat()).height == len(_PRED_ROWS)


# ---------------- 2) join 真的发生：is_st 列在，ST/停牌被剔除 ----------------
def test_filter_universe_joins_and_drops_st_and_halted(universe_root):
    """`filter_universe` 必须真做 join：含 is_st 列，且 ST/停牌行被剔除。

    修复前：无 is_st 列、pool=5、000752.SZ（最高分）居首。
    修复后：有 is_st 列、pool=3、只留 3 只干净标的。
    """
    pred = _pred(DAY)
    df, pool_size = filter_universe(pred, DAY.isoformat(), "all")

    # join 发生的直接证据：universe 侧列被带进来了
    for col in ("is_st", "is_halted", "board", "name", "industry"):
        assert col in df.columns, f"未 join 上 universe（缺列 {col}）"

    syms = df["symbol"].to_list()
    assert "000752.SZ" not in syms, "ST 标的未被剔除（universe join 未生效）"
    assert "600005.SH" not in syms, "停牌标的未被剔除（universe join 未生效）"
    assert set(syms) == _CLEAN, syms
    assert pool_size == len(_CLEAN) == 3
    # 即便 ST 标的预测分最高，也必须排在榜外
    assert df["pred_score"].to_list() == sorted(
        [_PRED_ROWS[s][0] for s in _CLEAN], reverse=True)


def test_filter_universe_require_universe_true_does_not_raise(universe_root):
    """P0 症状直击：`require_universe=True` 不得再抛 51001（推荐榜/预警依赖它）。"""
    pred = _pred(DAY)
    df, pool_size = filter_universe(pred, DAY.isoformat(), "all",
                                    require_universe=True)
    assert pool_size == 3
    assert set(df["symbol"].to_list()) == _CLEAN


def test_filter_universe_board_filter_uses_universe_board(universe_root):
    """板块过滤走 universe 的 board 列（而非 symbol 前缀兜底）。"""
    pred = _pred(DAY)
    df, pool_size = filter_universe(pred, DAY.isoformat(), "chinext_star")
    assert set(df["symbol"].to_list()) == {"600003.SH"}, df["symbol"].to_list()
    assert pool_size == 1

    df2, pool2 = filter_universe(pred, DAY.isoformat(), "main")
    assert set(df2["symbol"].to_list()) == {"600001.SH", "600002.SH"}
    assert pool2 == 2


# ---------------- 3) 反向：缺快照时仍要如实报错（不得静默降级） ----------------
def test_filter_universe_require_universe_raises_when_date_missing(universe_root):
    """年分区在、但该日无行 → 仍抛 51001（守卫未被顺手放宽成静默降级）。"""
    pred = _pred(_NOT_IN_UNIVERSE_DAY)
    with pytest.raises(AQPException) as excinfo:
        filter_universe(pred, _NOT_IN_UNIVERSE_DAY.isoformat(), "all",
                        require_universe=True)
    assert excinfo.value.code == ERR_DATA_EMPTY
    assert "无可交易股票池快照" in excinfo.value.message

    # 非 require 调用方仍允许拿到未过滤结果（保持既有语义，不改变行为）
    df, pool_size = filter_universe(pred, _NOT_IN_UNIVERSE_DAY.isoformat(), "all")
    assert pool_size == len(_PRED_ROWS) and df.height == len(_PRED_ROWS)
    assert "is_st" not in df.columns
