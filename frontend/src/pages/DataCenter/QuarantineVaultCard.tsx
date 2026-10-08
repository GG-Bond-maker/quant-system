import { useEffect, useState } from 'react';
import { datacenterApi } from '@/api/datacenter';
import { PanelEmpty } from '@/components/ui';
import type { QuarantineVaultResult } from '@/types/datacenter';

export default function QuarantineVaultCard() {
  const [data, setData] = useState<QuarantineVaultResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const loadData = () => {
    setLoading(true);
    setError(null);
    datacenterApi.quarantineVault()
      .then((res) => setData(res))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : '加载隔离区信息失败';
        setError(msg === 'Not Found' || msg.includes('404') ? '后端接口未找到 (404 Not Found)：请确认后端服务已重启以加载最新代码路由' : msg);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleExport = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `quarantine_vault_audit_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    setToastMsg('坏数据隔离审计清单导出成功');
    setTimeout(() => setToastMsg(null), 3000);
  };

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-hair px-4 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-ink">坏数据隔离区 (Quarantine Vault)</h2>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-semibold text-brand-600">
            {data?.vault_status === 'CLEAN' ? 'VAULT HEALTHY · 隔离沙盒受控' : 'ACTIVE · 隔离运转中'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {toastMsg && <span className="text-2xs text-success">{toastMsg}</span>}
          <button
            onClick={handleExport}
            disabled={!data || loading}
            className="rounded border border-hair px-2 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50 transition-colors"
          >
            📥 导出隔离审计单
          </button>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* 策略说明框 */}
        <div className="rounded-md border border-hair bg-surface-alt p-3">
          <div className="flex items-center justify-between text-xs font-semibold text-ink">
            <span>🛡️ 坏数据物理隔离与自动熔断策略 (Zero-Taint Pipeline)</span>
            <span className="text-2xs font-normal text-success">
              {data?.quarantine_policy.auto_isolation_enabled ? '自动阻断机制已激活' : '监控模式'}
            </span>
          </div>
          <p className="mt-1 text-2xs text-ink-secondary leading-relaxed">
            {data?.quarantine_policy.note ??
              '质检引擎在特征工程与回测引擎读取前自动阻断脏数据入库，写入隔离沙盒，确保下游因子计算与模拟交易 0 污染。'}
          </p>
          <div className="mt-2.5 grid grid-cols-1 gap-1.5 sm:grid-cols-3">
            {(data?.quarantine_policy.rules ?? [
              '物理几何越界（High < Low 或价格负数）立即隔离',
              'MAD 跳空极值暂缓入库并标记待核验',
              '零成交自动标注无流动性，杜绝假撮合',
            ]).map((rule, idx) => (
              <div key={idx} className="rounded bg-surface px-2.5 py-1.5 text-2xs text-ink">
                <span className="font-semibold text-brand-600">规则 {idx + 1}: </span>
                {rule}
              </div>
            ))}
          </div>
        </div>

        {/* 隔离记录列表 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-ink">
              当前隔离区滞留记录 ({data?.quarantined_items.length ?? 0})
            </span>
            <span className="text-2xs text-ink-muted">阻断时间与排查建议</span>
          </div>

          {loading ? (
            <div className="flex min-h-[140px] items-center justify-center text-xs text-ink-muted">
              <span className="animate-pulse">读取隔离区快照…</span>
            </div>
          ) : error ? (
            <div className="rounded border border-danger/30 bg-danger-bg p-3 text-xs text-danger">{error}</div>
          ) : !data?.quarantined_items.length ? (
            <PanelEmpty text="当前隔离沙盒无滞留坏数据，全库数据通过完整性校验 ✓" minH="min-h-[120px]" />
          ) : (
            <div className="max-h-[220px] overflow-y-auto rounded border border-hair">
              <table className="quant-table w-full">
                <thead>
                  <tr>
                    <th>记录 ID</th>
                    <th>目标表</th>
                    <th>标的</th>
                    <th>交易日</th>
                    <th>隔离拦截成因</th>
                    <th>建议处置</th>
                  </tr>
                </thead>
                <tbody>
                  {data.quarantined_items.map((item) => (
                    <tr key={item.id}>
                      <td className="font-mono text-2xs text-ink-muted">{item.id}</td>
                      <td className="font-mono text-2xs text-ink">{item.dataset}</td>
                      <td className="font-mono text-xs font-semibold text-brand-600">{item.symbol}</td>
                      <td className="font-mono text-2xs text-ink-secondary">{item.date}</td>
                      <td className="text-xs text-danger">{item.reason}</td>
                      <td className="text-2xs text-ink-secondary">{item.suggested_action}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
