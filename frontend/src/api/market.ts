/** 市场概览 API 分组。 */

import { TIMEOUT } from './timeouts';
import { get } from './client';
import type {
  AhSpreadResult, GlobalIndicesBlock, OverviewDaily, OverviewRt,
} from '@/types/stock';

/** 首次构建聚合可能较慢（全市场快照 + AI 准确率回溯），客户端超时放宽到 120s；Redis 缓存命中后 <5ms。
 * ⚠️ 服务端预算以 `market.py` 的常量为准（**勿在此处复述数字**，历史上这里写的 6s 已过期）：
 *   - 日频块 `DAILY_BUILD_TIMEOUT_SECONDS`（2026-10-04 实测：冷启动首次 7.6s）；
 *   - 实时块 `RT_BUILD_TIMEOUT_SECONDS`。
 * 超时返回结构化 degraded 载荷（data_freshness.status=degraded）而非让连接一直挂起；
 * 本前端 120s 仅作兜底（远大于服务端预算 ⇒ 服务端预算是唯一约束）。
 * 导出供页面经 useApi 直接调用 overview 系列时复用（避免魔数重复）。 */
export const OVERVIEW_TIMEOUT = TIMEOUT.heavy;

/** 核心指数日 K（选股中心「大盘走势」用；腾讯源，后端白名单校验） */
export interface IndexKlineData {
  code: string;
  name: string;
  bars: Array<{ date: string; close: number }>;
}

/**
 * 批量实时行情条目（§3.3；amount=元）。
 * ⚠️ `volume` 单位=【手】；`daily_bar`/`fetch_kline` 的 volume 是【股】，
 * 两者**不同口径**（差 100 倍），跨源计算前必须归一。
 */export interface DepthLevel {
  price: number | null;
  volume: number | null;
}

export interface LiveQuote {
  symbol: string;
  name: string | null;
  price: number | null;
  pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prev_close: number | null;
  /** 成交量（手，向后兼容口径） */
  volume: number | null;
  /** 成交量（手） */
  volume_hand?: number | null;
  /** 成交量（股，与日线/Parquet 数据湖绝对对齐） */
  volume_shares?: number | null;
  amount: number | null;
  /** 换手率（%） */
  turnover: number | null;
  /** 总市值（亿元） */
  total_cap_yi: number | null;
  /** 流通市值（亿元） */
  float_cap_yi: number | null;
  /** 当日涨停价（元） */
  limit_up: number | null;
  /** 当日跌停价（元） */
  limit_down: number | null;
  as_of: string | null;
  source: string;
  /** L2 五档买卖深度盘口 */
  bids?: DepthLevel[];
  asks?: DepthLevel[];
}

export interface QuotesData {
  as_of: string | null;
  source: 'tencent' | 'sina' | 'degraded' | string;
  quotes: LiveQuote[];
}

export const marketApi = {
  /** 实时块（L2-2）：指数/资金/异动，TTL 45s；轻刷新只打本端点 */
  overviewRt: (refresh = false) =>
    get<OverviewRt>('/api/v1/market/overview/rt',
      { ...(refresh ? { refresh: 1 } : {}) }, OVERVIEW_TIMEOUT),

  /** 日频块（L2-2）：本地口径分布/推荐榜/情绪，TTL 至次日盘后，零外部依赖；
   * date 仅切换 AI 推荐历史快照（与 overview 同语义） */
  overviewDaily: (recommendK = 50, date?: string, refresh = false) =>
    get<OverviewDaily>('/api/v1/market/overview/daily',
      { recommend_k: recommendK,
        ...(date ? { date } : {}),
        ...(refresh ? { refresh: 1 } : {}) },
      OVERVIEW_TIMEOUT),

  /** 外盘块：日经225/KOSPI/道指/纳指/标普500（新浪日线，TTL 30 分钟）。
   * 每项带**自己的** data_date（不同市场交易日/时区不同）。 */
  overviewGlobal: (refresh = false) =>
    get<GlobalIndicesBlock>('/api/v1/market/overview/global',
      { ...(refresh ? { refresh: 1 } : {}) }, OVERVIEW_TIMEOUT),

  /** 指数日 K（升序收盘序列）；limit 默认 400 根，覆盖「近1年」窗口有余量；
   * refresh=true 跳过 fetch 层 TTL 缓存强制重拉（§3.2 刷新按钮） */
  indexKline: (code: string, limit = 400, refresh = false) =>
    get<IndexKlineData>('/api/v1/market/index/kline',
      { code, limit, ...(refresh ? { refresh: 1 } : {}) }),

  /** 批量实时行情（§3.3）：≤200 只；TTL 15s 进程缓存，多页面共享一次抓取 */
  quotes: (symbols: string[]) =>
    get<QuotesData>('/api/v1/market/quotes',
      { symbols: symbols.join(',') }, TIMEOUT.quote),

  /** AH 股溢价与跨市场套利监控看板（TTL 30s） */
  ahSpread: (params?: { sort?: string; limit?: number }, refresh = false) =>
    get<AhSpreadResult>('/api/v1/market/ah-spread',
      { ...(params ?? {}), ...(refresh ? { refresh: 1 } : {}) }, TIMEOUT.quote),
};
