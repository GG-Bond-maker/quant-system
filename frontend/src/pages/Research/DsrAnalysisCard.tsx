/**
 * Deflated Sharpe Ratio (DSR) 缩水夏普与多重检验抗过拟合诊断卡片。
 *
 * 理论依据：
 * David H. Bailey & Marcos López de Prado (2014)
 * "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality"
 *
 * 核心洞见：
 * 绝大多数看似亮眼的量化回测夏普比率只是在回测调参试验中由于多次尝试 (Multiple Testing)
 * 和数据窥探 (Data Snooping) 产生的“极值运气”。
 * DSR 严谨扣除了试验次数 N、非正态分布偏度 (Skewness) 与超额峰度 (Kurtosis) 后的虚假期望值，
 * 计算出在统计意义上策略具备真实非零 Alpha 的置信度 P(SR > 0)。
 */
import { useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';

/** 标准正态累积分布函数 Φ(x) */
function normCdf(x: number): number {
  const b1 = 0.31938153;
  const b2 = -0.356563782;
  const b3 = 1.781477937;
  const b4 = -1.821255978;
  const b5 = 1.330274429;
  const p = 0.2316419;
  const c = 0.39894228;

  if (x >= 0.0) {
    const t = 1.0 / (1.0 + p * x);
    return 1.0 - c * Math.exp((-x * x) / 2.0) * t * (t * (t * (t * (t * b5 + b4) + b3) + b2) + b1);
  } else {
    const t = 1.0 / (1.0 - p * x);
    return c * Math.exp((-x * x) / 2.0) * t * (t * (t * (t * (t * b5 + b4) + b3) + b2) + b1);
  }
}

export interface DsrAnalysisCardProps {
  /** 初始或回测得出的观察夏普比率 (年化)，默认 1.8 */
  initialSharpe?: number;
  /** 历史样本观测天数 T，默认 500 (约2年) */
  initialObsDays?: number;
  /** 收益率偏度 (Skewness)，A 股常为负偏 (左偏)，默认 -0.5 */
  initialSkewness?: number;
  /** 收益率超额峰度 (Excess Kurtosis)，厚尾分布常 > 0，默认 3.0 */
  initialKurtosis?: number;
}

export default function DsrAnalysisCard({
  initialSharpe = 1.8,
  initialObsDays = 500,
  initialSkewness = -0.5,
  initialKurtosis = 3.0,
}: DsrAnalysisCardProps) {
  const theme = useTheme();

  // 试验与参数输入
  const [sharpe, setSharpe] = useState<number>(initialSharpe);
  const [numTrials, setNumTrials] = useState<number>(30); // 尝试过的试验/调参组合数 N
  const [obsDays, setObsDays] = useState<number>(initialObsDays);
  const [skewness, setSkewness] = useState<number>(initialSkewness);
  const [kurtosis, setKurtosis] = useState<number>(initialKurtosis);

  // 计算 DSR
  const dsrMetrics = useMemo(() => {
    // 日频未年化夏普比率
    const srDaily = sharpe / Math.sqrt(252);
    const n = Math.max(1, numTrials);
    const t = Math.max(10, obsDays);

    // 日频夏普方差 (考虑偏度和峰度调整)
    const varSr = (1 - skewness * srDaily + ((kurtosis - 1) / 4) * Math.pow(srDaily, 2)) / (t - 1);
    const stdSr = Math.sqrt(Math.max(1e-8, varSr));

    // 由于试验了 N 次，即使真实均值为 0，产生的虚假最大夏普的期望值 (Euler-Mascheroni 常数 γ ≈ 0.5772)
    const gamma = 0.5772156649;
    const factorN = n > 1 ? Math.sqrt(2 * Math.log(n)) + gamma / Math.sqrt(2 * Math.log(n)) : 0;
    const expectedMaxSrDaily = factorN * stdSr;
    const expectedMaxSrAnnual = expectedMaxSrDaily * Math.sqrt(252);

    // 检验统计量 z 与 DSR 置信度
    const zScore = (srDaily - expectedMaxSrDaily) / stdSr;
    const dsrProb = normCdf(zScore);

    // 缩水夏普 Haircut Sharpe = max(0, 观察夏普 - 虚假期望夏普)
    const haircutSharpe = Math.max(0, sharpe - expectedMaxSrAnnual);

    // 稳健性评级
    let level: 'robust' | 'borderline' | 'overfit' = 'overfit';
    let label = '虚假过拟合 (伪 Alpha)';
    if (dsrProb >= 0.95) {
      level = 'robust';
      label = '统计稳健 (DSR ≥ 95%)';
    } else if (dsrProb >= 0.7) {
      level = 'borderline';
      label = '边际临界 (70% ~ 95%)';
    }

    return {
      srDaily,
      stdSr,
      expectedMaxSrAnnual,
      haircutSharpe,
      zScore,
      dsrProb,
      level,
      label,
    };
  }, [sharpe, numTrials, obsDays, skewness, kurtosis]);

  // ECharts: 试验次数 N 对虚假期望夏普阈值的抬升曲线
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    const trialSteps = [1, 2, 5, 10, 20, 30, 50, 80, 120, 200, 300, 500];
    const t = Math.max(10, obsDays);
    const srDaily = sharpe / Math.sqrt(252);
    const varSr = (1 - skewness * srDaily + ((kurtosis - 1) / 4) * Math.pow(srDaily, 2)) / (t - 1);
    const stdSr = Math.sqrt(Math.max(1e-8, varSr));
    const gamma = 0.5772156649;

    const thresholdCurve = trialSteps.map((n) => {
      if (n <= 1) return 0;
      const fn = Math.sqrt(2 * Math.log(n)) + gamma / Math.sqrt(2 * Math.log(n));
      return Number((fn * stdSr * Math.sqrt(252)).toFixed(3));
    });

    const observedLine = trialSteps.map(() => Number(sharpe.toFixed(3)));

    return {
      grid: { left: 40, right: 16, top: 24, bottom: 24 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(3) : '—'),
      },
      legend: {
        data: ['期望虚假夏普阈值 E[max SR]', '当前回测观察夏普'],
        top: 0,
        textStyle: { fontSize: 9, color: p.INK2 },
      },
      xAxis: {
        type: 'category',
        name: '试验数 N',
        nameTextStyle: { fontSize: 9, color: p.INKM },
        data: trialSteps.map(String),
        axisLabel: { fontSize: 8, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        name: '年化夏普',
        nameTextStyle: { fontSize: 9, color: p.INKM },
        axisLabel: { fontSize: 8, color: p.INKM },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: '期望虚假夏普阈值 E[max SR]',
          type: 'line',
          data: thresholdCurve,
          lineStyle: { width: 1.8, color: p.WARN },
          itemStyle: { color: p.WARN },
          areaStyle: { color: p.SUNKEN, opacity: 0.3 },
        },
        {
          name: '当前回测观察夏普',
          type: 'line',
          data: observedLine,
          lineStyle: { width: 2, type: 'dashed', color: p.BRAND },
          itemStyle: { color: p.BRAND },
        },
      ],
    };
  }, [sharpe, obsDays, skewness, kurtosis, theme]);

  const chartRef = useChart(option);

  return (
    <div className="rounded-lg border border-hair bg-surface p-3 space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">
            Deflated Sharpe Ratio (DSR) 缩水夏普抗过拟合体检
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-3xs font-semibold ${
              dsrMetrics.level === 'robust'
                ? 'bg-success-bg text-success'
                : dsrMetrics.level === 'borderline'
                ? 'bg-warn-bg text-warn'
                : 'bg-danger-bg text-danger'
            }`}
          >
            {dsrMetrics.label}
          </span>
        </div>
        <span className="text-3xs text-ink-muted">
          Bailey &amp; López de Prado (2014) 多重试验选择偏差校正
        </span>
      </div>

      {/* 4 指标统计卡片 */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-2xs">
        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">观察回测夏普 (Observed SR)</div>
          <div className="mt-0.5 num font-bold text-sm text-ink">{sharpe.toFixed(2)}</div>
          <div className="text-3xs text-ink-muted">未扣减试验与分布偏差</div>
        </div>
        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">虚假最大夏普期望 E[max SR]</div>
          <div className="mt-0.5 num font-bold text-sm text-warn">
            {dsrMetrics.expectedMaxSrAnnual.toFixed(2)}
          </div>
          <div className="text-3xs text-ink-muted">{numTrials} 次试验极值白噪音</div>
        </div>
        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">真实缩水夏普 (Haircut SR)</div>
          <div className="mt-0.5 num font-bold text-sm text-brand-600">
            {dsrMetrics.haircutSharpe.toFixed(2)}
          </div>
          <div className="text-3xs text-ink-muted">扣减选择偏差后的净 Alpha</div>
        </div>
        <div className="rounded bg-surface-alt p-2 border border-hair">
          <div className="text-ink-muted">DSR 置信概率 P(SR&gt;0)</div>
          <div
            className={`mt-0.5 num font-bold text-sm ${
              dsrMetrics.dsrProb >= 0.95
                ? 'text-success'
                : dsrMetrics.dsrProb >= 0.7
                ? 'text-warn'
                : 'text-danger'
            }`}
          >
            {(dsrMetrics.dsrProb * 100).toFixed(1)}%
          </div>
          <div className="text-3xs text-ink-muted">
            {dsrMetrics.dsrProb >= 0.95 ? '通过 95% 显著性检验' : '未通过 95% 检验门槛'}
          </div>
        </div>
      </div>

      {/* 调节交互参数 */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 rounded bg-surface-alt p-2 text-2xs">
        <div>
          <span className="text-3xs text-ink-muted">观察夏普:</span>
          <input
            type="number"
            step="0.1"
            min="0"
            max="10"
            value={sharpe}
            onChange={(e) => setSharpe(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
        <div>
          <span className="text-3xs text-ink-muted">调参尝试次数 N:</span>
          <input
            type="number"
            step="5"
            min="1"
            max="1000"
            value={numTrials}
            onChange={(e) => setNumTrials(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
        <div>
          <span className="text-3xs text-ink-muted">回测样本天数 T:</span>
          <input
            type="number"
            step="50"
            min="60"
            max="3000"
            value={obsDays}
            onChange={(e) => setObsDays(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
        <div>
          <span className="text-3xs text-ink-muted">收益偏度 Skew:</span>
          <input
            type="number"
            step="0.1"
            min="-3"
            max="3"
            value={skewness}
            onChange={(e) => setSkewness(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
        <div>
          <span className="text-3xs text-ink-muted">超额峰度 Kurt:</span>
          <input
            type="number"
            step="0.5"
            min="0"
            max="15"
            value={kurtosis}
            onChange={(e) => setKurtosis(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-xs outline-none focus:border-brand-300"
          />
        </div>
      </div>

      {/* 极值漂移折线图 */}
      <div>
        <div className="flex items-center justify-between text-2xs text-ink-secondary mb-1">
          <span className="font-medium">
            多次试验下期望虚假夏普阈值 E[max SR] 抬升曲线
          </span>
          <span className="text-3xs text-ink-muted">
            虚假夏普随试验次数呈对数级膨胀，观察夏普必须在虚线之上方为真实 Alpha
          </span>
        </div>
        <div ref={chartRef} className="h-40 w-full" />
      </div>

      <p className="text-3xs text-ink-muted leading-tight">
        💡 <b>量化铁律</b>：在回测过程中如果尝试了 30~50 组因子组合或超参，即便底层毫无真实超额，在极值效应下也必然能随机搜出 1.5 以上的高夏普曲线。DSR 强制要求回测结果超越该随机上限。
      </p>
    </div>
  );
}
