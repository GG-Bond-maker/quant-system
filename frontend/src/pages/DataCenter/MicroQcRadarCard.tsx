import { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { datacenterApi } from '@/api/datacenter';
import { PanelEmpty } from '@/components/ui';
import type { MicroQcRadarResult } from '@/types/datacenter';

function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}

export default function MicroQcRadarCard() {
  const [data, setData] = useState<MicroQcRadarResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filterType, setFilterType] = useState<string>('all');

  const loadData = () => {
    setLoading(true);
    setError(null);
    datacenterApi.microQcRadar(50)
      .then((res) => setData(res))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : '加载微观质检雷达失败';
        setError(msg === 'Not Found' || msg.includes('404') ? '后端接口未找到 (404 Not Found)：请确认后端服务已重启以加载最新代码路由' : msg);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  const filteredAnomalies = useMemo(() => {
    if (!data?.anomalies) return [];
    if (filterType === 'all') return data.anomalies;
    return data.anomalies.filter((a) => a.kind === filterType);
  }, [data, filterType]);

  const statusBadge = useMemo(() => {
    if (!data) return null;
    if (data.status === 'HEALTHY') {
      return <span className="rounded bg-success-bg px-2 py-0.5 text-2xs font-semibold text-success">HEALTHY · 质检健康</span>;
    }
    if (data.status === 'WARNING') {
      return <span className="rounded bg-warn-bg px-2 py-0.5 text-2xs font-semibold text-warn">WARNING · 发现微观毛刺</span>;
    }
    return <span className="rounded bg-danger-bg px-2 py-0.5 text-2xs font-semibold text-danger">CRITICAL · 几何违规阻断</span>;
  }, [data]);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-hair px-4 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-ink">微观量价清洗雷达 (Micro QC Radar)</h2>
          {statusBadge}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadData}
            disabled={loading}
            className="rounded border border-hair px-2 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50 transition-colors"
          >
            {loading ? '质检中…' : '🔄 刷新质检雷达'}
          </button>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* 指标卡片行 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">已审计 K 线样本量</div>
            <div className="num mt-1 text-lg font-bold text-ink">
              {data ? fmtInt(data.total_audited_records) : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">覆盖 30+ 重点截面标的</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">Tukey / MAD 跳空极值</div>
            <div className="num mt-1 text-lg font-bold text-warn">
              {data ? data.jump_count : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">|ΔP| &gt; 3×IQR 或 |z| &gt; 4.5</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">OHLC 物理几何违规</div>
            <div className="num mt-1 text-lg font-bold text-danger">
              {data ? data.ohlc_breach_count : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">High &lt; Low 或 Close 越界</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">伪流动性 / 零成交异常</div>
            <div className="num mt-1 text-lg font-bold text-ink-secondary">
              {data ? data.zero_vol_count : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">Vol=0 且非停牌交易日</div>
          </div>
        </div>

        {/* 综合健康分条 */}
        {data && (
          <div className="rounded-md border border-hair bg-surface-sunken/40 p-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-ink">
                综合数据纯净度健康分: <span className="num font-bold text-brand-600">{data.health_score} / 100</span>
              </span>
              <span className="text-2xs text-ink-muted">
                异常率: {((data.anomaly_count / Math.max(1, data.total_audited_records)) * 100).toFixed(3)}%
              </span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
              <div
                className={`h-full rounded-full transition-all ${
                  data.health_score >= 95 ? 'bg-success' : data.health_score >= 80 ? 'bg-warn' : 'bg-danger'
                }`}
                style={{ width: `${Math.min(100, Math.max(0, data.health_score))}%` }}
              />
            </div>
          </div>
        )}

        {/* 异常细分筛选与列表 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-semibold text-ink">检出异常明细穿透</span>
              <div className="flex gap-1">
                {[
                  { key: 'all', label: '全部' },
                  { key: 'MAD_JUMP', label: 'MAD 跳空' },
                  { key: 'OHLC_BREACH', label: 'OHLC 越界' },
                  { key: 'ZERO_VOLUME', label: '零成交' },
                ].map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setFilterType(t.key)}
                    className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                      filterType === t.key
                        ? 'bg-brand-500 text-white font-medium'
                        : 'bg-surface-alt text-ink-secondary hover:text-ink'
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
            <span className="text-2xs text-ink-muted">显示 {filteredAnomalies.length} 条</span>
          </div>

          {loading ? (
            <div className="flex min-h-[160px] items-center justify-center text-xs text-ink-muted">
              <span className="animate-pulse">微观清洗算法扫描中…</span>
            </div>
          ) : error ? (
            <div className="rounded border border-danger/30 bg-danger-bg p-3 text-xs text-danger">{error}</div>
          ) : filteredAnomalies.length === 0 ? (
            <PanelEmpty text="当前过滤维度下未检出物理几何越界或清洗异常 ✓" minH="min-h-[140px]" />
          ) : (
            <div className="max-h-[260px] overflow-y-auto rounded border border-hair">
              <table className="quant-table w-full">
                <thead>
                  <tr>
                    <th>标的代码</th>
                    <th>违规交易日</th>
                    <th>规则类型</th>
                    <th>实测值 / 阈值</th>
                    <th>严重级别</th>
                    <th>清洗诊断与拦截结论</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAnomalies.map((an, idx) => (
                    <tr key={`${an.symbol}-${an.date}-${idx}`}>
                      <td className="font-mono text-xs font-medium">
                        <Link
                          to={`/stock/${encodeURIComponent(an.symbol)}`}
                          className="text-brand-600 hover:text-brand-700 hover:underline"
                        >
                          {an.symbol}
                        </Link>
                      </td>
                      <td className="font-mono text-2xs text-ink-secondary">{an.date}</td>
                      <td>
                        <span className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-3xs text-ink">
                          {an.kind}
                        </span>
                      </td>
                      <td className="font-mono text-2xs text-ink">
                        {an.value.toFixed(2)} / <span className="text-ink-muted">{an.threshold}</span>
                      </td>
                      <td>
                        <span
                          className={`rounded px-1.5 py-0.5 text-3xs font-semibold ${
                            an.severity === 'error'
                              ? 'bg-danger-bg text-danger'
                              : 'bg-warn-bg text-warn'
                          }`}
                        >
                          {an.severity.toUpperCase()}
                        </span>
                      </td>
                      <td className="text-xs text-ink-secondary">{an.message}</td>
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
