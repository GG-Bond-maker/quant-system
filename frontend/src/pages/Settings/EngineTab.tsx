import { useState } from 'react';
import type { EngineConfig } from '@/api/settings';
import { SectionCard } from '@/components/ui';

interface Props {
  engine: EngineConfig;
  isAdmin: boolean;
  onSave: (config: EngineConfig) => Promise<void>;
  saving: boolean;
}

const RISK_INDICATOR_OPTIONS = [
  { key: 'annual', label: '年化收益率 (CAGR)' },
  { key: 'max_dd', label: '最大回撤 (Max Drawdown)' },
  { key: 'sharpe', label: '夏普比率 (Sharpe Ratio)' },
  { key: 'sortino', label: '索提诺比率 (Sortino)' },
  { key: 'calmar', label: '卡玛比率 (Calmar Ratio)' },
  { key: 'win_rate', label: '交易胜率 (Win Rate)' },
  { key: 'profit_loss_ratio', label: '盈亏比 (P/L Ratio)' },
];

const BENCHMARK_OPTIONS = [
  { key: '000300.SH', label: '沪深 300 指数 (000300.SH · 大盘蓝筹基准)' },
  { key: '000905.SH', label: '中证 500 指数 (000905.SH · 中盘成长基准)' },
  { key: '000852.SH', label: '中证 1000 指数 (000852.SH · 小盘量化基准)' },
  { key: '399006.SZ', label: '创业板指数 (399006.SZ · 高弹性成长基准)' },
];

export function EngineTab({ engine, isAdmin, onSave, saving }: Props) {
  const [form, setForm] = useState<EngineConfig>({
    name: engine.name || 'vectorbt',
    risk_indicators: engine.risk_indicators || ['annual', 'sharpe', 'max_dd', 'sortino', 'calmar'],
    commission_pct: engine.commission_pct ?? 0.025,
    commission_sell_pct: engine.commission_sell_pct ?? 0.025,
    stamp_duty_pct: engine.stamp_duty_pct ?? 0.05,
    transfer_fee_pct: engine.transfer_fee_pct ?? 0.001,
    min_commission: engine.min_commission ?? 5.0,
    slippage_model: engine.slippage_model || 'fixed',
    slippage_pct: engine.slippage_pct ?? 0.1,
    default_benchmark: engine.default_benchmark || '000300.SH',
    risk_free_rate: engine.risk_free_rate ?? 2.0,
    t_plus_one: engine.t_plus_one ?? true,
    limit_up_down_guard: engine.limit_up_down_guard ?? true,
  });

  const [isDirty, setIsDirty] = useState(false);

  const update = <K extends keyof EngineConfig>(k: K, v: EngineConfig[K]) => {
    setForm((prev) => ({ ...prev, [k]: v }));
    setIsDirty(true);
  };

  const handleResetDefaults = () => {
    setForm({
      name: 'vectorbt',
      risk_indicators: ['annual', 'sharpe', 'max_dd', 'sortino', 'calmar', 'win_rate'],
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
    });
    setIsDirty(true);
  };

  const handleSave = async () => {
    await onSave(form);
    setIsDirty(false);
  };

  const inputCls =
    'w-full rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300 disabled:opacity-60 disabled:cursor-not-allowed';

  return (
    <div className="space-y-4">
      {!isAdmin && (
        <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn flex items-center gap-2">
          <span>⚠️ 当前账号为研究员/查看者权限，量化引擎与摩擦力全局参数仅管理员可修改保存。</span>
        </div>
      )}

      {/* 1. 核心引擎与撮合规则 */}
      <SectionCard
        title="量化计算引擎与交易撮合规则"
        action={
          <div className="flex items-center gap-2">
            {isDirty && <span className="text-2xs font-medium text-warn">● 存在未保存修改</span>}
            <button
              type="button"
              onClick={handleResetDefaults}
              disabled={!isAdmin || saving}
              className="rounded-md border border-hair bg-surface px-2.5 py-1 text-xs text-ink-secondary hover:bg-surface-alt disabled:opacity-50"
            >
              恢复推荐 A 股实盘参数
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={!isAdmin || !isDirty || saving}
              className="rounded-md bg-brand-500 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {saving ? '保存中…' : '保存引擎配置'}
            </button>
          </div>
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">默认回测计算内核</label>
            <select
              value={form.name}
              disabled={!isAdmin}
              onChange={(e) => update('name', e.target.value)}
              className={inputCls}
            >
              <option value="vectorbt">VECTORBT（向量化批处理 · 极速寻优）</option>
              <option value="backtrader">BACKTRADER（纯事件驱动 · 逐笔精准）</option>
              <option value="aqp">AQP 自研引擎（多因子 Top-K 截面打分）</option>
            </select>
            <p className="mt-1 text-3xs text-ink-muted">定义策略回测与多因子推演的默认执行引擎底层</p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">全局默认基准指数</label>
            <select
              value={form.default_benchmark}
              disabled={!isAdmin}
              onChange={(e) => update('default_benchmark', e.target.value)}
              className={inputCls}
            >
              {BENCHMARK_OPTIONS.map((b) => (
                <option key={b.key} value={b.key}>
                  {b.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-3xs text-ink-muted">策略未显式指定基准时计算超额收益与 Beta 的默认对标物</p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">年化无风险利率 (Rf %)</label>
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                step={0.1}
                min={0}
                max={10}
                value={form.risk_free_rate}
                disabled={!isAdmin}
                onChange={(e) => update('risk_free_rate', Number(e.target.value))}
                className={inputCls}
              />
              <span className="text-xs text-ink-muted">%</span>
            </div>
            <p className="mt-1 text-3xs text-ink-muted">用于计算夏普比率 (Sharpe) 与索提诺比率 (Sortino) 的超额无风险收益基底</p>
          </div>
        </div>

        {/* 撮合合规保护开关 */}
        <div className="mt-4 grid grid-cols-1 gap-3 border-t border-hair pt-3 sm:grid-cols-2">
          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-hair bg-surface-alt p-3">
            <input
              type="checkbox"
              checked={form.t_plus_one}
              disabled={!isAdmin}
              onChange={(e) => update('t_plus_one', e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-brand-500"
            />
            <div>
              <div className="text-xs font-semibold text-ink">强制执行 A 股 T+1 交易交收约束</div>
              <div className="text-3xs text-ink-muted">回测中当日买入的现货标的必须在次日才能平仓卖出，杜绝日内回转套利幻觉</div>
            </div>
          </label>

          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-hair bg-surface-alt p-3">
            <input
              type="checkbox"
              checked={form.limit_up_down_guard}
              disabled={!isAdmin}
              onChange={(e) => update('limit_up_down_guard', e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-brand-500"
            />
            <div>
              <div className="text-xs font-semibold text-ink">涨跌停板撮合拦截（价格笼子保护）</div>
              <div className="text-3xs text-ink-muted">一字涨停板禁止买入、一字跌停板禁止卖出，真实还原极端行情下的不可成交性</div>
            </div>
          </label>
        </div>
      </SectionCard>

      {/* 2. 真实 A 股精细摩擦力模型 */}
      <SectionCard
        title="A 股真实交易成本与摩擦力模型"
        action={
          <span className="text-2xs text-ink-muted">
            对标机构实盘摩擦力口径 · 还原净收益曲线
          </span>
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {/* 买入佣金 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3">
              <div className="text-xs font-semibold text-ink">买入佣金率 (Buy Fee)</div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <input
                  type="number"
                  step={0.005}
                  min={0.001}
                  max={0.5}
                  value={form.commission_pct}
                  disabled={!isAdmin}
                  onChange={(e) => update('commission_pct', Number(e.target.value))}
                  className="w-full rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                />
                <span className="text-xs text-ink-muted">%</span>
              </div>
              <div className="mt-1 text-3xs text-ink-muted">常规机构费率约万 1.5 ~ 万 2.5</div>
            </div>

            {/* 卖出佣金 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3">
              <div className="text-xs font-semibold text-ink">卖出佣金率 (Sell Fee)</div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <input
                  type="number"
                  step={0.005}
                  min={0.001}
                  max={0.5}
                  value={form.commission_sell_pct}
                  disabled={!isAdmin}
                  onChange={(e) => update('commission_sell_pct', Number(e.target.value))}
                  className="w-full rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                />
                <span className="text-xs text-ink-muted">%</span>
              </div>
              <div className="mt-1 text-3xs text-ink-muted">券商经纪手续费双向收取</div>
            </div>

            {/* 印花税 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink">印花税率 (Stamp Duty)</span>
                <span className="rounded bg-brand-500/10 px-1 text-3xs font-medium text-brand-600 dark:text-brand-400">单边卖出</span>
              </div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <input
                  type="number"
                  step={0.01}
                  min={0}
                  max={0.2}
                  value={form.stamp_duty_pct}
                  disabled={!isAdmin}
                  onChange={(e) => update('stamp_duty_pct', Number(e.target.value))}
                  className="w-full rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                />
                <span className="text-xs text-ink-muted">%</span>
              </div>
              <div className="mt-1 text-3xs text-ink-muted">现行 A 股标准：卖出单边收取 0.05%</div>
            </div>

            {/* 过户费与经手费 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3">
              <div className="text-xs font-semibold text-ink">过户与经手规费 (Transfer)</div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <input
                  type="number"
                  step={0.0005}
                  min={0}
                  max={0.05}
                  value={form.transfer_fee_pct}
                  disabled={!isAdmin}
                  onChange={(e) => update('transfer_fee_pct', Number(e.target.value))}
                  className="w-full rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                />
                <span className="text-xs text-ink-muted">%</span>
              </div>
              <div className="mt-1 text-3xs text-ink-muted">中登过户费及经手费，约万分之 0.1</div>
            </div>
          </div>

          {/* 最低佣金门槛与滑点模型 */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* 最低佣金 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink">单笔最低佣金门槛 (Min Fee)</span>
                <button
                  type="button"
                  disabled={!isAdmin}
                  onClick={() => update('min_commission', form.min_commission > 0 ? 0 : 5.0)}
                  className="text-2xs text-brand-600 hover:underline dark:text-brand-400"
                >
                  {form.min_commission > 0 ? '切换为免五 (0元)' : '恢复五元限制'}
                </button>
              </div>
              <div className="mt-1.5 flex items-center gap-2">
                <input
                  type="number"
                  step={1}
                  min={0}
                  max={50}
                  value={form.min_commission}
                  disabled={!isAdmin}
                  onChange={(e) => update('min_commission', Number(e.target.value))}
                  className="w-24 rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                />
                <span className="text-xs text-ink-secondary">元 / 单</span>
                <span className="text-3xs text-ink-muted">
                  ({form.min_commission > 0 ? '不足 5 元按 5 元计收，高频小单损耗大' : '机构免五席位，无最低收费门槛'})
                </span>
              </div>
            </div>

            {/* 滑点模型 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3">
              <div className="text-xs font-semibold text-ink">交易滑点冲击模型 (Slippage Model)</div>
              <div className="mt-1.5 flex items-center gap-3">
                <select
                  value={form.slippage_model}
                  disabled={!isAdmin}
                  onChange={(e) => update('slippage_model', e.target.value as any)}
                  className="w-48 rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                >
                  <option value="fixed">固定比率滑点 (Fixed Rate)</option>
                  <option value="almgren_chriss">Almgren-Chriss 平方根流动性冲击</option>
                  <option value="volatility">截面波动率动态折算模型</option>
                </select>

                {form.slippage_model === 'fixed' && (
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      step={0.01}
                      min={0}
                      max={1.0}
                      value={form.slippage_pct}
                      disabled={!isAdmin}
                      onChange={(e) => update('slippage_pct', Number(e.target.value))}
                      className="w-20 rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300"
                    />
                    <span className="text-xs text-ink-muted">%</span>
                  </div>
                )}
              </div>
              <div className="mt-1 text-3xs text-ink-muted">
                {form.slippage_model === 'almgren_chriss'
                  ? '根据订单金额占个股日均成交量比重依据平方根定律动态测算滑点，贴近机构大单实盘'
                  : '每笔成交在基准价上双向加罚固定百分比损耗'}
              </div>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* 3. 默认风险指标展示项 */}
      <SectionCard title="图表默认风控度量指标">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {RISK_INDICATOR_OPTIONS.map((item) => {
            const isChecked = form.risk_indicators.includes(item.key);
            return (
              <label
                key={item.key}
                className="flex cursor-pointer items-center gap-2 rounded border border-hair bg-surface-alt p-2 text-xs text-ink hover:bg-surface-sunken/40"
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  disabled={!isAdmin}
                  onChange={(e) => {
                    const next = e.target.checked
                      ? [...form.risk_indicators, item.key]
                      : form.risk_indicators.filter((k) => k !== item.key);
                    update('risk_indicators', next);
                  }}
                  className="h-3.5 w-3.5 accent-brand-500"
                />
                <span className="truncate">{item.label}</span>
              </label>
            );
          })}
        </div>
      </SectionCard>
    </div>
  );
}
