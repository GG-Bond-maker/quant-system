/** 因子自动化挖掘工作室：真实 GP 进化（语法生成 + RankIC 适应度）。 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';

import { ApiError } from '@/api/client';
import { studioApi, type AlphaEvalResult, type GpStatus } from '@/api/production';
import { SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import NlFactorCard from './NlFactorCard';
import FactorLab from './FactorLab';
import { useChart } from '@/utils/useChart';

const ALL_FIELDS = ['ret_1', 'ret_3', 'ret_5', 'ret_10', 'ret_20', 'ret_60',
  'vol_5', 'vol_10', 'vol_20', 'vol_60', 'ma_gap_5', 'ma_gap_20', 'ma_gap_60',
  'rsi_6', 'rsi_14', 'boll_pos', 'skew_ret_20', 'kurt_ret_20', 'v_rank_20',
  'atr_14', 'hl_range', 'ma_slope_20'];

/** 字段含义速查（hover title）—— 让用户知道每个缩写代表什么。 */
const FIELD_HINTS: Record<string, string> = {
  ret_1: '1 日收益率', ret_3: '3 日收益率', ret_5: '5 日收益率',
  ret_10: '10 日收益率', ret_20: '20 日收益率', ret_60: '60 日收益率',
  vol_5: '5 日波动率', vol_10: '10 日波动率', vol_20: '20 日波动率', vol_60: '60 日波动率',
  ma_gap_5: '5 日均线偏离度', ma_gap_20: '20 日均线偏离度', ma_gap_60: '60 日均线偏离度',
  rsi_6: '6 日 RSI', rsi_14: '14 日 RSI',
  boll_pos: '布林带位置（0=下轨，1=上轨）',
  skew_ret_20: '20 日收益偏度', kurt_ret_20: '20 日收益峰度',
  v_rank_20: '20 日成交量分位',
  atr_14: '14 日 ATR', hl_range: '日内振幅', ma_slope_20: '20 日均线斜率',
};

const HISTORY_KEY = 'aqp:gp:history';
const inputCls =
  'w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';

interface HistoryEntry {
  task_id: string;
  params: Record<string, unknown>;
  started_at: string;
  status: string;
}

export default function FactorStudio() {
  const [fields, setFields] = useState(['ret_5', 'vol_20', 'rsi_14']);
  const [population, setPopulation] = useState(20);
  const [generations, setGenerations] = useState(6);
  const [horizon, setHorizon] = useState(5);
  const [seed, setSeed] = useState(42);
  const [status, setStatus] = useState<GpStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [taskLost, setTaskLost] = useState<string | null>(null); // 任务失效（40400）
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [copiedExpr, setCopiedExpr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  // 表达式评估（/studio/alpha-eval）：结果面板 + 按行 busy
  const [evalResult, setEvalResult] = useState<AlphaEvalResult | null>(null);
  const [evalBusy, setEvalBusy] = useState<string | null>(null);
  const [evalErr, setEvalErr] = useState<string | null>(null);
  // P2-4：/studio/alpha-eval 真实截面评估 120s，卸载（切路由）时中断在途请求
  const evalTask = useAbortableTask();

  /* ---------- 历史持久化（localStorage，最近 10 条） ---------- */
  useEffect(() => {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      if (raw) setHistory(JSON.parse(raw));
    } catch { /* ignore */ }
  }, []);

  const persistHistory = (h: HistoryEntry[]) => {
    setHistory(h);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(h)); } catch { /* ignore */ }
  };

  const poll = useCallback(async (taskId: string) => {
    try {
      const st = await studioApi.miningStatus(taskId);
      setStatus(st);
      // 同步历史状态
      setHistory((prev) => {
        const next = prev.map((h) => h.task_id === taskId ? { ...h, status: st.status } : h);
        try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)); } catch { /* ignore */ }
        return next;
      });
      if (st.status === 'DONE' || st.status === 'FAILED' || st.status === 'CANCELLED') {
        setRunning(false);
        if (timer.current) clearInterval(timer.current);
      }
    } catch (e) {
      // 任务失效（服务重启 / task_id 过期）：引导用户重启
      // ERR_DATA_EMPTY=51001（本地无数据）或 ERR_NOT_FOUND=40400 均视为任务失效
      if (e instanceof ApiError && (e.code === 51001 || e.code === 40400)) {
        setTaskLost(taskId);
        setHistory((prev) => {
          const next = prev.filter((h) => h.task_id !== taskId);
          try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)); } catch { /* ignore */ }
          return next;
        });
      } else {
        setErr(e instanceof ApiError ? e.message : '状态查询失败');
      }
      setRunning(false);
      if (timer.current) clearInterval(timer.current);
    }
  }, []);

  const start = useCallback(async () => {
    setErr(null); setRunning(true); setStatus(null); setTaskLost(null);
    try {
      const res = await studioApi.startMining(
        { fields, horizon, population, generations, seed });
      const entry: HistoryEntry = {
        task_id: res.task_id,
        params: { fields, horizon, population, generations, seed },
        started_at: new Date().toISOString(),
        status: 'RUNNING',
      };
      persistHistory([entry, ...history].slice(0, 10));
      timer.current = setInterval(() => void poll(res.task_id), 2000);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '启动失败');
      setRunning(false);
    }
  }, [fields, horizon, population, generations, seed, poll, history]);

  /** 从历史恢复轮询（点击"查看"）。 */
  const resumeTask = useCallback((taskId: string) => {
    setErr(null); setTaskLost(null); setRunning(true); setStatus(null);
    timer.current = setInterval(() => void poll(taskId), 2000);
  }, [poll]);

  /** 复制表达式到剪贴板。 */
  const copyExpr = useCallback(async (expr: string) => {
    try {
      await navigator.clipboard.writeText(expr);
      setCopiedExpr(expr);
      setTimeout(() => setCopiedExpr(null), 1500);
    } catch { /* ignore */ }
  }, []);

  /** 评估单个表达式（Rank IC 时序 + 多空净值；horizon 沿用左侧配置）。 */
  const runEval = useCallback(async (expr: string) => {
    const ctrl = evalTask.begin();
    setEvalBusy(expr); setEvalErr(null);
    try {
      const r = await studioApi.alphaEval({ expr, horizon }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setEvalResult(r);
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setEvalErr(e instanceof ApiError ? e.message : '评估失败');
    } finally { if (evalTask.finish(ctrl)) setEvalBusy(null); }
  }, [horizon, evalTask]);

  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  /** 取消当前运行中的任务（优雅：当前代完成后退出）。 */
  const cancelRunning = useCallback(async () => {
    const tid = status?.task_id;
    if (!tid) return;
    try { await studioApi.cancelMining(tid); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '取消请求失败'); }
  }, [status]);

  // 双 Y 轴：左 fitness（复合适应度）· 右 MeanIC（真实 IC 水平）
  const option = status?.fitness_curve?.length
    ? ({
        grid: { left: 48, right: 56, top: 28, bottom: 24 },
        tooltip: { trigger: 'axis' },
        legend: { data: ['Fitness', 'MeanIC'], top: 0, textStyle: { fontSize: 10 } },
        xAxis: { type: 'category',
                 data: status.fitness_curve.map((_, i) => `Gen ${i + 1}`),
                 axisLabel: { fontSize: 9 } },
        yAxis: [
          { type: 'value', scale: true, name: 'Fitness', nameTextStyle: { fontSize: 9 },
            axisLabel: { fontSize: 9, formatter: (v: number) => v.toFixed(3) } },
          { type: 'value', scale: true, name: 'MeanIC', nameTextStyle: { fontSize: 9 },
            axisLabel: { fontSize: 9, formatter: (v: number) => v.toFixed(4) },
            splitLine: { show: false } },
        ],
        series: [
          { name: 'Fitness', type: 'line', data: status.fitness_curve,
            lineStyle: { width: 1.6, color: '#2563EB' },
            areaStyle: { opacity: 0.08 } },
          { name: 'MeanIC', type: 'line', yAxisIndex: 1,
            data: status.mean_ic_curve ?? [],
            lineStyle: { width: 1.4, color: '#DC2626' },
            symbol: 'circle', symbolSize: 4 },
        ],
      } as echarts.EChartsOption)
    : null;
  const chartRef = useChart(option);

  // 表达式评估：上 = 逐日 Rank IC 折线；下 = 多空分组累计净值
  const evalIcOption = evalResult
    ? ({
        grid: { left: 48, right: 16, top: 20, bottom: 24 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: evalResult.ic_series.map((p) => p.date),
                 axisLabel: { fontSize: 9 } },
        yAxis: { type: 'value', scale: true,
                 axisLabel: { fontSize: 9, formatter: (v: number) => v.toFixed(3) } },
        series: [{ name: 'Rank IC', type: 'line', symbol: 'none',
                   data: evalResult.ic_series.map((p) => p.ic),
                   lineStyle: { width: 1.2, color: '#2563EB' },
                   markLine: { symbol: 'none', data: [{ yAxis: 0 }],
                               lineStyle: { color: '#94A3B8', type: 'dashed', width: 1 },
                               label: { show: false } } }],
      } as echarts.EChartsOption)
    : null;
  const evalIcRef = useChart(evalIcOption);

  const evalNavOption = evalResult
    ? ({
        grid: { left: 48, right: 16, top: 28, bottom: 24 },
        tooltip: { trigger: 'axis' },
        legend: { data: ['多头', '空头', '多空'], top: 0, textStyle: { fontSize: 10 } },
        xAxis: { type: 'category',
                 data: evalResult.long_short_nav.map((p) => p.date),
                 axisLabel: { fontSize: 9 } },
        yAxis: { type: 'value', scale: true,
                 axisLabel: { fontSize: 9, formatter: (v: number) => v.toFixed(2) } },
        series: [
          { name: '多头', type: 'line', symbol: 'none',
            data: evalResult.long_nav.map((p) => p.nav),
            lineStyle: { width: 1.4, color: '#DC2626' } },
          { name: '空头', type: 'line', symbol: 'none',
            data: evalResult.short_nav.map((p) => p.nav),
            lineStyle: { width: 1.4, color: '#059669' } },
          { name: '多空', type: 'line', symbol: 'none',
            data: evalResult.long_short_nav.map((p) => p.nav),
            lineStyle: { width: 1.6, color: '#2563EB' } },
        ],
      } as echarts.EChartsOption)
    : null;
  const evalNavRef = useChart(evalNavOption);

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold text-ink">因子自动化挖掘工作室</h1>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}
      {taskLost && (
        <div className="flex items-center justify-between rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700">
          <span>任务 {taskLost.slice(0, 8)}… 已失效（服务重启或任务过期），无法继续轮询。</span>
          <button onClick={() => void start()} disabled={running || fields.length < 2}
                  className="rounded-md bg-amber-600 px-2.5 py-1 text-2xs font-medium text-white hover:bg-amber-700 disabled:opacity-60">
            重新启动挖掘
          </button>
        </div>
      )}

      {/* NL-to-Factor（1-1）：自然语言生成因子，白名单校验 + 真实 RankIC 评估 */}
      <NlFactorCard />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        <SectionCard title="挖掘任务配置" bodyClassName="p-3 space-y-3" className="xl:col-span-4">
          <div>
            <div className="mb-1 text-2xs text-ink-secondary">参与挖掘的因子字段（真实 features 列，2~6 个 · hover 看含义）</div>
            <div className="flex max-h-40 flex-wrap gap-1 overflow-auto">
              {ALL_FIELDS.map((f) => (
                <button key={f} title={FIELD_HINTS[f] ?? f}
                        onClick={() => setFields((prev) =>
                          prev.includes(f) ? prev.filter((x) => x !== f)
                            : prev.length < 6 ? [...prev, f] : prev)}
                        className={`rounded px-1.5 py-0.5 font-mono text-2xs border ${
                          fields.includes(f)
                            ? 'border-brand-400 bg-brand-50 text-brand-700'
                            : 'border-hair text-ink-secondary hover:bg-slate-50'}`}>
                  {f}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 text-2xs text-ink-secondary">
            <label>种群规模
              <input type="number" min={10} max={80} value={population}
                     className={`${inputCls} mt-0.5`}
                     onChange={(e) => setPopulation(Number(e.target.value))} />
            </label>
            <label>进化代数
              <input type="number" min={3} max={20} value={generations}
                     className={`${inputCls} mt-0.5`}
                     onChange={(e) => setGenerations(Number(e.target.value))} />
            </label>
            <label>预测口径（日）
              <input type="number" min={1} max={20} value={horizon}
                     className={`${inputCls} mt-0.5`}
                     onChange={(e) => setHorizon(Number(e.target.value))} />
            </label>
            <label>随机种子
              <input type="number" value={seed} className={`${inputCls} mt-0.5`}
                     onChange={(e) => setSeed(Number(e.target.value))} />
            </label>
          </div>
          {running ? (
            <button onClick={() => void cancelRunning()}
                    className="w-full rounded-md bg-red-600 py-2 text-xs font-medium text-white hover:bg-red-700">
              取消任务（当前代完成后退出）
            </button>
          ) : (
            <button disabled={fields.length < 2}
                    onClick={() => void start()}
                    className="w-full rounded-md bg-brand-500 py-2 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
              启动挖掘任务
            </button>
          )}
          <p className="text-2xs text-ink-muted">
            适应度 = |日均 Rank IC| − 复杂度惩罚（真实截面计算）；算子白名单来自表达式引擎，
            全部向后计算（PIT 安全）。任务在服务后台线程执行，重启后历史清空。
          </p>
        </SectionCard>

        <div className="space-y-3 xl:col-span-8">
          <SectionCard title="进化进度与适应度曲线" bodyClassName="p-3 space-y-2">
            {status ? (
              <>
                <div className="flex items-center gap-3 text-2xs">
                  <span className={`rounded px-1.5 py-0.5 ${
                    status.status === 'DONE' ? 'bg-emerald-50 text-emerald-700'
                      : status.status === 'FAILED' ? 'bg-red-50 text-red-600'
                      : status.status === 'CANCELLED' ? 'bg-amber-50 text-amber-700'
                      : 'bg-brand-50 text-brand-700'}`}>{status.status}</span>
                  <span>代数 {status.generation}/{status.total_generations}</span>
                  <span>耗时 {status.elapsed_sec}s</span>
                  {status.status === 'RUNNING' && (
                    <div className="h-1.5 flex-1 rounded bg-slate-100">
                      <div className="h-1.5 rounded bg-brand-500"
                           style={{ width: `${(status.generation / Math.max(1, status.total_generations)) * 100}%` }} />
                    </div>
                  )}
                  {status.status === 'RUNNING' && (
                    <span className="text-ink-muted">（进度按代数计，不代表剩余时间）</span>
                  )}
                </div>
                <div ref={chartRef} className="h-40 w-full" />
                {status.error && <p className="text-2xs text-red-600">{status.error}</p>}
              </>
            ) : <div className="py-10 text-center text-xs text-ink-muted">尚无任务 —— 配置左侧参数并启动</div>}
          </SectionCard>

          <SectionCard title="新生成的 Alpha 表达式（按适应度排序）" bodyClassName="p-3">
            {status?.top_expressions?.length ? (
              <table className="w-full text-2xs">
                <thead>
                  <tr className="text-ink-secondary">
                    <th className="text-left font-medium">#</th>
                    <th className="text-left font-medium">表达式</th>
                    <th className="font-medium">Mean IC</th>
                    <th className="font-medium">ICIR</th>
                    <th className="font-medium">t 值</th>
                    <th className="font-medium">天数</th>
                    <th className="font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {status.top_expressions.map((e, i) => (
                    <tr key={e.expr + i} className="border-t border-hair">
                      <td className="py-1 text-ink-muted">{i + 1}</td>
                      <td className="py-1 font-mono">{e.expr}</td>
                      <td className={`num text-center ${e.mean_ic == null ? 'text-ink-muted'
                          : e.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                        {e.mean_ic != null ? e.mean_ic.toFixed(4) : '—'}</td>
                      <td className="num text-center">{e.icir != null ? e.icir.toFixed(3) : '—'}</td>
                      <td className="num text-center">{e.t_stat != null ? e.t_stat.toFixed(2) : '—'}</td>
                      <td className="num text-center">{e.n_days ?? '—'}</td>
                      <td className="text-center">
                        <div className="flex items-center justify-center gap-1">
                          <button onClick={() => void runEval(e.expr)}
                                  disabled={evalBusy != null}
                                  className="rounded border border-hair px-1.5 py-0.5 text-2xs hover:bg-slate-50 disabled:opacity-60">
                            {evalBusy === e.expr ? '评估中…' : '评估'}
                          </button>
                          <button onClick={() => void copyExpr(e.expr)}
                                  className="rounded border border-hair px-1.5 py-0.5 text-2xs hover:bg-slate-50">
                            {copiedExpr === e.expr ? '已复制' : '复制'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="py-6 text-center text-xs text-ink-muted">等待任务完成…</div>}
          </SectionCard>

          {/* 表达式评估结果（点击行内「评估」后展开） */}
          {(evalResult || evalErr) && (
            <SectionCard title="表达式评估（Rank IC 时序 + 多空分组净值）" bodyClassName="p-3 space-y-2">
              {evalErr && (
                <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{evalErr}</div>
              )}
              {evalResult && (
                <>
                  <div className="font-mono text-2xs text-ink-secondary">{evalResult.expr}</div>
                  <div className="grid grid-cols-4 gap-2 text-2xs">
                    <div className="rounded-lg border border-hair bg-white px-3 py-2">
                      <div className="text-ink-secondary">Mean IC</div>
                      <div className={`num text-base font-semibold ${evalResult.mean_ic >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                        {evalResult.mean_ic.toFixed(4)}</div>
                    </div>
                    <div className="rounded-lg border border-hair bg-white px-3 py-2">
                      <div className="text-ink-secondary">ICIR</div>
                      <div className="num text-base font-semibold">{evalResult.icir.toFixed(3)}</div>
                    </div>
                    <div className="rounded-lg border border-hair bg-white px-3 py-2">
                      <div className="text-ink-secondary">t 值</div>
                      <div className="num text-base font-semibold">{evalResult.t_stat.toFixed(2)}</div>
                    </div>
                    <div className="rounded-lg border border-hair bg-white px-3 py-2">
                      <div className="text-ink-secondary">IC 天数</div>
                      <div className="num text-base font-semibold">{evalResult.n_days}</div>
                    </div>
                  </div>
                  <div className="text-2xs font-medium text-ink-secondary">逐日截面 Rank IC</div>
                  <div ref={evalIcRef} className="h-36 w-full" />
                  <div className="text-2xs font-medium text-ink-secondary">多空分组累计净值</div>
                  <div ref={evalNavRef} className="h-36 w-full" />
                  <p className="text-2xs text-ink-muted">
                    覆盖 {evalResult.n_symbols} 只标的 · IC = 因子值与 {evalResult.horizon} 日前向收益的截面
                    Rank 相关（含未来 {evalResult.horizon} 日，仅研究口径）· 多空分组 = 按因子值
                    Top/Bottom 20% 等权、日度再平衡（无费用）。
                  </p>
                </>
              )}
            </SectionCard>
          )}

          {/* 因子库 + 因子检测报告（§4.3，Sprint4） */}
          <FactorLab initialExpr={evalResult?.expr ?? null} horizon={horizon} />
        </div>
      </div>

      {/* 任务历史（localStorage 持久化，最近 10 条） */}
      {history.length > 0 && (
        <SectionCard title="任务历史（本地保存，最近 10 条 · 点击「查看」恢复轮询）" bodyClassName="p-3">
          <div className="max-h-40 overflow-auto">
            <table className="w-full text-2xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-ink-secondary">
                  <th className="text-left font-medium">task_id</th>
                  <th className="text-left font-medium">参数</th>
                  <th className="font-medium">启动时间</th>
                  <th className="font-medium">状态</th>
                  <th className="font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.task_id} className="border-t border-hair">
                    <td className="py-1 font-mono text-ink-muted">{h.task_id.slice(0, 8)}…</td>
                    <td className="py-1 font-mono text-ink-muted">
                      {`${(h.params.fields as string[])?.join(', ') ?? '—'} · pop=${h.params.population} gen=${h.params.generations} h=${h.params.horizon}`}
                    </td>
                    <td className="num text-center text-ink-muted">
                      {new Date(h.started_at).toLocaleString('zh-CN', { hour12: false })}
                    </td>
                    <td className="text-center">
                      <span className={`rounded px-1.5 py-0.5 ${
                        h.status === 'DONE' ? 'bg-emerald-50 text-emerald-700'
                          : h.status === 'FAILED' ? 'bg-red-50 text-red-600'
                          : h.status === 'CANCELLED' ? 'bg-amber-50 text-amber-700'
                          : 'bg-brand-50 text-brand-700'}`}>{h.status}</span>
                    </td>
                    <td className="text-center">
                      <button onClick={() => resumeTask(h.task_id)} disabled={running}
                              className="rounded border border-hair px-1.5 py-0.5 text-2xs hover:bg-slate-50 disabled:opacity-60">
                        查看
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}
    </div>
  );
}
