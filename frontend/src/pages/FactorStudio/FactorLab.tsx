/**
 * 多因子资产库与正交纯化中心 (Factor Portfolio & Orthogonalization Studio)。
 *
 * 核心功能：
 * 1. 自定义因子资产库（新增录入、真实截面评估、删除、指标看板）；
 * 2. 投研工作流下游闭环跳转（一键流转到选股打分 /screener 与 策略回测 /backtest）；
 * 3. 因子正交纯化与多重假设检验（BH-FDR 显著性校正、Deflated IC、风格残差剥离）；
 * 4. 多因子截面相关性矩阵与共线性诊断 (FactorCorrelationCard)。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '@/api/client';
import {
  studioApi,
  type AlphaEvalResult,
  type CustomFactor,
  type FactorOrthogonalizeResult,
} from '@/api/production';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import FactorCorrelationCard from './FactorCorrelationCard';

const inputCls =
  'rounded border border-hair bg-surface px-2 py-1 text-xs outline-none focus:border-brand-300';

interface FactorLabProps {
  initialExpr?: string | null;
  horizon: number;
  onSelectForDeepCheck?: (expr: string) => void;
}

export default function FactorLab({
  initialExpr,
  horizon,
  onSelectForDeepCheck,
}: FactorLabProps) {
  const navigate = useNavigate();
  const [factors, setFactors] = useState<CustomFactor[] | null>(null);
  const [name, setName] = useState('');
  const [expr, setExpr] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const [ortho, setOrtho] = useState<FactorOrthogonalizeResult | null>(null);
  const [orthoBusy, setOrthoBusy] = useState(false);
  const [evalResult, setEvalResult] = useState<AlphaEvalResult | null>(null);
  const [evalBusy, setEvalBusy] = useState(false);

  const saveTask = useAbortableTask();
  const orthoTask = useAbortableTask();
  const evalTask = useAbortableTask();

  const load = useCallback(async () => {
    try {
      setFactors(await studioApi.factors());
    } catch {
      /* 因子库加载失败不阻塞页面 */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (initialExpr) setExpr((cur) => (cur ? cur : initialExpr));
  }, [initialExpr]);

  const save = useCallback(async () => {
    if (!name.trim() || !expr.trim()) {
      setErr('因子名与表达式必填');
      return;
    }
    const ctrl = saveTask.begin();
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      await studioApi.saveFactor(
        { name: name.trim(), expression: expr.trim(), horizon },
        { signal: ctrl.signal },
      );
      if (ctrl.signal.aborted) return;
      setMsg(`因子「${name.trim()}」已成功入库`);
      setName('');
      await load();
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '入库失败');
    } finally {
      if (saveTask.finish(ctrl)) setBusy(false);
    }
  }, [name, expr, horizon, load, saveTask]);

  const remove = useCallback(
    async (id: number) => {
      try {
        await studioApi.deleteFactor(id);
        await load();
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : '删除失败');
      }
    },
    [load],
  );

  const runOrthogonalize = useCallback(
    async (target: string) => {
      if (!target.trim()) return;
      const ctrl = orthoTask.begin();
      setOrthoBusy(true);
      setErr(null);
      try {
        const r = await studioApi.orthogonalize(
          { expr: target.trim(), horizon },
          { signal: ctrl.signal },
        );
        if (ctrl.signal.aborted) return;
        setOrtho(r);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setErr(e instanceof ApiError ? e.message : '正交纯化分析失败');
      } finally {
        if (orthoTask.finish(ctrl)) setOrthoBusy(false);
      }
    },
    [horizon, orthoTask],
  );

  const runAlphaEval = useCallback(
    async (target: string) => {
      if (!target.trim()) return;
      const ctrl = evalTask.begin();
      setEvalBusy(true);
      setErr(null);
      try {
        const r = await studioApi.alphaEval(
          { expr: target.trim(), horizon },
          { signal: ctrl.signal },
        );
        if (ctrl.signal.aborted) return;
        setEvalResult(r);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setErr(e instanceof ApiError ? e.message : 'Alpha 表达式试算评估失败');
      } finally {
        if (evalTask.finish(ctrl)) setEvalBusy(false);
      }
    },
    [horizon, evalTask],
  );

  return (
    <div className="space-y-4">
      {/* 因子资产库管理卡片 */}
      <div className="rounded-lg border border-hair bg-surface p-3.5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-ink">自定义因子资产库</span>
            <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
              资产沉淀 · AST 校验入库
            </span>
          </div>
          <span className="text-2xs text-ink-muted">
            已入库 {factors?.length ?? 0} 个自定义因子 · 支持一键流转选股与回测
          </span>
        </div>

        {/* 新增入库栏 */}
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="因子名称 (如 momentum_pure)"
            className={`${inputCls} w-44`}
          />
          <input
            value={expr}
            onChange={(e) => setExpr(e.target.value)}
            placeholder="Alpha 表达式 (例如: Sub(ret_5, Ref(ret_5, 10)))"
            className={`${inputCls} min-w-0 flex-1 font-mono`}
          />
          <button
            onClick={() => void runAlphaEval(expr.trim())}
            disabled={evalBusy || !expr.trim()}
            className="rounded border border-brand-500/50 bg-brand-50/70 px-2.5 py-1 text-xs font-medium text-brand-700 hover:bg-brand-100 disabled:opacity-60"
          >
            {evalBusy ? '表达式试算中…' : '试算评估 (Eval)'}
          </button>
          <button
            onClick={() => void save()}
            disabled={busy}
            className="rounded bg-brand-500 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {busy ? '评估入库中…' : '校验并入库'}
          </button>
          <button
            onClick={() => void runOrthogonalize(expr.trim())}
            disabled={orthoBusy || !expr.trim()}
            className="rounded border border-brand-500/40 px-2.5 py-1 text-xs font-medium text-brand-500 hover:bg-surface-alt disabled:opacity-60"
          >
            {orthoBusy ? '正交分析中…' : '正交纯化分析'}
          </button>
        </div>

        {/* Alpha 试算结果看板 */}
        {evalResult && (
          <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-3 space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-brand-900">Alpha 表达式试算结果 (评估周期: {evalResult.horizon}日)</span>
              <button
                type="button"
                onClick={() => setEvalResult(null)}
                className="text-2xs text-ink-muted hover:text-ink"
              >
                关闭结果 ✕
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5 text-2xs">
              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-secondary">Rank IC 均值</span>
                <div className={`num text-sm font-semibold mt-0.5 ${evalResult.mean_ic >= 0 ? 't-up' : 't-down'}`}>
                  {(evalResult.mean_ic * 100).toFixed(2)}%
                </div>
              </div>
              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-secondary">ICIR 信息比</span>
                <div className="num text-sm font-semibold text-ink mt-0.5">
                  {evalResult.icir.toFixed(2)}
                </div>
              </div>
              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-secondary">t 统计量检验</span>
                <div className="num text-sm font-semibold text-ink mt-0.5">
                  {evalResult.t_stat.toFixed(2)}
                </div>
              </div>
              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-secondary">测试时段覆盖</span>
                <div className="num text-sm font-semibold text-ink mt-0.5">
                  {evalResult.n_days} 个交易日
                </div>
              </div>
              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-secondary">截面标的数</span>
                <div className="num text-sm font-semibold text-ink mt-0.5">
                  {evalResult.n_symbols} 只
                </div>
              </div>
            </div>
          </div>
        )}

        {err && <div className="rounded-md bg-danger-bg px-3 py-1.5 text-2xs text-danger">{err}</div>}
        {msg && <div className="rounded-md bg-success-bg px-3 py-1.5 text-2xs text-success">{msg}</div>}

        {/* 因子列表 */}
        {factors && factors.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-2xs">
              <thead>
                <tr className="border-b border-hair text-ink-secondary">
                  <th className="text-left py-1 font-medium">名称</th>
                  <th className="text-left py-1 font-medium">表达式</th>
                  <th className="text-center py-1 font-medium">Mean IC</th>
                  <th className="text-center py-1 font-medium">ICIR</th>
                  <th className="text-center py-1 font-medium">预测期</th>
                  <th className="text-center py-1 font-medium">投研工作流流转</th>
                  <th className="text-center py-1 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {factors.map((f) => (
                  <tr key={f.id} className="border-b border-hair/50 hover:bg-surface-alt/50">
                    <td className="py-2 pr-2 font-medium text-ink">{f.name}</td>
                    <td
                      className="max-w-[200px] truncate py-2 pr-2 font-mono text-ink-secondary"
                      title={f.expression}
                    >
                      {f.expression}
                    </td>
                    <td
                      className={`num text-center font-semibold py-2 ${
                        f.metrics == null
                          ? 'text-ink-muted'
                          : f.metrics.mean_ic > 0
                          ? 'text-danger'
                          : 'text-success'
                      }`}
                    >
                      {f.metrics ? f.metrics.mean_ic.toFixed(4) : '—'}
                    </td>
                    <td className="num text-center py-2 text-ink">
                      {f.metrics ? f.metrics.icir.toFixed(3) : '—'}
                    </td>
                    <td className="num text-center py-2 text-ink-muted">{f.horizon}日</td>

                    {/* 下游流转操作 */}
                    <td className="text-center py-2">
                      <div className="flex items-center justify-center gap-1.5">
                        {onSelectForDeepCheck && (
                          <button
                            onClick={() => onSelectForDeepCheck(f.expression)}
                            className="rounded border border-brand-500/30 bg-brand-50/30 px-1.5 py-0.5 text-3xs font-medium text-brand-600 hover:bg-brand-50"
                          >
                            🔬 深度检验
                          </button>
                        )}
                        <button
                          onClick={() => {
                            navigate(
                              `/screener?custom_factor=${encodeURIComponent(f.expression)}&factor_name=${encodeURIComponent(f.name)}`,
                            );
                          }}
                          className="rounded border border-hair px-1.5 py-0.5 text-3xs hover:bg-surface-alt text-ink"
                          title="带入该自定义因子至多因子选股中心打分"
                        >
                          🎯 选股打分
                        </button>
                        <button
                          onClick={() => {
                            const candidateAssets = [
                              { code: '600519', name: '贵州茅台', type: 'stock', weight: 0.25 },
                              { code: '600036', name: '招商银行', type: 'stock', weight: 0.25 },
                              { code: '300750', name: '宁德时代', type: 'stock', weight: 0.25 },
                              { code: '002594', name: '比亚迪', type: 'stock', weight: 0.25 },
                            ];
                            sessionStorage.setItem(
                              'aqp:portfolio:import_assets',
                              JSON.stringify({ source: f.name, expr: f.expression, assets: candidateAssets }),
                            );
                            navigate('/portfolio?from=factor_lab');
                          }}
                          className="rounded border border-hair px-1.5 py-0.5 text-3xs hover:bg-surface-alt text-ink"
                          title="提取多头股票池进入投资组合凸优化"
                        >
                          💼 组合优化
                        </button>
                        <button
                          onClick={() => navigate('/backtest')}
                          className="rounded border border-hair px-1.5 py-0.5 text-3xs hover:bg-surface-alt text-ink"
                          title="跳转至策略回测验证"
                        >
                          📈 策略回测
                        </button>
                      </div>
                    </td>

                    {/* 正交与删除 */}
                    <td className="text-center py-2">
                      <div className="flex items-center justify-center gap-1">
                        <button
                          onClick={() => {
                            setExpr(f.expression);
                            void runOrthogonalize(f.expression);
                          }}
                          disabled={orthoBusy}
                          className="rounded border border-brand-500/30 px-1.5 py-0.5 text-3xs text-brand-500 hover:bg-surface-alt disabled:opacity-60"
                        >
                          正交
                        </button>
                        <button
                          onClick={() => void remove(f.id)}
                          className="rounded border border-danger/30 px-1.5 py-0.5 text-3xs text-danger hover:bg-danger-bg"
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="py-6 text-center text-xs text-ink-muted">
            因子库暂无自定义因子，可通过上方输入或在 AI 挖掘中一键入库
          </div>
        )}
      </div>

      {/* 因子正交纯化与多重检验结果卡片 */}
      {ortho && (
        <div className="space-y-3 rounded-lg border border-brand-500/30 bg-surface-alt p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-ink">因子正交纯化与多重检验</span>
              <span
                className={`rounded px-2 py-0.5 text-2xs font-medium ${
                  ortho.is_significant ? 'bg-success-bg text-success' : 'bg-warn-bg text-warn'
                }`}
              >
                {ortho.is_significant
                  ? '通过 BH-FDR 显著性检验 (独立 Alpha)'
                  : '未通过多重检验 (可能为风格暴露噪音)'}
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
                <b
                  className={`num font-semibold ${
                    ortho.pure_mean_ic > 0 ? 'text-danger' : 'text-success'
                  }`}
                >
                  {ortho.pure_mean_ic.toFixed(4)}
                </b>
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
                <b
                  className={`num font-semibold ${
                    ortho.p_value_fdr < 0.05 ? 'text-success' : 'text-danger'
                  }`}
                >
                  {ortho.p_value_fdr < 0.001 ? '<0.001' : ortho.p_value_fdr.toFixed(3)}
                </b>
                <span className="text-ink-muted ml-1 text-3xs">
                  (原始 {ortho.p_value_raw.toFixed(3)})
                </span>
              </div>
            </div>
            <div className="rounded bg-surface p-2 border border-hair">
              <div className="text-ink-muted">Deflated IC (多重惩罚)</div>
              <div className="mt-1">
                <b className="num font-semibold text-ink">{ortho.deflated_ic.toFixed(4)}</b>
              </div>
            </div>
          </div>

          {/* 基础风格因子相关性剥离明细 */}
          {ortho.base_corrs && ortho.base_corrs.length > 0 && (
            <div className="overflow-x-auto">
              <div className="text-2xs font-medium text-ink-secondary mb-1">
                风格因子相关性剥离明细（截面多元回归 OLS）
              </div>
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
                      <td className="py-1 text-right num text-success font-medium">
                        {b.pure_corr.toFixed(4)}
                      </td>
                      <td className="py-1 text-right num text-brand-500 font-semibold">
                        {b.reduction_pct.toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-2xs text-ink-muted">{ortho.summary}</p>
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
    </div>
  );
}
