"""前端 B9a / B8 / B9b / B9c + §4.13 披露消费（审核报告 §8.2 第 19 项）——源码断言锁。

前端**无测试框架**（项目既有做法：`test_backtest_price_basis_disclosure.py:222`、
`test_monitor_trigger_caliber.py:354`、`test_risk_caliber_consistency.py:156`），
故对 `frontend/src/**` 直接做字符串断言：每个断点对应审核报告 §4.7 的一条缺陷，
断言"修复特征必须在"，并断言"旧的失真实现必须不在"，防止后续改版静默回退。

覆盖清单（见 `docs/audit-2026-09-18/AQP-全栈审核报告.md` §4.7 表 429-484 行）：

* B9a 行情/个股：F-01/F-02（竞态+旧状态驻留）、F-03/F-04（指数按位置取）、
  F-05（走势列无数据）、F-06（degraded 未披露）、F-07（导出越权可见）
* B8 基础设施：B8-06（自选行情竞态）、B8-07（同名类型混淆）、B8-10（错误态吞消息）
* B9b 量化页：I-4（优化参数无上界）、I-5/I-6（组合搜索竞态/基准退化/空值）、
  I-7（日报竞态）、I-8/I-9/I-12（面板竞态与 data_warnings）、I-10（不可中止）、
  I-11（口径未披露）、I-13（缺失指标 toFixed 崩）
* B9c 运维页：C-1（hooks 顺序）、C-2（截断当全量）、C-3（母单窗口）、
  C-4（假清零）、C-5（饱和值/错误信息丢弃）、C-6（0 覆盖假清白）、
  C-7（不存在的 DAG 节点）、C-8（PENDING 染红/耗时 0.0s）、C-9（角色门控）、
  C-10（未接线滑条）、C-11（autoSync 硬编码 + 静默失败）、C-20（熔断不可读当正常）、
  C-21（下拉标签错位）、C-22（危险操作无确认）
* 同批：P1-22（useChart 依赖）、P1-23（viewer 必然 40300 的调用点）、
  P1-24（ADMIN_TOKEN 迁移）
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
SRC = PROJECT_ROOT / "frontend" / "src"

if not SRC.exists():  # pragma: no cover - 工作区不带前端时跳过
    pytest.skip("前端目录不在本工作区", allow_module_level=True)


def _read(*parts: str) -> str:
    return (SRC.joinpath(*parts)).read_text(encoding="utf-8")


# ==================== B9a：行情 / 个股 ====================

def test_f01_f02_stock_detail_guards_stale_responses_and_clears_old_state():
    """F-01/F-02：切标的时旧状态驻留 + 旧响应覆盖新响应（`StockDetail/index.tsx:55-70`）。"""
    src = _read("pages", "StockDetail", "index.tsx")
    assert "const seq = ++seqRef.current;" in src, "缺少请求序号（无法判定响应是否过期）"
    assert "if (seq !== seqRef.current) return;" in src, "过期响应未被丢弃"
    assert "setProfile(null); setKline(null); setPredict(null); setPanels(null);" in src, (
        "切换标的前必须先清空旧标的状态，否则新数据到达前一直显示上一只的行情"
    )


def test_f03_f04_market_overview_indices_matched_by_code_not_position():
    """F-03/F-04：KPI 卡按**位置**取指数 ⇒ 顺序变化即错配；且占位符带 loading 伪装。"""
    src = _read("pages", "MarketOverview", "KpiCards.tsx")
    assert "items.find((i) => i.code === 'sh000001')" in src, "上证指数未按 code 匹配"
    assert "items.find((i) => i.code === 'sh000300')" in src, "沪深300未按 code 匹配"
    assert 'value="—" up loading' not in src, (
        "占位符仍带 loading 标记：数据缺失被渲染成「正在加载」，与真实缺数据无法区分"
    )


def test_f05_money_flow_panel_drops_dead_sparkline_column():
    """F-05：资金流面板的「走势」列后端从不返回（`MoneyFlowPanel.tsx`）。"""
    src = _read("pages", "MarketOverview", "MoneyFlowPanel.tsx")
    assert "MiniSpark" not in src, "仍是引用永不产出数据的 sparkline 组件"
    assert "走势" not in src, "「走势」列仍在：空列会让用户以为数据源坏了"


def test_f06_watchlist_discloses_degraded_dashboard():
    """F-06：`watchlist.py:264,360` 返回的 status=degraded/reason 此前被丢弃。"""
    types = _read("types", "watchlist.ts")
    assert "status?: 'ok' | 'degraded'" in types, "类型未声明 status"
    assert "reason?: string | null" in types, "类型未声明 reason"
    page = _read("pages", "Watchlist", "index.tsx")
    assert "dash?.status === 'degraded'" in page, "降级状态未在页面披露"
    assert 'role="alert"' in page, "降级提示缺少无障碍语义（role=alert）"


def test_f07_screener_export_hidden_for_viewer():
    """F-07 / P1-23：`GET /export/screener` 后端要求 researcher（`export.py:23`）。"""
    src = _read("pages", "Screener", "index.tsx")
    assert "const canExport = hasMinimumRole(role, 'researcher');" in src, (
        "导出按钮未按角色门控（viewer 点了必然 40300）"
    )
    assert "{canExport && (" in src, "导出按钮未真正隐藏"
    assert "F-07：" in src, "缺少对应的整改注释（便于后续追溯）"


# ==================== B8：基础设施 ====================

def test_b8_06_watchlist_quotes_ignore_stale_responses():
    """B8-06：自选行情并发请求乱序覆盖 + loading 卡死（`useWatchlistQuotes.ts`）。"""
    src = _read("hooks", "useWatchlistQuotes.ts")
    assert "const seqRef = useRef(0);" in src, "缺少序号守卫"
    assert "const seq = ++seqRef.current;" in src
    assert "if (seq !== seqRef.current) return;" in src, "旧响应仍会写状态"
    assert "if (seq === seqRef.current) setLoading(false);" in src, (
        "loading 未按序号收敛：被丢弃的请求也会清 loading"
    )


def test_b8_07_market_money_flow_block_renamed_apart_from_stock_one():
    """B8-07：行情侧与个股侧同名 `MoneyFlowBlock` 结构不同 ⇒ 消费端张冠李戴。"""
    types = _read("types", "stock.ts")
    assert "export interface MarketMoneyFlowBlock" in types, "行情侧类型未改名"
    assert "市场侧资金块（B8-07）" in types, "未留下改名依据的注释"
    heatmap = _read("components", "charts", "MarketHeatmap.tsx")
    assert "MarketMoneyFlowBlock" in heatmap, "热力图未改用行情侧类型"


def test_b8_10_error_state_keeps_backend_message():
    """B8-10：`ErrorState` 丢弃后端 message，用户只看到通用文案。"""
    src = _read("components", "ui", "index.tsx")
    assert "{message || '数据源暂时没有响应'}" in src, (
        "后端错误消息仍被吞掉（无法定位是权限、缺数据还是真故障）"
    )


# ==================== B9b：量化页 ====================

def test_i4_optimize_bounds_match_backend_caps():
    """I-4：参数优化组合数无上界 ⇒ 前端允许组合爆炸，后端 `param_search.py:74` 上限 500。"""
    src = _read("pages", "Backtest", "parts.tsx")
    assert "const MAX_CANDIDATES = 30;" in src, "单列表候选数无上界（后端 ≤30）"
    assert "const MAX_TRIALS = 500;" in src, "总试验数无上界（后端 max_trials=500）"


def test_i10_backtest_apis_accept_abort_signal():
    """I-10：回测请求不可中止 ⇒ 切页/重跑后旧响应继续占用并写状态。"""
    sbt = _read("api", "strategyBacktest.ts")
    assert "signal?: AbortSignal" in sbt, "strategyBacktest.run 未接受 signal"
    assert "{ signal }" in sbt, "signal 未透传给 fetch"
    bt = _read("api", "backtest.ts")
    assert bt.count("signal?: AbortSignal") >= 2, "runTopK / signalAnalysis 未都接受 signal"


def test_i10_i11_backtest_page_aborts_and_caches_last_result():
    """I-10/I-11：切 tab 重挂即重跑 + 结果不跨 tab 复用 + 流动性口径未披露。"""
    src = _read("pages", "Backtest", "index.tsx")
    assert "const abortRef = useRef<AbortController | null>(null);" in src
    assert "abortRef.current?.abort();" in src, "未中止在途请求"
    assert "if (ctrl.signal.aborted) return;" in src, "中止后的响应仍会写状态"
    assert "useEffect(() => () => abortRef.current?.abort(), []);" in src, "卸载未中止请求"
    assert "let cachedResult: StrategyBacktestResult | null = null;" in src, "结果未做模块级缓存"
    assert "if (!cachedResult) void run(f);" in src, "挂载仍无条件自动重跑"
    assert "流动性/摩擦口径：" in src, "流动性口径（冲击成本是否计入）未披露"


def test_i11_topk_panel_discloses_friction_and_rejected_trades():
    """I-11：`liquidity.note`/`friction_costs`/`rejected_trades` 后端早已返回、前端零渲染。"""
    src = _read("pages", "Backtest", "TopKPanel.tsx")
    assert "FRICTION_LABEL" in src and "delist_loss: '退市强平减记'," in src, "摩擦成本键未映射成中文口径"
    assert "REJECT_LABEL" in src and "liquidity_cap: '流动性上限'," in src, "拒单原因未映射"
    assert "成本与成交口径" in src, "缺少摩擦口径面板"
    assert "liquidity?.note" in src, "未渲染后端 liquidity.note"
    assert "rejected_trades" in src, "未渲染 rejected_trades"
    assert "universe_daily_bt" in src, "数据集口径未按后端实际返回值展示"
    types = _read("types", "p1.ts")
    assert "universe_scope" in types and "liquidity" in types, "结果类型未声明披露字段"


def test_i5_i6_portfolio_search_race_and_degenerate_benchmark():
    """I-5/I-6：搜索乱序覆盖 + 搜索失败被吞成「无匹配」+ 基准退化仍显示 beta/alpha。"""
    page = _read("pages", "Portfolio", "index.tsx")
    assert "const searchSeqRef = useRef(0);" in page, "搜索缺序号守卫"
    assert "searchSeqRef.current += 1; // 作废在途请求" in page, "清空输入未作废在途请求"
    assert "setSearchError(e instanceof ApiError ? e.message : '搜索失败');" in page, (
        "搜索失败被吞成空结果（用户读成「没有这只标的」）"
    )
    assert "const benchDegenerate = result != null" in page, "基准退化未识别"
    assert "benchDegenerate ? '—' : fmtNum(metrics.beta)" in page, "退化基准仍显示 beta"
    assert "const pct = (v: number | null | undefined) =>" in page, "百分比未做空值保护"
    api = _read("api", "portfolio.ts")
    assert "signal?: AbortSignal" in api, "portfolio.search 未接受 signal"


def test_i7_report_page_guards_stale_daily_report():
    """I-7：快速切换历史期 / 重新生成时旧日报覆盖新选择。"""
    src = _read("pages", "Report", "index.tsx")
    assert "const seqRef = useRef(0);" in src, "日报缺序号守卫"
    assert "if (seq !== seqRef.current) return;" in src, "旧响应仍会写状态"
    assert "if (!canRegenerate) return;" in src, "P1-23：viewer 仍可触发重新生成（后端要求 researcher）"


def test_i8_i9_i12_research_panels_guard_stale_and_disclose_warnings():
    """I-8/I-9/I-12：因子/CV/优化器面板竞态 + `data_warnings` 被丢弃。"""
    research = _read("pages", "Research", "index.tsx")
    for var in ("factorSeqRef", "cvSeqRef", "optSeqRef"):
        assert f"const {var} = useRef(0);" in research, f"{var} 序号守卫缺失"
    assert "if (!mountedRef.current || seq !== factorSeqRef.current) return;" in research
    assert "if (mountedRef.current && seq === cvSeqRef.current) setCv(value);" in research
    assert "if (mountedRef.current && seq === optSeqRef.current) setOptimize(value);" in research
    assert "数据质量提示：{impact.data_warnings.join('；')}" in research, (
        "I-12：后端 data_warnings（VWAP 坏点修正等）被前端丢弃"
    )
    cap = _read("pages", "CapacityAttribution", "index.tsx")
    assert "const capSeqRef = useRef(0);" in cap, "容量归因缺序号守卫"
    assert "if (seq !== capSeqRef.current) return;" in cap


def test_i13_factor_studio_tolerates_missing_metrics():
    """I-13：`top_expressions` 指标缺字段时 `.toFixed()` 崩（后端确实会缺）。"""
    types = _read("api", "production.ts")
    assert "top_expressions: Array<{ expr: string; fitness: number; mean_ic?: number | null;" in types, (
        "top_expressions 指标字段未声明为可选"
    )
    src = _read("pages", "FactorStudio", "index.tsx")
    assert "{e.mean_ic != null ? e.mean_ic.toFixed(4) : '—'}" in src, "缺失 mean_ic 未兜底"
    assert "{e.icir != null ? e.icir.toFixed(3) : '—'}" in src, "缺失 icir 未兜底"
    assert "{e.t_stat != null ? e.t_stat.toFixed(2) : '—'}" in src, "缺失 t_stat 未兜底"
    assert "{e.n_days ?? '—'}" in src, "缺失 n_days 未兜底"


# ==================== B9c：运维页 ====================

def test_c1_disk_gauge_calls_hooks_before_any_return():
    """C-1：`DiskGauge` 在 null 分支先 return ⇒ hooks 数量 3↔0，翻转一次整站白屏。"""
    src = _read("pages", "DataCenter", "index.tsx")
    assert "const option = useMemo<echarts.EChartsOption | null>(() => {" in src, (
        "useMemo 未提升到所有 return 之前（且需允许 null option）"
    )
    assert "const { ref } = useChart(option, 130);" in src, "useChart 未在 return 之前无条件调用"
    assert src.index("const { ref } = useChart(option, 130);") < src.index("if (percent == null) {"), (
        "useChart 仍在 null 早退之后：percent 翻转会改变 hooks 数量"
    )


def test_c2_datalist_total_is_backend_total_not_window_length():
    """C-2：`datacenter/instruments` 的 total 曾被当全量（后端已改独立 COUNT，前端须消费披露字段）。"""
    api = _read("api", "datacenter.ts")
    assert "returned?: number; truncated?: boolean; limit?: number" in api, (
        "未声明窗口披露字段"
    )
    src = _read("pages", "DataCenter", "index.tsx")
    assert "setPreviewReturned(r.returned ?? r.items.length);" in src
    assert "setPreviewTruncated(!!r.truncated);" in src
    assert "（列表仅前 ${previewReturned} 只，上限 ${previewLimit}）" in src, (
        "UI 未披露预览窗口（total 是真实总量、列表却是截断窗口）"
    )


def test_c3_order_desk_discloses_truncated_order_window():
    """C-3 / P2-8：母单列表曾只取最近 N 条且后端不返回 total/truncated，
    筛选/分页/计数全建立在窗口上。后端已补 `{items,total,returned,limit,truncated}`
    信封（desk.py `/orders`），前端改为消费真实总数与截断标记。"""
    api = _read("api", "production.ts")
    assert "total: number; returned: number; limit: number;" in api, (
        "orders 未声明后端 total/returned/limit 披露字段"
    )
    assert "truncated: boolean }>(`/api/v1/desk/orders?limit=${limit}`" in api, (
        "orders 未消费后端 truncated 标记"
    )
    src = _read("pages", "OrderDesk", "index.tsx")
    assert "const ORDER_WINDOW = 200;" in src
    assert "deskApi.orders(ORDER_WINDOW)" in src, "未按声明的窗口取数"
    assert "setOrdersTotal(ords.total);" in src, "未消费后端返回的真实总数"
    assert "setOrdersTruncated(ords.truncated);" in src, "未消费后端返回的截断标记"
    assert "母单总数" in src, "未向用户展示后端返回的真实总数"


def test_c4_alerts_mark_all_read_uses_backend_all_flag():
    """C-4：未读徽标与「全部已读」只覆盖 limit=50 窗口 ⇒ 假清零；后端支持 {all:true}。"""
    api = _read("api", "alerts.ts")
    assert "markAllRead: () => post<{ marked: number }>('/api/v1/alerts/events/read', { all: true })" in api, (
        "未使用后端 {all:true}：窗口外未读永远标不掉"
    )
    src = _read("pages", "Alerts", "index.tsx")
    assert "const UNREAD_SCAN_LIMIT = 200;" in src, "未读口径未独立扫描"
    assert "await alertsApi.markAllRead(); await load();" in src
    assert "setError(e instanceof ApiError ? e.message : '标记全部已读失败');" in src, (
        "已读失败仍被静默吞掉"
    )
    assert "≥${unreadCount} 条未读（已达扫描上限）" in src, "饱和未读未标注为「≥」"
    assert "未读不可读" in src, "读不到未读时仍显示 0（把未知当干净）"


def test_c5_alerts_discloses_saturated_failed_jobs_and_last_error():
    """C-5：`recent_truncated`/`recent_limit`/`truncated`/`supplement_limit`/`last_error` 曾被整体丢弃。"""
    src = _read("pages", "Alerts", "index.tsx")
    assert "const saturated = !!p.recent_truncated || !!p.truncated;" in src, (
        "饱和标志未消费：20 条触顶会被读成精确值"
    )
    assert "p.recent_limit ?? p.supplement_limit" in src, "触顶上限未披露"
    assert "if (p.last_error) bits.push(`最近错误：${String(p.last_error)}`);" in src, (
        "last_error 仍未展示"
    )


def test_c6_data_quality_never_claims_clean_without_coverage():
    """C-6：0 覆盖（0 只 / 0 行）时曾渲染绿色「未检出任何质量问题」。"""
    src = _read("pages", "DataQuality", "index.tsx")
    assert "const noCoverage = !!scan && (scan.symbols_scanned === 0 || scan.rows_scanned === 0);" in src
    assert "const partialCoverage = !!scan && !noCoverage && scan.truncated;" in src
    assert '本次扫描未覆盖任何数据（0 只 / 0 行），不能判定"无质量问题"' in src, (
        "0 覆盖仍可能显示绿色清白结论"
    )
    assert "未覆盖数据，不能判定通过" in src, "检查项分布仍显示「全部通过」"
    assert "窗口外未覆盖" in src, "截断扫描的结论未限定在窗口内"


def test_c7_pipeline_dag_has_no_phantom_desk_stage():
    """C-7：`/ops/dag` 只返回 5 个 stage（`ops.py:400-411`），前端却多画一个 desk 假节点。"""
    src = _read("pages", "Pipeline", "index.tsx")
    assert "const STAGE_ORDER = ['harvest', 'qc', 'features', 'infer', 'screener'];" in src, (
        "STAGE_ORDER 仍含后端不存在的 'desk'"
    )
    assert "'screener', 'desk'" not in src


def test_c8_pipeline_job_status_and_null_duration():
    """C-8：非 SUCCESS 一律染红（PENDING/RUNNING 也在跑）；duration_ms 为 NULL 时渲染 0.0s。"""
    src = _read("pages", "Pipeline", "index.tsx")
    assert "const JOB_STATUS_TONE: Record<string, string> = {" in src, "状态配色未按语义分档"
    assert "FAILED: 'text-red-600'," in src
    assert "JOB_STATUS_TONE[j.status] ?? 'text-amber-600'" in src, "未知/进行中状态仍被染红"
    assert "{j.duration_ms == null ? '—' : `${(j.duration_ms / 1000).toFixed(1)}s`}" in src, (
        "NULL 耗时被渲染成 0.0s"
    )
    api = _read("api", "production.ts")
    assert "duration_ms: number | null;" in api, "类型未如实声明 duration_ms 可为 NULL"


def test_c9_role_gating_for_researcher_and_admin_endpoints():
    """C-9 / P1-23：viewer 级页面无角色门控 ⇒ 一进页就 40300（含挂载即越权 test）。"""
    dc = _read("pages", "DataCenter", "index.tsx")
    assert "const canResearch = hasMinimumRole(role, 'researcher');" in dc
    for endpoint, call in (
        ("logs(60)", "canResearch ? datacenterApi.logs(60) : Promise.resolve(null)"),
        ("status()", "canResearch ? datacenterApi.status() : Promise.resolve(null)"),
        ("autoStatus()", "canResearch ? datacenterApi.autoStatus() : Promise.resolve(null)"),
    ):
        assert call in dc, f"{endpoint} 对 viewer 仍会发起请求"
    assert "{canResearch && <TrainPanel />}" in dc, "train/status 在 viewer 挂载即轮询（恒红框）"
    assert "canFetch={canResearch}" in dc, "sync/fetch 入口未门控"
    assert "canResearch={canResearch}" in dc, "text/mirror 写操作未门控"
    assert "需要 researcher 及以上角色才能查看同步日志。" in dc
    assert "需要 researcher 及以上角色才能执行数据同步。" in dc

    st = _read("pages", "Settings", "index.tsx")
    assert "const canResearch = hasMinimumRole(authUser?.role, 'researcher');" in st
    assert "if (withTest && canResearch) void testAll();" in st, "挂载即 2 次越权连接测试"
    assert "useEffect(() => { void load(canResearch); }, [load, canResearch]);" in st
    assert "需要 researcher 及以上角色才能手动同步。" in st, "/data/sync 入口未门控"
    assert st.count("{canResearch && (") >= 3, "连接测试/同步入口未全部门控"
    assert st.count("{isAdmin && (") >= 2, "/data/cache/clear 与 /db/backup 未按 admin 门控"

    pf = _read("pages", "Portfolio", "index.tsx")
    assert "const canRun = hasMinimumRole(role, 'researcher');" in pf
    assert "disabled={!canRun || loading || !weightOk || !dateOk}" in pf, (
        "POST /portfolio/backtest 对 viewer 仍可点"
    )
    rp = _read("pages", "Report", "index.tsx")
    assert "const canRegenerate = hasMinimumRole(role, 'researcher');" in rp
    assert "disabled={!canRegenerate || regenerating}" in rp, (
        "POST /report/daily/generate 对 viewer 仍可点"
    )


def test_c10_settings_fake_cache_retention_slider_removed():
    """C-10：「自动清理缓存（N 天前数据）」滑条完全未接线，后端 clear 也无保留期参数。"""
    src = _read("pages", "Settings", "index.tsx")
    assert "cacheDays" not in src, "未接线的保留期状态仍在"
    assert "自动清理缓存（{cacheDays}" not in src, "伪功能滑条仍在（用户会以为自动清理已生效）"
    assert 'type="range" min={7} max={90}' not in src, "保留期滑条控件仍在"
    assert "后端清缓存为全量操作，无按保留期自动清理的能力。" in src, (
        "删除伪功能后未说明真实能力边界"
    )


def test_c11_datasync_toggle_has_no_hardcoded_default():
    """C-11：autoSync 初值硬编码 true + 读写失败静默 ⇒ 长期显示假的「已勾选」。"""
    src = _read("pages", "DataCenter", "index.tsx")
    assert "const [autoSync, setAutoSync] = useState<boolean | null>(null);" in src, (
        "初值仍硬编码（读取失败会假装已勾选）"
    )
    assert "checked={autoSync === true} disabled={autoSync === null}" in src, "未知状态仍渲染为已勾选"
    assert "setAutoSync(null);" in src, "读取失败未回落到「未知」"
    assert "setAutoSync(previous);" in src, "写失败未回退，失败被静默吞掉"
    assert "自动更新状态读取失败，未做任何假设" in src, "失败未对用户可见"


def test_c20_order_desk_kill_switch_unknown_is_not_rendered_as_safe():
    """C-20：熔断状态读取失败（kill=null）曾被渲染成绿色「正常运行」。"""
    src = _read("pages", "OrderDesk", "index.tsx")
    assert "kill == null ? '状态不可读' : kill.kill_switch ? '熔断激活' : '正常运行'" in src, (
        "不可读仍被当成安全状态"
    )
    assert "? 'bg-slate-100 text-ink-secondary'" in src, "未知状态仍在用安全色"
    assert "熔断状态读取失败，页面不作\"运行正常\"假设；请刷新后确认。" in src
    assert "disabled={kill == null}" in src, "状态不可读时仍允许盲目解除熔断"


def test_c21_settings_refresh_interval_labels_show_seconds():
    """C-21：刷新频率下拉 3 个选项标签全是「管理配置」，值与文案错位。"""
    src = _read("pages", "Settings", "index.tsx")
    assert "{[3, 5, 10].map((s) => <option key={s} value={s}>每 {s} 秒</option>)}" in src, (
        "下拉标签仍与取值错位（用户无法辨别 3/5/10 秒）"
    )


def test_c22_destructive_actions_require_confirmation():
    """C-22：全量重构/修复缺漏一键即发、删除预警规则无确认 ⇒ 与清缓存/熔断口径不一致。"""
    dc = _read("pages", "DataCenter", "index.tsx")
    assert "const [confirmSync, setConfirmSync] = useState<null | { mode: 'rebuild' | 'repair'; symbols?: string[] }>(null);" in dc
    assert "onClick={() => setConfirmSync({ mode: 'rebuild' })}" in dc, "全量重构仍一键即发"
    assert "onClick={() => setConfirmSync({ mode: 'repair' })}" in dc, "修复缺漏仍一键即发"
    assert "onClick={() => setConfirmSync({ mode: 'repair', symbols: [...selectedGaps] })}" in dc
    assert "if (pending) void startSync(pending.mode, pending.symbols, resume);" in dc, (
        "确认后才真正发起同步"
    )
    assert "确认全量数据重构？" in dc

    al = _read("pages", "Alerts", "index.tsx")
    assert "const [confirmDelete, setConfirmDelete] = useState<AlertRule | null>(null);" in al
    assert "onClick={() => setConfirmDelete(r)}" in al, "删除规则仍无确认"
    assert "确认删除该预警规则？" in al
    assert "if (target) void removeRule(target.id);" in al


# ==================== 同批：P1-22 / P1-24 ====================

def test_p1_22_use_chart_depends_on_node_and_option():
    """P1-22：`useChart` 的 setOption effect 依赖缺 `node` ⇒ 条件挂载图表的首个 option 丢失。"""
    src = _read("utils", "useChart.ts")
    assert "[node, option]);" in src, "setOption effect 未同时依赖 node 与 option"
    assert "inst.current.setOption(option, true);" in src


def test_p1_24_auth_store_has_no_admin_token_migration():
    """P1-24：模块加载即把 localStorage.AQP_ADMIN_TOKEN 迁移成 role=admin 会话。"""
    src = _read("stores", "useAuthStore.ts")
    for stale in ("AQP_ADMIN_TOKEN", "LEGACY_TOKEN_KEY", "readLegacyToken"):
        assert stale not in src, f"越权会话迁移代码回归：{stale}"
    assert "会话只能由" in src, "未说明会话来源（防止再次被静默重加）"


# ============ KPI 迷你图（Sparkline）：绘制下限 + 渐变 id 唯一（本轮 KPI 真实序列改造）============

def test_sparkline_guards_min_draw_points_floor_independently():
    """前端**独立**守住可绘制点数下限（≥6 个真实观测点才算有趋势）。

    缺陷：原先前端完全信任后端 `enough`，判据硬编码 ``pts.length < 2``
    ⇒ 后端一旦在 2~5 个点时误给 `enough=true`，前端就会画出"2 点折线"这种
    看似趋势、实则不含趋势信息的假图形（`/screener` 与 `/etf` 的 KPI 卡）。
    修复：引入与后端 ``app/data/kpi_series.py`` 的 ``MIN_POINTS = 6`` 同源的
    ``MIN_DRAW_POINTS = 6``，并让 **title 文案分支**与**提前 return 分支**
    （`frontend/src/components/charts/Sparkline.tsx` 约 :73 / :83）**两处**都用它。
    """
    src = _read("components", "charts", "Sparkline.tsx")
    assert "const MIN_DRAW_POINTS = 6;" in src, (
        "缺少与后端 kpi_series.MIN_POINTS 同源的 MIN_DRAW_POINTS 常量"
    )
    assert src.count("pts.length < MIN_DRAW_POINTS") == 2, (
        "title 文案分支与提前 return 分支未都改用 MIN_DRAW_POINTS（应恰为 2 处）"
    )
    assert "pts.length < 2" not in src, (
        "旧的硬编码 2 点下限仍在：2~5 点会被画成假趋势线"
    )


def test_sparkline_gradient_id_is_unique_per_instance():
    """迷你图渐变 ``<linearGradient id>`` 必须每个实例唯一。

    缺陷：原先 ``const gid = `spark-${color.replace('#','')}```，`/screener` 的
    「今日胜率」与「平均涨跌幅」两卡同用 ``#DC2626`` ⇒ 产出两个相同的
    ``id="spark-DC2626"``（非法重复 DOM id）。修复：改用 ``useId()`` 并清除
    React 生成的 ``:``（直接拼进 CSS ``url(#...)`` 会失效）。
    """
    src = _read("components", "charts", "Sparkline.tsx")
    assert "useId" in src, "未引入 useId"
    assert "const uid = useId().replace(/:/g, '');" in src, (
        "useId 结果未清除 ':'（会破坏 url(#...) 引用）"
    )
    assert "const gid = `spark-${uid}`;" in src, "渐变 id 未改用唯一 uid"
    assert "spark-${color" not in src, (
        "旧的按颜色拼 id 仍在：同色卡片会产出重复 DOM id"
    )


# ===== 同类残留修复 A/B/C/D：ETF 资金流表 null 兜底 + MarketOverview 迷你图/方向 =====

def test_etf_flow_net_inflow_null_is_neutral_not_red_plus():
    """A：ETF 资金流向表「净流入(元)」列不得用 ``(it.net_inflow ?? 0)`` 兜底。

    ``EtfFlowItem.net_inflow`` 是 ``number | null``（``types/etf.ts:178``），而
    ``null ?? 0 === 0`` 且 ``0 >= 0`` 为真 ⇒ 源不可达（net_inflow=null）时该格被
    判成 ``t-up``(红) 并加上 ``+``，渲染出红色的 ``+—``：把「未知」呈现成了
    「正值方向」。修复后 tone 与 ``+`` 前缀均以 ``it.net_inflow == null`` 为前置。
    """
    src = _read("pages", "Etf", "index.tsx")
    assert (
        "it.net_inflow == null ? '' : it.net_inflow >= 0 ? 't-up' : 't-down'" in src
    ), "净流入列 tone 未对 null 前置判空（仍可能把 null 染成红）"
    assert "it.net_inflow != null && it.net_inflow >= 0 ? '+' : ''" in src, (
        "'+' 前缀未与 `it.net_inflow != null` 绑定（null 会渲染出 '+—'）"
    )
    assert "(it.net_inflow ?? 0) >= 0" not in src, (
        "净流入的显示路径仍在用 `(it.net_inflow ?? 0)` 兜底"
    )


def test_etf_flow_inflow_ratio_tone_null_is_neutral():
    """C：同表「净流入率」列的 **tone** 不得再用 ``(it.inflow_ratio ?? 0)`` 兜底。

    值那一支早已正确判空（显示 ``—``），只有 tone 这一支还在兜 0 ⇒ null 时该格
    仍被染成 ``t-up``(红)，用颜色替「未知」表方向。修复后 null 一律中性，
    与紧邻的「净流入(元)」列口径一致。
    """
    src = _read("pages", "Etf", "index.tsx")
    assert (
        "it.inflow_ratio == null ? '' : it.inflow_ratio >= 0 ? 't-up' : 't-down'" in src
    ), "净流入率列 tone 未对 null 前置判空（仍可能把 null 染成红）"
    assert "(it.inflow_ratio ?? 0) >= 0" not in src, (
        "净流入率列 tone 仍在用 `(it.inflow_ratio ?? 0)` 兜底"
    )


def test_marketoverview_minispark_gradient_id_unique():
    """B①：`MiniSpark` 渐变 id 不得再按 ``up`` / ``pts.length`` / ``min`` 派生。

    原先 ``const gid = `sg-${up ? 'u' : 'd'}-${pts.length}-${Math.round(min)}```
    ⇒ 同页两实例若三者相同即撞 id（与 Sparkline 修复同源）。改用 ``useId()``。
    """
    src = _read("pages", "MarketOverview", "pieces.tsx")
    assert "import { useId } from 'react';" in src, "未引入 useId"
    assert "const uid = useId().replace(/:/g, '');" in src, (
        "useId 结果未清除 ':'（会破坏 url(#...) 引用）"
    )
    assert "const gid = `sg-${uid}`;" in src, "MiniSpark 渐变 id 未改用唯一 uid"
    assert "sg-${up" not in src, (
        "旧的按 up/点数/min 拼 gid 仍在：两实例可能产出重复 DOM id"
    )


def test_marketoverview_minispark_useid_precedes_early_return():
    """B②：`useId()` **必须**位于 ``pts.length < 3`` 的提前 return 之前。

    否则构成条件调用 hook：``pts.length`` 跨越 3 时 hook 数量翻转，React 直接崩
    （比 id 唯一性更严重）。注意：本文件注释里也出现过 ``useId()`` 字样，故判据
    必须锚定**代码** ``const uid = useId()`` 而非 ``useId()`` 子串，否则是「假绿」。
    """
    src = _read("pages", "MarketOverview", "pieces.tsx")
    uid_code = "const uid = useId().replace(/:/g, '');"
    early_return = "if (pts.length < 3) return"
    assert uid_code in src and early_return in src, "锚点缺失"
    assert src.index(uid_code) < src.index(early_return), (
        "useId() 被放到提前 return 之后：构成条件调用 hook，pts 跨越 3 时 React 崩溃"
    )


def test_marketoverview_kpi_direction_is_nullable_not_ghost_direction():
    """D：MarketOverview ``Kpi`` 的 ``up`` 必须可空，调用点不得对可空字段用 ``?? 0``。

    原先 ``up={(flowVal ?? 0) >= 0}`` / ``up={(ai?.rank_ic ?? 0) >= 0}`` —— ``up``
    是必填 ``boolean``，取数失败(null)被兜成「非负」⇒ 用颜色方向替「未知」表态。
    修复后 ``up?: boolean``（undefined=中性）、调用点传 ``undefined``、中性走
    ``text-ink-muted``、透传 MiniSpark 用 ``up ?? false``。
    """
    src = _read("pages", "MarketOverview", "KpiCards.tsx")
    assert "up?: boolean;" in src, "Kpi.up 仍为必填 boolean（无法表达未知/中性）"
    assert "up={flowVal == null ? undefined : flowVal >= 0}" in src, (
        "资金流向卡仍用 `(flowVal ?? 0) >= 0` 决定方向"
    )
    assert "up={ai?.rank_ic == null ? undefined : ai.rank_ic >= 0}" in src, (
        "AI RankIC 卡仍用 `(ai?.rank_ic ?? 0) >= 0` 决定方向"
    )
    assert "up == null ? 'text-ink-muted' : up ? 't-up' : 't-down'" in src, (
        "未知方向未走中性色（仍在替取数失败表态）"
    )
    assert "up={up ?? false}" in src, "MiniSpark 透传未把 undefined 收敛为 false"
    assert "flowVal ?? 0" not in src, "资金流向仍在用 `flowVal ?? 0` 兜底"
    assert "rank_ic ?? 0" not in src, "AI RankIC 仍在用 `rank_ic ?? 0` 兜底"


# ===== §⑤ 前端 null 伪方向家族清扫：可空字段的**显示路径**不得用 `?? 0` 兜方向 =====
# 判据：`x ?? 0` 同时满足 (1) 参与比较/决定颜色方向 (2) 同一 UI 位已存在诚实的
# 「未知」表达（`—` / 空值 / 中性色）却被绕过落进非中性红/绿 —— 才算缺陷。
# 修法一律：null 走中性（text 用 text-ink-muted，bg 用 bg-brand-500，SVG 描边用
# 项目 flat 色 #94A3B8），**非 null 行为逐字节不变**。

def test_watchlist_kpi_tones_neutral_on_null_not_red():
    """Watchlist KPI 卡 tone 不得对可空字段用 ``?? 0`` 兜出红色方向。

    ``WatchSummary.avg_pct`` / ``flow_total_yi`` 均为 ``number | null``
    （``types/watchlist.ts:7,9``），同一行的 ``value`` 已诚实判空显示 ``—``；但
    tone 仍用 ``(summary?.X ?? 0) >= 0`` ⇒ null 被当非负染红，与「—」自相矛盾。
    """
    src = _read("pages", "Watchlist", "index.tsx")
    assert (
        "tone: summary?.avg_pct == null ? 'text-ink-muted' : summary.avg_pct >= 0 ? 'text-red-500' : 'text-emerald-600'"
        in src
    ), "平均涨跌幅 tone 未对 null 前置判空（仍可能把 null 染红）"
    assert (
        "tone: summary?.flow_total_yi == null ? 'text-ink-muted' : summary.flow_total_yi >= 0 ? 'text-red-500' : 'text-emerald-600'"
        in src
    ), "资金净流入 tone 未对 null 前置判空（仍可能把 null 染红）"
    assert "(summary?.avg_pct ?? 0) >= 0" not in src, "平均涨跌幅仍在用 `?? 0` 兜方向"
    assert "(summary?.flow_total_yi ?? 0) >= 0" not in src, "资金净流入仍在用 `?? 0` 兜方向"


def test_watchlist_alert_count_missing_is_em_not_fake_zero():
    """「预警异动信号」卡不得用 ``summary?.alert_count ?? 0`` 把「无数据」写成「0 只」。

    ``summary`` 为 null（首载 / 取数失败）时原先渲染 ``0 只 （暂无）`` —— 把"没有
    数据"伪造成"零条告警"。修复后缺数显 ``—`` 且 tone 走中性。
    """
    src = _read("pages", "Watchlist", "index.tsx")
    assert (
        "value: summary?.alert_count != null ? `${summary.alert_count} 只 ${alertDesc}` : '—',"
        in src
    ), "预警卡 value 未对缺数显 '—'（仍可能渲染出 '0 只'）"
    assert "tone: summary?.alert_count == null ? 'text-ink-muted' : 'text-red-500'," in src, (
        "预警卡 tone 未对 null 走中性"
    )
    assert "`${summary?.alert_count ?? 0} 只 ${alertDesc}`" not in src, (
        "预警卡仍在用 `alert_count ?? 0` 伪造零条告警"
    )


def test_watchlist_mini_sparkline_unknown_pct_is_neutral():
    """自选表迷你 K 线：``it.pct`` 可空（``types/watchlist.ts:22``），不得用
    ``(it.pct ?? 0) >= 0`` 把未知染红。修复后本地 ``Sparkline.up`` 可空、null 走中性灰。"""
    src = _read("pages", "Watchlist", "index.tsx")
    assert (
        "function Sparkline({ closes, up }: { closes: Array<number | null>; up?: boolean })" in src
    ), "本地 Sparkline 的 up 仍为必填 boolean（无法表达未知）"
    assert "up == null ? '#94A3B8' : up ? '#EF4444' : '#22C55E'" in src, (
        "未知 pct 未走中性灰（仍会被染成红/绿）"
    )
    assert "up={it.pct == null ? undefined : it.pct >= 0}" in src, (
        "调用点仍用 `(it.pct ?? 0) >= 0` 决定迷你 K 线颜色"
    )
    assert "up={(it.pct ?? 0) >= 0}" not in src, "调用点仍在用 `?? 0` 兜方向"


def test_backtest_kpi_cards_up_nullable_and_consumer_neutral():
    """回测 KPI 卡：``StrategyKpi`` 三个比率为 ``number | null``
    （``api/strategyBacktest.ts:54-56``），``value`` 已判空显示 ``—``；不得用
    ``(x ?? 0) >= 0`` 兜方向。``up`` 可空后消费点须走中性；方向信息**只由 color
    单一来源承载** —— ``SparkArea`` 的 ``up`` 是解构后从不使用的死 prop，已删除
    （避免"两个方向真源不一致"隐患，见 QC 批 3）。"""
    src = _read("pages", "Backtest", "resultParts.tsx")
    for field in ("annual_strategy", "annual_benchmark", "sharpe"):
        assert f"up: kpi.{field} == null ? undefined : kpi.{field} >= 0," in src, (
            f"{field} 仍用 `(kpi.{field} ?? 0) >= 0` 兜方向"
        )
        assert f"(kpi.{field} ?? 0) >= 0" not in src, f"{field} 仍在用 `?? 0` 兜方向"
    assert "c.up === undefined ? 'text-ink-muted' : c.up ? 't-up' : 't-down'" in src, (
        "KPI 值仍把 undefined 落进 t-down（绿）：未知被染成跌"
    )
    assert "function SparkArea({ data, color }: { data: number[]; color: string })" in src, (
        "SparkArea 仍声明/解构死 prop up（方向应由 color 单一来源承载）"
    )
    assert "up: boolean;" not in src, "SparkArea props 类型里仍残留 `up: boolean;` 死 prop"
    assert "up={c.up ?? false}" not in src, "仍在向 SparkArea 透传已删除的 up 死 prop"
    assert "c.up == null ? '#94A3B8'" in src, "迷你面积图未知方向未走中性灰"


def test_backtest_overfit_ratio_null_not_colored_as_normal():
    """Walk-Forward 过拟合比：``overfit_ratio?: number | null``
    （``api/strategyBacktest.ts:100``），值已显示 ``—``；不得用 ``(x ?? 0) > 1.5``
    兜底 ⇒ null 落进 ``text-up``（红）。"""
    src = _read("pages", "Backtest", "index.tsx")
    assert "result.optimization.overfit_ratio == null ? 'text-ink-muted'" in src, (
        "过拟合比未对 null 走中性（仍把无数据说成正常偏红）"
    )
    assert "(result.optimization.overfit_ratio ?? 0) > 1.5" not in src, (
        "过拟合比仍在用 `?? 0` 兜方向"
    )


def test_research_stress_portfolio_return_tone_neutral_on_null():
    """压力测试表：``portfolio_return: number | null``（``api/research.ts:130``），
    值已显示 ``—``；不得用 ``(x ?? 0) >= 0`` 兜成红。"""
    src = _read("pages", "Research", "parts.tsx")
    assert (
        "s.portfolio_return == null ? 'text-ink-muted' : s.portfolio_return >= 0 ? 'text-red-600' : 'text-emerald-600'"
        in src
    ), "组合收益列 tone 未对 null 走中性"
    assert "(s.portfolio_return ?? 0) >= 0" not in src, "组合收益列仍在用 `?? 0` 兜方向"


def test_stockdetail_vol_percentile_null_not_colored_low():
    """个股风险面板：``vol_percentile: number | null``（``types/stock.ts:283``），
    值已显示 ``—``；不得用 ``(x ?? 0)`` 兜底 ⇒ null 被判 ``0 <= 20`` 染绿
    （把"缺失"说成"低波动"）。"""
    src = _read("pages", "StockDetail", "index.tsx")
    assert "r.vol_percentile == null ? 'bg-brand-500'" in src, (
        "波动率分位 tone 未对 null 走中性（仍会被染成绿）"
    )
    assert "(r.vol_percentile ?? 0) >= 80" not in src, "波动率分位仍在用 `?? 0` 兜方向"


def test_stockdetail_fund_tone_null_not_red():
    """个股基本面进度条：缺失指标 ``v`` 为 null（值文本 ``—``、填充 0），
    ``fundTone`` 不得用 ``(v ?? 0) >= 0`` 兜成 ``bg-red-400``。"""
    src = _read("pages", "StockDetail", "index.tsx")
    assert "if (v == null) return 'bg-brand-500';" in src, "fundTone 未对 null 走中性色"
    assert "(v ?? 0) >= 0 ? 'bg-red-400' : 'bg-green-400'" not in src, (
        "fundTone 仍在用 `(v ?? 0)` 兜方向"
    )


# ===== 形状 2（`x != null && x > 0 ? A : B`，null 落 else 绿）—— QC 批 3 漏扫清扫 =====
# 这 5 处**不出现 `?? 0`**，故上一轮的 grep 模式匹配漏掉：null 是被三元 else 分支接走。
# 修法同 §⑤：null 走中性（text 用 text-ink-muted；tone 组件的既有先例支持 undefined 则传
# undefined），非 null 分支逐字节不变。

def test_research_icir_mean_ic_null_not_green():
    """L1 `pages/Research/parts.tsx`：因子 IC 表 ``mean_ic`` 列。

    ``FactorIcirRow.mean_ic: number | null``（``api/research.ts:17``），值已显 ``—``；
    tone 写作 ``mean_ic != null && mean_ic > 0 ? 红 : 绿`` ⇒ null 落 **绿**（把"无数据"
    说成"负 IC"）。
    """
    src = _read("pages", "Research", "parts.tsx")
    assert "r.mean_ic == null ? 'text-ink-muted'" in src, (
        "mean_ic 缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "r.mean_ic != null && r.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `!= null && > 0 ? 红 : 绿` 让 null 落进绿"
    )


def test_screener_win_rate_null_not_green():
    """L2 `pages/Screener/StatsCards.tsx`：「今日胜率」卡。

    ``win_rate: number | null``（``types/p1.ts:47``），value 已 ``—``；tone 写作
    ``win_rate != null && win_rate >= 50 ? t-up : t-down`` ⇒ null 落 **t-down（绿）**。
    修复口径与同屏「平均涨跌幅」卡（同一 ``KpiCard``）一致：null ⇒ ``undefined``（中性）。
    """
    src = _read("pages", "Screener", "StatsCards.tsx")
    assert "tone={t.win_rate == null ? undefined : t.win_rate >= 50 ? 't-up' : 't-down'}" in src, (
        "win_rate 缺失时 tone 未走中性（仍会落进 t-down 绿）"
    )
    assert "t.win_rate != null && t.win_rate >= 50 ? 't-up' : 't-down'" not in src, (
        "仍在用 `!= null && >= 50 ? t-up : t-down` 让 null 落进绿"
    )


def test_factorstudio_lab_metrics_mean_ic_null_not_green():
    """L3 `pages/FactorStudio/FactorLab.tsx`：因子库 Mean IC 列。

    ``metrics: {...} | null``（``api/production.ts:96``），value 已 ``—``；tone 写作
    ``f.metrics && f.metrics.mean_ic > 0 ? 红 : 绿`` ⇒ ``metrics == null`` 落 **绿**。
    """
    src = _read("pages", "FactorStudio", "FactorLab.tsx")
    assert "f.metrics == null ? 'text-ink-muted'" in src, (
        "metrics 缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "f.metrics && f.metrics.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `metrics && mean_ic > 0 ? 红 : 绿` 让 null 落进绿"
    )


def test_factorstudio_lab_quintile_annual_null_not_green():
    """L4 `pages/FactorStudio/FactorLab.tsx`：5 分组年化。

    ``quintile_annual: Record<string, number | null>``（``api/production.ts:83``），
    值 null 时已显 ``—``；tone 写作 ``v != null && v > 0 ? 红 : 绿`` ⇒ null 落 **绿**。
    """
    src = _read("pages", "FactorStudio", "FactorLab.tsx")
    assert "v == null ? 'text-ink-muted'" in src, (
        "分组年化缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "v != null && v > 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `v != null && v > 0 ? 红 : 绿` 让 null 落进绿"
    )


def test_orderdesk_total_return_null_not_green():
    """L5 `pages/OrderDesk/index.tsx`：账户「累计收益率」。

    该值为派生态（``:181`` ``account && initial_cash > 0 ? ... : null``），可为 null，
    value 已 ``—``；tone 写作 ``totalReturn != null && totalReturn >= 0 ? 红 : 绿``
    ⇒ null 落 **绿**。
    """
    src = _read("pages", "OrderDesk", "index.tsx")
    assert "totalReturn == null ? 'text-ink-muted'" in src, (
        "累计收益率缺失时 tone 未走中性（仍会落进 text-emerald-600 绿）"
    )
    assert "totalReturn != null && totalReturn >= 0 ? 'text-red-600' : 'text-emerald-600'" not in src, (
        "仍在用 `!= null && >= 0 ? 红 : 绿` 让 null 落进绿"
    )

# ===== 第 4 批：结构性穷举抓出的 5 处 + 最大回撤配对（形状⑦⑧⑨） =====
# 与 `test_frontend_null_direction_leaks_qa3.py` 互为双重锁（两个独立文件各自固定同一判据）。


def test_ai_sentiment_gauge_unknown_score_not_pinned_to_zero():
    """情绪仪表 `AiPicksPanel.tsx`：`score` 是 **0~100 有界刻度**，`0` 是合法极值（极度恐慌）。

    ⛔ 不得用 `?? 0` / `|| 0` 兜底（把"未知"钉在刻度最左端 = 谎报成极度恐慌）；
    ⛔ 也不得用 `!score` 判空（把真实的 0 分当成缺失 = 用"未知"吞掉真实极值）。
    只接受**严格空值判断**；未知时指针/锚点不画、数值显示 `—`。
    """
    src = _read("pages", "MarketOverview", "AiPicksPanel.tsx")
    for bad in ("score ?? 0", "score || 0", "score ?? 0.0", "score || 0.0"):
        assert bad not in src, f"情绪仪表仍用 `{bad}` 兜底 ⇒ 未知被钉在最左端（极度恐慌）"
    assert "sentiment?.score ?? null" in src, "score 未保留 null（无法区分未知与真实 0 分）"
    assert any(g in src for g in (
        "score == null", "score != null", "score === null", "score !== null",
        "score === undefined", "score !== undefined", "score ?? null", "score ?? undefined",
        "Number.isFinite(score)", "Number.isNaN(score)",
    )), "未对 score 做严格空值判断（禁止 `!score` —— 0 是合法极值）"


def test_datacenter_failed_count_unknown_is_not_emerald():
    """数据中心同步卡：`failed_count?: number` 可为 null。

    `?? 0` 会让未知落进"无失败"分支 ⇒ 满条 `bg-emerald-400`（绿=零失败/全部成功），
    把"失败数未知"说成"一次都没失败"。`0` 是合法真实值 ⇒ 同样禁止 `!failed_count` 判空。
    """
    src = _read("pages", "DataCenter", "index.tsx")
    assert "(sync?.failed_count ?? 0) > 0" not in src, "仍在用 `failed_count ?? 0` 兜底 ⇒ 未知显示成零失败"
    assert "failed_count || 0" not in src, "仍在用 `|| 0` 兜底 ⇒ 未知同样显示成零失败"
    assert "sync?.failed_count == null ? 'bg-slate-300'" in src, (
        "未知失败数时进度条未走中性（仍会被染成代表成功的绿）"
    )
    assert "' · 失败数未知'" in src, "未知失败数时未在文案上显式声明未知"


def test_stockdetail_chip_curve_unknown_current_price_not_all_green():
    """筹码密度曲线：`ChipBlock.current_price: number | null`。

    `const cur = c.current_price ?? 0;` ⇒ cur=0 时真实价格恒 > 0 ⇒ 曲线每一格都落进
    `p.price <= cur` 的假分支 ⇒ **整条全绿 = 谎报成"100% 套牢"**。
    """
    src = _read("pages", "StockDetail", "index.tsx")
    assert "c.current_price ?? 0" not in src, "筹码曲线仍用 `?? 0` 兜现价 ⇒ 整条全绿（全部套牢）"
    assert "current_price || 0" not in src, "筹码曲线仍用 `|| 0` 兜现价 ⇒ 同样整条全绿"
    assert "c.current_price == null || c.current_price <= 0 ? 'bg-slate-300'" in src, (
        "现价未知/非法时曲线未走中性（不得用 `!cur` 真假判断）"
    )


def test_breadth_panel_up_down_unknown_is_dash_not_zero_percent():
    """市场涨跌分布：`HeatBlock.up?/down?` 全可选。

    `upPct = Math.round(((heat?.up ?? 0) / total) * 100)` ⇒ 家数未知时算成 0 并渲染
    「红盘 0%」= 把"未知"说成 0%。须沿用同组件 :124 的 `n != null && n > 0 ? ... : '—'` 先例。
    """
    src = _read("pages", "MarketOverview", "BreadthPanel.tsx")
    assert "((heat?.up ?? 0) / total)" not in src, "红盘占比仍用 `?? 0` ⇒ 未知显示「红盘 0%」"
    assert "((heat?.down ?? 0) / total)" not in src, "绿盘占比仍用 `?? 0` ⇒ 未知显示「绿盘 0%」"
    assert "heat?.up || 0" not in src and "heat?.down || 0" not in src, "仍用 `|| 0` 兜底 ⇒ 同样显示 0%"
    assert "upPct == null ? '—'" in src and "downPct == null ? '—'" in src, (
        "未知家数时占比未显示 '—'（仍在显示 0%）"
    )


def test_max_drawdown_null_tone_is_neutral_in_both_places():
    """最大回撤两处（成对）：回撤**恒为负**，故有值时染绿是定义性配色、不算缺陷；
    但值为 null 时显示 `—` 却仍染绿 ⇒ 替"未知"表态。两处必须一起中性化，
    否则就会出现"一处中性、一处染绿"的同族不自洽。

    - `pages/StockDetail/index.tsx` 风险面板的「最大回撤」StatRow
    - `pages/Backtest/resultParts.tsx` KPI 卡的「最大回撤」卡
    """
    sd = _read("pages", "StockDetail", "index.tsx")
    assert 'tone="t-down"' not in sd, "风险面板最大回撤仍是常量 tone ⇒ null 时 '—' 却染绿"
    assert "tone={r.max_drawdown == null ? undefined : 't-down'}" in sd, (
        "风险面板最大回撤未对 null 走中性"
    )
    rp = _read("pages", "Backtest", "resultParts.tsx")
    assert "up: false, data: spark, hl: false" not in rp, (
        "回测最大回撤卡仍是常量 up:false ⇒ null 时 '—' 却染绿"
    )
    assert "up: kpi.max_drawdown == null ? undefined : false" in rp, (
        "回测最大回撤卡未对 null 走中性"
    )
