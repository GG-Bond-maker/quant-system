/**
 * 系统设置（/settings）：对标顶级量化平台的 5-Tab 投研环境与治理中枢。
 *
 * 架构：
 * 1. 通用偏好与用户资料 (PreferencesTab)：主题外观、默认市场、语言、刷新频率；
 * 2. 量化引擎与 A 股摩擦力 (EngineTab)：VectorBT/Backtrader 内核、印花税/买卖佣金/过户费/最低5元/Almgren-Chriss 滑点/默认基准/无风险利率；
 * 3. 数据源网关与连接路由 (DataGatewaysTab)：AKShare/Eastmoney 延迟心跳探测、主备路由、流控限速、增量同步；
 * 4. 通知集成与安全凭证 (WebhookAndSecurityTab)：飞书/钉钉/企微 Webhook、测试连通性、开发者 API Key (PAT) 生成与撤销；
 * 5. 存储运维与审计大屏 (SystemOpsTab)：SQLite 备份、Redis 缓存保护清理、硬件算力探针 Telemetry、操作审计日志。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { datacenterApi } from '@/api/datacenter';
import {
  settingsApi,
  type ApiKeyCreated,
  type ConnectorTest,
  type DataSourceConfig,
  type EngineConfig,
  type Preferences,
  type SettingsBundle,
  type WebhookConfig,
} from '@/api/settings';
import { hasMinimumRole } from '@/components/RequireAuth';
import { PageHeader } from '@/components/ui';
import { useAuthStore } from '@/stores/useAuthStore';
import { usePreferencesStore } from '@/stores/usePreferencesStore';

import { PreferencesTab } from './PreferencesTab';
import { EngineTab } from './EngineTab';
import { DataGatewaysTab } from './DataGatewaysTab';
import { WebhookAndSecurityTab } from './WebhookAndSecurityTab';
import { SystemOpsTab } from './SystemOpsTab';

type TabKey = 'preferences' | 'engine' | 'gateways' | 'webhook' | 'ops';

export default function Settings() {
  const authUser = useAuthStore((s) => s.user);
  const isAdmin = hasMinimumRole(authUser?.role, 'admin');
  const canResearch = hasMinimumRole(authUser?.role, 'researcher');

  const [activeTab, setActiveTab] = useState<TabKey>('preferences');
  const [bundle, setBundle] = useState<SettingsBundle | null>(null);
  const [tests, setTests] = useState<Record<string, ConnectorTest>>({});
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncProg, setSyncProg] = useState('');
  const [toast, setToast] = useState<{ text: string; kind: 'ok' | 'err' } | null>(null);

  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const syncPollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const notify = useCallback((text: string, kind: 'ok' | 'err' = 'ok') => {
    setToast({ text, kind });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3200);
  }, []);

  const applyTheme = useCallback((theme: Preferences['theme']) => {
    const root = document.documentElement;
    const dark =
      theme === 'dark' ||
      (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    root.classList.toggle('theme-dark', dark);
  }, []);

  /* ---------- 初始化加载与静默测速 ---------- */
  const runTestOne = useCallback(
    async (connector: 'akshare' | 'eastmoney') => {
      setTesting((t) => ({ ...t, [connector]: true }));
      try {
        const r = await settingsApi.testConnector(connector);
        setTests((cur) => ({ ...cur, [connector]: r }));
      } catch {
        setTests((cur) => ({ ...cur, [connector]: { status: 'error', latency_ms: null } }));
      } finally {
        setTesting((t) => ({ ...t, [connector]: false }));
      }
    },
    [],
  );

  const testAllConnectors = useCallback(async () => {
    setTesting({ akshare: true, eastmoney: true });
    const results = await Promise.allSettled([
      settingsApi.testConnector('akshare'),
      settingsApi.testConnector('eastmoney'),
    ]);
    setTesting({});
    setTests((cur) => {
      const next = { ...cur };
      results.forEach((r) => {
        if (r.status === 'fulfilled') next[r.value.connector] = r.value;
      });
      return next;
    });
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await settingsApi.all();
      setBundle(res);
      setTests(res.settings.last_tests ?? {});
      applyTheme(res.settings.preferences.theme);
      if (canResearch) {
        void testAllConnectors();
      }
    } catch {
      notify('设置加载失败，请检查后端服务连通性', 'err');
    } finally {
      setLoading(false);
    }
  }, [applyTheme, canResearch, notify, testAllConnectors]);

  useEffect(() => {
    void loadData();
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
      if (syncPollTimer.current) clearInterval(syncPollTimer.current);
    };
  }, [loadData]);

  /* ---------- 偏好保存 ---------- */
  const handleSavePreferences = async (newPrefs: Preferences) => {
    setSaving(true);
    try {
      const updated = await settingsApi.savePreferences(newPrefs);
      usePreferencesStore.getState().setRefreshFreq(updated.refresh_freq);
      setBundle((prev) =>
        prev
          ? {
              ...prev,
              settings: { ...prev.settings, preferences: updated },
            }
          : prev,
      );
      notify('个人与平台偏好已成功保存并即时生效');
    } catch {
      notify('偏好保存失败，请重试', 'err');
    } finally {
      setSaving(false);
    }
  };

  /* ---------- 引擎参数保存 ---------- */
  const handleSaveEngine = async (newEngine: EngineConfig) => {
    setSaving(true);
    try {
      const updated = await settingsApi.saveEngine(newEngine);
      setBundle((prev) =>
        prev
          ? {
              ...prev,
              settings: { ...prev.settings, engine: updated },
            }
          : prev,
      );
      notify('量化引擎与 A 股交易摩擦力配置已保存');
    } catch {
      notify('引擎参数保存失败，请检查管理员权限', 'err');
    } finally {
      setSaving(false);
    }
  };

  /* ---------- Webhook 保存与测试 ---------- */
  const handleSaveWebhook = async (wh: WebhookConfig) => {
    setSaving(true);
    try {
      const updated = await settingsApi.saveWebhook(wh);
      setBundle((prev) =>
        prev
          ? {
              ...prev,
              settings: { ...prev.settings, webhook: updated },
            }
          : prev,
      );
      notify('Webhook 告警通知信道已保存');
    } catch {
      notify('Webhook 配置保存失败', 'err');
    } finally {
      setSaving(false);
    }
  };

  const handleTestWebhook = async (wh: Partial<WebhookConfig>) => {
    try {
      const res = await settingsApi.testWebhook({
        webhook_url: wh.webhook_url,
        channel_type: wh.channel_type,
        secret: wh.secret,
      });
      notify(res.message || 'Webhook 测试成功');
    } catch (e: any) {
      notify(e?.message || 'Webhook 连通性测试失败', 'err');
    }
  };

  /* ---------- 数据源保存 ---------- */
  const handleSaveDataSources = async (ds: DataSourceConfig) => {
    setSaving(true);
    try {
      const updated = await settingsApi.saveDataSources(ds);
      setBundle((prev) =>
        prev
          ? {
              ...prev,
              settings: { ...prev.settings, datasources: updated },
            }
          : prev,
      );
      notify('数据源主备路由与流控配置已保存');
    } catch {
      notify('数据源配置保存失败', 'err');
    } finally {
      setSaving(false);
    }
  };

  /* ---------- API 密钥创建与删除 ---------- */
  const handleCreateApiKey = async (payload: {
    name: string;
    permission: string;
    expires_days?: number | null;
  }): Promise<ApiKeyCreated> => {
    const res = await settingsApi.createApiKey(payload);
    setBundle((prev) =>
      prev
        ? {
            ...prev,
            settings: {
              ...prev.settings,
              api_keys: [
                ...(prev.settings.api_keys || []),
                {
                  key_id: res.key_id,
                  name: res.name,
                  prefix: res.prefix,
                  permission: res.permission as any,
                  created_at: res.created_at,
                  expires_at: res.expires_at,
                },
              ],
            },
          }
        : prev,
    );
    notify('API 访问密钥已成功生成');
    return res;
  };

  const handleDeleteApiKey = async (keyId: string) => {
    try {
      await settingsApi.deleteApiKey(keyId);
      setBundle((prev) =>
        prev
          ? {
              ...prev,
              settings: {
                ...prev.settings,
                api_keys: (prev.settings.api_keys || []).filter((k) => k.key_id !== keyId),
              },
            }
          : prev,
      );
      notify(`API 密钥 ${keyId} 已撤销`);
    } catch {
      notify('密钥撤销失败', 'err');
    }
  };

  /* ---------- 运维与备份 ---------- */
  const handleClearCache = async () => {
    try {
      const r = await settingsApi.clearCache();
      notify(r.message);
      void loadData();
    } catch {
      notify('缓存清理失败', 'err');
    }
  };

  const handleBackupDb = async () => {
    try {
      const r = await settingsApi.backupDb();
      notify(`数据库备份已完成：${r.file} (${r.size_mb} MB)`);
      void loadData();
    } catch {
      notify('数据库备份失败', 'err');
    }
  };

  /* ---------- 日常同步 ---------- */
  const handleRunSync = async () => {
    if (syncing || syncPollTimer.current) return;
    try {
      await settingsApi.syncDaily();
      setSyncing(true);
      syncPollTimer.current = setInterval(() => {
        void (async () => {
          try {
            const status = await datacenterApi.status();
            if (status.running) {
              setSyncProg(`${status.done}/${status.total}`);
              return;
            }
            if (syncPollTimer.current) {
              clearInterval(syncPollTimer.current);
              syncPollTimer.current = null;
            }
            setSyncing(false);
            setSyncProg('');
            notify('当日 A 股日线增量数据同步完成');
            void loadData();
          } catch {
            // 忽略临时网络抖动
          }
        })();
      }, 1500);
    } catch (e: any) {
      if (syncPollTimer.current) {
        clearInterval(syncPollTimer.current);
        syncPollTimer.current = null;
      }
      setSyncing(false);
      notify(e?.message || '同步启动失败', 'err');
    }
  };

  return (
    <div className="flex min-h-full flex-col gap-3">
      <PageHeader
        title="系统设置"
        sub="对标工业级量化平台的投研环境、计算内核、A 股真实摩擦力模型、数据网关与系统治理中枢"
      />

      {/* 5-Tab 导航栏 */}
      <div className="border-b border-hair">
        <nav className="flex space-x-2 sm:space-x-4 overflow-x-auto">
          <button
            type="button"
            onClick={() => setActiveTab('preferences')}
            className={`border-b-2 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
              activeTab === 'preferences'
                ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            通用偏好与用户资料
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('engine')}
            className={`border-b-2 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
              activeTab === 'engine'
                ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            量化引擎与交易摩擦
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('gateways')}
            className={`border-b-2 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
              activeTab === 'gateways'
                ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            数据源网关与连接路由
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('webhook')}
            className={`border-b-2 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
              activeTab === 'webhook'
                ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            通知信道与开发者密钥
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('ops')}
            className={`border-b-2 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
              activeTab === 'ops'
                ? 'border-brand-500 text-brand-600 dark:text-brand-400'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            系统运维与算力探针
          </button>
        </nav>
      </div>

      {/* 加载中与错误骨架屏 */}
      {loading ? (
        <div className="animate-pulse rounded-lg border border-hair bg-surface p-8 text-center text-xs text-ink-muted">
          正在加载量化系统配置与环境探针指标…
        </div>
      ) : !bundle ? (
        <div className="rounded-lg border border-hair bg-surface p-8 text-center text-xs text-ink-muted">
          未能获取系统配置，请检查服务可用性
        </div>
      ) : (
        <>
          {/* Tab 1: 通用偏好 */}
          {activeTab === 'preferences' && (
            <PreferencesTab
              preferences={bundle.settings.preferences}
              userRole={authUser?.role}
              onSave={handleSavePreferences}
              onThemeChange={applyTheme}
              saving={saving}
            />
          )}

          {/* Tab 2: 量化引擎 */}
          {activeTab === 'engine' && (
            <EngineTab
              engine={bundle.settings.engine}
              isAdmin={isAdmin}
              onSave={handleSaveEngine}
              saving={saving}
            />
          )}

          {/* Tab 3: 数据网关 */}
          {activeTab === 'gateways' && (
            <DataGatewaysTab
              dataSources={
                bundle.settings.datasources || {
                  primary_source: 'eastmoney',
                  fallback_source: 'akshare',
                  request_timeout_sec: 15,
                  rate_limit_qps: 5,
                }
              }
              tests={tests}
              testing={testing}
              lastSync={bundle.last_sync}
              syncing={syncing}
              syncProg={syncProg}
              canResearch={canResearch}
              isAdmin={isAdmin}
              onRunTest={runTestOne}
              onTestAll={testAllConnectors}
              onRunSync={handleRunSync}
              onSaveDataSources={handleSaveDataSources}
              saving={saving}
            />
          )}

          {/* Tab 4: 通知与安全凭证 */}
          {activeTab === 'webhook' && (
            <WebhookAndSecurityTab
              webhook={bundle.settings.webhook}
              apiKeys={bundle.settings.api_keys || []}
              isAdmin={isAdmin}
              canResearch={canResearch}
              onSaveWebhook={handleSaveWebhook}
              onTestWebhook={handleTestWebhook}
              onCreateApiKey={handleCreateApiKey}
              onDeleteApiKey={handleDeleteApiKey}
              saving={saving}
            />
          )}

          {/* Tab 5: 系统运维与探针 */}
          {activeTab === 'ops' && (
            <SystemOpsTab
              storageGb={bundle.storage_gb}
              system={bundle.system ?? null}
              telemetry={bundle.telemetry}
              auditLogs={bundle.audit_logs || []}
              isAdmin={isAdmin}
              onClearCache={handleClearCache}
              onBackupDb={handleBackupDb}
            />
          )}
        </>
      )}

      {/* 全局 Toast */}
      {toast && (
        <div
          className={`fixed bottom-6 right-6 z-50 rounded-md border px-4 py-2.5 text-xs shadow-lg transition-all ${
            toast.kind === 'ok'
              ? 'border-success/30 bg-success-bg text-success'
              : 'border-danger/30 bg-danger-bg text-danger'
          }`}
        >
          {toast.text}
        </div>
      )}

      {/* 页脚 */}
      <div className="flex flex-wrap items-center justify-between gap-1 pb-1 text-2xs text-ink-muted">
        <span>Alpha Quant Platform 系统治理中心 ｜ AQP 量化分析系统 v2.5</span>
        <span>工业级量化投研环境 · 仅供研究使用</span>
      </div>
    </div>
  );
}
