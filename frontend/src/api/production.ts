/** 生产端模块 API：因子工作室 / 数据质量与血缘 / 执行中心 / 调度 / 归因。 */
import { del, get, post, type RequestOptions } from './client';

/* ==================== 因子自动化挖掘工作室 ==================== */
export interface GpStatus {
  task_id: string;
  status: 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'CANCELLED';
  generation: number;
  total_generations: number;
  fitness_curve: number[];
  /** 同期 best 的 MeanIC 曲线（让用户看清真实 IC 水平，不只看复合 fitness） */
  mean_ic_curve: number[];
  /** 同期 best 的 ICIR 曲线 */
  icir_curve: number[];
  best: { expr: string; fitness: number; mean_ic: number; icir: number;
          t_stat: number; n_days: number; complexity: number } | null;
  /** I-13：后端求值失败的条目会以 `st={}` 落进 history[:8]（有效表达式 <8 个时），
   *  故指标字段可能整体缺失，消费方必须按可选处理。 */
  top_expressions: Array<{ expr: string; fitness: number; mean_ic?: number | null;
                           icir?: number | null; t_stat?: number | null;
                           n_days?: number | null }>;
  error: string | null;
  elapsed_sec: number;
}

/** 单表达式评估结果（/studio/alpha-eval）：IC 时序 + 多空净值 + 关键指标。 */
export interface AlphaEvalResult {
  expr: string;
  horizon: number;
  n_symbols: number;
  mean_ic: number;
  icir: number;
  t_stat: number;
  n_days: number;
  ic_series: Array<{ date: string; ic: number }>;
  long_nav: Array<{ date: string; nav: number }>;
  short_nav: Array<{ date: string; nav: number }>;
  long_short_nav: Array<{ date: string; nav: number }>;
}

export const studioApi = {
  startMining: (req: { fields: string[]; horizon: number; population: number;
                       generations: number; seed: number }) =>
    post<{ task_id: string; params: Record<string, unknown>; snapshot_rows: number;
           snapshot_end: string }>('/api/v1/studio/mining/start', req, 60_000),
  miningStatus: (taskId: string) =>
    get<GpStatus>(`/api/v1/studio/mining/status/${taskId}`),
  /** 请求取消一个运行中的挖掘任务（优雅：当前代完成后退出）。 */
  cancelMining: (taskId: string) =>
    post<{ cancelled: boolean; task_id: string }>(
      `/api/v1/studio/mining/cancel/${taskId}`, {}),
  /** 评估单个 Alpha 表达式（Rank IC 时序 + 多空净值 + 关键指标）。 */
  alphaEval: (req: { expr: string; horizon: number; symbols?: string[] }, options?: RequestOptions) =>
    post<AlphaEvalResult>('/api/v1/studio/alpha-eval', req, 120_000, options),

  /* ---------------- 因子库与检测报告（§4.3，Sprint4） ---------------- */
  /** 因子检测报告：5 分组分层净值/年化 + 1/5/10/20 日衰减 + Top 组换手率 */
  factorReport: (req: { expr: string; horizon: number }, options?: RequestOptions) =>
    post<FactorReportResult>('/api/v1/studio/factor-report', req, 120_000, options),

  factors: () => get<CustomFactor[]>('/api/v1/studio/factors'),

  /** 保存自定义因子（后端 AST 白名单校验 + 真实截面评估后入库） */
  saveFactor: (req: { name: string; expression: string; horizon: number }, options?: RequestOptions) =>
    post<CustomFactor>('/api/v1/studio/factors', req, 120_000, options),

  deleteFactor: (id: number) =>
    del<{ id: number }>(`/api/v1/studio/factors/${id}`),
};

/** 因子检测报告（/studio/factor-report） */
export interface FactorReportResult {
  expr: string;
  horizon: number;
  mean_ic: number;
  icir: number;
  t_stat: number;
  n_days: number;
  ic_series: Array<{ date: string; ic: number }>;
  /** 5 分组分层累计净值（q1=最低分位 .. q5=最高分位） */
  quintile_navs: Array<{ q: string; nav: Array<{ date: string; nav: number }> }>;
  /** 各分位年化收益（持有 horizon 日口径） */
  quintile_annual: Record<string, number | null>;
  /** 衰减分析：horizon → RankIC/ICIR */
  decay: Record<string, { rank_ic: number; icir: number | null; n_days: number }>;
  /** Top 20% 名单日度变动率均值（持有成本代理） */
  top_turnover: number | null;
}

/** 自定义因子库条目（/studio/factors） */
export interface CustomFactor {
  id: number;
  name: string;
  expression: string;
  horizon: number;
  metrics: { mean_ic: number; icir: number; t_stat: number; n_days: number } | null;
  enabled: boolean;
  created_by: string | null;
  created_at: string;
}

/* ==================== 数据质量与血缘 / 调度 DAG ==================== */
export interface QualityScanResult {
  dataset: string; year: number; rows_scanned: number; symbols_scanned: number;
  /* 截断契约：symbols_available 是**截断前**可用标的数，truncated 为真时
     symbols_scanned 只覆盖前 scan_limit 个目录（见后端 ops.py 的 F4 修复）。 */
  symbols_available: number; total_symbols: number;
  truncated: boolean; scan_limit: number;
  n_issues: number; n_errors: number;
  by_kind: Record<string, number>;
  worst_symbols: Array<[string, number]>;
  samples: Array<{ dataset: string; symbol: string; date: string | null;
                   kind: string; severity: string; detail: string }>;
}

/**
 * 数据血缘图谱。
 *
 * ⚠️ 口径说明：拓扑（谁流向谁）是流水线代码的静态依赖，不随数据变化；
 * 节点状态（exists / rows / latest_date / records …）才是实时扫描结果。
 * 此前本端点 13 个节点、18 条边全为硬编码常量，已改为实扫 + 显式声明来源。
 */
export interface LineageGraph {
  nodes: Array<{
    id: string;
    name: string;
    group: string;
    desc?: string;
    /** ok=有产物 / missing=无产物 / unknown=无法读取 / external=外部源 / derived=训练期现算 */
    status?: 'ok' | 'missing' | 'unknown' | 'external' | 'derived';
    exists?: boolean;
    /** parquet 总行数（dataset 节点） */
    rows?: number | null;
    size_mb?: number | null;
    /** 最新数据日期 YYYY-MM-DD */
    latest_date?: string | null;
    files?: number | null;
    partitions?: number | null;
    /** SQLite 业务表记录数（table 节点） */
    records?: number | null;
    /** 生产模型（model 节点） */
    version?: string | null;
    valid_rank_ic?: number | null;
    test_rank_ic?: number | null;
    registered?: number | null;
    /** QC 隔离分区数（qc 节点） */
    quarantined_partitions?: number | null;
  }>;
  edges: Array<{ source: string; target: string }>;
  note: string;
  /** 'static' 表示拓扑为代码静态声明（非扫描推断） */
  topology_source?: 'static';
  node_states_scanned?: boolean;
  generated_at?: string | null;
  benchmark_date?: string | null;
  /** 尚无本地产物的节点名 */
  missing_nodes?: string[];
  degraded_note?: string | null;
}

export interface DagStatus {
  benchmark_date: string | null;
  stages: Array<{ id: string; name: string; dataset: string;
                  done_date: string | null; status: 'ok' | 'stale' }>;
  recent_jobs: Array<{ job_type: string; trade_date: string; status: string;
                       current_step: string | null;
                       /** C-8：data_jobs.duration_ms 可为 NULL（PENDING/RUNNING 未结束） */
                       duration_ms: number | null;
                       error_message: string | null; finished_at: string | null }>;
  note: string;
}

export const opsApi = {
  qualityScan: (req: { dataset?: string; year?: number }, options?: RequestOptions) =>
    post<QualityScanResult>('/api/v1/ops/quality-scan', req, 180_000, options),
  /** 后端已加跨请求 SWR 持久化缓存（TTL 300s + 日界换键），稳态命中实测约 13~41ms
   *  （QA 独立复测 6 轮 avg 22.7ms）。
   *  但**冷路径**（缓存未预热/过期后台重建）实测仍需约 33s，远超 client 默认 15s，
   *  故保留 60s 兜底，避免冷启动时前端超时并永久停在"加载血缘…"。 */
  lineage: (options?: RequestOptions) =>
    get<LineageGraph>('/api/v1/ops/lineage', undefined, 60_000, options),
  /** 调度 DAG：同样实扫数据集新鲜度，冷路径可能超 15s，与 lineage 同口径放宽到 60s。 */
  dag: (options?: RequestOptions) =>
    get<DagStatus>('/api/v1/ops/dag', undefined, 60_000, options),
  dagRerun: (req: { trade_date: string; codes?: string[] }, options?: RequestOptions) =>
    post<{ ok: boolean; summary?: string; error?: string }>(
      '/api/v1/ops/dag/rerun', req, 300_000, options),
};

/* ==================== 执行中心（模拟盘）与风控闸门 ==================== */
export interface PaperFill {
  exec_date: string; qty: number; price: number; amount: number;
  fee: number; impact_bps: number; participation: number; basis_bps: number;
}

export interface PaperOrder {
  id: number; symbol: string; side: string; algo: string;
  order_amount: number; filled_amount: number; split_days: number;
  status: 'PENDING' | 'PART_FILLED' | 'FILLED' | 'CANCELLED' | 'REJECTED';
  decision_price: number | null; reject_reason: string | null;
  created_at: string; fills: PaperFill[];
}

export interface PaperAccount {
  initial_cash: number; cash: number; market_value: number; equity: number;
  total_fees: number; total_impact_cost: number; n_fills: number;
  positions: Record<string, { qty: number; last_price: number;
                              market_value: number; cost_price: number; pnl: number }>;
  /** 派生指标（活跃交易日 < 30 时为 null，样本不足） */
  max_drawdown: number | null;
  sharpe: number | null;
  annualized_return: number | null;
  n_active_days: number;
  /** 逐日净值序列（由真实成交 + 真实收盘价重建） */
  nav_series: Array<{ date: string; cash: number; mv: number; equity: number }>;
  /**
   * 数据完整性告警（P1-3）：历史上若存在「卖出股数 > 当时持仓」的成交，
   * 该笔曾凭空造出现金 ⇒ 现金/权益偏高。账目仍是对已记录成交的纯推导，
   * 此字段用于**显式披露**而不是静默呈现虚高数字。正常为空数组。
   */
  integrity_warnings: string[];
}

export interface ExclusionItemT {
  id: number; symbol: string; category: string; reason: string;
  active: boolean; created_at: string;
}

export interface AttributionResult {
  window_days: number; n_obs: number;
  portfolio_return: number; benchmark_return: number; excess_return: number;
  brinson: {
    sectors: Array<{ industry: string; portfolio_weight: number;
                     benchmark_weight: number; portfolio_return: number;
                     benchmark_return: number; allocation: number;
                     selection: number; interaction: number; total: number }>;
    summary: { portfolio_return: number; benchmark_return: number;
               excess_return: number; allocation: number; selection: number;
               interaction: number; residual_alpha: number };
  };
  style: { n_days: number; betas: Record<string, number>;
           contributions: Record<string, number>;
           alpha_annualized: number | null; r_squared: number | null };
  benchmark_desc: string;
  benchmark_policy: { type: string; weight_basis: string; symbol_count: number; as_of: string };
}

export const deskApi = {
  killSwitch: () => get<{ kill_switch: boolean; pending_orders: number }>(
    '/api/v1/desk/kill-switch'),
  setKillSwitch: (active: boolean, reason = '') =>
    post<{ kill_switch: boolean; cancelled_orders: number }>(
      '/api/v1/desk/kill-switch', { active, reason }),
  exclusionList: () => get<ExclusionItemT[]>('/api/v1/desk/exclusion'),
  addExclusion: (symbol: string, category: string, reason = '') =>
    post<{ ok: boolean; id: number }>('/api/v1/desk/exclusion',
      { symbol, category, reason }),
  toggleExclusion: (id: number, active: boolean) =>
    post<{ ok: boolean }>('/api/v1/desk/exclusion/toggle', { id, active }),
  screenCandidates: () =>
    get<Array<{ symbol: string; name: string | null; category: string;
                reason: string }>>('/api/v1/desk/exclusion/screen'),
  placeOrder: (req: { symbol: string; side: 'buy' | 'sell'; order_amount: number;
                      algo: 'market' | 'vwap' | 'twap' | 'pov';
                      split_days: number; participation_cap: number }) =>
    post<{ ok: boolean; order_id?: number; decision_price?: number;
            reason?: string }>('/api/v1/desk/orders', req),
  // 撮合需扫描全部到期母单并生成子单，母单多时实测可超 15s；前端先断后端仍在跑
  // ⇒ 用户以为失败会重复撮合。按长任务约定放宽到 30s。
  runFills: () => post<{ orders_scanned: number; fills_created: number;
                         deferred_children: number }>(
    '/api/v1/desk/fills/run', {}, 30_000),
  /** 母单列表（信封：items + 真实 total + 窗口披露）。
   *  后端已补 `total`（独立 COUNT）/`returned`/`limit`/`truncated`，
   *  调用方据此展示真实总数与是否截断，不再把窗口条数当全量。 */
  orders: (limit = 200) =>
    get<{ items: PaperOrder[]; total: number; returned: number; limit: number;
          truncated: boolean }>(`/api/v1/desk/orders?limit=${limit}`, undefined, 30_000),
  // /desk/account 需按逐日净值序列重建（母单/成交多时可能超 15s），放宽到 30s。
  account: () => get<PaperAccount>('/api/v1/desk/account', undefined, 30_000),
  capacity: (participation_cap = 0.01, holdings = 20, rebalance_per_year = 12) =>
    get<{ aum_threshold: number | null; aum_yi: number | null; formula: string }>(
      `/api/v1/desk/capacity?participation_cap=${participation_cap}&holdings=${holdings}&rebalance_per_year=${rebalance_per_year}`),
  attribution: (
    assets: Array<{ code: string; weight: number }>,
    window_days = 120,
    benchmark: {
      benchmark_type: 'universe_equal' | 'custom_portfolio' | 'single_symbol';
      benchmark_assets?: Array<{ code: string; weight: number }>;
      benchmark_symbol?: string;
    } = { benchmark_type: 'universe_equal' },
    options?: RequestOptions,
  ) =>
    post<AttributionResult>('/api/v1/desk/attribution',
      { assets, window_days, ...benchmark }, 180_000, options),
};
