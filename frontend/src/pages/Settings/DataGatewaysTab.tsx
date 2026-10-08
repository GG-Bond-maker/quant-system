import { useState } from 'react';
import type { ConnectorTest, DataSourceConfig } from '@/api/settings';
import { SectionCard } from '@/components/ui';

interface Props {
  dataSources: DataSourceConfig;
  tests: Record<string, ConnectorTest>;
  testing: Record<string, boolean>;
  lastSync: string | null;
  syncing: boolean;
  syncProg: string;
  canResearch: boolean;
  isAdmin: boolean;
  onRunTest: (connector: 'akshare' | 'eastmoney') => Promise<void>;
  onTestAll: () => Promise<void>;
  onRunSync: () => Promise<void>;
  onSaveDataSources: (ds: DataSourceConfig) => Promise<void>;
  saving: boolean;
}

function StatusBadge({ test, testing }: { test?: ConnectorTest; testing: boolean }) {
  if (testing) {
    return (
      <span className="flex items-center gap-1 rounded bg-warn-bg px-1.5 py-0.5 text-2xs text-warn">
        <svg className="h-2.5 w-2.5 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
        </svg>
        测速中…
      </span>
    );
  }
  if (!test) return <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-ink-muted">未测试</span>;
  if (test.status === 'success') {
    return (
      <span className="flex items-center gap-1 rounded bg-success-bg px-1.5 py-0.5 text-2xs font-medium text-success">
        <span className="h-1.5 w-1.5 rounded-full bg-down" />
        正常 ({test.latency_ms ?? 0}ms)
      </span>
    );
  }
  return (
    <span
      className="flex items-center gap-1 rounded bg-danger-bg px-1.5 py-0.5 text-2xs font-medium text-danger"
      title={test.message}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-up" />
      异常离线
    </span>
  );
}

export function DataGatewaysTab({
  dataSources,
  tests,
  testing,
  lastSync,
  syncing,
  syncProg,
  canResearch,
  isAdmin,
  onRunTest,
  onTestAll,
  onRunSync,
  onSaveDataSources,
  saving,
}: Props) {
  const [form, setForm] = useState<DataSourceConfig>(dataSources);
  const [isDirty, setIsDirty] = useState(false);

  const update = <K extends keyof DataSourceConfig>(k: K, v: DataSourceConfig[K]) => {
    setForm((prev) => ({ ...prev, [k]: v }));
    setIsDirty(true);
  };

  const handleSave = async () => {
    await onSaveDataSources(form);
    setIsDirty(false);
  };

  const inputCls =
    'w-full rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300 disabled:opacity-60';

  return (
    <div className="space-y-4">
      {/* 1. 数据接口网关状态卡片 */}
      <SectionCard
        title="行情与宏观数据源网关状态"
        action={
          <div className="flex items-center gap-2">
            {canResearch && (
              <button
                type="button"
                onClick={onTestAll}
                className="rounded-md border border-hair bg-surface px-2.5 py-1 text-xs text-ink-secondary hover:bg-surface-alt"
              >
                一键全面探测上游
              </button>
            )}
          </div>
        }
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {/* AKShare */}
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                <span className="flex h-4 w-4 items-center justify-center rounded bg-down text-[8px] font-bold text-white">
                  AK
                </span>
                AKShare 开源金融数据
              </span>
              <StatusBadge test={tests.akshare} testing={!!testing.akshare} />
            </div>
            <div className="mt-2 text-2xs text-ink-muted leading-relaxed">
              数据范围：A 股宽基指数、申万行业划分、ETF 日频行情、宏观利率
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-hair pt-2">
              <span className="text-3xs text-ink-muted">
                {tests.akshare?.ts ? `最后探测: ${tests.akshare.ts}` : '尚未进行探测'}
              </span>
              {canResearch && (
                <button
                  type="button"
                  onClick={() => void onRunTest('akshare')}
                  disabled={!!testing.akshare}
                  className="rounded border border-hair bg-surface px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50"
                >
                  测试延迟
                </button>
              )}
            </div>
          </div>

          {/* Eastmoney */}
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                <span className="flex h-4 w-4 items-center justify-center rounded bg-warn text-[8px] font-bold text-white">
                  EM
                </span>
                东方财富行情推送网关
              </span>
              <StatusBadge test={tests.eastmoney} testing={!!testing.eastmoney} />
            </div>
            <div className="mt-2 text-2xs text-ink-muted leading-relaxed">
              数据范围：盘中快照推送、盘口委托簿、分笔成交、日内实时成交量
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-hair pt-2">
              <span className="text-3xs text-ink-muted">
                {tests.eastmoney?.ts ? `最后探测: ${tests.eastmoney.ts}` : '尚未进行探测'}
              </span>
              {canResearch && (
                <button
                  type="button"
                  onClick={() => void onRunTest('eastmoney')}
                  disabled={!!testing.eastmoney}
                  className="rounded border border-hair bg-surface px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50"
                >
                  测试延迟
                </button>
              )}
            </div>
          </div>

          {/* IBKR / QC 占位 */}
          <div className="rounded-lg border border-hair bg-surface-alt p-3 opacity-70">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs font-medium text-ink-muted">
                <span className="font-mono text-2xs">&lt;/&gt;</span>
                IBKR / QuantConnect 券商网关
              </span>
              <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-3xs text-ink-muted">
                规划中
              </span>
            </div>
            <div className="mt-2 text-2xs text-ink-muted/80 leading-relaxed">
              数据范围：港美股高频 Level-2 行情与直连机构 DMA 柜台交易协议
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-hair pt-2">
              <span className="text-3xs text-ink-muted">当前版本仅支持国内现货</span>
              <span className="text-3xs text-ink-muted">暂不可用</span>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* 2. 主备路由与请求流控 */}
      <SectionCard
        title="数据源主备故障转移与流控网关"
        action={
          <div className="flex items-center gap-2">
            {isDirty && <span className="text-2xs font-medium text-warn">● 存在未保存修改</span>}
            <button
              type="button"
              onClick={handleSave}
              disabled={!isAdmin || !isDirty || saving}
              className="rounded-md bg-brand-500 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {saving ? '保存中…' : '保存网关配置'}
            </button>
          </div>
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">首选主数据源 (Primary)</label>
            <select
              value={form.primary_source}
              disabled={!isAdmin}
              onChange={(e) => update('primary_source', e.target.value)}
              className={inputCls}
            >
              <option value="eastmoney">东方财富网关 (低延迟 · 首选)</option>
              <option value="akshare">AKShare 开源数据源 (稳定全量)</option>
            </select>
            <p className="mt-1 text-3xs text-ink-muted">系统默认优先向该上游接口请求数据</p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">备用降级数据源 (Fallback)</label>
            <select
              value={form.fallback_source}
              disabled={!isAdmin}
              onChange={(e) => update('fallback_source', e.target.value)}
              className={inputCls}
            >
              <option value="akshare">AKShare 开源接口 (备用)</option>
              <option value="eastmoney">东方财富网关 (备用)</option>
            </select>
            <p className="mt-1 text-3xs text-ink-muted">主源请求超时或熔断时自动降级切换至该备用链路</p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">单个请求超时上限 (秒)</label>
            <input
              type="number"
              min={5}
              max={60}
              value={form.request_timeout_sec}
              disabled={!isAdmin}
              onChange={(e) => update('request_timeout_sec', Number(e.target.value))}
              className={inputCls}
            />
            <p className="mt-1 text-3xs text-ink-muted">超过此时限自动判定单点超时并触发重试机制</p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">上游最大抓取速率 (QPS)</label>
            <input
              type="number"
              min={1}
              max={30}
              value={form.rate_limit_qps}
              disabled={!isAdmin}
              onChange={(e) => update('rate_limit_qps', Number(e.target.value))}
              className={inputCls}
            />
            <p className="mt-1 text-3xs text-ink-muted">限制每秒最大并发请求数，避免被上游反爬风控拉黑</p>
          </div>
        </div>
      </SectionCard>

      {/* 3. 日常增量数据同步与调度触发 */}
      <SectionCard title="日线数据增量同步作业">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center rounded-lg border border-hair bg-surface-alt p-3.5">
          <div>
            <div className="text-xs font-semibold text-ink">全市场 A 股后复权日线增量同步</div>
            <div className="mt-0.5 text-2xs text-ink-muted">
              从上游网关拉取当日最新收盘行情并增量追加写入本地 Parquet 列式分区存储
            </div>
            <div className="num mt-1 text-3xs text-ink-muted">
              上次增量落库时间: {lastSync ? lastSync.replace('T', ' ') : '—'}
            </div>
          </div>

          <div className="shrink-0">
            {canResearch ? (
              <button
                type="button"
                onClick={() => void onRunSync()}
                disabled={syncing}
                className="rounded-md bg-brand-500 px-4 py-2 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
              >
                {syncing ? `正在同步 (${syncProg || '计算中'})…` : '立即同步当日日线 (收盘后)'}
              </button>
            ) : (
              <span className="text-2xs text-ink-muted">需要 researcher 及以上权限</span>
            )}
          </div>
        </div>
      </SectionCard>
    </div>
  );
}
