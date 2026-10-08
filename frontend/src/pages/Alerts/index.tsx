/**
 * 预警中心（/alerts）：
 *   P0/P1/P2 工业级量化风控监控驾驶舱演进：
 *   - 顶部全局 4 联风险态势驾驶舱 (AlertStatsCards)
 *   - 3-Tab 量化工作台：【告警事件中心】、【风控规则中枢】、【通道与健康监控】
 *   - 10 类运筹学/量化规则治理与预设量化模板库 (RuleDrawerModal)
 *   - 穿透诊断与秒级交易处置闭环 (AlertDetailModal -> 交易台卖出 / 禁买黑名单)
 *   - 彻底废除原始 JSON.stringify，全面徽标结构化渲染
 */
import { useCallback, useEffect, useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '@/api/client';
import {
  alertsApi,
  type AlertEventItem,
  type AlertHealth,
  type AlertRule,
  type AlertRulePayload,
  type AlertRuleType,
  type AlertSeverity,
  type AlertStats,
} from '@/api/alerts';
import { EmptyState, ErrorState, LoadingState, Modal, PageHeader } from '@/components/ui';
import { AlertStatsCards } from './AlertStatsCards';
import { AlertDetailModal } from './AlertDetailModal';
import {
  RuleDrawerModal,
  RULE_TYPE_META,
  SCOPE_LABEL,
} from './RuleDrawerModal';
import { fmtNum } from '@/utils/format';

const UNREAD_SCAN_LIMIT = 200;
const EVENT_WINDOW = 100;

export default function Alerts() {
  const navigate = useNavigate();

  // Tab 状态
  const [activeTab, setActiveTab] = useState<'events' | 'rules' | 'telemetry'>('events');

  // 数据状态
  const [stats, setStats] = useState<AlertStats | null>(null);
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [events, setEvents] = useState<AlertEventItem[] | null>(null);
  const [unread, setUnread] = useState<{ count: number; saturated: boolean } | null>(null);
  const [health, setHealth] = useState<AlertHealth | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);

  // 加载与交互状态
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // 模态弹窗与抽屉
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);
  const [savingRule, setSavingRule] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<AlertRule | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);

  // 事件穿透诊断弹窗
  const [selectedEvent, setSelectedEvent] = useState<AlertEventItem | null>(null);

  // 事件多维过滤状态
  const [filterSeverity, setFilterSeverity] = useState<AlertSeverity | ''>('');
  const [filterRuleType, setFilterRuleType] = useState<AlertRuleType | ''>('');
  const [filterSymbol, setFilterSymbol] = useState('');
  const [filterUnreadOnly, setFilterUnreadOnly] = useState(false);

  // 规则过滤
  const [ruleSearch, setRuleSearch] = useState('');

  /** 加载健康状态 */
  const loadHealth = useCallback(async () => {
    try {
      const h = await alertsApi.health();
      setHealth(h);
      setHealthErr(null);
    } catch (e) {
      setHealth(null);
      setHealthErr(e instanceof ApiError ? e.message : '预警数据源健康状态读取失败');
    }
  }, []);

  /** 加载全库未读数 */
  const loadUnread = useCallback(async () => {
    try {
      const rows = await alertsApi.events(true, UNREAD_SCAN_LIMIT);
      setUnread({ count: rows.length, saturated: rows.length >= UNREAD_SCAN_LIMIT });
    } catch {
      setUnread(null);
    }
  }, []);

  /** 加载驾驶舱 KPI 统计 */
  const loadStats = useCallback(async () => {
    try {
      const s = await alertsApi.stats();
      setStats(s);
    } catch {
      // 容错兜底
    }
  }, []);

  /** 加载事件列表（支持多维过滤） */
  const loadEvents = useCallback(async () => {
    try {
      const es = await alertsApi.events({
        unread: filterUnreadOnly,
        limit: EVENT_WINDOW,
        severity: filterSeverity,
        rule_type: filterRuleType,
        symbol: filterSymbol,
      });
      setEvents(es);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载预警事件流失败');
    }
  }, [filterUnreadOnly, filterSeverity, filterRuleType, filterSymbol]);

  /** 加载规则列表 */
  const loadRules = useCallback(async () => {
    try {
      const rs = await alertsApi.rules();
      setRules(rs);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载预警规则列表失败');
    }
  }, []);

  /** 全量初始化加载 */
  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await Promise.all([loadStats(), loadRules(), loadEvents(), loadUnread(), loadHealth()]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '预警中心数据加载失败');
    } finally {
      setLoading(false);
    }
  }, [loadStats, loadRules, loadEvents, loadUnread, loadHealth]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // 轮询刷新：30s 同步事件、统计与探针
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadStats();
        void loadEvents();
        void loadUnread();
        void loadHealth();
      }
    }, 30_000);
    return () => clearInterval(t);
  }, [loadStats, loadEvents, loadUnread, loadHealth]);

  const unreadCount = unread?.count ?? 0;
  const unreadSaturated = unread?.saturated ?? false;

  // 过滤后的规则列表
  const filteredRules = useMemo(() => {
    if (!rules) return [];
    if (!ruleSearch.trim()) return rules;
    const q = ruleSearch.trim().toLowerCase();
    return rules.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.rule_type.toLowerCase().includes(q) ||
        (r.symbol && r.symbol.toLowerCase().includes(q)),
    );
  }, [rules, ruleSearch]);

  // 规则启停切换
  const toggleEnabled = async (r: AlertRule) => {
    if (togglingId != null) return;
    const next = !r.enabled;
    setTogglingId(r.id);
    setError(null);
    setRules((prev) => prev?.map((x) => (x.id === r.id ? { ...x, enabled: next } : x)) ?? prev);
    try {
      await alertsApi.updateRule(r.id, {
        name: r.name,
        rule_type: r.rule_type,
        scope: r.scope,
        symbol: r.symbol,
        params: r.params,
        channels: r.channels,
        cooldown_minutes: r.cooldown_minutes,
        enabled: next,
      });
      void loadStats();
    } catch (e) {
      setRules((prev) => prev?.map((x) => (x.id === r.id ? { ...x, enabled: r.enabled } : x)) ?? prev);
      setError(e instanceof ApiError ? e.message : '切换规则启用状态失败');
    } finally {
      setTogglingId(null);
    }
  };

  // 保存规则（新建或更新）
  const handleSaveRule = async (payload: Omit<AlertRulePayload, 'enabled'>) => {
    setSavingRule(true);
    setError(null);
    try {
      if (editingRule) {
        const latestEnabled =
          rules?.find((x) => x.id === editingRule.id)?.enabled ?? editingRule.enabled;
        await alertsApi.updateRule(editingRule.id, { ...payload, enabled: latestEnabled });
        setSuccessMsg(`规则 [${payload.name}] 已成功更新`);
      } else {
        await alertsApi.createRule({ ...payload, enabled: true });
        setSuccessMsg(`预警规则 [${payload.name}] 创建成功并已上线值守`);
      }
      setRuleModalOpen(false);
      setEditingRule(null);
      await Promise.all([loadRules(), loadStats()]);
    } finally {
      setSavingRule(false);
    }
  };

  // 删除规则
  const handleDeleteRule = async (id: number) => {
    try {
      await alertsApi.deleteRule(id);
      setSuccessMsg('预警规则已删除');
      await Promise.all([loadRules(), loadStats()]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '删除规则失败');
    }
  };

  // 标记全部已读
  const handleMarkAllRead = async () => {
    try {
      await alertsApi.markAllRead();
      setSuccessMsg('所有预警事件已标记为已读');
      await Promise.all([loadEvents(), loadUnread(), loadStats()]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '标记全部已读失败');
    }
  };

  // 标记单条已读
  const handleMarkOneRead = async (id: number) => {
    try {
      await alertsApi.markRead([id]);
      setEvents((prev) => prev?.map((x) => (x.id === id ? { ...x, is_read: true } : x)) ?? prev);
      if (selectedEvent && selectedEvent.id === id) {
        setSelectedEvent({ ...selectedEvent, is_read: true });
      }
      void loadUnread();
      void loadStats();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '标记已读失败');
    }
  };

  // 渲染参数 Badges
  const renderParamBadges = (r: AlertRule) => {
    const p = r.params || {};
    const t = r.rule_type;
    const badges: Array<{ label: string; highlight?: boolean }> = [];

    if (t === 'portfolio_drawdown') {
      badges.push({ label: `动态回撤 ≥ ${p.max_drawdown ?? 8}%`, highlight: true });
      if (p.benchmark) badges.push({ label: `基准: ${p.benchmark}` });
    } else if (t === 'portfolio_var') {
      badges.push({ label: `VaR ≥ ${p.limit_pct ?? 3}%`, highlight: true });
      badges.push({ label: `${Math.round((p.confidence ?? 0.95) * 100)}% 置信度` });
      badges.push({ label: `${p.time_horizon ?? 1}日` });
    } else if (t === 'portfolio_risk') {
      badges.push({
        label: `${p.factor ?? '敞口'} ${p.direction === 'below' ? '≤' : '≥'} ${p.threshold ?? 20}%`,
        highlight: true,
      });
    } else if (t === 'liquidity_stress') {
      const metricName =
        p.metric === 'seal_ratio'
          ? '封单压迫比'
          : p.metric === 'turnover_burst'
            ? '换手爆发'
            : 'Amihud冲击';
      badges.push({ label: `${metricName} ≥ ${p.threshold ?? 1.5}`, highlight: true });
    } else if (t === 'price_pct') {
      badges.push({ label: `涨跌 ≥ ±${p.threshold ?? 3}%`, highlight: true });
    } else if (t === 'price_cross') {
      badges.push({
        label: `${p.direction === 'down' ? '下穿' : '上穿'} ￥${p.price ?? 0}`,
        highlight: true,
      });
    } else if (t === 'volume_spike') {
      badges.push({ label: `放量 ≥ ${p.k ?? 3}倍`, highlight: true });
      badges.push({ label: `${p.window ?? 20}日均量` });
    } else if (t === 'score_topk') {
      badges.push({ label: `Top-${p.k ?? 50} 迁移`, highlight: true });
    } else if (t === 'factor_quantile') {
      badges.push({
        label: `${p.factor ?? '因子'} 突破 ${Math.round((p.quantile ?? 0.95) * 100)}%分位`,
        highlight: true,
      });
      badges.push({ label: `${p.window ?? 250}日` });
    } else if (t === 'data_health') {
      badges.push({ label: `监控: ${p.metric ?? 'disk'}`, highlight: true });
      if (p.threshold) badges.push({ label: `阈值: ${p.threshold}%` });
    }

    if (p.hysteresis) {
      badges.push({ label: `迟滞 ±${p.hysteresis}%` });
    }

    return (
      <div className="flex flex-wrap items-center gap-1">
        {badges.map((b, idx) => (
          <span
            key={idx}
            className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
              b.highlight
                ? 'border border-brand-200 bg-brand-50 text-brand-700'
                : 'border border-hair bg-surface-sunken text-ink-secondary'
            }`}
          >
            {b.label}
          </span>
        ))}
      </div>
    );
  };

  // 严重度标签渲染
  const renderSeverityBadge = (severity?: AlertSeverity) => {
    const sev = severity || 'info';
    if (sev === 'critical') {
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-danger/30 bg-danger-bg px-2 py-0.5 text-2xs font-bold text-danger">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-danger" />
          Critical
        </span>
      );
    }
    if (sev === 'warning') {
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-warn/30 bg-warn-bg px-2 py-0.5 text-2xs font-semibold text-warn">
          <span className="h-1.5 w-1.5 rounded-full bg-warn" />
          Warning
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-brand-200 bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
        <span className="h-1.5 w-1.5 rounded-full bg-brand-500" />
        Info
      </span>
    );
  };

  // 格式化事件快照
  const renderEventSummary = (e: AlertEventItem) => {
    const p = e.payload ?? {};
    const bits: string[] = [];
    if (p.rule_type === 'portfolio_drawdown') {
      bits.push(
        `高水位回撤 -${Number(p.current_drawdown ?? 0).toFixed(2)}%（止损阈值 ${Number(p.max_drawdown ?? 0).toFixed(2)}%）`,
      );
    } else if (p.rule_type === 'portfolio_var') {
      bits.push(
        `95% VaR ${Number(p.var_95 ?? 0).toFixed(2)}%（风控限额 ${Number(p.limit_pct ?? 0).toFixed(2)}%）`,
      );
    } else if (p.rule_type === 'price_pct') {
      bits.push(`涨跌 ${Number(p.pct ?? 0).toFixed(2)}%（阈值 ±${Number(p.threshold_pct ?? 0)}%）`);
    } else if (p.rule_type === 'price_cross') {
      bits.push(`${p.direction === 'up' ? '上穿' : '下穿'} ￥${Number(p.threshold_price ?? 0)}`);
    } else if (p.rule_type === 'volume_spike') {
      bits.push(`现量 ${fmtNum(Number(p.volume_hand ?? 0))} 手，${Number(p.k ?? 0)} 倍均量`);
    } else if (p.rule_type === 'score_topk') {
      bits.push(`${p.action === 'enter' ? '进入' : '跌出'} Top-${p.k}`);
    } else if (p.rule_type === 'factor_quantile') {
      bits.push(`${p.factor} 突破 ${Number(p.quantile)} 分位`);
    } else if (p.rule_type === 'liquidity_stress') {
      bits.push(`流动性挤压: ${p.metric || 'Amihud'} 冲击指数 ${Number(p.threshold ?? 0)}`);
    } else if (p.rule_type === 'data_health') {
      if (p.metric === 'disk') {
        bits.push(`磁盘水位 ${Number(p.usage_percent ?? 0).toFixed(1)}%`);
      } else if (p.metric === 'pipeline') {
        bits.push(`流水线失败 ×${Number(p.failed_jobs ?? 0)}`);
      } else {
        bits.push('底层行情与数据源降级');
      }
    }
    return (
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-secondary">
        <span>{bits.join(' · ') || '触发预警风控指标'}</span>
        {p.note && <span className="text-2xs text-ink-muted">({String(p.note)})</span>}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* 顶部标题栏与全局动作 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader
          title="量化智能风控与监控驾驶舱"
          sub="动态高水位回撤 · 95% Parametric VaR · Amihud 冲击度量 · 三级防御体系与秒级交易处置闭环"
        />
        <div className="flex flex-wrap items-center gap-2">
          {unreadCount > 0 && (
            <span className="rounded-full bg-danger-bg px-2.5 py-0.5 text-xs font-semibold text-danger">
              {unreadSaturated ? `≥${unreadCount} 条未读` : `${unreadCount} 条未读`}
            </span>
          )}
          <button
            type="button"
            onClick={() => void handleMarkAllRead()}
            disabled={!unreadCount}
            className="rounded border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-300 hover:text-brand-600 disabled:opacity-40"
          >
            全部标记已读
          </button>
          <button
            type="button"
            onClick={() => {
              setEditingRule(null);
              setRuleModalOpen(true);
            }}
            className="rounded bg-brand-500 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-600 shadow-sm"
          >
            + 新建风控规则
          </button>
        </div>
      </div>

      {/* 顶部 4 联风险态势驾驶舱 */}
      <AlertStatsCards
        stats={stats}
        health={health}
        loading={loading}
        onFilterSeverity={(sev) => {
          setFilterSeverity(sev);
          setActiveTab('events');
        }}
        onFilterSymbol={(sym) => {
          setFilterSymbol(sym);
          setActiveTab('events');
        }}
      />

      {/* 全局消息与错误反馈 */}
      {error && (
        <div className="rounded-lg border border-warn/30 bg-warn-bg px-4 py-2.5 text-xs text-warn flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-warn hover:opacity-80">✕</button>
        </div>
      )}
      {successMsg && (
        <div className="rounded-lg border border-success/30 bg-success-bg px-4 py-2.5 text-xs text-success flex items-center justify-between">
          <span>{successMsg}</span>
          <button onClick={() => setSuccessMsg(null)} className="text-success hover:opacity-80">✕</button>
        </div>
      )}

      {/* 数据源降级预警横幅 */}
      {healthErr && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warn/30 bg-warn-bg p-3.5 text-xs text-warn">
          <span>预警数据源健康状态不可读：{healthErr}（当前无法确认 Top-K 规则是否正常评估）</span>
          <button
            type="button"
            onClick={() => void loadHealth()}
            className="rounded border border-warn/30 bg-surface px-2.5 py-1 text-2xs font-medium text-warn hover:bg-warn-bg"
          >
            重试
          </button>
        </div>
      )}
      {health?.degraded && (
        <div role="alert" className="rounded-lg border border-danger/30 bg-danger-bg p-3.5 text-xs text-danger">
          <div className="font-semibold flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-danger animate-ping" />
            预警数据源降级预警：Top-K 迁移规则 (score_topk) 本轮评估已被跳过，将不会产生预警事件。
          </div>
          <div className="mt-1 text-2xs text-danger/90">
            根因：{health.reason ?? '未捕获具体错误'}
            {health.snapshot_date ? ` · 股票池快照日期: ${health.snapshot_date}` : ' · 股票池快照缺失'}
            {health.checked_at ? ` · 探测时间: ${health.checked_at}` : ''}
            {health.detail ? ` · ${health.detail}` : ''}
          </div>
        </div>
      )}

      {/* 3-Tab 导航页签 */}
      <div className="flex items-center border-b border-hair text-xs">
        <button
          type="button"
          onClick={() => setActiveTab('events')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'events'
              ? 'border-brand-500 text-brand-600 font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>告警事件中心</span>
          {unreadCount > 0 && (
            <span className="rounded-full bg-danger-bg px-1.5 text-[10px] font-bold text-danger">
              {unreadCount}
            </span>
          )}
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('rules')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'rules'
              ? 'border-brand-500 text-brand-600 font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>风控规则中枢</span>
          <span className="rounded-full bg-surface-sunken px-1.5 text-[10px] text-ink-muted">
            {rules?.length ?? 0} 条
          </span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('telemetry')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'telemetry'
              ? 'border-brand-500 text-brand-600 font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>通道与健康监控</span>
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              health?.degraded ? 'bg-danger' : 'bg-success'
            }`}
          />
        </button>
      </div>

      {/* ==================== TAB 1: 告警事件中心 ==================== */}
      {activeTab === 'events' && (
        <div className="space-y-3">
          {/* 多维过滤工具栏 */}
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-hair bg-surface p-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {/* 严重度过滤 */}
              <div className="flex items-center gap-1 rounded bg-surface-sunken p-0.5">
                {(
                  [
                    { key: '', label: '全部严重度' },
                    { key: 'critical', label: '致命 Critical' },
                    { key: 'warning', label: '告警 Warning' },
                    { key: 'info', label: '提示 Info' },
                  ] as const
                ).map((opt) => (
                  <button
                    key={opt.key}
                    type="button"
                    onClick={() => setFilterSeverity(opt.key)}
                    className={`rounded px-2 py-1 text-2xs transition-colors ${
                      filterSeverity === opt.key
                        ? 'bg-surface font-semibold text-brand-600 shadow-xs'
                        : 'text-ink-secondary hover:text-ink'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              {/* 规则类型下拉 */}
              <select
                value={filterRuleType}
                onChange={(e) => setFilterRuleType(e.target.value as AlertRuleType | '')}
                className="rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink outline-none"
              >
                <option value="">全部规则类型 (10 类)</option>
                {Object.entries(RULE_TYPE_META).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v.label} ({v.category})
                  </option>
                ))}
              </select>

              {/* 标的代码输入 */}
              <div className="flex items-center gap-1">
                <input
                  type="text"
                  placeholder="搜索标的代码 (如 600519.SH)"
                  value={filterSymbol}
                  onChange={(e) => setFilterSymbol(e.target.value.toUpperCase())}
                  className="w-44 rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink outline-none uppercase placeholder:normal-case focus:border-brand-300"
                />
                {filterSymbol && (
                  <button
                    type="button"
                    onClick={() => setFilterSymbol('')}
                    className="text-xs text-ink-muted hover:text-ink"
                  >
                    ✕
                  </button>
                )}
              </div>

              {/* 仅看未读 */}
              <label className="flex items-center gap-1.5 text-2xs text-ink-secondary cursor-pointer">
                <input
                  type="checkbox"
                  checked={filterUnreadOnly}
                  onChange={(e) => setFilterUnreadOnly(e.target.checked)}
                />
                仅看未读
              </label>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void loadEvents()}
                className="rounded border border-hair bg-surface px-2.5 py-1 text-2xs text-ink-secondary hover:border-brand-300"
              >
                刷新事件流
              </button>
            </div>
          </div>

          {/* 事件流卡片列表 */}
          {loading && events === null ? (
            <LoadingState text="正在获取预警事件流…" />
          ) : events === null ? (
            <ErrorState message={error ?? '事件加载失败'} onRetry={() => void loadEvents()} />
          ) : events.length === 0 ? (
            <EmptyState
              title="暂无匹配预警事件"
              hint={
                filterSeverity || filterRuleType || filterSymbol || filterUnreadOnly
                  ? '当前筛选条件下没有事件，请尝试清空筛选条件'
                  : '策略值守状态良好，暂未触发任何风控红线'
              }
            />
          ) : (
            <div className="space-y-2">
              {events.map((e) => {
                const p = e.payload ?? {};
                const sev = (p.severity as AlertSeverity) || 'info';
                return (
                  <div
                    key={e.id}
                    className={`flex flex-col gap-2 rounded-lg border p-3.5 transition-all hover:border-brand-300 ${
                      e.is_read
                        ? 'border-hair bg-surface'
                        : 'border-danger/20 bg-danger-bg/20 shadow-xs'
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        {renderSeverityBadge(sev)}
                        <span className="text-xs font-semibold text-ink">
                          {p.rule || `风控规则 #${e.rule_id}`}
                        </span>
                        {e.symbol && (
                          <button
                            type="button"
                            onClick={() => setFilterSymbol(e.symbol!)}
                            title="点击筛选此标的"
                            className="num rounded bg-surface-sunken px-1.5 py-0.5 text-xs font-medium text-ink hover:text-brand-600"
                          >
                            {e.symbol}
                          </button>
                        )}
                        {!e.is_read && (
                          <span className="rounded bg-danger px-1 text-[10px] font-bold text-white">
                            NEW
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-3 text-2xs text-ink-muted">
                        <span className="num">
                          {p.ts ?? e.triggered_at.replace('T', ' ').slice(0, 19)}
                        </span>
                      </div>
                    </div>

                    {/* 指标快照与量化说明 */}
                    <div>{renderEventSummary(e)}</div>

                    {/* 快捷处置动作 */}
                    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-hair/60 pt-2 text-2xs">
                      <div className="flex items-center gap-2">
                        {p.remediation_action === 'sell_or_reduce' && (
                          <span className="rounded border border-danger/30 bg-danger-bg px-2 py-0.5 font-semibold text-danger">
                            处置建议: 卖出/减仓
                          </span>
                        )}
                        {p.remediation_action === 'add_exclusion' && (
                          <span className="rounded border border-warn/30 bg-warn-bg px-2 py-0.5 font-semibold text-warn">
                            处置建议: 禁买名单拦截
                          </span>
                        )}
                        {p.remediation_action === 'review' && (
                          <span className="rounded border border-brand-200 bg-brand-50 px-2 py-0.5 font-medium text-brand-700">
                            处置建议: 专员复核
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-1.5">
                        {!e.is_read && (
                          <button
                            type="button"
                            onClick={() => void handleMarkOneRead(e.id)}
                            className="rounded border border-hair px-2 py-1 text-ink-secondary hover:border-brand-300 hover:text-brand-600"
                          >
                            标记已读
                          </button>
                        )}
                        {e.symbol && (
                          <button
                            type="button"
                            onClick={() =>
                              navigate(
                                `/desk?symbol=${encodeURIComponent(e.symbol!)}&action=sell`,
                              )
                            }
                            className="rounded border border-brand-200 bg-brand-50 px-2.5 py-1 font-medium text-brand-700 hover:bg-brand-100"
                          >
                            转交易台卖出 ↗
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setSelectedEvent(e)}
                          className="rounded bg-surface-sunken px-2.5 py-1 font-medium text-ink hover:bg-hair2"
                        >
                          穿透诊断 🔍
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ==================== TAB 2: 风控规则中枢 ==================== */}
      {activeTab === 'rules' && (
        <div className="space-y-3">
          {/* 规则中枢工具栏 */}
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-hair bg-surface p-3">
            <div className="flex items-center gap-2 text-xs">
              <input
                type="text"
                placeholder="搜索规则名称 / 标的 / 算法类型…"
                value={ruleSearch}
                onChange={(e) => setRuleSearch(e.target.value)}
                className="w-64 rounded border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300"
              />
              <span className="text-2xs text-ink-muted">
                共 {rules?.length ?? 0} 条规则（
                {rules?.filter((r) => r.enabled).length ?? 0} 启用）
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setEditingRule(null);
                  setRuleModalOpen(true);
                }}
                className="rounded border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs font-medium text-brand-700 hover:bg-brand-100"
              >
                🚀 导入预设量化模板
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditingRule(null);
                  setRuleModalOpen(true);
                }}
                className="rounded bg-brand-500 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
              >
                + 新建规则
              </button>
            </div>
          </div>

          {/* 规则管理表格 */}
          {loading && rules === null ? (
            <LoadingState text="正在获取规则列表…" />
          ) : rules === null ? (
            <ErrorState message={error ?? '规则列表加载失败'} onRetry={() => void loadRules()} />
          ) : filteredRules.length === 0 ? (
            <EmptyState
              title="暂无匹配预警规则"
              hint="点击右上角「导入预设量化模板」或「新建规则」开始配置风控"
            />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-hair bg-surface shadow-xs">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-hair bg-surface-sunken text-2xs text-ink-muted">
                    <th className="py-2.5 px-3 font-semibold">状态</th>
                    <th className="py-2.5 px-3 font-semibold">严重度</th>
                    <th className="py-2.5 px-3 font-semibold">规则名称</th>
                    <th className="py-2.5 px-3 font-semibold">算法分类与类型</th>
                    <th className="py-2.5 px-3 font-semibold">作用域</th>
                    <th className="py-2.5 px-3 font-semibold">运算参数定义 (结构化)</th>
                    <th className="py-2.5 px-3 font-semibold">渠道</th>
                    <th className="py-2.5 px-3 font-semibold">冷却</th>
                    <th className="py-2.5 px-3 font-semibold text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hair/60">
                  {filteredRules.map((r) => {
                    const sev = r.params?.severity ?? 'warning';
                    const meta = RULE_TYPE_META[r.rule_type] ?? {
                      label: r.rule_type,
                      desc: '',
                      category: '自定义',
                    };
                    return (
                      <tr key={r.id} className="hover:bg-surface-sunken/50 transition-colors">
                        <td className="py-2.5 px-3">
                          <button
                            type="button"
                            role="switch"
                            aria-checked={r.enabled}
                            disabled={togglingId != null}
                            onClick={() => void toggleEnabled(r)}
                            title={r.enabled ? '点击停用该规则' : '点击启用该规则'}
                            className={`relative inline-flex h-4 w-8 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
                              r.enabled ? 'bg-brand-500' : 'bg-hair2'
                            }`}
                          >
                            <span
                              className={`inline-block h-3 w-3 transform rounded-full bg-surface shadow transition-transform ${
                                r.enabled ? 'translate-x-4' : 'translate-x-0.5'
                              }`}
                            />
                          </button>
                        </td>

                        <td className="py-2.5 px-3">{renderSeverityBadge(sev)}</td>

                        <td className="py-2.5 px-3">
                          <div
                            className={`font-semibold ${
                              r.enabled ? 'text-ink' : 'text-ink-muted line-through'
                            }`}
                          >
                            {r.name}
                          </div>
                        </td>

                        <td className="py-2.5 px-3">
                          <div className="flex items-center gap-1.5">
                            <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-[10px] text-ink-muted">
                              {meta.category}
                            </span>
                            <span className="font-medium text-ink">{meta.label}</span>
                          </div>
                        </td>

                        <td className="py-2.5 px-3 text-ink-secondary">
                          <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs">
                            {SCOPE_LABEL[r.scope]}
                          </span>
                          {r.symbol && (
                            <span className="num ml-1 font-semibold text-ink">{r.symbol}</span>
                          )}
                        </td>

                        <td className="py-2.5 px-3">{renderParamBadges(r)}</td>

                        <td className="py-2.5 px-3 text-2xs text-ink-muted">
                          {r.channels.join('/')}
                        </td>

                        <td className="num py-2.5 px-3 text-2xs text-ink-secondary">
                          {r.cooldown_minutes}m
                        </td>

                        <td className="py-2.5 px-3 text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => {
                                setEditingRule(r);
                                setRuleModalOpen(true);
                              }}
                              className="rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-300 hover:text-brand-600"
                            >
                              编辑
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmDelete(r)}
                              className="rounded border border-danger/30 px-2 py-0.5 text-2xs text-danger hover:bg-danger-bg"
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ==================== TAB 3: 通道与健康监控 ==================== */}
      {activeTab === 'telemetry' && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* 数据健康探针 */}
          <div className="flex flex-col rounded-lg border border-hair bg-surface p-4">
            <div className="flex items-center justify-between border-b border-hair pb-3">
              <div>
                <h2 className="text-sm font-semibold text-ink">预警数据源与探针观测</h2>
                <div className="mt-0.5 text-2xs text-ink-muted">
                  Top-K 选股池、历史行情与截面因子健康度探测
                </div>
              </div>
              <span
                className={`rounded px-2 py-0.5 text-2xs font-bold ${
                  health?.degraded
                    ? 'bg-danger-bg text-danger'
                    : health?.checked_at
                      ? 'bg-success-bg text-success'
                      : 'bg-surface-sunken text-ink-muted'
                }`}
              >
                {health?.degraded ? '⚠️ 探针告警降级' : health?.checked_at ? '✓ 探测正常' : '未评估'}
              </span>
            </div>

            <div className="mt-3 space-y-3 text-xs">
              <div className="flex items-center justify-between border-b border-hair/50 py-1.5">
                <span className="text-ink-secondary">Top-K 股票池快照日期</span>
                <span className="num font-semibold text-ink">
                  {health?.snapshot_date ?? '快照缺失'}
                </span>
              </div>
              <div className="flex items-center justify-between border-b border-hair/50 py-1.5">
                <span className="text-ink-secondary">最近健康探测时间</span>
                <span className="num text-ink">{health?.checked_at ?? '尚未执行调度评估'}</span>
              </div>
              <div className="flex items-center justify-between border-b border-hair/50 py-1.5">
                <span className="text-ink-secondary">最近正常在线时间戳</span>
                <span className="num text-ink">{health?.last_ok_at ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between py-1.5">
                <span className="text-ink-secondary">降级与跳过原因</span>
                <span className="text-ink-muted">
                  {health?.reason ? (
                    <span className="font-semibold text-danger">{health.reason}</span>
                  ) : (
                    '无降级'
                  )}
                </span>
              </div>

              {health?.detail && (
                <div className="rounded bg-surface-sunken p-2.5 text-2xs font-mono text-ink-secondary break-all">
                  {health.detail}
                </div>
              )}

              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => void loadHealth()}
                  className="rounded border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-300"
                >
                  立即重测探针
                </button>
              </div>
            </div>
          </div>

          {/* 实时通知通道与交易联锁 */}
          <div className="flex flex-col rounded-lg border border-hair bg-surface p-4">
            <div className="flex items-center justify-between border-b border-hair pb-3">
              <div>
                <h2 className="text-sm font-semibold text-ink">通知推送通道与交易联锁</h2>
                <div className="mt-0.5 text-2xs text-ink-muted">
                  SSE 实时事件总线 · Webhook 外部协同 · 交易台联锁熔断
                </div>
              </div>
              <span className="rounded bg-success-bg px-2 py-0.5 text-2xs font-semibold text-success">
                在线连接
              </span>
            </div>

            <div className="mt-3 space-y-3.5 text-xs">
              <div className="rounded-lg border border-hair bg-surface-sunken p-3">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-ink">SSE 实时事件总线通道</span>
                  <span className="rounded bg-success-bg px-1.5 text-2xs font-bold text-success">
                    Active
                  </span>
                </div>
                <div className="mt-1 text-2xs text-ink-secondary leading-relaxed">
                  通过长连接 SSE 将告警事件毫秒级广播至终端前端；心跳探测正常，支持网络断开指数回退自愈重连。
                </div>
              </div>

              <div className="rounded-lg border border-hair bg-surface-sunken p-3">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-ink">Webhook 外部推送中枢</span>
                  <span className="text-2xs text-ink-muted">NOTIFY_WEBHOOK_URL</span>
                </div>
                <div className="mt-1 text-2xs text-ink-secondary leading-relaxed">
                  支持钉钉群机器人、飞书机器人、企业微信与自建告警网关接入。致命告警 (Critical) 触发时自动将处置建议与指标快照推送到外部工作群。
                </div>
              </div>

              <div className="rounded-lg border border-danger/20 bg-danger-bg/20 p-3">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-danger">交易台事前风控联锁 (Interlock)</span>
                  <span className="rounded bg-danger px-1.5 text-[10px] font-bold text-white">
                    保护中
                  </span>
                </div>
                <div className="mt-1 text-2xs text-danger/90 leading-relaxed">
                  预警中心与【交易执行中心】建立双向闭环。对于流动性枯竭或合规触顶标的，风控员可一键列入禁买黑名单，实盘下单前事前预检将强制拦截阻断。
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 规则新建与编辑抽屉/模态弹窗 */}
      <RuleDrawerModal
        open={ruleModalOpen}
        editingRule={editingRule}
        saving={savingRule}
        onClose={() => {
          setRuleModalOpen(false);
          setEditingRule(null);
        }}
        onSubmit={handleSaveRule}
      />

      {/* 事件穿透诊断与闭环处置弹窗 */}
      <AlertDetailModal
        event={selectedEvent}
        open={selectedEvent !== null}
        onClose={() => setSelectedEvent(null)}
        onMarkRead={handleMarkOneRead}
        onExclusionAdded={() => {
          setSuccessMsg('已成功列入禁买黑名单！');
        }}
      />

      {/* 删除二次确认弹窗 */}
      <Modal
        open={confirmDelete !== null}
        title="确认删除该预警规则？"
        sub={
          confirmDelete
            ? `${confirmDelete.name} · ${RULE_TYPE_META[confirmDelete.rule_type]?.label ?? confirmDelete.rule_type}`
            : undefined
        }
        onClose={() => setConfirmDelete(null)}
        footer={
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setConfirmDelete(null)}
              className="rounded-md border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-200"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => {
                const target = confirmDelete;
                setConfirmDelete(null);
                if (target) void handleDeleteRule(target.id);
              }}
              className="rounded-md bg-danger px-3 py-1.5 text-xs font-medium text-white hover:bg-danger/90"
            >
              确认删除
            </button>
          </div>
        }
      >
        <p className="text-xs leading-relaxed text-ink-secondary">
          删除后该规则将彻底停止评估与调度，历史产生的触发记录保留；此操作不可撤销。
        </p>
      </Modal>
    </div>
  );
}
