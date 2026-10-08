/**
 * 策略交易费率与换手率敏感性分析 (Turnover & Cost Sensitivity Frontier)。
 *
 * 理论与实战背景：
 * 在量化策略（尤其是高换手多因子、动量轮动或短周期机器学习模型）中，
 * “纸面富贵”的年化超额收益往往在实盘中被印花税、经手费、券商佣金与冲击滑点侵蚀殆尽。
 * 本组件测算策略在不同单边交易摩擦成本（Cost bps）和不同换手率下的收益衰减边界，
 * 并求解策略的盈亏平衡单边摩擦上限（Break-even Cost Bps）与最大可持续换手率。
 */
import { useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';

export interface CostSensitivityCardProps {
  /** 初始年化毛收益率 (Gross Return %)，默认 22% */
  initialGrossReturn?: number;
  /** 初始年化单边换手率 (Turnover 倍数)，默认 18 倍 */
  initialTurnover?: number;
  /** 预设基准收益率 (Benchmark Return %)，默认 4% */
  initialBenchmarkReturn?: number;
}

export default function CostSensitivityCard({
  initialGrossReturn = 22,
  initialTurnover = 18,
  initialBenchmarkReturn = 4,
}: CostSensitivityCardProps) {
  const theme = useTheme();

  const [grossReturn, setGrossReturn] = useState<number>(initialGrossReturn);
  const [turnover, setTurnover] = useState<number>(initialTurnover);
  const [benchmarkReturn, setBenchmarkReturn] = useState<number>(initialBenchmarkReturn);

  // 费率敏感性计算
  const costAnalysis = useMemo(() => {
    const gross = grossReturn / 100;
    const bench = benchmarkReturn / 100;
    const grossAlpha = gross - bench;
    const to = Math.max(0.1, turnover);

    // 盈亏平衡单边成本 (以绝对收益为 0 的边界): gross = 2 * to * cost
    const breakEvenAbsoluteBps = (gross / (2 * to)) * 10000;

    // 盈亏平衡单边成本 (以超额收益为 0 的边界): grossAlpha = 2 * to * cost
    const breakEvenAlphaBps = (grossAlpha / (2 * to)) * 10000;

    // 在标准 A 股典型综合摩擦 (单边 10 bps: 5bps印花税 + 1.5bps佣金规费 + 3.5bps滑点) 下的年化交易成本
    const standardCostBps = 10;
    const annualFrictionLoss = 2 * to * (standardCostBps / 10000);
    const netReturnAt10bps = gross - annualFrictionLoss;
    const netAlphaAt10bps = netReturnAt10bps - bench;

    // 最大可持续换手率 (在 10 bps 摩擦下保持正 Alpha 的最大换手)
    const maxSustainableTurnover = (grossAlpha / (2 * (standardCostBps / 10000)));

    // 风险评级
    let level: 'robust' | 'moderate' | 'danger' = 'moderate';
    let note = '中等交易摩擦耐受力';
    if (breakEvenAlphaBps < 8) {
      level = 'danger';
      note = '高危：保本成本 < 8 bps，A 股最低刚性税费将抹平超额！';
    } else if (breakEvenAlphaBps >= 20) {
      level = 'robust';
      note = '强健：保本成本 ≥ 20 bps，对实盘滑点与流动性冲击具备充足缓冲。';
    }

    return {
      grossAlpha,
      breakEvenAbsoluteBps,
      breakEvenAlphaBps,
      annualFrictionLoss,
      netReturnAt10bps,
      netAlphaAt10bps,
      maxSustainableTurnover,
      level,
      note,
    };
  }, [grossReturn, turnover, benchmarkReturn]);

  // ECharts: 费率阶梯 (0 ~ 30 bps) 下的绝对收益与超额收益衰减折线
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    const bpsLadder = [0, 2, 5, 8, 10, 12, 15, 18, 20, 25, 30];
    const gross = grossReturn;
    const bench = benchmarkReturn;
    const to = turnover;

    const netReturns = bpsLadder.map((bps) => {
      const friction = 2 * to * (bps / 10000) * 100;
      return Number((gross - friction).toFixed(2));
    });

    const netAlphas = bpsLadder.map((bps) => {
      const friction = 2 * to * (bps / 10000) * 100;
      return Number((gross - bench - friction).toFixed(2));
    });

    return {
      grid: { left: 44, right: 16, top: 24, bottom: 24 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? `${v.toFixed(2)}%` : '—'),
      },
      legend: {
        data: ['扣费后年化净收益', '扣费后年化超额 (Alpha)', '基准收益率'],
        top: 0,
        textStyle: { fontSize: 9, color: p.INK2 },
      },
      xAxis: {
        type: 'category',
        name: '单边摩擦 (bps)',
        nameTextStyle: { fontSize: 8, color: p.INKM },
        data: bpsLadder.map((b) => `${b} bps`),
        axisLabel: { fontSize: 8, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        name: '收益率 (%)',
        nameTextStyle: { fontSize: 8, color: p.INKM },
        axisLabel: {
          fontSize: 8,
          color: p.INKM,
          formatter: '{value}%',
        },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: '扣费后年化净收益',
          type: 'line',
          data: netReturns,
          lineStyle: { width: 2, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          markLine: {
            symbol: 'none',
            data: [
              {
                yAxis: 0,
                lineStyle: { color: p.DOWN, type: 'dashed' },
                label: { formatter: '绝对保本线 (0%)', fontSize: 8 },
              },
            ],
          },
        },
        {
          name: '扣费后年化超额 (Alpha)',
          type: 'line',
          data: netAlphas,
          lineStyle: { width: 1.8, color: p.WARN },
          itemStyle: { color: p.WARN },
        },
        {
          name: '基准收益率',
          type: 'line',
          data: bpsLadder.map(() => bench),
          lineStyle: { width: 1.2, type: 'dotted', color: p.INKM },
          itemStyle: { color: p.INKM },
          showSymbol: false,
        },
      ],
    };
  }, [grossReturn, turnover, benchmarkReturn, theme]);

  const chartRef = useChart(option);

  return (
    <div className="rounded-lg border border-hair bg-surface p-3 space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">
            交易费用与换手率敏感性分析 (Cost Frontier)
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-3xs font-semibold ${
              costAnalysis.level === 'robust'
                ? 'bg-success-bg text-success'
                : costAnalysis.level === 'moderate'
                ? 'bg-warn-bg text-warn'
                : 'bg-danger-bg text-danger'
            }`}
          >
            {costAnalysis.level === 'robust'
              ? '高摩擦容忍度'
              : costAnalysis.level === 'moderate'
              ? '中等容忍度'
              : '摩擦脆弱型'}
          </span>
        </div>
        <span className="text-3xs text-ink-muted">
          双边摩擦损耗 = 2 × 年换手 × 单边费率
        </span>
      </div>

      {/* 核心指标四宫格 */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-2xs">
        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">超额保本成本上限 (Alpha Break-even)</div>
          <div
            className={`mt-0.5 num font-bold text-sm ${
              costAnalysis.breakEvenAlphaBps >= 15
                ? 'text-success'
                : costAnalysis.breakEvenAlphaBps >= 8
                ? 'text-warn'
                : 'text-danger'
            }`}
          >
            {costAnalysis.breakEvenAlphaBps.toFixed(1)} bps
          </div>
          <div className="text-3xs text-ink-muted">
            单边成本超此值时超额归零
          </div>
        </div>

        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">10bps基准下单边扣费损耗</div>
          <div className="mt-0.5 num font-bold text-sm text-danger">
            -{(costAnalysis.annualFrictionLoss * 100).toFixed(2)}%
          </div>
          <div className="text-3xs text-ink-muted">年化总侵蚀利润</div>
        </div>

        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">10bps摩擦下真实净超额 (Net Alpha)</div>
          <div
            className={`mt-0.5 num font-bold text-sm ${
              costAnalysis.netAlphaAt10bps >= 0 ? 'text-brand-600' : 'text-danger'
            }`}
          >
            {(costAnalysis.netAlphaAt10bps * 100).toFixed(2)}%
          </div>
          <div className="text-3xs text-ink-muted">扣除双边税费与滑点</div>
        </div>

        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">最大可持续年换手 (10bps成本下)</div>
          <div className="mt-0.5 num font-bold text-sm text-ink">
            {costAnalysis.maxSustainableTurnover.toFixed(1)} 倍
          </div>
          <div className="text-3xs text-ink-muted">当前设定: {turnover} 倍</div>
        </div>
      </div>

      {/* 交互输入 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 rounded bg-surface-alt p-2 text-2xs">
        <div>
          <span className="text-3xs text-ink-muted">回测年化毛收益 (Gross Return %):</span>
          <input
            type="number"
            step="1"
            min="-50"
            max="150"
            value={grossReturn}
            onChange={(e) => setGrossReturn(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
        <div>
          <span className="text-3xs text-ink-muted">年化单边换手率 (Turnover 倍数):</span>
          <input
            type="number"
            step="1"
            min="0.5"
            max="200"
            value={turnover}
            onChange={(e) => setTurnover(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
        <div>
          <span className="text-3xs text-ink-muted">基准年化收益 (Benchmark Return %):</span>
          <input
            type="number"
            step="1"
            min="-50"
            max="100"
            value={benchmarkReturn}
            onChange={(e) => setBenchmarkReturn(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
      </div>

      {/* 收益衰减折线图 */}
      <div>
        <div className="flex items-center justify-between text-2xs text-ink-secondary mb-1">
          <span className="font-medium">单边摩擦成本爬升下的净收益衰减直线</span>
          <span className="text-3xs text-ink-muted">
            交点越靠右表明策略抗交易摩擦能力越强
          </span>
        </div>
        <div ref={chartRef} className="h-44 w-full" />
      </div>

      <p className="text-3xs text-ink-muted leading-tight">
        📌 <b>诊断结论</b>：{costAnalysis.note}
      </p>
    </div>
  );
}
