/**
 * 期货中心与期货分析类型定义（与后端 /api/v1/futures 对齐）。
 */

export interface FuturesContractItem {
  symbol: string;               // 如 "IF主力"
  prefix: string;               // 如 "IF"
  contract_code?: string;       // 如 "IF2610"
  name: string;                 // 如 "沪深300股指期货"
  exchange: string;             // 如 "CFFEX"
  category: string;             // 'index' | 'bond' | 'precious' | 'metal' | 'ferrous' | 'energy' | 'agri'
  multiplier: number;           // 合约乘数
  tick_size: number;            // 最小变动价位
  margin_rate: number;          // 保证金率 (0.12)
  margin_per_lot: number;       // 每手保证金占用 (元)
  close: number;                // 最新价
  change_pct: number;           // 涨跌幅 (%)
  volume: number;               // 成交量
  open_interest: number;        // 持仓量
  spot_price: number;           // 现货指数价格
  basis: number;                // 基差 (现货 - 期货)
  basis_rate: number;           // 基差率 (%)
  annualized_basis_rate: number;// 年化升贴水率 (%)
  days_to_expiry?: number;      // 距交割日剩余自然日 (DTE)
  delivery_date?: string;       // 交割日
  basis_percentile?: number;    // 历史基差分位数 (0~100%)
  percentile_regime?: string;   // 极度贴水 / 温和贴水 / 极度升水
}

export interface FuturesMarketSnapshot {
  as_of: string;
  degraded: boolean;
  count: number;
  items: FuturesContractItem[];
}

export interface TermStructureContract {
  label: string;                // "当月合约", "下月合约", "当季合约", "隔季合约"
  contract_code: string;        // "IF2410"
  delivery_date?: string;       // 交割日
  days_to_expiry: number;       // 到期天数
  price: number;                // 期货价格
  spot_price: number;           // 现货价格
  basis: number;                // 基差
  basis_rate: number;           // 基差率 (%)
  annualized_basis_rate: number;// 年化基差率 (%)
  open_interest?: number;       // 持仓量 (手)
  volume?: number;              // 成交量 (手)
  oi_share_pct?: number;        // 持仓占比 (%)
}

export interface RolloverStats {
  near_code: string;
  next_code: string;
  days_to_expiry: number;
  delivery_date?: string;
  progress_pct: number;
  stage: string;                // "常规持仓期" | "移仓启动期" | "换月高峰期" | "主力切换期"
  stage_desc: string;
  status_tag: 'success' | 'brand' | 'warn' | 'danger';
  near_oi: number;
  next_oi: number;
  total_oi: number;
  capital_commitment_yi: number;// 沉淀资金 (亿元)
}

export interface CostOfCarry {
  spot_price: number;
  dte: number;
  interest_rate_pct: number;
  dividend_yield_pct: number;
  net_carry_rate_pct: number;
  fair_price: number;
  fair_basis: number;
  friction_val: number;
  upper_band: number;
  lower_band: number;
  near_price: number;
  mispricing: number;
  arbitrage_status: 'fair' | 'cash_and_carry' | 'reverse_cash_and_carry';
  arbitrage_label: string;
  arbitrage_desc: string;
}

export interface TermStructureResult {
  symbol_prefix: string;
  name: string;
  spot_price: number;
  structure_type: string;       // "Contango (正向/升水)" | "Backwardation (反向/贴水)"
  contracts: TermStructureContract[];
  rollover_stats?: RolloverStats;
  cost_of_carry?: CostOfCarry;
}

export interface FuturesKlineBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  open_interest: number;
  is_continuous?: boolean;
  orig_close?: number;
  adj_factor?: number;
}

/** 从区间最高点计算的回测表条目（Drawdown from Peak Breakdown Table） */
export interface DrawdownPeriod {
  peak_date: string;            // 高点日期
  peak_value: number;           // 高点净值
  trough_date: string;          // 谷底日期
  trough_value: number;         // 谷底净值
  recovery_date: string | null; // 修复至新高日期
  max_drawdown_pct: number;     // 回撤幅度 (%)
  drawdown_days: number;        // 下跌历时 (交易日)
  recovery_days: number;        // 修复历时 (交易日)
  total_duration_days: number;  // 总持续天数
  is_recovered: boolean;        // 是否已修复
}

export interface TradeStats {
  trades: number;
  wins: number;
  win_rate: number;
  total_pnl: number;
}

export interface CostStressCell {
  slippage_ticks: number;
  fee_multiplier: number;
  sharpe: number;
  total_return_pct: number;
  max_drawdown_pct: number;
}

export interface CostStressMatrix {
  grid: CostStressCell[];
  breakeven_slippage_ticks: number | null;
  tested_slippages: number[];
  tested_fee_multipliers: number[];
}

export interface FuturesBacktestResult {
  status: string;
  symbol_prefix: string;
  name: string;
  strategy_type: string;        // 'dual_ma' | 'donchian' | 'hedge'
  direction: string;            // 'both' | 'long_only' | 'short_only'
  leverage: number;
  init_cash: number;
  final_equity: number;
  total_return_pct: number;
  ann_return_pct: number;
  annual_volatility_pct?: number;
  max_drawdown_pct: number;
  current_drawdown_pct: number;
  sharpe_ratio: number;
  sortino_ratio?: number;
  calmar_ratio: number;
  peak_margin_ratio?: number;
  
  // 基准相对与 Alpha/Beta 归因
  benchmark_name?: string;
  benchmark_return_pct?: number;
  benchmark_ann_return_pct?: number;
  excess_return_pct?: number;
  beta?: number;
  alpha_annual_pct?: number;
  tracking_error_pct?: number;
  information_ratio?: number;

  // 极端风险与杠杆流动性
  var_95_pct?: number;
  cvar_95_pct?: number;
  margin_call_count?: number;
  liquidation_count?: number;

  // 统计学显著性与防过拟合
  total_trades?: number;
  profit_factor?: number;
  payoff_ratio?: number;
  avg_win?: number;
  avg_loss?: number;
  max_win?: number;
  max_loss?: number;
  max_win_contrib_pct?: number;
  t_statistic?: number;
  p_value?: number;
  psr?: number;
  dsr?: number;
  is_sample_sufficient?: boolean;

  // 微观摩擦与回测参数回传
  slippage_ticks?: number;
  fee_multiplier?: number;
  history_days?: number;

  long_stats: TradeStats;
  short_stats: TradeStats;
  overall_win_rate: number;
  dates: string[];
  equity_curve: number[];
  benchmark_curve: number[];
  drawdown_curve: number[];
  margin_used_curve: number[];
  margin_ratio_curve?: number[];
  positions: number[];          // +1 多头, -1 空头, 0 空仓
  trades?: FuturesTradeRecord[];
  top_drawdowns: DrawdownPeriod[];
  cost_stress_matrix?: CostStressMatrix;
}

export interface FuturesBacktestRequest {
  symbol: string;
  strategy_type?: 'dual_ma' | 'donchian' | 'hedge';
  start_date?: string;
  end_date?: string;
  init_cash?: number;
  leverage?: number;
  fast_period?: number;
  slow_period?: number;
  direction?: 'both' | 'long_only' | 'short_only';
  beta?: number;
  continuous?: boolean;         // 是否采用展期后复权连续合约
  slippage_ticks?: number;      // 滑点跳数 (最小变动价位)
  fee_multiplier?: number;      // 手续费倍数 (应力测试)
  history_days?: number;        // 回测历史天数 (250 / 500 / 1000 / 全量)
}

export interface CalendarSpreadPoint {
  date: string;
  near_price: number;
  far_price: number;
  spread: number;
  spread_pct: number;
  z_score?: number;
}

export interface CalendarSpreadResult {
  symbol_prefix: string;
  name: string;
  near_contract: string;
  far_contract: string;
  count: number;
  mean_spread: number;
  std_spread: number;
  current_spread: number;
  current_z_score: number;
  arbitrage_signal: 'short_near_long_far' | 'long_near_short_far' | 'neutral';
  series: CalendarSpreadPoint[];
}

export interface HedgeCalcResult {
  symbol: string;
  name: string;
  portfolio_value: number;
  futures_price: number;
  multiplier: number;
  contract_value_per_lot: number;
  margin_rate: number;
  beta: number;
  required_contracts: number;
  total_notional_value: number;
  total_margin_required: number;
  hedge_ratio_pct: number;
}

export interface CotMemberRecord {
  rank: number;
  member_name: string;
  long_volume: number;
  long_change: number;
  short_volume: number;
  short_change: number;
  net_position: number;
  net_change: number;
}

export interface CotSummary {
  total_long: number;
  total_short: number;
  long_change_total: number;
  short_change_total: number;
  net_position_total: number;
  long_short_ratio: number;
  top_bull_member: string;
  top_bear_member: string;
}

export interface CotResult {
  symbol_prefix: string;
  name: string;
  as_of: string;
  count: number;
  records: CotMemberRecord[];
  summary: CotSummary;
}

export interface InterCommoditySpreadPoint {
  date: string;
  asset1_price: number;
  asset2_price: number;
  spread_value: number;
  z_score?: number;
}

export interface HalfLifeStats {
  half_life_days: number | null;
  is_stationary: boolean;
  lambda_param: number;
  reversion_speed: 'fast' | 'moderate' | 'slow' | 'divergent' | 'static' | 'insufficient_data';
}

export interface InterCommoditySpreadResult {
  pair_key: string;
  name: string;
  asset1_name: string;
  asset2_name: string;
  type: 'ratio' | 'spread';
  current_value: number;
  mean_value: number;
  std_value: number;
  current_z_score: number;
  arbitrage_signal: 'short_asset1_long_asset2' | 'long_asset1_short_asset2' | 'neutral';
  half_life_stats: HalfLifeStats;
  count: number;
  series: InterCommoditySpreadPoint[];
}

export interface RollYieldItem {
  prefix: string;
  name: string;
  exchange: string;
  category: string;
  near_price: number;
  far_price: number;
  near_dte: number;
  far_dte: number;
  roll_yield_pct: number;
  structure_type: string;
  action_hint: string;
}

export interface RollYieldMatrixResult {
  as_of: string;
  total_count: number;
  top_backwardation: RollYieldItem[];
  top_contango: RollYieldItem[];
  all_items: RollYieldItem[];
}

export interface BasisHistoryPoint {
  date: string;
  spot_price: number;
  futures_price: number;
  basis: number;
  basis_rate: number;
  annualized_basis_rate: number;
}

export interface BasisPercentileStats {
  percentile: number;
  regime: string;
  historical_min: number;
  historical_max: number;
  historical_mean: number;
}

export interface BasisHistoryResult {
  symbol_prefix: string;
  name: string;
  current_basis: number;
  percentile_stats: BasisPercentileStats;
  count: number;
  series: BasisHistoryPoint[];
}

export interface FuturesTradeRecord {
  id: string;
  direction: 'LONG' | 'SHORT';
  entry_date: string;
  entry_price: number;
  exit_date: string;
  exit_price: number;
  lots: number;
  fee: number;
  pnl: number;
  pnl_pct: number;
}

export interface VolatilityStats {
  hv20_pct: number;
  atr14: number;
  atr_pct: number;
}

export interface OpenInterestStats {
  latest_oi: number;
  oi_change: number;
  oi_change_pct: number;
  structure: string;
}

export interface FuturesKlineWithIndicator extends FuturesKlineBar {
  ma5: number;
  ma10?: number;
  ma20: number;
  ma60?: number;
  oi_delta?: number;
}

export interface FuturesInstrumentProfile {
  symbol_prefix: string;
  name: string;
  exchange: string;
  category: string;
  multiplier: number;
  tick_size: number;
  tick_value: number;
  margin_rate: number;
  margin_rate_pct: number;
  fee_type: string;
  fee_unit: number;
  underlying_code: string | null;
  latest_price: number;
  contract_value: number;
  margin_per_lot: number;
  volatility_stats: VolatilityStats;
  open_interest_stats: OpenInterestStats;
  kline: FuturesKlineWithIndicator[];
}

export interface ParameterSensitivityPoint {
  fast: number;
  slow: number;
  sharpe: number;
  total_return_pct: number;
  max_drawdown_pct: number;
}

export interface ParameterSensitivityResult {
  symbol_prefix: string;
  name: string;
  strategy_type: string;
  grid: ParameterSensitivityPoint[];
  optimal_params: ParameterSensitivityPoint | null;
  total_combinations: number;
  cost_stress_matrix?: CostStressMatrix;
}


