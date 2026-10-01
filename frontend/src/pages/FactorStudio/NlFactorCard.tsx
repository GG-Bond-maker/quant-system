/**
 * NL-to-Factor 卡（前沿演进 1-1）：自然语言 → Alpha 表达式。
 *
 * 链路：LLM 只产 DSL 文本 → 后端 alpha_expr AST 白名单静态校验（防注入）
 * → 通过后在真实 features 截面评估 RankIC（与 alpha-eval 同口径）。
 * LLM 未配置（LLM_PROVIDER=none）时后端返回 53000，界面给出配置指引。
 */
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { monitorApi, type NlFactorResult } from '@/api/monitor';
import { SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';

const inputCls =
  'w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';

const EXAMPLES = [
  '近 20 日量价背离：成交量创新高但收益率走弱的因子',
  '5 日动量除以 20 日波动率的夏普式因子',
  '收盘价在 20 日布林带中的位置乘以成交量分位',
];

export default function NlFactorCard() {
  const [text, setText] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<NlFactorResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // P2-4：NL→因子（LLM 编译 + 真实截面 RankIC 评估）120s，卸载时中断在途请求
  const task = useAbortableTask();

  const run = async () => {
    if (!text.trim() || running) return;
    const ctrl = task.begin();
    setRunning(true); setErr(null);
    try {
      const r = await monitorApi.nlToFactor(text.trim(), undefined, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setResult(r);
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setErr(e instanceof ApiError ? e.message : '生成失败');
    } finally { if (task.finish(ctrl)) setRunning(false); }
  };

  const ev = result?.evaluation ?? null;

  return (
    <SectionCard title="NL-to-Factor · 自然语言生成因子" bodyClassName="space-y-2">
      <p className="text-2xs text-ink-muted">
        用自然语言描述因子逻辑，LLM 编译为表达式后经<b>算子白名单校验</b>与
        <b>真实截面 RankIC 评估</b>（未配置 LLM 时请在项目根目录 .env 设置
        <code className="mx-0.5 rounded bg-surface-sunken px-1">LLM_PROVIDER</code>）。
      </p>
      <div className="flex items-start gap-2">
        <textarea
          rows={2} value={text} disabled={running}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void run(); }}
          placeholder={EXAMPLES[0]}
          className={`${inputCls} flex-1 resize-y`} />
        <button onClick={() => void run()} disabled={running || !text.trim()}
          className="shrink-0 rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white
            hover:bg-brand-600 disabled:opacity-60">
          {running ? '生成中…' : '生成并评估'}
        </button>
      </div>
      <div className="flex flex-wrap gap-1">
        {EXAMPLES.map((ex) => (
          <button key={ex} onClick={() => setText(ex)}
            className="rounded bg-surface-alt px-1.5 py-0.5 text-2xs text-ink-secondary
              hover:bg-brand-50 hover:text-brand-600">
            {ex.slice(0, 18)}…
          </button>
        ))}
      </div>
      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}
      {result && (
        <div className="space-y-2 rounded-md border border-hair bg-surface-alt p-2.5">
          <div className="flex items-center gap-2">
            <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${
              result.valid ? 'bg-success-bg text-success' : 'bg-danger-bg text-danger'}`}>
              {result.valid ? '校验通过' : '校验失败'}
            </span>
            <span className="text-2xs text-ink-muted">
              {result.provider} · {result.model} · H={result.horizon}
            </span>
          </div>
          <code className="block break-all rounded bg-surface px-2 py-1.5 font-mono text-xs text-ink">
            {result.expression}
          </code>
          {!result.valid && result.error && (
            <p className="text-2xs text-danger">{result.error}</p>
          )}
          {result.valid && ev ? (
            <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-2xs text-ink-secondary">
              <span>Mean RankIC <b className="num text-ink">{ev.mean_ic?.toFixed(4) ?? '—'}</b></span>
              <span>ICIR <b className="num text-ink">{ev.icir?.toFixed(2) ?? '—'}</b></span>
              <span>t-stat <b className="num text-ink">{ev.t_stat?.toFixed(2) ?? '—'}</b></span>
              <span>有效 {ev.n_days ?? '—'} 日</span>
            </div>
          ) : result.valid && (
            <p className="text-2xs text-warn">{result.error}</p>
          )}
        </div>
      )}
    </SectionCard>
  );
}
