import React, { useState, useEffect } from 'react';
import type {
  AlertRule,
  AlertRulePayload,
  AlertRuleParams,
  AlertRuleType,
  AlertScope,
  AlertSeverity,
} from '@/api/alerts';
import { Modal } from '@/components/ui';

export const RULE_TYPE_META: Record<
  AlertRuleType,
  { label: string; desc: string; category: string }
> = {
  price_pct: { label: '涨跌幅', desc: '单日涨跌幅超 ±N%', category: '行情异动' },
  price_cross: { label: '价格穿越', desc: '上穿/下穿特定价格阈值', category: '行情异动' },
  volume_spike: { label: '放量脉冲', desc: '现量 > N 日均量 × k', category: '行情异动' },
  score_topk: { label: 'Top-K 迁移', desc: '进入/跌出模型 Top-K 选股池', category: '策略多头' },
  factor_quantile: { label: '因子分位', desc: '因子值突破 N 日历史分位数', category: '策略多头' },
  portfolio_risk: { label: '集中度/权重', desc: '单一标的或行业持仓暴露超限', category: '组合风控' },
  portfolio_drawdown: { label: '高水位回撤', desc: '组合从历史峰值动态回撤超限', category: '组合风控' },
  portfolio_var: { label: 'Parametric VaR', desc: '95%置信度单日在险价值超限', category: '组合风控' },
  liquidity_stress: { label: '流动性挤压', desc: 'Amihud 冲击 / 跌停板封单压迫', category: '微观结构' },
  data_health: { label: '系统与数据健康', desc: '磁盘水位 / 流水线失败 / 源降级', category: '运维治理' },
};

export const SCOPE_LABEL: Record<AlertScope, string> = {
  symbol: '单标的',
  watchlist: '我的自选',
  global: '全局投研组合',
};

export const DEFAULT_PARAMS: Record<AlertRuleType, AlertRuleParams> = {
  price_pct: { threshold: 3, severity: 'warning', hysteresis: 0.2 },
  price_cross: { price: 100, direction: 'up', severity: 'warning' },
  volume_spike: { window: 20, k: 3, severity: 'info', hysteresis: 0.1 },
  score_topk: { k: 50, severity: 'info' },
  factor_quantile: { factor: 'mom_20', window: 250, quantile: 0.95, severity: 'warning' },
  portfolio_risk: { factor: 'industry', threshold: 20, direction: 'above', severity: 'warning', hysteresis: 0.5 },
  portfolio_drawdown: { max_drawdown: 8, benchmark: '000300.SH', severity: 'critical', hysteresis: 0.3 },
  portfolio_var: { confidence: 0.95, limit_pct: 3.0, time_horizon: 1, severity: 'warning', hysteresis: 0.1 },
  liquidity_stress: { metric: 'amihud', threshold: 1.5, severity: 'warning', hysteresis: 0.1 },
  data_health: { metric: 'disk', threshold: 90, severity: 'critical' },
};

export interface QuantTemplate {
  id: string;
  title: string;
  category: string;
  desc: string;
  rule_type: AlertRuleType;
  scope: AlertScope;
  symbol?: string;
  params: AlertRuleParams;
}

export const QUANT_TEMPLATES: QuantTemplate[] = [
  {
    id: 'tmpl_fund_10_limit',
    title: '公募双十合规·单股持仓超限守卫',
    category: '合规风控',
    desc: '单一股票持仓市值不得超过基金资产净值的 10%，触发致命处置',
    rule_type: 'portfolio_risk',
    scope: 'global',
    params: { factor: 'single_stock_weight', threshold: 10, direction: 'above', severity: 'critical', hysteresis: 0.2 },
  },
  {
    id: 'tmpl_industry_limit',
    title: '行业敞口超限·单行业超 20% 守卫',
    category: '合规风控',
    desc: '单一行业风险暴露不得超过 20%，防范板块系统性回撤风险',
    rule_type: 'portfolio_risk',
    scope: 'global',
    params: { factor: 'industry', threshold: 20, direction: 'above', severity: 'warning', hysteresis: 0.5 },
  },
  {
    id: 'tmpl_dynamic_drawdown',
    title: '高水位动态回撤硬止损 (8%)',
    category: '组合风控',
    desc: '自组合历史高点动态回撤触及 8% 时立即触发致命预警并建议平仓',
    rule_type: 'portfolio_drawdown',
    scope: 'global',
    params: { max_drawdown: 8.0, benchmark: '000300.SH', severity: 'critical', hysteresis: 0.3 },
  },
  {
    id: 'tmpl_var_guard',
    title: '95% Parametric VaR 在险价值守卫',
    category: '组合风控',
    desc: '单日 95% 置信度 VaR 预测损失超过 3% 时发起风控提示',
    rule_type: 'portfolio_var',
    scope: 'global',
    params: { confidence: 0.95, limit_pct: 3.0, time_horizon: 1, severity: 'warning', hysteresis: 0.1 },
  },
  {
    id: 'tmpl_amihud_impact',
    title: '微观流动性 Amihud 冲击守卫 (1.5x)',
    category: '微观结构',
    desc: '非流动性冲击突破 1.5 倍基准，提示冲击成本急剧扩大',
    rule_type: 'liquidity_stress',
    scope: 'watchlist',
    params: { metric: 'amihud', threshold: 1.5, severity: 'warning', hysteresis: 0.1 },
  },
  {
    id: 'tmpl_seal_ratio_choke',
    title: '跌停板卖盘封单压迫守卫 (3.0x)',
    category: '微观结构',
    desc: '跌停板封单与日均成交量之比超 3 倍，存在极端踩踏风险',
    rule_type: 'liquidity_stress',
    scope: 'watchlist',
    params: { metric: 'seal_ratio', threshold: 3.0, severity: 'critical', hysteresis: 0.2 },
  },
  {
    id: 'tmpl_crash_guard',
    title: '核心持仓暴跌防范 (单日超 5%)',
    category: '行情异动',
    desc: '关注标的单日跌幅超过 5% 触发中级告警',
    rule_type: 'price_pct',
    scope: 'watchlist',
    params: { threshold: 5.0, severity: 'warning', hysteresis: 0.2 },
  },
  {
    id: 'tmpl_disk_line',
    title: '数据中心磁盘水位红线 (90%)',
    category: '运维治理',
    desc: '时序库及行情缓存分区可用空间不足 10% 时触发告警',
    rule_type: 'data_health',
    scope: 'global',
    params: { metric: 'disk', threshold: 90, severity: 'critical' },
  },
];

interface RuleDrawerModalProps {
  open: boolean;
  editingRule: AlertRule | null;
  saving: boolean;
  onClose: () => void;
  onSubmit: (payload: Omit<AlertRulePayload, 'enabled'>) => Promise<void>;
}

export const RuleDrawerModal: React.FC<RuleDrawerModalProps> = ({
  open,
  editingRule,
  saving,
  onClose,
  onSubmit,
}) => {
  const [name, setName] = useState('');
  const [ruleType, setRuleType] = useState<AlertRuleType>('price_pct');
  const [scope, setScope] = useState<AlertScope>('symbol');
  const [symbol, setSymbol] = useState('');
  const [params, setParams] = useState<AlertRuleParams>(DEFAULT_PARAMS.price_pct);
  const [webhook, setWebhook] = useState(false);
  const [cooldown, setCooldown] = useState(30);
  const [localError, setLocalError] = useState<string | null>(null);

  // 初始化或编辑回填
  useEffect(() => {
    if (editingRule) {
      setName(editingRule.name);
      setRuleType(editingRule.rule_type);
      setScope(editingRule.scope);
      setSymbol(editingRule.symbol ?? '');
      setParams(editingRule.params ?? DEFAULT_PARAMS[editingRule.rule_type]);
      setWebhook(editingRule.channels.includes('webhook'));
      setCooldown(editingRule.cooldown_minutes);
    } else {
      setName('');
      setRuleType('price_pct');
      setScope('symbol');
      setSymbol('');
      setParams(DEFAULT_PARAMS.price_pct);
      setWebhook(false);
      setCooldown(30);
    }
    setLocalError(null);
  }, [editingRule, open]);

  const switchType = (t: AlertRuleType) => {
    setRuleType(t);
    setParams(DEFAULT_PARAMS[t]);
  };

  const applyTemplate = (t: QuantTemplate) => {
    setName(t.title);
    setRuleType(t.rule_type);
    setScope(t.scope);
    setSymbol(t.symbol ?? '');
    setParams({ ...t.params });
  };

  const num = (v: string) => (v === '' ? undefined : Number(v));

  const handleSave = async () => {
    if (!name.trim()) {
      setLocalError('规则名称必填');
      return;
    }
    if (scope === 'symbol' && !symbol.trim()) {
      setLocalError('单标的规则需填写标的代码（如 600519.SH）');
      return;
    }
    setLocalError(null);
    try {
      await onSubmit({
        name: name.trim(),
        rule_type: ruleType,
        scope,
        symbol: scope === 'symbol' ? symbol.trim().toUpperCase() : null,
        params,
        channels: webhook ? ['sse', 'webhook'] : ['sse'],
        cooldown_minutes: cooldown,
      });
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : '保存失败');
    }
  };

  const fieldCls =
    'w-full rounded border border-hair bg-surface px-2.5 py-1.5 text-xs outline-none focus:border-brand-300 text-ink';

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editingRule ? `编辑预警规则 · ${editingRule.name}` : '新建量化预警规则'}
      sub={editingRule ? `规则 ID: #${editingRule.id}` : '支持 10 类量化风控算法 · 预设模板一键导入'}
      footer={
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded border border-hair bg-surface px-3 py-1.5 text-xs text-ink-secondary hover:border-brand-200 disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="rounded bg-brand-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {saving ? '保存中…' : editingRule ? '保存修改' : '确认创建'}
          </button>
        </div>
      }
    >
      <div className="space-y-4 text-xs">
        {/* 预设量化策略模板库（仅新建时显示） */}
        {!editingRule && (
          <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-3">
            <div className="flex items-center justify-between">
              <span className="text-2xs font-semibold text-brand-900">
                🚀 快捷套用量化风控模板库
              </span>
              <span className="text-2xs text-brand-700">点击自动填单</span>
            </div>
            <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
              {QUANT_TEMPLATES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => applyTemplate(t)}
                  className="flex flex-col items-start rounded border border-hair bg-surface p-2 text-left hover:border-brand-400 hover:bg-surface-sunken"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium text-ink">{t.title}</span>
                    <span className="rounded bg-brand-50 px-1 text-[10px] text-brand-600">
                      {t.category}
                    </span>
                  </div>
                  <div className="mt-0.5 line-clamp-1 text-2xs text-ink-muted">{t.desc}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 核心配置栏 */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="text-2xs text-ink-secondary">
            规则名称 <span className="text-danger">*</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：茅台高水位回撤监控"
              className={fieldCls}
            />
          </label>

          <label className="text-2xs text-ink-secondary">
            规则类型
            <select
              value={ruleType}
              onChange={(e) => switchType(e.target.value as AlertRuleType)}
              className={fieldCls}
            >
              {Object.entries(RULE_TYPE_META).map(([k, v]) => (
                <option key={k} value={k}>
                  [{v.category}] {v.label} · {v.desc}
                </option>
              ))}
            </select>
          </label>

          <label className="text-2xs text-ink-secondary">
            作用域范围
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as AlertScope)}
              className={fieldCls}
            >
              <option value="symbol">单标的 (精确代码)</option>
              <option value="watchlist">我的自选标的池</option>
              <option value="global">全局投资组合</option>
            </select>
          </label>

          {scope === 'symbol' && (
            <label className="text-2xs text-ink-secondary">
              标的代码 <span className="text-danger">*</span>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="如 600519.SH / 000001.SZ"
                className={fieldCls}
              />
            </label>
          )}

          <label className="text-2xs text-ink-secondary">
            严重度等级 (Severity)
            <select
              value={params.severity ?? 'warning'}
              onChange={(e) =>
                setParams({ ...params, severity: e.target.value as AlertSeverity })
              }
              className={fieldCls}
            >
              <option value="critical">Critical (致命/紧急平仓)</option>
              <option value="warning">Warning (告警/重点关注)</option>
              <option value="info">Info (提示信息)</option>
            </select>
          </label>

          <label className="text-2xs text-ink-secondary">
            防抖迟滞容差 (Hysteresis)
            <input
              type="number"
              step="0.05"
              min="0"
              max="10"
              value={params.hysteresis ?? ''}
              placeholder="0.2"
              onChange={(e) => setParams({ ...params, hysteresis: num(e.target.value) })}
              className={fieldCls}
            />
          </label>
        </div>

        {/* 动态参数表单 */}
        <div className="rounded-lg border border-hair bg-surface-sunken p-3">
          <div className="mb-2 font-semibold text-ink text-2xs">
            规则运行参数 ({RULE_TYPE_META[ruleType]?.label})
          </div>

          {ruleType === 'price_pct' && (
            <label className="text-2xs text-ink-secondary">
              涨跌幅阈值（±%）
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="30"
                value={params.threshold ?? ''}
                onChange={(e) => setParams({ ...params, threshold: num(e.target.value) })}
                className={fieldCls}
              />
            </label>
          )}

          {ruleType === 'price_cross' && (
            <div className="grid grid-cols-2 gap-2">
              <label className="text-2xs text-ink-secondary">
                价格阈值 (元)
                <input
                  type="number"
                  step="0.01"
                  value={params.price ?? ''}
                  onChange={(e) => setParams({ ...params, price: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                穿越方向
                <select
                  value={params.direction ?? 'up'}
                  onChange={(e) =>
                    setParams({ ...params, direction: e.target.value as 'up' | 'down' })
                  }
                  className={fieldCls}
                >
                  <option value="up">上穿目标价</option>
                  <option value="down">下穿目标价</option>
                </select>
              </label>
            </div>
          )}

          {ruleType === 'volume_spike' && (
            <div className="grid grid-cols-2 gap-2">
              <label className="text-2xs text-ink-secondary">
                基准均量窗口 (交易日)
                <input
                  type="number"
                  min="2"
                  max="120"
                  value={params.window ?? 20}
                  onChange={(e) => setParams({ ...params, window: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                放量倍数 k
                <input
                  type="number"
                  step="0.1"
                  min="1.2"
                  max="20"
                  value={params.k ?? 3}
                  onChange={(e) => setParams({ ...params, k: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
            </div>
          )}

          {ruleType === 'score_topk' && (
            <label className="text-2xs text-ink-secondary">
              模型 Top-K 选股池容量
              <input
                type="number"
                min="1"
                max="500"
                value={params.k ?? 50}
                onChange={(e) => setParams({ ...params, k: num(e.target.value) })}
                className={fieldCls}
              />
            </label>
          )}

          {ruleType === 'factor_quantile' && (
            <div className="grid grid-cols-3 gap-2">
              <label className="text-2xs text-ink-secondary">
                因子列名
                <input
                  value={params.factor ?? 'mom_20'}
                  placeholder="mom_20"
                  onChange={(e) => setParams({ ...params, factor: e.target.value })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                历史窗口 (日)
                <input
                  type="number"
                  min="30"
                  max="750"
                  value={params.window ?? 250}
                  onChange={(e) => setParams({ ...params, window: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                分位阈值 (0.5~0.99)
                <input
                  type="number"
                  step="0.01"
                  min="0.5"
                  max="0.99"
                  value={params.quantile ?? 0.95}
                  onChange={(e) => setParams({ ...params, quantile: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
            </div>
          )}

          {ruleType === 'portfolio_risk' && (
            <div className="grid grid-cols-3 gap-2">
              <label className="text-2xs text-ink-secondary">
                风控因子 / 维度
                <input
                  value={params.factor ?? 'industry'}
                  placeholder="industry 或 single_stock_weight"
                  onChange={(e) => setParams({ ...params, factor: e.target.value })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                暴露阈值 (%)
                <input
                  type="number"
                  step="0.5"
                  min="1"
                  max="100"
                  value={params.threshold ?? 20}
                  onChange={(e) => setParams({ ...params, threshold: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                偏离方向
                <select
                  value={params.direction ?? 'above'}
                  onChange={(e) =>
                    setParams({ ...params, direction: e.target.value as 'above' | 'below' })
                  }
                  className={fieldCls}
                >
                  <option value="above">超过上限 (超限告警)</option>
                  <option value="below">跌破下限 (不足告警)</option>
                </select>
              </label>
            </div>
          )}

          {ruleType === 'portfolio_drawdown' && (
            <div className="grid grid-cols-2 gap-2">
              <label className="text-2xs text-ink-secondary">
                高水位最大回撤止损阈值 (%)
                <input
                  type="number"
                  step="0.5"
                  min="1"
                  max="50"
                  value={params.max_drawdown ?? 8}
                  onChange={(e) => setParams({ ...params, max_drawdown: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                对标基准代码
                <input
                  value={params.benchmark ?? '000300.SH'}
                  placeholder="000300.SH"
                  onChange={(e) => setParams({ ...params, benchmark: e.target.value })}
                  className={fieldCls}
                />
              </label>
            </div>
          )}

          {ruleType === 'portfolio_var' && (
            <div className="grid grid-cols-3 gap-2">
              <label className="text-2xs text-ink-secondary">
                VaR 置信度 (如 0.95)
                <input
                  type="number"
                  step="0.01"
                  min="0.9"
                  max="0.99"
                  value={params.confidence ?? 0.95}
                  onChange={(e) => setParams({ ...params, confidence: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                限额百分比 (%)
                <input
                  type="number"
                  step="0.1"
                  min="0.5"
                  max="20"
                  value={params.limit_pct ?? 3.0}
                  onChange={(e) => setParams({ ...params, limit_pct: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
              <label className="text-2xs text-ink-secondary">
                持有期 (日)
                <input
                  type="number"
                  min="1"
                  max="30"
                  value={params.time_horizon ?? 1}
                  onChange={(e) => setParams({ ...params, time_horizon: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
            </div>
          )}

          {ruleType === 'liquidity_stress' && (
            <div className="grid grid-cols-2 gap-2">
              <label className="text-2xs text-ink-secondary">
                微观流动性指标
                <select
                  value={params.metric ?? 'amihud'}
                  onChange={(e) =>
                    setParams({
                      ...params,
                      metric: e.target.value as 'amihud' | 'seal_ratio' | 'turnover_burst',
                    })
                  }
                  className={fieldCls}
                >
                  <option value="amihud">Amihud 非流动性冲击比率</option>
                  <option value="seal_ratio">跌停板封单压迫比率</option>
                  <option value="turnover_burst">异常换手/量能爆发</option>
                </select>
              </label>
              <label className="text-2xs text-ink-secondary">
                告警阈值
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  value={params.threshold ?? 1.5}
                  onChange={(e) => setParams({ ...params, threshold: num(e.target.value) })}
                  className={fieldCls}
                />
              </label>
            </div>
          )}

          {ruleType === 'data_health' && (
            <div className="grid grid-cols-2 gap-2">
              <label className="text-2xs text-ink-secondary">
                健康指标类型
                <select
                  value={params.metric ?? 'disk'}
                  onChange={(e) =>
                    setParams({
                      ...params,
                      metric: e.target.value as 'disk' | 'pipeline' | 'source',
                    })
                  }
                  className={fieldCls}
                >
                  <option value="disk">磁盘分区空间水位</option>
                  <option value="pipeline">流水线作业失败监控</option>
                  <option value="source">行情与财报数据源降级</option>
                </select>
              </label>
              {params.metric === 'disk' && (
                <label className="text-2xs text-ink-secondary">
                  空间占用阈值 (%)
                  <input
                    type="number"
                    min="50"
                    max="99"
                    value={params.threshold ?? 90}
                    onChange={(e) => setParams({ ...params, threshold: num(e.target.value) })}
                    className={fieldCls}
                  />
                </label>
              )}
            </div>
          )}
        </div>

        {/* 触发渠道与冷却 */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="text-2xs text-ink-secondary">
            告警冷却周期 (分钟)
            <input
              type="number"
              min="0"
              max="1440"
              value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              className={fieldCls}
            />
          </label>
          <div className="flex items-center gap-2 pt-4">
            <label className="flex items-center gap-1.5 text-2xs text-ink-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={webhook}
                onChange={(e) => setWebhook(e.target.checked)}
              />
              同时推送 Webhook（钉钉 / 飞书 / 企业微信）
            </label>
          </div>
        </div>

        {localError && (
          <div className="rounded border border-danger/30 bg-danger-bg px-3 py-2 text-2xs text-danger">
            {localError}
          </div>
        )}
      </div>
    </Modal>
  );
};
