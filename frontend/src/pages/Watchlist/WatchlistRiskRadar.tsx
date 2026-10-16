/**
 * 自选池组合即时风险透视看板（Watchlist Portfolio Risk Radar）。
 *
 * 核心功能架构：
 * 1. 多资产风险度量：年化波动率、基准 Beta、分散化倍数 (DR)、最大回撤 (MDD)、VaR/CVaR；
 * 2. Barra 风格因子特征画像雷达：规模 (Size)、价值 (Value)、动量 (Momentum)、稳健 (Vol)、质量 (Quality)、流动性 (Liquidity)、弹性 (Beta) 7 维全景图与智能诊断；
 * 3. 动态多权重模拟器与微型组合优化：等权配置 (Equal)、逆波动率风险平价 (Inv-Vol)、市值代理加权 (Cap) 收益风险与夏普比即时推演；
 * 4. 申万/行业暴露堆叠与黑天鹅极端情景压力测试 (Stress Testing)。
 *
 * 全量消费后端 POST /api/v1/watchlist/risk-summary 真实计算，杜绝伪造数据。
 */
import { useEffect, useMemo, useState } from 'react';
import { watchlistApi } from '@/api/watchlist';
import type {
  WatchlistRiskResponse,
  WatchlistRiskSummaryData,
  WeightMetricComparison,
} from '@/types/watchlist';
import { fmtPct, pctClass } from '@/utils/format';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';

const WINDOW_OPTIONS = [
  { label: '60日', value: 60 },
  { label: '120日', value: 120 },
  { label: '250日', value: 250 },
] as const;

const WEIGHT_OPTIONS: Array<{ label: string; value: 'equal' | 'inv_vol' | 'cap'; tip: string }> = [
  { label: '等权配置', value: 'equal', tip: '基准方案：各标的平权 w = 1/N' },
  { label: '逆波动率 (风险平价)', value: 'inv_vol', tip: '简配风险平价：低波增配，高波降权' },
  { label: '市值加权', value: 'cap', tip: '规模代理加权：大盘重仓，小盘轻仓' },
];

// 行业调色板
const SECTOR_COLORS = CATEGORY_COLORS.PIE;

interface WatchlistRiskRadarProps {
  /** 当前选中的标的列表（若未选则使用当前分组全量标的） */
  symbols: string[];
  /** 标的显示总数 */
  totalSymbolsCount: number;
}

export function WatchlistRiskRadar({ symbols, totalSymbolsCount }: WatchlistRiskRadarProps) {
  const theme = useTheme();
  const [windowDays, setWindowDays] = useState<number>(120);
  const [weightsMode, setWeightsMode] = useState<'equal' | 'inv_vol' | 'cap'>('equal');
  const [data, setData] = useState<WatchlistRiskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [showConstituents, setShowConstituents] = useState(false);
  const [showStressTest, setShowStressTest] = useState(false);

  useEffect(() => {
    if (!symbols.length) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    watchlistApi
      .riskSummary(symbols, windowDays, weightsMode)
      .then((res) => {
        if (!cancelled) {
          setData(res);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.message || '组合风险数据计算失败');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbols, windowDays, weightsMode]);

  const summary: WatchlistRiskSummaryData | null = data?.summary ?? null;
  const breakdown = data?.industry_breakdown ?? [];
  const assets = data?.assets ?? [];
  const radarData = summary?.factor_radar;
  const weightsComp = summary?.weights_comparison;

  // Barra 7 维风格因子雷达图 Option
  const radarOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!radarData) return null;
    const p = chartPalette();

    const indicators = [
      { name: '规模 Size', max: 100 },
      { name: '价值 Value', max: 100 },
      { name: '动量 Mom', max: 100 },
      { name: '稳健 Vol', max: 100 },
      { name: '质量 Qlt', max: 100 },
      { name: '流动 Liq', max: 100 },
      { name: '弹性 Beta', max: 100 },
    ];

    const vals = [
      radarData.size,
      radarData.value,
      radarData.momentum,
      radarData.volatility,
      radarData.quality,
      radarData.liquidity,
      radarData.beta,
    ];

    return {
      tooltip: {
        trigger: 'item',
        formatter: () => {
          return `<div class="font-medium mb-1">Barra 风格因子特征得分</div>
            <div class="text-2xs space-y-0.5">
              <div>规模 (Size): <b>${radarData.size}</b></div>
              <div>价值 (Value): <b>${radarData.value}</b></div>
              <div>动量 (Momentum): <b>${radarData.momentum}</b></div>
              <div>稳健低波 (Low Vol): <b>${radarData.volatility}</b></div>
              <div>质量防御 (Quality): <b>${radarData.quality}</b></div>
              <div>流动性 (Liquidity): <b>${radarData.liquidity}</b></div>
              <div>基准弹性 (Beta): <b>${radarData.beta}</b></div>
            </div>`;
        },
      },
      radar: {
        indicator: indicators,
        shape: 'polygon',
        splitNumber: 4,
        axisName: {
          color: p.INK,
          fontSize: 10,
          fontWeight: 500,
        },
        splitLine: {
          lineStyle: {
            color: p.SUNKEN,
          },
        },
        splitArea: {
          show: true,
          areaStyle: {
            color: ['rgba(250,250,250,0.3)', 'rgba(230,230,230,0.1)'],
          },
        },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: vals,
              name: '自选池风格得分',
              areaStyle: {
                color: withAlpha(p.BRAND, 0.28),
              },
              lineStyle: {
                color: p.BRAND,
                width: 2,
              },
              itemStyle: {
                color: p.BRAND,
              },
            },
          ],
        },
      ],
    };
  }, [radarData, theme]);

  const radarChartRef = useChart(radarOption);

  if (!symbols.length) {
    return null;
  }

  // Beta 风格分类
  const betaBadge = (b: number | null) => {
    if (b == null) return null;
    if (b < 0.85) return <span className="rounded bg-blue-50 px-1.5 py-0.5 text-2xs font-medium text-blue-600 border border-blue-200">防守型 (低Beta)</span>;
    if (b > 1.15) return <span className="rounded bg-rose-50 px-1.5 py-0.5 text-2xs font-medium text-rose-600 border border-rose-200">进取型 (高Beta)</span>;
    return <span className="rounded bg-slate-50 px-1.5 py-0.5 text-2xs font-medium text-ink-secondary border border-slate-200">均衡型</span>;
  };

  // 分散化倍数评级
  const drBadge = (dr: number | null) => {
    if (dr == null) return null;
    if (dr >= 1.35) return <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-2xs font-medium text-emerald-600 border border-emerald-200">高分散红利</span>;
    return <span className="rounded bg-amber-50 px-1.5 py-0.5 text-2xs font-medium text-amber-600 border border-amber-200">一般分散</span>;
  };

  const assetTypeBadge = (type: string) => {
    switch (type) {
      case 'etf':
        return <span className="rounded bg-blue-50 px-1 py-0.2 text-2xs font-medium text-blue-600 border border-blue-200">ETF</span>;
      case 'futures':
        return <span className="rounded bg-amber-50 px-1 py-0.2 text-2xs font-medium text-amber-700 border border-amber-200">期货</span>;
      case 'hk':
        return <span className="rounded bg-indigo-50 px-1 py-0.2 text-2xs font-medium text-indigo-600 border border-indigo-200">港股</span>;
      default:
        return <span className="rounded bg-slate-50 px-1 py-0.2 text-2xs font-medium text-ink-muted border border-slate-200">A股</span>;
    }
  };

  return (
    <div className="rounded-lg border border-hair bg-white transition-all shadow-xs">
      {/* 标题栏与控制选项 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded bg-brand-50 text-brand-600">
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
          </div>
          <span className="text-xs font-semibold text-ink">自选池组合即时风险透视</span>
          <span className="text-2xs text-ink-muted">
            （基于当前 {symbols.length} 只标的 · 池容量 {totalSymbolsCount} 只 · 对齐沪深300基准）
          </span>
          {loading && <span className="text-2xs text-brand-500 animate-pulse">计算中...</span>}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* 权重分配方案切换 */}
          <div className="inline-flex rounded-md border border-hair p-0.5 bg-slate-50">
            {WEIGHT_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setWeightsMode(opt.value)}
                title={opt.tip}
                className={`rounded px-2 py-0.5 text-2xs font-medium transition-colors ${
                  weightsMode === opt.value
                    ? 'bg-white text-brand-600 shadow-2xs font-semibold'
                    : 'text-ink-muted hover:text-ink'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* 回看窗口切换 */}
          <div className="inline-flex rounded-md border border-hair p-0.5 bg-slate-50">
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setWindowDays(opt.value)}
                className={`rounded px-2 py-0.5 text-2xs font-medium transition-colors ${
                  windowDays === opt.value
                    ? 'bg-white text-brand-600 shadow-2xs font-semibold'
                    : 'text-ink-muted hover:text-ink'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* 折叠切换 */}
          <button
            onClick={() => setExpanded(!expanded)}
            className="rounded p-1 text-ink-muted hover:bg-slate-100 hover:text-ink transition-colors"
            title={expanded ? '收起透视面板' : '展开透视面板'}
          >
            <svg
              viewBox="0 0 24 24"
              className={`h-4 w-4 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
        </div>
      </div>

      {expanded && (
        <div className="p-4 space-y-3.5">
          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
              {error}
            </div>
          )}

          {data?.status === 'degraded' && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              数据降级：{data.reason || '部分标的历史日线样本不足'}。仅对有效标的执行统计。
            </div>
          )}

          {/* 6 维核心量化指标卡片 */}
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
            {/* 1. 年化波动率 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>组合年化波动率</span>
                <span className="text-ink-muted">σ</span>
              </div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {summary?.portfolio_ann_vol != null ? `${summary.portfolio_ann_vol.toFixed(2)}%` : '—'}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                {summary?.window_days ? `最近 ${summary.window_days} 交易日` : '—'}
              </div>
            </div>

            {/* 2. 基准 Beta */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>基准 Beta (沪深300)</span>
                <span className="text-ink-muted">β</span>
              </div>
              <div className="num mt-1 flex items-baseline gap-1.5 text-base font-semibold text-ink">
                <span>{summary?.portfolio_beta != null ? summary.portfolio_beta.toFixed(2) : '—'}</span>
                {betaBadge(summary?.portfolio_beta ?? null)}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                相关系数: {summary?.benchmark_corr != null ? summary.benchmark_corr.toFixed(2) : '—'}
              </div>
            </div>

            {/* 3. 分散化倍数 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>资产分散倍数 (DR)</span>
                <span className="text-ink-muted">Σwσ / σp</span>
              </div>
              <div className="num mt-1 flex items-baseline gap-1.5 text-base font-semibold text-ink">
                <span>{summary?.diversification_ratio != null ? `${summary.diversification_ratio.toFixed(2)}x` : '—'}</span>
                {drBadge(summary?.diversification_ratio ?? null)}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                有效资产: {summary?.valid_count ?? 0} / {summary?.asset_count ?? symbols.length}
              </div>
            </div>

            {/* 4. 区间最大回撤 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>区间最大回撤 (MDD)</span>
                <span className="text-ink-muted">Peak-T</span>
              </div>
              <div className="num mt-1 text-base font-semibold text-rose-600">
                {summary?.max_drawdown != null ? `-${summary.max_drawdown.toFixed(2)}%` : '—'}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                区间: {summary?.start_date ? `${summary.start_date.slice(5)} ~ ${summary.end_date?.slice(5)}` : '—'}
              </div>
            </div>

            {/* 5. 行业集中度与有效行业数 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>行业集中度</span>
                <span className="text-ink-muted">HHI</span>
              </div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {summary?.top_industry_ratio != null ? `${summary.top_industry_ratio.toFixed(1)}%` : '—'}
              </div>
              <div className="mt-0.5 truncate text-2xs text-ink-muted" title={summary?.top_industry ?? ''}>
                首要: {summary?.top_industry || '—'} · {summary?.effective_sectors ? `${summary.effective_sectors}有效行业` : ''}
              </div>
            </div>

            {/* 6. 95% 在险价值与尾部亏损 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>95% 在险价值 (VaR)</span>
                <span className="text-ink-muted">CVaR</span>
              </div>
              <div className="num mt-1 text-base font-semibold text-amber-600">
                {summary?.var_95 != null ? `-${summary.var_95.toFixed(2)}%` : '—'}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                尾部预期 (CVaR): {summary?.cvar_95 != null ? `-${summary.cvar_95.toFixed(2)}%` : '—'}
              </div>
            </div>
          </div>

          {/* 阶段三 & 阶段四：Barra 7 维风格因子雷达与多权重模拟器双栏布局 */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
            {/* 左侧：Barra 风格画像雷达图 */}
            <div className="rounded-md border border-hair p-3 bg-slate-50/30 lg:col-span-5 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between pb-2 border-b border-hair">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-ink">Barra 风格因子特征画像</span>
                    <span className="rounded bg-brand-50 px-1 py-0.5 text-3xs font-medium text-brand-600 border border-brand-200">
                      7维雷达
                    </span>
                  </div>
                  <span className="text-2xs text-ink-muted">标准化得分 (20~85)</span>
                </div>
                {radarOption ? (
                  <div className="relative w-full py-1">
                    <div ref={radarChartRef} style={{ height: 210 }} className="w-full" />
                  </div>
                ) : (
                  <div className="py-12 text-center text-2xs text-ink-muted">
                    {loading ? '正在求解风格因子暴露…' : '样本数量不足，暂无雷达画像'}
                  </div>
                )}
              </div>

              {/* 智能诊断建议条 */}
              {radarData?.diagnosis && (
                <div className="mt-2 rounded bg-brand-50/70 border border-brand-100 p-2 text-2xs text-brand-800 leading-relaxed">
                  <div className="font-semibold flex items-center gap-1 text-brand-700 mb-0.5">
                    <span>💡</span>
                    <span>组合风格诊断：</span>
                  </div>
                  <span>{radarData.diagnosis}</span>
                </div>
              )}
            </div>

            {/* 右侧：多权重模拟器矩阵 & 行业暴露 */}
            <div className="rounded-md border border-hair p-3 bg-slate-50/30 lg:col-span-7 flex flex-col justify-between gap-3">
              {/* 多权重模拟矩阵 */}
              <div>
                <div className="flex items-center justify-between pb-2 border-b border-hair">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-ink">动态多权重模拟对比</span>
                    <span className="text-2xs text-ink-muted">（等权 vs 逆波动率风险平价 vs 市值加权）</span>
                  </div>
                  <span className="text-2xs font-medium text-brand-600">
                    当前生效: {WEIGHT_OPTIONS.find((w) => w.value === weightsMode)?.label}
                  </span>
                </div>

                {weightsComp ? (
                  <div className="mt-2 overflow-x-auto rounded border border-hair bg-white">
                    <table className="quant-table w-full text-2xs">
                      <thead>
                        <tr>
                          <th>权重分配方案</th>
                          <th className="text-right">年化收益率</th>
                          <th className="text-right">年化波动率</th>
                          <th className="text-right">夏普比率</th>
                          <th className="text-right">最大回撤</th>
                          <th className="text-right">分散倍数</th>
                          <th className="text-right">组合Beta</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(['equal', 'inv_vol', 'cap'] as const).map((mKey) => {
                          const item: WeightMetricComparison | undefined = weightsComp[mKey];
                          if (!item) return null;
                          const isCur = weightsMode === mKey;
                          const label = mKey === 'equal' ? '等权配置 (1/N)' : mKey === 'inv_vol' ? '逆波动率 (风险平价)' : '市值代理加权';
                          return (
                            <tr
                              key={mKey}
                              onClick={() => setWeightsMode(mKey)}
                              className={`cursor-pointer transition-colors ${isCur ? 'bg-brand-50/80 font-medium' : 'hover:bg-slate-50'}`}
                            >
                              <td className="text-ink">
                                <div className="flex items-center gap-1.5">
                                  <span className={`h-1.5 w-1.5 rounded-full ${isCur ? 'bg-brand-600' : 'bg-slate-300'}`} />
                                  <span>{label}</span>
                                </div>
                              </td>
                              <td className={`num text-right ${pctClass(item.ann_return)}`}>
                                {fmtPct(item.ann_return)}
                              </td>
                              <td className="num text-right text-ink">{item.ann_vol.toFixed(2)}%</td>
                              <td className={`num text-right font-semibold ${item.sharpe >= 1.0 ? 'text-emerald-600' : item.sharpe < 0 ? 'text-red-500' : 'text-ink'}`}>
                                {item.sharpe.toFixed(2)}
                              </td>
                              <td className="num text-right text-rose-500">-{item.mdd.toFixed(2)}%</td>
                              <td className="num text-right text-ink-secondary">{item.dr.toFixed(2)}x</td>
                              <td className="num text-right text-ink-secondary">{item.beta.toFixed(2)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="py-6 text-center text-2xs text-ink-muted">计算中…</div>
                )}
              </div>

              {/* 行业分布堆叠进度条 */}
              {breakdown.length > 0 && (
                <div className="space-y-1.5 pt-1">
                  <div className="flex items-center justify-between text-2xs text-ink-secondary">
                    <span className="font-medium">自选池行业暴露构成</span>
                    <span>共覆盖 {breakdown.length} 个行业板块</span>
                  </div>
                  {/* 堆叠色彩长条 */}
                  <div className="flex h-2.5 w-full overflow-hidden rounded bg-slate-100">
                    {breakdown.map((item, idx) => (
                      <div
                        key={item.industry}
                        style={{
                          width: `${item.weight}%`,
                          backgroundColor: SECTOR_COLORS[idx % SECTOR_COLORS.length],
                        }}
                        title={`${item.industry}: ${item.weight}% (${item.count}只)`}
                        className="h-full transition-all duration-300 first:rounded-l last:rounded-r"
                      />
                    ))}
                  </div>
                  {/* 行业标签流 */}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-0.5 text-2xs">
                    {breakdown.map((item, idx) => (
                      <div key={item.industry} className="flex items-center gap-1.5">
                        <span
                          className="h-2 w-2 rounded-full shrink-0"
                          style={{ backgroundColor: SECTOR_COLORS[idx % SECTOR_COLORS.length] }}
                        />
                        <span className="text-ink">{item.industry}</span>
                        <span className="num font-semibold text-ink-secondary">{item.weight}%</span>
                        <span className="text-ink-muted">({item.count}只)</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 标的风险明细与黑天鹅压力测试展开开关 */}
          {assets.length > 0 && (
            <div className="pt-0.5">
              <div className="flex flex-wrap items-center gap-4">
                <button
                  onClick={() => setShowConstituents(!showConstituents)}
                  className="flex items-center gap-1 text-2xs font-medium text-brand-600 hover:text-brand-700"
                >
                  <span>{showConstituents ? '收起各标的风险贡献明细' : '展开各标的风险贡献明细（波动率 / Beta / 回撤）'}</span>
                  <svg
                    viewBox="0 0 24 24"
                    className={`h-3 w-3 transition-transform ${showConstituents ? 'rotate-180' : ''}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>

                <button
                  onClick={() => setShowStressTest(!showStressTest)}
                  className="flex items-center gap-1 text-2xs font-medium text-brand-600 hover:text-brand-700"
                >
                  <span>🛡️ {showStressTest ? '收起黑天鹅压力测试' : '黑天鹅极端情景压力测试 (Stress Testing)'}</span>
                  <svg
                    viewBox="0 0 24 24"
                    className={`h-3 w-3 transition-transform ${showStressTest ? 'rotate-180' : ''}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
              </div>

              {/* 极端情景压力测试推演面板 */}
              {showStressTest && summary && (
                <div className="mt-2 rounded-md border border-hair bg-surface-alt/60 p-3 space-y-2 text-2xs">
                  <div className="flex items-center justify-between font-medium text-ink">
                    <span>宏观极端冲击压力测试推演 (MSCI Barra 敏感度模型)</span>
                    <span className="text-ink-muted">当前组合 Beta: {summary.portfolio_beta != null ? summary.portfolio_beta.toFixed(2) : '1.00'}</span>
                  </div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                    {/* 情景 1 */}
                    <div className="rounded border border-down/30 bg-down-bg/40 p-2 space-y-1">
                      <div className="flex justify-between font-semibold text-down">
                        <span>情景1: 市场系统性急跌</span>
                        <span>基准 -5.0%</span>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-ink-secondary">预计净值冲击:</span>
                        <span className="font-mono font-bold text-down">
                          {summary.portfolio_beta != null ? `-${(summary.portfolio_beta * 5.0).toFixed(2)}%` : '—'}
                        </span>
                      </div>
                      <div className="text-ink-muted leading-relaxed">
                        {summary.portfolio_beta && summary.portfolio_beta > 1.15
                          ? '组合高弹性，基准急跌时放大下行损失'
                          : '组合弹性可控，具有较好缓冲保护'}
                      </div>
                    </div>

                    {/* 情景 2 */}
                    <div className="rounded border border-warn/30 bg-warn-bg/40 p-2 space-y-1">
                      <div className="flex justify-between font-semibold text-warn">
                        <span>情景2: 重仓行业黑天鹅</span>
                        <span>单行业 -10.0%</span>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-ink-secondary">预计净值冲击:</span>
                        <span className="font-mono font-bold text-warn">
                          {summary.top_industry_ratio != null ? `-${((summary.top_industry_ratio * 10.0) / 100).toFixed(2)}%` : '—'}
                        </span>
                      </div>
                      <div className="text-ink-muted leading-relaxed">
                        首要行业 [{summary.top_industry || '主要行业'}] 占比 {summary.top_industry_ratio?.toFixed(1) ?? '—'}%
                      </div>
                    </div>

                    {/* 情景 3 */}
                    <div className="rounded border border-hair bg-surface-sunken/60 p-2 space-y-1">
                      <div className="flex justify-between font-semibold text-ink">
                        <span>情景3: 流动性紧缩冲击</span>
                        <span>波动率 +40%</span>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-ink-secondary">年化波动率预计升至:</span>
                        <span className="font-mono font-bold text-ink">
                          {summary.portfolio_ann_vol != null ? `${(summary.portfolio_ann_vol * 1.4).toFixed(1)}%` : '—'}
                        </span>
                      </div>
                      <div className="text-ink-muted leading-relaxed">
                        分散化倍数 {summary.diversification_ratio?.toFixed(2) ?? '1.0'}x，
                        {summary.diversification_ratio && summary.diversification_ratio >= 1.3 ? '分散充分有效' : '持仓相关度偏高'}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {showConstituents && (
                <div className="mt-2 overflow-x-auto rounded border border-hair">
                  <table className="quant-table w-full text-2xs">
                    <thead>
                      <tr>
                        <th>代码</th>
                        <th>名称</th>
                        <th>品类</th>
                        <th>行业分类</th>
                        <th className="text-right">方案占比 ({weightsMode === 'equal' ? '等权' : weightsMode === 'inv_vol' ? '逆波' : '市值'})</th>
                        <th className="text-right">年化波动率</th>
                        <th className="text-right">基准Beta</th>
                        <th className="text-right">区间最大回撤</th>
                        <th className="text-right">区间收益率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {assets.map((ast) => {
                        const curWeight = ast.weights?.[weightsMode] ?? ast.weight;
                        return (
                          <tr key={ast.symbol}>
                            <td className="num font-medium text-ink">{ast.code}</td>
                            <td className="text-ink-secondary">{ast.name}</td>
                            <td>{assetTypeBadge(ast.type)}</td>
                            <td>
                              <span className="rounded bg-slate-100 px-1 py-0.5 text-ink-muted">
                                {ast.industry}
                              </span>
                            </td>
                            <td className="num text-right font-medium text-ink">{curWeight.toFixed(1)}%</td>
                            <td className="num text-right font-medium text-ink">
                              {ast.ann_vol != null ? `${ast.ann_vol.toFixed(2)}%` : '—'}
                            </td>
                            <td className="num text-right text-ink">
                              {ast.beta != null ? ast.beta.toFixed(2) : '—'}
                            </td>
                            <td className="num text-right text-rose-500">
                              {ast.mdd != null ? `-${ast.mdd.toFixed(2)}%` : '—'}
                            </td>
                            <td className={`num text-right font-medium ${pctClass(ast.period_return)}`}>
                              {ast.period_return != null ? fmtPct(ast.period_return) : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
