/**
 * 文本数据（FinLLM 前置）+ 截面镜像（MED-003 前置）面板。
 *
 * 两个数据前置能力的运维入口：
 * - 公告文档导入（JSON 粘贴）→ 规则/LLM 情绪打分 → 日频情绪因子落盘；
 * - 截面分区镜像新鲜度与手动重建（晚间例行每日自动执行，此处为手动兜底）。
 */
import { useCallback, useEffect, useState } from 'react';

import { ApiError, type RequestOptions } from '@/api/client';
import { datacenterApi, type MirrorStatus, type TextDataStatus } from '@/api/datacenter';
import { useAbortableTask } from '@/hooks/useAbortableTask';

/** 与 DataCenter 页内 Card 同款的轻量卡片（避免循环引用不从此处导出） */
function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-hair bg-white">
      <div className="border-b border-hair px-3 py-2 text-xs font-semibold text-ink">
        {title}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

const inputCls =
  'w-full rounded-md border border-hair bg-white px-2 py-1.5 font-mono text-2xs outline-none focus:border-brand-300';

const SAMPLE = JSON.stringify([
  { symbol: '000001.SZ', date: '2026-08-28', title: '示例公告标题',
    content: '公司中标XX项目，预计对全年业绩产生积极影响' },
], null, 1);

export default function TextDataPanel({ canResearch = false }: { canResearch?: boolean }) {
  const [status, setStatus] = useState<TextDataStatus | null>(null);
  const [mirror, setMirror] = useState<MirrorStatus | null>(null);
  const [jsonText, setJsonText] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // P2-4：mirror/status 冷扫 120s、text/build-factor 60s、mirror/rebuild 180s，
  // 卸载（切路由）时中断在途请求；状态读取与重建操作互不相关，各用一个 hook 实例。
  const loadTask = useAbortableTask();
  const runTask = useAbortableTask();

  const load = useCallback(async () => {
    const ctrl = loadTask.begin();
    // 两个读**互不相关**（见文件头注释）：一个失败不得丢弃另一个的成功结果。
    // 用 allSettled 而非 all —— 否则任一读失败（如 mirrorStatus 冷扫曾因 45s 预算
    // 小于实测 ~50s 而超时）会把已成功的另一个结果一起丢掉，令统计条全部渲染 "—"
    // 并只弹一条无归属的红条（正是本次线上问题）。改为独立落库 + 只报失败面板。
    const [t, m] = await Promise.allSettled([
      datacenterApi.textStatus(), datacenterApi.mirrorStatus({ signal: ctrl.signal })]);
    if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
    if (t.status === 'fulfilled') setStatus(t.value);
    if (m.status === 'fulfilled') setMirror(m.value);
    const failed: string[] = [];
    if (t.status === 'rejected') failed.push('文本数据');
    if (m.status === 'rejected') failed.push('截面镜像');
    if (!failed.length) { setErr(null); return; }
    // 只报告失败的面板；文案保留底层 ApiError（如「请求超时，请稍后重试」）
    const rej = t.status === 'rejected' ? t : (m.status === 'rejected' ? m : null);
    const detail = rej && rej.reason instanceof ApiError ? rej.reason.message : '状态加载失败';
    setErr(`${failed.join(' / ')}加载失败：${detail}`);
  }, [loadTask]);

  useEffect(() => { void load(); }, [load]);

  const run = async (fn: (options?: RequestOptions) => Promise<unknown>,
                     okMsg: (r: unknown) => string) => {
    const ctrl = runTask.begin();
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await fn({ signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setMsg(okMsg(r));
      await load();
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '操作失败');
    } finally { if (runTask.finish(ctrl)) setBusy(false); }
  };

  const doImport = () => {
    if (!jsonText.trim()) { setErr('请先粘贴 JSON 文档数组'); return; }
    let docs: unknown;
    try { docs = JSON.parse(jsonText); }
    catch { setErr('JSON 解析失败'); return; }
    void run(
      () => datacenterApi.textImport(docs as object[]),
      (r) => `导入完成：${(r as { imported: number }).imported} 条新增，`
        + `${(r as { duplicates: number }).duplicates} 条重复`);
  };

  const mirrorRows = mirror
    ? Object.entries(mirror).filter(([k]) => k !== 'root') as
        Array<[string, { source_dates: number; mirror_dates: number;
                         source_last: string | null; mirror_last: string | null;
                         lag_days: number }]>
    : [];

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <Card title="文本数据（公告情绪 · FinLLM 前置）">
        <div className="space-y-2 text-xs">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-secondary">
            <span>公告文档 <b className="num text-ink">{status?.docs ?? '—'}</b> 条</span>
            <span>覆盖 <b className="num text-ink">{status?.doc_symbols ?? '—'}</b> 标的 /{' '}
              <b className="num text-ink">{status?.doc_dates ?? '—'}</b> 交易日</span>
            <span>情绪因子 <b className="num text-ink">{status?.factor_rows ?? '—'}</b> 行</span>
            <span title="公告中含业绩表述（超预期/预增/不及预期等）的非零因子行数">
              超预期事件 <b className="num text-ink">{status?.surprise_active_rows ?? '—'}</b> 行
            </span>
            <span>LLM <b className={status?.llm_enabled ? 'text-emerald-600' : 'text-ink-muted'}>
              {status?.llm_enabled ? '已配置' : '未配置（规则词库）'}</b></span>
          </div>
          <p className="text-2xs leading-relaxed text-ink-muted">
            ⓘ {status?.basis}
          </p>
          <p className="text-2xs leading-relaxed text-ink-muted">{status?.note}</p>
          <textarea
            rows={3} value={jsonText} disabled={busy}
            onChange={(e) => setJsonText(e.target.value)}
            placeholder={`粘贴公告 JSON 数组（格式如：\n${SAMPLE}）`}
            className={inputCls} />
          <div className="flex flex-wrap gap-2">
            {/* C-9：text/import 与 text/build-factor 后端要求 researcher */}
            {canResearch && (
              <>
                <button onClick={() => void doImport()} disabled={busy}
                  className="rounded-md border border-hair bg-white px-2.5 py-1 text-2xs
                    hover:border-brand-200 hover:text-brand-600 disabled:opacity-60">
                  导入文档
                </button>
                <button
                  onClick={() => void run(
                    (o) => datacenterApi.textBuildFactor(o),
                    (r) => `情绪因子已构建：${(r as { rows: number }).rows} 行`
                      + `（${(r as { method: string }).method}）`)}
                  disabled={busy}
                  className="rounded-md border border-hair bg-white px-2.5 py-1 text-2xs
                    hover:border-brand-200 hover:text-brand-600 disabled:opacity-60">
                  构建情绪因子
                </button>
              </>
            )}
            <button onClick={() => setJsonText(SAMPLE)} disabled={busy}
              className="rounded-md px-2.5 py-1 text-2xs text-ink-muted hover:text-ink">
              填充示例
            </button>
          </div>
        </div>
      </Card>

      <Card title="截面分区镜像（daily_bar 三口径 · 性能前置）">
        <div className="space-y-2 text-xs">
          <table className="w-full text-2xs">
            <thead>
              <tr className="text-left text-ink-muted">
                <th className="font-normal">数据集</th>
                <th className="text-right font-normal">源日期</th>
                <th className="text-right font-normal">镜像日期</th>
                <th className="text-right font-normal">源最新</th>
                <th className="text-right font-normal">滞后</th>
              </tr>
            </thead>
            <tbody className="num text-ink-secondary">
              {mirrorRows.map(([ds, st]) => (
                <tr key={ds} className="border-t border-hair">
                  <td className="py-1 font-mono">{ds}</td>
                  <td className="text-right">{st.source_dates}</td>
                  <td className="text-right">{st.mirror_dates}</td>
                  <td className="text-right">{st.source_last ?? '—'}</td>
                  <td className={`text-right ${st.lag_days > 0 ? 'text-amber-600' : 'text-emerald-600'}`}>
                    {st.lag_days} 天
                  </td>
                </tr>
              ))}
              {!mirrorRows.length && (
                <tr><td colSpan={5} className="py-4 text-center text-ink-muted">加载中…</td></tr>
              )}
            </tbody>
          </table>
          <p className="text-2xs leading-relaxed text-ink-muted">
            镜像按交易日分区（date=YYYYMMDD），扩池后截面查询 O(1) 文件；
            晚间例行 17:30 自动增量重建，此处为手动兜底。
          </p>
          {/* C-9：mirror/rebuild 后端要求 researcher */}
          {canResearch && (
            <button
              onClick={() => void run(
                (o) => datacenterApi.mirrorRebuild(o),
                (r) => `重建完成：${(r as Array<{ built: number }>).reduce((a, b) => a + b.built, 0)} 个日期`)}
              disabled={busy}
              className="rounded-md border border-hair bg-white px-2.5 py-1 text-2xs
                hover:border-brand-200 hover:text-brand-600 disabled:opacity-60">
              {busy ? '处理中…' : '增量重建镜像'}
            </button>
          )}
        </div>
      </Card>

      {msg && (
        <div className="rounded-md bg-emerald-50 px-3 py-2 text-2xs text-emerald-700 lg:col-span-2">
          {msg}
        </div>
      )}
      {err && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-2xs text-red-600 lg:col-span-2">
          {err}
        </div>
      )}
    </div>
  );
}
