/**
 * 模型训练面板（数据前置的消费端：数据抓取到位后一键启动训练）。
 *
 * - 就绪度门禁清单（torch / features / 样本量 / 关系边），不满足时按钮禁用
 *   并给原因——数据还没抓够就明确提示"先抓数据"，绝不静默开训；
 * - 单任务串行：启动后 2s 轮询状态与日志，支持优雅取消（逐 epoch 检查）；
 * - 上次训练结果（valid/test RankIC）落库持久化，进程重启后仍可见。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/client';
import { useTaskPolling } from '@/hooks/useTaskPolling';
import {
  datacenterApi,
  type TrainReadiness,
  type TrainStatus,
} from '@/api/datacenter';

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

const MODELS = [
  { id: 'tft' as const, label: 'TFT 时序模型', desc: 'SeqTransformer 序列回归（无需关系边）' },
  { id: 'gnn' as const, label: 'GNN 产业链传导', desc: '裸 GCN 图卷积（需关系边 > 0）' },
] as const;

function GateDot({ ok: gateOk }: { ok: boolean }) {
  return (
    <span className={`inline-block size-1.5 rounded-full ${gateOk ? 'bg-emerald-500' : 'bg-red-400'}`} />
  );
}

export default function TrainPanel() {
  const [readiness, setReadiness] = useState<TrainReadiness | null>(null);
  const { data: status, error: statusError, mutate: refreshStatus } =
    useTaskPolling<TrainStatus>('/api/v1/datacenter/train/status');
  const [model, setModel] = useState<'tft' | 'gnn'>('tft');
  const [epochs, setEpochs] = useState(30);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const loadReadiness = useCallback(async () => {
    try {
      setReadiness(await datacenterApi.trainReadiness());
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '状态加载失败');
    }
  }, []);

  useEffect(() => { void loadReadiness(); }, [loadReadiness]);
  useEffect(() => {
    if (statusError) setErr(statusError instanceof ApiError ? statusError.message : '训练状态加载失败');
  }, [statusError]);

  const running = status?.running ?? false;

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [status?.logs?.length]);

  const gate = model === 'tft' ? readiness?.tft_ready : readiness?.gnn_ready;
  const runningModel = running ? status?.model : null;

  const start = async () => {
    setBusy(true); setErr(null);
    try {
      await datacenterApi.trainStart(model, { epochs });
      await refreshStatus();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '启动失败');
    } finally { setBusy(false); }
  };

  const cancel = async () => {
    setBusy(true); setErr(null);
    try {
      await datacenterApi.trainCancel();
      await refreshStatus();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '取消失败');
    } finally { setBusy(false); }
  };

  const last = status?.last_result ?? null;
  const fmtIc = (v: number | undefined) =>
    v === undefined || Number.isNaN(v) ? '—' : v.toFixed(4);

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <Card title="模型训练（数据前置消费端 · 一键启动）">
        <div className="space-y-2 text-xs">
          {/* 就绪度门禁清单 */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-2xs text-ink-secondary">
            <span className="flex items-center gap-1.5">
              <GateDot ok={readiness?.torch_ready ?? false} /> PyTorch{readiness?.torch_ready ? '' : '（未安装）'}
            </span>
            {readiness?.torch_device?.device && (
              <span className="flex items-center gap-1.5" title="训练设备：xpu = Intel Arc 核显/独显加速，cpu = 处理器直算">
                设备 <b className="text-ink">{readiness.torch_device.device}</b>
                {readiness.torch_device.device_name && (
                  <span className="text-ink-muted">· {readiness.torch_device.device_name}</span>
                )}
              </span>
            )}
            <span className="flex items-center gap-1.5">
              <GateDot ok={readiness?.features.exists ?? false} /> features 已构建
            </span>
            <span className="flex items-center gap-1.5">
              <GateDot ok={readiness?.sample_ok ?? false} />
              预估样本 <b className="num text-ink">{readiness?.est_samples ?? '—'}</b>
              / {readiness?.min_samples ?? 5000}
            </span>
            <span className="flex items-center gap-1.5">
              <GateDot ok={(readiness?.gnn_edges ?? 0) > 0} />
              关系边 <b className="num text-ink">{readiness?.gnn_edges ?? '—'}</b>（GNN 用）
            </span>
          </div>
          {readiness && !readiness.features.exists && (
            <p className="text-2xs text-ink-muted">
              features 目录：{readiness.features.dir}（先在流水线执行 build_features）
            </p>
          )}

          {/* 模型选择 */}
          <div className="grid grid-cols-2 gap-2">
            {MODELS.map((m) => {
              const ready = m.id === 'tft' ? readiness?.tft_ready : readiness?.gnn_ready;
              return (
                <button key={m.id} disabled={running}
                  onClick={() => setModel(m.id)}
                  className={`rounded-md border px-2 py-1.5 text-left text-2xs transition-colors disabled:opacity-60 ${
                    model === m.id
                      ? 'border-brand-300 bg-brand-50 text-ink'
                      : 'border-hair bg-white text-ink-secondary hover:border-brand-200'
                  }`}>
                  <span className="font-semibold">{m.label}</span>
                  <span className={`ml-1 ${ready ? 'text-emerald-600' : 'text-ink-muted'}`}>
                    {ready ? '✓' : '未就绪'}
                  </span>
                  <div className="mt-0.5 text-2xs text-ink-muted">{m.desc}</div>
                </button>
              );
            })}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-2xs text-ink-secondary">
              epochs
              <input type="number" min={1} max={200} value={epochs} disabled={running}
                onChange={(e) => setEpochs(Math.max(1, Number(e.target.value) || 1))}
                className="w-16 rounded-md border border-hair bg-white px-1.5 py-1
                  font-mono text-2xs outline-none focus:border-brand-300" />
            </label>
            <button
              onClick={() => void start()}
              disabled={busy || running || gate !== true}
              title={gate !== true ? '就绪度门禁未满足' : undefined}
              className="rounded-md bg-brand-500 px-3 py-1 text-2xs font-medium text-white
                hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50">
              开始训练
            </button>
            <button onClick={() => void cancel()}
              disabled={busy || !running}
              className="rounded-md border border-hair bg-white px-2.5 py-1 text-2xs
                hover:border-red-300 hover:text-red-600 disabled:opacity-40">
              取消训练
            </button>
            {running && (
              <span className="text-2xs text-amber-600">
                {runningModel === 'tft' ? 'TFT' : 'GNN'} 训练中…
                {status?.cancelled ? '（取消请求已发出，等待当前 epoch 结束）' : ''}
              </span>
            )}
          </div>

          {gate !== true && !running && (
            <p className="text-2xs leading-relaxed text-ink-muted">
              ⓘ {readiness?.note ?? '前置条件未满足'}——先把数据抓够（扩池 ≥1000 只、
              5 年历史），再回来一键训练。
            </p>
          )}

          {/* 上次结果 */}
          {last && (
            <div className={`rounded-md px-2.5 py-2 text-2xs ${
              last.ok ? 'bg-emerald-50 text-emerald-700'
                : 'bg-red-50 text-red-600'}`}>
              {last.ok ? (
                <>上次训练：{last.model}/{last.version} · 样本{' '}
                  <b className="num">{last.samples ?? '—'}</b> · valid RankIC{' '}
                  <b className="num">{fmtIc(last.valid_rank_ic)}</b>
                  {last.test_rank_ic !== undefined &&
                    <> · test RankIC <b className="num">{fmtIc(last.test_rank_ic)}</b></>}
                </>
              ) : (
                <>上次训练未产出：{last.reason ?? last.error ?? '已取消'}</>
              )}
            </div>
          )}
          {status?.error && !running && (
            <div className="rounded-md bg-red-50 px-2.5 py-2 text-2xs text-red-600">
              训练异常：{status.error}
            </div>
          )}
        </div>
      </Card>

      <Card title={`训练日志${status?.model ? ` · ${status.model}` : ''}`}>
        <div ref={logRef}
          className="h-48 overflow-y-auto rounded-md bg-gray-50 p-2 font-mono
            text-2xs leading-relaxed text-ink-secondary">
          {status?.logs?.length
            ? status.logs.map((l, i) => (
                <div key={i} className="flex gap-2">
                  <span className="shrink-0 text-ink-muted">{l.ts}</span>
                  <span className="break-all">{l.message}</span>
                </div>
              ))
            : (
              <div className="py-8 text-center text-ink-muted">
                暂无日志——启动训练后此处实时滚动输出
              </div>
            )}
        </div>
      </Card>

      {err && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-2xs text-red-600 lg:col-span-2">
          {err}
        </div>
      )}
    </div>
  );
}
