"""QA#3 独立证伪：§⑤「前端 null 伪方向」家族清扫**漏掉的同族位点**（源码断言锁）。

背景：`§⑤` 那一轮声称清掉 12 处「可空数值用 `?? 0` 兜出红/绿方向」的缺陷。
QA#3 独立按同一判据在 `frontend/src/**` 全域再扫，发现 5 处**同族残留**：

判据（与工程师一致）：`x ?? 0`（或 `A && A.x > 0 ? 红 : 绿` 这种把 null 落进 else 的写法）
同时满足
  (1) 参与比较 / 决定颜色方向；
  (2) 同一 UI 位已存在诚实的「未知」表达（`—` / 中性色）却被绕过落进**非中性红/绿**。

每处的**值**都已诚实显示 `—`，只有 **tone/颜色**这一支仍把 null 当确定方向 ——
即「值说未知、颜色表方向」的自相矛盾，正是本轮要消灭的形态。

⚠️ 本文件是 QA#3 的新增证据：**当前应全红**（缺陷未修）。修法同 §⑤：null 走中性
（text 用 `text-ink-muted`），**非 null 行为逐字节不变**。修好后本文件转绿。
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
SRC = PROJECT_ROOT / "frontend" / "src"

if not SRC.exists():  # pragma: no cover
    pytest.skip("前端目录不在本工作区", allow_module_level=True)


def _read(*parts: str) -> str:
    return SRC.joinpath(*parts).read_text(encoding="utf-8")


def test_leak_research_factor_icir_mean_ic_null_not_green():
    """L1 `pages/Research/parts.tsx:49`：因子 IC 表 `mean_ic` 列。

    `FactorIcirRow.mean_ic: number | null`（`api/research.ts:17`），值已 `—`；
    但 tone 写作 `r.mean_ic != null && r.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'`
    ⇒ `mean_ic == null` 落到 **绿**（把"无数据"说成"负 IC"）。
    """
    src = _read("pages", "Research", "parts.tsx")
    assert "r.mean_ic == null ? 'text-ink-muted'" in src, (
        "mean_ic 缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "r.mean_ic != null && r.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `!= null && > 0 ? 红 : 绿` 让 null 落进绿"
    )


def test_leak_screener_win_rate_null_not_green():
    """L2 `pages/Screener/StatsCards.tsx:116`：「今日胜率」卡。

    `win_rate: number | null`（`types/p1.ts:47`），value 已 `—`；
    tone 写作 `t.win_rate != null && t.win_rate >= 50 ? 't-up' : 't-down'`
    ⇒ null 落到 **t-down（绿）**。同文件 `:124` 的 avg_pct 已修（本处漏了）。
    """
    src = _read("pages", "Screener", "StatsCards.tsx")
    assert "t.win_rate == null ? undefined" in src or "t.win_rate == null ? 'text-ink-muted'" in src, (
        "win_rate 缺失时 tone 未走中性（仍会落进 t-down 绿）"
    )
    assert "t.win_rate != null && t.win_rate >= 50 ? 't-up' : 't-down'" not in src, (
        "仍在用 `!= null && >= 50 ? t-up : t-down` 让 null 落进绿"
    )


def test_leak_factorstudio_lab_metrics_mean_ic_null_not_green():
    """L3 `pages/FactorStudio/FactorLab.tsx:135`：因子库 Mean IC 列。

    `metrics: { mean_ic: number; ... } | null`（`api/production.ts:96`），value 已 `—`；
    tone 写作 `f.metrics && f.metrics.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'`
    ⇒ `metrics == null` 落到 **绿**。
    """
    src = _read("pages", "FactorStudio", "FactorLab.tsx")
    assert "f.metrics == null ? 'text-ink-muted'" in src, (
        "metrics 缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "f.metrics && f.metrics.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `metrics && mean_ic > 0 ? 红 : 绿` 让 null 落进绿"
    )


def test_leak_factorstudio_lab_quintile_annual_null_not_green():
    """L4 `pages/FactorStudio/FactorLab.tsx:173`：5 分组年化。

    `quintile_annual: Record<string, number | null>`（`api/production.ts:83`），
    value 为 null 时已显示 `—`；tone 写作 `v != null && v > 0 ? 'text-red-600' : 'text-emerald-600'`
    ⇒ null 落到 **绿**。
    """
    src = _read("pages", "FactorStudio", "FactorLab.tsx")
    assert "v == null ? 'text-ink-muted'" in src, (
        "分组年化缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "v != null && v > 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `v != null && v > 0 ? 红 : 绿` 让 null 落进绿"
    )


def test_leak_orderdesk_total_return_null_not_green():
    """L5 `pages/OrderDesk/index.tsx:280`：账户「累计收益率」。

    该值是派生态，可为 null（`:181` `account && initial_cash > 0 ? ... : null`），
    value 已 `—`；tone 写作 `totalReturn != null && totalReturn >= 0 ? 'text-red-600' : 'text-emerald-600'`
    ⇒ null 落到 **绿**。
    """
    src = _read("pages", "OrderDesk", "index.tsx")
    assert "totalReturn == null ? 'text-ink-muted'" in src, (
        "累计收益率缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "totalReturn != null && totalReturn >= 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `!= null && >= 0 ? 红 : 绿` 让 null 落进绿"
    )


# =====================================================================
# 第 3 批：结构性穷举（按"判色位点"枚举，非按 bug 形状匹配）新发现的泄漏
# 以下均为**当前必红** —— 缺陷未修。修法同上：null/undefined ⇒ 中性。
# =====================================================================

def test_leak_ai_sentiment_gauge_unknown_score_not_pinned_to_zero():
    """L6 `pages/MarketOverview/AiPicksPanel.tsx:55`：AI 情绪仪表盘。

    `SentimentBlock.score?: number`（`types/stock.ts:489`，可选 ⇒ 可 undefined），
    且 `sentiment?: SentimentBlock`（`:520/:546`）整体可 undefined；
    `const score = sentiment?.score ?? 0;` 把"未知"压成 **0**。
    仪表刻度是 **0~100**：0 不是"平"，而是刻度**端点**= 极度恐慌（`#16A34A` 绿），
    detail formatter 渲染出 `0 · —`（值说未知、指针钉在极端恐慌）。
    ⇒ 新形状：`?? 0` 落在**有界刻度的端点**而非中点，0 ≠ 中性。
    """
    src = _read("pages", "MarketOverview", "AiPicksPanel.tsx")
    # 负向：任何把缺失 score 兜成 0 的写法都不允许。用 `score ?? 0` / `score || 0`
    # 这种**后缀匹配**可一次性覆盖 `sentiment?.score ?? 0`、`sentiment.score ?? 0`、
    # `score || 0` 等全部变体 —— 专门防「看起来改了、其实指针还在 0」的空修。
    for bad in ("score ?? 0", "score || 0", "score ?? 0.0", "score || 0.0"):
        assert bad not in src, (
            f"情绪仪表仍把缺失 score 兜成 0（命中 `{bad}`）：0 是 0~100 刻度的**端点**"
            "（=极度恐慌），不是中性点（50）；不得落 0"
        )
    # 正向：必须真的存在对 score 可空性的处理。只删掉 `?? 0` 而不判空 ⇒ 空修
    #（指针仍会落在 0，或干脆不渲染但也没区分"未知"）。
    #
    # ⚠️ 只接受**严格空值判断**，**刻意排除真假判断 `!score`**：
    # score 是 0~100 有界刻度上的语义值，**0 是合法真实值（=极度恐慌）**。
    # `!score` 在 score===0 时同样为真 ⇒ 会把"真实打 0 分的极度恐慌"当成
    # "没有数据"而不画 —— 方向相反、但同样是谎报（用「未知」吞掉一个真实极值）。
    # 这正是形状⑦的特殊性：0 在这里不是"没有值"，而是"最极端的值"。
    _NULL_GUARDS = ("score == null", "score != null", "score === null", "score !== null",
                    "score === undefined", "score !== undefined",
                    "score ?? null", "score ?? undefined",
                    "Number.isFinite(score)", "Number.isNaN(score)")
    assert any(g in src for g in _NULL_GUARDS), (
        "未见对情绪 score 的判空处理：仅删掉 `?? 0` 而不判空属于空修"
        "（未区分「未知」，指针仍可能落在 0）"
    )


def test_leak_datacenter_failed_count_unknown_not_emerald():
    """L7 `pages/DataCenter/index.tsx:834`：同步失败数进度条。

    `failed_count?: number`（`types/datacenter.ts:87`，可选 ⇒ 可 undefined）；
    `(sync?.failed_count ?? 0) > 0 ? 'bg-amber-400' : 'bg-emerald-400'` 且 `width:100%`
    ⇒ 失败数未知时落 **bg-emerald-400 满条绿**（="全部成功/健康"）。
    与 `sync.error` 的红框分属两层，绿条会把"未知"说成"零失败"。
    """
    src = _read("pages", "DataCenter", "index.tsx")
    assert "(sync?.failed_count ?? 0) > 0 ? 'bg-amber-400' : 'bg-emerald-400'" not in src, (
        "失败数未知时进度条仍落 bg-emerald-400（满条绿=零失败）；须有中性/未知分支"
    )
    # 变体兜底（`|| 0` 同样把缺失压成 0 ⇒ 同样落"全部成功"）
    assert "failed_count || 0" not in src, (
        "失败数仍用 `|| 0` 兜底 ⇒ 未知时同样落满条绿（零失败）"
    )


def test_leak_stockdetail_chip_curve_unknown_price_not_all_green():
    """L8 `pages/StockDetail/index.tsx:562` + `:588`：筹码密度曲线。

    `ChipBlock.current_price: number | null`（`types/stock.ts:250`）；
    `const cur = c.current_price ?? 0;` ⇒ 现价未知时 cur=0，
    而曲线每格判 `p.price <= cur ? 'bg-red-300' : 'bg-green-300'`：
    真实价格恒 > 0 ⇒ **整条曲线全落 bg-green-300 = 全部套牢**。
    ⚠️ 同函数 `:567`（平均成本 tone）已写 `c.avg_cost != null && cur > 0` ——
    **同一函数内已有 `cur > 0` 先例，`:588` 却没判**，足证是遗漏而非有意。
    """
    src = _read("pages", "StockDetail", "index.tsx")
    assert "c.current_price ?? 0" not in src, (
        "筹码分布仍用 `?? 0` 兜现价：现价未知 ⇒ 整条筹码曲线落 bg-green-300（全部套牢）"
    )
    assert "current_price || 0" not in src, (
        "筹码分布仍用 `|| 0` 兜现价 ⇒ 未知时整条曲线同样全绿（全部套牢）"
    )
    # 正向：必须真的判空（防"只删掉 ?? 0、不判空"的空修）。
    # 接受两种等价修法之一：
    #   (a) 沿用同函数 :567 的 `cur > 0` 数值判断 —— 可接受：股价为 0 本就不是合法价格，
    #       故数值判断不会像 D1 那样把"真实极值"误判成未知；
    #   (b) 严格空值判断 `current_price == null` / `!= null` / `=== null` / `!== null`。
    # ⚠️ 注意：此处**不**接受 `!cur` 这类真假判断（理由同 D1：会把合法值当缺失）。
    _CUR_GUARDS = ("current_price == null", "current_price != null",
                   "current_price === null", "current_price !== null")
    assert src.count("cur > 0") >= 2 or any(g in src for g in _CUR_GUARDS), (
        "筹码密度曲线既未沿用 :567 的 `cur > 0` 先例、也未对 current_price 做严格判空；"
        "现价未知时不得整条染绿（且不得用 `!cur` 这类真假判断，见 D1 的 0 值教训）"
    )


def test_leak_breadth_up_down_unknown_not_zero_percent():
    """L9 `pages/MarketOverview/BreadthPanel.tsx:84-85`（渲染于 `:129`）：红/绿盘占比。

    `HeatBlock.up?/down?/flat?: number`（`types/stock.ts`，全部可选 ⇒ 可 undefined）；
    `upPct = Math.round(((heat?.up ?? 0) / total) * 100)` ⇒ up 缺失时算成 **0**
    并渲染「红盘 0%」（`:129`，text-up 红）；down 同理。
    ⚠️ 同文件 `:124` 的网格格已写 `n != null && n > 0 ? fmtNum(n,0) : '—'` ——
    **同一组件内已有判空先例，占比却不判**，足证是遗漏。
    """
    src = _read("pages", "MarketOverview", "BreadthPanel.tsx")
    assert "((heat?.up ?? 0) / total)" not in src, (
        "红盘占比仍用 `heat?.up ?? 0` 兜底 ⇒ up 缺失时显示「红盘 0%」（把未知说成 0）"
    )
    assert "((heat?.down ?? 0) / total)" not in src, (
        "绿盘占比仍用 `heat?.down ?? 0` 兜底 ⇒ down 缺失时显示「绿盘 0%」（把未知说成 0）"
    )
    # 变体兜底
    assert "heat?.up || 0" not in src and "heat?.down || 0" not in src, (
        "红/绿盘占比仍用 `|| 0` 兜底 ⇒ 缺失时同样显示 0%"
    )


def test_leak_stockdetail_max_drawdown_null_tone_neutral():
    """L10（**边界项 / 低危**）`pages/StockDetail/index.tsx:525`：最大回撤 tone 硬编码。

    `RiskBlock.max_drawdown: number | null`（`types/stock.ts:275`）；
    值已判空显 `—`，但 `tone="t-down"` 是**常量** ⇒ null 时仍染绿（=跌）。
    严格按判据（可空 + 落非中性）成立；但"最大回撤"按定义恒为下跌，
    属**定义性配色**而非捏造信息 —— 与 Backtest 侧 `up: false` 同款，
    严重度低。若团队认定"回撤恒负"可接受，则本条可降级为不改。
    """
    src = _read("pages", "StockDetail", "index.tsx")
    assert 'tone="t-down"' not in src, (
        "最大回撤 tone 硬编码 t-down：max_drawdown 为 null 时值显 '—' 却仍染绿（跌）"
    )
