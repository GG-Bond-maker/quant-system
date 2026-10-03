"""Phase 0 可行性探针（一次性，用完可留作复查）。

回答三个必须先验证、否则会浪费数小时的问题：

1. 全市场快照能否拉到？多少行？其中北交所多少只？
2. 🔴 **北交所代码能否真的抓到日线？** —— ``expand_universe_2500.py:83`` 注释声称
   「新浪日线不支持北交所」，但本项目判据是「注释里写的 ≠ 代码里做的」，
   必须实测：注释可能过时（新浪接口演进），也可能只说对了一半。
3. 代码 -> symbol 后缀映射对北交所（4/8/92 段）是否安全？注释提到
   「92 会被 ``_sina_symbol`` 误映射为 sh」——若为真，则抓到的会是**错误标的的数据**，
   属于比"抓不到"更危险的静默错配。
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.ingest import akshare_adapter as ada  # noqa: E402

print("=" * 72)
print("Phase 0 · 全市场覆盖可行性探针")
print("=" * 72)

# ---------- 1. 快照 ----------
t0 = time.monotonic()
print("\n[1] 拉取全市场快照 akshare.stock_zh_a_spot() ...")
try:
    df = ada._safe_call(ada.get_akshare().stock_zh_a_spot)
    print(f"    成功：{len(df)} 行，耗时 {time.monotonic() - t0:.1f}s")
    print(f"    列名：{list(df.columns)[:10]}")
except Exception as e:  # noqa: BLE001
    print(f"    ❌ 快照失败：{type(e).__name__}: {e}")
    raise SystemExit(1)

code_col = next((c for c in ("代码", "code", "symbol") if c in df.columns), None)
print(f"    代码列 = {code_col!r}")
codes = [str(x).strip() for x in df[code_col].tolist()]
print(f"    代码样本：{codes[:5]}")

# 前缀分布（新浪返回的代码通常带 sh/sz/bj 前缀，或纯 6 位）
pref = Counter(c[:2].lower() for c in codes)
print(f"    前缀分布：{pref.most_common(14)}")

# 归一化后按段分类
def _norm(c: str) -> str:
    c = c.lower().replace("sh", "").replace("sz", "").replace("bj", "").strip()
    return c.zfill(6)

norm = [_norm(c) for c in codes]
seg = Counter()
bse_codes: list[str] = []
for c in norm:
    if c[:2] in ("43", "83", "87", "88", "92"):
        seg["北交所段(43/83/87/88/92)"] += 1
        bse_codes.append(c)
    elif c[:1] in ("6", "9"):
        seg["沪市(6/9)"] += 1
    elif c[:1] in ("0", "2", "3"):
        seg["深市(0/2/3)"] += 1
    else:
        seg[f"其它({c[:1]})"] += 1
print(f"    代码段分布：{dict(seg)}")
print(f"    北交所候选（前 8）：{bse_codes[:8]}")

# ---------- 2. 北交所实测 ----------
print("\n[2] 🔴 实测北交所代码能否抓到日线（注释声称不支持，须证伪/证实）")
from app.data.ingest.akshare_adapter import daily_source_priority, fetch_daily_bar  # noqa: E402

ds = daily_source_priority()
print(f"    降级链 = {ds}")
samples = (bse_codes[:4] or ["920819", "832000", "430047", "871981"])
print(f"    抽样 {len(samples)} 只：{samples}")
ok = fail = 0
for c in samples:
    try:
        rows = fetch_daily_bar(c, "2026-09-01", "2026-09-30", ds)
        n = len(rows)
        last = rows[-1]["date"] if rows else None
        if n:
            ok += 1
            print(f"      ✅ {c}: {n} 行，末日 {last}，收盘 {rows[-1].get('close')}")
        else:
            fail += 1
            print(f"      ⚠️ {c}: 空返回（0 行）")
    except Exception as e:  # noqa: BLE001
        fail += 1
        print(f"      ❌ {c}: {type(e).__name__}: {str(e)[:110]}")
print(f"    北交所实测：成功 {ok} / 失败 {fail}")

# ---------- 3. 后缀映射安全性 ----------
print("\n[3] 🔴 代码 -> 后缀映射安全性（92 段是否会被误映射为 sh）")
try:
    from app.data.etf import _market_for_code  # noqa: E402
    for c in ["600519", "000001", "300750", "920819", "832000", "430047", "871981"]:
        try:
            print(f"      _market_for_code({c}) = {_market_for_code(c, 'cn')}")
        except Exception as e:  # noqa: BLE001
            print(f"      _market_for_code({c}) 异常: {type(e).__name__}: {e}")
except Exception as e:  # noqa: BLE001
    print(f"      (etf._market_for_code 不可用: {e})")

print("\n" + "=" * 72)
