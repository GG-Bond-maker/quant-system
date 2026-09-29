/**
 * 预警中心（/alerts，§4.1 Sprint2 MVP）：
 *   左：规则管理（六类规则 CRUD + 参数白名单表单）
 *   右：触发历史（未读徽标 + 批量已读；30s 轮询，评估调度在后端 lifespan）
 *
 * 纪律：触发时间以 payload.ts（本地时间）展示；triggered_at 为 UTC 存储，
 * 仅作排序依据不直接展示，避免时区误读。
 */
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import {
  alertsApi, type AlertEventItem, type AlertHealth, type AlertRule, type AlertRulePayload,
  type AlertRuleParams, type AlertRuleType, type AlertScope,
} from '@/api/alerts';
import { EmptyState, ErrorState, LoadingState, Modal } from '@/components/ui';
import { fmtNum } from '@/utils/format';

/* ==================== 常量 ==================== */
const RULE_TYPE_META: Record<AlertRuleType, { label: string; desc: string }> = {
  price_pct: { label: '涨跌幅', desc: '单日涨跌幅超 ±N%' },
  price_cross: { label: '价格穿越', desc: '上穿/下穿价格阈值' },
  volume_spike: { label: '放量', desc: '现量 > N 日均量 × k' },
  score_topk: { label: 'Top-K 迁移', desc: '进入/跌出模型 Top-K' },
  factor_quantile: { label: '因子分位', desc: '因子值突破 N 日分位' },
  data_health: { label: '数据健康', desc: '磁盘水位/流水线失败/源降级' },
};

const SCOPE_LABEL: Record<AlertScope, string> = {
  symbol: '单标的', watchlist: '我的自选', global: '全局',
};

/** C-4：未读口径独立扫描上限。后端 /events 的 limit ≤ 200 且**不返回未读总数**，
 *  取满即饱和，徽标必须标注"≥"而不能当作精确值。 */
const UNREAD_SCAN_LIMIT = 200;
/** C-4：展示窗口（触发历史列表）与全库未读口径分离，避免再次混淆 */
const EVENT_WINDOW = 50;

/** 空参数构造（按类型给合理默认值，可改） */
const DEFAULT_PARAMS: Record<AlertRuleType, AlertRuleParams> = {
  price_pct: { threshold: 3 },
  price_cross: { price: 100, direction: 'up' },
  volume_spike: { window: 20, k: 3 },
  score_topk: { k: 50 },
  factor_quantile: { factor: 'mom_20', window: 250, quantile: 0.95 },
  data_health: { metric: 'disk', threshold: 90 },
};

/* ==================== 小件 ==================== */
function Card({ title, extra, children }: {
  title: string; extra?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
        {extra}
      </div>
      <div className="min-w-0 flex-1 p-3">{children}</div>
    </div>
  );
}

const fieldCls = 'w-full rounded border border-hair bg-white px-2 py-1.5 text-xs outline-none focus:border-brand-300';

/** 按规则类型渲染参数表单（白名单字段一一对应） */
function ParamsForm({ ruleType, params, onChange }: {
  ruleType: AlertRuleType;
  params: AlertRuleParams;
  onChange: (p: AlertRuleParams) => void;
}) {
  const num = (v: string) => (v === '' ? undefined : Number(v));
  if (ruleType === 'price_pct') {
    return (
      <label className="text-2xs text-ink-secondary">涨跌幅阈值（%）
        <input type="number" step="0.1" min="0.1" max="30" value={params.threshold ?? ''}
          onChange={(e) => onChange({ ...params, threshold: num(e.target.value) })}
          className={fieldCls} />
      </label>
    );
  }
  if (ruleType === 'price_cross') {
    return (
      <div className="grid grid-cols-2 gap-2">
        <label className="text-2xs text-ink-secondary">价格阈值
          <input type="number" step="0.01" value={params.price ?? ''}
            onChange={(e) => onChange({ ...params, price: num(e.target.value) })}
            className={fieldCls} />
        </label>
        <label className="text-2xs text-ink-secondary">方向
          <select value={params.direction ?? 'up'}
            onChange={(e) => onChange({ ...params, direction: e.target.value as 'up' | 'down' })}
            className={fieldCls}>
            <option value="up">上穿</option>
            <option value="down">下穿</option>
          </select>
        </label>
      </div>
    );
  }
  if (ruleType === 'volume_spike') {
    return (
      <div className="grid grid-cols-2 gap-2">
        <label className="text-2xs text-ink-secondary">均量窗口（日）
          <input type="number" min="2" max="120" value={params.window ?? ''}
            onChange={(e) => onChange({ ...params, window: num(e.target.value) })}
            className={fieldCls} />
        </label>
        <label className="text-2xs text-ink-secondary">放量倍数 k
          <input type="number" step="0.1" min="1.2" max="20" value={params.k ?? ''}
            onChange={(e) => onChange({ ...params, k: num(e.target.value) })}
            className={fieldCls} />
        </label>
      </div>
    );
  }
  if (ruleType === 'score_topk') {
    return (
      <label className="text-2xs text-ink-secondary">Top-K
        <input type="number" min="1" max="500" value={params.k ?? ''}
          onChange={(e) => onChange({ ...params, k: num(e.target.value) })}
          className={fieldCls} />
      </label>
    );
  }
  if (ruleType === 'factor_quantile') {
    return (
      <div className="grid grid-cols-3 gap-2">
        <label className="text-2xs text-ink-secondary">因子列
          <input value={params.factor ?? ''} placeholder="如 mom_20"
            onChange={(e) => onChange({ ...params, factor: e.target.value })}
            className={fieldCls} />
        </label>
        <label className="text-2xs text-ink-secondary">窗口（日）
          <input type="number" min="30" max="750" value={params.window ?? ''}
            onChange={(e) => onChange({ ...params, window: num(e.target.value) })}
            className={fieldCls} />
        </label>
        <label className="text-2xs text-ink-secondary">分位
          <input type="number" step="0.01" min="0.5" max="0.99" value={params.quantile ?? ''}
            onChange={(e) => onChange({ ...params, quantile: num(e.target.value) })}
            className={fieldCls} />
        </label>
      </div>
    );
  }
  return (
    <label className="text-2xs text-ink-secondary">健康指标
      <select value={params.metric ?? 'disk'}
        onChange={(e) => onChange({ ...params, metric: e.target.value as 'disk' | 'pipeline' | 'source' })}
        className={fieldCls}>
        <option value="disk">磁盘水位</option>
        <option value="pipeline">流水线失败</option>
        <option value="source">数据源降级</option>
      </select>
    </label>
  );
}

/* ==================== 规则表单（新建 / 编辑共用） ==================== */
/**
 * 表单内部值（与 API 契约解耦：webhook 从 channels 拆出，便于勾选）。
 *
 * 注意：**刻意不含 enabled** —— 启停是列表上的独立开关，可在编辑表单打开期间
 * 被切换。若表单持有打开时的 enabled 快照，保存时会把开关状态回退成旧值
 * （陈旧状态竞态）。故 enabled 一律由父级在提交瞬间取最新权威值合并。
 */
interface RuleFormValues {
  name: string;
  ruleType: AlertRuleType;
  scope: AlertScope;
  symbol: string;
  params: AlertRuleParams;
  webhook: boolean;
  cooldown: number;
}

const BLANK_FORM: RuleFormValues = {
  name: '', ruleType: 'price_pct', scope: 'symbol', symbol: '',
  params: DEFAULT_PARAMS.price_pct, webhook: false, cooldown: 30,
};

/** 把已有规则映射为表单初值（编辑预填；enabled 不进入表单，见 RuleFormValues 注释） */
function ruleToFormValues(r: AlertRule): RuleFormValues {
  return {
    name: r.name,
    ruleType: r.rule_type,
    scope: r.scope,
    symbol: r.symbol ?? '',
    params: r.params ?? DEFAULT_PARAMS[r.rule_type],
    webhook: r.channels.includes('webhook'),
    cooldown: r.cooldown_minutes,
  };
}

/**
 * 新建与编辑共用的规则表单。表单值自持，避免两套逻辑漂移。
 * onSubmit 抛错时由本组件就地展示（不静默吞错）；成功后由父级关闭。
 * 提交的 payload 不含 enabled，由父级按最新状态补齐。
 */
function RuleForm({ initial, submitLabel, saving, onSubmit, onCancel }: {
  initial: RuleFormValues;
  submitLabel: string;
  saving: boolean;
  onSubmit: (payload: Omit<AlertRulePayload, 'enabled'>) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [ruleType, setRuleType] = useState(initial.ruleType);
  const [scope, setScope] = useState(initial.scope);
  const [symbol, setSymbol] = useState(initial.symbol);
  const [params, setParams] = useState<AlertRuleParams>(initial.params);
  const [webhook, setWebhook] = useState(initial.webhook);
  const [cooldown, setCooldown] = useState(initial.cooldown);
  const [localError, setLocalError] = useState<string | null>(null);

  const switchType = (t: AlertRuleType) => {
    setRuleType(t);
    setParams(DEFAULT_PARAMS[t]);
  };

  const submit = async () => {
    if (!name.trim()) { setLocalError('规则名称必填'); return; }
    if (scope === 'symbol' && !symbol.trim()) { setLocalError('单标的规则需填写标的代码'); return; }
    setLocalError(null);
    try {
      await onSubmit({
        name: name.trim(), rule_type: ruleType, scope,
        symbol: scope === 'symbol' ? symbol.trim().toUpperCase() : null,
        params, channels: webhook ? ['sse', 'webhook'] : ['sse'],
        cooldown_minutes: cooldown,
      });
    } catch (e) {
      setLocalError(e instanceof ApiError ? e.message : '保存失败');
    }
  };

  return (
    <>
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-4">
        <label className="text-2xs text-ink-secondary">名称
          <input value={name} onChange={(e) => setName(e.target.value)}
            placeholder="如：茅台大涨提醒" className={fieldCls} />
        </label>
        <label className="text-2xs text-ink-secondary">规则类型
          <select value={ruleType} onChange={(e) => switchType(e.target.value as AlertRuleType)}
            className={fieldCls}>
            {Object.entries(RULE_TYPE_META).map(([k, v]) => (
              <option key={k} value={k}>{v.label} · {v.desc}</option>
            ))}
          </select>
        </label>
        <label className="text-2xs text-ink-secondary">作用域
          <select value={scope} onChange={(e) => setScope(e.target.value as AlertScope)}
            className={fieldCls}>
            <option value="symbol">单标的</option>
            <option value="watchlist">我的自选</option>
            <option value="global">全局</option>
          </select>
        </label>
        {scope === 'symbol' ? (
          <label className="text-2xs text-ink-secondary">标的代码
            <input value={symbol} onChange={(e) => setSymbol(e.target.value)}
              placeholder="600519.SH" className={fieldCls} />
          </label>
        ) : (
          <label className="text-2xs text-ink-secondary">冷却（分钟）
            <input type="number" min="0" max="1440" value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))} className={fieldCls} />
          </label>
        )}
      </div>
      <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-3">
        <ParamsForm ruleType={ruleType} params={params} onChange={setParams} />
        <label className="flex items-center gap-1.5 self-end text-2xs text-ink-secondary">
          <input type="checkbox" checked={webhook}
            onChange={(e) => setWebhook(e.target.checked)} />
          同时推送 Webhook（需后端配置 NOTIFY_WEBHOOK_URL）
        </label>
        {scope === 'symbol' ? (
          <label className="self-end text-2xs text-ink-secondary">冷却（分钟）
            <input type="number" min="0" max="1440" value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))} className={fieldCls} />
          </label>
        ) : <span />}
      </div>
      {localError && (
        <div className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1.5 text-2xs text-red-600">
          {localError}
        </div>
      )}
      <div className="mt-3 flex justify-end gap-2">
        <button onClick={onCancel} disabled={saving}
          className="rounded border border-hair bg-white px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-200 disabled:opacity-50">
          取消
        </button>
        <button onClick={() => void submit()} disabled={saving}
          className="rounded bg-brand-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50">
          {saving ? '保存中…' : submitLabel}
        </button>
      </div>
    </>
  );
}

/** 事件 payload 的紧凑摘要（按规则类型取关键字段，不罗列 JSON） */
function EventSummary({ e }: { e: AlertEventItem }) {
  const p = e.payload ?? {};
  const bits: string[] = [];
  if (p.rule_type === 'price_pct') {
    bits.push(`涨跌 ${Number(p.pct).toFixed(2)}%（阈值 ${Number(p.threshold_pct)}%）`);
  } else if (p.rule_type === 'price_cross') {
    bits.push(`${p.direction === 'up' ? '上穿' : '下穿'} ${Number(p.threshold_price)}`);
  } else if (p.rule_type === 'volume_spike') {
    bits.push(`现量 ${fmtNum(p.volume_hand as number)} 手，${Number(p.k)} 倍均量`);
  } else if (p.rule_type === 'score_topk') {
    bits.push(p.action === 'enter' ? '进入' : '跌出', ` Top${p.k}`);
  } else if (p.rule_type === 'factor_quantile') {
    bits.push(`${p.factor} 突破 ${Number(p.quantile)} 分位`);
  } else if (p.rule_type === 'data_health') {
    if (p.metric === 'disk') {
      bits.push(`磁盘 ${Number(p.usage_percent).toFixed(1)}%`);
    } else if (p.metric === 'pipeline') {
      // C-5：failed_jobs 触顶时后端给了 recent_truncated/recent_limit（两条查询独立），
      // 原实现全丢弃 ⇒ 饱和值 20 被当成精确值；last_error 同样被丢弃
      const saturated = !!p.recent_truncated || !!p.truncated;
      const cap = Number(p.recent_limit ?? p.supplement_limit ?? 0) || null;
      bits.push(`流水线失败 ×${Number(p.failed_jobs)}`
        + (saturated ? `（已达统计上限 ${cap ?? '—'}，实际更多）` : ''));
      if (p.last_error) bits.push(`最近错误：${String(p.last_error)}`);
    } else {
      bits.push('数据源降级');
    }
  }
  return (
    <span className="text-2xs text-ink-secondary">
      {e.symbol ? <span className="num font-medium text-ink">{e.symbol} </span> : null}
      {bits.join(' ')}
    </span>
  );
}

/* ==================== 主组件 ==================== */
export default function Alerts() {
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [events, setEvents] = useState<AlertEventItem[] | null>(null);
  /** C-4：全库未读（独立扫描）；saturated=true 表示触及扫描上限，只能显示 ≥ */
  const [unread, setUnread] = useState<{ count: number; saturated: boolean } | null>(null);
  /** C-22：删除规则需二次确认（与清缓存/熔断同口径） */
  const [confirmDelete, setConfirmDelete] = useState<AlertRule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  /** 编辑目标：非空时展示编辑表单（与新建表单互斥） */
  const [editing, setEditing] = useState<AlertRule | null>(null);
  const [saving, setSaving] = useState(false);
  /** 启停中的规则 id：加锁防连点 */
  const [togglingId, setTogglingId] = useState<number | null>(null);
  /** 首屏加载态（与 error 配合区分"加载中"与"加载失败"） */
  const [loading, setLoading] = useState(true);
  /** 数据源健康（GET /alerts/health）：null=未读到，degraded=true 时 Top-K 规则静默失效 */
  const [health, setHealth] = useState<AlertHealth | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);

  /** 健康状态独立加载：失败**必须可见**（置 healthErr），不得静默当作"正常" */
  const loadHealth = useCallback(async () => {
    try {
      setHealth(await alertsApi.health());
      setHealthErr(null);
    } catch (e) {
      setHealth(null);
      setHealthErr(e instanceof ApiError ? e.message : '预警数据源健康状态读取失败');
    }
  }, []);

  const loadUnread = useCallback(async () => {
    try {
      // C-4：徽标查"全部未读"，不复用 50 条展示窗口（否则窗口外未读永不显示）
      const rows = await alertsApi.events(true, UNREAD_SCAN_LIMIT);
      setUnread({ count: rows.length, saturated: rows.length >= UNREAD_SCAN_LIMIT });
    } catch {
      setUnread(null); // 读不到就显示"未读不可读"，不静默归零
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const [rs, es] = await Promise.all([
        alertsApi.rules(), alertsApi.events(false, EVENT_WINDOW),
      ]);
      setRules(rs); setEvents(es); setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
    void loadUnread();
    void loadHealth();
  }, [loadUnread, loadHealth]);

  useEffect(() => { void load(); }, [load]);
  // 触发历史轮询：预警为分钟级事件，30s 足够（评估调度在后端 lifespan）
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void alertsApi.events(false, EVENT_WINDOW)
          // 轮询失败**有意静默**：30s 一次的后台刷新若弹错会持续打断用户，
          // 且下一轮自愈；首屏失败已由 ErrorState 明确暴露，不会掩盖真实故障。
          .then(setEvents).catch(() => { /* 轮询失败忽略，见上 */ });
        void loadUnread();
        // 健康状态与评估同频轮询：降级可能随时发生/恢复，需随之刷新横幅
        void loadHealth();
      }
    }, 30_000);
    return () => clearInterval(t);
  }, [loadUnread, loadHealth]);

  // C-4：徽标只用"全库未读"口径（独立扫描），不得由展示窗口推算
  const unreadCount = unread?.count ?? 0;
  const unreadSaturated = unread?.saturated ?? false;

  /** 打开/收起新建表单（与编辑互斥） */
  const toggleCreate = () => {
    setEditing(null);
    setCreating((v) => !v);
  };

  /** 打开编辑表单（预填当前值，与新建互斥） */
  const openEdit = (r: AlertRule) => {
    setCreating(false);
    setEditing(r);
  };

  const submitCreate = async (payload: Omit<AlertRulePayload, 'enabled'>) => {
    setSaving(true); setError(null);
    try {
      // 新建默认启用（表单不再持有 enabled 字段，见 RuleFormValues 注释）
      await alertsApi.createRule({ ...payload, enabled: true });
      setCreating(false);
      await load();
    } finally { setSaving(false); }
  };

  const submitEdit = async (payload: Omit<AlertRulePayload, 'enabled'>) => {
    if (!editing) return;
    // 关键：enabled 取"提交瞬间"的最新值，而不是打开表单时的快照。
    // rules 是唯一权威源（启停开关会即时乐观更新它），这样编辑期间切换开关
    // 再保存，不会被表单里的旧 enabled 回退（陈旧状态竞态）。
    // 若该规则已被并发删除（rules 中查不到），退回快照值兜底。
    const latestEnabled = rules?.find((x) => x.id === editing.id)?.enabled ?? editing.enabled;
    setSaving(true); setError(null);
    try {
      await alertsApi.updateRule(editing.id, { ...payload, enabled: latestEnabled });
      setEditing(null);
      await load();
    } finally { setSaving(false); }
  };

  /** 启停切换：乐观更新 + 失败回滚 + 可见错误提示（不静默吞错） */
  const toggleEnabled = async (r: AlertRule) => {
    if (togglingId != null) return; // 防连点
    const next = !r.enabled;
    setTogglingId(r.id);
    setError(null);
    setRules((prev) => prev?.map((x) => (x.id === r.id ? { ...x, enabled: next } : x)) ?? prev);
    try {
      await alertsApi.updateRule(r.id, {
        name: r.name, rule_type: r.rule_type, scope: r.scope,
        symbol: r.symbol, params: r.params, channels: r.channels,
        cooldown_minutes: r.cooldown_minutes, enabled: next,
      });
    } catch (e) {
      // 回滚到原状态
      setRules((prev) => prev?.map((x) => (x.id === r.id ? { ...x, enabled: r.enabled } : x)) ?? prev);
      setError(e instanceof ApiError ? e.message : '切换启用状态失败');
    } finally { setTogglingId(null); }
  };

  const removeRule = async (id: number) => {
    try { await alertsApi.deleteRule(id); await load(); }
    catch (e) { setError(e instanceof ApiError ? e.message : '删除失败'); }
  };

  const markAllRead = async () => {
    try {
      // C-4：走 {all:true} 覆盖窗口外未读；失败必须可见，不得静默"假清零"
      await alertsApi.markAllRead(); await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '标记全部已读失败');
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-bold text-ink">预警中心</h1>
        <div className="flex items-center gap-2">
          {/* 数据源健康：确认正常才显绿；尚未评估（checked_at 为空）显中性——不得把
              "还没跑过"当成"正常"（后端仅在评估轮次后写入 checked_at）。 */}
          {health && !health.degraded && (
            health.checked_at ? (
              <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-2xs font-medium text-emerald-600"
                title={`score_topk 数据源正常 · 最近检查 ${health.checked_at}${health.last_ok_at ? ` · 最近正常 ${health.last_ok_at}` : ''}${health.snapshot_date ? ` · 快照 ${health.snapshot_date}` : ''}`}>
                数据源正常
              </span>
            ) : (
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-2xs text-ink-muted"
                title="预警评估调度尚未运行过，暂无法确认 Top-K 数据源健康">
                数据源未评估
              </span>
            )
          )}
          {unreadCount > 0 && (
            <span className="rounded bg-red-50 px-1.5 py-0.5 text-2xs font-medium text-red-600">
              {unreadSaturated ? `≥${unreadCount} 条未读（已达扫描上限）` : `${unreadCount} 条未读`}
            </span>
          )}
          {unread === null && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-2xs text-ink-muted">未读不可读</span>
          )}
          <button onClick={toggleCreate}
            className="rounded bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600">
            {creating ? '收起表单' : '新建规则'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">{error}</div>
      )}

      {/* 数据源健康横幅：Top-K 迁移规则在股票池快照缺失时会静默跳过本轮判定，
          若不显式暴露，用户会误以为预警在正常值守（后端 /alerts/health 的唯一前端入口）。 */}
      {healthErr && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          <span>预警数据源健康状态不可读：{healthErr}（当前无法确认 Top-K 规则是否正常评估）</span>
          <button onClick={() => void loadHealth()}
            className="rounded-md border border-amber-200 bg-white px-2.5 py-0.5 text-2xs font-medium text-amber-700 hover:bg-amber-100">
            重试
          </button>
        </div>
      )}
      {health?.degraded && (
        <div role="alert" className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
          <div className="font-medium">
            预警数据源降级：Top-K 迁移（score_topk）规则本轮已被跳过，不会触发。
          </div>
          <div className="mt-0.5 text-2xs text-red-500">
            原因：{health.reason ?? '未知'}
            {health.snapshot_date ? ` · 股票池快照日期 ${health.snapshot_date}` : ' · 股票池快照缺失'}
            {health.checked_at ? ` · 检查于 ${health.checked_at}` : ''}
            {health.detail ? ` · ${health.detail}` : ''}
          </div>
        </div>
      )}

      {/* 新建规则表单（与编辑表单互斥，共用 RuleForm） */}
      {creating && (
        <Card title="新建预警规则">
          <RuleForm key="create" initial={BLANK_FORM} submitLabel="创建规则"
            saving={saving} onSubmit={submitCreate} onCancel={() => setCreating(false)} />
        </Card>
      )}

      {/* 编辑规则表单（预填当前值，保存走 PUT /alerts/rules/{id}） */}
      {editing && (
        <Card title={`编辑预警规则 · ${editing.name}`}>
          <RuleForm key={`edit-${editing.id}`} initial={ruleToFormValues(editing)}
            submitLabel="保存修改" saving={saving}
            onSubmit={submitEdit} onCancel={() => setEditing(null)} />
        </Card>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {/* 规则列表 */}
        <Card title={`预警规则（${rules?.length ?? 0}）`}>
          {loading && rules === null ? <LoadingState /> : rules === null ? (
            <ErrorState message={error ?? '规则列表加载失败'} onRetry={() => void load()} />
          ) : rules.length === 0 ? (
            <EmptyState title="暂无规则" hint="点击右上角「新建规则」创建第一条预警" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-hair text-2xs text-ink-muted">
                    <th className="py-1.5 pr-2 font-medium">状态</th>
                    <th className="py-1.5 pr-2 font-medium">名称</th>
                    <th className="py-1.5 pr-2 font-medium">类型</th>
                    <th className="py-1.5 pr-2 font-medium">作用域</th>
                    <th className="py-1.5 pr-2 font-medium">参数</th>
                    <th className="py-1.5 pr-2 font-medium">渠道</th>
                    <th className="py-1.5 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.id} className="border-b border-hair/60 last:border-0">
                      <td className="py-1.5 pr-2">
                        <button type="button" role="switch" aria-checked={r.enabled}
                          disabled={togglingId != null}
                          onClick={() => void toggleEnabled(r)}
                          title={r.enabled ? '点击停用该规则' : '点击启用该规则'}
                          className={`relative inline-flex h-4 w-8 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                            r.enabled ? 'bg-brand-500' : 'bg-slate-300'}`}>
                          <span className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
                            r.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                        </button>
                      </td>
                      <td className={`py-1.5 pr-2 font-medium ${r.enabled ? 'text-ink' : 'text-ink-muted'}`}>
                        {r.name}
                      </td>
                      <td className="py-1.5 pr-2 text-ink-secondary">{RULE_TYPE_META[r.rule_type]?.label ?? r.rule_type}</td>
                      <td className="py-1.5 pr-2 text-ink-secondary">
                        {SCOPE_LABEL[r.scope]}{r.symbol ? ` · ${r.symbol}` : ''}
                      </td>
                      <td className="num py-1.5 pr-2 text-2xs text-ink-muted">
                        {JSON.stringify(r.params)}
                      </td>
                      <td className="py-1.5 pr-2 text-2xs text-ink-muted">{r.channels.join('/')}</td>
                      <td className="py-1.5 text-right">
                        <div className="flex justify-end gap-1">
                          <button onClick={() => openEdit(r)}
                            className="rounded border border-hair px-1.5 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                            编辑
                          </button>
                          <button onClick={() => setConfirmDelete(r)}
                            className="rounded border border-red-200 px-1.5 py-0.5 text-2xs text-red-500 hover:bg-red-50">
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* 触发历史 */}
        <Card title="触发历史" extra={
          <button onClick={() => void markAllRead()} disabled={!unreadCount}
            className="rounded border border-hair px-1.5 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
            全部已读
          </button>
        }>
          {loading && events === null ? <LoadingState /> : events === null ? (
            /* 与规则列表同口径：加载失败必须可见，不得永久转圈 */
            <ErrorState message={error ?? '触发历史加载失败'} onRetry={() => void load()} />
          ) : events.length === 0 ? (
            <EmptyState title="暂无触发记录" hint="规则触发后将在此与 Webhook 通知" />
          ) : (
            <ul className="max-h-96 space-y-1.5 overflow-y-auto">
              {events.map((e) => (
                <li key={e.id}
                  className={`flex items-center justify-between gap-2 rounded border px-2 py-1.5 ${
                    e.is_read ? 'border-hair bg-white' : 'border-amber-200 bg-amber-50'}`}>
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-ink">{e.payload.rule ?? `规则 #${e.rule_id}`}</div>
                    <EventSummary e={e} />
                  </div>
                  <span className="num shrink-0 text-2xs text-ink-muted">
                    {e.payload.ts ?? e.triggered_at.slice(0, 19).replace('T', ' ')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* C-22：删除规则为不可撤销操作，与清缓存/熔断同口径二次确认 */}
      <Modal open={confirmDelete != null}
        title="确认删除该预警规则？"
        sub={confirmDelete ? `${confirmDelete.name} · ${RULE_TYPE_META[confirmDelete.rule_type]?.label ?? confirmDelete.rule_type}` : undefined}
        onClose={() => setConfirmDelete(null)}
        footer={(
          <div className="flex justify-end gap-2">
            <button onClick={() => setConfirmDelete(null)}
              className="rounded-md border border-hair bg-white px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-200">
              取消
            </button>
            <button
              onClick={() => {
                const target = confirmDelete;
                setConfirmDelete(null);
                if (target) void removeRule(target.id);
              }}
              className="rounded-md bg-red-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600">
              确认删除
            </button>
          </div>
        )}>
        <p className="text-xs leading-relaxed text-ink-secondary">
          删除后该规则不再评估，历史触发记录保留；此操作不可撤销。
        </p>
      </Modal>
    </div>
  );
}
