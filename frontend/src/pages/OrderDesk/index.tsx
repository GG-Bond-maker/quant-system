/** 实盘/模拟盘执行中心 + 多级量化风控中心 + TCA 成本归因。
 *
 * 架构规范：
 * 1. 顶部全局交易台控制舱仪表盘 (Cockpit Bar)：总资产、现金与2%缓冲刻度、在途敞口、三级风控盾牌；
 * 2. 3-Tab 体系：【交易执行大厅】、【量化风控中心】、【TCA 成本归因】；
 * 3. 严格遵循工程门禁：图表颜色经 chartPalette() 运行时读取，严禁硬编码 hex。
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';

import { ApiError } from '@/api/client';
import {
  deskApi,
  type ExclusionItemT,
  type PaperAccount,
  type PaperOrder,
  type PreflightCheckResult,
  type RiskStatusResult,
  type TcaResult,
} from '@/api/production';
import { useRefreshIntervalMs } from '@/hooks/useRefreshInterval';
import { useTheme } from '@/hooks/useTheme';
import { PageHeader, SectionCard } from '@/components/ui';
import { useChart } from '@/utils/useChart';

const inputCls =
  'w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300 bg-surface text-ink';

const STATUS_TONE: Record<string, string> = {
  FILLED: 'bg-success-bg text-success',
  PART_FILLED: 'bg-brand-50 text-brand-700',
  PENDING: 'bg-warn-bg text-warn',
  CANCELLED: 'bg-surface-sunken text-ink-muted',
  REJECTED: 'bg-danger-bg text-danger',
};

const STATUS_OPTIONS = ['PENDING', 'PART_FILLED', 'FILLED', 'CANCELLED', 'REJECTED'];
const ORDER_WINDOW = 200;

const EX_CATEGORY_LABELS: Record<string, string> = {
  manual_blacklist: '手工黑名单',
  st: 'ST 风险',
  delist_risk: '退市风险',
  illiquid: '流动性差',
};

type ActiveTab = 'execution' | 'risk' | 'tca';

export default function OrderDesk() {
  useTheme();

  // Tab 导航
  const [activeTab, setActiveTab] = useState<ActiveTab>('execution');

  // 基础数据状态
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [ordersTotal, setOrdersTotal] = useState<number | null>(null);
  const [ordersTruncated, setOrdersTruncated] = useState(false);
  const [riskStatus, setRiskStatus] = useState<RiskStatusResult | null>(null);
  const [tca, setTca] = useState<TcaResult | null>(null);
  const [exclusions, setExclusions] = useState<ExclusionItemT[]>([]);

  // 交互反馈与加载
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);

  // 模态框状态
  const [confirmKill, setConfirmKill] = useState(false);
  const [confirmLiquidate, setConfirmLiquidate] = useState(false);

  // 下单表单与事前风控预检
  const [form, setForm] = useState({
    symbol: '000001.SZ',
    side: 'buy' as 'buy' | 'sell',
    algo: 'vwap' as 'market' | 'vwap' | 'twap' | 'pov',
    order_amount: 100_000,
    split_days: 5,
    participation_cap: 0.05,
  });

  useEffect(() => {
    try {
      if (typeof window !== 'undefined' && window.location?.search) {
        const params = new URLSearchParams(window.location.search);
        const sym = params.get('symbol');
        const act = params.get('action') || params.get('side');
        if (sym) {
          setForm((prev) => ({
            ...prev,
            symbol: sym.trim().toUpperCase(),
            side: act === 'sell' ? 'sell' : act === 'buy' ? 'buy' : prev.side,
          }));
        }
      }
    } catch {
      /* ignore */
    }
  }, []);

  const [preflightResult, setPreflightResult] = useState<PreflightCheckResult | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);

  // 订单列表筛选与分页
  const [orderSearch, setOrderSearch] = useState('');
  const [orderStatus, setOrderStatus] = useState<string>('');
  const [orderSide, setOrderSide] = useState<string>('');
  const [orderPage, setOrderPage] = useState(1);
  const orderPageSize = 10;
  const [expandedOrder, setExpandedOrder] = useState<number | null>(null);

  // 禁买池表单与批量操作
  const [newEx, setNewEx] = useState({ symbol: '', category: 'manual_blacklist', reason: '' });
  const [exSearch, setExSearch] = useState('');
  const [exCategory, setExCategory] = useState<string>('');
  const [selectedEx, setSelectedEx] = useState<Set<number>>(new Set());

  // 投研联动闭环：凸优化批量母单草稿
  const [batchDraft, setBatchDraft] = useState<{
    source: string;
    method?: string;
    orders: Array<{
      symbol: string;
      side: 'buy' | 'sell';
      order_amount: number;
      algo: 'market' | 'vwap' | 'twap' | 'pov';
      split_days: number;
      participation_cap: number;
      target_weight?: number;
    }>;
    createdAt: string;
  } | null>(null);
  const [batchExecuting, setBatchExecuting] = useState(false);
  const [execProgress, setExecProgress] = useState<{ current: number; total: number } | null>(null);

  // 初始化加载草稿
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem('aqp:desk:batch_import');
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed?.orders) && parsed.orders.length) {
          setBatchDraft(parsed);
        }
      }
    } catch {
      /* 忽略脏数据 */
    }
  }, []);

  const clearBatchDraft = useCallback(() => {
    sessionStorage.removeItem('aqp:desk:batch_import');
    setBatchDraft(null);
  }, []);

  // 统一刷新数据
  const refresh = useCallback(async () => {
    try {
      const [acc, ords, rk, ex, tcaData] = await Promise.all([
        deskApi.account(),
        deskApi.orders(ORDER_WINDOW),
        deskApi.riskStatus ? deskApi.riskStatus().catch(() => null) : deskApi.killSwitch(),
        deskApi.exclusionList(),
        deskApi.tca(60).catch(() => null),
      ]);

      setAccount(acc);
      setOrders(ords.items);
      setOrdersTotal(ords.total);
      setOrdersTruncated(ords.truncated);
      setExclusions(ex);
      setTca(tcaData);

      // 适配新旧风控结构
      if (rk && 'rules' in rk) {
        setRiskStatus(rk as RiskStatusResult);
      } else if (rk) {
        setRiskStatus({
          kill_switch: (rk as { kill_switch?: boolean }).kill_switch ?? false,
          halt_buys: false,
          pending_orders: (rk as { pending_orders?: number }).pending_orders ?? 0,
          rules: {
            max_position_ratio: 0.55,
            max_single_order_amount: 1_000_000,
            min_cash_buffer_ratio: 0.02,
          },
        });
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '加载交易台数据失败');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Market 算法分批天数恒为 1
  useEffect(() => {
    if (form.algo === 'market' && form.split_days !== 1) {
      setForm((f) => ({ ...f, split_days: 1 }));
    }
  }, [form.algo, form.split_days]);

  // 自动刷新
  const refreshMs = useRefreshIntervalMs(2);
  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(() => void refresh(), refreshMs);
    return () => clearInterval(t);
  }, [autoRefresh, refresh, refreshMs]);

  // 触发事前风控预检
  const triggerPreflight = useCallback(
    async (sym: string, side: 'buy' | 'sell', amount: number) => {
      if (!sym.trim() || amount <= 0) return;
      setPreflightLoading(true);
      try {
        if (deskApi.preflight) {
          const res = await deskApi.preflight({ symbol: sym, side, order_amount: amount });
          setPreflightResult(res);
        } else {
          // 本地兜底估算
          const cash = account?.cash ?? 1_000_000;
          const eq = account?.equity ?? 1_000_000;
          const passed = side === 'buy' ? amount <= 1_000_000 && cash - amount >= eq * 0.02 : true;
          setPreflightResult({
            passed,
            reasons: passed ? [] : ['本地试算预检未通过'],
            warnings: [],
            metrics: {
              order_amount: amount,
              current_cash: cash,
              total_equity: eq,
              max_position_ratio: 0.55,
            },
          });
        }
      } catch {
        setPreflightResult(null);
      } finally {
        setPreflightLoading(false);
      }
    },
    [account],
  );

  useEffect(() => {
    const t = setTimeout(() => {
      void triggerPreflight(form.symbol, form.side, form.order_amount);
    }, 250);
    return () => clearTimeout(t);
  }, [form.symbol, form.side, form.order_amount, triggerPreflight]);

  // 提交母单
  const submitOrder = useCallback(async () => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const res = await deskApi.placeOrder(form);
      if (res.ok) {
        setMsg(`母单 #${res.order_id} 已成功提交（决策价 ${res.decision_price} 元）`);
      } else {
        setErr(`母单被风控拦截：${res.reason}`);
      }
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '下单提交失败');
    } finally {
      setBusy(false);
    }
  }, [form, refresh]);

  // 批量下单执行
  const executeBatchOrders = useCallback(async () => {
    if (!batchDraft?.orders.length) return;
    setBatchExecuting(true);
    setErr(null);
    setMsg(null);
    let successCount = 0;
    const failedList: string[] = [];

    for (let i = 0; i < batchDraft.orders.length; i++) {
      const o = batchDraft.orders[i];
      setExecProgress({ current: i + 1, total: batchDraft.orders.length });
      try {
        const res = await deskApi.placeOrder({
          symbol: o.symbol,
          side: o.side,
          order_amount: o.order_amount,
          algo: o.algo,
          split_days: o.split_days,
          participation_cap: o.participation_cap,
        });
        if (res.ok) {
          successCount++;
        } else {
          failedList.push(`${o.symbol}: ${res.reason || '被拦截'}`);
        }
      } catch (e) {
        failedList.push(`${o.symbol}: ${e instanceof Error ? e.message : '网络或服务异常'}`);
      }
    }

    setBatchExecuting(false);
    setExecProgress(null);
    sessionStorage.removeItem('aqp:desk:batch_import');
    setBatchDraft(null);
    await refresh();

    if (failedList.length === 0) {
      setMsg(`⚡ 组合批量报单成功！已提交全部 ${successCount} 笔母单，算法拆单与撮合闸门已就绪。`);
    } else {
      setMsg(`⚡ 批量报单处理完成：${successCount} 笔成功，${failedList.length} 笔失败。`);
      setErr(`部分母单拦截详情：${failedList.join('；')}`);
    }
  }, [batchDraft, refresh]);

  // 撮合到期子单
  const runFills = useCallback(async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await deskApi.runFills();
      setMsg(
        `撮合完成：成交 ${r.fills_created} 笔子单，顺延 ${r.deferred_children} 笔（顺延 = 目标执行日行情未同步，更新后自动撮合）`,
      );
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '撮合执行失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // L1 切换暂停开仓
  const toggleHaltBuys = useCallback(
    async (active: boolean) => {
      try {
        if (deskApi.setHaltBuys) {
          await deskApi.setHaltBuys(active, '控制台人工切换');
          setMsg(active ? 'L1 风险限额：已暂停新开仓（仅允许平仓）' : '已恢复正常开仓');
          await refresh();
        }
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : '切换失败');
      }
    },
    [refresh],
  );

  // L2 切换全局硬熔断
  const toggleKill = useCallback(
    async (active: boolean) => {
      setConfirmKill(false);
      try {
        const r = await deskApi.setKillSwitch(active, '控制台人工触发');
        setMsg(active ? `L2 硬熔断已激活，已撤销 ${r.cancelled_orders} 笔未完成母单` : '硬熔断已解除');
        await refresh();
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : '熔断操作失败');
      }
    },
    [refresh],
  );

  // L3 一键紧急清盘
  const handleLiquidateAll = useCallback(async () => {
    setConfirmLiquidate(false);
    setBusy(true);
    setMsg(null);
    try {
      if (deskApi.liquidateAll) {
        const res = await deskApi.liquidateAll();
        setMsg(
          `L3 紧急清盘完成：已撤销 ${res.cancelled_orders} 笔在途单，并对 ${res.liquidated_positions} 个持仓标的下达市价清盘撮合。`,
        );
        await refresh();
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '紧急清盘执行失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // 订单表筛选与分页
  const filteredOrders = useMemo(() => {
    let out = orders;
    if (orderSearch.trim()) {
      const q = orderSearch.trim().toUpperCase();
      out = out.filter((o) => o.symbol.toUpperCase().includes(q) || String(o.id).includes(q));
    }
    if (orderStatus) out = out.filter((o) => o.status === orderStatus);
    if (orderSide) out = out.filter((o) => o.side === orderSide);
    return out;
  }, [orders, orderSearch, orderStatus, orderSide]);

  const totalPages = Math.max(1, Math.ceil(filteredOrders.length / orderPageSize));
  const pagedOrders = filteredOrders.slice((orderPage - 1) * orderPageSize, orderPage * orderPageSize);
  useEffect(() => {
    setOrderPage(1);
  }, [orderSearch, orderStatus, orderSide]);

  // 禁买池筛选与批量管理
  const filteredExclusions = useMemo(() => {
    let out = exclusions;
    if (exSearch.trim()) {
      const q = exSearch.trim().toUpperCase();
      out = out.filter((x) => x.symbol.toUpperCase().includes(q));
    }
    if (exCategory) out = out.filter((x) => x.category === exCategory);
    return out;
  }, [exclusions, exSearch, exCategory]);

  const toggleExSelected = (id: number) =>
    setSelectedEx((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleAllEx = (checked: boolean) =>
    setSelectedEx(checked ? new Set(filteredExclusions.map((x) => x.id)) : new Set());

  const batchToggleEx = async (active: boolean) => {
    if (selectedEx.size === 0) return;
    setBusy(true);
    setErr(null);
    try {
      for (const id of selectedEx) {
        await deskApi.toggleExclusion(id, active);
      }
      setMsg(`已${active ? '启用' : '停用'} ${selectedEx.size} 条禁买标的`);
      setSelectedEx(new Set());
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '批量操作失败');
    } finally {
      setBusy(false);
    }
  };

  // 收益率派生
  const totalReturn =
    account && account.initial_cash > 0 ? (account.equity - account.initial_cash) / account.initial_cash : null;
  const cashRatio = account && account.equity > 0 ? (account.cash / account.equity) * 100 : 0;

  // 净值曲线 Option
  const navOption =
    account && account.nav_series.length > 1
      ? (() => {
          const p = chartPalette();
          return {
            grid: { left: 54, right: 12, top: 12, bottom: 22 },
            tooltip: { trigger: 'axis' },
            xAxis: {
              type: 'category',
              data: account.nav_series.map((n) => n.date),
              axisLabel: { fontSize: 8, color: p.INKM },
            },
            yAxis: {
              type: 'value',
              scale: true,
              axisLabel: {
                fontSize: 8,
                color: p.INKM,
                formatter: (v: number) => `${(v / 10000).toFixed(0)}万`,
              },
              splitLine: { lineStyle: { color: p.SUNKEN } },
            },
            series: [
              {
                name: '总权益',
                type: 'line',
                symbol: 'none',
                data: account.nav_series.map((n) => n.equity),
                lineStyle: { width: 1.6, color: p.BRAND },
                itemStyle: { color: p.BRAND },
                areaStyle: { opacity: 0.08 },
              },
            ],
          } as echarts.EChartsOption;
        })()
      : null;
  const navRef = useChart(navOption);

  // TCA 成本落差瀑布图 Option
  const waterfallOption = useMemo(() => {
    if (!tca || !tca.has_data) return null;
    const p = chartPalette();
    const s = tca.summary;
    const fee = s.fee_bps;
    const delay = s.delay_bps;
    const impact = s.avg_impact_bps;
    const total = s.total_shortfall_bps;

    const categories = ['规费与佣金 (Fee)', '时机延迟 (Delay)', '冲击成本 (Impact)', '总实现落差 (Total IS)'];
    const baseData = [0, delay >= 0 ? fee : Math.max(0, fee + delay), fee + delay, 0];
    const valData = [
      { value: fee, itemStyle: { color: p.BRAND } },
      { value: Math.abs(delay), itemStyle: { color: delay >= 0 ? p.UP : p.DOWN } },
      { value: impact, itemStyle: { color: p.UP } },
      { value: Math.abs(total), itemStyle: { color: total >= 0 ? p.DANGER : p.SUCCESS } },
    ];

    return {
      grid: { left: 50, right: 20, top: 25, bottom: 35 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any) => {
          const idx = params[0]?.dataIndex ?? 0;
          const rawVals = [fee, delay, impact, total];
          const v = rawVals[idx];
          return `<div class="text-xs"><b>${categories[idx]}</b><br/>贡献落差: <b>${v >= 0 ? '+' : ''}${v.toFixed(2)} bps</b></div>`;
        },
      },
      xAxis: {
        type: 'category',
        data: categories,
        axisLabel: { fontSize: 9, color: p.INK2, interval: 0 },
      },
      yAxis: {
        type: 'value',
        name: 'bps',
        nameTextStyle: { fontSize: 9, color: p.INKM },
        axisLabel: { fontSize: 9, color: p.INKM },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: 'Base',
          type: 'bar',
          stack: 'Shortfall',
          itemStyle: { borderColor: 'transparent', color: 'transparent' },
          emphasis: { itemStyle: { borderColor: 'transparent', color: 'transparent' } },
          data: baseData,
        },
        {
          name: 'Cost',
          type: 'bar',
          stack: 'Shortfall',
          label: {
            show: true,
            position: 'top',
            fontSize: 9,
            color: p.INK,
            formatter: (params: any) => {
              const rawVals = [fee, delay, impact, total];
              const v = rawVals[params.dataIndex];
              return `${v >= 0 ? '+' : ''}${v.toFixed(1)}`;
            },
          },
          data: valData,
        },
      ],
    } as echarts.EChartsOption;
  }, [tca]);
  const waterfallRef = useChart(waterfallOption);

  return (
    <div className="space-y-3">
      {/* 顶部标题与全局动作 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PageHeader
          title={
            <>
              交易与风控中心{' '}
              <span className="text-2xs font-normal text-warn">
                （Bloomberg EMSX 架构 · A股三级阶梯风控 · 本地真实撮合）
              </span>
            </>
          }
        />
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-2xs text-ink-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="h-3 w-3 rounded text-brand-600 focus:ring-brand-500"
            />
            自动刷新（8s）
          </label>
          <button
            onClick={() => void runFills()}
            disabled={busy}
            className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60 shadow-xs flex items-center gap-1"
          >
            <span>{busy ? '正在撮合…' : '⚡ 撮合到期子单'}</span>
          </button>
          <button
            onClick={() => void refresh()}
            disabled={busy}
            className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:bg-surface-alt disabled:opacity-50"
          >
            刷新
          </button>
        </div>
      </div>

      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger border border-danger/30">{err}</div>}
      {msg && <div className="rounded-md bg-brand-50 px-3 py-2 text-xs text-brand-700 border border-brand-200">{msg}</div>}

      {/* ==================== 顶部交易台控制舱仪表盘 (Cockpit Bar) ==================== */}
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
        {/* Card 1: 动态资产与总权益 */}
        <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs">
          <div className="flex items-center justify-between text-2xs text-ink-secondary">
            <span>动态权益 (NAV)</span>
            <span
              className={`num font-semibold ${totalReturn == null ? 'text-ink-muted' : totalReturn >= 0 ? 'text-danger' : 'text-success'}`}
            >
              {totalReturn != null ? `${totalReturn >= 0 ? '+' : ''}${(totalReturn * 100).toFixed(2)}%` : '—'}
            </span>
          </div>
          <div className="num mt-1 text-lg font-bold text-ink">
            {account ? `${account.equity.toLocaleString()} 元` : '—'}
          </div>
          <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
            <span>初始本金: {account ? `${account.initial_cash.toLocaleString()} 元` : '—'}</span>
            <span>
              夏普: {account?.sharpe != null ? account.sharpe.toFixed(2) : '—'} | 回撤:{' '}
              {account?.max_drawdown != null ? `${(account.max_drawdown * 100).toFixed(1)}%` : '—'}
            </span>
          </div>
        </div>

        {/* Card 2: 现金头寸与最低 2% 缓冲进度条 */}
        <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs">
          <div className="flex items-center justify-between text-2xs text-ink-secondary">
            <span>可用现金头寸</span>
            <span
              className={`rounded px-1.5 py-0.2 text-3xs font-medium ${
                cashRatio > 15
                  ? 'bg-success-bg text-success'
                  : cashRatio >= 5
                  ? 'bg-brand-50 text-brand-700'
                  : cashRatio >= 2
                  ? 'bg-warn-bg text-warn'
                  : 'bg-danger-bg text-danger'
              }`}
            >
              占比 {cashRatio.toFixed(1)}%
            </span>
          </div>
          <div className="num mt-1 text-lg font-bold text-ink">
            {account ? `${account.cash.toLocaleString()} 元` : '—'}
          </div>
          {/* 现金缓冲进度条 */}
          <div className="mt-1.5 space-y-1">
            <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
              <div
                className={`h-full rounded-full transition-all ${
                  cashRatio < 2 ? 'bg-danger' : cashRatio < 5 ? 'bg-warn' : 'bg-brand-500'
                }`}
                style={{ width: `${Math.min(100, Math.max(0, cashRatio))}%` }}
              />
            </div>
            <div className="flex justify-between text-3xs text-ink-muted">
              <span>防欠资安全线: 2.0%</span>
              <span>
                {cashRatio < 2 ? '⚠️ 已破 2% 备付底线' : cashRatio < 5 ? '⚡ 触及防守预警' : '✅ 资金流动性充裕'}
              </span>
            </div>
          </div>
        </div>

        {/* Card 3: 持仓市值与在途敞口 */}
        <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs">
          <div className="flex items-center justify-between text-2xs text-ink-secondary">
            <span>多头持仓市值</span>
            <span className="text-3xs text-ink-muted">
              在持标的: {account ? Object.keys(account.positions).length : 0} 支
            </span>
          </div>
          <div className="num mt-1 text-lg font-bold text-ink">
            {account ? `${account.market_value.toLocaleString()} 元` : '—'}
          </div>
          <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
            <span>
              挂单在途: <b className="text-ink">{riskStatus?.pending_orders ?? orders.filter(o => o.status === 'PENDING' || o.status === 'PART_FILLED').length}</b> 笔
            </span>
            <span>累计交易佣金: {account ? `${account.total_fees.toFixed(1)} 元` : '—'}</span>
          </div>
        </div>

        {/* Card 4: 风控安全盾牌与应急响应 */}
        <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-2xs text-ink-secondary">风控响应级别</span>
            <span
              className={`rounded px-2 py-0.5 text-2xs font-bold ${
                riskStatus?.kill_switch
                  ? 'bg-danger-bg text-danger animate-pulse'
                  : riskStatus?.halt_buys
                  ? 'bg-warn-bg text-warn'
                  : 'bg-success-bg text-success'
              }`}
            >
              {riskStatus?.kill_switch ? 'L2 全局硬熔断' : riskStatus?.halt_buys ? 'L1 仅允许平仓' : 'L0 平稳运行中'}
            </span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            {riskStatus?.kill_switch ? (
              <button
                onClick={() => void toggleKill(false)}
                className="w-full rounded bg-brand-500 py-1 text-xs font-semibold text-white hover:bg-brand-600"
              >
                解除熔断
              </button>
            ) : (
              <>
                <button
                  onClick={() => setConfirmKill(true)}
                  className="flex-1 rounded border border-danger/40 bg-danger-bg py-1 text-xs font-semibold text-danger hover:bg-danger hover:text-white transition-colors"
                >
                  一键熔断
                </button>
                <button
                  onClick={() => setConfirmLiquidate(true)}
                  className="flex-1 rounded border border-warn/40 bg-warn-bg py-1 text-xs font-semibold text-warn hover:bg-warn hover:text-white transition-colors"
                >
                  紧急清盘
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ==================== 3-Tab 导航切换 ==================== */}
      <div className="flex border-b border-hair bg-surface rounded-t-lg px-2 pt-1">
        <button
          onClick={() => setActiveTab('execution')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-xs font-semibold transition-colors ${
            activeTab === 'execution'
              ? 'border-brand-500 text-brand-600'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>⚡ 交易执行大厅</span>
          <span className="rounded bg-brand-50 px-1.5 py-0.2 text-3xs text-brand-700">
            {orders.length} 笔母单
          </span>
        </button>

        <button
          onClick={() => setActiveTab('risk')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-xs font-semibold transition-colors ${
            activeTab === 'risk'
              ? 'border-brand-500 text-brand-600'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>🛡️ 量化风控中心</span>
          <span
            className={`rounded px-1.5 py-0.2 text-3xs ${
              riskStatus?.kill_switch
                ? 'bg-danger-bg text-danger'
                : riskStatus?.halt_buys
                ? 'bg-warn-bg text-warn'
                : 'bg-success-bg text-success'
            }`}
          >
            {riskStatus?.kill_switch ? 'L2 熔断' : riskStatus?.halt_buys ? 'L1 停买' : '合规受控'}
          </span>
        </button>

        <button
          onClick={() => setActiveTab('tca')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-xs font-semibold transition-colors ${
            activeTab === 'tca'
              ? 'border-brand-500 text-brand-600'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>📊 TCA 交易成本与归因</span>
          {tca?.has_data && (
            <span className="rounded bg-surface-alt px-1.5 py-0.2 text-3xs text-ink-muted">
              落差 {tca.summary.total_shortfall_bps.toFixed(1)} bps
            </span>
          )}
        </button>
      </div>

      {/* ==================== 投研联动闭环草稿 (在执行Tab全局置顶) ==================== */}
      {batchDraft && batchDraft.orders.length > 0 && activeTab === 'execution' && (
        <div className="rounded-lg border border-brand-500/30 bg-surface p-3.5 space-y-2.5 shadow-xs">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-500 text-3xs font-bold text-white">
                ⚡
              </span>
              <div>
                <span className="text-xs font-semibold text-ink">
                  投研联动闭环：待执行调仓母单草稿
                </span>
                <span className="ml-2 rounded bg-brand-50 px-1.5 py-0.5 text-3xs font-medium text-brand-700">
                  来源：{batchDraft.source}
                </span>
                <span className="ml-2 text-3xs text-ink-muted">
                  生成时间：{new Date(batchDraft.createdAt).toLocaleTimeString()}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => void executeBatchOrders()}
                disabled={busy || batchExecuting}
                className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60 flex items-center gap-1.5 shadow-xs"
              >
                <span>
                  {batchExecuting
                    ? `正在批量报单 (${execProgress?.current ?? 0}/${batchDraft.orders.length})…`
                    : `⚡ 一键批量报单 (${batchDraft.orders.length} 笔)`}
                </span>
              </button>
              <button
                onClick={clearBatchDraft}
                disabled={batchExecuting}
                className="rounded-md border border-hair px-2.5 py-1.5 text-xs text-ink-secondary hover:bg-surface-alt disabled:opacity-50"
              >
                丢弃草稿
              </button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-2xs">
              <thead>
                <tr className="border-b border-hair text-ink-secondary">
                  <th className="py-1 text-left font-medium">标的代码</th>
                  <th className="py-1 text-center font-medium">买卖方向</th>
                  <th className="py-1 text-right font-medium">目标权重</th>
                  <th className="py-1 text-right font-medium">拟报单金额</th>
                  <th className="py-1 text-center font-medium">拆单算法</th>
                  <th className="py-1 text-center font-medium">分批天数</th>
                  <th className="py-1 text-right font-medium">单日参与率上限</th>
                </tr>
              </thead>
              <tbody>
                {batchDraft.orders.map((o) => (
                  <tr key={o.symbol} className="border-b border-hair/50">
                    <td className="py-1.5 font-mono font-medium text-ink">{o.symbol}</td>
                    <td className="py-1.5 text-center">
                      <span className="rounded bg-danger-bg px-1.5 py-0.2 font-medium text-danger">
                        BUY 买入
                      </span>
                    </td>
                    <td className="num py-1.5 text-right font-medium text-brand-600">
                      {o.target_weight != null ? `${o.target_weight}%` : '—'}
                    </td>
                    <td className="num py-1.5 text-right font-medium text-ink">
                      {o.order_amount.toLocaleString()} 元
                    </td>
                    <td className="py-1.5 text-center font-mono uppercase text-ink-secondary">{o.algo}</td>
                    <td className="num py-1.5 text-center text-ink-secondary">{o.split_days} 天</td>
                    <td className="num py-1.5 text-right text-ink-secondary">
                      {(o.participation_cap * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ==================== TAB 1: 交易执行大厅 ==================== */}
      {activeTab === 'execution' && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
          {/* 左侧 4 列：下单表单 + 事前风控预检 */}
          <div className="space-y-3 xl:col-span-4">
            <SectionCard title="提交母单（算法分批拆单）" bodyClassName="space-y-2.5">
              <div>
                <label className="block text-2xs font-medium text-ink-secondary mb-1">标的代码</label>
                <input
                  value={form.symbol}
                  placeholder="如 000001.SZ 或 510300.SH"
                  className={inputCls}
                  onChange={(e) => setForm({ ...form, symbol: e.target.value.toUpperCase() })}
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-2xs font-medium text-ink-secondary mb-1">方向</label>
                  <select
                    value={form.side}
                    className={inputCls}
                    onChange={(e) => setForm({ ...form, side: e.target.value as 'buy' | 'sell' })}
                  >
                    <option value="buy">BUY 买入</option>
                    <option value="sell">SELL 卖出</option>
                  </select>
                </div>
                <div>
                  <label className="block text-2xs font-medium text-ink-secondary mb-1">执行算法</label>
                  <select
                    value={form.algo}
                    className={inputCls}
                    onChange={(e) => setForm({ ...form, algo: e.target.value as typeof form.algo })}
                  >
                    <option value="market">Market 一次性</option>
                    <option value="vwap">VWAP 成交量加权</option>
                    <option value="twap">TWAP 时间匀速</option>
                    <option value="pov">POV 动态跟随</option>
                  </select>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-2xs font-medium text-ink-secondary">报单总金额 (元)</label>
                  <div className="flex gap-1">
                    {[100_000, 300_000, 500_000, 1_000_000].map((amt) => (
                      <button
                        key={amt}
                        type="button"
                        onClick={() => setForm({ ...form, order_amount: amt })}
                        className={`rounded px-1.5 py-0.2 text-3xs ${
                          form.order_amount === amt
                            ? 'bg-brand-500 text-white'
                            : 'bg-surface-sunken text-ink-muted hover:bg-surface-alt'
                        }`}
                      >
                        {amt / 10000}万
                      </button>
                    ))}
                  </div>
                </div>
                <input
                  type="number"
                  min={10000}
                  step={50000}
                  value={form.order_amount}
                  className={inputCls}
                  onChange={(e) => setForm({ ...form, order_amount: Number(e.target.value) })}
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-2xs font-medium text-ink-secondary mb-1">
                    拆单天数 {form.algo === 'market' && '(Market恒为1)'}
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={form.split_days}
                    disabled={form.algo === 'market'}
                    className={`${inputCls} ${form.algo === 'market' ? 'opacity-50 cursor-not-allowed' : ''}`}
                    onChange={(e) => setForm({ ...form, split_days: Number(e.target.value) })}
                  />
                </div>
                <div>
                  <label className="block text-2xs font-medium text-ink-secondary mb-1">
                    参与率上限 ({(form.participation_cap * 100).toFixed(1)}%)
                  </label>
                  <input
                    type="range"
                    min={0.005}
                    max={0.3}
                    step={0.005}
                    value={form.participation_cap}
                    className="w-full mt-2"
                    onChange={(e) => setForm({ ...form, participation_cap: Number(e.target.value) })}
                  />
                </div>
              </div>

              {/* 事前量化风控预检 (Preflight Gate) */}
              <div className="rounded-md border border-hair bg-surface-alt p-2.5 space-y-1.5">
                <div className="flex items-center justify-between text-2xs">
                  <span className="font-semibold text-ink flex items-center gap-1">
                    <span>🛡️ 事前量化风控预检</span>
                    {preflightLoading && <span className="text-3xs text-ink-muted">试算中…</span>}
                  </span>
                  <span
                    className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                      preflightResult?.passed ? 'bg-success-bg text-success' : 'bg-danger-bg text-danger'
                    }`}
                  >
                    {preflightResult?.passed ? '通过检验' : '拦截风险'}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-1 text-3xs">
                  <div className="flex items-center gap-1 text-ink-secondary">
                    <span>{form.order_amount <= 1_000_000 ? '✅' : '❌'}</span>
                    <span>防肥手指限额 (≤100万)</span>
                  </div>
                  <div className="flex items-center gap-1 text-ink-secondary">
                    <span>
                      {account && form.side === 'buy' && account.cash - form.order_amount >= account.equity * 0.02
                        ? '✅'
                        : form.side === 'sell'
                        ? '✅'
                        : '❌'}
                    </span>
                    <span>2%现金备付缓冲守卫</span>
                  </div>
                  <div className="flex items-center gap-1 text-ink-secondary">
                    <span>
                      {exclusions.some((x) => x.symbol === form.symbol && x.active) ? '❌' : '✅'}
                    </span>
                    <span>合规禁买池命中检测</span>
                  </div>
                  <div className="flex items-center gap-1 text-ink-secondary">
                    <span>{riskStatus?.kill_switch ? '❌' : riskStatus?.halt_buys && form.side === 'buy' ? '❌' : '✅'}</span>
                    <span>熔断/限仓状态受控</span>
                  </div>
                </div>

                {preflightResult && !preflightResult.passed && preflightResult.reasons.length > 0 && (
                  <div className="rounded bg-danger-bg px-2 py-1 text-3xs text-danger">
                    {preflightResult.reasons.join('；')}
                  </div>
                )}
                {preflightResult && preflightResult.warnings.length > 0 && (
                  <div className="rounded bg-warn-bg px-2 py-1 text-3xs text-warn">
                    {preflightResult.warnings.join('；')}
                  </div>
                )}
              </div>

              <button
                disabled={busy || (preflightResult != null && !preflightResult.passed)}
                onClick={() => void submitOrder()}
                className="w-full rounded-md bg-brand-500 py-2 text-xs font-semibold text-white hover:bg-brand-600 disabled:opacity-50 transition-colors shadow-xs"
              >
                {busy ? '正在提交…' : '提交母单'}
              </button>

              <p className="text-3xs text-ink-muted leading-relaxed">
                💡 <b>撮合规则</b>：母单提交后生成分批子单计划，D0+1 起由各执行日真实开盘价与平方根冲击定律进行撮合。严禁偷价，无未来函数。
              </p>
            </SectionCard>
          </div>

          {/* 右侧 8 列：母单 / 子单监控 */}
          <div className="space-y-3 xl:col-span-8">
            <SectionCard title="母单 / 子单监控（成交价 vs 决策价 = 基差）" bodyClassName="space-y-2">
              {/* 筛选条 */}
              <div className="flex flex-wrap items-end gap-2 text-2xs">
                <input
                  value={orderSearch}
                  placeholder="搜索标的 / #ID"
                  className={`${inputCls} w-32`}
                  onChange={(e) => setOrderSearch(e.target.value)}
                />
                <select
                  value={orderStatus}
                  className={`${inputCls} w-32`}
                  onChange={(e) => setOrderStatus(e.target.value)}
                >
                  <option value="">全部状态</option>
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <select
                  value={orderSide}
                  className={`${inputCls} w-24`}
                  onChange={(e) => setOrderSide(e.target.value)}
                >
                  <option value="">全部方向</option>
                  <option value="buy">买入</option>
                  <option value="sell">卖出</option>
                </select>
                <span className="text-ink-muted">
                  当前 {filteredOrders.length} 笔{filteredOrders.length !== orders.length && `（共 ${orders.length}）`}
                </span>
                <span className={ordersTruncated ? 'text-warn' : 'text-ink-muted'}>
                  {ordersTotal != null ? `母单总数 ${ordersTotal} 笔` : ''}
                  {ordersTruncated && '（窗口展示最近200条）'}
                </span>
                {totalPages > 1 && (
                  <div className="ml-auto flex items-center gap-1">
                    <button
                      onClick={() => setOrderPage((p) => Math.max(1, p - 1))}
                      disabled={orderPage <= 1}
                      className="rounded border border-hair px-1.5 py-0.5 disabled:opacity-40"
                    >
                      ‹
                    </button>
                    <span className="text-ink-muted">
                      {orderPage}/{totalPages}
                    </span>
                    <button
                      onClick={() => setOrderPage((p) => Math.min(totalPages, p + 1))}
                      disabled={orderPage >= totalPages}
                      className="rounded border border-hair px-1.5 py-0.5 disabled:opacity-40"
                    >
                      ›
                    </button>
                  </div>
                )}
              </div>

              {/* 订单明细表 */}
              <div className="max-h-[520px] overflow-auto">
                <table className="w-full text-2xs">
                  <thead className="sticky top-0 bg-surface">
                    <tr className="border-b border-hair text-ink-secondary">
                      <th className="text-left font-medium py-1.5">#</th>
                      <th className="text-left font-medium py-1.5">标的代码</th>
                      <th className="text-center font-medium py-1.5">方向</th>
                      <th className="text-center font-medium py-1.5">算法</th>
                      <th className="text-right font-medium py-1.5">委托金额</th>
                      <th className="text-right font-medium py-1.5">已成金额</th>
                      <th className="text-center font-medium py-1.5">状态</th>
                      <th className="text-right font-medium py-1.5">决策价</th>
                      <th className="text-center font-medium py-1.5">子单明细</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pagedOrders.map((o) => (
                      <Fragment key={o.id}>
                        <tr
                          className="border-t border-hair align-top cursor-pointer hover:bg-surface-alt transition-colors"
                          onClick={() => setExpandedOrder(expandedOrder === o.id ? null : o.id)}
                        >
                          <td className="py-2 text-ink-muted">#{o.id}</td>
                          <td className="py-2 font-mono font-medium text-ink">{o.symbol}</td>
                          <td className={`py-2 text-center font-medium ${o.side === 'buy' ? 'text-danger' : 'text-success'}`}>
                            {o.side === 'buy' ? '买入' : '卖出'}
                          </td>
                          <td className="py-2 text-center uppercase font-mono text-ink-secondary">{o.algo}</td>
                          <td className="num py-2 text-right">{o.order_amount.toLocaleString()}</td>
                          <td className="num py-2 text-right text-brand-600 font-medium">
                            {o.filled_amount.toLocaleString()}
                          </td>
                          <td className="py-2 text-center">
                            <span className={`rounded px-1.5 py-0.5 text-3xs font-medium ${STATUS_TONE[o.status] ?? ''}`}>
                              {o.status}
                            </span>
                            {o.reject_reason && (
                              <div className="mt-0.5 text-3xs text-danger truncate max-w-[140px]" title={o.reject_reason}>
                                {o.reject_reason}
                              </div>
                            )}
                          </td>
                          <td className="num py-2 text-right text-ink-secondary">{o.decision_price ?? '—'}</td>
                          <td className="py-2 text-center">
                            {o.fills.length ? (
                              <span className="text-brand-600 hover:underline">
                                {expandedOrder === o.id ? '收起 ▴' : `${o.fills.length} 笔成交 ▾`}
                              </span>
                            ) : (
                              <span className="text-ink-muted">待撮合</span>
                            )}
                          </td>
                        </tr>

                        {expandedOrder === o.id && o.fills.length > 0 && (
                          <tr className="bg-surface-alt">
                            <td colSpan={9} className="p-2.5">
                              <div className="space-y-1">
                                <div className="text-3xs font-semibold text-ink-secondary mb-1">
                                  子单成交流水（决策基差与平方根冲击明细）：
                                </div>
                                <div className="space-y-0.5 font-mono text-2xs">
                                  {o.fills.map((f, i) => (
                                    <div
                                      key={i}
                                      className="flex flex-wrap justify-between border-b border-hair/50 py-1 text-ink"
                                    >
                                      <span>
                                        📅 {f.exec_date} · <b>{f.qty}</b> 股 @ <b>{f.price.toFixed(3)}</b> 元 (
                                        {f.amount.toLocaleString()} 元)
                                      </span>
                                      <span className="space-x-2">
                                        <span className="text-ink-secondary">佣金: {f.fee.toFixed(1)}元</span>
                                        <span className="text-danger">冲击: {f.impact_bps}bps</span>
                                        <span
                                          className={`font-semibold ${
                                            f.basis_bps >= 0 ? 'text-danger' : 'text-success'
                                          }`}
                                        >
                                          基差: {f.basis_bps >= 0 ? '+' : ''}
                                          {f.basis_bps}bps
                                        </span>
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}

                    {!pagedOrders.length && (
                      <tr>
                        <td colSpan={9} className="py-12 text-center text-xs text-ink-muted">
                          {orders.length ? '未找到符合条件的订单' : '暂无订单，请在左侧提交母单并撮合'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </div>
        </div>
      )}

      {/* ==================== TAB 2: 量化风控中心 ==================== */}
      {activeTab === 'risk' && (
        <div className="space-y-3">
          {/* 三级阶梯应急风控控制台 */}
          <SectionCard title="三级阶梯量化风控应急控制中心 (Tiered Risk Engine)" bodyClassName="p-3">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              {/* L1 控制台 */}
              <div
                className={`rounded-lg border p-3.5 space-y-2 transition-all ${
                  riskStatus?.halt_buys
                    ? 'border-warn bg-warn-bg/20'
                    : 'border-hair bg-surface'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-warn text-3xs font-bold text-white">
                      L1
                    </span>
                    <span className="text-xs font-bold text-ink">暂停新开仓 (Halt Buys)</span>
                  </div>
                  <span
                    className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                      riskStatus?.halt_buys ? 'bg-warn text-white' : 'bg-surface-sunken text-ink-muted'
                    }`}
                  >
                    {riskStatus?.halt_buys ? '已启用' : '未激活'}
                  </span>
                </div>
                <p className="text-2xs text-ink-secondary leading-relaxed">
                  拦截一切买入开仓单，但允许持仓平仓卖出。用于应对结构性宏观风险、资金面紧张或组合再平衡准备。
                </p>
                <button
                  onClick={() => void toggleHaltBuys(!riskStatus?.halt_buys)}
                  disabled={busy}
                  className={`w-full rounded py-1.5 text-xs font-semibold transition-colors ${
                    riskStatus?.halt_buys
                      ? 'border border-hair bg-surface text-ink hover:bg-surface-alt'
                      : 'border border-warn/40 bg-warn-bg text-warn hover:bg-warn hover:text-white'
                  }`}
                >
                  {riskStatus?.halt_buys ? '恢复允许开仓' : '激活 L1 暂停买入'}
                </button>
              </div>

              {/* L2 控制台 */}
              <div
                className={`rounded-lg border p-3.5 space-y-2 transition-all ${
                  riskStatus?.kill_switch
                    ? 'border-danger bg-danger-bg/20'
                    : 'border-hair bg-surface'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-danger text-3xs font-bold text-white">
                      L2
                    </span>
                    <span className="text-xs font-bold text-ink">全局硬熔断 (Kill Switch)</span>
                  </div>
                  <span
                    className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                      riskStatus?.kill_switch ? 'bg-danger text-white' : 'bg-surface-sunken text-ink-muted'
                    }`}
                  >
                    {riskStatus?.kill_switch ? '硬熔断中' : '正常待命'}
                  </span>
                </div>
                <p className="text-2xs text-ink-secondary leading-relaxed">
                  拒绝一切新订单写入，并立即撤销全部在途未完成母单（已成交保留）。用于应对行情异常、系统故障或极端黑天鹅。
                </p>
                {riskStatus?.kill_switch ? (
                  <button
                    onClick={() => void toggleKill(false)}
                    disabled={busy}
                    className="w-full rounded border border-hair bg-surface py-1.5 text-xs font-semibold text-ink hover:bg-surface-alt"
                  >
                    解除硬熔断
                  </button>
                ) : (
                  <button
                    onClick={() => setConfirmKill(true)}
                    disabled={busy}
                    className="w-full rounded bg-danger py-1.5 text-xs font-semibold text-white hover:bg-danger/90 shadow-xs"
                  >
                    激活 L2 硬熔断
                  </button>
                )}
              </div>

              {/* L3 控制台 */}
              <div className="rounded-lg border border-hair bg-surface p-3.5 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ink text-3xs font-bold text-white">
                      L3
                    </span>
                    <span className="text-xs font-bold text-ink">一键紧急清盘 (Liquidate All)</span>
                  </div>
                  <span className="rounded bg-surface-sunken px-1.5 py-0.2 text-3xs font-medium text-ink-muted">
                    终极防守
                  </span>
                </div>
                <p className="text-2xs text-ink-secondary leading-relaxed">
                  撤销全部在途挂单，对全部持仓标的下达市价清仓单，并自动触发撮合平仓，快速回收全部可用现金。
                </p>
                <button
                  onClick={() => setConfirmLiquidate(true)}
                  disabled={busy}
                  className="w-full rounded border border-danger/60 bg-danger-bg py-1.5 text-xs font-semibold text-danger hover:bg-danger hover:text-white transition-colors"
                >
                  触发 L3 紧急平仓
                </button>
              </div>
            </div>
          </SectionCard>

          {/* 事前风控规则矩阵指标卡 */}
          <SectionCard title="事前量化风控限额矩阵 (Pre-Trade Limits Matrix)" bodyClassName="p-3">
            <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
              <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                <div className="text-3xs text-ink-muted">单标的集中度上限</div>
                <div className="num mt-0.5 text-base font-bold text-ink">
                  {((riskStatus?.rules.max_position_ratio ?? 0.55) * 100).toFixed(0)}%
                </div>
                <div className="text-3xs text-ink-secondary mt-0.5">防重仓穿透，超限直接拦截</div>
              </div>

              <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                <div className="text-3xs text-ink-muted">防肥手指单笔限额</div>
                <div className="num mt-0.5 text-base font-bold text-ink">
                  {((riskStatus?.rules.max_single_order_amount ?? 1_000_000) / 10000).toFixed(0)} 万元
                </div>
                <div className="text-3xs text-ink-secondary mt-0.5">防止键盘错按超额报单</div>
              </div>

              <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                <div className="text-3xs text-ink-muted">最低现金缓冲率</div>
                <div className="num mt-0.5 text-base font-bold text-brand-600">
                  {((riskStatus?.rules.min_cash_buffer_ratio ?? 0.02) * 100).toFixed(1)}%
                </div>
                <div className="text-3xs text-ink-secondary mt-0.5">备付印花税、经手费与佣金</div>
              </div>

              <div className="rounded-md border border-hair bg-surface-alt p-2.5">
                <div className="text-3xs text-ink-muted">撮合无未来函数门禁</div>
                <div className="num mt-0.5 text-base font-bold text-success">D0+1 顺延</div>
                <div className="text-3xs text-ink-secondary mt-0.5">严禁当日偷价当日成交</div>
              </div>
            </div>
          </SectionCard>

          {/* 下方双栏：左合规禁买池，右持仓结构透视 */}
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
            {/* 合规禁买池 */}
            <div className="xl:col-span-6">
              <SectionCard title="合规禁买池管理 (Restricted List)" bodyClassName="space-y-2">
                <div className="flex flex-wrap items-end gap-2 text-2xs">
                  <input
                    value={newEx.symbol}
                    placeholder="标的代码 (如 600000.SH)"
                    className={`${inputCls} w-36`}
                    onChange={(e) => setNewEx({ ...newEx, symbol: e.target.value.toUpperCase() })}
                  />
                  <select
                    value={newEx.category}
                    className={`${inputCls} w-32`}
                    onChange={(e) => setNewEx({ ...newEx, category: e.target.value })}
                  >
                    <option value="manual_blacklist">手工黑名单</option>
                    <option value="st">ST 风险</option>
                    <option value="delist_risk">退市风险</option>
                    <option value="illiquid">流动性差</option>
                  </select>
                  <input
                    value={newEx.reason}
                    placeholder="合规理由"
                    className={`${inputCls} flex-1 min-w-[120px]`}
                    onChange={(e) => setNewEx({ ...newEx, reason: e.target.value })}
                  />
                  <button
                    disabled={busy || !newEx.symbol}
                    onClick={async () => {
                      try {
                        await deskApi.addExclusion(newEx.symbol, newEx.category, newEx.reason);
                        setNewEx({ ...newEx, symbol: '', reason: '' });
                        await refresh();
                      } catch (e) {
                        setErr(e instanceof ApiError ? e.message : '添加失败');
                      }
                    }}
                    className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
                  >
                    加入禁买池
                  </button>
                  <button
                    disabled={busy}
                    onClick={async () => {
                      try {
                        const cands = await deskApi.screenCandidates();
                        for (const c of cands.slice(0, 10)) {
                          await deskApi.addExclusion(c.symbol, c.category, c.reason);
                        }
                        await refresh();
                      } catch (e) {
                        setErr(e instanceof ApiError ? e.message : '导入失败');
                      }
                    }}
                    className="rounded-md border border-hair px-2.5 py-1.5 text-xs text-ink-secondary hover:bg-surface-alt disabled:opacity-50"
                  >
                    一键导入 ST/流动性差标的
                  </button>
                </div>

                {selectedEx.size > 0 && (
                  <div className="flex items-center gap-2 rounded-md bg-brand-50 px-2 py-1 text-2xs text-brand-700">
                    已选 {selectedEx.size} 条
                    <button
                      disabled={busy}
                      onClick={() => void batchToggleEx(true)}
                      className="rounded border border-hair px-2 py-0.5 hover:bg-surface-alt"
                    >
                      批量启用
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void batchToggleEx(false)}
                      className="rounded border border-hair px-2 py-0.5 hover:bg-surface-alt"
                    >
                      批量停用
                    </button>
                    <button
                      onClick={() => setSelectedEx(new Set())}
                      className="rounded border border-hair px-2 py-0.5 hover:bg-surface-alt"
                    >
                      清空选择
                    </button>
                  </div>
                )}

                <div className="flex items-center gap-2 text-2xs">
                  <input
                    value={exSearch}
                    placeholder="搜索标的代码"
                    className={`${inputCls} w-32`}
                    onChange={(e) => setExSearch(e.target.value)}
                  />
                  <select
                    value={exCategory}
                    className={`${inputCls} w-32`}
                    onChange={(e) => setExCategory(e.target.value)}
                  >
                    <option value="">全部类别</option>
                    {Object.entries(EX_CATEGORY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                  <span className="text-ink-muted ml-auto">共 {filteredExclusions.length} 条禁买规则</span>
                </div>

                <div className="max-h-60 overflow-auto">
                  <table className="w-full text-2xs">
                    <thead className="sticky top-0 bg-surface">
                      <tr className="border-b border-hair text-ink-secondary">
                        <th className="w-6 text-center py-1">
                          <input
                            type="checkbox"
                            checked={filteredExclusions.length > 0 && selectedEx.size === filteredExclusions.length}
                            onChange={(e) => toggleAllEx(e.target.checked)}
                          />
                        </th>
                        <th className="text-left font-medium py-1">标的代码</th>
                        <th className="text-center font-medium py-1">风险类别</th>
                        <th className="text-left font-medium py-1">原因</th>
                        <th className="text-center font-medium py-1">状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredExclusions.map((x) => (
                        <tr key={x.id} className="border-t border-hair hover:bg-surface-alt">
                          <td className="text-center py-1.5">
                            <input
                              type="checkbox"
                              checked={selectedEx.has(x.id)}
                              onChange={() => toggleExSelected(x.id)}
                            />
                          </td>
                          <td className="font-mono font-medium text-ink py-1.5">{x.symbol}</td>
                          <td className="text-center py-1.5">
                            <span className="rounded bg-surface-sunken px-1.5 py-0.2 text-3xs text-ink-secondary">
                              {EX_CATEGORY_LABELS[x.category] ?? x.category}
                            </span>
                          </td>
                          <td className="text-ink-secondary py-1.5 truncate max-w-[140px]">{x.reason}</td>
                          <td className="text-center py-1.5">
                            <button
                              onClick={async () => {
                                await deskApi.toggleExclusion(x.id, !x.active);
                                await refresh();
                              }}
                              className={`rounded px-1.5 py-0.2 text-3xs font-medium ${
                                x.active ? 'bg-danger-bg text-danger' : 'bg-surface-sunken text-ink-muted'
                              }`}
                            >
                              {x.active ? '生效中' : '已停用'}
                            </button>
                          </td>
                        </tr>
                      ))}
                      {!filteredExclusions.length && (
                        <tr>
                          <td colSpan={5} className="py-8 text-center text-ink-muted">
                            禁买池为空（下单不受合规限制）
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </SectionCard>
            </div>

            {/* 持仓结构与盈亏透视 */}
            <div className="xl:col-span-6">
              <SectionCard title="实时持仓结构与盈亏透视 (Positions & PnL)" bodyClassName="space-y-2">
                {account && Object.keys(account.positions).length > 0 ? (
                  <div className="space-y-2 max-h-[380px] overflow-auto">
                    {Object.entries(account.positions).map(([sym, pos]) => {
                      const posWeight = account.equity > 0 ? (pos.market_value / account.equity) * 100 : 0;
                      return (
                        <div key={sym} className="rounded-lg border border-hair bg-surface-alt p-2.5 space-y-1.5">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-1.5">
                              <span className="font-mono text-xs font-bold text-ink">{sym}</span>
                              <span className="text-3xs text-ink-muted">
                                {pos.qty} 股 · 成本 {pos.cost_price.toFixed(2)} · 现价 {pos.last_price.toFixed(2)}
                              </span>
                            </div>
                            <span
                              className={`num text-xs font-bold ${pos.pnl >= 0 ? 'text-danger' : 'text-success'}`}
                            >
                              {pos.pnl >= 0 ? '+' : ''}
                              {pos.pnl.toFixed(0)} 元 ({pos.cost_price > 0 ? `${((pos.pnl / (pos.cost_price * pos.qty)) * 100).toFixed(2)}%` : '—'})
                            </span>
                          </div>

                          {/* 集中度条形图 */}
                          <div>
                            <div className="flex justify-between text-3xs text-ink-secondary mb-0.5">
                              <span>仓位占比: {posWeight.toFixed(1)}%</span>
                              <span className={posWeight > 55 ? 'text-danger font-bold' : 'text-ink-muted'}>
                                上限: 55%
                              </span>
                            </div>
                            <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
                              <div
                                className={`h-full rounded-full transition-all ${
                                  posWeight > 55 ? 'bg-danger' : posWeight > 35 ? 'bg-warn' : 'bg-brand-500'
                                }`}
                                style={{ width: `${Math.min(100, posWeight)}%` }}
                              />
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="py-12 text-center text-xs text-ink-muted">
                    当前空仓状态，无持仓风险敞口。
                  </div>
                )}
              </SectionCard>
            </div>
          </div>
        </div>
      )}

      {/* ==================== TAB 3: TCA 交易成本归因 ==================== */}
      {activeTab === 'tca' && (
        <div className="space-y-3">
          {tca && tca.has_data ? (
            <>
              {/* 5 大核心成本指标卡 */}
              <div className="grid grid-cols-2 gap-2 text-2xs md:grid-cols-5">
                <div className="rounded-md border border-hair bg-surface p-2.5 shadow-2xs">
                  <div className="text-ink-muted">成交总规模</div>
                  <div className="num mt-1 text-base font-bold text-ink">
                    {(tca.summary.total_volume / 10000).toFixed(1)} 万元
                  </div>
                  <div className="text-3xs text-ink-muted mt-0.5">{tca.total_fills} 笔子单撮合完成</div>
                </div>

                <div className="rounded-md border border-hair bg-surface p-2.5 shadow-2xs">
                  <div className="text-ink-muted">直接税费与佣金</div>
                  <div className="num mt-1 text-base font-bold text-ink">
                    {tca.summary.total_fee.toLocaleString()} 元
                  </div>
                  <div className="text-3xs text-ink-secondary mt-0.5">{tca.summary.fee_bps.toFixed(1)} bps</div>
                </div>

                <div className="rounded-md border border-hair bg-surface p-2.5 shadow-2xs">
                  <div className="text-ink-muted">市场冲击成本 (Impact)</div>
                  <div className="num mt-1 text-base font-bold text-danger">
                    {tca.summary.avg_impact_bps.toFixed(1)} bps
                  </div>
                  <div className="text-3xs text-ink-muted mt-0.5">平方根流动性冲击定律</div>
                </div>

                <div className="rounded-md border border-hair bg-surface p-2.5 shadow-2xs">
                  <div className="text-ink-muted">时机价格漂移 (Delay)</div>
                  <div
                    className={`num mt-1 text-base font-bold ${
                      tca.summary.delay_bps >= 0 ? 'text-danger' : 'text-success'
                    }`}
                  >
                    {tca.summary.delay_bps >= 0 ? '+' : ''}
                    {tca.summary.delay_bps.toFixed(1)} bps
                  </div>
                  <div className="text-3xs text-ink-muted mt-0.5">执行日开盘价基准漂移</div>
                </div>

                <div className="rounded-md border border-hair bg-surface p-2.5 shadow-2xs">
                  <div className="text-ink-muted">Perold 总执行落差</div>
                  <div
                    className={`num mt-1 text-base font-bold ${
                      tca.summary.total_shortfall_bps >= 0 ? 'text-danger' : 'text-success'
                    }`}
                  >
                    {tca.summary.total_shortfall_bps >= 0 ? '+' : ''}
                    {tca.summary.total_shortfall_bps.toFixed(1)} bps
                  </div>
                  <div className="text-3xs text-ink-muted mt-0.5">落差 = 佣金 + 冲击 + 延迟</div>
                </div>
              </div>

              {/* 成本落差瀑布图 + 逐日净值曲线 */}
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
                <div className="xl:col-span-6">
                  <SectionCard title="Perold 实现落差瀑布图 (Implementation Shortfall Waterfall)">
                    <div ref={waterfallRef} className="h-56 w-full" />
                    <p className="text-3xs text-ink-muted mt-1">
                      💡 <b>瀑布图归因</b>：直接佣金与税费作为固定摩擦支出；执行日开盘漂移体现信号衰减；流动性冲击反映单笔资金对买一/卖一档位的冲击成本。
                    </p>
                  </SectionCard>
                </div>

                <div className="xl:col-span-6">
                  <SectionCard title="模拟实盘逐日净值重建曲线 (Equity Curve)">
                    {account && account.nav_series.length > 1 ? (
                      <div ref={navRef} className="h-56 w-full" />
                    ) : (
                      <div className="flex h-56 items-center justify-center text-xs text-ink-muted">
                        净值序列样本积累中（首笔成交后按真实交易日画线）
                      </div>
                    )}
                    <div className="mt-1 flex justify-between text-3xs text-ink-muted">
                      <span>年化收益: {account?.annualized_return != null ? `${(account.annualized_return * 100).toFixed(2)}%` : '—'}</span>
                      <span>最大回撤: {account?.max_drawdown != null ? `${(account.max_drawdown * 100).toFixed(2)}%` : '—'}</span>
                      <span>夏普比率: {account?.sharpe != null ? account.sharpe.toFixed(2) : '—'}</span>
                    </div>
                  </SectionCard>
                </div>
              </div>

              {/* 算法执行质量横向评测 */}
              {tca.algo_breakdown.length > 0 && (
                <SectionCard title="算法分批执行质量对比 (Market vs VWAP vs TWAP vs POV)">
                  <div className="overflow-x-auto">
                    <table className="w-full text-2xs">
                      <thead>
                        <tr className="border-b border-hair text-ink-secondary">
                          <th className="py-1 text-left font-medium">执行算法</th>
                          <th className="py-1 text-center font-medium">成交笔数</th>
                          <th className="py-1 text-right font-medium">成交金额</th>
                          <th className="py-1 text-right font-medium">平均冲击 (bps)</th>
                          <th className="py-1 text-right font-medium">平均决策基差 (bps)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tca.algo_breakdown.map((ab) => (
                          <tr key={ab.algo} className="border-b border-hair">
                            <td className="py-1.5 font-mono uppercase text-ink font-bold">{ab.algo}</td>
                            <td className="num py-1.5 text-center">{ab.fill_count}</td>
                            <td className="num py-1.5 text-right">{ab.volume.toLocaleString()} 元</td>
                            <td className="num py-1.5 text-right text-danger">{ab.avg_impact_bps.toFixed(1)}</td>
                            <td
                              className={`num py-1.5 text-right font-semibold ${
                                ab.avg_basis_bps >= 0 ? 'text-danger' : 'text-success'
                              }`}
                            >
                              {ab.avg_basis_bps >= 0 ? '+' : ''}
                              {ab.avg_basis_bps.toFixed(1)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </SectionCard>
              )}

              {/* 最近撮合流水明细 */}
              {tca.recent_executions.length > 0 && (
                <SectionCard title={`最近撮合成交冲击与基差流水 (最近 ${tca.recent_executions.length} 笔)`}>
                  <div className="max-h-52 overflow-auto">
                    <table className="w-full text-2xs">
                      <thead className="sticky top-0 bg-surface">
                        <tr className="border-b border-hair text-ink-secondary">
                          <th className="py-1 text-left font-medium">执行日期</th>
                          <th className="py-1 text-left font-medium">母单#</th>
                          <th className="py-1 text-left font-medium">标的代码</th>
                          <th className="py-1 text-center font-medium">方向</th>
                          <th className="py-1 text-right font-medium">成交价</th>
                          <th className="py-1 text-right font-medium">数量</th>
                          <th className="py-1 text-right font-medium">成交额</th>
                          <th className="py-1 text-right font-medium">手续费</th>
                          <th className="py-1 text-right font-medium">冲击 (bps)</th>
                          <th className="py-1 text-right font-medium">基差 (bps)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tca.recent_executions.map((e) => (
                          <tr key={e.id} className="border-b border-hair hover:bg-surface-alt">
                            <td className="py-1 font-mono text-ink-secondary">{e.exec_date}</td>
                            <td className="py-1 text-ink-muted">#{e.order_id}</td>
                            <td className="py-1 font-mono font-medium text-ink">{e.symbol}</td>
                            <td className={`py-1 text-center font-medium ${e.side === 'buy' ? 'text-danger' : 'text-success'}`}>
                              {e.side === 'buy' ? '买入' : '卖出'}
                            </td>
                            <td className="num py-1 text-right">{e.price.toFixed(2)}</td>
                            <td className="num py-1 text-right">{e.qty}</td>
                            <td className="num py-1 text-right">{e.amount.toLocaleString()}</td>
                            <td className="num py-1 text-right text-ink-secondary">{e.fee.toFixed(1)}</td>
                            <td className="num py-1 text-right text-danger">{e.impact_bps.toFixed(1)}</td>
                            <td
                              className={`num py-1 text-right font-semibold ${
                                e.basis_bps >= 0 ? 'text-danger' : 'text-success'
                              }`}
                            >
                              {e.basis_bps >= 0 ? '+' : ''}
                              {e.basis_bps.toFixed(1)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </SectionCard>
              )}
            </>
          ) : (
            <div className="rounded-lg border border-hair bg-surface p-12 text-center text-xs text-ink-muted">
              暂无真实成交数据。请在【交易执行大厅】提交母单并点击顶部「撮合到期子单」，引擎撮合成交后将自动生成完整的 Perold 实现落差 4 维瀑布图归因。
            </div>
          )}
        </div>
      )}

      {/* ==================== 确认一键熔断 Modal ==================== */}
      {confirmKill && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setConfirmKill(false)}
        >
          <div className="w-84 rounded-lg bg-surface p-4 shadow-xl border border-hair" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 text-sm font-bold text-danger">⚠️ 确认激活 L2 全局硬熔断？</div>
            <p className="mb-3 text-2xs text-ink-secondary leading-relaxed">
              系统将立即撤销全部在途挂单（共 <span className="font-bold text-danger">{riskStatus?.pending_orders ?? 0}</span> 笔未完成母单），并锁定交易台拒绝任何新订单写入。
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmKill(false)}
                className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:bg-surface-alt"
              >
                取消
              </button>
              <button
                onClick={() => void toggleKill(true)}
                className="rounded-md bg-danger px-3 py-1.5 text-xs font-semibold text-white hover:bg-danger/90"
              >
                确认熔断
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ==================== 确认紧急清盘 Modal ==================== */}
      {confirmLiquidate && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setConfirmLiquidate(false)}
        >
          <div className="w-84 rounded-lg bg-surface p-4 shadow-xl border border-danger/40" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 text-sm font-bold text-danger">🚨 确认执行 L3 一键紧急清盘？</div>
            <p className="mb-3 text-2xs text-ink-secondary leading-relaxed">
              此操作将撤销全部在途母单，并对当前所有持仓标的下达<b>市价清仓单</b>并立刻撮合成交，将全部多头资产变现为现金。请确认操作意图！
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmLiquidate(false)}
                className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:bg-surface-alt"
              >
                取消
              </button>
              <button
                onClick={() => void handleLiquidateAll()}
                className="rounded-md bg-danger px-3 py-1.5 text-xs font-bold text-white hover:bg-danger/90"
              >
                确认清盘
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
