/**
 * AQP 业务类型定义（与后端 API 契约一一对应）。
 * 所有可空指标字段（如 K 线窗口不足时的均线值）用 null 表示，
 * JSON 层面即 null，禁止用 undefined 混淆。
 */
import type { APIResponse } from './api';

// ---------------- 个股 ----------------

/** 个股模糊搜索命中项（GET /api/v1/stock/search） */
export interface SearchHit {
  symbol: string;
  code: string;
  name: string;
  market: string;
  is_st: boolean;
}

/** 最新行情卡片 */
export interface LatestQuote {
  date: string;
  close: number;
  pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
}

/** 个股档案（GET /api/v1/stock/{symbol}/profile） */
export interface StockProfile {
  symbol: string;
  code: string;
  name: string;
  market: string;
  type: string;
  list_date: string | null;
  is_st: boolean;
  industry: string | null;
  area: string | null;
  latest: LatestQuote | null;
}

export type AdjustMode = 'none' | 'qfq' | 'hfq';
export type KLinePeriod = 'day' | 'week' | 'month';

/** 单根 K 线（含后端叠加的技术指标，窗口不足处为 null） */
export interface KLineBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number | null;
  // ---- 技术指标（enrich_kline 叠加） ----
  ma_5?: number | null;
  ma_10?: number | null;
  ma_20?: number | null;
  ma_60?: number | null;
  ma_120?: number | null;
  ma_250?: number | null;
  macd_dif?: number | null;
  macd_dea?: number | null;
  macd_bar?: number | null;
  rsi_14?: number | null;
  boll_mid?: number | null;
  boll_up?: number | null;
  boll_low?: number | null;
}

/** K 线响应（GET /api/v1/stock/{symbol}/kline） */
export interface KLineResult {
  symbol: string;
  adjust: AdjustMode;
  start: string; // YYYYMMDD
  end: string;   // YYYYMMDD
  count: number;
  bars: KLineBar[];
}

// ---------------- 机器学习预测 ----------------

/** 单个因子的 TreeSHAP 贡献 */
export interface FactorContribution {
  feature: string;
  /** 对预测值的贡献量（所有因子贡献之和 + base_value = pred_return） */
  contribution: number;
  direction: 'positive' | 'negative';
}

/** 个股 ML 预测（GET /api/v1/stock/{symbol}/predict） */
export interface MLPredictResult {
  symbol: string;
  /** 数据日期（YYYY-MM-DD） */
  date: string;
  /** 预测的未来 N 个交易日 */
  horizon: number;
  /** 未来 N 日预期收益率（回归头点估计） */
  pred_return: number;
  /** 模型级置信度 0~1（0.5+2×验证集RankIC 的常数映射，非个股上涨概率）；
   *  模型 metrics 缺失时为 null（如实展示不可用，不造数） */
  confidence: number | null;
  /** confidence 的口径披露（后端 confidence_basis 原样透传，供 ⓘ 提示） */
  confidence_basis?: string;
  model_version: string;
  /** TreeSHAP 基准值：sum(top5) + base_value ≈ pred_return */
  base_value: number;
  top5_factors: FactorContribution[];
  latest_close: number;
}

// ---------------- 个股详情面板（GET /api/v1/stock/{symbol}/panels） ----------------

/**
 * 面板数据块降级契约：每个块独立 try/except，
 * 失败时返回 { status: 'unavailable' } 而非让整个接口失败。
 */
export interface PanelBlock {
  status: 'ok' | 'degraded' | 'unavailable';
  reason?: string;
  from_cache?: boolean;
}

/** 实时快照：总市值 / 流通市值 / 换手率 / 内外盘 */
export interface QuoteBlock extends PanelBlock {
  price: number | null;
  pct: number | null;
  /** 换手率 % */
  turnover: number | null;
  /** 总市值（亿元） */
  total_cap_yi: number | null;
  /** 流通市值（亿元） */
  float_cap_yi: number | null;
  outer_vol: number | null;
  inner_vol: number | null;
  outer_ratio: number | null;
  inner_ratio: number | null;
  quote_time: string | null;
  source?: string;
}

/** 主力资金净流入 */
export interface MoneyFlowBlock extends PanelBlock {
  date: string | null;
  /** 主力净流入（元） */
  main_net: number | null;
  /** 主力净流入（亿元） */
  main_net_yi: number | null;
  /** 主力净流入占成交额比 % */
  main_net_ratio: number | null;
  super_large_net_yi: number | null;
  large_net_yi: number | null;
  medium_net_yi: number | null;
  small_net_yi: number | null;
  source?: string;
}

/** 北向（陆股通）持股 */
export interface NorthBlock extends PanelBlock {
  date: string | null;
  hold_shares: number | null;
  /** 持股市值（亿元） */
  hold_cap_yi: number | null;
  /** 占 A 股百分比 % */
  pct_of_float: number | null;
  source?: string;
}

/** 基本面：估值 + 盈利能力 */
export interface FundamentalsBlock extends PanelBlock {
  /** 市盈率 TTM（倍） */
  pe_ttm: number | null;
  /** 市净率（倍） */
  pb: number | null;
  /** 加权平均净资产收益率 % */
  roe: number | null;
  /** 销售毛利率 %（银行 / 保险等金融股为 null） */
  gross_margin: number | null;
  /** 销售净利率 %（归母净利润 / 营业总收入推导） */
  net_margin: number | null;
  /** 基本每股收益 */
  eps: number | null;
  /** 每股净资产 */
  bps: number | null;
  /** 营业总收入（亿元） */
  revenue_yi: number | null;
  /** 归母净利润（亿元） */
  net_profit_yi: number | null;
  /** 财报报告期 YYYY-MM-DD */
  report_date: string | null;
  note?: string;
}

/** 近期事件条目（公告 / 重大新闻） */
export interface EventItem {
  title: string;
  /** YYYY-MM-DD */
  date: string | null;
  /** 原文外链；null 时弹窗展示详情 */
  url: string | null;
  source?: string;
  event_type?: string;
  sentiment?: 'positive' | 'negative' | 'neutral';
}

export interface EventsBlock extends PanelBlock {
  items: EventItem[];
}

/** 股东户数 */
export interface HolderNumInfo {
  end_date: string | null;
  holder_num: number | null;
  prev_holder_num: number | null;
  /** 环比变化 % */
  change_ratio: number | null;
  /** 户均持股市值（元） */
  avg_market_cap: number | null;
  avg_hold_num: number | null;
}

/** 十大流通股东条目 */
export interface TopHolder {
  rank: number | null;
  name: string | null;
  hold_num: number | null;
  /** 占流通股比例 % */
  pct_of_float: number | null;
  change: string | null;
  holder_type: string | null;
}

export interface HoldersBlock extends PanelBlock {
  holder_num: HolderNumInfo | null;
  top10: TopHolder[];
  end_date: string | null;
}

/** 筹码分布曲线上的一点（pct 为相对峰值的 0~1） */
export interface ChipCurvePoint {
  price: number;
  pct: number;
}

/** 筹码分布 */
export interface ChipBlock extends PanelBlock {
  as_of: string | null;
  current_price: number | null;
  /** 平均成本 */
  avg_cost: number | null;
  /** 获利盘比例 0~1 */
  profit_ratio: number | null;
  /** 套牢盘比例 0~1 */
  trapped_ratio: number | null;
  /** 90% 筹码区间下沿（5% 分位） */
  p5: number | null;
  /** 90% 筹码区间上沿（95% 分位） */
  p95: number | null;
  /** 筹码集中度：(p95-p5)/均值，越小越集中 */
  concentration: number | null;
  lookback: number;
  curve: ChipCurvePoint[];
  note?: string;
}

/** 风险度量 */
export interface RiskBlock extends PanelBlock {
  as_of: string | null;
  window: number;
  /** 年化波动率 */
  annual_vol: number | null;
  /** 最大回撤 0~1 */
  max_drawdown: number | null;
  /** 夏普比率 */
  sharpe: number | null;
  /** Beta（对沪深300） */
  beta: number | null;
  /** 近 20 日年化波动率 */
  vol_short: number | null;
  /** 波动率分位 0~100 */
  vol_percentile: number | null;
  benchmark?: string | null;
  note?: string;
}

/** 个股详情面板聚合响应 */
export interface StockPanels {
  symbol: string;
  /** 交易日 YYYYMMDD */
  trade_date: string;
  quote: QuoteBlock;
  money_flow: MoneyFlowBlock;
  north: NorthBlock;
  fundamentals: FundamentalsBlock;
  events: EventsBlock;
  holders: HoldersBlock;
  chip: ChipBlock;
  risk: RiskBlock;
}

// ---------------- 市场概览 ----------------

/** 指数卡片（含近 60 日迷你走势） */
export interface MarketIndexItem {
  code: string;
  name: string;
  close: number;
  pct: number;
  sparkline: number[];
}

/** 涨跌分布 + 成交额 */
export interface MarketHeat {
  up: number;
  down: number;
  flat: number;
  limit_up: number;
  limit_down: number;
  total_amount_yi: number;
}

/** 涨跌幅区间分布桶（b1<=-7% … b9>=+7%，与后端 _bucket_of 一致） */
export type HeatBuckets = Record<string, number>;

/** 行业板块资金结构（主力=超大单+大单，散户=中单+小单） */
export interface SectorFlow {
  name: string;
  main_yi: number;
  retail_yi: number;
  total_yi: number;
}

/** 迷你K线（推荐榜） */
export interface MiniBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** 公告资讯（推荐榜附注） */
export interface RecNews {
  title: string | null;
  sentiment: string | null;
  pub_date: string | null;
}

/** 北向 / 主力资金 */
export interface MarketMoneyFlow {
  north_net_today: number | null;
  main_net_today: number | null;
}

/** 异动事件 */
export interface AnomalyItem {
  rule: string;
  code: string;
  name: string;
  pct: number;
}

/** 推荐榜条目（predictions 分区，附带名称 / 迷你K线 / 资讯 / 横截面分位） */
export interface RecommendItem {
  date: string;
  symbol: string;
  name?: string | null;
  pred_score: number;
  /**
   * 当日全市场预测样本中的百分位 0~100（后端按 pred_score 秩真实计算）。
   * 注意：这是【相对排名】，不是模型置信度；平台暂不提供个股级置信度。
   */
  rank_pct?: number | null;
  model_version?: string | null;
  feature_version?: string | null;
  label_horizon?: number | null;
  candidate_pool_size?: number;
  filter_policy?: string;
  /** 用于确认推荐项具备真实可展示行情；全空候选由后端过滤。 */
  close?: number | null;
  pct?: number | null;
  amount?: number | null;
  bars?: MiniBar[];
  news?: RecNews | null;
}

/**
 * 数据块通用降级结构：后端每个数据块独立 try/except，
 * 失败时返回 { status: 'unavailable', reason } 而非让整个接口失败。
 */
export interface BlockBase {
  status: 'ok' | 'degraded' | 'unavailable';
  /** 稳定机器码；展示时使用 message。 */
  reason?: string;
  /** 面向用户的友好说明，不含后端异常类名。 */
  message?: string;
  /** 数据所属交易日/快照时刻。 */
  as_of?: string | null;
  coverage?: { available: number; total: number; ratio: number | null };
  freshness?: {
    as_of: string | null;
    expected: string | null;
    lag_trading_days: number | null;
    is_stale: boolean | null;
    note: string;
  };
}

export interface IndicesBlock extends BlockBase {
  items?: MarketIndexItem[];
  failed?: string[];
}

export interface HeatBlock extends BlockBase {
  up?: number;
  down?: number;
  flat?: number;
  limit_up?: number;
  limit_down?: number;
  total_amount_yi?: number;
  /** 涨跌幅区间分布（网格块图） */
  buckets?: HeatBuckets;
  /** 数据来源：em=东财实时快照 / local=本地日线降级 */
  source?: 'em' | 'local';
  note?: string;
}

export interface MoneyFlowBlock extends BlockBase {
  north_net_today?: number | null;
  main_net_today?: number | null;
  /** 行业板块主力/散户净流入（双向柱状图） */
  sector_flows?: SectorFlow[];
}

/** 行业板块涨幅条目 */
export interface SectorItem {
  name: string;
  pct: number;
  /** 领涨股名称（em 榜单自带 / 本地聚合回填） */
  leader?: string | null;
  leader_pct?: number | null;
  /** 本地降级聚合时的样本数 */
  count?: number;
}

export interface SectorsBlock extends BlockBase {
  items?: SectorItem[];
  note?: string;
}

export interface AnomaliesBlock extends BlockBase {
  items?: AnomalyItem[];
  note?: string;
}

export interface RecommendBlock extends BlockBase {
  date?: string;
  /** 当日参与排名的预测样本总数（rank_pct 的分母） */
  sample_size?: number | null;
  items?: RecommendItem[];
}

/** AI 预测方向命中率（真实次日回溯） */
export interface AiStatsBlock extends BlockBase {
  horizon?: number;
  label_price_basis?: string;
  rank_ic?: number;
  rank_ic_positive_ratio?: number;
  top_k?: number;
  top_k_precision?: number;
  top_k_excess_return?: number;
  directional_hit_rate?: number | null;
  samples?: number;
  n_days?: number;
}

/** AI 市场情绪指数（0-100，平台自研口径） */
export interface SentimentBlock extends BlockBase {
  score?: number;
  label?: string;
  /** platform = 平台自研口径（非第三方情绪指数） */
  kind?: 'platform';
  /** 口径说明（公式 + 权重来源） */
  basis?: string;
}

/** 市场概览聚合（GET /api/v1/market/overview） */
export interface MarketOverviewData {
  trade_date: string;
  /** true=缓存命中 / 'refreshed'=refresh=1 强制重算完成 / false=实时构建 */
  from_cache: boolean | 'refreshed';
  /** 缓存已软过期（旧值服务中，后端后台重建里） */
  stale?: boolean;
  /** refresh 防抖：5s 内重复刷新被合并，返回的是当前缓存值 */
  refreshed_recently?: boolean;
  /**
   * ⚠️ 运行时注意：MarketOverview 页把 daily 与 rt 两块**浅合并**
   * （`{...daily.data, ...rtData}`），单块先到时另一块字段为 undefined，
   * 故这些"声明为必需"的块在渲染期仍可能缺失 —— 消费方必须用
   * `data?.indices?.status` 这类**全链路可选链**，不可只写 `data?.indices.status`。
   * （2026-09-13 由此引发首页 ErrorBoundary「页面出现异常」，见 commit e2283c9）
   */
  indices: IndicesBlock;
  heat: HeatBlock;
  money_flow: MoneyFlowBlock;
  sectors?: SectorsBlock;
  anomalies: AnomaliesBlock;
  recommend: RecommendBlock;
  ai_stats?: AiStatsBlock;
  sentiment?: SentimentBlock;
  /** 可选历史交易日（YYYYMMDD，来自 predictions 分区） */
  pred_dates?: string[];
}

/** 实时块（L2-2，GET /market/overview/rt）：指数/资金/异动，分钟级 */
export interface OverviewRt {
  trade_date: string;
  /** 快照时间（轻刷新徽标的时间戳来源） */
  as_of?: string;
  indices: IndicesBlock;
  money_flow: MoneyFlowBlock;
  anomalies: AnomaliesBlock;
  /** true=缓存命中 / 'refreshed'=refresh=1 强制重算完成 / false=实时构建 */
  from_cache: boolean | 'refreshed';
  stale?: boolean;
  refreshed_recently?: boolean;
}

/** 日频块（L2-2，GET /market/overview/daily）：本地口径分布/推荐榜/情绪 */
export interface OverviewDaily {
  trade_date: string;
  heat: HeatBlock;
  sectors?: SectorsBlock;
  recommend: RecommendBlock;
  ai_stats?: AiStatsBlock;
  sentiment?: SentimentBlock;
  pred_dates?: string[];
  from_cache: boolean | 'refreshed';
  stale?: boolean;
  refreshed_recently?: boolean;
}

/** 供类型收窄使用：泛型 APIResponse（与 client.ts 解包配合） */
export type ApiResponseOf<T> = APIResponse<T>;
