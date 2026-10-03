"""全市场日线数据质量检测（2026-10-02）。

用途：抓取/回补完成后，对 ``data/parquet`` 的 ``daily_bar`` 与 ``daily_bar_hfq``
做一次**可复现**的质量体检，输出结论 + 问题清单 + 处理建议。

设计原则（对齐本项目既定纪律）
------------------------------
1. **权威来源是磁盘**，不是 manifest：宿主 safe-delete 守卫会让 ``manifest_invalidate()``
   静默失效，``list_symbols_with_data()`` 会复用陈旧 manifest ⇒ 本脚本只走目录与分区文件。
2. **分两层**：先做**廉价层**（目录/分区名，不读 parquet），再做**内容层**（读 parquet）。
   廉价层能发现"缺分区/缺年份"，内容层才能发现"量纲错/OHLC 非法/重复日期/空值"。
3. 🔴 **量纲必须用「同日恒等式」实测**，不能读注释：``amount / (volume × close) ≈ 1``
   ⇒ volume 单位是**股**（≈0.01 ⇒ 手）。禁止跨日比大小。
4. 🔴 **0 不等于不可得**：空值率按 ``len() - null_count()`` 算有效观测数，不看 dtype。
5. 抽检一律**显式披露抽样口径**（"抽样降级率远高于直觉"，不披露等于误导）。

用法（cd backend）::

    ./.venv/Scripts/python.exe scripts/quality_check_full_market.py
    ./.venv/Scripts/python.exe scripts/quality_check_full_market.py --sample 300
    ./.venv/Scripts/python.exe scripts/quality_check_full_market.py --json 报告.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "parquet"
UNIVERSE = PROJECT_ROOT / "data" / "universe_full.json"
FETCH_LOG = PROJECT_ROOT / "expand_2022r2_fetch.log"

# 目标口径（与 expand_universe_2500.py 的 CLI 一致）
TARGET_START_YEAR = 2022
TARGET_END = date(2026, 9, 30)
# 同一标的两口径的日期集合一致性，只抽检这么多只（读 hfq 会让成本翻倍）
HFQ_CROSSCHECK_SAMPLE = 120

# 反推"每年首个交易日"的参照标的：**上市早、流动性高、几乎每个交易日都有成交**
# （用两只取更早者，避免单只停牌造成偏差）。
REFERENCE_SYMBOLS = ("600519.SH", "000001.SZ")

# 量纲恒等式 `amount/(volume×close)` 的合理区间：1.0 = **股**（0.01 = 手）。
# 留 ±10% 容忍拆股/口径微差；落在区间外即判为「单位可疑」。
UNIT_RATIO_LO, UNIT_RATIO_HI = 0.9, 1.1
# 尾部陈旧的容差（自然日）：末日早于 `目标末日 − 该值` 即计入。
# ⚠️ 只作**告警**、不判硬失败 —— 停牌/退市标的的尾部**合法滞后**，
# 判硬失败会变成**永久阻塞器**（见 §四 的教训）。
TAIL_TOLERANCE_DAYS = 7


def _log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 廉价层：目录 / 分区名
# --------------------------------------------------------------------------- #
def _years_of(sym_dir: Path) -> list[int]:
    out: list[int] = []
    for f in sym_dir.glob("year=*.parquet"):
        m = re.search(r"year=(\d{4})", f.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(set(out))


def scan_coverage(symbols: list[str]) -> dict:
    """目录级覆盖度：不读 parquet，只看分区文件是否存在。"""
    rows: dict[str, dict] = {}
    for ds in ("daily_bar", "daily_bar_hfq"):
        root = DATA_ROOT / ds
        for sym in symbols:
            d = root / f"symbol={sym}"
            years = _years_of(d) if d.is_dir() else []
            rows.setdefault(sym, {})[ds] = years
    return rows


def classify(rows: dict[str, dict]) -> dict:
    both, only_raw, only_hfq, neither = [], [], [], []
    for sym, v in rows.items():
        r, h = v.get("daily_bar") or [], v.get("daily_bar_hfq") or []
        if r and h:
            both.append(sym)
        elif r:
            only_raw.append(sym)
        elif h:
            only_hfq.append(sym)
        else:
            neither.append(sym)
    return {"both": sorted(both), "only_raw": sorted(only_raw),
            "only_hfq": sorted(only_hfq), "neither": sorted(neither)}


def year_profile(rows: dict[str, dict], syms: list[str]) -> dict:
    """起始年 / 缺年 / 是否连续。"""
    first_year: Counter[int] = Counter()
    missing_years: dict[str, list[int]] = {}
    not_reaching_end: list[str] = []
    late_start: list[str] = []
    for sym in syms:
        ys = rows[sym].get("daily_bar") or []
        if not ys:
            continue
        first_year[ys[0]] += 1
        gap = [y for y in range(ys[0], ys[-1] + 1) if y not in ys]
        if gap:
            missing_years[sym] = gap
        if ys[-1] < TARGET_END.year:
            not_reaching_end.append(sym)
        if ys[0] != TARGET_START_YEAR:
            late_start.append(sym)
    return {
        "first_year_dist": dict(sorted(first_year.items())),
        "missing_years": missing_years,
        "not_reaching_end_year": sorted(not_reaching_end),
        "late_start": sorted(late_start),
    }


def year_first_trading_days(years: list[int]) -> dict[int, "date"]:
    """用**长历史高流动性参照标的**反推每个自然年的"全市场首个交易日"。

    为什么需要它：判断"起始年晚"是**上市晚**还是**被年初截断**，需要一个**数据自证**的基准。
    固定阈值（如"1 月头 7 天内"）会误报 —— 恰好 1 月初上市的标的会被冤枉，
    而"年初截断"的**精确特征**是：最早日期 == **该年首个交易日**（不是"1 月上旬"）。
    """
    import polars as pl

    out: dict[int, date] = {}
    for y in years:
        best: date | None = None
        for sym in REFERENCE_SYMBOLS:
            f = DATA_ROOT / "daily_bar" / f"symbol={sym}" / f"year={y}.snappy.parquet"
            if not f.exists():
                continue
            try:
                d = pl.read_parquet(f, columns=["date"])["date"].min()
            except Exception:  # noqa: BLE001
                continue
            if d is not None and (best is None or d < best):
                best = d
        if best is not None:
            out[y] = best
    return out


def classify_late_start(syms: list[str]) -> dict[str, list[str]]:
    """把「起始年 ≠ 2022」的标的一分为二 —— 这一步是**判定正确性**的关键。

    🔴 **晚上市 ≠ 缺陷**：2024 年才上市的标的**本就不该有** 2022 数据。
    真正可疑的是「**年初被截断**」：最早日期**恰好等于该年的首个交易日**
    （例如 2023-01-03 正是 2023 年首个交易日）⇒ 更像"抓取只拿到了 2023 年起的切片"。
    """
    import polars as pl

    firsts: dict[str, date] = {}
    for sym in syms:
        files = sorted((DATA_ROOT / "daily_bar" / f"symbol={sym}").glob("year=*.parquet"))
        if not files:
            continue
        try:
            d = pl.read_parquet(files[0], columns=["date"])["date"].min()
        except Exception:  # noqa: BLE001
            continue
        if d is not None:
            firsts[sym] = d

    refs = year_first_trading_days(sorted({d.year for d in firsts.values()}))

    truncated: list[str] = []
    late_listed: list[str] = []
    for sym, d in firsts.items():
        ref = refs.get(d.year)
        if ref is not None and d == ref:
            truncated.append(sym)      # 最早日 == 该年首个交易日 ⇒ 疑似年初截断
        else:
            late_listed.append(sym)    # 晚于首个交易日 ⇒ 判定为上市晚（合理）
    return {"truncated_suspect": sorted(truncated), "late_listed": sorted(late_listed)}


# --------------------------------------------------------------------------- #
# 内容层：读 parquet
# --------------------------------------------------------------------------- #
def inspect_symbol(sym: str, ds: str = "daily_bar") -> dict:
    """读一只标的的**全部年份**分区，做内容体检（一次读入，多指标复用）。"""
    import polars as pl

    root = DATA_ROOT / ds / f"symbol={sym}"
    files = sorted(root.glob("year=*.parquet"))
    if not files:
        return {"symbol": sym, "error": "no_files"}

    cols = ["date", "open", "high", "low", "close", "volume", "amount"]
    try:
        df = pl.concat([pl.read_parquet(f, columns=cols) for f in files],
                       how="vertical_relaxed")
    except Exception as e:  # noqa: BLE001 - 单标的坏文件不阻断整轮
        return {"symbol": sym, "error": f"{type(e).__name__}: {e}"[:120]}

    if df.is_empty():
        return {"symbol": sym, "error": "empty"}

    n = df.height
    res: dict = {"symbol": sym, "rows": n}

    # E1 日期唯一
    res["dup_dates"] = int(n - df["date"].n_unique())

    # E5 空值率（有效观测数口径，不看 dtype）
    res["null_close"] = int(df["close"].null_count())
    res["null_volume"] = int(df["volume"].null_count())
    res["null_amount"] = int(df["amount"].null_count())

    # E4 非负 / 正值
    res["bad_close"] = int((df["close"] <= 0).sum())
    res["bad_volume"] = int((df["volume"] < 0).sum())
    res["bad_amount"] = int((df["amount"] < 0).sum())

    # E3 OHLC 合法性（忽略含空值的行）
    ok = df.drop_nulls(["open", "high", "low", "close"])
    if ok.height:
        hi = ok["high"]
        lo = ok["low"]
        bad = ((hi < ok["open"]) | (hi < ok["close"])
               | (lo > ok["open"]) | (lo > ok["close"]) | (hi < lo)).sum()
        res["bad_ohlc"] = int(bad)
    else:
        res["bad_ohlc"] = 0

    # E2 量纲恒等式：amount / (volume × close) 的中位数（≈1 ⇒ 股；≈0.01 ⇒ 手）
    m = df.filter(
        (pl.col("volume") > 0) & (pl.col("close") > 0) & (pl.col("amount") > 0)
    ).with_columns((pl.col("amount") / (pl.col("volume") * pl.col("close"))).alias("r"))
    res["unit_ratio_median"] = (round(float(m["r"].median()), 4) if m.height else None)

    res["min_date"] = str(df["date"].min())
    res["max_date"] = str(df["date"].max())
    return res


def hfq_date_set_mismatch(sym: str) -> dict | None:
    """两口径的**日期集合**是否一致（同标的同区间）。不一致 ⇒ 派生 qfq 会错位。"""
    import polars as pl

    def dates(ds: str) -> set[str] | None:
        files = sorted((DATA_ROOT / ds / f"symbol={sym}").glob("year=*.parquet"))
        if not files:
            return None
        try:
            df = pl.concat([pl.read_parquet(f, columns=["date"]) for f in files])
        except Exception:  # noqa: BLE001
            return None
        return {str(x) for x in df["date"].to_list()}

    raw = dates("daily_bar")
    hfq = dates("daily_bar_hfq")
    if raw is None or hfq is None:
        return {"symbol": sym, "note": "一侧缺分区"}
    if raw == hfq:
        return None
    return {"symbol": sym, "raw_only": len(raw - hfq), "hfq_only": len(hfq - raw),
            "raw_n": len(raw), "hfq_n": len(hfq)}


def last_partition_date(ds: str, sym: str) -> str | None:
    """只读**最晚年份分区**取该口径的末日（分区按 ``year`` 落盘 ⇒ 最晚日期必在末分区）。

    🔴 为什么需要它（2026-10-02 实测缺陷）
    ------------------------------------
    :func:`inspect_symbol` 的 ``ds`` 默认是 ``"daily_bar"``，而调用点只传了 ``sym``
    ⇒ 历史版本的「尾部陈旧」判定（``max_date_dist`` → ``[C]``）**只覆盖 raw**，
    **从不检查 ``daily_bar_hfq``**。后果：**raw 尾部正常、hfq 尾部陈旧**的标的
    **静默漏网**。实测漏了 **2 只**：

    | 标的 | raw 末日 | hfq 末日 | 后果 |
    |---|---|---|---|
    | 688015.SH | 2026-09-30 | **2026-09-17** | hfq 缺 8 个交易日 |
    | 688028.SH | 2026-09-30 | **2026-09-17** | hfq 缺 8 个交易日 |

    而 qfq 派生自 hfq（``qfq = hfq / F_last``）⇒ **qfq 也跟着缺**。
    这类标的在旧的质检里**完全看不见**（既不进 ``only_raw``，也不进 ``stale_tail``）。

    ⚠️ 这个判定**只需读一个文件**：读全部分区求 ``max(date)`` 是 10 倍浪费，
    而分区按 year 落盘 ⇒ 最晚日期必在末分区内。
    """
    import polars as pl

    files = sorted((DATA_ROOT / ds / f"symbol={sym}").glob("year=*.parquet"))
    if not files:
        return None
    try:
        df = pl.read_parquet(files[-1], columns=["date"])
    except Exception:  # noqa: BLE001 - 单标的坏文件不阻断整轮
        return None
    if df.is_empty():
        return None
    return str(df["date"].max())


def cs_mirror_survey() -> dict:
    """普查 ``cs/`` 截面镜像：**逐日行数** + 末日滞后（2026-10-02 新增）。

    🔴 为什么必须单独做这一项
    ------------------------
    ``app.data.cross_section.mirror_status()`` 的 ``lag_days`` **只比日期集合**，
    ⇒ 「镜像文件**存在**、但只有 1 行」的**残缺截面**会被判为「已覆盖」而**完全不报**。
    实测（2026-10-02）：``cs/daily_bar/date=20260928.parquet`` 只有 **1 行**
    （源同日 5206 只）、``cs/daily_bar_hfq`` **2 行**，而 ``cs/daily_bar_qfq``
    同日 2492 行正常 —— ``mirror_status`` 对三者**一视同仁地报"日期已覆盖"**。
    ``/screener/stocks`` 读到的截面因此近乎为空（用户可见的静默退化）。

    判据（**镜像内部自比**，零外部依赖，不需要扫源）：
    某日行数 < ``max(10, 0.5 × 该镜像行数中位数)`` ⇒ 判为**残缺截面**。
    自比而非比源，是因为「源当日覆盖数」要扫 5220 个分区（~1 分钟/口径），
    而残缺截面在数量级上与中位数差得很远（1 vs 2443），自比已足够灵敏。

    ⚠️ ``mirror_dates`` 少于 ``source_dates`` **通常不是缺陷**：镜像按**结果集口径**
    只建 2024+ 分区，而扩容后源已到 2022 ⇒ 差值里大部分是**结构性**的
    （实测 lag=486/492 中绝大部分属此类）。故这里只把**末日滞后**与
    **残缺截面**列为异常，不把日期总数差列为异常。
    """
    import polars as pl

    root = DATA_ROOT / "cs"
    out: dict = {}
    for ds in ("daily_bar", "daily_bar_hfq", "daily_bar_qfq"):
        files = sorted((root / ds).glob("year=*/date=*.parquet"))
        rows: list[tuple[str, int]] = []
        for f in files:
            try:
                rows.append((f.stem.split("=")[1],
                             pl.read_parquet(f, columns=["symbol"]).height))
            except Exception:  # noqa: BLE001 - 单个坏文件不阻断整轮
                rows.append((f.stem.split("=")[1], -1))
        good = [n for _, n in rows if n >= 0]
        med = sorted(good)[len(good) // 2] if good else 0
        floor = max(10, int(med * 0.5))
        truncated = [(d, n) for d, n in rows if 0 <= n < floor]
        out[ds] = {
            "dates": len(rows),
            "last": rows[-1][0] if rows else None,
            "median_rows": med,
            "truncated_dates": len(truncated),
            "truncated_sample": truncated[-10:],
            "read_errors": sum(1 for _, n in rows if n < 0),
        }
    return out


def parse_failed_from_log() -> dict[str, str]:
    """从抓取日志提取 FAILED 标的与原因。"""
    if not FETCH_LOG.exists():
        return {}
    out: dict[str, str] = {}
    pat = re.compile(r"\]\s+(\S+)\s+FAILED\s+(.+)$")
    for line in FETCH_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.search(line)
        if m:
            out[m.group(1)] = m.group(2).strip()[:120]
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0,
                    help="内容层抽检只数（0=全量；建议全量，抽样须披露）")
    ap.add_argument("--json", default="", help="把结果写入该 JSON 路径")
    ap.add_argument("--unavailable-json", default="",
                    help="把「两口径均无」与「疑似年初截断」标的显式登记到该 JSON 路径"
                         "（只新增文件，不改 universe_full.json）")
    args = ap.parse_args()

    for p in (DATA_ROOT, UNIVERSE):
        if not p.exists():
            raise SystemExit(f"路径不存在：{p}")

    uni = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    symbols = [x["symbol"] for x in uni["symbols"]]
    _log(f"清单标的数 = {len(symbols)}")

    # ---- 廉价层 ----
    rows = scan_coverage(symbols)
    cls = classify(rows)
    prof = year_profile(rows, cls["both"])
    _log(f"[A] 覆盖度：两侧均有 {len(cls['both'])} / 仅 raw {len(cls['only_raw'])} / "
         f"仅 hfq {len(cls['only_hfq'])} / 两侧均无 {len(cls['neither'])}")
    _log(f"[B] 两侧均有者的最早年份分布 = {prof['first_year_dist']}")
    if prof["missing_years"]:
        _log(f"[D] 分区缺年标的数 = {len(prof['missing_years'])}"
             f"（样例 {list(prof['missing_years'].items())[:3]}）")
    if prof["not_reaching_end_year"]:
        _log(f"[C] 未覆盖到 {TARGET_END.year} 年的标的数 = "
             f"{len(prof['not_reaching_end_year'])}")

    # [B2] 起始年 ≠ 2022 的标的必须**二分**：晚上市（合理）vs 疑似年初截断（需人工确认）
    late = classify_late_start(prof["late_start"])
    _log(f"[B2] 起始年≠{TARGET_START_YEAR} 的 {len(prof['late_start'])} 只中："
         f"**晚上市（合理）** {len(late['late_listed'])} 只 / "
         f"**疑似年初截断（需人工确认，判据=最早日==该年首个交易日）** "
         f"{len(late['truncated_suspect'])} 只"
         f"（样例 {late['truncated_suspect'][:6]}）")

    # ---- 内容层 ----
    target = cls["both"]
    if args.sample and args.sample < len(target):
        step = max(1, len(target) // args.sample)
        content_syms = target[::step][: args.sample]
        _log(f"[E] 内容层：**抽检** {len(content_syms)}/{len(target)} 只"
             f"（步长 {step}，均匀抽样）—— 抽样口径须随结论一并披露")
    else:
        content_syms = target
        _log(f"[E] 内容层：**全量** {len(content_syms)} 只")

    findings: list[dict] = []
    stats = {
        "checked": 0, "errors": 0, "dup_dates": 0, "bad_ohlc": 0,
        "bad_close": 0, "bad_volume": 0, "bad_amount": 0,
        "null_close": 0, "null_volume": 0, "null_amount": 0,
        # 量纲异常：amount/(volume×close) 中位数 ∉ [RATIO_LO, RATIO_HI]
        # ⇒ volume 单位很可能不是「股」（≈100 ⇒ 是「手」）
        "unit_anomaly": 0,
    }
    unit_ratios: list[float] = []
    max_date_dist: Counter[str] = Counter()

    for i, sym in enumerate(content_syms, 1):
        r = inspect_symbol(sym)
        if r.get("error"):
            stats["errors"] += 1
            findings.append({"type": "read_error", **r})
            continue
        stats["checked"] += 1
        for k in ("dup_dates", "bad_ohlc", "bad_close", "bad_volume", "bad_amount",
                  "null_close", "null_volume", "null_amount"):
            if r.get(k):
                stats[k] += 1
                findings.append({"type": k, "symbol": sym, "value": r[k]})
        ur = r.get("unit_ratio_median")
        if ur is not None:
            unit_ratios.append(ur)
            if not (UNIT_RATIO_LO <= ur <= UNIT_RATIO_HI):
                stats["unit_anomaly"] += 1
                findings.append({"type": "unit_anomaly", "symbol": sym,
                                 "ratio_median": ur})
        max_date_dist[r["max_date"]] += 1
        if i % 500 == 0:
            _log(f"    …内容层 {i}/{len(content_syms)}")

    _log(f"[E] 内容层完成：{stats['checked']} 只，读取失败 {stats['errors']} 只")
    _log(f"[E1] 有重复日期的标的 = {stats['dup_dates']}")
    _log(f"[E2] 量纲恒等式 amount/(volume×close) 中位数分布："
         f"min={min(unit_ratios) if unit_ratios else None} "
         f"max={max(unit_ratios) if unit_ratios else None} "
         f"（≈1 ⇒ volume 单位=股）")
    _log(f"[E3] OHLC 非法行所在标的 = {stats['bad_ohlc']}")
    _log(f"[E4] 非正收盘 {stats['bad_close']} / 负成交量 {stats['bad_volume']} / "
         f"负成交额 {stats['bad_amount']}")
    _log(f"[E5] 含空值的标的：close {stats['null_close']} / volume {stats['null_volume']} / "
         f"amount {stats['null_amount']}")
    _log(f"[C] 最后交易日分布（前 6）= {max_date_dist.most_common(6)}")

    # ---- 两口径日期集合一致性（抽检）----
    cross = []
    step = max(1, len(target) // HFQ_CROSSCHECK_SAMPLE)
    for sym in target[::step][:HFQ_CROSSCHECK_SAMPLE]:
        d = hfq_date_set_mismatch(sym)
        if d:
            cross.append(d)
    _log(f"[E6] 两口径日期集合不一致的标的（抽检 {min(len(target), HFQ_CROSSCHECK_SAMPLE)} 只）"
         f" = {len(cross)}（样例 {cross[:3]}）")

    # ---- 失败清单 ----
    failed = parse_failed_from_log()
    _log(f"[F1] 抓取日志中的 FAILED 标的 = {len(failed)}（{list(failed)[:8]}）")
    _log(f"[F2] 仅 raw（hfq 缺失）标的 = {len(cls['only_raw'])}"
         f"（{cls['only_raw'][:8]}）")

    # ---- 尾部陈旧：**两个口径都要看**（raw 正常不代表 hfq 正常）----
    #
    # 🔴 历史版本只统计 raw 的末日分布 ⇒ 「raw 正常、hfq 陈旧」静默漏网（实测漏 2 只）。
    # 这里补一次 hfq 末日普查：**只读末分区**，每只 1 个文件，成本可忽略。
    _tail_floor = str(TARGET_END - timedelta(days=TAIL_TOLERANCE_DAYS))
    hfq_max_dist: Counter[str] = Counter()
    hfq_stale: list[str] = []
    for sym in target:
        d = last_partition_date("daily_bar_hfq", sym)
        if d is None:
            continue
        hfq_max_dist[d] += 1
        if d < _tail_floor:
            hfq_stale.append(sym)
    _log(f"[C2] hfq 最后交易日分布（前 6）= {hfq_max_dist.most_common(6)}")
    _log(f"[C2] hfq 尾部陈旧的标的 = {len(hfq_stale)}（{sorted(hfq_stale)[:8]}）")

    # ---- cs 截面镜像完整性（**按行数判**，不是按日期判）----
    #
    # 🔴 这一项存在的唯一理由：``mirror_status().lag_days`` 只数**日期**，
    # ⇒ 「文件在、内容几乎为空」的残缺截面被判为已覆盖（见 cs_mirror_survey 的 docstring）。
    # 源的末日 = ``max_date_dist`` 中**覆盖数最多**的日期（禁用 ``max(date)``：会被
    # 单标的孤立行劫持 —— 本仓已两次命中且不报错）。
    mir = cs_mirror_survey()
    src_last = max_date_dist.most_common(1)[0][0] if max_date_dist else None
    for ds, info in mir.items():
        _log(f"[G] cs/{ds}: dates={info['dates']} last={info['last']} "
             f"median_rows={info['median_rows']} "
             f"残缺日={info['truncated_dates']}{info['truncated_sample'] or ''}")
    if src_last:
        _log(f"[G] 源末日（覆盖数最多的日期）= {src_last}")
    mir_bad = {ds: i for ds, i in mir.items()
               if i["truncated_dates"] or (src_last and i["last"] and i["last"] < src_last)}

    # ---- 结论 ----
    # 尾部陈旧：末日早于「目标末日 − 容差」的标的（只作告警，见 TAIL_TOLERANCE_DAYS 注释）
    stale_tail = {d: c for d, c in max_date_dist.items() if d < _tail_floor}
    n_stale_tail = sum(stale_tail.values())
    verdict, reasons = _verdict(cls, prof, late, stats, cross, n_stale_tail, stale_tail,
                                hfq_stale=hfq_stale, cs_mirror_bad=mir_bad)
    _log("\n" + "=" * 64)
    _log(f"结论：{verdict}")
    for r in reasons:
        _log(f"  - {r}")
    _log("=" * 64)

    # ---- 显式登记「不可得」标的（项目红线：不得静默丢弃）----
    #
    # ⚠️ **只新增一个独立文件，绝不改动 `universe_full.json`** ——
    # 后者是流水线的**输入清单**，改它会影响后续抓取/重建的行为。
    # 这里只做「如实登记 + 给出可读原因」，把"缺席"从"静默"变成"可审计"。
    if args.unavailable_json:
        un = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "coverage_scope": f"{TARGET_START_YEAR}-01-01 ~ {TARGET_END}",
            "note": ("本文件登记『清单内但两口径均无分区』的标的。它们**不阻断下游重建**"
                     "（缺席不传播空洞），但按项目红线**必须显式登记、不得静默**。"
                     "原因未定论：可能为退市 / 数据源无该标的 / 代码无效，需人工确认。"),
            "neither_count": len(cls["neither"]),
            "neither": [{"symbol": s, "reason": "两口径均无分区（抓取区间内未获得任何数据）"}
                        for s in cls["neither"]],
            "truncated_suspect_count": len(late["truncated_suspect"]),
            "truncated_suspect": [
                {"symbol": s,
                 "reason": "最早日 == 该年首个交易日；可能是恰好年初上市，也可能是只抓到该年起的切片",
                 "action": "需人工用外部上市日核对"}
                for s in late["truncated_suspect"]],
        }
        Path(args.unavailable_json).write_text(
            json.dumps(un, ensure_ascii=False, indent=1), encoding="utf-8")
        _log(f"不可得/可疑登记已写入 {args.unavailable_json}"
             f"（neither={len(cls['neither'])}，truncated_suspect={len(late['truncated_suspect'])}）")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "verdict": verdict, "reasons": reasons,
            "coverage": {k: len(v) for k, v in cls.items()},
            "first_year_dist": prof["first_year_dist"],
            "late_start_late_listed": len(late["late_listed"]),
            "late_start_truncated_suspect": late["truncated_suspect"][:50],
            "missing_years_count": len(prof["missing_years"]),
            "content_stats": stats,
            "unit_ratio_min": min(unit_ratios) if unit_ratios else None,
            "unit_ratio_max": max(unit_ratios) if unit_ratios else None,
            "max_date_dist": dict(max_date_dist.most_common(10)),
            # 🔴 两个口径都要落 JSON：只记 raw 会让「hfq 尾部陈旧」静默漏网
            "hfq_max_date_dist": dict(hfq_max_dist.most_common(10)),
            "hfq_stale_tail": sorted(hfq_stale)[:50],
            "hfq_crosscheck_mismatch": len(cross),
            # 🔴 cs 截面镜像：**按行数**判（mirror_status 只按日期判 ⇒ 残缺截面漏网）
            "cs_mirror": mir,
            "cs_mirror_source_last": src_last,
            "cs_mirror_incomplete": {ds: i["truncated_dates"] for ds, i in mir_bad.items()},
            "fetch_failed": failed,
            "only_raw_sample": cls["only_raw"][:20],
            "neither_sample": cls["neither"][:20],
            "findings_sample": findings[:80],
            "content_checked": len(content_syms),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        _log(f"JSON 已写入 {args.json}")
    # 退出码语义：只有「不通过」才为 1。「有条件通过」视为**可通过**
    # （告警项不阻断重建，但结论里逐条列出、JSON 里保留完整 verdict 供人工判断）。
    return 1 if verdict.startswith("不通过") else 0


def _verdict(cls: dict, prof: dict, late: dict, stats: dict, cross: list,
             n_stale_tail: int = 0, stale_tail: dict | None = None,
             hfq_stale: list | None = None,
             cs_mirror_bad: dict | None = None,
             ) -> tuple[str, list[str]]:
    """把观测转成结论 + 理由（**逐条可追溯到上面的指标**）。"""
    reasons: list[str] = []
    hard = False

    # 「两侧均无」**不判硬失败** —— 这些标的**根本没有数据**，重跑也拿不到；
    # 判硬失败会让质检**永远不通过**、下游重建**永远不启动**（实测会这样卡死）。
    # 而且它们**不会传播任何空洞**（标的只是缺席，不是"半成品"）。
    # 真正会传播空洞的是「仅 raw」（raw 有、hfq 无 ⇒ qfq 派生会缺）。
    # ⇒ 处置：如实披露 + 要求**显式登记「不可得 + 原因」**（项目红线：不得静默）。
    if cls["neither"]:
        reasons.append(f"⚠️ 有 {len(cls['neither'])} 只**完全无数据**（清单内但两侧都无分区）"
                       "⇒ 不阻断重建，但**必须**在交付文档里显式登记「不可得 + 原因」，"
                       "不得静默（红线：不用 0/占位冒充不可得）")
    if cls["only_raw"]:
        hard = True
        reasons.append(f"❌ 有 {len(cls['only_raw'])} 只**仅 raw、hfq 缺失**"
                       "（hfq 空洞会被 rebuild 静默传播到 qfq）⇒ 必须先补齐 hfq")
    if cls["only_hfq"]:
        hard = True
        reasons.append(f"❌ 有 {len(cls['only_hfq'])} 只**仅 hfq、raw 缺失**"
                       "（qfq 由 raw+hfq 派生，缺 raw 同样会派生不完整）")

    # 🔴 起始年：**必须二分**。晚上市（如 2024 年上市）本就不该有 2022 数据，
    # 把它判为缺陷是**误报**。
    #
    # ⚠️ 而"疑似年初截断"**只降级为告警、不判硬失败** —— 因为**本地没有权威上市日来源**，
    # 无法区分「恰好在该年首个交易日上市」与「抓取只拿到该年起的切片」：
    # 实测样例多为科创板 6883xx（2023 年初集中上市），大概率是真实上市。
    # 判为硬失败会因**假阳性卡住下游重建**，代价远大于收益。故如实标注"需人工确认"，
    # 并把判据（最早日 == 该年首个交易日）写清楚，便于人工用外部上市日核对。
    n_late_listed = len(late["late_listed"])
    n_trunc = len(late["truncated_suspect"])
    if n_trunc:
        reasons.append(f"⚠️ 有 {n_trunc} 只**疑似年初截断**（最早日 == 该年首个交易日，"
                       f"也可能是恰好年初上市）⇒ **需人工用外部上市日确认**："
                       f"{late['truncated_suspect'][:10]}")
    if n_late_listed:
        reasons.append(f"✅ 另有 {n_late_listed} 只起始年晚于 {TARGET_START_YEAR}，"
                       "经判定为**晚上市**（首日晚于该年首个交易日）⇒ 无 2022 数据属**正常**")
    if not n_trunc and not n_late_listed:
        reasons.append(f"✅ 两侧均有者的起始年 **100% = {TARGET_START_YEAR}**")

    if prof["missing_years"]:
        hard = True
        reasons.append(f"❌ 有 {len(prof['missing_years'])} 只存在**年份分区缺档**"
                       "（区间内跳年）")
    if stats["errors"]:
        hard = True
        reasons.append(f"❌ 有 {stats['errors']} 只**读取失败**（坏文件/空文件）")
    if stats["dup_dates"]:
        hard = True
        reasons.append(f"❌ 有 {stats['dup_dates']} 只存在**重复日期**")
    if stats["bad_ohlc"]:
        reasons.append(f"⚠️ 有 {stats['bad_ohlc']} 只存在 OHLC 非法行（high/low 越界）")
    if stats["bad_close"] or stats["bad_volume"] or stats["bad_amount"]:
        reasons.append(f"⚠️ 非正收盘 {stats['bad_close']} 只 / 负量 {stats['bad_volume']} 只 / "
                       f"负额 {stats['bad_amount']} 只")
    if stats["null_close"] or stats["null_volume"] or stats["null_amount"]:
        reasons.append(f"⚠️ 含空值：close {stats['null_close']} / volume "
                       f"{stats['null_volume']} / amount {stats['null_amount']}（只）")
    if cross:
        hard = True
        reasons.append(f"❌ 抽检中 {len(cross)} 只**两口径日期集合不一致**"
                       "（派生 qfq 会错位）")

    # 🔴 量纲异常 → **硬失败**：≈100 ⇒ volume 是「手」而非「股」
    # ⇒ 基于 volume 的因子（换手/流动性/volume_spike）会**差 100 倍**，且不报错。
    if stats.get("unit_anomaly"):
        hard = True
        reasons.append(f"❌ 有 {stats['unit_anomaly']} 只**量纲异常**"
                       f"（`amount/(volume×close)` 中位数 ∉ [{UNIT_RATIO_LO}, {UNIT_RATIO_HI}]）"
                       "⇒ volume 单位很可能不是「股」⇒ 基于 volume 的因子会差 100 倍")

    # 尾部陈旧 → **只告警**（不判硬失败）：停牌/退市标的的尾部**合法滞后**，
    # 判硬失败会让门禁变成永久阻塞器（本仓已踩过同类坑）。
    if n_stale_tail:
        top = sorted((stale_tail or {}).items(), key=lambda kv: -kv[1])[:4]
        reasons.append(f"⚠️ 有 {n_stale_tail} 只**尾部陈旧**（末日早于 "
                       f"{TARGET_END - timedelta(days=TAIL_TOLERANCE_DAYS)}）⇒ 不阻断重建，"
                       f"但需补抓；分布 {top}"
                       "（注：停牌/退市标的的尾部合法滞后，故不判硬失败）")

    # 🔴 hfq 尾部陈旧 → **只告警**（同 raw 的理由：停牌/退市合法滞后）。
    #
    # 为什么必须单列：raw 尾部正常**不代表** hfq 尾部正常。实测（2026-10-02）有 2 只
    # （688015.SH / 688028.SH）raw 到 2026-09-30 而 hfq 停在 2026-09-17
    # ⇒ hfq 缺 8 个交易日，**qfq 派生自 hfq 也跟着缺**，但旧质检完全不报。
    if hfq_stale:
        reasons.append(f"⚠️ 有 {len(hfq_stale)} 只 **hfq 尾部陈旧**（raw 正常但 hfq 末日早于 "
                       f"{TARGET_END - timedelta(days=TAIL_TOLERANCE_DAYS)}）"
                       f"⇒ qfq 派生自 hfq 会跟着缺；需补抓 hfq。样例 {sorted(hfq_stale)[:8]}"
                       "（注：旧版本只统计 raw 末日 ⇒ 这类标的**静默漏网**）")

    # 🔴 cs 截面镜像残缺 → **只告警**（它是**派生缓存**，跑 `build_cs_mirror` 即可修复；
    # 判硬失败会让「重建尚未执行」这一**正常的中间态**卡死门禁）。
    #
    # 但必须显式告警：这是**用户可见**的静默退化 —— ``/screener/stocks`` 读到的截面
    # 会近乎为空。实测（2026-10-02）``cs/daily_bar/date=20260928`` 只有 **1 行**
    # （源同日 5206 只），而 ``mirror_status().lag_days`` 对此**完全不报**（它只数日期）。
    if cs_mirror_bad:
        detail = "; ".join(
            f"{ds}: 残缺 {i['truncated_dates']} 日 / 末日 {i['last']}"
            for ds, i in cs_mirror_bad.items())
        reasons.append(
            f"⚠️ 有 {len(cs_mirror_bad)} 个 **cs 截面镜像不完整**（{detail}）"
            "⇒ `/screener/stocks` 会读到残缺截面（**用户可见**）；"
            "修复：跑 `build_cs_mirror` 步（`--steps rebuild` 已含）"
            "（注：`mirror_status()` 只比日期集合 ⇒ 残缺截面**静默漏网**）")

    if hard:
        return "不通过（存在必须处理项）", reasons
    if any(r.startswith("⚠️") for r in reasons):
        return "有条件通过（仅告警项）", reasons
    return "通过", reasons


if __name__ == "__main__":
    sys.exit(main())
