"""一次性历史回补（东财逐日快照版）—— announcements.parquet，2024-01-01 至今。

为什么不用巨潮 cninfo
--------------------
``scripts/backfill_announcements.py`` 走巨潮 ``hisAnnouncement/query``，但该接口：
  1. 分页深到约 100 页后会**重置回第 1 页**（实测 page 200 与 page 1 返回同一批
     announcementId）⇒ 单窗口硬上限约 3000 条，**年报季峰值日单日就超限**；
  2. 已被服务端 WAF 403 封锁（裸 POST 返回 body 长度 0，带完整浏览器头亦然）。
⇒ 结构性不可行，本脚本改用东财 ``stock_notice_report`` **逐披露日快照**。

代价对比（实测）
---------------
  巨潮分段：请求数 ~2.7 万、耗时 13~16s/段、且失败
  东财逐日：1 请求/交易日、1.6~4.3s/日、52 日采样 100852 行 / **0 错误**
  东财峰值日：2026-04-29 = 26424 行、2024-04-29 = 10995 行（远低于巨潮的 3000 页限）

设计要点
--------
- **按交易日历**枚举，不用自然日（自然日会白跑 ~110 天/年且更易触发限流）。
- **断点续跑**：进度写入 ``progress_em.json``，重跑自动跳过已完成日期。
- **单日失败不中断**：记录到失败列表，最后统一报告；重跑即可补齐。
- **PIT 正确**：``pub_date`` 用接口返回的**真实公告日期**，绝不写成抓取日。
- 代理规避：本机 egress 代理（127.0.0.1:63978）会间歇故障，开头强制 unset + NO_PROXY。

用法（必须用项目 venv，cwd 在 backend/）
--------------------------------------
    cd backend && ./.venv/Scripts/python.exe scripts/backfill_announcements_em.py
    # 指定区间 / 强制重跑：
    ... --start 2024-01-01 --end 2026-09-30 --reset
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

# ---- 代理规避（必须早于任何网络库 import） ----
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402

from app.data.ingest.announcements import _build_frame  # noqa: E402
from app.domain.a_share_rules import code_to_symbol  # noqa: E402

PROGRESS = BACKEND_ROOT / "tmp_probe" / "progress_em.json"
RETRIES = 3
BACKOFF = 5.0
#: 单日抓取墙钟上限（秒）。防本机 egress 代理间歇性"挂住"导致整体无限阻塞。
FETCH_TIMEOUT = 60.0
#: akshare ``stock_notice_report`` 的 ``symbol`` 是封闭枚举，"全部" 表示全市场。
RENAME = {"公告标题": "title", "公告日期": "pub_date", "公告类型": "category",
          "代码": "raw_code", "网址": "url", "公告链接": "url"}


def _load_progress() -> dict:
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {"done": [], "failed": {}}


def _save_progress(prog: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(prog, ensure_ascii=False, indent=1),
                        encoding="utf-8")


def _trading_days(start: date, end: date) -> list[date]:
    """交易日历（复用项目事实源，避免自建节假日规则）。"""
    import akshare as ak

    for attempt in range(1, RETRIES + 1):
        try:
            cal = ak.tool_trade_date_hist_sina()
            days = {d for d in cal["trade_date"].tolist()}
            break
        except Exception as ex:  # noqa: BLE001
            print(f"  calendar retry {attempt}/{RETRIES}: {type(ex).__name__}",
                  flush=True)
            time.sleep(BACKOFF * attempt)
    else:
        raise RuntimeError("交易日历获取失败")
    out = []
    cur = start
    while cur <= end:
        if cur in days:
            out.append(cur)
        cur = date.fromordinal(cur.toordinal() + 1)
    return out


def _fetch_day(d: date) -> pl.DataFrame:
    """拉取单披露日全市场公告，规范化为 7 列 schema。

    ⚠️ 必须套 **硬墙钟超时**：本机 egress 代理（127.0.0.1:63978）会间歇性"挂住"
    （TCP 已建立但不返回数据），而 akshare/requests **默认无 timeout** ⇒ 单次调用
    可无限期阻塞。实测 2026-09-30 回补跑到 70/666 天时整体卡死 >3 分钟零进展
    （进程存活但无写入、无输出）—— 正是本项目后端"静默消失"的同一根因。
    用线程 + join(timeout) 做墙钟兜底：超时即放弃该日（交由外层重试/记录失败），
    绝不让单日拖死整轮。
    """
    import concurrent.futures as cf

    def _do() -> pl.DataFrame:
        import akshare as ak

        raw = ak.stock_notice_report(symbol="全部", date=d.strftime("%Y%m%d"))
        if raw is None or raw.empty:
            return pl.DataFrame()
        raw = raw.rename(
            columns={k: v for k, v in RENAME.items() if k in raw.columns})
        if "title" not in raw.columns:
            raise ValueError(f"公告字段漂移: {list(raw.columns)[:8]}")
        df = pl.from_pandas(raw[[c for c in ("raw_code", "title", "pub_date", "url")
                                 if c in raw.columns]])
        if "raw_code" not in df.columns:
            return pl.DataFrame()
        df = df.with_columns(
            pl.col("raw_code").cast(pl.String, strict=False).str.strip_chars())
        df = df.filter(pl.col("raw_code").str.contains(r"^\d{6}$"))
        if df.is_empty():
            return pl.DataFrame()
        df = df.with_columns(
            pl.col("raw_code").map_elements(code_to_symbol, return_dtype=pl.String)
            .alias("symbol"))
        df = df.filter(pl.col("symbol").is_not_null())
        # PIT 红线：pub_date 必须是接口返回的真实公告日期。
        df = df.with_columns(
            pl.col("pub_date").cast(pl.String, strict=False).str.slice(0, 10)
            .str.to_date(strict=False).alias("pub_date"))
        # 极少数公告日期为空的，用披露日兜底（仅兜底，不覆盖已有真实日期）。
        df = df.with_columns(
            pl.col("pub_date").fill_null(pl.lit(d, dtype=pl.Date)))
        out = _build_frame(
            df.select(["symbol", "pub_date", "title", "url"]), "eastmoney")
        return out.unique(
            subset=["symbol", "title", "pub_date", "url"], keep="last")

    #: 单日墙钟上限。实测正常 1.6~4.3s，给 60s 足够宽容（含慢速年报季）。
    ex = cf.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_do)
    try:
        return fut.result(timeout=FETCH_TIMEOUT)
    except cf.TimeoutError:
        raise TimeoutError(
            f"单日抓取超过 {FETCH_TIMEOUT}s 墙钟上限（疑似代理挂住）") from None
    finally:
        ex.shutdown(wait=False)



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--reset", action="store_true", help="忽略进度，全量重跑")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    prog = {"done": [], "failed": {}} if args.reset else _load_progress()
    #: done = **已落盘**的交易日（续跑的唯一权威口径，仅由 _flush_year 写入）。
    done = set(prog["done"])
    #: fetched = 本进程已抓取过的交易日（仅用于日志/进度显示，不参与续跑判断）。
    fetched: set[str] = set()

    print(f"[em-backfill] range {start} ~ {end}", flush=True)
    days = _trading_days(start, end)
    todo = [d for d in days if d.isoformat() not in done]
    print(f"[em-backfill] trading_days={len(days)} todo={len(todo)} "
          f"already_done={len(days) - len(todo)}", flush=True)

    t0 = time.time()
    total_rows = 0
    #: 内存缓冲：按年累积，每年结束后用 write_year_batch 整年原子覆盖一次。
    #:
    #: ⚠️ 为什么不用 save_announcements（=write_partition）逐日落盘：
    #: write_partition 是「读整年分区 -> concat -> unique -> sort -> 原子重写」，
    #: 逐日调用 ⇒ 全年 N 天要做 N 次"读全量+排序全量"，写放大 O(N²)。
    #: 实测 2024 分区累积到 ~11 万行后，单日耗时从 1.6s 涨到 >150s（进程 467MB），
    #: 666 天根本跑不完。整年一次性覆盖把复杂度压回 O(N)。
    #:
    #: 🔴 断点续跑契约（2026-09-30 修正，这是踩过的坑）：
    #: 「已抓取」与「已落盘」**必须分开记**。最初把两者混为一谈（抓到就写 done），
    #: 结果进程在年中被杀时，缓冲区里已抓但未落盘的日行全部丢失，而 progress 仍记 done
    #: ⇒ 重跑不再补，形成**静默数据缺口**（实测：2024 分区少了 01~04 月共 80 个交易日）。
    #: 现在只有 `_flush_year` 成功返回后才把该年的日期写入 `done`；
    #: `fetched` 仅用于日志与进度显示，**不参与续跑判断**。
    buf: dict[int, list[pl.DataFrame]] = {}
    cur_year: int | None = None

    def _flush_year(y: int) -> int:
        """把缓冲区里该年的所有日帧 concat 去重后整年覆盖落盘。

        成功后把该年**实际驻留缓冲区**的日期标记为 done（而非「曾抓到过」的日期）
        —— 这是断点续跑的唯一权威口径。
        """
        from app.data.parquet_store import write_year_batch
        parts = buf.pop(y, [])
        keys = buf_years_keys.pop(y, [])
        if not parts:
            return 0
        merged = pl.concat(parts, how="vertical_relaxed")
        merged = merged.unique(
            subset=["symbol", "title", "pub_date", "url"], keep="last")
        write_year_batch("announcements", "__all__", y, merged)
        # 落盘成功后才记账（顺序不可颠倒）。
        done.update(keys)
        prog["done"] = sorted(done)
        _save_progress(prog)
        return merged.height

    #: 记录每年缓冲区里都有哪些交易日（用于 flush 后精确记账）。
    buf_years_keys: dict[int, list[str]] = {}

    for i, d in enumerate(todo, 1):
        key = d.isoformat()
        last: Exception | None = None
        for attempt in range(1, RETRIES + 1):
            try:
                df = _fetch_day(d)
                if not df.is_empty():
                    buf.setdefault(d.year, []).append(df)
                    buf_years_keys.setdefault(d.year, []).append(key)
                    total_rows += df.height
                else:
                    # 抓到空表也要记账（该日确实无公告），但同样等 flush 后生效。
                    buf_years_keys.setdefault(d.year, []).append(key)
                fetched.add(key)
                prog["failed"].pop(key, None)
                _save_progress(prog)
                print(f"  [{i}/{len(todo)}] {key} rows={df.height if not df.is_empty() else 0} "
                      f"fetched={len(fetched)} flushed={len(done)} cum={total_rows}",
                      flush=True)
                last = None
                break
            except Exception as ex:  # noqa: BLE001 单日失败不中断整轮
                last = ex
                time.sleep(BACKOFF * attempt)
        if last is not None:
            prog["failed"][key] = f"{type(last).__name__}: {last}"
            _save_progress(prog)
            print(f"  [{i}/{len(todo)}] {key} FAILED {type(last).__name__}: "
                  f"{last}", flush=True)

        # 年份翻页：年份变更时落盘上一年的整年数据。
        if cur_year is None:
            cur_year = d.year
        elif d.year != cur_year:
            n = _flush_year(cur_year)
            print(f"  >>> flushed year={cur_year} rows={n} done={len(done)}",
                  flush=True)
            cur_year = d.year


    if cur_year is not None:
        n = _flush_year(cur_year)
        print(f"  >>> flushed year={cur_year} rows={n} done={len(done)}",
              flush=True)

    _save_progress(prog)
    dur = time.time() - t0
    print(f"\n[em-backfill] DONE duration={dur:.0f}s rows_fetched={total_rows} "
          f"done={len(done)} failed={len(prog['failed'])}", flush=True)
    if prog["failed"]:
        print("failed days:", list(prog["failed"])[:20], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
