/**
 * AI 日报（前沿演进 1-3 模板版）：每日盘后由晚间例行调度生成。
 *
 * 结构化 sections 原生渲染（**加粗** 最小解析），支持按历史日期回看；
 * 「重新生成」走 researcher 权限（后端 require_role，未授权时展示信封错误）。
 */
import { useCallback, useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { monitorApi, type DailyReport } from '@/api/monitor';
import { SectionCard } from '@/components/ui';

/** 最小 markdown 行内渲染：**加粗** 与 `代码` */
function renderLine(line: string, key: number) {
  const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <li key={key} className="leading-relaxed">
      {parts.map((p, j) =>
        p.startsWith('**') ? <strong key={j} className="text-ink">{p.slice(2, -2)}</strong>
          : p.startsWith('`') ? (
            <code key={j} className="rounded bg-slate-100 px-1 font-mono">{p.slice(1, -1)}</code>
          ) : <span key={j}>{p}</span>)}
    </li>
  );
}

const STATE_STYLE: Record<string, string> = {
  healthy: 'bg-emerald-50 text-emerald-700',
  watch: 'bg-amber-50 text-amber-700',
  degraded: 'bg-red-50 text-red-600',
  unknown: 'bg-slate-100 text-ink-muted',
};

export default function Report() {
  const [report, setReport] = useState<DailyReport | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (date?: string) => {
    setLoading(true); setErr(null);
    try {
      const d = await monitorApi.report(date);
      setReport(d.report);
      setHistory(d.history);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '日报加载失败');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(selected); }, [load, selected]);

  const regenerate = async () => {
    setRegenerating(true); setErr(null);
    try {
      const rep = await monitorApi.generateReport();
      setReport(rep);
      setSelected(undefined);
      setHistory((h) => (h.includes(rep.date) ? h : [...h, rep.date].sort()));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '生成失败（需要研究员权限）');
    } finally { setRegenerating(false); }
  };

  return (
    <div className="flex min-h-full flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-bold text-ink">AI 日报</h1>
          <p className="text-2xs text-ink-muted">
            每日收盘后自动生成（数据面 · 因子健康度 · 模拟盘执行 · 风险提示），确定性模板产出、无外部依赖
          </p>
        </div>
        <div className="flex items-center gap-2">
          {history.length > 0 && (
            <select
              value={selected ?? ''}
              onChange={(e) => setSelected(e.target.value || undefined)}
              className="rounded-md border border-hair bg-white px-2 py-1.5 text-xs outline-none focus:border-brand-300">
              <option value="">最新一期</option>
              {[...history].reverse().map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          )}
          <button onClick={() => void regenerate()} disabled={regenerating}
            className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white
              hover:bg-brand-600 disabled:opacity-60">
            {regenerating ? '生成中…' : '重新生成'}
          </button>
        </div>
      </div>

      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}
      {loading ? (
        <div className="animate-pulse rounded-lg border border-hair bg-white p-6 text-xs text-ink-muted">
          加载中…
        </div>
      ) : !report ? (
        <SectionCard title="尚无日报" bodyClassName="p-6 text-center">
          <p className="text-xs text-ink-muted">
            晚间例行（默认 17:30）会自动生成首期日报；也可以点击右上角「重新生成」立即生成。
          </p>
        </SectionCard>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-2xs text-ink-muted">
            <span>期数：<b className="text-ink">{report.date}</b></span>
            <span>生成于 {report.generated_at.replace('T', ' ')}</span>
            {report.tips.length > 0 && (
              <span className={`rounded px-1.5 py-0.5 font-medium ${
                STATE_STYLE[report.tips.length > 2 ? 'degraded' : 'watch']}`}>
                {report.tips.length} 条风险提示
              </span>
            )}
          </div>
          {report.tips.length > 0 && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
              <div className="mb-1 text-xs font-semibold text-amber-900">风险提示</div>
              <ul className="list-disc space-y-0.5 pl-4 text-2xs text-amber-800">
                {report.tips.map((t, i) => <li key={i}>{t}</li>)}
              </ul>
            </div>
          )}
          {report.sections.map((s) => (
            <SectionCard key={s.title} title={s.title} bodyClassName="p-3">
              <ul className="list-disc space-y-1 pl-4 text-xs text-ink-secondary">
                {s.lines.map((line, i) => renderLine(line, i))}
              </ul>
            </SectionCard>
          ))}
          <details className="rounded-lg border border-hair bg-white px-3 py-2">
            <summary className="cursor-pointer text-2xs text-ink-muted">Markdown 原文（复制/存档用）</summary>
            <pre className="mt-2 whitespace-pre-wrap break-words text-2xs leading-relaxed text-ink-secondary">
              {report.markdown}
            </pre>
          </details>
        </>
      )}
      <p className="text-center text-2xs text-ink-muted">
        日报全部由真实落库数据聚合生成 ｜ 仅供研究参考
      </p>
    </div>
  );
}
