/**
 * 预警中心（/alerts，§4.1 Sprint2 MVP）：
 *   左：规则管理（六类规则 CRUD + 参数白名单表单）
 *   右：触发历史（未读徽标 + 批量已读；30s 轮询，评估调度在后端 lifespan）
 *
 * 纪律：触发时间以 payload.ts（本地时间）展示；triggered_at 为 UTC 存储，
 * 仅作排序依据不直接展示，避免时区误读。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError } from '@/api/client';
import {
  alertsApi, type AlertEventItem, type AlertRule,
  type AlertRuleParams, type AlertRuleType, type AlertScope,
} from '@/api/alerts';
import { EmptyState, LoadingState } from '@/components/ui';
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
    bits.push(p.metric === 'disk' ? `磁盘 ${Number(p.usage_percent).toFixed(1)}%`
      : p.metric === 'pipeline' ? `流水线失败 ×${p.failed_jobs}`
      : '数据源降级');
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
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);

  // 表单状态
  const [fName, setFName] = useState('');
  const [fType, setFType] = useState<AlertRuleType>('price_pct');
  const [fScope, setFScope] = useState<AlertScope>('symbol');
  const [fSymbol, setFSymbol] = useState('');
  const [fParams, setFParams] = useState<AlertRuleParams>(DEFAULT_PARAMS.price_pct);
  const [fWebhook, setFWebhook] = useState(false);
  const [fCooldown, setFCooldown] = useState(30);

  const load = useCallback(async () => {
    try {
      const [rs, es] = await Promise.all([
        alertsApi.rules(), alertsApi.events(false, 50),
      ]);
      setRules(rs); setEvents(es); setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  // 触发历史轮询：预警为分钟级事件，30s 足够（评估调度在后端 lifespan）
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void alertsApi.events(false, 50)
        .then(setEvents).catch(() => { /* 轮询失败忽略 */ });
    }, 30_000);
    return () => clearInterval(t);
  }, []);

  const unreadCount = useMemo(() => (events ?? []).filter((e) => !e.is_read).length, [events]);

  const switchType = (t: AlertRuleType) => {
    setFType(t);
    setFParams(DEFAULT_PARAMS[t]);
  };

  const submit = async () => {
    if (!fName.trim()) { setError('规则名称必填'); return; }
    if (fScope === 'symbol' && !fSymbol.trim()) { setError('单标的规则需填写标的代码'); return; }
    setSaving(true); setError(null);
    try {
      await alertsApi.createRule({
        name: fName.trim(), rule_type: fType, scope: fScope,
        symbol: fScope === 'symbol' ? fSymbol.trim().toUpperCase() : null,
        params: fParams, channels: fWebhook ? ['sse', 'webhook'] : ['sse'],
        cooldown_minutes: fCooldown, enabled: true,
      });
      setCreating(false); setFName(''); setFSymbol('');
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '创建失败');
    } finally { setSaving(false); }
  };

  const removeRule = async (id: number) => {
    try { await alertsApi.deleteRule(id); await load(); }
    catch (e) { setError(e instanceof ApiError ? e.message : '删除失败'); }
  };

  const markAllRead = async () => {
    const ids = (events ?? []).filter((e) => !e.is_read).map((e) => e.id);
    if (!ids.length) return;
    try { await alertsApi.markRead(ids); await load(); }
    catch { /* 已读失败忽略 */ }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-bold text-ink">预警中心</h1>
        <div className="flex items-center gap-2">
          {unreadCount > 0 && (
            <span className="rounded bg-red-50 px-1.5 py-0.5 text-2xs font-medium text-red-600">
              {unreadCount} 条未读
            </span>
          )}
          <button onClick={() => setCreating((v) => !v)}
            className="rounded bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600">
            {creating ? '收起表单' : '新建规则'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">{error}</div>
      )}

      {/* 新建规则表单 */}
      {creating && (
        <Card title="新建预警规则">
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-4">
            <label className="text-2xs text-ink-secondary">名称
              <input value={fName} onChange={(e) => setFName(e.target.value)}
                placeholder="如：茅台大涨提醒" className={fieldCls} />
            </label>
            <label className="text-2xs text-ink-secondary">规则类型
              <select value={fType} onChange={(e) => switchType(e.target.value as AlertRuleType)}
                className={fieldCls}>
                {Object.entries(RULE_TYPE_META).map(([k, v]) => (
                  <option key={k} value={k}>{v.label} · {v.desc}</option>
                ))}
              </select>
            </label>
            <label className="text-2xs text-ink-secondary">作用域
              <select value={fScope} onChange={(e) => setFScope(e.target.value as AlertScope)}
                className={fieldCls}>
                <option value="symbol">单标的</option>
                <option value="watchlist">我的自选</option>
                <option value="global">全局</option>
              </select>
            </label>
            {fScope === 'symbol' ? (
              <label className="text-2xs text-ink-secondary">标的代码
                <input value={fSymbol} onChange={(e) => setFSymbol(e.target.value)}
                  placeholder="600519.SH" className={fieldCls} />
              </label>
            ) : (
              <label className="text-2xs text-ink-secondary">冷却（分钟）
                <input type="number" min="0" max="1440" value={fCooldown}
                  onChange={(e) => setFCooldown(Number(e.target.value))} className={fieldCls} />
              </label>
            )}
          </div>
          <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-3">
            <ParamsForm ruleType={fType} params={fParams} onChange={setFParams} />
            <label className="flex items-center gap-1.5 self-end text-2xs text-ink-secondary">
              <input type="checkbox" checked={fWebhook}
                onChange={(e) => setFWebhook(e.target.checked)} />
              同时推送 Webhook（需后端配置 NOTIFY_WEBHOOK_URL）
            </label>
            {fScope === 'symbol' ? (
              <label className="self-end text-2xs text-ink-secondary">冷却（分钟）
                <input type="number" min="0" max="1440" value={fCooldown}
                  onChange={(e) => setFCooldown(Number(e.target.value))} className={fieldCls} />
              </label>
            ) : <span />}
          </div>
          <div className="mt-3 text-right">
            <button onClick={() => void submit()} disabled={saving}
              className="rounded bg-brand-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50">
              {saving ? '保存中…' : '创建规则'}
            </button>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {/* 规则列表 */}
        <Card title={`预警规则（${rules?.length ?? 0}）`}>
          {rules === null ? <LoadingState /> : rules.length === 0 ? (
            <EmptyState title="暂无规则" hint="点击右上角「新建规则」创建第一条预警" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-hair text-2xs text-ink-muted">
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
                      <td className="py-1.5 pr-2 font-medium text-ink">{r.name}</td>
                      <td className="py-1.5 pr-2 text-ink-secondary">{RULE_TYPE_META[r.rule_type]?.label ?? r.rule_type}</td>
                      <td className="py-1.5 pr-2 text-ink-secondary">
                        {SCOPE_LABEL[r.scope]}{r.symbol ? ` · ${r.symbol}` : ''}
                      </td>
                      <td className="num py-1.5 pr-2 text-2xs text-ink-muted">
                        {JSON.stringify(r.params)}
                      </td>
                      <td className="py-1.5 pr-2 text-2xs text-ink-muted">{r.channels.join('/')}</td>
                      <td className="py-1.5 text-right">
                        <button onClick={() => void removeRule(r.id)}
                          className="rounded border border-red-200 px-1.5 py-0.5 text-2xs text-red-500 hover:bg-red-50">
                          删除
                        </button>
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
          {events === null ? <LoadingState /> : events.length === 0 ? (
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
    </div>
  );
}
