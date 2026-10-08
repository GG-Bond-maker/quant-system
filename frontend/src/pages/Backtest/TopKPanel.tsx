/**
 * Top-K 模型信号等权调仓回测面板（POST /backtest/run）。
 *
 * ## 顶级量化平台（JoinQuant / Qlib / Barra）专业对标
 * 1. 资产净值与现金走势图（Equity & Cash Curve，支持仓位时序透视）；
 * 2. 动态水下回撤曲线（Underwater Drawdown）；
 * 3. A 股交易闸门拒单统计（停牌/涨跌停/T+1/流动性上限严格核算，非盲目撮合）；
 * 4. 摩擦成本细分（滑点 / 冲击成本 / 换手衰减 / 退市强平）；
 * 5. 调仓流水明细表格（含标的、价格、数量、费用与调仓原因）；
 * 6. 最新截面持仓透视与「一键导出标的至组合优化」投研闭环。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { ApiError } from '@/api/client';
import { backtestApi } from '@/api/backtest';
import { datacenterApi } from '@/api/datacenter';
import { exportApi } from '@/api/export';
import type { BacktestResultData } from '@/types/p1';
import { fmtNum, fmtPct } from '@/utils/format';

const inputCls =
  'rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300 bg-surface';

/** 摩擦成本分项（后端 broker.friction_costs 的键） */
const FRICTION_LABEL: Record<string, string> = {
  slippage: '滑点', impact: '冲击成本', decay: '换手衰减', delist_loss: '退市强平减记',
};

/** 拒单原因（后端 broker/engine 的 reason 取值） */
const REJECT_LABEL: Record<string, string> = {
  halted: '停牌', limit_up: '涨停不可买', limit_down: '跌停不可卖', t1: 'T+1 限制',
  no_position: '无可卖持仓', no_cash: '现金不足', liquidity_cap: '流动性上限',
  delisted_liquidation: '退市强平', no_bar: '当日无行情',
};

export default function TopKPanel() {
  useTheme();
  const [start, setStart] = useState('2024-01-01');
  const [end, setEnd] = useState('2025-12-31');
  const [topK, setTopK] = useState(10);
  const [initCash, setInitCash] = useState(1_000_000);
  const [freq, setFreq] = useState<'daily' | 'weekly'>('daily');
  const [friction, setFriction] = useState(false);
  const [result, setResult] = useState<BacktestResultData | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [exportedTip, setExportedTip] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const ds = await datacenterApi.datasets();
        const pred = ds.items.find((d) => d.dataset === 'predictions');
        if (pred?.end) setEnd(pred.end);
        if (pred?.start) setStart(pred.start);
      } catch { /* 默认区间 */ }
    })();
  }, []);

  const run = useCallback(async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setRunning(true); setErr(null);
    try {
      const r = await backtestApi.runTopK({
        start, end, top_k: topK, init_cash: initCash,
        rebalance_freq: freq, enable_friction: friction,
      }, ctrl.signal);
      if (ctrl.signal.aborted) return;
      setResult(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '回测失败');
    } finally {
      if (abortRef.current === ctrl) setRunning(false);
    }
  }, [start, end, topK, initCash, freq, friction]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const [exporting, setExporting] = useState(false);

  const doExport = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      await exportApi.backtest({ start, end, top_k: topK, init_cash: initCash,
        rebalance_freq: freq, enable_friction: friction });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '导出失败');
    } finally {
      setExporting(false);
    }
  };

  // 1. 资产净值与现金仓位走势图配置
  const equityOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!result?.equity_curve?.length) return null;
    const p = chartPalette();
    const curve = result.equity_curve;
    const dates = curve.map((c) => c.date);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const pt = curve[idx];
          if (!pt) return '';
          const total = pt.equity + pt.cash;
          const posRatio = total > 0 ? ((pt.equity / total) * 100).toFixed(1) : '0.0';
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px">
              <b>交易日: ${pt.date}</b><br/>
              策略净值: <b style="color:${p.BRAND}">${pt.nav.toFixed(4)}</b><br/>
              持仓市值: ${(pt.equity / 1e4).toFixed(2)} 万元<br/>
              可用现金: ${(pt.cash / 1e4).toFixed(2)} 万元<br/>
              股票仓位占比: <b>${posRatio}%</b>
            </div>
          `;
        },
      },
      legend: {
        data: ['策略净值 (NAV)', '持仓市值 (万元)', '现金头寸 (万元)'],
        top: 4,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 48, right: 48, top: 32, bottom: 40 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: [
        {
          type: 'value',
          name: '净值',
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(2) },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        {
          type: 'value',
          name: '金额 (万元)',
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${(v / 1e4).toFixed(0)}万` },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside' },
        {
          type: 'slider',
          bottom: 2,
          height: 16,
          borderColor: p.HAIR,
          fillerColor: withAlpha(p.BRAND, 0.15),
          handleStyle: { color: p.BRAND },
        },
      ],
      series: [
        {
          name: '策略净值 (NAV)',
          type: 'line',
          yAxisIndex: 0,
          data: curve.map((c) => c.nav),
          showSymbol: false,
          lineStyle: { width: 2, color: p.BRAND },
          itemStyle: { color: p.BRAND },
        },
        {
          name: '持仓市值 (万元)',
          type: 'line',
          yAxisIndex: 1,
          data: curve.map((c) => c.equity),
          showSymbol: false,
          lineStyle: { width: 1.2, color: p.UP },
          itemStyle: { color: p.UP },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: withAlpha(p.UP, 0.25) },
              { offset: 1, color: withAlpha(p.UP, 0.02) },
            ]),
          },
        },
        {
          name: '现金头寸 (万元)',
          type: 'line',
          yAxisIndex: 1,
          data: curve.map((c) => c.cash),
          showSymbol: false,
          lineStyle: { width: 1.2, color: p.INKM, type: 'dashed' },
          itemStyle: { color: p.INKM },
        },
      ],
    };
  }, [result]);

  const equityChartRef = useChart(equityOption);

  // 2. 水下回撤曲线配置
  const ddOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!result?.drawdown_curve?.length) return null;
    const p = chartPalette();
    const curve = result.drawdown_curve;
    const dates = curve.map((c) => c.date);
    const ddVals = curve.map((c) => c.drawdown * 100);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const pt = curve[idx];
          if (!pt) return '';
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px">
              <b>交易日: ${pt.date}</b><br/>
              水下回撤: <b style="color:${p.DOWN}">-${(Math.abs(pt.drawdown) * 100).toFixed(2)}%</b>
            </div>
          `;
        },
      },
      grid: { left: 48, right: 16, top: 16, bottom: 20 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, color: p.INKM, interval: Math.floor(dates.length / 6) },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'value',
        max: 0,
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v.toFixed(0)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: '水下回撤',
          type: 'line',
          showSymbol: false,
          data: ddVals,
          lineStyle: { width: 1.4, color: p.DOWN },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: withAlpha(p.DOWN, 0.35) },
              { offset: 1, color: withAlpha(p.DOWN, 0.04) },
            ]),
          },
        },
      ],
    };
  }, [result]);

  const ddChartRef = useChart(ddOption);

  // 一键将当前持仓标的推入组合优化工作台
  const handleExportUniverse = () => {
    if (!result?.holdings?.length) return;
    const lastHolding = result.holdings[result.holdings.length - 1].holdings;
    const symbols = Object.keys(lastHolding);
    if (!symbols.length) return;
    try {
      localStorage.setItem('aqp:optimizer_imported_universe', JSON.stringify(symbols));
      setExportedTip(`已成功导出最新 ${symbols.length} 只持仓标的至组合优化工作台！可在「投资组合」中一键加载。`);
      setTimeout(() => setExportedTip(null), 4000);
    } catch {
      setExportedTip('导出失败，请重试');
    }
  };

  const m = result?.metrics;

  // 提取最新持仓列表
  const latestHoldings = useMemo(() => {
    if (!result?.holdings?.length) return [];
    const last = result.holdings[result.holdings.length - 1];
    return Object.entries(last.holdings).map(([symbol, weight]) => ({
      symbol,
      weight,
    }));
  }, [result]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-2xs text-ink-muted">
          基于本地 predictions 模型信号的 Top-K 等权调仓回测（
          {result?.universe_scope?.dataset ?? 'universe_daily_bt'} + pred_daily）。
        </p>
        {result && (
          <span className="text-3xs text-ink-muted">
            模型版本: <span className="font-mono text-ink">{result.model_version}</span> ·
            状态: <span className="font-mono text-ink">{result.from_cache ? '缓存' : '实时计算'}</span>
          </span>
        )}
      </div>

      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}

      {/* 参数控制栏 */}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-hair bg-surface p-3 text-2xs">
        <label>起始<input type="date" value={start} onChange={(e) => setStart(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <label>结束<input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <label>Top-K<input type="number" min={1} max={100} value={topK}
          onChange={(e) => setTopK(Number(e.target.value))} className={`${inputCls} ml-1 w-16`} /></label>
        <label>初始资金<input type="number" value={initCash}
          onChange={(e) => setInitCash(Number(e.target.value))} className={`${inputCls} ml-1 w-28`} /></label>
        <select value={freq} onChange={(e) => setFreq(e.target.value as 'daily' | 'weekly')}
          className={inputCls}>
          <option value="daily">日调仓</option>
          <option value="weekly">周调仓</option>
        </select>
        <label className="flex items-center gap-1 cursor-pointer">
          <input type="checkbox" checked={friction} onChange={(e) => setFriction(e.target.checked)} />
          启用摩擦成本
        </label>
        <button disabled={running} onClick={() => void run()}
          className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60 transition-colors">
          {running ? '回测中…' : '运行 Top-K 回测'}
        </button>
        {result && (
          <button disabled={exporting} onClick={() => void doExport()}
            className="rounded-md border border-hair bg-surface px-3 py-1.5 text-xs hover:bg-surface-alt disabled:opacity-60 transition-colors">
            {exporting ? '导出中…' : '导出 Excel'}
          </button>
        )}
      </div>

      {exportedTip && (
        <div className="rounded-lg bg-success-bg border border-hair px-4 py-2 text-2xs text-success transition-all">
          {exportedTip}
        </div>
      )}

      {/* 核心指标 KPI 概览 */}
      {result && m && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
          {[
            { label: '年化收益', value: fmtPct(m.annual_return * 100), up: m.annual_return >= 0 },
            { label: '夏普比率', value: m.sharpe?.toFixed(2) ?? '—', up: (m.sharpe ?? 0) >= 0 },
            {
              label: '卡玛比率',
              value: m.calmar != null ? Number(m.calmar).toFixed(2)
                : (m.max_drawdown > 0 ? (m.annual_return / m.max_drawdown).toFixed(2) : '—'),
              up: m.annual_return >= 0,
            },
            { label: '最大回撤', value: m.max_drawdown != null ? `-${(m.max_drawdown * 100).toFixed(2)}%` : '—', up: false },
            { label: '年化波动率', value: fmtPct(m.annual_vol * 100) },
            { label: '调仓胜率', value: fmtPct(m.win_rate * 100) },
            { label: '成交总笔数', value: String(result.filled_trades) },
            { label: '交易天数', value: String(m.n_days) },
          ].map((k) => (
            <div key={k.label} className="rounded-lg border border-hair bg-surface px-3 py-2">
              <div className="text-3xs text-ink-muted">{k.label}</div>
              <div className={`num text-sm font-semibold ${
                k.up === undefined ? 'text-ink' : k.up ? 't-up' : 't-down'
              }`}>
                {k.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 专业图表 1 & 2：资产净值走势与水下回撤 */}
      {result && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
          <div className="flex flex-col rounded-lg border border-hair bg-surface xl:col-span-8">
            <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
              <h2 className="text-sm font-semibold text-ink">资产净值与持仓/现金动态</h2>
              <span className="text-2xs text-ink-muted">包含多资产持仓市值与现金储备时序</span>
            </div>
            <div className="p-3">
              <div ref={equityChartRef} className="h-72 w-full" />
            </div>
          </div>

          <div className="flex flex-col rounded-lg border border-hair bg-surface xl:col-span-4">
            <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
              <h2 className="text-sm font-semibold text-ink">动态水下回撤 (Underwater)</h2>
              <span className="text-2xs text-ink-muted">
                历史深潜最深 -{((result.metrics.max_drawdown ?? 0) * 100).toFixed(1)}%
              </span>
            </div>
            <div className="p-3">
              <div ref={ddChartRef} className="h-72 w-full" />
            </div>
          </div>
        </div>
      )}

      {/* A 股交易闸门拒单统计与成本口径披露 */}
      {result && (
        <div className="rounded-lg border border-hair bg-surface p-3.5 text-2xs space-y-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-ink text-xs">真实 A 股闸门与摩擦成本披露</span>
              <span className={`rounded px-1.5 py-0.5 text-3xs font-medium ${
                result.enable_friction ? 'bg-success-bg text-success' : 'bg-warning-bg text-warning'}`}>
                {result.enable_friction ? '已计入滑点与冲击成本' : '未计入摩擦成本（可能高估年化）'}
              </span>
            </div>
            <span className="text-3xs text-ink-muted">
              流动性假设：{result.liquidity?.note ?? '参与率上限 10%'}
            </span>
          </div>

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {/* 摩擦成本分项 */}
            <div className="rounded bg-surface-alt p-2.5 border border-hair">
              <div className="text-ink-muted mb-1">各摩擦成本分项减记</div>
              <div className="space-y-1">
                {Object.entries(result.friction_costs ?? {}).length > 0 ? (
                  Object.entries(result.friction_costs).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-3xs">
                      <span>{FRICTION_LABEL[k] ?? k}:</span>
                      <b className="num text-danger">{fmtNum(v)} 元</b>
                    </div>
                  ))
                ) : (
                  <div className="text-ink-muted text-3xs">暂无摩擦费用</div>
                )}
              </div>
            </div>

            {/* A 股不可成交拒单分类 */}
            <div className="rounded bg-surface-alt p-2.5 border border-hair sm:col-span-1 lg:col-span-3">
              <div className="text-ink-muted mb-1">A 股微结构拒单统计（未成交真实约束）</div>
              <div className="flex flex-wrap gap-2">
                {Object.keys(result.rejected_trades ?? {}).length > 0 ? (
                  Object.entries(result.rejected_trades).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-1 rounded bg-surface px-2 py-1 border border-hair text-3xs">
                      <span className="text-ink-secondary">{REJECT_LABEL[k] ?? k}:</span>
                      <b className="num font-semibold text-warning">{v} 次</b>
                    </div>
                  ))
                ) : (
                  <span className="text-success text-3xs">所有订单均顺利撮合成交（无涨跌停/停牌拒单）</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 最新持仓透视与调仓记录表格 */}
      {result && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
          {/* 最新截面持仓 */}
          <div className="rounded-lg border border-hair bg-surface p-3.5 xl:col-span-4 flex flex-col">
            <div className="flex items-center justify-between border-b border-hair pb-2 mb-2">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-semibold text-ink">最新截面持仓</span>
                <span className="rounded bg-surface-alt px-1.5 py-0.5 text-3xs font-mono text-ink-muted">
                  {latestHoldings.length} 只标的
                </span>
              </div>
              {latestHoldings.length > 0 && (
                <button
                  type="button"
                  onClick={handleExportUniverse}
                  className="rounded border border-brand-500/30 bg-brand-50/50 px-2 py-0.5 text-3xs font-medium text-brand-600 hover:bg-brand-50 transition-colors"
                >
                  🎯 导出至组合优化
                </button>
              )}
            </div>
            <div className="max-h-60 overflow-y-auto space-y-1">
              {latestHoldings.map((h) => (
                <div key={h.symbol} className="flex items-center justify-between rounded px-2 py-1 text-2xs hover:bg-surface-alt">
                  <span className="font-mono text-ink font-medium">{h.symbol}</span>
                  <span className="num text-ink-secondary">权重 {(h.weight * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </div>

          {/* 调仓流水明细表格 */}
          <div className="rounded-lg border border-hair bg-surface p-3.5 xl:col-span-8 flex flex-col">
            <div className="flex items-center justify-between border-b border-hair pb-2 mb-2">
              <span className="text-xs font-semibold text-ink">调仓执行明细流水</span>
              <span className="text-3xs text-ink-muted">显示最新 20 笔成交</span>
            </div>
            <div className="max-h-60 overflow-x-auto overflow-y-auto">
              <table className="quant-table w-full text-2xs">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>标的代码</th>
                    <th className="text-center">方向</th>
                    <th className="text-right">成交均价</th>
                    <th className="text-right">成交数量</th>
                    <th className="text-right">成交金额</th>
                    <th className="text-right">手续费</th>
                    <th>原因</th>
                  </tr>
                </thead>
                <tbody>
                  {(result.trades ?? []).slice(-20).reverse().map((t, idx) => (
                    <tr key={`${t.date}-${t.symbol}-${idx}`}>
                      <td className="font-mono text-ink-secondary whitespace-nowrap">{t.date}</td>
                      <td className="font-mono font-medium text-ink">{t.symbol}</td>
                      <td className="text-center">
                        <span className={`rounded px-1.5 py-0.5 text-3xs font-medium ${
                          t.side.includes('买') ? 'bg-danger-bg t-up' : 'bg-success-bg t-down'
                        }`}>
                          {t.side}
                        </span>
                      </td>
                      <td className="num text-right">{t.price.toFixed(2)}</td>
                      <td className="num text-right">{t.qty.toLocaleString()}</td>
                      <td className="num text-right">{(t.amount / 1e4).toFixed(1)}万</td>
                      <td className="num text-right">{t.cost.toFixed(1)}</td>
                      <td className="text-ink-muted truncate max-w-[100px]">{t.reason || '调仓'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
