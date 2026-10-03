"""qfq 锚定不变量验收：``daily_bar_qfq`` 是否已被正确重建。

判据（2026-10-02 实测确立）
--------------------------
``step_rebuild_qfq`` 由本地 raw+hfq 全量重算 qfq，公式为 ``qfq = hfq / F_last``
（**锚定最新一日**）。取锚定日（末日）两侧：

    qfq_last_close == raw_last_close      （相对误差 < 1e-6）
    qfq_last_date  == raw_last_date

实测对照（2026-10-02 21:36）：

| 标的 | 状态 | raw 末日 | qfq 末日 | 结果 |
|---|---|---|---|---|
| 000001.SZ | 本次已重建 | 2026-09-30 | 2026-09-30 | ✅ 相等 |
| 000002.SZ | 本次已重建 | 2026-09-30 | 2026-09-30 | ✅ 相等 |
| 600000.SH | **尚未轮到** | 2026-09-30 | 2026-09-17 | ❌ 停在旧的尾部陈旧态 |

⇒ 该不变量可以**机器判定「某个标的的 qfq 是否已被重建」**，因此适合做重建后的收尾验收：
凡是 qfq 末日早于 raw 末日的，就是**还没被重建到**（或 hfq 尾部缺失）。

🔴 为什么不能只判「文件存在」
--------------------------------
第一次重建在 3425/5223 处被应用重启杀死，但那 3425 个目录**全部存在** ——
「文件存在」完全无法区分「已重建」与「半途中断」。锚定不变量可以。

用法（cd backend）
------------------
    ./.venv/Scripts/python.exe scripts/verify_qfq_anchor.py
    ./.venv/Scripts/python.exe scripts/verify_qfq_anchor.py --limit 200
    ./.venv/Scripts/python.exe scripts/verify_qfq_anchor.py --json ../data/qfq_anchor.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "backend"))

try:
    import polars as pl
except Exception as exc:  # pragma: no cover - 环境自检
    print(f"[qfq-anchor] 无法导入 polars（请用 backend/.venv/Scripts/python.exe）：{exc}")
    raise

REL_LO, REL_HI = -1e-6, 1e-6


def _last_row(root: Path, rel: str, sym: str, col: str = "close"):
    """只读**最晚年份分区**取末日 (date, close) —— 分区按 year 落盘 ⇒ 最晚日期必在末分区。"""
    base = root / rel / f"symbol={sym}"
    if not base.exists():
        return None
    files = sorted(base.glob("year=*.parquet"))
    if not files:
        return None
    try:
        df = pl.read_parquet(files[-1], columns=["date", col]).sort("date")
    except Exception as e:  # noqa: BLE001 - 单标的失败不应中断全量扫描
        return {"error": f"{type(e).__name__}: {e}"}
    if df.is_empty():
        return None
    return {"date": str(df["date"][-1]), "close": float(df[col][-1])}


def main() -> int:
    ap = argparse.ArgumentParser(description="qfq 锚定不变量验收（qfq 末日 == raw 末日 且收盘相等）")
    ap.add_argument("--data-root", default=None, help="覆盖 DATA_ROOT（默认取 app 配置）")
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 只（0 = 全量）")
    ap.add_argument("--json", default=None, help="把结果写 JSON（便于回填报告）")
    args = ap.parse_args()

    if args.data_root:
        import os
        os.environ["DATA_ROOT"] = str(Path(args.data_root).expanduser().resolve())
    from app.core.config import get_settings

    get_settings.cache_clear()
    root: Path = get_settings().DATA_ROOT

    from app.data.parquet_store import read_all_symbols

    raw_syms = set(read_all_symbols("daily_bar"))
    qfq_syms = set(read_all_symbols("daily_bar_qfq"))
    hfq_syms = set(read_all_symbols("daily_bar_hfq"))
    syms = sorted(raw_syms | qfq_syms | hfq_syms)
    if args.limit > 0:
        syms = syms[: args.limit]

    ok: list[str] = []
    no_raw: list[str] = []          # raw 缺失 ⇒ 无锚定基准（不可得标的，非缺陷）
    qfq_missing: list[str] = []     # raw 有、qfq 无 ⇒ 真的没重建
    stale: list[dict] = []          # 末日不一致 ⇒ qfq 未跟上 raw（未重建到 / hfq 尾部缺失）
    value_bad: list[dict] = []      # 末日一致但收盘不等 ⇒ 派生公式有问题（硬失败）
    read_err: list[dict] = []

    for i, sym in enumerate(syms, 1):
        r = _last_row(root, "daily_bar", sym)
        q = _last_row(root, "daily_bar_qfq", sym)
        if isinstance(r, dict) and "error" in r:
            read_err.append({"symbol": sym, "side": "raw", "error": r["error"]})
            continue
        if isinstance(q, dict) and "error" in q:
            read_err.append({"symbol": sym, "side": "qfq", "error": q["error"]})
            continue
        if r is None:
            no_raw.append(sym)
            continue
        if q is None:
            qfq_missing.append(sym)
            continue
        if q["date"] != r["date"]:
            stale.append({"symbol": sym, "raw_date": r["date"], "qfq_date": q["date"]})
            continue
        base = r["close"]
        rel = (q["close"] - base) / base if base else float("nan")
        if not (REL_LO <= rel <= REL_HI):
            value_bad.append({"symbol": sym, "date": r["date"],
                              "raw_close": base, "qfq_close": q["close"], "rel": rel})
            continue
        ok.append(sym)
        if i % 1000 == 0:
            print(f"  ...已扫 {i}/{len(syms)}（ok={len(ok)} stale={len(stale)}）", flush=True)

    total = len(syms)
    print(f"\n=== qfq 锚定不变量验收（DATA_ROOT={root}）===")
    print(f"扫描 {total} 只："
          f"✅ ok={len(ok)}  "
          f"⚠️ 尾部未跟上={len(stale)}  "
          f"⚠️ raw 缺失（不可得）={len(no_raw)}  "
          f"❌ qfq 缺失={len(qfq_missing)}  "
          f"❌ 收盘不等={len(value_bad)}  "
          f"❌ 读取失败={len(read_err)}")

    for name, bucket in (("尾部未跟上", stale), ("qfq 缺失", qfq_missing),
                         ("收盘不等", value_bad), ("读取失败", read_err)):
        if bucket:
            head = bucket[:10]
            print(f"\n[{name}] 前 {len(head)} / {len(bucket)}：")
            for item in head:
                print(f"  {item}")

    # 硬失败只算「派生公式错」（收盘不等）与「读取失败」；
    # 「尾部未跟上」= 还没重建到，属**进度**判据而非缺陷；「raw 缺失」= 不可得标的。
    hard = bool(value_bad or read_err)
    if hard:
        print("\n结论：不通过（存在派生公式错误或读取失败）")
    elif stale:
        print("\n结论：未完成（仍有 qfq 未跟上 raw 末日 —— 重建尚未跑完或 hfq 尾部缺失）")
    else:
        print("\n结论：通过（全部标的 qfq 末日与 raw 一致且收盘相等）")

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps({
            "data_root": str(root), "scanned": total, "ok": len(ok),
            "stale_tail": stale, "no_raw": no_raw, "qfq_missing": qfq_missing,
            "value_mismatch": value_bad, "read_error": read_err,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON：{out}")

    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
