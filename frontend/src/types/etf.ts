/** ETF 中心类型定义（与后端 /api/v1/etf 契约一致）。 */
import type { KpiSeriesEnvelope } from './kpi';

export type EtfCountry = 'cn' | 'us' | 'jp' | 'kr';

/** 统一数据新鲜度（market/etf/screener 一致契约）：降级/超时时给出可观察原因 */
export interface DataFreshness {
  status: string;
  as_of?: string;
  source?: string;
  reason?: string;
  message?: string;
}

/** 行情可取状态：日韩本土标的行情源不可达时为 unavailable */
export type QuoteStatus = 'ok' | 'unavailable';

export interface EtfItem {
  code: string;
  name: string | null;
  country: EtfCountry;
  exchange: string | null;
  /** 投资类型：股票型 / 债券型 / 商品型 / 货币型 / 跨境型 */
  type: string;
  /** 榜单板块：宽基ETF / 行业ETF / 主题ETF / Smart Beta / 跨境ETF / 货币型 / 商品型 / 债券型 */
  board: string;
  tracking_index: string | null;
  manager: string | null;
  /** 成立日期 YYYY-MM-DD */
  inception: string | null;
  price: number | null;
  /** 涨跌幅 % */
  pct: number | null;
  /** 成交额（元） */
  amount: number | null;
  /** 规模（亿元） */
  size_yi: number | null;
  quote_status: QuoteStatus;
  /** 境外敞口（中国上市但跟踪境外指数，如日经 225） */
  overseas: string | null;
}

/** 海外 ETF 口径披露（P2-8：不混入境内合计） */
export interface EtfOverseasInfo {
  us_count: number;
  jp_count: number;
  kr_count: number;
  /** 美股规模（美元 × 配置汇率折算的亿元口径） */
  us_size_yi: number;
  note: string;
}

/** 市场概览（GET /etf/overview） */
export interface EtfOverviewDay {
  date: string;
  /** 本次中国 ETF 全量目录的实际数据源：eastmoney / sina+tencent / unknown（口径披露） */
  source?: string;
  /** 仅境内有真实行情的中国 ETF 数量 */
  etf_count: number;
  /** 总规模（亿元，仅中国 ETF 人民币实盘口径） */
  total_size_yi: number;
  /** 平均涨跌幅 % */
  avg_pct: number | null;
  /**
   * 资金净流入（亿元）。
   *
   * ⚠️ **可空且不受 `status` 门控**：ETF 资金流源头（东财主力净流入口径）不可达时，
   * 后端如实返回 `null`（红线：不得用 0 冒充"取数失败"）。此 null 出现在**正常路径**
   * 上，`status` 仍是缺省值 ⇒ 消费侧**必须**显式判空，不能依赖 `status === 'unavailable'`
   * 的早返回兜底。（曾因本字段被误声明为 `number;`，TS 认为 `.toFixed()` 安全，
   * 导致 `/etf` 整页白屏。）
   */
  net_inflow_yi: number | null;
  /** 资金流取数状态（后端新增，与 `net_inflow_yi` 同源）；不可用时用 `reason` 披露口径 */
  flow?: {
    status: 'ok' | 'unavailable';
    net_inflow_yi: number | null;
    reason: string | null;
  };
  /**
   * 成交额（亿元）。**非空**：由 `sum(...)` 计算，正常路径恒为数字；
   * 唯一的 null 场景在 `_fallback` 里，且同时带 `status: 'unavailable'`（已被早返回拦掉）。
   */
  amount_yi: number;
  overseas?: EtfOverseasInfo | null;
  /** 降级块状态：后端超时/异常时 status=unavailable（并给 reason），此时数字字段为 null */
  status?: 'ok' | 'degraded' | 'unavailable';
  reason?: string;
  message?: string;
}

export interface EtfOverview {
  today: EtfOverviewDay;
  /** 前一存档快照；首次运行为 null */
  prev: EtfOverviewDay | null;
  /**
   * ETF 数量「较上一期」是否可比：前后两次存档的**数据源口径**一致才为 true。
   * 数据源切换（如 东财 1337 只 → 新浪+腾讯 1679 只）或老快照缺 source（unknown）
   * 时为 false —— 此时数量差值来自口径变更而非市场变化，前端应显示「—」并加注不可比。
   */
  count_comparable?: boolean;
  /** count_comparable=false 时的可读原因（口径不同说明）；可比时为 null。 */
  comparison_note?: string | null;
  /** 数据新鲜度（降级原因），后端冷路径超时/异常时携带 */
  data_freshness?: DataFreshness;
}

/**
 * ETF 概览 KPI 卡片的历史序列（`GET /etf/overview/series`）。
 *
 * 形状与公共 `KpiSeriesEnvelope` 完全一致（含 `drawable` / `metrics[key].enough` /
 * `.comparable` / `.note` / `.dropped`），故直接复用，不再抄一份。当前各指标
 * `enough=false`（本地无落库，靠每日盘后归档累积）⇒ 前端显示「暂无历史序列」。
 */
export type EtfOverviewSeries = KpiSeriesEnvelope;

export interface EtfListResult {
  total: number;
  page: number;
  page_size: number;
  items: EtfItem[];
  options: {
    boards: string[];
    types: string[];
    indexes: string[];
    managers: string[];
  };
  /** 后端实际生效的排序键（'' = 默认顺序）；后端恒返回，前端当前不使用 */
  sort_applied: string;
  /** 后端实际生效的排序方向（asc|desc）；后端恒返回，前端当前不使用 */
  dir_applied: string;
}

/** ETF表现 单条序列 */
export interface EtfSeries {
  code: string;
  name: string;
  status: QuoteStatus;
  /** 该序列不可用原因（后端降级时给出，如"数据源响应超时"） */
  reason?: string;
  /** metric=pct 时为累计涨跌幅 %，metric=price 时为收盘价 */
  points: Array<{ date: string; value: number }>;
}

export interface EtfPerformance {
  metric: 'pct' | 'price';
  period: string;
  series: EtfSeries[];
  /** 数据新鲜度（降级原因），冷路径超时/异常时携带 */
  data_freshness?: DataFreshness;
}

export interface EtfScalePoint {
  date: string;
  /** 估算规模（亿元） */
  value: number;
  /** 当日有成交的样本数 */
  count: number;
}

export interface EtfScale {
  period: string;
  sample_size: number;
  points: EtfScalePoint[];
  note?: string;
  /** 降级块状态：后端超时/异常时 status=unavailable（并给 reason） */
  status?: 'ok' | 'degraded' | 'unavailable';
  reason?: string;
}

export interface EtfFlowItem {
  code: string;
  name: string | null;
  pct: number | null;
  /** 成交额（元） */
  amount: number | null;
  /** 净流入（元，可为负） */
  net_inflow: number | null;
  /** 净流入率 % */
  inflow_ratio: number | null;
}

/** ---------- ETF 分析详情页 ---------- */

export interface EtfKlineBar {
  date: string;
  open: number | null;
  close: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
}

export interface EtfHoldingItem {
  code: string;
  name: string;
  /** 占净值比例 % */
  ratio: number | null;
  /** 持仓市值（亿元） */
  mv_yi: number | null;
}

export interface EtfIndustryItem {
  industry: string;
  ratio: number | null;
  mv_yi: number | null;
}

export interface EtfTrackingPoint {
  date: string;
  /** 累计涨跌幅 % */
  etf: number;
  /** 累计涨跌幅 % */
  index: number;
}

export type BlockStatus = 'ok' | 'degraded' | 'unavailable';

export interface EtfDetailBlock {
  status: BlockStatus;
  reason?: string;
  note?: string;
}

export interface EtfDetailHeader extends EtfDetailBlock {
  name: string | null;
  code: string;
  country: EtfCountry;
  price: number | null;
  pct: number | null;
  amount: number | null;
  size_yi: number | null;
  management_fee: number | null;
  custody_fee: number | null;
  tracking_index: string | null;
  manager: string | null;
  inception: string | null;
}

export interface EtfDetailKline extends EtfDetailBlock {
  period: string;
  bars: EtfKlineBar[];
}

export interface EtfDetailHoldings extends EtfDetailBlock {
  holdings: { date: string; items: EtfHoldingItem[] };
  industry: { date: string; items: EtfIndustryItem[] };
}

export interface EtfDetailTracking extends EtfDetailBlock {
  benchmark_code: string;
  benchmark_name: string;
  points: EtfTrackingPoint[];
  tracking_error: number | null;
}

export interface EtfDetailValuation extends EtfDetailBlock {
  pe_ttm: number | null;
  pb: number | null;
  index_code: string | null;
  index_name: string | null;
  pe_percentile: number | null;
  pb_percentile: number | null;
}

export interface EtfDetailFlow extends EtfDetailBlock {
  /**
   * `net_inflow` = 东财 `f52` **主力净额**（元，= 大单 + 超大单；审计 P1-14 修复）。
   * `null` = 源字段为 `-`（停牌/无数据）——不再像修复前那样伪造成 0。
   * 分项净额（大单/超大单/中单/小单）为附加字段，便于独立核对"主力=大单+超大单"。
   */
  items: Array<{ date: string; net_inflow: number | null;
                 super_large_net?: number | null; large_net?: number | null;
                 medium_net?: number | null; small_net?: number | null }>;
}

/** 基金公告 / 新闻动态 */
export interface EtfDetailNews extends EtfDetailBlock {
  items: Array<{ date: string; title: string; category: string; url: string | null }>;
}

/** 情感打分（基于公告标题的关键词统计） */
export interface EtfDetailSentiment extends EtfDetailBlock {
  score: number;
  label: string;
  positive: number;
  negative: number;
  samples: Array<{ title: string; positive: number; negative: number }>;
  basis: string;
}

/** 产业链归集（由持仓细分行业映射） */
export interface EtfDetailChain extends EtfDetailBlock {
  items: Array<{ chain: string; ratio: number }>;
}

export interface EtfDetail {
  code: string;
  name: string | null;
  country: EtfCountry;
  status: BlockStatus;
  blocks: {
    header: EtfDetailHeader;
    kline: EtfDetailKline;
    holdings: EtfDetailHoldings;
    tracking: EtfDetailTracking;
    valuation: EtfDetailValuation;
    flow: EtfDetailFlow;
    news: EtfDetailNews;
    sentiment: EtfDetailSentiment;
    chain: EtfDetailChain;
  };
}
