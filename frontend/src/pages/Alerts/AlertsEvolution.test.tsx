/**
 * 预警中心 (Alerts) 工业级量化风控监控驾驶舱演进单测套件
 * 严格检验：
 * 1. 顶部 4 联风险态势驾驶舱 (KPI Cards) 正确渲染指标与严重度状态；
 * 2. 3-Tab 架构切换（告警事件中心、风控规则中枢、通道与健康监控）；
 * 3. 规则参数结构化 Badges 渲染（彻底废除原始 JSON.stringify）；
 * 4. 事件穿透诊断与闭环处置（转交易台卖出、一键列入禁买黑名单）；
 * 5. 规则抽屉与量化预设模板库一键填单。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AlertEventItem, AlertHealth, AlertRule, AlertStats } from '@/api/alerts';
import Alerts from './index';

const mockedNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockedNavigate,
  };
});

const mockAlertsApi = vi.hoisted(() => ({
  rules: vi.fn(),
  stats: vi.fn(),
  health: vi.fn(),
  createRule: vi.fn(),
  updateRule: vi.fn(),
  deleteRule: vi.fn(),
  events: vi.fn(),
  markRead: vi.fn(),
  markAllRead: vi.fn(),
}));

const mockDeskApi = vi.hoisted(() => ({
  addExclusion: vi.fn(),
}));

vi.mock('@/api/alerts', async () => {
  const actual = await vi.importActual<typeof import('@/api/alerts')>('@/api/alerts');
  return {
    ...actual,
    alertsApi: mockAlertsApi,
  };
});

vi.mock('@/api/production', () => ({
  deskApi: mockDeskApi,
}));

describe('预警中心演进重构 (Alerts Evolution Cockpit)', () => {
  const sampleStats: AlertStats = {
    today_events_count: 5,
    unread_count: 2,
    critical_count: 1,
    warning_count: 2,
    info_count: 2,
    active_rules_count: 7,
    total_rules_count: 8,
    health_degraded: false,
    risk_level: 'HIGH_RISK',
    top_symbols: [
      { symbol: '600519.SH', count: 3, last_severity: 'critical' },
      { symbol: '000001.SZ', count: 2, last_severity: 'warning' },
    ],
  };

  const sampleHealth: AlertHealth = {
    degraded: false,
    reason: null,
    detail: null,
    snapshot_date: '2026-10-08',
    checked_at: '2026-10-08 15:00:00',
    last_ok_at: '2026-10-08 15:00:00',
  };

  const sampleRules: AlertRule[] = [
    {
      id: 1,
      name: '动态高水位回撤止损',
      rule_type: 'portfolio_drawdown',
      scope: 'global',
      symbol: null,
      params: { max_drawdown: 8.0, benchmark: '000300.SH', severity: 'critical', hysteresis: 0.2 },
      channels: ['sse', 'webhook'],
      cooldown_minutes: 30,
      enabled: true,
      created_at: '2026-10-08 09:00:00',
    },
    {
      id: 2,
      name: '95% Parametric VaR超限',
      rule_type: 'portfolio_var',
      scope: 'global',
      symbol: null,
      params: { confidence: 0.95, limit_pct: 3.0, time_horizon: 1, severity: 'warning' },
      channels: ['sse'],
      cooldown_minutes: 60,
      enabled: true,
      created_at: '2026-10-08 09:05:00',
    },
    {
      id: 3,
      name: '茅台暴跌预警',
      rule_type: 'price_pct',
      scope: 'symbol',
      symbol: '600519.SH',
      params: { threshold: 4.0, severity: 'warning' },
      channels: ['sse'],
      cooldown_minutes: 15,
      enabled: false,
      created_at: '2026-10-08 09:10:00',
    },
  ];

  const sampleEvents: AlertEventItem[] = [
    {
      id: 101,
      rule_id: 1,
      symbol: '600519.SH',
      triggered_at: '2026-10-08T14:30:00Z',
      payload: {
        rule: '动态高水位回撤止损',
        rule_type: 'portfolio_drawdown',
        ts: '2026-10-08 14:30:00',
        severity: 'critical',
        current_drawdown: 9.2,
        max_drawdown: 8.0,
        remediation_action: 'sell_or_reduce',
        note: '当前动态回撤 9.20% 超出高水位止损线 8.00%，建议平仓',
      },
      is_read: false,
    },
    {
      id: 102,
      rule_id: 2,
      symbol: null,
      triggered_at: '2026-10-08T14:35:00Z',
      payload: {
        rule: '95% Parametric VaR超限',
        rule_type: 'portfolio_var',
        ts: '2026-10-08 14:35:00',
        severity: 'warning',
        var_95: 3.4,
        limit_pct: 3.0,
        remediation_action: 'review',
        note: '组合在险价值达到 3.40%，超过限额 3.00%',
      },
      is_read: true,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    mockAlertsApi.stats.mockResolvedValue(sampleStats);
    mockAlertsApi.health.mockResolvedValue(sampleHealth);
    mockAlertsApi.rules.mockResolvedValue(sampleRules);
    mockAlertsApi.events.mockResolvedValue(sampleEvents);
    mockAlertsApi.markRead.mockResolvedValue({ marked: 1 });
    mockAlertsApi.markAllRead.mockResolvedValue({ marked: 2 });
    mockAlertsApi.createRule.mockImplementation((payload) =>
      Promise.resolve({ ...payload, id: 99, created_at: '2026-10-08 15:00:00' }),
    );
    mockAlertsApi.updateRule.mockImplementation((id, payload) =>
      Promise.resolve({ ...payload, id, created_at: '2026-10-08 15:00:00' }),
    );
    mockAlertsApi.deleteRule.mockResolvedValue({ id: 1 });
    mockDeskApi.addExclusion.mockResolvedValue({ ok: true, id: 42 });
  });

  it('正确渲染顶部 4 联风险态势驾驶舱 KPI 卡与数据探针', async () => {
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );

    // 等待页面标题和驾驶舱卡片渲染
    await waitFor(() => {
      expect(screen.getByText('量化智能风控与监控驾驶舱')).toBeInTheDocument();
    });

    // KPI 卡片 1: 今日预警触发
    expect(screen.getByText('今日预警触发总数')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('2 待处理')).toBeInTheDocument();

    // KPI 卡片 2: 致命告警
    expect(screen.getByText('致命告警 (Critical)')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();

    // KPI 卡片 3: 防御态势
    expect(screen.getByText('严重超限')).toBeInTheDocument();

    // KPI 卡片 4: 高频标的与探针
    expect(screen.getByText('高频告警标的 Top 3')).toBeInTheDocument();
    expect(screen.getAllByText('600519.SH').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('✓ 正常')).toBeInTheDocument();
  });

  it('支持 3-Tab 切换与风控规则表格结构化参数 Badges 渲染', async () => {
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('告警事件中心')).toBeInTheDocument();
    });

    // 切换至 Tab 2: 风控规则中枢
    const rulesTabBtn = screen.getByText('风控规则中枢');
    fireEvent.click(rulesTabBtn);

    // 验证规则行渲染
    await waitFor(() => {
      expect(screen.getByText('动态高水位回撤止损')).toBeInTheDocument();
      expect(screen.getByText('95% Parametric VaR超限')).toBeInTheDocument();
    });

    // 结构化 Badges 检验：必须包含 "动态回撤 ≥ 8%" 与 "VaR ≥ 3%"，杜绝原始 JSON.stringify
    expect(screen.getByText('动态回撤 ≥ 8%')).toBeInTheDocument();
    expect(screen.getByText('VaR ≥ 3%')).toBeInTheDocument();
    expect(screen.getByText('95% 置信度')).toBeInTheDocument();
    expect(screen.getByText('迟滞 ±0.2%')).toBeInTheDocument();

    // 切换至 Tab 3: 通道与健康监控
    const telemetryTabBtn = screen.getByText('通道与健康监控');
    fireEvent.click(telemetryTabBtn);

    await waitFor(() => {
      expect(screen.getByText('预警数据源与探针观测')).toBeInTheDocument();
      expect(screen.getByText('SSE 实时事件总线通道')).toBeInTheDocument();
      expect(screen.getByText('交易台事前风控联锁 (Interlock)')).toBeInTheDocument();
    });
  });

  it('支持事件穿透诊断、转交易台卖出及一键列入禁买黑名单秒级处置闭环', async () => {
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );

    // 等待事件流卡片渲染
    await waitFor(() => {
      expect(screen.getByText('动态高水位回撤止损')).toBeInTheDocument();
      expect(screen.getByText('高水位回撤 -9.20%（止损阈值 8.00%）')).toBeInTheDocument();
    });

    // 点击事件卡片上的「穿透诊断 🔍」
    const drilldownBtns = screen.getAllByText('穿透诊断 🔍');
    fireEvent.click(drilldownBtns[0]);

    // 弹出详情弹窗
    await waitFor(() => {
      expect(screen.getByText('预警事件穿透诊断与闭环处置')).toBeInTheDocument();
      expect(screen.getByText('动态高水位当前回撤')).toBeInTheDocument();
      expect(screen.getByText('-9.20%')).toBeInTheDocument();
      expect(screen.getByText('量化风控诊断说明')).toBeInTheDocument();
    });

    // 点击一键「列入禁买名单」
    const addExclusionBtn = screen.getByText('列入禁买名单');
    fireEvent.click(addExclusionBtn);

    await waitFor(() => {
      expect(mockDeskApi.addExclusion).toHaveBeenCalledWith(
        '600519.SH',
        'risk_alert',
        expect.stringContaining('动态高水位回撤止损'),
      );
      expect(screen.getByText('已列入禁买名单')).toBeInTheDocument();
    });

    // 点击「转交易台平仓/卖出」
    const goToDeskBtn = screen.getByText('转交易台平仓/卖出');
    fireEvent.click(goToDeskBtn);

    expect(mockedNavigate).toHaveBeenCalledWith('/desk?symbol=600519.SH&action=sell');
  });

  it('新建规则时支持预设量化模板库一键导入填单', async () => {
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('+ 新建风控规则')).toBeInTheDocument();
    });

    // 打开新建模态框
    fireEvent.click(screen.getByText('+ 新建风控规则'));

    await waitFor(() => {
      expect(screen.getByText('新建量化预警规则')).toBeInTheDocument();
      expect(screen.getByText('🚀 快捷套用量化风控模板库')).toBeInTheDocument();
      expect(screen.getByText('公募双十合规·单股持仓超限守卫')).toBeInTheDocument();
    });

    // 点击套用「公募双十合规·单股持仓超限守卫」模板
    fireEvent.click(screen.getByText('公募双十合规·单股持仓超限守卫'));

    // 验证表单自动填充
    const nameInput = screen.getByPlaceholderText('如：茅台高水位回撤监控') as HTMLInputElement;
    expect(nameInput.value).toBe('公募双十合规·单股持仓超限守卫');

    // 提交保存
    fireEvent.click(screen.getByText('确认创建'));

    await waitFor(() => {
      expect(mockAlertsApi.createRule).toHaveBeenCalledWith(
        expect.objectContaining({
          name: '公募双十合规·单股持仓超限守卫',
          rule_type: 'portfolio_risk',
          scope: 'global',
          enabled: true,
        }),
      );
    });
  });

  it('全部标记已读正常触发后端 API', async () => {
    render(
      <MemoryRouter>
        <Alerts />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('全部标记已读')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('全部标记已读'));

    await waitFor(() => {
      expect(mockAlertsApi.markAllRead).toHaveBeenCalled();
    });
  });
});
