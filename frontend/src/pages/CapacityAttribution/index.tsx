/** 策略容量与业绩归因中心：ADV 容量模型 + Brinson 行业归因 + 风格回归。 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';

import { ApiError } from '@/api/client';
import { deskApi, type AttributionResult } from '@/api/production';
import { SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useChart } from '@/utils/useChart';

const inputCls =
  'w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';
const DEFAULT_ASSETS = [
  { code: '000001.SZ', weight: 0.5 },
  { code: '300750.SZ', weight: 0.5 },
];
const WINDOW_OPTIONS = [30, 60, 120, 250];
type BenchmarkType = 'universe_equal' | 'custom_portfolio' | 'single_symbol';
const BENCHMARK_LABELS: Record<BenchmarkType, string> = {
  universe_equal: 'universe 等权',
  custom_portfolio: '自定义组合',
  single_symbol: '单一标的',
};

export default function CapacityAttribution() {
  const [capacity, setCapacity] = useState<{ aum_yi: number | null; formula: string } | null>(null);
  const [assets, setAssets] = useState(DEFAULT_ASSETS);
  const [attr, setAttr] = useState<AttributionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState(120);
  // 容量假设参数（P1-9）：与后端 /desk/capacity 默认值一致
  const [participationCap, setParticipationCap] = useState(0.01);
  const [holdings, setHoldings] = useState(20);
  const [rebalancePerYear, setRebalancePerYear] = useState(12);
  // 基准三选一（默认 universe 等权，保持原行为）
  const [benchmarkType, setBenchmarkType] = useState<BenchmarkType>('universe_equal');
  const [benchmarkAssets, setBenchmarkAssets] = useState(DEFAULT_ASSETS);
  const [benchmarkSymbol, setBenchmarkSymbol] = useState('000001.SZ');
  /** P2-4：归因请求（180s）的中断控制；容量请求为默认 15s 超时，不在此列 */
  const attrTask = useAbortableTask();

  /** I-9：容量请求代际守卫 —— 防抖只挡"还没发"的请求，
   *  已发出的旧请求后到会让 aum 与滑块值/公式串不一致。 */
  const capSeqRef = useRef(0);
  const loadCapacity = useCallback(async () => {
    const seq = ++capSeqRef.current;
    try {
      const value = await deskApi.capacity(participationCap, holdings, rebalancePerYear);
      if (seq !== capSeqRef.current) return;
      setCapacity(value);
    } catch (e) {
      if (seq !== capSeqRef.current) return;
      setErr(e instanceof ApiError ? e.message : '容量计算失败');
    }
  }, [participationCap, holdings, rebalancePerYear]);

  // 滑块拖动会连续变更参数，400ms 防抖后再请求，避免密集打后端
  useEffect(() => {
    const t = setTimeout(() => { void loadCapacity(); }, 400);
    return () => clearTimeout(t);
  }, [loadCapacity]);

  const runAttr = useCallback(async () => {
    // P2-4：Brinson 行业归因 + 风格回归 180s，卸载（切路由）时中断在途请求
    const ctrl = attrTask.begin();
    setBusy(true); setErr(null);
    try {
      const r = await deskApi.attribution(assets, windowDays, {
        benchmark_type: benchmarkType,
        ...(benchmarkType === 'custom_portfolio'
          ? { benchmark_assets: benchmarkAssets } : {}),
        ...(benchmarkType === 'single_symbol'
          ? { benchmark_symbol: benchmarkSymbol } : {}),
      }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setAttr(r);
    }
    catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setErr(e instanceof ApiError ? e.message : '归因计算失败');
    }
    finally { if (attrTask.finish(ctrl)) setBusy(false); }
  }, [assets, windowDays, benchmarkType, benchmarkAssets, benchmarkSymbol, attrTask]);

  useEffect(() => { void runAttr(); /* 初始默认组合 */ // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 权重和（后端会自动归一化，但提示用户当前总和）
  const weightSum = assets.reduce((s, a) => s + (Number(a.weight) || 0), 0);
  const updateAsset = (i: number, patch: Partial<{ code: string; weight: number }>) =>
    setAssets(assets.map((x, j) => j === i ? { ...x, ...patch } : x));
  const addAsset = () => setAssets([...assets, { code: '', weight: 0 }]);
  const removeAsset = (i: number) => setAssets(assets.filter((_, j) => j !== i));

  // 基准组合编辑（复用资产行的增删改模式）
  const updateBenchAsset = (i: number, patch: Partial<{ code: string; weight: number }>) =>
    setBenchmarkAssets(benchmarkAssets.map((x, j) => j === i ? { ...x, ...patch } : x));
  const addBenchAsset = () =>
    setBenchmarkAssets([...benchmarkAssets, { code: '', weight: 0 }]);
  const removeBenchAsset = (i: number) =>
    setBenchmarkAssets(benchmarkAssets.filter((_, j) => j !== i));


  const styleOption = attr
    ? ({
        grid: { left: 60, right: 16, top: 20, bottom: 22 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'value', axisLabel: { fontSize: 9, formatter: (v: number) => `${(v * 100).toFixed(1)}%` } },
        yAxis: { type: 'category', data: Object.keys(attr.style.contributions),
                 axisLabel: { fontSize: 9 } },
        series: [{ type: 'bar',
                   data: Object.values(attr.style.contributions).map((v) => v),
                   itemStyle: { color: (p: { data: number }) =>
                     p.data >= 0 ? '#DC2626' : '#059669' }, barWidth: 14,
                   label: { show: true, position: 'right',
                            formatter: (p: { data: number }) => `${(p.data * 100).toFixed(2)}%`,
                            fontSize: 9 } }],
      } as echarts.EChartsOption)
    : null;
  const styleRef = useChart(styleOption);

  const fmtPct = (v: number | null | undefined, digits = 2) =>
    v == null ? '—' : `${(v * 100).toFixed(digits)}%`;

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold text-ink">策略容量与业绩归因中心</h1>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}

      <SectionCard title="策略容量上限评估（真实 ADV 推导）" bodyClassName="p-3 space-y-3">
        <div className="flex items-baseline gap-3">
          <span className="num text-2xl font-semibold text-brand-700">
            {capacity?.aum_yi != null ? `${capacity.aum_yi} 亿元` : '—'}
          </span>
          <span className="text-2xs text-ink-muted">{capacity?.formula ?? ''}</span>
        </div>
        {/* 假设参数（P1-9）：调整后立即重算容量 */}
        <div className="grid grid-cols-1 gap-3 border-t border-hair pt-2 sm:grid-cols-3">
          <label className="space-y-1 text-2xs text-ink-secondary">
            <span>单日参与上限 <span className="num font-medium text-ink">{(participationCap * 100).toFixed(1)}%</span> ADV</span>
            <input type="range" min={0.001} max={0.3} step={0.001}
                   value={participationCap}
                   onChange={(e) => setParticipationCap(Number(e.target.value))}
                   className="w-full accent-brand-500" />
            <span className="block text-ink-muted">0.1% ~ 30%（默认 1%）</span>
          </label>
          <label className="space-y-1 text-2xs text-ink-secondary">
            <span>持仓数量 <span className="num font-medium text-ink">{holdings}</span> 只</span>
            <input type="range" min={1} max={100} step={1}
                   value={holdings}
                   onChange={(e) => setHoldings(Number(e.target.value))}
                   className="w-full accent-brand-500" />
            <span className="block text-ink-muted">1 ~ 100（默认 20）</span>
          </label>
          <label className="space-y-1 text-2xs text-ink-secondary">
            <span>年调仓次数 <span className="num font-medium text-ink">{rebalancePerYear}</span> 次</span>
            <input type="range" min={1} max={252} step={1}
                   value={rebalancePerYear}
                   onChange={(e) => setRebalancePerYear(Number(e.target.value))}
                   className="w-full accent-brand-500" />
            <span className="block text-ink-muted">1 ~ 252（默认 12）</span>
          </label>
        </div>
      </SectionCard>

      <SectionCard title="业绩归因（Brinson 行业 + 风格回归，真实收益）" bodyClassName="p-3 space-y-3">
        <div className="flex flex-wrap items-end gap-2 text-2xs">
          {assets.map((a, i) => (
            <div key={i} className="flex items-center gap-1">
              <input value={a.code}
                     onChange={(e) => updateAsset(i, { code: e.target.value })}
                     className={`${inputCls} w-28 font-mono`} />
              <input type="number" step={0.05} min={0} max={1} value={a.weight}
                     onChange={(e) => updateAsset(i, { weight: Number(e.target.value) })}
                     className={`${inputCls} w-16`} />
              {assets.length > 1 && (
                <button onClick={() => removeAsset(i)} title="删除此行"
                        className="rounded border border-hair px-1 py-0.5 text-2xs text-red-600 hover:bg-red-50">×</button>
              )}
            </div>
          ))}
          <button onClick={addAsset} disabled={assets.length >= 20}
                  className="rounded-md border border-hair px-2 py-1.5 text-2xs hover:bg-slate-50 disabled:opacity-60">
            + 添加资产
          </button>
          <button disabled={busy} onClick={() => void runAttr()}
                  className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
            {busy ? '归因计算中…' : '运行归因'}
          </button>
          {attr && <span className="text-ink-muted">{attr.benchmark_desc} · 近 {attr.window_days} 个交易日（{attr.n_obs} 个有效观测）</span>}
        </div>
        <div className="flex items-center gap-3 text-2xs text-ink-secondary">
          <span>权重合计 <span className={`num font-semibold ${Math.abs(weightSum - 1) < 0.001 ? 'text-emerald-600' : 'text-amber-600'}`}>{weightSum.toFixed(2)}</span>
            {Math.abs(weightSum - 1) >= 0.001 && <span className="text-amber-600">（≠ 1，后端自动归一化）</span>}
          </span>
          <span className="text-ink-muted">·</span>
          <span>时间窗口</span>
          {WINDOW_OPTIONS.map((w) => (
            <button key={w} onClick={() => setWindowDays(w)}
                    className={`rounded px-1.5 py-0.5 text-2xs ${windowDays === w ? 'bg-brand-50 text-brand-700 font-medium' : 'border border-hair text-ink-secondary hover:bg-slate-50'}`}>
              {w} 日
            </button>
          ))}
        </div>

        {/* 基准选择：universe 等权 / 自定义组合 / 单一标的（不依赖外部指数数据） */}
        <div className="flex flex-wrap items-end gap-2 text-2xs">
          <span className="text-ink-secondary">基准</span>
          <select value={benchmarkType}
                  className={`${inputCls} w-28`}
                  onChange={(e) => setBenchmarkType(e.target.value as BenchmarkType)}>
            {(Object.keys(BENCHMARK_LABELS) as BenchmarkType[]).map((t) => (
              <option key={t} value={t}>{BENCHMARK_LABELS[t]}</option>
            ))}
          </select>
          {benchmarkType === 'single_symbol' && (
            <input value={benchmarkSymbol} placeholder="基准标的代码（如 510300.SH）"
                   className={`${inputCls} w-44 font-mono`}
                   onChange={(e) => setBenchmarkSymbol(e.target.value)} />
          )}
          {benchmarkType === 'custom_portfolio' && (
            <>
              {benchmarkAssets.map((b, i) => (
                <div key={i} className="flex items-center gap-1">
                  <input value={b.code} placeholder="代码"
                         onChange={(e) => updateBenchAsset(i, { code: e.target.value })}
                         className={`${inputCls} w-28 font-mono`} />
                  <input type="number" step={0.05} min={0} max={1} value={b.weight}
                         onChange={(e) => updateBenchAsset(i, { weight: Number(e.target.value) })}
                         className={`${inputCls} w-16`} />
                  {benchmarkAssets.length > 1 && (
                    <button onClick={() => removeBenchAsset(i)} title="删除此行"
                            className="rounded border border-hair px-1 py-0.5 text-2xs text-red-600 hover:bg-red-50">×</button>
                  )}
                </div>
              ))}
              <button onClick={addBenchAsset} disabled={benchmarkAssets.length >= 50}
                      className="rounded-md border border-hair px-2 py-1.5 text-2xs hover:bg-slate-50 disabled:opacity-60">
                + 基准资产
              </button>
            </>
          )}
        </div>

        {attr && (
          <>
            <div className="grid grid-cols-4 gap-2 text-2xs">
              <div className="rounded-lg border border-hair bg-white px-3 py-2">
                <div className="text-ink-secondary">组合收益</div>
                <div className="num text-base font-semibold">{fmtPct(attr.portfolio_return)}</div>
              </div>
              <div className="rounded-lg border border-hair bg-white px-3 py-2">
                <div className="text-ink-secondary">基准收益</div>
                <div className="num text-base font-semibold">{fmtPct(attr.benchmark_return)}</div>
              </div>
              <div className="rounded-lg border border-hair bg-white px-3 py-2">
                <div className="text-ink-secondary">超额收益</div>
                <div className={`num text-base font-semibold ${attr.excess_return >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {fmtPct(attr.excess_return)}</div>
              </div>
              <div className="rounded-lg border border-hair bg-white px-3 py-2">
                <div className="text-ink-secondary">残差 Alpha（年化）</div>
                <div className="num text-base font-semibold">{fmtPct(attr.style.alpha_annualized)}</div>
              </div>
            </div>
            <div className="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-2xs text-blue-800">
              基准口径：{attr.benchmark_desc}；{attr.benchmark_policy.weight_basis === 'equal_weight_all_available_universe_symbols'
                ? `窗口内 ${attr.benchmark_policy.symbol_count} 个有数据成分等权`
                : '按请求中的基准权重计算'}，数据截至 {attr.benchmark_policy.as_of}。
            </div>

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <div>
                <div className="mb-1 text-2xs font-medium text-ink-secondary">
                  Brinson-Fachler 行业归因（allocation / selection / interaction）
                </div>
                <table className="w-full text-2xs">
                  <thead>
                    <tr className="text-ink-secondary">
                      <th className="text-left font-medium">行业</th>
                      <th className="font-medium">组合权重</th>
                      <th className="font-medium">基准权重</th>
                      <th className="font-medium">配置收益</th>
                      <th className="font-medium">选股收益</th>
                      <th className="font-medium">合计</th>
                    </tr>
                  </thead>
                  <tbody>
                    {attr.brinson.sectors.map((s) => (
                      <tr key={s.industry} className="border-t border-hair">
                        <td className="py-1">{s.industry}</td>
                        <td className="num text-center">{fmtPct(s.portfolio_weight, 1)}</td>
                        <td className="num text-center">{fmtPct(s.benchmark_weight, 1)}</td>
                        <td className={`num text-center ${s.allocation >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                          {fmtPct(s.allocation)}</td>
                        <td className={`num text-center ${s.selection >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                          {fmtPct(s.selection)}</td>
                        <td className={`num text-center font-semibold ${s.total >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                          {fmtPct(s.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t-2 border-hair bg-slate-50 font-semibold">
                      <td className="py-1">合计</td>
                      <td className="num text-center">—</td>
                      <td className="num text-center">—</td>
                      <td className={`num text-center ${attr.brinson.summary.allocation >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                        {fmtPct(attr.brinson.summary.allocation)}</td>
                      <td className={`num text-center ${attr.brinson.summary.selection >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                        {fmtPct(attr.brinson.summary.selection)}</td>
                      <td className={`num text-center ${attr.brinson.summary.residual_alpha >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                        {fmtPct(attr.brinson.summary.residual_alpha)}</td>
                    </tr>
                  </tfoot>
                </table>
                <div className="mt-1 text-2xs text-ink-secondary">
                  交互 {fmtPct(attr.brinson.summary.interaction)} ·
                  残差 Alpha {fmtPct(attr.brinson.summary.residual_alpha)} ·
                  交互 {fmtPct(attr.brinson.summary.interaction)} ·
                  残差 Alpha {fmtPct(attr.brinson.summary.residual_alpha)}
                </div>
              </div>
              <div>
                <div className="mb-1 text-2xs font-medium text-ink-secondary">
                  风格贡献（Barra-lite 回归，R² = {attr.style.r_squared != null ? (attr.style.r_squared * 100).toFixed(0) + '%' : '—'}）
                </div>
                <div ref={styleRef} className="h-44 w-full" />
                <div className="text-2xs text-ink-secondary">
                  β：{Object.entries(attr.style.betas).map(([k, v]) => `${k}=${v.toFixed(2)}`).join(' · ')}
                </div>
              </div>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
}
