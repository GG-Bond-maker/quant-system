import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Settings from './index';
import { PreferencesTab } from './PreferencesTab';
import { EngineTab } from './EngineTab';
import { DataGatewaysTab } from './DataGatewaysTab';
import { WebhookAndSecurityTab } from './WebhookAndSecurityTab';
import { SystemOpsTab } from './SystemOpsTab';
import type {
  ApiKeyItem,
  AuditLogItem,
  ConnectorTest,
  DataSourceConfig,
  EngineConfig,
  Preferences,
  SettingsBundle,
  SystemStatus,
  TelemetryData,
  WebhookConfig,
} from '@/api/settings';

const h = vi.hoisted(() => ({
  all: vi.fn(),
  savePreferences: vi.fn(),
  saveEngine: vi.fn(),
  saveWebhook: vi.fn(),
  testWebhook: vi.fn(),
  saveDataSources: vi.fn(),
  createApiKey: vi.fn(),
  deleteApiKey: vi.fn(),
  testConnector: vi.fn(),
  clearCache: vi.fn(),
  backupDb: vi.fn(),
  syncDaily: vi.fn(),
  datacenterStatus: vi.fn(),
}));

vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: (sel: (s: { user: { role: string; username: string } }) => unknown) =>
    sel({ user: { role: 'admin', username: 'admin_master' } }),
}));

vi.mock('@/components/RequireAuth', () => ({
  hasMinimumRole: () => true,
}));

vi.mock('@/api/datacenter', () => ({
  datacenterApi: {
    status: h.datacenterStatus,
  },
}));

vi.mock('@/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/settings')>();
  return {
    ...actual,
    settingsApi: {
      ...actual.settingsApi,
      all: h.all,
      savePreferences: h.savePreferences,
      saveEngine: h.saveEngine,
      saveWebhook: h.saveWebhook,
      testWebhook: h.testWebhook,
      saveDataSources: h.saveDataSources,
      createApiKey: h.createApiKey,
      deleteApiKey: h.deleteApiKey,
      testConnector: h.testConnector,
      clearCache: h.clearCache,
      backupDb: h.backupDb,
      syncDaily: h.syncDaily,
    },
  };
});

describe('系统设置 P0/P1/P2 演进重构组件测试', () => {
  const mockPreferences: Preferences = {
    theme: 'dark',
    language: 'zh-CN',
    market: 'A',
    notify_backtest: true,
    refresh_freq: 3,
    nickname: 'Lead Quant PM',
    email: 'lead_pm@hedgefund.com',
  };

  const mockEngine: EngineConfig = {
    name: 'vectorbt',
    risk_indicators: ['annual', 'sharpe', 'max_dd', 'calmar'],
    commission_pct: 0.025,
    commission_sell_pct: 0.025,
    stamp_duty_pct: 0.05,
    transfer_fee_pct: 0.001,
    min_commission: 5.0,
    slippage_model: 'almgren_chriss',
    slippage_pct: 0.08,
    default_benchmark: '000300.SH',
    risk_free_rate: 2.0,
    t_plus_one: true,
    limit_up_down_guard: true,
  };

  const mockWebhook: WebhookConfig = {
    enabled: true,
    channel_type: 'feishu',
    webhook_url: 'https://open.feishu.cn/open-apis/bot/v2/hook/test-key',
    secret: 'test-secret',
    events: ['backtest_done', 'risk_alert'],
  };

  const mockDataSources: DataSourceConfig = {
    primary_source: 'eastmoney',
    fallback_source: 'akshare',
    request_timeout_sec: 15,
    rate_limit_qps: 5,
  };

  const mockApiKeys: ApiKeyItem[] = [
    {
      key_id: 'key_123456',
      name: 'Jupyter Research Client',
      prefix: 'aqp_live_abc1****',
      permission: 'read_only',
      created_at: '2026-10-08 10:00:00',
      expires_at: '2026-11-08 10:00:00',
    },
  ];

  const mockTests: Record<string, ConnectorTest> = {
    akshare: { status: 'success', latency_ms: 128, ts: '2026-10-08 12:00:00' },
    eastmoney: { status: 'success', latency_ms: 45, ts: '2026-10-08 12:00:00' },
  };

  const mockSystem: SystemStatus = {
    cache: {
      enabled: true,
      ok: true,
      degraded: false,
      breaker: { fail_count: 0, open: false, open_until: 0 },
    },
    disk: {
      usage_percent: 42.5,
      level: 'normal',
    },
  };

  const mockTelemetry: TelemetryData = {
    python_version: '3.11.9',
    platform_system: 'Windows-11',
    polars_version: '1.8.2',
    numpy_version: '1.26.4',
    cpu_percent: 15.2,
    memory_used_gb: 4.8,
    memory_total_gb: 32.0,
    server_time: '2026-10-08 17:30:00',
  };

  const mockAuditLogs: AuditLogItem[] = [
    {
      id: 'log_1',
      action: '更新量化引擎与摩擦力配置',
      operator: 'admin_master',
      timestamp: '2026-10-08 15:30:00',
      details: '印花税: 0.05%, 引擎: vectorbt',
    },
  ];

  const mockBundle: SettingsBundle = {
    settings: {
      preferences: mockPreferences,
      engine: mockEngine,
      webhook: mockWebhook,
      datasources: mockDataSources,
      api_keys: mockApiKeys,
      last_tests: mockTests,
      audit_logs: mockAuditLogs,
    },
    storage_gb: 24.5,
    last_sync: '2026-10-08 15:05:00',
    system: mockSystem,
    telemetry: mockTelemetry,
    audit_logs: mockAuditLogs,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('PreferencesTab 渲染偏好配置并支持保存', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onThemeChange = vi.fn();

    render(
      <PreferencesTab
        preferences={mockPreferences}
        userRole="admin"
        onSave={onSave}
        onThemeChange={onThemeChange}
        saving={false}
      />,
    );

    expect(screen.getByText('Lead Quant PM')).toBeInTheDocument();
    expect(screen.getByText('深色极客')).toBeInTheDocument();
    expect(screen.getByText('中国 A 股市场 (CN Stock)')).toBeInTheDocument();

    // 切换主题
    const lightThemeBtn = screen.getByText('浅色明亮');
    fireEvent.click(lightThemeBtn);
    expect(onThemeChange).toHaveBeenCalledWith('light');

    // 保存偏好
    const saveBtn = screen.getByRole('button', { name: /保存偏好配置/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalled();
    });
  });

  it('EngineTab 渲染 A 股真实四联费率、滑点冲击模型与风控规则', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <EngineTab
        engine={mockEngine}
        isAdmin={true}
        onSave={onSave}
        saving={false}
      />,
    );

    // 1. 验证 A 股摩擦力模型字段
    expect(screen.getByText('A 股真实交易成本与摩擦力模型')).toBeInTheDocument();
    expect(screen.getByText('买入佣金率 (Buy Fee)')).toBeInTheDocument();
    expect(screen.getByText('卖出佣金率 (Sell Fee)')).toBeInTheDocument();
    expect(screen.getByText('印花税率 (Stamp Duty)')).toBeInTheDocument();
    expect(screen.getByText('过户与经手规费 (Transfer)')).toBeInTheDocument();
    expect(screen.getByText('单笔最低佣金门槛 (Min Fee)')).toBeInTheDocument();
    expect(screen.getByText('Almgren-Chriss 平方根流动性冲击')).toBeInTheDocument();

    // 2. 验证基准指数与 T+1 规则
    expect(screen.getByText('强制执行 A 股 T+1 交易交收约束')).toBeInTheDocument();
    expect(screen.getByText('涨跌停板撮合拦截（价格笼子保护）')).toBeInTheDocument();

    // 3. 恢复推荐参数
    const resetBtn = screen.getByRole('button', { name: /恢复推荐 A 股实盘参数/ });
    fireEvent.click(resetBtn);

    // 4. 保存引擎配置
    const saveBtn = screen.getByRole('button', { name: /保存引擎配置/ });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalled();
    });
  });

  it('DataGatewaysTab 渲染连接状态、主备路由配置并支持测速与同步', async () => {
    const onRunTest = vi.fn().mockResolvedValue(undefined);
    const onTestAll = vi.fn().mockResolvedValue(undefined);
    const onRunSync = vi.fn().mockResolvedValue(undefined);
    const onSaveDataSources = vi.fn().mockResolvedValue(undefined);

    render(
      <DataGatewaysTab
        dataSources={mockDataSources}
        tests={mockTests}
        testing={{}}
        lastSync="2026-10-08 15:05:00"
        syncing={false}
        syncProg=""
        canResearch={true}
        isAdmin={true}
        onRunTest={onRunTest}
        onTestAll={onTestAll}
        onRunSync={onRunSync}
        onSaveDataSources={onSaveDataSources}
        saving={false}
      />,
    );

    expect(screen.getByText('AKShare 开源金融数据')).toBeInTheDocument();
    expect(screen.getByText('东方财富行情推送网关')).toBeInTheDocument();
    expect(screen.getByText('正常 (128ms)')).toBeInTheDocument();
    expect(screen.getByText('正常 (45ms)')).toBeInTheDocument();

    // 触发单源测速
    const testBtns = screen.getAllByRole('button', { name: /测试延迟/ });
    expect(testBtns.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(testBtns[0]);
    expect(onRunTest).toHaveBeenCalledWith('akshare');

    // 触发增量同步
    const syncBtn = screen.getByRole('button', { name: /立即同步当日日线/ });
    fireEvent.click(syncBtn);
    expect(onRunSync).toHaveBeenCalled();
  });

  it('WebhookAndSecurityTab 渲染 Webhook 机器人信道配置与 API Key 令牌管理', async () => {
    const onSaveWebhook = vi.fn().mockResolvedValue(undefined);
    const onTestWebhook = vi.fn().mockResolvedValue(undefined);
    const onCreateApiKey = vi.fn().mockResolvedValue({
      key_id: 'key_new_999',
      name: 'Python Client',
      raw_token: 'aqp_live_secret_token_12345',
      prefix: 'aqp_live_sec****',
      permission: 'read_only',
      created_at: '2026-10-08 12:00:00',
      expires_at: '2026-11-08 12:00:00',
    });
    const onDeleteApiKey = vi.fn().mockResolvedValue(undefined);

    render(
      <WebhookAndSecurityTab
        webhook={mockWebhook}
        apiKeys={mockApiKeys}
        isAdmin={true}
        canResearch={true}
        onSaveWebhook={onSaveWebhook}
        onTestWebhook={onTestWebhook}
        onCreateApiKey={onCreateApiKey}
        onDeleteApiKey={onDeleteApiKey}
        saving={false}
      />,
    );

    // 1. Webhook 测试消息
    expect(screen.getByText('投研告警与通知 Webhook 机器人信道')).toBeInTheDocument();
    const testWhBtn = screen.getByRole('button', { name: /发送测试消息/ });
    fireEvent.click(testWhBtn);
    expect(onTestWebhook).toHaveBeenCalled();

    // 2. 检查现有 API Key 列表
    expect(screen.getByText('Jupyter Research Client')).toBeInTheDocument();
    expect(screen.getByText('aqp_live_abc1****')).toBeInTheDocument();

    // 3. 撤销 API Key
    const revokeBtn = screen.getByRole('button', { name: '撤销' });
    fireEvent.click(revokeBtn);
    expect(onDeleteApiKey).toHaveBeenCalledWith('key_123456');

    // 4. 打开生成新 API Key 模态框并生成
    const createBtn = screen.getByRole('button', { name: /\+ 生成新 API 密钥/ });
    fireEvent.click(createBtn);
    expect(screen.getByText('生成开发者访问令牌 (API Key)')).toBeInTheDocument();

    const generateBtn = screen.getByRole('button', { name: '立即生成' });
    fireEvent.click(generateBtn);
    await waitFor(() => {
      expect(onCreateApiKey).toHaveBeenCalled();
      expect(screen.getByText('aqp_live_secret_token_12345')).toBeInTheDocument();
    });
  });

  it('SystemOpsTab 渲染数据库备份、Redis 缓存保护、算力探针与审计流水', async () => {
    const onClearCache = vi.fn().mockResolvedValue(undefined);
    const onBackupDb = vi.fn().mockResolvedValue(undefined);

    render(
      <SystemOpsTab
        storageGb={24.5}
        system={mockSystem}
        telemetry={mockTelemetry}
        auditLogs={mockAuditLogs}
        isAdmin={true}
        onClearCache={onClearCache}
        onBackupDb={onBackupDb}
      />,
    );

    // 1. 存储与探针指标
    expect(screen.getByText('24.50 GB')).toBeInTheDocument();
    expect(screen.getByText('3.11.9')).toBeInTheDocument();
    expect(screen.getByText('1.8.2')).toBeInTheDocument();
    expect(screen.getByText('15.2%')).toBeInTheDocument();

    // 2. 数据库备份
    const backupBtn = screen.getByRole('button', { name: /备份数据库/ });
    fireEvent.click(backupBtn);
    expect(onBackupDb).toHaveBeenCalled();

    // 3. 清理缓存二次确认弹窗
    const clearCacheBtn = screen.getByRole('button', { name: '清理全站临时缓存' });
    fireEvent.click(clearCacheBtn);
    expect(screen.getByText('清理所有 Redis 缓存切片')).toBeInTheDocument();

    const confirmBtn = screen.getByRole('button', { name: '确认清理' });
    fireEvent.click(confirmBtn);
    expect(onClearCache).toHaveBeenCalled();

    // 4. 审计日志列表
    expect(screen.getByText('更新量化引擎与摩擦力配置')).toBeInTheDocument();
    expect(screen.getByText('admin_master')).toBeInTheDocument();
  });

  it('Settings 主页面 5-Tab 导航切换与数据全景加载集成测试', async () => {
    h.all.mockResolvedValueOnce(mockBundle);
    h.testConnector.mockResolvedValue({ connector: 'akshare', status: 'success', latency_ms: 50 });

    render(<Settings />);

    await waitFor(() => {
      expect(screen.getByText('系统设置')).toBeInTheDocument();
    });

    // 默认展示 Tab 1: 通用偏好
    expect(screen.getByText('投研账户与身份')).toBeInTheDocument();
    expect(screen.getByText('Lead Quant PM')).toBeInTheDocument();

    // 切换至 Tab 2: 量化引擎与交易摩擦
    const engineTabBtn = screen.getByRole('button', { name: '量化引擎与交易摩擦' });
    fireEvent.click(engineTabBtn);
    expect(screen.getByText('量化计算引擎与交易撮合规则')).toBeInTheDocument();
    expect(screen.getByText('A 股真实交易成本与摩擦力模型')).toBeInTheDocument();

    // 切换至 Tab 3: 数据源网关与连接路由
    const gatewaysTabBtn = screen.getByRole('button', { name: '数据源网关与连接路由' });
    fireEvent.click(gatewaysTabBtn);
    expect(screen.getByText('行情与宏观数据源网关状态')).toBeInTheDocument();

    // 切换至 Tab 4: 通知信道与开发者密钥
    const webhookTabBtn = screen.getByRole('button', { name: '通知信道与开发者密钥' });
    fireEvent.click(webhookTabBtn);
    expect(screen.getByText('投研告警与通知 Webhook 机器人信道')).toBeInTheDocument();

    // 切换至 Tab 5: 系统运维与算力探针
    const opsTabBtn = screen.getByRole('button', { name: '系统运维与算力探针' });
    fireEvent.click(opsTabBtn);
    expect(screen.getByText('数据库与持久化存储')).toBeInTheDocument();
    expect(screen.getByText('系统配置与运维操作审计日志 (Audit Trail)')).toBeInTheDocument();
  });
});
