/** 选股 API（P1-8）。
 *
 * 历史问题（审计 1.3）：此文件曾导出 backtestApi.run() 指向 /backtest/run，
 * 但全项目 0 处 import —— Backtest 页面实际走 strategyBacktestApi
 * （/backtest/strategy-run），属于「后端有实现、前端有壳、无页面消费」的死代码，
 * 已删除。回测导出请使用 api/export.ts。
 */
import { get } from './client';
import type { ScreenerResult, ScreenerSeriesEnvelope, StockListResult, WatchlistQuotes } from '@/types/p1';

/** 全市场股票列表：冷算要遍历全市场 + 打外部快照，超时放宽到 30s（默认 15s 会误判断线） */
const STOCKS_TIMEOUT = 30_000;
/** 选股：`refresh=1` 时后端重算全市场截面，与同族 `/screener/stocks` 口径一致放宽到 30s */
const SCREEN_TIMEOUT = 30_000;
/**
 * KPI 历史序列：冷路径要复算 30+ 个预测分区与截面日线（后端实测 ~1.5s），
 * 命中缓存 <5ms。与 `screen` 同放 30s，避免冷启动被 15s 默认超时误判为断线。
 */
const SERIES_TIMEOUT = 30_000;

export const screenerApi = {
  screen: (params: { date?: string; strategy?: string; top_k?: number; board?: string },
           refresh = false) =>
    get<ScreenerResult>('/api/v1/screener',
      { ...params, ...(refresh ? { refresh: 1 } : {}) }, SCREEN_TIMEOUT),

  /**
   * KPI 卡片的历史序列（近 `days` 个预测交易日，10~60）。
   *
   * ⚠️ **`top_k` 必须传当前榜单实际用的 top_k**：序列是"每日取前 top_k 名"复算出来的，
   * 与页面榜单不同 top_k 会让卡片数字与曲线口径不一致。`board` 同理。
   *
   * 能否绘制由响应里的 `metrics[key].enough && .comparable` 决定：
   * `win_rate`/`avg_pct`/`avg_score` 可画；三个**计数**指标
   * （`pool_size`/`strong_signal`/`industry_count`）后端标 `comparable=false`
   * （窗口内预测覆盖度 4.8%→97.5%，序列起伏由数据补全进度驱动），**不得画成趋势**。
   */
  statsSeries: (params: { days?: number; strategy?: string; top_k?: number; board?: string },
                refresh = false) =>
    get<ScreenerSeriesEnvelope>('/api/v1/screener/stats/series',
      { ...params, ...(refresh ? { refresh: 1 } : {}) }, SERIES_TIMEOUT),

  /**
   * 全市场股票列表（服务端筛选 + 排序 + 分页）。
   *
   * board / industry 默认 'all'；sort 传空串表示后端默认顺序（code 升序）；
   * dir 默认 desc；basis=auto 由后端自行决定实时 / 日终口径并回传 basis_desc。
   */
  stocks: (params: {
    board?: string;
    industry?: string;
    q?: string;
    exclude_st?: number;
    sort?: string;
    dir?: string;
    page?: number;
    page_size?: number;
    basis?: string;
    refresh?: number;
  }) => get<StockListResult>(
    '/api/v1/screener/stocks', params as Record<string, unknown>, STOCKS_TIMEOUT),

  /** 自选股行情快照：symbols 为标的代码数组（后端以逗号拼接） */
  watchlist: (symbols: string[]) =>
    get<WatchlistQuotes>('/api/v1/screener/watchlist', { symbols: symbols.join(',') }),
};
