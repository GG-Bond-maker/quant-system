/**
 * 因子库 + 因子检测报告（§4.3，Sprint4）：
 * - 因子库：表达式经后端 AST 白名单校验 + 真实截面评估后入库/删除；
 * - 检测报告：5 分组分层净值/年化 + 1/5/10/20 日衰减 + Top 组换手率。
 * 自包含组件：不依赖父页面状态（保存表达式可从「评估」结果带出）。
 */
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import {
  studioApi, type CustomFactor, type FactorReportResult,
} from '@/api/production';
import { useChart } from '@/utils/useChart';
import * as echarts from '@/lib/echarts';

const inputCls = 'rounded border border-hair bg-white px-2 py-1 text-xs outline-none focus:border-brand-300';

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
    setBusy(true); setErr(null); setMsg(null);
    try {
      await studioApi.saveFactor({ name: name.trim(), expression: expr.trim(), horizon });
      setMsg(`因子「${name.trim()}」已入库`);
      setName('');
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '入库失败');
    } finally { setBusy(false); }
  }, [name, expr, horizon, load]);

  const remove = useCallback(async (id: number) => {
    try { await studioApi.deleteFactor(id); await load(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '删除失败'); }
  }, [load]);

  const runReport = useCallback(async (target: string) => {
    setReportBusy(true); setErr(null);
    try {
      setReport(await studioApi.factorReport({ expr: target, horizon }));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '报告生成失败');
    } finally { setReportBusy(false); }
  }, [horizon]);

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
    <SectionCard title="因子库与检测报告（§4.3）" bodyClassName="p-3 space-y-3">
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
      </div>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-2xs text-red-600">{err}</div>}
      {msg && <div className="rounded-md bg-emerald-50 px-3 py-2 text-2xs text-emerald-700">{msg}</div>}

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
                <td className={`num text-center ${f.metrics && f.metrics.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {f.metrics ? f.metrics.mean_ic.toFixed(4) : '—'}</td>
                <td className="num text-center">{f.metrics ? f.metrics.icir.toFixed(3) : '—'}</td>
                <td className="num text-center">{f.horizon}</td>
                <td className="text-center">
                  <div className="flex items-center justify-center gap-1">
                    <button onClick={() => { setExpr(f.expression); void runReport(f.expression); }}
                            disabled={reportBusy}
                            className="rounded border border-hair px-1.5 py-0.5 hover:bg-slate-50 disabled:opacity-60">
                      {reportBusy ? '报告中…' : '报告'}
                    </button>
                    <button onClick={() => void remove(f.id)}
                            className="rounded border border-red-200 px-1.5 py-0.5 text-red-500 hover:bg-red-50">
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 检测报告 */}
      {report && (
        <div className="space-y-2 rounded-lg border border-hair bg-slate-50/60 p-3">
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
              <span key={q} className="rounded bg-white px-1.5 py-0.5">
                {q} 年化 <b className={`num ${v != null && v > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {v != null ? `${(v * 100).toFixed(1)}%` : '—'}</b>
              </span>
            ))}
          </div>
          <div className="text-2xs font-medium text-ink-secondary">衰减分析（RankIC @ 1/5/10/20 日）</div>
          <div className="flex flex-wrap gap-2 text-2xs">
            {Object.entries(report.decay).map(([h, d]) => (
              <span key={h} className="rounded bg-white px-1.5 py-0.5">
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
    </SectionCard>
  );
}

function SectionCard({ title, bodyClassName, children }: {
  title: string; bodyClassName?: string; children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-hair bg-white">
      <div className="border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
      </div>
      <div className={bodyClassName ?? 'p-3'}>{children}</div>
    </div>
  );
}
