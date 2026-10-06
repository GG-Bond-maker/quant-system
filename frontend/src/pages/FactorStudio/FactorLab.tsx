/**
 * 因子库 + 因子检测报告（§4.3，Sprint4）：
 * - 因子库：表达式经后端 AST 白名单校验 + 真实截面评估后入库/删除；
 * - 检测报告：5 分组分层净值/年化 + 1/5/10/20 日衰减 + Top 组换手率。
 * 自包含组件：不依赖父页面状态（保存表达式可从「评估」结果带出）。
 */
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import {
  studioApi, type CustomFactor, type FactorReportResult, type FactorOrthogonalizeResult,
} from '@/api/production';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useChart } from '@/utils/useChart';
import * as echarts from '@/lib/echarts';
import FactorCorrelationCard from './FactorCorrelationCard';
import FactorDecayCard from './FactorDecayCard';

const inputCls = 'rounded border border-hair bg-surface px-2 py-1 text-xs outline-none focus:border-brand-300';

export default function FactorLab({ initialExpr, horizon }: {
  /** 父页面评估结果的表达式（点击「存入因子库」带出） */
  initialExpr?: string | null;
  horizon: number;
}) {
  const [factors, setFactors] = useState<CustomFactor[] | null>(null);
  const [name, setName] = useState('');
  const [expr, setExpr] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const [report, setReport] = useState<FactorReportResult | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [ortho, setOrtho] = useState<FactorOrthogonalizeResult | null>(null);
  const [orthoBusy, setOrthoBusy] = useState(false);

  // P2-4：/studio/factors 保存与 /studio/factor-report 均为真实截面评估 120s，
  // 卸载（切路由）时中断在途请求；两个操作互不相关，各用一个 hook 实例。
  const saveTask = useAbortableTask();
  const reportTask = useAbortableTask();
  const orthoTask = useAbortableTask();

  const load = useCallback(async () => {
    try { setFactors(await studioApi.factors()); }
    catch { /* 因子库加载失败不阻塞页面 */ }
  }, []);
  useEffect(() => { void load(); }, [load]);
  // 父页面表达式变化时同步到保存输入框（未手动编辑过的前提下）
  useEffect(() => {
    if (initialExpr) setExpr((cur) => (cur ? cur : initialExpr));
  }, [initialExpr]);

  const save = useCallback(async () => {
    if (!name.trim() || !expr.trim()) { setErr('因子名与表达式必填'); return; }
    const ctrl = saveTask.begin();
    setBusy(true); setErr(null); setMsg(null);
    try {
      await studioApi.saveFactor({ name: name.trim(), expression: expr.trim(), horizon },
        { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setMsg(`因子「${name.trim()}」已入库`);
      setName('');
      await load();
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setErr(e instanceof ApiError ? e.message : '入库失败');
    } finally { if (saveTask.finish(ctrl)) setBusy(false); }
  }, [name, expr, horizon, load, saveTask]);

  const remove = useCallback(async (id: number) => {
    try { await studioApi.deleteFactor(id); await load(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '删除失败'); }
  }, [load]);

  const runReport = useCallback(async (target: string) => {
    const ctrl = reportTask.begin();
    setReportBusy(true); setErr(null);
    try {
      const r = await studioApi.factorReport({ expr: target, horizon }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setReport(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '报告生成失败');
    } finally { if (reportTask.finish(ctrl)) setReportBusy(false); }
  }, [horizon, reportTask]);

  const runOrthogonalize = useCallback(async (target: string) => {
    const ctrl = orthoTask.begin();
    setOrthoBusy(true); setErr(null);
    try {
      const r = await studioApi.orthogonalize({ expr: target, horizon }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setOrtho(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '正交纯化分析失败');
    } finally { if (orthoTask.finish(ctrl)) setOrthoBusy(false); }
  }, [horizon, orthoTask]);


  // 5 分组分层净值曲线（q1..q5 色阶；单调性 = 因子有效性的直观判据）
  // 用宽松的 EChartsCoreOption：图例触发 tooltip 在严格 ComposeOption 下过窄
  const navOption = report ? ({
    grid: { left: 48, right: 12, top: 24, bottom: 24 },
    tooltip: { trigger: 'legend' },
    legend: { data: report.quintile_navs.map((q) => q.q), top: 0, textStyle: { fontSize: 10 } },
    xAxis: { type: 'category',
             data: report.quintile_navs[0]?.nav.map((p) => p.date) ?? [],
             axisLabel: { fontSize: 9 } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9 } },
    series: report.quintile_navs.map((q) => ({
      name: q.q, type: 'line' as const, data: q.nav.map((p) => p.nav),
      lineStyle: { width: 1.4 }, symbol: 'none',
    })),
  } as echarts.EChartsCoreOption) : null;
  const navRef = useChart(navOption);

  return (
    <SectionCard title="因子库与检测报告（§4.3）" bodyClassName="space-y-3">
      {/* 入库行 */}
      <div className="flex flex-wrap items-center gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="因子名"
          className={`${inputCls} w-32`} />
        <input value={expr} onChange={(e) => setExpr(e.target.value)}
          placeholder="Alpha 表达式（可用字段/算子见表达式引擎白名单）"
          className={`${inputCls} min-w-0 flex-1 font-mono`} />
        <button onClick={() => void save()} disabled={busy}
          className="rounded bg-brand-500 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
          {busy ? '评估入库中…' : '校验并入库'}
        </button>
        <button onClick={() => void runOrthogonalize(expr.trim())} disabled={orthoBusy || !expr.trim()}
          className="rounded border border-brand-500/40 px-2.5 py-1 text-xs font-medium text-brand-500 hover:bg-surface-alt disabled:opacity-60">
          {orthoBusy ? '正交纯化中…' : '正交纯化分析'}
        </button>
      </div>
      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-2xs text-danger">{err}</div>}
      {msg && <div className="rounded-md bg-success-bg px-3 py-2 text-2xs text-success">{msg}</div>}

      {/* 因子库列表 */}
      {factors && factors.length > 0 && (
        <table className="w-full text-2xs">
          <thead>
            <tr className="text-ink-secondary">
              <th className="text-left font-medium">名称</th>
              <th className="text-left font-medium">表达式</th>
              <th className="font-medium">Mean IC</th>
              <th className="font-medium">ICIR</th>
              <th className="font-medium">h</th>
              <th className="font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {factors.map((f) => (
              <tr key={f.id} className="border-t border-hair">
                <td className="py-1 pr-2 font-medium text-ink">{f.name}</td>
                <td className="max-w-0 truncate py-1 pr-2 font-mono text-ink-secondary"
                    title={f.expression}>{f.expression}</td>
                {/* ⚠️ metrics 可为 null（下方已显 '—'）：不得写 `f.metrics && f.metrics.mean_ic > 0 ? 红 : 绿`
                    —— null/falsy 会落进 text-success（把"无数据"说成"负 IC"）。null ⇒ 中性色。 */}
                <td className={`num text-center ${f.metrics == null ? 'text-ink-muted' : f.metrics.mean_ic > 0 ? 'text-danger' : 'text-success'}`}>
                  {f.metrics ? f.metrics.mean_ic.toFixed(4) : '—'}</td>
                <td className="num text-center">{f.metrics ? f.metrics.icir.toFixed(3) : '—'}</td>
                <td className="num text-center">{f.horizon}</td>
                <td className="text-center">
                  <div className="flex items-center justify-center gap-1">
                    <button onClick={() => { setExpr(f.expression); void runReport(f.expression); }}
                            disabled={reportBusy}
                            className="rounded border border-hair px-1.5 py-0.5 hover:bg-surface-alt disabled:opacity-60">
                      {reportBusy ? '报告中…' : '报告'}
                    </button>
                    <button onClick={() => { setExpr(f.expression); void runOrthogonalize(f.expression); }}
                            disabled={orthoBusy}
                            className="rounded border border-brand-500/30 px-1.5 py-0.5 text-brand-500 hover:bg-surface-alt disabled:opacity-60">
                      {orthoBusy ? '正交中…' : '正交'}
                    </button>
                    <button onClick={() => void remove(f.id)}
                            className="rounded border border-danger/30 px-1.5 py-0.5 text-danger hover:bg-danger-bg">
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 正交纯化分析卡片 */}
      {ortho && (
        <div className="space-y-3 rounded-lg border border-brand-500/30 bg-surface-alt p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-ink">因子正交纯化与多重检验</span>
              <span className={`rounded px-2 py-0.5 text-2xs font-medium ${ortho.is_significant ? 'bg-success-bg text-success' : 'bg-warning-bg text-warning'}`}>
                {ortho.is_significant ? '通过 BH-FDR 显著性检验 (独立 Alpha)' : '未通过多重检验 (或为风格暴露噪音)'}
              </span>
            </div>
            <span className="font-mono text-2xs text-ink-muted">样本数: {ortho.n_obs} 日</span>
          </div>

          <div className="font-mono text-2xs text-ink-secondary">{ortho.expr}</div>

          {/* 指标对比栅格 */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5 text-2xs">
            <div className="rounded bg-surface p-2 border border-hair">
              <div className="text-ink-muted">原始 IC vs 纯化 IC</div>
              <div className="mt-1 flex items-baseline gap-1">
                <span className="num text-ink-secondary">{ortho.raw_mean_ic.toFixed(4)}</span>
                <span className="text-ink-muted">→</span>
                <b className={`num font-semibold ${ortho.pure_mean_ic > 0 ? 'text-danger' : 'text-success'}`}>{ortho.pure_mean_ic.toFixed(4)}</b>
              </div>
            </div>
            <div className="rounded bg-surface p-2 border border-hair">
              <div className="text-ink-muted">原始 ICIR vs 纯化 ICIR</div>
              <div className="mt-1 flex items-baseline gap-1">
                <span className="num text-ink-secondary">{ortho.raw_icir.toFixed(3)}</span>
                <span className="text-ink-muted">→</span>
                <b className="num font-semibold text-brand-500">{ortho.pure_icir.toFixed(3)}</b>
              </div>
            </div>
            <div className="rounded bg-surface p-2 border border-hair">
              <div className="text-ink-muted">t 统计量</div>
              <div className="mt-1">
                <b className="num font-semibold text-ink">{ortho.t_stat.toFixed(2)}</b>
              </div>
            </div>
            <div className="rounded bg-surface p-2 border border-hair">
              <div className="text-ink-muted">BH-FDR 修正 p 值</div>
              <div className="mt-1">
                <b className={`num font-semibold ${ortho.p_value_fdr < 0.05 ? 'text-success' : 'text-danger'}`}>{ortho.p_value_fdr < 0.001 ? '<0.001' : ortho.p_value_fdr.toFixed(3)}</b>
                <span className="text-ink-muted ml-1 text-3xs">(原始 {ortho.p_value_raw.toFixed(3)})</span>
              </div>
            </div>
            <div className="rounded bg-surface p-2 border border-hair">
              <div className="text-ink-muted">Deflated IC (惩罚后)</div>
              <div className="mt-1">
                <b className="num font-semibold text-ink">{ortho.deflated_ic.toFixed(4)}</b>
              </div>
            </div>
          </div>

          {/* 基础风格因子相关性剥离表 */}
          {ortho.base_corrs && ortho.base_corrs.length > 0 && (
            <div className="overflow-x-auto">
              <div className="text-2xs font-medium text-ink-secondary mb-1">风格因子相关性剥离明细（截面多元回归 OLS）</div>
              <table className="w-full text-2xs">
                <thead>
                  <tr className="border-b border-hair text-ink-muted">
                    <th className="text-left py-1 font-medium">基准风格因子</th>
                    <th className="text-left py-1 font-medium">名称说明</th>
                    <th className="text-right py-1 font-medium">原始相关系数</th>
                    <th className="text-right py-1 font-medium">残差纯化后相关</th>
                    <th className="text-right py-1 font-medium">相关性消除率</th>
                  </tr>
                </thead>
                <tbody>
                  {ortho.base_corrs.map((b) => (
                    <tr key={b.factor} className="border-b border-hair/50">
                      <td className="py-1 font-mono text-ink">{b.factor}</td>
                      <td className="py-1 text-ink-secondary">{b.name}</td>
                      <td className="py-1 text-right num text-ink">{b.raw_corr.toFixed(3)}</td>
                      <td className="py-1 text-right num text-success font-medium">{b.pure_corr.toFixed(4)}</td>
                      <td className="py-1 text-right num text-brand-500 font-semibold">{b.reduction_pct.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-2xs text-ink-muted">
            {ortho.summary}
          </p>
        </div>
      )}

      {/* 检测报告 */}
      {report && (

        <div className="space-y-2 rounded-lg border border-hair bg-surface-alt p-3">
          <div className="font-mono text-2xs text-ink-secondary">{report.expr}</div>
          <div className="flex flex-wrap items-center gap-3 text-2xs">
            <span>Mean IC <b className="num">{report.mean_ic.toFixed(4)}</b></span>
            <span>ICIR <b className="num">{report.icir.toFixed(3)}</b></span>
            <span>Top 组换手 <b className="num">{report.top_turnover != null
              ? `${(report.top_turnover * 100).toFixed(1)}%/日` : '—'}</b></span>
          </div>
          <div className="text-2xs font-medium text-ink-secondary">5 分组分层累计净值（q1 最低 → q5 最高）</div>
          <div ref={navRef} className="h-44 w-full" />
          <div className="flex flex-wrap gap-2 text-2xs">
            {Object.entries(report.quintile_annual).map(([q, v]) => (
              <span key={q} className="rounded bg-surface px-1.5 py-0.5">
                {/* ⚠️ 分组年化 v 可为 null（:174 已显 '—'）：不得写 `v != null && v > 0 ? 红 : 绿`
                    —— null 会落进 text-success。null ⇒ 中性色。 */}
                {q} 年化 <b className={`num ${v == null ? 'text-ink-muted' : v > 0 ? 'text-danger' : 'text-success'}`}>
                  {v != null ? `${(v * 100).toFixed(1)}%` : '—'}</b>
              </span>
            ))}
          </div>
          <div className="text-2xs font-medium text-ink-secondary">衰减分析（RankIC @ 1/5/10/20 日）</div>
          <div className="flex flex-wrap gap-2 text-2xs">
            {Object.entries(report.decay).map(([h, d]) => (
              <span key={h} className="rounded bg-surface px-1.5 py-0.5">
                {h}日 <b className="num">{d.rank_ic.toFixed(4)}</b>
                {d.icir != null && <span className="text-ink-muted">（ICIR {d.icir.toFixed(2)}）</span>}
              </span>
            ))}
          </div>
          <p className="text-2xs text-ink-muted">
            分层 = 每日按因子值 5 分位等权分组、逐日再平衡（无费用）；衰减 = 同一截面
            对 {report.horizon} 日外多窗口前向收益的 RankIC；换手率为 Top 20% 名单日度变动率，
            是持有成本的直观代理。
          </p>
        </div>
      )}

      {/* 因子正交与相关性矩阵看板 */}
      <FactorCorrelationCard
        customFactors={factors}
        onSelectForOrthogonalize={(target) => {
          setExpr(target);
          void runOrthogonalize(target);
        }}
      />

      {/* 因子 IC 衰减与换手半衰期分析看板 */}
      <FactorDecayCard customFactors={factors} />
    </SectionCard>
  );
}

function SectionCard({ title, bodyClassName, children }: {
  title: string; bodyClassName?: string; children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-hair bg-surface">
      <div className="border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
      </div>
      <div className={bodyClassName ?? 'p-3'}>{children}</div>
    </div>
  );
}
