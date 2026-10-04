/** ETF 中心 API 分组。 */

import { TIMEOUT } from './timeouts';
import { get } from './client';
import type {
  EtfDetail, EtfFlowItem, EtfItem, EtfListResult, EtfOverview,
  EtfOverviewSeries, EtfPerformance, EtfScale,
} from '@/types/etf';

/** 列表接口会带上四国全量目录，超时放宽到 30s */
const ETF_TIMEOUT = TIMEOUT.aggregate;

export const etfApi = {
  overview: () => get<EtfOverview>('/api/v1/etf/overview', undefined, ETF_TIMEOUT),

  /**
   * KPI 卡片的**历史序列**（近 `days` 天快照，10~60）。
   *
   * 用于把卡片右侧的「写死装饰环 / 两点假趋势线」换成真实序列。
   * 能否绘制由响应里的 `metrics[key].enough && .comparable` 决定（当前 ETF 侧
   * 恒为 false ⇒ 卡片显示「暂无历史序列」），**前端不做任何补齐**。
   */
  overviewSeries: (days = 30) =>
    get<EtfOverviewSeries>('/api/v1/etf/overview/series', { days }, ETF_TIMEOUT),

  list: (params: {
    country?: string;
    board?: string;
    etype?: string;
    index?: string;
    manager?: string;
    q?: string;
    min_size?: number;
    max_size?: number;
    inception_from?: string;
    inception_to?: string;
    /** 排序键：'' | size | amount | pct | code，空串=后端默认顺序 */
    sort?: string;
    /** 排序方向：asc | desc（默认 desc） */
    dir?: string;
    page?: number;
    page_size?: number;
  }) => get<EtfListResult>('/api/v1/etf/list', params as Record<string, unknown>, ETF_TIMEOUT),

  /**
   * 热门 ETF TOP N。
   *
   * `country` / `board` / `etype` 三者缺省为 `'all'` —— 与后端缺省一致，
   * 即"不传 = 全市场榜单"。传具体值后榜单随筛选联动（后端 `_filter_catalog`
   * 同一内核，条件间 AND）。
   */
  hot: (limit = 5, sort: 'amount' | 'pct' = 'amount', filters?: {
    country?: string;
    board?: string;
    etype?: string;
  }) =>
    get<{
      items: EtfItem[];
      sort_applied?: string;
      /** 实际生效的过滤条件（仅含非 all 项），便于区分"已过滤"与"参数未生效" */
      filters_applied?: Record<string, string>;
    }>(
      '/api/v1/etf/hot',
      { limit, sort, country: filters?.country, board: filters?.board,
        etype: filters?.etype },
      ETF_TIMEOUT),

  performance: (symbols: string, metric = 'pct', period = '1y') =>
    get<EtfPerformance>('/api/v1/etf/performance',
      { symbols, metric, period }, ETF_TIMEOUT),

  scale: (period = '1m', topN = 10) =>
    get<EtfScale>('/api/v1/etf/scale', { period, top_n: topN }, ETF_TIMEOUT),

  flow: (period = '1d', limit = 10) =>
    get<{
      period: string;
      items: EtfFlowItem[];
      /** 'ok' | 'unavailable'：数据源不可用时后端返回结构化降级（HTTP 200 信封） */
      status?: string;
      /** 数据源不可用时的可读原因 */
      reason?: string;
      /** 作用域标识（后端回显，如 'all_market'）。本块**不**跟随页面筛选，
       *  故必须把作用域如实渲染出来，避免用户误读为"已按国家/板块筛过"。 */
      scope?: string;
      /** 作用域的可读说明（后端回显，前端不得自行拼写） */
      scope_note?: string;
    }>('/api/v1/etf/flow', { period, limit }, ETF_TIMEOUT),

  /**
   * ETF 详情聚合块。
   *
   * ⚠️ `klinePeriod` 会改后端**服务端聚合**口径（`_build_kline_block` 内的
   * `E.aggregate_kline`）。但 `KLineChart` 内部**还有一层前端聚合**（其自带
   * 日/周/月/季/年周期栏）。若调用方传非 `'day'` 的值，图表会把「周线」再当「日线」
   * 聚合一次（或把月线聚成季线），**数字全错且无任何报错**。
   *
   * 因此约定：**页面恒用默认值 `'day'`**，周期一律交给图表前端聚合
   * （个股详情页 `/stock/{symbol}/kline` 无 period 参数，即是此口径）。
   * 非 `'day'` 仅用于"只想拿周线/月线数据、不渲染 `KLineChart`"的消费方。
   */
  detail: (code: string, klinePeriod: 'day' | 'week' | 'month' = 'day') =>
    get<EtfDetail>(`/api/v1/etf/detail/${code}`, { kline_period: klinePeriod }, ETF_TIMEOUT),
};
