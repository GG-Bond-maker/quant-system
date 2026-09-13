/**
 * 导出 API（P1-6 接线）。
 *
 * 背景（审计 1.2）：后端 /export/screener 与 /export/backtest 早已实现
 * （含 workbook 生成），但前端 0 处引用，导出能力完全无入口。
 *
 * 两个端点都返回二进制 xlsx（非统一信封），因此走 client.download，
 * 由它解析文件名并处理"HTTP 200 + JSON 错误信封"的情况。
 */
import { download } from './client';

export const exportApi = {
  /** 选股 Top-N 导出（GET，服务端按 date/top_k/board 重新计算） */
  screener: (params: { date?: string; top_k: number; board: string }) =>
    download('/api/v1/export/screener', 'screener.xlsx', params),

  /**
   * 策略回测导出（POST，服务端用相同参数重跑真实引擎）。
   * 与页面展示的 /backtest/strategy-run 同引擎同口径，
   * 导出 trades + nav + 参数快照三个 sheet。
   */
  strategyBacktest: (req: {
    strategy_name: string; start: string; end: string;
    init_cash: number; commission_rate: number;
    short_ma: number; long_ma: number; trailing_stop_pct: number;
    symbols: string[];
  }) => download('/api/v1/export/strategy-backtest', 'strategy_backtest.xlsx', undefined, req),

  /**
   * Top-K 调仓回测导出（POST，trades + holdings 双 sheet）。
   * 注意：这是 /backtest/run 引擎的导出，当前前端没有对应页面，
   * 保留以备后续接入调仓回测 UI 时使用。
   */
  backtest: (req: {
    start: string; end: string; top_k: number; init_cash: number;
    rebalance_freq: 'daily' | 'weekly'; enable_friction: boolean;
  }) => download('/api/v1/export/backtest', 'backtest.xlsx', undefined, req),
};
