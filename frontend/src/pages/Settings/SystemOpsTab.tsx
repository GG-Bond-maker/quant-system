import { useState } from 'react';
import { settingsApi, type AuditLogItem, type SystemStatus, type TelemetryData } from '@/api/settings';
import { SectionCard } from '@/components/ui';

interface Props {
  storageGb: number | null;
  system: SystemStatus | null;
  telemetry?: TelemetryData;
  auditLogs: AuditLogItem[];
  isAdmin: boolean;
  onClearCache: () => Promise<void>;
  onBackupDb: () => Promise<void>;
}

export function SystemOpsTab({
  storageGb,
  system,
  telemetry,
  auditLogs,
  isAdmin,
  onClearCache,
  onBackupDb,
}: Props) {
  const [confirmCacheModal, setConfirmCacheModal] = useState(false);
  const [backingUp, setBackingUp] = useState(false);
  const [currentTelemetry, setCurrentTelemetry] = useState<TelemetryData | undefined>(telemetry);
  const [refreshingTelemetry, setRefreshingTelemetry] = useState(false);

  const handleRefreshTelemetry = async () => {
    setRefreshingTelemetry(true);
    try {
      const data = await settingsApi.getTelemetry();
      setCurrentTelemetry(data);
    } catch {
      // 容错降级
    } finally {
      setRefreshingTelemetry(false);
    }
  };

  const handleBackup = async () => {
    setBackingUp(true);
    try {
      await onBackupDb();
    } finally {
      setBackingUp(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* 1. 存储与底层数据库状态 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <SectionCard title="数据库与持久化存储">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs text-ink-secondary">SQLite 业务数据库</div>
                <div className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-success">
                  <span className="h-2 w-2 rounded-full bg-success" />
                  正常运行 (WAL 模式)
                </div>
              </div>
              {isAdmin && (
                <button
                  type="button"
                  onClick={handleBackup}
                  disabled={backingUp}
                  className="rounded-md border border-hair bg-surface px-2.5 py-1 text-xs text-ink hover:bg-surface-alt disabled:opacity-50"
                >
                  {backingUp ? '备份中…' : '📦 备份数据库'}
                </button>
              )}
            </div>

            <div className="rounded-lg border border-hair bg-surface-alt p-2.5 space-y-1.5 text-2xs text-ink-secondary">
              <div className="flex justify-between">
                <span>Parquet 行情列存:</span>
                <span className="num font-semibold text-ink">{storageGb != null ? `${storageGb.toFixed(2)} GB` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span>磁盘水位占用:</span>
                <span className="num font-semibold text-ink">
                  {system?.disk?.usage_percent != null ? `${system.disk.usage_percent.toFixed(1)}%` : '—'}
                </span>
              </div>
            </div>
          </div>
        </SectionCard>

        {/* 2. 缓存体系与安全清理 */}
        <SectionCard title="Redis 缓存状态与持久键保护" className="lg:col-span-2">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs">
                {system?.cache ? (
                  system.cache.degraded ? (
                    <span className="rounded bg-warn-bg px-2 py-0.5 font-medium text-warn">
                      ⚠️ 缓存已降级为进程内 LRU (Redis 离线)
                    </span>
                  ) : system.cache.enabled ? (
                    <span className="rounded bg-success-bg px-2 py-0.5 font-medium text-success">
                      ● Redis 高速缓存就绪
                    </span>
                  ) : (
                    <span className="rounded bg-surface-sunken px-2 py-0.5 text-ink-muted">缓存未启用</span>
                  )
                ) : (
                  <span className="text-ink-muted">缓存状态探测中…</span>
                )}
              </div>

              {isAdmin && (
                <button
                  type="button"
                  onClick={() => setConfirmCacheModal(true)}
                  className="rounded-md border border-danger/30 bg-danger-bg/50 px-2.5 py-1 text-xs font-medium text-danger hover:bg-danger-bg"
                >
                  清理全站临时缓存
                </button>
              )}
            </div>

            <div className="rounded-lg border border-hair bg-surface-alt p-3 text-2xs text-ink-muted space-y-1 leading-relaxed">
              <div className="font-semibold text-ink-secondary">🛡️ 业务持久状态键隔离保护机制：</div>
              <div>
                系统清理缓存时已实施严格安全名单过滤，仅删除行情分片与临时特征缓存；核心业务快照（如 ETF 每日归档 `aqp:etf:snap:history`）绝不清除，防止数据丢失。
              </div>
            </div>
          </div>
        </SectionCard>
      </div>

      {/* 3. 运行环境算力探针 */}
      {(currentTelemetry || telemetry) && (
        <SectionCard
          title="量化执行内核与硬件算力探针 (Runtime Telemetry)"
          action={
            <button
              type="button"
              onClick={handleRefreshTelemetry}
              disabled={refreshingTelemetry}
              className="flex items-center gap-1 rounded border border-hair bg-surface px-2 py-0.5 text-2xs text-ink hover:bg-surface-alt disabled:opacity-50"
            >
              <span className={refreshingTelemetry ? 'animate-spin' : ''}>🔄</span>
              {refreshingTelemetry ? '探测中…' : '实时探活'}
            </button>
          }
        >
          {(() => {
            const tel = currentTelemetry || telemetry!;
            return (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                  <div className="text-3xs text-ink-muted">Python 运行时</div>
                  <div className="num mt-1 font-mono text-sm font-semibold text-ink">{tel.python_version}</div>
                </div>
                <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                  <div className="text-3xs text-ink-muted">Polars 向量引擎</div>
                  <div className="num mt-1 font-mono text-sm font-semibold text-ink">{tel.polars_version}</div>
                </div>
                <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                  <div className="text-3xs text-ink-muted">NumPy 线性代数</div>
                  <div className="num mt-1 font-mono text-sm font-semibold text-ink">{tel.numpy_version}</div>
                </div>
                <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                  <div className="text-3xs text-ink-muted">宿主 CPU 占用</div>
                  <div className="num mt-1 font-mono text-sm font-semibold text-ink">{tel.cpu_percent.toFixed(1)}%</div>
                </div>
                <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                  <div className="text-3xs text-ink-muted">内存使用情况</div>
                  <div className="num mt-1 font-mono text-sm font-semibold text-ink">
                    {tel.memory_used_gb} / {tel.memory_total_gb} GB
                  </div>
                </div>
                <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                  <div className="text-3xs text-ink-muted">服务器系统时间</div>
                  <div className="num mt-1 font-mono text-2xs font-semibold text-ink truncate">{tel.server_time}</div>
                </div>
              </div>
            );
          })()}
        </SectionCard>
      )}

      {/* 4. 系统敏感操作审计流水 */}
      <SectionCard title="系统配置与运维操作审计日志 (Audit Trail)">
        <div className="overflow-x-auto rounded-lg border border-hair">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-hair bg-surface-alt text-2xs text-ink-muted">
              <tr>
                <th className="px-3 py-2 font-medium">操作时间</th>
                <th className="px-3 py-2 font-medium">操作人</th>
                <th className="px-3 py-2 font-medium">运维动作</th>
                <th className="px-3 py-2 font-medium">影响与详情</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hair bg-surface">
              {auditLogs.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-6 text-center text-xs text-ink-muted">
                    暂无审计日志记录
                  </td>
                </tr>
              ) : (
                auditLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-surface-alt/50">
                    <td className="px-3 py-2 font-mono text-2xs text-ink-muted">{log.timestamp}</td>
                    <td className="px-3 py-2 font-medium text-ink">{log.operator}</td>
                    <td className="px-3 py-2 font-semibold text-brand-600 dark:text-brand-400">{log.action}</td>
                    <td className="px-3 py-2 text-2xs text-ink-secondary">{log.details || '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>

      {/* 清理缓存确认弹窗 */}
      {confirmCacheModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink/40" onClick={() => setConfirmCacheModal(false)} />
          <div className="relative z-10 w-full max-w-sm rounded-lg border border-hair bg-surface p-5 shadow-xl">
            <h3 className="text-sm font-semibold text-ink">清理所有 Redis 缓存切片</h3>
            <p className="mt-2 text-xs leading-relaxed text-ink-secondary">
              将清空 Redis 中全部已缓存的行情切片、因子中间态与进程内 LRU（不影响本地 Parquet 与 SQLite 行情数据）。清理后首次请求需要重新计算或拉取。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmCacheModal(false)}
                className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink-secondary hover:bg-surface-alt"
              >
                取消
              </button>
              <button
                type="button"
                onClick={async () => {
                  await onClearCache();
                  setConfirmCacheModal(false);
                }}
                className="rounded-md bg-danger px-3 py-1.5 text-xs font-medium text-white hover:bg-danger/90"
              >
                确认清理
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
