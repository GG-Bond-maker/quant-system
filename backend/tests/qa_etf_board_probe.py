"""QA 探针（非 pytest）：对**运行中的**后端做 ETF 中心只读探测。

只发 GET 请求、不写任何数据、不改业务代码。用法::

    python backend/tests/qa_etf_board_probe.py

输出：9 个板块 Tab × 5 个国家 Tab 的 total、组合场景、排序三态、分页边界、筛选器。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"

# 运行中的后端用根目录 .env 里的 ADMIN_TOKEN（测试用的默认 token 对它无效）。
def _load_token() -> str:
    p = Path(__file__).resolve().parents[2] / ".env"
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("ADMIN_TOKEN="):
            return line.split("=", 1)[1].strip()
    return "aqp-dev-token-change-me"


TOKEN = _load_token()

BOARDS = ["市场总览", "宽基ETF", "行业ETF", "主题ETF", "Smart Beta",
          "跨境ETF", "商品型", "债券型", "货币型"]
COUNTRIES = [("all", "全部"), ("cn", "中国"), ("us", "美国"),
             ("jp", "日本"), ("kr", "韩国")]


def call(path: str, **params) -> tuple[dict, float]:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE}{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body, (time.time() - t0) * 1000


def get_list(**params) -> tuple[dict, float]:
    body, ms = call("/api/v1/etf/list", **params)
    return body.get("data", {}), ms


def main() -> int:
    print("=" * 78)
    print("A. 板块 Tab 逐个切换（country=all）")
    print("=" * 78)
    board_totals: dict[str, int] = {}
    for b in BOARDS:
        params = {"board": None if b == "市场总览" else b, "page_size": 1}
        d, ms = get_list(**params)
        board_totals[b] = d.get("total", -1)
        print(f"  {b:<10s} total={d.get('total'):>6}  "
              f"首条={((d.get('items') or [{}])[0].get('name') or '-')[:18]:<18} "
              f"{ms:.0f}ms")
    zero = [b for b, t in board_totals.items() if t == 0]
    print(f"  → 恒为 0 条的 Tab: {zero or '无'}")

    print()
    print("=" * 78)
    print("B. 国家 Tab 逐个切换（board=市场总览）")
    print("=" * 78)
    for key, label in COUNTRIES:
        d, ms = get_list(country=key, page_size=1)
        print(f"  {label}({key})  total={d.get('total'):>6}  {ms:.0f}ms")

    print()
    print("=" * 78)
    print("C. 组合场景：国家 × 板块")
    print("=" * 78)
    for key, label in COUNTRIES:
        row = []
        for b in BOARDS[1:]:
            d, _ = get_list(country=key,
                            board=None if b == "市场总览" else b, page_size=1)
            row.append(f"{b}={d.get('total')}")
        print(f"  {label}: " + "  ".join(row))

    print()
    print("=" * 78)
    print("D. 排序三态（涨跌幅 / 规模 / 成交额）")
    print("=" * 78)
    for key in ("pct", "size", "amount"):
        for st in ("desc", "asc", ""):
            d, _ = get_list(sort=st or "", dir="desc" if not st else st,
                            page_size=5)
            vals = [x.get({"pct": "pct", "size": "size_yi",
                           "amount": "amount"}[key])
                    for x in (d.get("items") or [])]
            print(f"  sort={key or '(空)'} dir={st or 'desc(default)':<12} "
                  f"applied={d.get('sort_applied')}/{d.get('dir_applied')} "
                  f"前5={vals}")
    # null 恒末尾校验
    d, _ = get_list(sort="pct", dir="desc", page_size=100, country="all")
    vals = [x.get("pct") for x in (d.get("items") or [])]
    nn = [v for v in vals if v is not None]
    nulls_at_tail = all(v is None for v in vals[len(nn):])
    print(f"  → 降序 null 恒末尾: {nulls_at_tail}（非空 {len(nn)} / 共 {len(vals)}）")
    d, _ = get_list(sort="pct", dir="asc", page_size=100, country="all")
    vals = [x.get("pct") for x in (d.get("items") or [])]
    nn = [v for v in vals if v is not None]
    print(f"  → 升序 null 恒末尾: "
          f"{all(v is None for v in vals[len(nn):])}"
          f"（非空 {len(nn)} / 共 {len(vals)}）")

    print()
    print("=" * 78)
    print("E. 分页边界")
    print("=" * 78)
    d, _ = get_list(page=1, page_size=20)
    total = d.get("total", 0)
    total_pages = max(1, -(-total // 20))
    print(f"  total={total} totalPages={total_pages}")
    for p in (1, total_pages, total_pages + 1, total_pages + 50, 9999):
        d, ms = get_list(page=p, page_size=20)
        print(f"  page={p:<6} 返回 rows={len(d.get('items') or [])} "
              f"total={d.get('total')} {ms:.0f}ms")
    # page_size 边界
    for ps in ("0", "1", "100", "101"):
        try:
            d, _ = get_list(page=1, page_size=ps)
            print(f"  page_size={ps:<4} -> rows={len(d.get('items') or [])}")
        except Exception as e:  # noqa: BLE001
            print(f"  page_size={ps:<4} -> HTTP异常 {type(e).__name__}: {e}")

    print()
    print("=" * 78)
    print("F. 筛选器")
    print("=" * 78)
    d, _ = get_list(page_size=1)
    print(f"  options.types  = {d.get('options', {}).get('types')}")
    print(f"  options.boards = {d.get('options', {}).get('boards')}")
    for t in ("股票型", "债券型", "商品型", "货币型", "跨境型"):
        d, _ = get_list(etype=t, page_size=1)
        print(f"  etype={t:<6} total={d.get('total')}")
    for lo, hi in ((0, None), (100, None), (None, 1), (1, 10)):
        d, _ = get_list(min_size=lo, max_size=hi, page_size=1)
        print(f"  size[{lo},{hi}] total={d.get('total')}")
    for f, t in (("2000-01-01", None), (None, "2015-01-01"),
                 ("2020-01-01", "2021-01-01")):
        d, _ = get_list(inception_from=f, inception_to=t, page_size=1)
        print(f"  inception[{f},{t}] total={d.get('total')}")

    print()
    print("=" * 78)
    print("G. 冷/热路径耗时（同一 URL 连打两次）")
    print("=" * 78)
    for path in ("/api/v1/etf/overview", "/api/v1/etf/list", "/api/v1/etf/hot",
                 "/api/v1/etf/scale", "/api/v1/etf/flow"):
        for i in (1, 2):
            body, ms = call(path)
            d = body.get("data", {}) or {}
            st = (d.get("today", {}) or {}).get("status") or d.get("status") or "-"
            print(f"  {path:<28} 第{i}次 {ms:>7.0f}ms status={st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
