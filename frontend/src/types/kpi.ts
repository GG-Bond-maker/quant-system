/**
 * KPI 卡片历史序列的**共享契约**（选股中心 + ETF 中心）。
 *
 * 与后端 `backend/app/data/kpi_series.py` 的 `MetricSeries.to_dict()` 一一对应；
 * 两个端点复用同一形状：
 * - `GET /api/v1/screener/stats/series`（选股中心，有真实历史）
 * - `GET /api/v1/etf/overview/series`（ETF 中心，靠每日盘后归档累积）
 *
 * ## 为什么单独放一个文件
 * 两个页面（Screener / Etf）的 KPI 卡片消费**同一份**序列契约。若各自在
 * `types/p1.ts` / `types/etf.ts` 里各写一份，后端改字段时极易只改一边 ——
 * 本轮 `/etf/overview` 的 `net_inflow_yi` 就是「生产侧改了契约、消费侧没跟」
 * 导致整页白屏的实例。故此处作为**单一事实源**，两个页面都从这里取。
 *
 * ## 绘制开关（前端唯一判据）
 * `MetricSeries.enough && MetricSeries.comparable` —— 两者皆为 `true` 才允许绘制。
 * 任一为 `false` 时必须显示「暂无历史序列」占位，**不得**用任何方式补齐曲线。
 */

/** 序列中的一个真实观测点（后端已剔除 `value == null` 的日期，不会出现空洞占位）。 */
export interface SeriesPoint {
  /** 观测日 YYYY-MM-DD（升序） */
  date: string;
  /**
   * 观测值。后端在 `to_dict()` 前已过滤掉 `None` ⇒ 契约上**非空**
   * （`screener` 侧由 `_build_metric` 过滤，ETF 侧由 `s.get(k) is not None` 过滤）。
   * 若线上出现 `null`，说明后端契约被破坏，消费侧（`Sparkline`）仍会防御性丢弃该点。
   */
  value: number;
  /** 该日参与统计的样本数（ETF 侧为当日 ETF 条数） */
  n: number | null;
  /** 该日候选池规模（计数类指标可比性判定用；ETF 侧为 null） */
  pool_size: number | null;
  /** 该日预测覆盖度 = pool_size / universe 行数（ETF 侧为 null） */
  coverage: number | null;
}

/**
 * 单个指标的历史序列 + 口径 + 可绘制性判定。
 *
 * `enough` / `comparable` 是前端**唯一**的绘制开关（见文件头注释）。
 */
export interface MetricSeries {
  /** 指标键，与 `KpiSeriesEnvelope.metrics` 的键一致 */
  key: string;
  /** 恒为 `"platform"`（平台口径，非某个策略私有口径） */
  kind: string;
  /** 口径说明（后端保证非空串；用于向用户披露派生指标的计算方式） */
  basis: string;
  /** 单位：% / 只 / 个 / 亿元 / 小数 */
  unit: string;
  /** 有效点数 ≥ `min_points`(6) 时为 true —— 少于此不画（如 ETF 侧存档初期） */
  enough: boolean;
  /**
   * 计数类指标（pool_size / strong_signal / industry_count）在窗口内预测覆盖度
   * 极差 > 10% 时为 false：序列起伏主要由「数据补全进度」驱动，不是市场变化，
   * 画成趋势即误读。此类卡片改用「真实构成环」而非趋势线。
   */
  comparable: boolean;
  /** 有效点数 */
  count: number;
  points: SeriesPoint[];
  /** 被剔除的日期及原因（分区缺失 / pool_size < top_k / 非交易日 …），**绝不补值** */
  dropped: Array<{ date: string; reason: string }>;
  /** 口径补充说明或不可绘制原因（可空） */
  note: string | null;
}

/**
 * 两个序列端点的公共信封。
 *
 * 注意：`screener` 端点额外带 `strategy` / `top_k` / `board`（见 `ScreenerSeriesEnvelope`），
 * 两个端点在**整体不可用**时都会给顶层 `status` + `reason`（可选字段）。
 */
export interface KpiSeriesEnvelope {
  /** 窗口内最新观测日；无数据时为 null */
  as_of: string | null;
  /** 实际回溯窗口（交易日 / 快照天数） */
  window_days: number;
  /** 绘制所需最少点数（后端常量 MIN_POINTS = 6） */
  min_points: number;
  /** = `enough && comparable` 的指标键列表；**当前为空即一个都不画** */
  drawable: string[];
  /** 指标键 -> 序列 */
  metrics: Record<string, MetricSeries>;
  /** 整体不可用时后端给的状态（可用时该字段缺省，不是 `'ok'`） */
  status?: 'ok' | 'unavailable';
  /** 整体不可用时的可读原因 */
  reason?: string | null;
}

/** `GET /api/v1/screener/stats/series` 响应（在公共信封上追加口径回显字段）。 */
export interface ScreenerSeriesEnvelope extends KpiSeriesEnvelope {
  /** 策略标识（回显请求参数） */
  strategy: string;
  /** 每日取前 top_k 名（回显请求参数；**必须与页面榜单一致**，否则卡片口径不符） */
  top_k: number;
  /** 板块（回显请求参数） */
  board: string;
}
