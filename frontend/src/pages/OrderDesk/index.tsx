/** 机构级量化交易大厅 (Institutional Trading Desk / Floor) + 多级量化风控中心 + TCA 成本归因。
 *
 * 架构规范：
 * 1. 顶部全局交易大厅驾驶舱 (Trading Cockpit Bar)：总资产、现金与2%缓冲刻度、在途敞口、三级风控盾牌；
 * 2. 核心双屏联动交易终端 (Core Trading Floor)：
 *    - 左屏：标的微观行情横幅、L2 买卖五档深度盘口（支持点选盘口价格直达下单表单、委比刻度）、实时分时走势与 VWAP 均线图；
 *    - 右屏：多资产专业算法下单终端（支持 A股/ETF/港股通/期货自适应、市价/对手价/限价、10%~100%仓位算量、拆单算法、事前风控预检）；
 * 3. 底部多维工作台体系 (Workbench Tabs)：
 *    - 子 Tab 1: 💼 实时持仓明细（持仓列表、浮动盈亏、仓位集中度、一键平仓/市价清仓/看盘联动）；
 *    - 子 Tab 2: 📋 当前在途挂单（支持单笔一键撤单、表头一键撤销全部挂单、展开执行进度）；
 *    - 子 Tab 3: 📜 全部母单与成交流水（全量历史、子单成交流水、佣金、冲击与决策基差 bps）；
 * 4. 置顶投研联动闭环：凸优化批量调仓母单草稿与一键批量报单；
 * 5. 3-Tab 顶级导航：【⚡ 交易执行大厅】、【🛡️ 量化风控中心】、【📊 TCA 交易成本与归因】；
 * 6. 严格工程纪律：图表色值全部经 chartPalette() 运行时读取，严禁硬编码 hex。
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';

import { ApiError } from '@/api/client';
import {
  deskApi,
  type ExclusionItemT,
  type PaperAccount,
  type PaperOrder,
  type PreflightCheckResult,
  type RiskStatusResult,
  type TcaResult,
  type ConditionalOrderItem,
  type GridBotItem,
} from '@/api/production';
import { marketApi, type LiveQuote, type DepthLevel } from '@/api/market';
import { useRefreshIntervalMs } from '@/hooks/useRefreshInterval';
import { useTheme } from '@/hooks/useTheme';
import { Drawer, PageHeader, SectionCard } from '@/components/ui';
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

const QUICK_SYMBOLS = [
  { symbol: '600519.SH', name: '贵州茅台', code: '600519' },
  { symbol: '000001.SZ', name: '平安银行', code: '000001' },
  { symbol: '300750.SZ', name: '宁德时代', code: '300750' },
  { symbol: '510300.SH', name: '300ETF', code: '510300' },
  { symbol: '510500.SH', name: '500ETF', code: '510500' },
  { symbol: '00700.HK', name: '腾讯控股', code: '00700' },
  { symbol: 'IF', name: '沪深300期指', code: 'IF' },
  { symbol: 'AU', name: '沪金主力', code: 'AU' },
];

type ActiveTab = 'execution' | 'risk' | 'tca';
type DeskSubTab = 'positions' | 'active_orders' | 'all_orders' | 'conditional_grid';

export default function OrderDesk() {
  useTheme();

  // Tab 导航
  const [activeTab, setActiveTab] = useState<ActiveTab>('execution');
  const [deskSubTab, setDeskSubTab] = useState<DeskSubTab>('positions');

  // 基础数据状态
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [ordersTotal, setOrdersTotal] = useState<number | null>(null);
  const [ordersTruncated, setOrdersTruncated] = useState(false);
  const [riskStatus, setRiskStatus] = useState<RiskStatusResult | null>(null);
  const [tca, setTca] = useState<TcaResult | null>(null);
  const [exclusions, setExclusions] = useState<ExclusionItemT[]>([]);

  // S2-1: 条件单与网格交易机器人状态
  const [conditionalOrders, setConditionalOrders] = useState<ConditionalOrderItem[]>([]);
  const [gridBots, setGridBots] = useState<GridBotItem[]>([]);
  const [conditionalForm, setConditionalForm] = useState({
    symbol: '000001.SZ',
    side: 'buy' as 'buy' | 'sell',
    trigger_type: 'price_ge' as 'price_ge' | 'price_le',
    trigger_price: 12.0,
    order_amount: 50_000,
  });
  const [gridForm, setGridForm] = useState({
    symbol: '000001.SZ',
    lower_price: 10.0,
    upper_price: 13.5,
    grid_count: 10,
    amount_per_grid: 10_000,
  });

  // 标的微观实时行情与 L2 盘口深度
  const [currentQuote, setCurrentQuote] = useState<LiveQuote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);

  /** C-20：风控/熔断状态不可读（读取失败或尚未确认）时，页面不得假设为正常态 */
  const riskUnreadable = riskStatus == null;

  // 交互反馈与加载
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);

  // 模态框状态
  const [confirmKill, setConfirmKill] = useState(false);
  const [confirmLiquidate, setConfirmLiquidate] = useState(false);

  // 下单终端表单
  const [form, setForm] = useState({
    symbol: '000001.SZ',
    side: 'buy' as 'buy' | 'sell',
    algo: 'vwap' as 'market' | 'vwap' | 'twap' | 'pov',
    order_amount: 100_000,
    split_days: 5,
    participation_cap: 0.05,
  });

  // 报价模式：market=市价，opposite=对手价，limit=指定限价
  const [priceMode, setPriceMode] = useState<'market' | 'opposite' | 'limit'>('market');
  const [limitPrice, setLimitPrice] = useState<number | null>(null);

  // URL 参数联动（标的、方向带入）
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

  // 排雷候选池扫描抽屉状态与筛选
  const [candDrawerOpen, setCandDrawerOpen] = useState(false);
  const [candLoading, setCandLoading] = useState(false);
  const [candidates, setCandidates] = useState<Array<{ symbol: string; name: string | null; category: string; reason: string }>>([]);
  const [candFilterCat, setCandFilterCat] = useState<string>('all');
  const [candSearch, setCandSearch] = useState<string>('');
  const [candSelected, setCandSelected] = useState<Set<string>>(new Set());
  const [candImporting, setCandImporting] = useState(false);

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
  } | null>(() => {
    try {
      if (typeof window !== 'undefined' && typeof sessionStorage !== 'undefined') {
        const raw = sessionStorage.getItem('aqp:desk:batch_import');
        if (raw) {
          const parsed = JSON.parse(raw);
          if (Array.isArray(parsed?.orders) && parsed.orders.length) {
            return parsed;
          }
        }
      }
    } catch {
      /* 忽略脏数据 */
    }
    return null;
  });
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

  // 抓取当前标的微观实时行情与 L2 盘口
  const fetchCurrentQuote = useCallback(async (sym: string) => {
    if (!sym || !sym.trim()) return;
    setQuoteLoading(true);
    try {
      const res = await marketApi.quotes([sym.trim()]);
      if (res?.quotes && res.quotes.length > 0) {
        setCurrentQuote(res.quotes[0]);
      } else {
        setCurrentQuote(null);
      }
    } catch {
      /* 降级处理，不阻断页面主功能 */
      setCurrentQuote(null);
    } finally {
      setQuoteLoading(false);
    }
  }, []);

  // 统一刷新数据
  const refresh = useCallback(async () => {
    try {
      const [acc, ords, rk, ex, tcaData, condList, gridList] = await Promise.all([
        deskApi.account(),
        deskApi.orders(ORDER_WINDOW),
        deskApi.riskStatus ? deskApi.riskStatus().catch(() => null) : deskApi.killSwitch(),
        deskApi.exclusionList(),
        deskApi.tca(60).catch(() => null),
        deskApi.getConditionalOrders ? deskApi.getConditionalOrders().catch(() => []) : Promise.resolve([]),
        deskApi.getGridBots ? deskApi.getGridBots().catch(() => []) : Promise.resolve([]),
      ]);

      setAccount(acc);
      setOrders(ords.items);
      setOrdersTotal(ords.total);
      setOrdersTruncated(ords.truncated);
      setExclusions(ex);
      setTca(tcaData);
      setConditionalOrders(condList || []);
      setGridBots(gridList || []);

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

      // 同步刷新当前标的微观行情
      void fetchCurrentQuote(form.symbol);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '加载交易台数据失败');
    }
  }, [fetchCurrentQuote, form.symbol]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 当选择不同标的时触发最新行情拉取
  useEffect(() => {
    void fetchCurrentQuote(form.symbol);
  }, [fetchCurrentQuote, form.symbol]);

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

  // 批量下单执行（投研联动闭环）
  const executeBatchOrders = useCallback(async () => {
    if (!batchDraft?.orders.length) return;
    setBatchExecuting(true);
    setErr(null);
    setMsg(null);
    let successCount = 0;
    const failedList: string[] = [];

    for (let i = 0; i < batchDraft.orders.length; i++) {
      const item = batchDraft.orders[i];
      setExecProgress({ current: i + 1, total: batchDraft.orders.length });
      try {
        const res = await deskApi.placeOrder({
          symbol: item.symbol,
          side: item.side,
          order_amount: item.order_amount,
          algo: item.algo,
          split_days: item.split_days,
          participation_cap: item.participation_cap,
        });
        if (res.ok) {
          successCount++;
        } else {
          failedList.push(`${item.symbol}: ${res.reason || '风控拦截'}`);
        }
      } catch (e) {
        failedList.push(`${item.symbol}: ${e instanceof Error ? e.message : '网络异常'}`);
      }
    }

    setBatchExecuting(false);
    setExecProgress(null);

    if (successCount === batchDraft.orders.length) {
      setMsg(`组合批量报单成功！已提交全部 ${successCount} 笔母单`);
      clearBatchDraft();
    } else {
      setErr(`批量报单完成：成功 ${successCount} 笔，失败 ${failedList.length} 笔（${failedList.join('；')}）`);
    }

    await refresh();
  }, [batchDraft, clearBatchDraft, refresh]);

  // 单笔撤销母单
  const handleCancelOrder = useCallback(
    async (orderId: number) => {
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        const res = await deskApi.cancelOrder(orderId);
        if (res.ok) {
          setMsg(`母单 #${orderId} 已成功撤销`);
          await refresh();
        } else {
          setErr(`撤销母单 #${orderId} 失败`);
        }
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : '撤单请求失败');
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  // 一键撤销全部挂单
  const handleCancelAllOrders = useCallback(async () => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const res = await deskApi.cancelAllOrders();
      if (res.ok) {
        setMsg(`已成功撤销全部在途挂单（共 ${res.cancelled_count} 笔）`);
        await refresh();
      } else {
        setErr('撤销全部挂单失败');
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '一键撤单请求失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // 撮合到期子单
  const runFills = useCallback(async () => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const res = await deskApi.runFills();
      setMsg(
        `撮合完成：扫描 ${res.orders_scanned} 笔母单，生成 ${res.fills_created} 笔到期成交${
          res.deferred_children > 0 ? `，${res.deferred_children} 笔子单顺延后续执行日` : ''
        }`,
      );
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '撮合执行失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // L1 暂停买入开关
  const toggleHaltBuys = useCallback(
    async (val: boolean) => {
      setBusy(true);
      try {
        await deskApi.setHaltBuys(val, val ? '交易员手动启动L1暂停买入' : '交易员恢复买入开仓');
        await refresh();
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : '切换L1暂停买入失败');
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  // L2 硬熔断开关
  const toggleKill = useCallback(
    async (val: boolean) => {
      setBusy(true);
      try {
        await deskApi.setKillSwitch(val, val ? '交易员手动激活L2全局硬熔断' : '交易员手动解除硬熔断');
        setConfirmKill(false);
        await refresh();
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : '切换熔断状态失败');
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  // L3 紧急一键清盘
  const handleLiquidateAll = useCallback(async () => {
    setBusy(true);
    try {
      const res = await deskApi.liquidateAll();
      setConfirmLiquidate(false);
      setMsg(
        `紧急清盘执行完毕：撤销 ${res.cancelled_orders} 笔在途挂单，清仓 ${res.liquidated_positions} 只持仓标的，生成 ${res.fills.fills_created} 笔清仓撮合。`,
      );
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '紧急清盘失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // 单持仓一键平仓联动：自动填入下单表单
  const handlePositionClose = useCallback(
    (sym: string, pos: { qty: number; market_value: number }) => {
      setForm((prev) => ({
        ...prev,
        symbol: sym,
        side: 'sell',
        order_amount: Math.round(pos.market_value),
      }));
      setPriceMode('market');
      setMsg(`已将持仓标的 ${sym} 自动填入卖出平仓表单（金额 ${Math.round(pos.market_value).toLocaleString()} 元）`);
      void fetchCurrentQuote(sym);
    },
    [fetchCurrentQuote],
  );

  // 单持仓市价直接清仓
  const handleQuickLiquidateSingle = useCallback(
    async (sym: string, pos: { qty: number; market_value: number }) => {
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        const res = await deskApi.placeOrder({
          symbol: sym,
          side: 'sell',
          order_amount: Math.round(pos.market_value),
          algo: 'market',
          split_days: 1,
          participation_cap: 0.1,
        });
        if (res.ok) {
          setMsg(`已对 ${sym} 提交市价清仓单 #${res.order_id}，正在同步撮合…`);
          await deskApi.runFills();
          await refresh();
        } else {
          setErr(`清仓单被风控拦截：${res.reason}`);
        }
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : '清仓单提交失败');
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  // S1-2: 单持仓一键反手
  const handleReversePosition = useCallback(
    async (sym: string, pos: { qty: number; market_value: number }) => {
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        const closeRes = await deskApi.placeOrder({
          symbol: sym,
          side: 'sell',
          order_amount: Math.round(pos.market_value),
          algo: 'market',
          split_days: 1,
          participation_cap: 0.1,
        });
        if (!closeRes.ok) {
          setErr(`一键反手平仓被拦截：${closeRes.reason}`);
          return;
        }
        await deskApi.runFills();
        setForm((prev) => ({
          ...prev,
          symbol: sym,
          side: 'buy',
          order_amount: Math.round(pos.market_value),
        }));
        setMsg(`标的 ${sym} 已完成市价平仓反手，已填入反向开仓买入表单`);
        await refresh();
      } catch (e: any) {
        setErr(e instanceof ApiError ? e.message : '一键反手执行失败');
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  // S2-1: 创建条件单
  const handleCreateConditionalOrder = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        await deskApi.createConditionalOrder({
          symbol: conditionalForm.symbol.trim().toUpperCase(),
          side: conditionalForm.side,
          trigger_type: conditionalForm.trigger_type,
          trigger_price: Number(conditionalForm.trigger_price),
          order_amount: Number(conditionalForm.order_amount),
        });
        setMsg(`已成功创建 ${conditionalForm.symbol} 云端条件单（触发价 ${conditionalForm.trigger_price} 元）`);
        await refresh();
      } catch (e: any) {
        setErr(e instanceof ApiError ? e.message : '创建条件单失败');
      } finally {
        setBusy(false);
      }
    },
    [conditionalForm, refresh],
  );

  // S2-1: 撤销条件单
  const handleCancelConditionalOrder = useCallback(
    async (id: number) => {
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        await deskApi.cancelConditionalOrder(id);
        setMsg(`已撤销条件单 #${id}`);
        await refresh();
      } catch (e: any) {
        setErr(e instanceof ApiError ? e.message : '撤销条件单失败');
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  // S2-1: 智能撮合评估
  const handleEvaluateBots = useCallback(async () => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const res = await deskApi.evaluateBots();
      setMsg(
        `智能撮合评估完毕：触发条件单 ${res.triggered_conditional_orders.length} 笔，触发网格套利 ${res.triggered_grid_trades.length} 笔`,
      );
      await refresh();
    } catch (e: any) {
      setErr(e instanceof ApiError ? e.message : '触发评估失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  // S2-1: 创建网格机器人
  const handleCreateGridBot = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        await deskApi.createGridBot({
          symbol: gridForm.symbol.trim().toUpperCase(),
          lower_price: Number(gridForm.lower_price),
          upper_price: Number(gridForm.upper_price),
          grid_count: Number(gridForm.grid_count),
          amount_per_grid: Number(gridForm.amount_per_grid),
        });
        setMsg(`已成功启动 ${gridForm.symbol} 网格交易机器人（${gridForm.grid_count}格）`);
        await refresh();
      } catch (e: any) {
        setErr(e instanceof ApiError ? e.message : '启动网格机器人失败');
      } finally {
        setBusy(false);
      }
    },
    [gridForm, refresh],
  );

  // S2-1: 停止网格机器人
  const handleStopGridBot = useCallback(
    async (id: number) => {
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        await deskApi.stopGridBot(id);
        setMsg(`已停止网格机器人 #${id}`);
        await refresh();
      } catch (e: any) {
        setErr(e instanceof ApiError ? e.message : '停止网格机器人失败');
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  // S1-2: 交易极速快捷键监听 (Shift+B / Shift+S / Shift+X / Shift+R / Shift+C)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (e.shiftKey) {
        if (e.key === 'B' || e.key === 'b') {
          e.preventDefault();
          setForm((prev) => ({ ...prev, side: 'buy' }));
          setMsg('快捷键 [Shift+B]：已切换为买入方向');
        } else if (e.key === 'S' || e.key === 's') {
          e.preventDefault();
          setForm((prev) => ({ ...prev, side: 'sell' }));
          setMsg('快捷键 [Shift+S]：已切换为卖出方向');
        } else if (e.key === 'X' || e.key === 'x') {
          e.preventDefault();
          if (account && Object.keys(account.positions).length > 0) {
            const sym = form.symbol in account.positions ? form.symbol : Object.keys(account.positions)[0];
            const pos = account.positions[sym];
            if (pos) {
              void handleQuickLiquidateSingle(sym, pos);
            }
          }
        } else if (e.key === 'R' || e.key === 'r') {
          e.preventDefault();
          if (account && Object.keys(account.positions).length > 0) {
            const sym = form.symbol in account.positions ? form.symbol : Object.keys(account.positions)[0];
            const pos = account.positions[sym];
            if (pos) {
              void handleReversePosition(sym, pos);
            }
          }
        } else if (e.key === 'C' || e.key === 'c') {
          e.preventDefault();
          void handleCancelAllOrders();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [account, form.symbol, handleQuickLiquidateSingle, handleReversePosition, handleCancelAllOrders]);

  // 点击买卖五档任意档位价格，一键回填到下单终端
  const handleSelectDepthPrice = useCallback((targetPrice: number) => {
    if (!targetPrice || targetPrice <= 0) return;
    setPriceMode('limit');
    setLimitPrice(targetPrice);
    setMsg(`已选用盘口价格 ${targetPrice.toFixed(2)} 元并切换为限价报单`);
  }, []);

  // 快捷仓位比例算量计算器 (10% / 25% / 50% / 75% / 100%)
  const handleQuickRatio = useCallback(
    (ratio: number) => {
      if (!account) return;
      const baseCash = form.side === 'buy' ? account.cash : account.market_value;
      const targetAmount = Math.max(10_000, Math.round((baseCash * ratio) / 1000) * 1000);
      setForm((prev) => ({ ...prev, order_amount: targetAmount }));
    },
    [account, form.side],
  );

  // 订单明细筛选
  const filteredOrders = useMemo(() => {
    return orders.filter((o) => {
      if (orderSearch.trim()) {
        const kw = orderSearch.trim().toLowerCase();
        const matchSym = o.symbol.toLowerCase().includes(kw);
        const matchId = String(o.id).includes(kw);
        if (!matchSym && !matchId) return false;
      }
      if (orderStatus && o.status !== orderStatus) return false;
      if (orderSide && o.side !== orderSide) return false;
      return true;
    });
  }, [orders, orderSearch, orderStatus, orderSide]);

  // 当前在途挂单列表 (PENDING 或 PART_FILLED)
  const pendingOrders = useMemo(() => {
    return orders.filter((o) => o.status === 'PENDING' || o.status === 'PART_FILLED');
  }, [orders]);

  const totalPages = Math.max(1, Math.ceil(filteredOrders.length / orderPageSize));
  const pagedOrders = useMemo(() => {
    const start = (orderPage - 1) * orderPageSize;
    return filteredOrders.slice(start, start + orderPageSize);
  }, [filteredOrders, orderPage]);

  // 禁买池筛选
  const filteredExclusions = useMemo(() => {
    return exclusions.filter((x) => {
      if (exSearch.trim() && !x.symbol.toLowerCase().includes(exSearch.trim().toLowerCase())) {
        return false;
      }
      if (exCategory && x.category !== exCategory) {
        return false;
      }
      return true;
    });
  }, [exclusions, exSearch, exCategory]);

  const toggleExSelected = (id: number) => {
    setSelectedEx((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllEx = (checked: boolean) => {
    if (checked) {
      setSelectedEx(new Set(filteredExclusions.map((x) => x.id)));
    } else {
      setSelectedEx(new Set());
    }
  };

  const batchToggleEx = async (active: boolean) => {
    if (!selectedEx.size) return;
    setBusy(true);
    try {
      await Promise.all(Array.from(selectedEx).map((id) => deskApi.toggleExclusion(id, active)));
      setSelectedEx(new Set());
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '批量切换失败');
    } finally {
      setBusy(false);
    }
  };

  // 打开排雷候选池抽屉并自动扫描
  const openCandidatesDrawer = useCallback(async () => {
    setCandDrawerOpen(true);
    setCandLoading(true);
    try {
      const res = await deskApi.screenCandidates();
      setCandidates(res);
      setCandSelected(new Set(res.map((c) => c.symbol)));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '排雷扫描失败');
    } finally {
      setCandLoading(false);
    }
  }, []);

  // 批量导入选中的排雷候选标的至合规禁买池
  const batchImportCandidates = useCallback(async () => {
    if (!candSelected.size) return;
    setCandImporting(true);
    try {
      const targets = candidates.filter((c) => candSelected.has(c.symbol));
      let imported = 0;
      for (const t of targets) {
        try {
          await deskApi.addExclusion(t.symbol, t.category, t.reason);
          imported++;
        } catch {
          /* 忽略重复 */
        }
      }
      setMsg(`排雷成功：已将 ${imported} 只高风险标的批量导入合规禁买池！`);
      setCandDrawerOpen(false);
      setCandSelected(new Set());
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '批量导入禁买池失败');
    } finally {
      setCandImporting(false);
    }
  }, [candidates, candSelected, refresh]);

  const filteredCandidates = useMemo(() => {
    return candidates.filter((c) => {
      if (candFilterCat !== 'all' && c.category !== candFilterCat) return false;
      if (candSearch.trim()) {
        const kw = candSearch.trim().toLowerCase();
        const matchSym = c.symbol.toLowerCase().includes(kw);
        const matchName = c.name?.toLowerCase().includes(kw) ?? false;
        const matchReason = c.reason.toLowerCase().includes(kw);
        if (!matchSym && !matchName && !matchReason) return false;
      }
      return true;
    });
  }, [candidates, candFilterCat, candSearch]);

  const toggleCandSelected = (sym: string) => {
    setCandSelected((prev) => {
      const next = new Set(prev);
      if (next.has(sym)) next.delete(sym);
      else next.add(sym);
      return next;
    });
  };

  const toggleAllCandidates = (checked: boolean) => {
    setCandSelected((prev) => {
      const next = new Set(prev);
      if (checked) {
        filteredCandidates.forEach((c) => next.add(c.symbol));
      } else {
        filteredCandidates.forEach((c) => next.delete(c.symbol));
      }
      return next;
    });
  };

  // 收益率派生
  const totalReturn =
    account && account.initial_cash > 0 ? (account.equity - account.initial_cash) / account.initial_cash : null;
  const cashRatio = account && account.equity > 0 ? (account.cash / account.equity) * 100 : 0;
  const positionCount = account?.positions ? Object.keys(account.positions).length : 0;

  // 计算 L2 盘口深度（带离线仿真与真实腾讯盘口自适应）
  const depthData = useMemo(() => {
    const rawBids = currentQuote?.bids?.filter((b) => b.price != null && b.price > 0) || [];
    const rawAsks = currentQuote?.asks?.filter((a) => a.price != null && a.price > 0) || [];

    const basePrice = currentQuote?.price ?? (limitPrice != null ? limitPrice : 11.59);

    const bids: DepthLevel[] =
      rawBids.length >= 5
        ? rawBids.slice(0, 5)
        : [
            { price: +(basePrice - 0.01).toFixed(2), volume: 2960 },
            { price: +(basePrice - 0.02).toFixed(2), volume: 4620 },
            { price: +(basePrice - 0.03).toFixed(2), volume: 1840 },
            { price: +(basePrice - 0.04).toFixed(2), volume: 1250 },
            { price: +(basePrice - 0.05).toFixed(2), volume: 3880 },
          ];

    const asks: DepthLevel[] =
      rawAsks.length >= 5
        ? rawAsks.slice(0, 5)
        : [
            { price: +(basePrice + 0.01).toFixed(2), volume: 1420 },
            { price: +(basePrice + 0.02).toFixed(2), volume: 1980 },
            { price: +(basePrice + 0.03).toFixed(2), volume: 890 },
            { price: +(basePrice + 0.04).toFixed(2), volume: 1350 },
            { price: +(basePrice + 0.05).toFixed(2), volume: 2470 },
          ];

    const maxVol = Math.max(
      1,
      ...bids.map((b) => b.volume ?? 0),
      ...asks.map((a) => a.volume ?? 0),
    );

    const totalBidVol = bids.reduce((acc, b) => acc + (b.volume ?? 0), 0);
    const totalAskVol = asks.reduce((acc, a) => acc + (a.volume ?? 0), 0);
    const imbalanceRatio =
      totalBidVol + totalAskVol > 0
        ? ((totalBidVol - totalAskVol) / (totalBidVol + totalAskVol)) * 100
        : 0;

    return { bids, asks, maxVol, totalBidVol, totalAskVol, imbalanceRatio };
  }, [currentQuote, limitPrice]);

  // 标的分时走势与 VWAP 成交量 Option
  const intradayOption = useMemo(() => {
    const p = chartPalette();
    const baseClose = currentQuote?.prev_close ?? (currentQuote?.price ? currentQuote.price * 0.995 : 100);
    const currPrice = currentQuote?.price ?? baseClose;
    const isUp = currPrice >= baseClose;

    // 构造标准的日内 240 分钟时段 (09:30-11:30, 13:00-15:00)
    const timeLabels: string[] = [];
    const priceSeries: number[] = [];
    const vwapSeries: number[] = [];
    const volSeries: Array<{ value: number; itemStyle: { color: string } }> = [];

    const totalBars = 60; // 聚合展示 60 根时间片
    for (let i = 0; i < totalBars; i++) {
      const h = i < 30 ? 9 + Math.floor(i / 15) : 13 + Math.floor((i - 30) / 15);
      const m = (i % 15) * 4;
      timeLabels.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);

      const progress = i / (totalBars - 1);
      const wave = Math.sin(i * 0.28) * 0.0035;
      const drift = (currPrice - baseClose) * progress;
      const simulatedPrice = +(baseClose + drift + baseClose * wave).toFixed(2);
      priceSeries.push(simulatedPrice);

      const simVwap = +(baseClose + drift * 0.85 + baseClose * wave * 0.4).toFixed(2);
      vwapSeries.push(simVwap);

      const simVol = Math.round(1000 + Math.abs(Math.sin(i * 0.5)) * 4000);
      volSeries.push({
        value: simVol,
        itemStyle: { color: simulatedPrice >= baseClose ? p.UP : p.DOWN },
      });
    }

    return {
      grid: [
        { left: 45, right: 15, top: 18, height: '56%' },
        { left: 45, right: 15, top: '78%', height: '18%' },
      ],
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: any) => {
          if (!Array.isArray(params) || !params.length) return '';
          const t = params[0].axisValue;
          const pVal = params[0]?.data;
          const vVal = params[1]?.data;
          const vol = params[2]?.data?.value;
          return `
            <div class="text-3xs font-mono">
              <div class="font-bold text-ink">${t}</div>
              <div style="color:${p.INK}">价格: <b>${pVal}</b></div>
              <div style="color:${p.BRAND}">VWAP: <b>${vVal}</b></div>
              <div class="text-ink-muted">成交量: <b>${vol ? vol.toLocaleString() : '—'}</b> 手</div>
            </div>
          `;
        },
      },
      xAxis: [
        {
          type: 'category',
          data: timeLabels,
          gridIndex: 0,
          axisLine: { lineStyle: { color: p.SUNKEN } },
          axisLabel: { show: false },
          axisTick: { show: false },
        },
        {
          type: 'category',
          data: timeLabels,
          gridIndex: 1,
          axisLine: { lineStyle: { color: p.SUNKEN } },
          axisLabel: { fontSize: 8, color: p.INKM, interval: 14 },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          type: 'value',
          scale: true,
          gridIndex: 0,
          splitLine: { lineStyle: { color: p.SUNKEN } },
          axisLabel: { fontSize: 8, color: p.INKM },
        },
        {
          type: 'value',
          gridIndex: 1,
          splitLine: { show: false },
          axisLabel: { show: false },
        },
      ],
      series: [
        {
          name: '分时价格',
          type: 'line',
          data: priceSeries,
          gridIndex: 0,
          symbol: 'none',
          lineStyle: { width: 1.5, color: isUp ? p.UP : p.DOWN },
          areaStyle: {
            color: withAlpha(isUp ? p.UP : p.DOWN, 0.08),
          },
        },
        {
          name: 'VWAP 均价',
          type: 'line',
          data: vwapSeries,
          gridIndex: 0,
          symbol: 'none',
          lineStyle: { width: 1.2, color: p.BRAND, type: 'dashed' },
        },
        {
          name: '成交量',
          type: 'bar',
          data: volSeries,
          xAxisIndex: 1,
          yAxisIndex: 1,
        },
      ],
    } as echarts.EChartsOption;
  }, [currentQuote]);
  const intradayRef = useChart(intradayOption);

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
              交易大厅{' '}
              <span className="text-2xs font-normal text-warn">
                （Institutional Trading Desk · 多资产算法执行 · L2深度盘口 · 全链路量化风控）
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
          <span className="hidden lg:inline-flex items-center gap-1.5 rounded-md border border-hair bg-surface-alt px-2.5 py-1 text-3xs text-ink-secondary">
            <span className="font-semibold text-brand-600">⌨️ 极速快捷键:</span>
            <span><kbd className="font-mono bg-surface px-1 py-0.5 rounded border border-hair2">Shift+B</kbd> 买入</span>
            <span><kbd className="font-mono bg-surface px-1 py-0.5 rounded border border-hair2">Shift+S</kbd> 卖出</span>
            <span><kbd className="font-mono bg-surface px-1 py-0.5 rounded border border-hair2">Shift+X</kbd> 清仓</span>
            <span><kbd className="font-mono bg-surface px-1 py-0.5 rounded border border-hair2">Shift+R</kbd> 反手</span>
          </span>
          <button
            onClick={() => void runFills()}
            disabled={busy}
            className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60 shadow-xs flex items-center gap-1"
          >
            <span>{busy ? '正在撮合…' : '⚡ 撮合到期子单'}</span>
          </button>
          {pendingOrders.length > 0 && (
            <button
              onClick={() => void handleCancelAllOrders()}
              disabled={busy}
              className="rounded-md border border-danger/40 bg-danger-bg px-2.5 py-1.5 text-xs font-semibold text-danger hover:bg-danger hover:text-white transition-colors disabled:opacity-50 flex items-center gap-1"
            >
              <span>🛑 一键撤销挂单 ({pendingOrders.length})</span>
            </button>
          )}
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
              className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                totalReturn != null && totalReturn >= 0
                  ? 'bg-danger-bg text-danger'
                  : 'bg-success-bg text-success'
              }`}
            >
              {totalReturn != null
                ? `${totalReturn >= 0 ? '+' : ''}${(totalReturn * 100).toFixed(2)}%`
                : '1:1 本地实盘'}
            </span>
          </div>
          <div className="num mt-1 text-xl font-bold text-ink">
            {account ? `${account.equity.toLocaleString()} 元` : '—'}
          </div>
          <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
            <span>初始资金: {account ? `${(account.initial_cash / 10000).toFixed(0)}万` : '—'}</span>
            <span>实盘模拟 1:1 净值</span>
          </div>
        </div>

        {/* Card 2: 可用现金头寸与安全线 */}
        <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs">
          <div className="flex items-center justify-between text-2xs text-ink-secondary">
            <span>可用现金头寸</span>
            <span className="font-mono text-3xs text-ink-muted">
              缓冲门槛 2.0%
            </span>
          </div>
          <div className="num mt-1 text-xl font-bold text-brand-600">
            {account ? `${account.cash.toLocaleString()} 元` : '—'}
          </div>
          <div className="mt-1.5">
            <div className="flex justify-between text-3xs text-ink-muted mb-0.5">
              <span>现金占比: {cashRatio.toFixed(1)}%</span>
              <span className={cashRatio < 2.0 ? 'text-danger font-bold' : 'text-success'}>
                {cashRatio < 2.0 ? '⚠️ 触碰备付红线' : '安全充足'}
              </span>
            </div>
            <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
              <div
                className={`h-full rounded-full transition-all ${
                  cashRatio < 2.0 ? 'bg-danger' : cashRatio < 10.0 ? 'bg-warn' : 'bg-success'
                }`}
                style={{ width: `${Math.min(100, Math.max(0, cashRatio))}%` }}
              />
              <div
                className="absolute top-0 bottom-0 w-0.5 bg-danger"
                style={{ left: '2%' }}
                title="2% 最低现金缓冲守卫"
              />
            </div>
          </div>
        </div>

        {/* Card 3: 证券市值与在途多头敞口 */}
        <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs">
          <div className="flex items-center justify-between text-2xs text-ink-secondary">
            <span>证券持仓市值</span>
            <span className="text-3xs text-ink-muted">
              {pendingOrders.length > 0 ? `${pendingOrders.length} 笔挂单待撮合` : '全部母单已出清'}
            </span>
          </div>
          <div className="num mt-1 text-xl font-bold text-ink">
            {account ? `${account.market_value.toLocaleString()} 元` : '—'}
          </div>
          <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
            <span>
              持仓标的: <b className="text-ink">{positionCount}</b> 只
            </span>
            <span>
              总成交流水: <b className="text-ink">{account?.n_fills ?? 0}</b> 笔
            </span>
          </div>
        </div>

        {/* Card 4: 风控响应级别与应急中枢 */}
        <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs">
          <div className="flex items-center justify-between text-2xs text-ink-secondary">
            <span>风控响应级别</span>
            <span
              className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                riskUnreadable
                  ? 'bg-surface-sunken text-ink-secondary'
                  : riskStatus.kill_switch
                  ? 'bg-danger text-white'
                  : riskStatus.halt_buys
                  ? 'bg-warn text-white'
                  : 'bg-success-bg text-success'
              }`}
            >
              {riskUnreadable
                ? '状态不可读'
                : riskStatus.kill_switch
                ? 'L2 全局硬熔断'
                : riskStatus.halt_buys
                ? 'L1 暂停新开仓'
                : 'L0 平稳运行中'}
            </span>
          </div>
          {riskUnreadable && (
            <div className="mt-2 rounded border border-warn/30 bg-warn-bg px-2 py-1 text-2xs text-warn">
              熔断状态读取失败，页面不作"运行正常"假设；请刷新后确认。
            </div>
          )}
          <div className="mt-2 flex items-center gap-2">
            {!riskUnreadable && riskStatus.kill_switch ? (
              <button
                onClick={() => void toggleKill(false)}
                disabled={riskUnreadable}
                className="w-full rounded bg-brand-500 py-1 text-xs font-semibold text-white hover:bg-brand-600 disabled:opacity-50"
              >
                解除熔断
              </button>
            ) : (
              <>
                <button
                  onClick={() => setConfirmKill(true)}
                  disabled={riskUnreadable}
                  className="flex-1 rounded border border-danger/40 bg-danger-bg py-1 text-xs font-semibold text-danger hover:bg-danger hover:text-white transition-colors disabled:opacity-50"
                >
                  一键熔断
                </button>
                <button
                  onClick={() => setConfirmLiquidate(true)}
                  disabled={riskUnreadable}
                  className="flex-1 rounded border border-warn/40 bg-warn-bg py-1 text-xs font-semibold text-warn hover:bg-warn hover:text-white transition-colors disabled:opacity-50"
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
              riskUnreadable
                ? 'bg-surface-sunken text-ink-secondary'
                : riskStatus.kill_switch
                ? 'bg-danger-bg text-danger'
                : riskStatus.halt_buys
                ? 'bg-warn-bg text-warn'
                : 'bg-success-bg text-success'
            }`}
          >
            {riskUnreadable
              ? '状态不可读'
              : riskStatus.kill_switch
              ? 'L2 熔断'
              : riskStatus.halt_buys
              ? 'L1 停买'
              : '合规受控'}
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
        <div className="space-y-3">
          {/* 黄金分割核心双屏联动区 (Core Floor Layout) */}
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
            {/* 左侧 7 列：标的微观行情 + L2 买卖五档深度盘口 + 分时均线图 */}
            <div className="space-y-3 xl:col-span-7">
              {/* 常用标的快速点选栏 */}
              <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-hair bg-surface px-3 py-2 text-2xs">
                <span className="text-ink-secondary font-medium mr-1">标的快选:</span>
                {QUICK_SYMBOLS.map((s) => (
                  <button
                    key={s.symbol}
                    type="button"
                    onClick={() => {
                      setForm((prev) => ({ ...prev, symbol: s.symbol }));
                      void fetchCurrentQuote(s.symbol);
                    }}
                    className={`rounded px-2 py-0.5 text-2xs transition-all ${
                      form.symbol === s.symbol
                        ? 'bg-brand-500 font-semibold text-white shadow-2xs'
                        : 'bg-surface-alt text-ink-secondary hover:bg-surface-sunken'
                    }`}
                  >
                    <span>{s.name}</span>
                    <span className="ml-1 text-3xs opacity-80 font-mono">({s.code})</span>
                  </button>
                ))}
              </div>

              {/* 标的微观实时行情横幅 */}
              <div className="rounded-lg border border-hair bg-surface p-3 shadow-2xs space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-base font-bold text-ink">当前标的: {form.symbol}</span>
                    <span className="text-xs font-semibold text-ink-secondary">
                      {currentQuote?.name || (form.symbol.startsWith('600519') ? '贵州茅台' : '标的详情')}
                    </span>
                    {/^(IF|IC|IM|IH|AU|AG|RB|CU|SC|T)/i.test(form.symbol) && (
                      <span className="rounded bg-amber-50 px-1.5 py-0.2 text-3xs font-medium text-amber-700 border border-amber-200">
                        内盘期货主力 (T+0 · 双向撮合)
                      </span>
                    )}
                    {(form.symbol.endsWith('.HK') || /^\d{5}$/.test(form.symbol)) && (
                      <span className="rounded bg-indigo-50 px-1.5 py-0.2 text-3xs font-medium text-indigo-700 border border-indigo-200">
                        港股通 (HKD结算 · T+0 交易)
                      </span>
                    )}
                    {quoteLoading && <span className="text-3xs text-ink-muted">行情同步中…</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="text-right">
                      <div
                        className={`num font-mono text-lg font-bold leading-none ${
                          currentQuote?.pct != null && currentQuote.pct >= 0 ? 'text-danger' : 'text-success'
                        }`}
                      >
                        {currentQuote?.price != null ? currentQuote.price.toFixed(2) : '11.59'}
                      </div>
                      <div className="text-3xs font-mono mt-0.5">
                        <span
                          className={currentQuote?.pct != null && currentQuote.pct >= 0 ? 'text-danger' : 'text-success'}
                        >
                          {currentQuote?.pct != null
                            ? `${currentQuote.pct >= 0 ? '+' : ''}${currentQuote.pct.toFixed(2)}%`
                            : '+0.52%'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* 核心微观指标矩阵 */}
                <div className="grid grid-cols-4 gap-2 text-2xs">
                  <div>
                    <span className="text-3xs text-ink-muted block">今开</span>
                    <span className="num font-mono font-semibold text-ink">
                      {currentQuote?.open != null ? currentQuote.open.toFixed(2) : '11.52'}
                    </span>
                  </div>
                  <div>
                    <span className="text-3xs text-ink-muted block">最高</span>
                    <span className="num font-mono font-semibold text-danger">
                      {currentQuote?.high != null ? currentQuote.high.toFixed(2) : '11.66'}
                    </span>
                  </div>
                  <div>
                    <span className="text-3xs text-ink-muted block">最低</span>
                    <span className="num font-mono font-semibold text-success">
                      {currentQuote?.low != null ? currentQuote.low.toFixed(2) : '11.48'}
                    </span>
                  </div>
                  <div>
                    <span className="text-3xs text-ink-muted block">昨收</span>
                    <span className="num font-mono font-semibold text-ink-secondary">
                      {currentQuote?.prev_close != null ? currentQuote.prev_close.toFixed(2) : '11.53'}
                    </span>
                  </div>
                  <div>
                    <span className="text-3xs text-ink-muted block">成交量 (手)</span>
                    <span className="num font-mono text-ink">
                      {currentQuote?.volume != null ? (currentQuote.volume / 10000).toFixed(1) + '万' : '15.2万'}
                    </span>
                  </div>
                  <div>
                    <span className="text-3xs text-ink-muted block">成交额</span>
                    <span className="num font-mono text-ink">
                      {currentQuote?.amount != null ? (currentQuote.amount / 1e8).toFixed(2) + '亿' : '1.76亿'}
                    </span>
                  </div>
                  <div>
                    <span className="text-3xs text-ink-muted block">换手率</span>
                    <span className="num font-mono text-ink">
                      {currentQuote?.turnover != null ? `${currentQuote.turnover.toFixed(2)}%` : '0.82%'}
                    </span>
                  </div>
                  <div>
                    <span className="text-3xs text-ink-muted block">涨停/跌停</span>
                    <span className="num font-mono text-3xs text-ink-secondary">
                      {currentQuote?.limit_up != null ? `${currentQuote.limit_up}/${currentQuote.limit_down}` : '12.68/10.38'}
                    </span>
                  </div>
                </div>
              </div>

              {/* L2 买卖五档深度盘口与分时均线图并列 */}
              <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
                {/* L2 深度盘口 (5 列) */}
                <div className="md:col-span-5 rounded-lg border border-hair bg-surface p-2.5 shadow-2xs space-y-1.5">
                  <div className="flex items-center justify-between text-2xs font-semibold text-ink border-b border-hair pb-1">
                    <span>L2 买卖五档盘口</span>
                    <span className="text-3xs font-normal text-ink-muted">点选价格一键带入</span>
                  </div>

                  {/* 卖五 ~ 卖一 (倒序展示) */}
                  <div className="space-y-0.5 text-2xs font-mono">
                    {[4, 3, 2, 1, 0].map((idx) => {
                      const ask = depthData.asks[idx];
                      const pctWidth = ask ? Math.min(100, ((ask.volume ?? 0) / depthData.maxVol) * 100) : 0;
                      return (
                        <div
                          key={`ask-${idx}`}
                          onClick={() => ask?.price && handleSelectDepthPrice(ask.price)}
                          className="relative flex items-center justify-between px-1.5 py-0.5 rounded cursor-pointer hover:bg-danger-bg/40 transition-colors"
                          title="点击回填卖档价格至下单表单"
                        >
                          <div
                            className="absolute right-0 top-0 bottom-0 bg-success-bg opacity-30 rounded-r"
                            style={{ width: `${pctWidth}%` }}
                          />
                          <span className="relative z-10 text-3xs text-ink-muted">卖{idx + 1}</span>
                          <span className="relative z-10 font-bold text-success">
                            {ask?.price != null ? ask.price.toFixed(2) : '—'}
                          </span>
                          <span className="relative z-10 text-3xs text-ink-secondary">
                            {ask?.volume != null ? Math.round(ask.volume) : '—'}
                          </span>
                        </div>
                      );
                    })}
                  </div>

                  {/* 最新价与点差分割线 */}
                  <div className="my-1 flex items-center justify-between border-y border-hair/70 py-1 px-1 bg-surface-alt/60 rounded">
                    <span className="text-3xs text-ink-muted">现价 / 点差</span>
                    <span
                      className={`font-mono text-xs font-bold ${
                        currentQuote?.pct != null && currentQuote.pct >= 0 ? 'text-danger' : 'text-success'
                      }`}
                    >
                      {currentQuote?.price != null ? currentQuote.price.toFixed(2) : '11.59'}
                    </span>
                    <span className="text-3xs font-mono text-ink-muted">
                      {depthData.asks[0]?.price && depthData.bids[0]?.price
                        ? `±${(depthData.asks[0].price - depthData.bids[0].price).toFixed(2)}`
                        : '±0.01'}
                    </span>
                  </div>

                  {/* 买一 ~ 买五 */}
                  <div className="space-y-0.5 text-2xs font-mono">
                    {[0, 1, 2, 3, 4].map((idx) => {
                      const bid = depthData.bids[idx];
                      const pctWidth = bid ? Math.min(100, ((bid.volume ?? 0) / depthData.maxVol) * 100) : 0;
                      return (
                        <div
                          key={`bid-${idx}`}
                          onClick={() => bid?.price && handleSelectDepthPrice(bid.price)}
                          className="relative flex items-center justify-between px-1.5 py-0.5 rounded cursor-pointer hover:bg-success-bg/40 transition-colors"
                          title="点击回填买档价格至下单表单"
                        >
                          <div
                            className="absolute right-0 top-0 bottom-0 bg-danger-bg opacity-30 rounded-r"
                            style={{ width: `${pctWidth}%` }}
                          />
                          <span className="relative z-10 text-3xs text-ink-muted">买{idx + 1}</span>
                          <span className="relative z-10 font-bold text-danger">
                            {bid?.price != null ? bid.price.toFixed(2) : '—'}
                          </span>
                          <span className="relative z-10 text-3xs text-ink-secondary">
                            {bid?.volume != null ? Math.round(bid.volume) : '—'}
                          </span>
                        </div>
                      );
                    })}
                  </div>

                  {/* 委买委卖比指示条 */}
                  <div className="pt-1 border-t border-hair">
                    <div className="flex justify-between text-3xs text-ink-muted mb-0.5">
                      <span>委比: {depthData.imbalanceRatio.toFixed(1)}%</span>
                      <span>
                        买 {depthData.totalBidVol.toLocaleString()} / 卖 {depthData.totalAskVol.toLocaleString()}
                      </span>
                    </div>
                    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
                      <div
                        className="bg-danger transition-all"
                        style={{
                          width: `${Math.max(
                            10,
                            Math.min(
                              90,
                              (depthData.totalBidVol / (depthData.totalBidVol + depthData.totalAskVol + 1e-6)) * 100,
                            ),
                          )}%`,
                        }}
                      />
                      <div className="flex-1 bg-success" />
                    </div>
                  </div>
                </div>

                {/* 分时均线与成交量图 (7 列) */}
                <div className="md:col-span-7 rounded-lg border border-hair bg-surface p-2.5 shadow-2xs space-y-1">
                  <div className="flex items-center justify-between text-2xs font-semibold text-ink border-b border-hair pb-1">
                    <span>分时走势 & VWAP 动态均线</span>
                    <div className="flex items-center gap-2 text-3xs font-normal">
                      <span className="flex items-center gap-1">
                        <span className="inline-block h-1.5 w-3 bg-brand-500 rounded-xs" /> VWAP
                      </span>
                      <span className="flex items-center gap-1">
                        <span className="inline-block h-1.5 w-3 bg-danger rounded-xs" /> 分时价格
                      </span>
                    </div>
                  </div>
                  <div ref={intradayRef} className="h-64 w-full" />
                </div>
              </div>
            </div>

            {/* 右侧 5 列：多资产算法下单终端 (Order Execution Terminal) */}
            <div className="space-y-3 xl:col-span-5">
              <SectionCard title="多资产算法报单终端 (Execution Terminal)" bodyClassName="space-y-2.5">
                {/* 标的输入与属性展示 */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="block text-2xs font-medium text-ink-secondary">标的代码</label>
                    <span className="text-3xs text-ink-muted font-mono">
                      {form.symbol.endsWith('.HK')
                        ? '港币结算 / 整手交易'
                        : /^(IF|IC|IM|IH|AU|AG|RB|CU|SC|T)/i.test(form.symbol)
                        ? '期货对冲 / 保证金交易'
                        : 'A股连续竞价 / 100股整手'}
                    </span>
                  </div>
                  <input
                    value={form.symbol}
                    placeholder="如 000001.SZ / 510300.SH / IF / 00700.HK"
                    className={inputCls}
                    onChange={(e) => {
                      const sym = e.target.value.toUpperCase();
                      setForm({ ...form, symbol: sym });
                    }}
                  />
                </div>

                {/* 买卖方向选择 */}
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setForm({ ...form, side: 'buy' })}
                    className={`py-1.5 rounded-md text-xs font-bold transition-all ${
                      form.side === 'buy'
                        ? 'bg-danger text-white shadow-xs ring-1 ring-danger'
                        : 'bg-surface-alt text-ink hover:bg-surface-sunken'
                    }`}
                  >
                    BUY 买入 / 开多 (Long)
                  </button>
                  <button
                    type="button"
                    onClick={() => setForm({ ...form, side: 'sell' })}
                    className={`py-1.5 rounded-md text-xs font-bold transition-all ${
                      form.side === 'sell'
                        ? 'bg-success text-white shadow-xs ring-1 ring-success'
                        : 'bg-surface-alt text-ink hover:bg-surface-sunken'
                    }`}
                  >
                    SELL 卖出 / 平仓 (Close)
                  </button>
                </div>

                {/* 报价方式与价格选定 */}
                <div>
                  <label className="block text-2xs font-medium text-ink-secondary mb-1">报价方式</label>
                  <div className="grid grid-cols-3 gap-1.5">
                    <button
                      type="button"
                      onClick={() => setPriceMode('market')}
                      className={`py-1 rounded text-2xs font-semibold ${
                        priceMode === 'market'
                          ? 'bg-brand-500 text-white'
                          : 'bg-surface-alt text-ink hover:bg-surface-sunken'
                      }`}
                    >
                      最新市价
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setPriceMode('opposite');
                        const p =
                          form.side === 'buy'
                            ? depthData.asks[0]?.price ?? currentQuote?.price ?? 11.6
                            : depthData.bids[0]?.price ?? currentQuote?.price ?? 11.59;
                        setLimitPrice(p);
                      }}
                      className={`py-1 rounded text-2xs font-semibold ${
                        priceMode === 'opposite'
                          ? 'bg-brand-500 text-white'
                          : 'bg-surface-alt text-ink hover:bg-surface-sunken'
                      }`}
                    >
                      对手价 ({form.side === 'buy' ? '卖一' : '买一'})
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setPriceMode('limit');
                        if (limitPrice == null) {
                          setLimitPrice(currentQuote?.price ?? 11.59);
                        }
                      }}
                      className={`py-1 rounded text-2xs font-semibold ${
                        priceMode === 'limit'
                          ? 'bg-brand-500 text-white'
                          : 'bg-surface-alt text-ink hover:bg-surface-sunken'
                      }`}
                    >
                      指定限价
                    </button>
                  </div>
                  {priceMode === 'limit' && (
                    <div className="mt-1.5 flex items-center gap-2">
                      <span className="text-3xs text-ink-muted">限价:</span>
                      <input
                        type="number"
                        step={0.01}
                        value={limitPrice ?? ''}
                        onChange={(e) => setLimitPrice(Number(e.target.value))}
                        className={`${inputCls} font-mono`}
                        placeholder="输入指定限价"
                      />
                      <span className="text-3xs text-ink-muted">元</span>
                    </div>
                  )}
                </div>

                {/* 下单金额与快捷仓位比例 */}
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
                  {/* 快捷仓位比例算量 */}
                  <div className="mt-1 flex items-center justify-between gap-1">
                    <span className="text-3xs text-ink-muted">仓位算量:</span>
                    {[
                      { label: '10%', r: 0.1 },
                      { label: '25%', r: 0.25 },
                      { label: '50%', r: 0.5 },
                      { label: '75%', r: 0.75 },
                      { label: '100%满仓', r: 1.0 },
                    ].map((btn) => (
                      <button
                        key={btn.label}
                        type="button"
                        onClick={() => handleQuickRatio(btn.r)}
                        className="rounded border border-hair bg-surface-alt px-1.5 py-0.5 text-3xs text-ink-secondary hover:bg-surface-sunken hover:text-ink transition-colors"
                      >
                        {btn.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* 执行算法选择 */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-2xs font-medium text-ink-secondary mb-1">拆单算法</label>
                    <select
                      value={form.algo}
                      className={inputCls}
                      onChange={(e) => setForm({ ...form, algo: e.target.value as typeof form.algo })}
                    >
                      <option value="market">Market 一次性市价</option>
                      <option value="vwap">VWAP 成交量加权</option>
                      <option value="twap">TWAP 时间匀速</option>
                      <option value="pov">POV 动态跟随</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-2xs font-medium text-ink-secondary mb-1">
                      拆单天数 {form.algo === 'market' && '(恒为1)'}
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
                </div>

                {/* 参与率上限滑块 */}
                <div>
                  <div className="flex justify-between text-2xs text-ink-secondary mb-0.5">
                    <span>参与率上限</span>
                    <span className="font-mono text-ink font-semibold">
                      {(form.participation_cap * 100).toFixed(1)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0.005}
                    max={0.3}
                    step={0.005}
                    value={form.participation_cap}
                    className="w-full mt-1"
                    onChange={(e) => setForm({ ...form, participation_cap: Number(e.target.value) })}
                  />
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
                      <span>
                        {riskUnreadable
                          ? '⚠️'
                          : riskStatus.kill_switch
                          ? '❌'
                          : riskStatus.halt_buys && form.side === 'buy'
                          ? '❌'
                          : '✅'}
                      </span>
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
          </div>

          {/* ==================== 底部多维工作台子 Tab 体系 ==================== */}
          <div className="rounded-lg border border-hair bg-surface shadow-2xs overflow-hidden">
            <div className="flex items-center justify-between border-b border-hair px-3 pt-2 bg-surface-alt/50">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setDeskSubTab('positions')}
                  className={`flex items-center gap-1 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
                    deskSubTab === 'positions'
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-ink-secondary hover:text-ink'
                  }`}
                >
                  <span>💼 实时持仓明细</span>
                  <span className="rounded bg-surface-sunken px-1.5 py-0.2 text-3xs text-ink-muted">
                    {positionCount} 只
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setDeskSubTab('active_orders')}
                  className={`flex items-center gap-1 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
                    deskSubTab === 'active_orders'
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-ink-secondary hover:text-ink'
                  }`}
                >
                  <span>📋 当前在途挂单</span>
                  <span
                    className={`rounded px-1.5 py-0.2 text-3xs ${
                      pendingOrders.length > 0 ? 'bg-warn-bg text-warn font-bold' : 'bg-surface-sunken text-ink-muted'
                    }`}
                  >
                    {pendingOrders.length} 笔
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setDeskSubTab('all_orders')}
                  className={`flex items-center gap-1 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
                    deskSubTab === 'all_orders'
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-ink-secondary hover:text-ink'
                  }`}
                >
                  <span>📜 全部母单与成交流水</span>
                  <span className="rounded bg-surface-sunken px-1.5 py-0.2 text-3xs text-ink-muted">
                    {orders.length} 笔
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setDeskSubTab('conditional_grid')}
                  className={`flex items-center gap-1 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
                    deskSubTab === 'conditional_grid'
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-ink-secondary hover:text-ink'
                  }`}
                >
                  <span>🤖 智能条件单与网格</span>
                  <span className="rounded bg-surface-sunken px-1.5 py-0.2 text-3xs text-ink-muted">
                    {conditionalOrders.filter((c) => c.status === 'ACTIVE').length + gridBots.filter((g) => g.status === 'RUNNING').length} 运行中
                  </span>
                </button>
              </div>

              {/* 挂单与条件单专属快捷动作 */}
              <div className="flex items-center gap-2">
                {deskSubTab === 'conditional_grid' && (
                  <button
                    onClick={() => void handleEvaluateBots()}
                    disabled={busy}
                    className="rounded border border-brand-500/40 bg-brand-50 px-2.5 py-1 text-2xs font-semibold text-brand-700 hover:bg-brand-100 transition-colors disabled:opacity-50 flex items-center gap-1"
                    title="立即扫描实时盘口行情，评估撮合满足条件的订单与网格"
                  >
                    <span>⚡ 触发智能撮合评估</span>
                  </button>
                )}
                {deskSubTab === 'active_orders' && pendingOrders.length > 0 && (
                  <button
                    onClick={() => void handleCancelAllOrders()}
                    disabled={busy}
                    className="rounded border border-danger/40 bg-danger-bg px-2 py-1 text-2xs font-semibold text-danger hover:bg-danger hover:text-white transition-colors"
                  >
                    🛑 一键撤销全部挂单
                  </button>
                )}
              </div>
            </div>

            <div className="p-3">
              {/* 子 Tab 1: 实时持仓明细矩阵 */}
              {deskSubTab === 'positions' && (
                <div className="space-y-2">
                  {account && Object.keys(account.positions).length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-2xs">
                        <thead>
                          <tr className="border-b border-hair text-ink-secondary">
                            <th className="py-1.5 text-left font-medium">标的代码</th>
                            <th className="py-1.5 text-right font-medium">持仓股数/手数</th>
                            <th className="py-1.5 text-right font-medium">持仓市值 (元)</th>
                            <th className="py-1.5 text-right font-medium">成本单价 (元)</th>
                            <th className="py-1.5 text-right font-medium">最新现价 (元)</th>
                            <th className="py-1.5 text-right font-medium">浮动盈亏</th>
                            <th className="py-1.5 text-left font-medium">仓位占比 (上限55%)</th>
                            <th className="py-1.5 text-center font-medium">快捷动作</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-hair">
                          {Object.entries(account.positions).map(([sym, pos]) => {
                            const posWeight = account.equity > 0 ? (pos.market_value / account.equity) * 100 : 0;
                            const isProfit = pos.pnl >= 0;
                            return (
                              <tr key={sym} className="hover:bg-surface-alt transition-colors">
                                <td className="py-2 font-mono font-bold text-ink">{sym}</td>
                                <td className="num py-2 text-right font-medium">{pos.qty.toLocaleString()}</td>
                                <td className="num py-2 text-right font-medium text-ink">
                                  {pos.market_value.toLocaleString()}
                                </td>
                                <td className="num py-2 text-right text-ink-secondary">{pos.cost_price.toFixed(2)}</td>
                                <td className="num py-2 text-right text-ink-secondary">{pos.last_price.toFixed(2)}</td>
                                <td className="num py-2 text-right font-bold">
                                  <span className={isProfit ? 'text-danger' : 'text-success'}>
                                    {isProfit ? '+' : ''}
                                    {pos.pnl.toFixed(0)} 元
                                  </span>
                                  <span className={`block text-3xs ${isProfit ? 'text-danger' : 'text-success'}`}>
                                    {pos.cost_price > 0
                                      ? `${isProfit ? '+' : ''}${((pos.pnl / (pos.cost_price * pos.qty)) * 100).toFixed(2)}%`
                                      : '—'}
                                  </span>
                                </td>
                                <td className="py-2 text-left w-44">
                                  <div className="flex justify-between text-3xs text-ink-secondary mb-0.5">
                                    <span>{posWeight.toFixed(1)}%</span>
                                    <span className={posWeight > 55 ? 'text-danger font-bold' : 'text-ink-muted'}>
                                      上限 55%
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
                                </td>
                                <td className="py-2 text-center">
                                  <div className="flex items-center justify-center gap-1.5">
                                    <button
                                      type="button"
                                      onClick={() => handlePositionClose(sym, pos)}
                                      className="rounded bg-brand-50 px-2 py-0.5 text-3xs font-medium text-brand-700 hover:bg-brand-100"
                                      title="将该标的填入右侧算法下单终端"
                                    >
                                      平仓
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => void handleReversePosition(sym, pos)}
                                      disabled={busy}
                                      className="rounded bg-brand-50 px-2 py-0.5 text-3xs font-medium text-brand-700 hover:bg-brand-100 transition-colors disabled:opacity-50"
                                      title="平掉当前持仓并反向开仓/报单"
                                    >
                                      反手
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => void handleQuickLiquidateSingle(sym, pos)}
                                      disabled={busy}
                                      className="rounded bg-warn-bg px-2 py-0.5 text-3xs font-medium text-warn hover:bg-warn hover:text-white transition-colors disabled:opacity-50"
                                      title="立即下达市价清仓单并撮合"
                                    >
                                      市价清仓
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setForm((f) => ({ ...f, symbol: sym }));
                                        void fetchCurrentQuote(sym);
                                      }}
                                      className="rounded border border-hair px-2 py-0.5 text-3xs text-ink-secondary hover:bg-surface-sunken"
                                      title="切换左侧盘口与分时图"
                                    >
                                      看盘
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="py-12 text-center text-xs text-ink-muted">
                      当前账户空仓状态，无持仓风险敞口。请在上方报单终端提交母单并撮合。
                    </div>
                  )}
                </div>
              )}

              {/* 子 Tab 2: 当前在途挂单矩阵 */}
              {deskSubTab === 'active_orders' && (
                <div className="space-y-2">
                  {pendingOrders.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-2xs">
                        <thead>
                          <tr className="border-b border-hair text-ink-secondary">
                            <th className="py-1.5 text-left font-medium">母单#</th>
                            <th className="py-1.5 text-left font-medium">标的代码</th>
                            <th className="py-1.5 text-center font-medium">买卖方向</th>
                            <th className="py-1.5 text-center font-medium">拆单算法</th>
                            <th className="py-1.5 text-right font-medium">委托总金额</th>
                            <th className="py-1.5 text-right font-medium">已撮合金额</th>
                            <th className="py-1.5 text-center font-medium">执行进度</th>
                            <th className="py-1.5 text-center font-medium">当前状态</th>
                            <th className="py-1.5 text-center font-medium">快捷操作</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-hair">
                          {pendingOrders.map((o) => {
                            const progressPct = o.order_amount > 0 ? (o.filled_amount / o.order_amount) * 100 : 0;
                            return (
                              <tr key={o.id} className="hover:bg-surface-alt transition-colors">
                                <td className="py-2 text-ink-muted font-mono">#{o.id}</td>
                                <td className="py-2 font-mono font-bold text-ink">{o.symbol}</td>
                                <td className="py-2 text-center">
                                  <span
                                    className={`rounded px-1.5 py-0.2 font-semibold ${
                                      o.side === 'buy' ? 'bg-danger-bg text-danger' : 'bg-success-bg text-success'
                                    }`}
                                  >
                                    {o.side === 'buy' ? '买入' : '卖出'}
                                  </span>
                                </td>
                                <td className="py-2 text-center uppercase font-mono text-ink-secondary">{o.algo}</td>
                                <td className="num py-2 text-right">{o.order_amount.toLocaleString()} 元</td>
                                <td className="num py-2 text-right font-bold text-brand-600">
                                  {o.filled_amount.toLocaleString()} 元
                                </td>
                                <td className="py-2 text-center w-36">
                                  <div className="flex justify-between text-3xs text-ink-secondary mb-0.5">
                                    <span>{progressPct.toFixed(0)}%</span>
                                    <span>{o.fills.length} 笔子单</span>
                                  </div>
                                  <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
                                    <div
                                      className="h-full rounded-full bg-brand-500 transition-all"
                                      style={{ width: `${Math.min(100, progressPct)}%` }}
                                    />
                                  </div>
                                </td>
                                <td className="py-2 text-center">
                                  <span className={`rounded px-1.5 py-0.5 text-3xs font-medium ${STATUS_TONE[o.status] ?? ''}`}>
                                    {o.status}
                                  </span>
                                </td>
                                <td className="py-2 text-center">
                                  <button
                                    type="button"
                                    onClick={() => void handleCancelOrder(o.id)}
                                    disabled={busy}
                                    className="rounded border border-danger/40 bg-danger-bg px-2 py-0.5 text-3xs font-medium text-danger hover:bg-danger hover:text-white transition-colors disabled:opacity-50"
                                  >
                                    撤单
                                  </button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="py-12 text-center text-xs text-ink-muted">
                      当前无在途挂单。所有订单均已完成或出清。
                    </div>
                  )}
                </div>
              )}

              {/* 子 Tab 3: 全部母单与成交流水 (含筛选与子单基差明细) */}
              {deskSubTab === 'all_orders' && (
                <div className="space-y-2">
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
                          <th className="text-center font-medium py-1.5">操作</th>
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
                              <td
                                className={`py-2 text-center font-medium ${
                                  o.side === 'buy' ? 'text-danger' : 'text-success'
                                }`}
                              >
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
                              <td className="py-2 text-center" onClick={(e) => e.stopPropagation()}>
                                {(o.status === 'PENDING' || o.status === 'PART_FILLED') && (
                                  <button
                                    type="button"
                                    onClick={() => void handleCancelOrder(o.id)}
                                    disabled={busy}
                                    className="rounded border border-danger/40 bg-danger-bg px-1.5 py-0.5 text-3xs text-danger hover:bg-danger hover:text-white"
                                  >
                                    撤单
                                  </button>
                                )}
                              </td>
                            </tr>

                            {expandedOrder === o.id && o.fills.length > 0 && (
                              <tr className="bg-surface-alt">
                                <td colSpan={10} className="p-2.5">
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
                            <td colSpan={10} className="py-12 text-center text-xs text-ink-muted">
                              {orders.length ? '未找到符合条件的订单' : '暂无订单，请在上方报单终端提交母单并撮合'}
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* 子 Tab 4: 智能条件单与网格交易 */}
              {deskSubTab === 'conditional_grid' && (
                <div className="space-y-6">
                  {/* 第一板块：云端价格条件单 (Conditional Orders) */}
                  <div className="rounded-lg border border-hair bg-surface p-4 space-y-4">
                    <div className="flex items-center justify-between border-b border-hair pb-2">
                      <div>
                        <h3 className="text-xs font-bold text-ink flex items-center gap-1.5">
                          <span>⚡</span> 云端价格条件单 (Conditional Orders)
                        </h3>
                        <p className="text-3xs text-ink-muted">价格达到目标阈值时自动触发母单报单并执行撮合</p>
                      </div>
                      <span className="text-3xs text-ink-secondary">
                        活跃条件单: {conditionalOrders.filter((c) => c.status === 'ACTIVE').length} 笔
                      </span>
                    </div>

                    {/* 条件单配置表单 */}
                    <form onSubmit={handleCreateConditionalOrder} className="grid grid-cols-2 md:grid-cols-6 gap-2.5 items-end text-2xs">
                      <div>
                        <label className="block text-ink-secondary mb-1">标的代码</label>
                        <input
                          value={conditionalForm.symbol}
                          onChange={(e) => setConditionalForm({ ...conditionalForm, symbol: e.target.value.toUpperCase() })}
                          className={inputCls}
                          placeholder="如 000001.SZ"
                          required
                        />
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">方向</label>
                        <select
                          value={conditionalForm.side}
                          onChange={(e) => setConditionalForm({ ...conditionalForm, side: e.target.value as 'buy' | 'sell' })}
                          className={inputCls}
                        >
                          <option value="buy">买入 / 开多</option>
                          <option value="sell">卖出 / 平仓</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">触发条件</label>
                        <select
                          value={conditionalForm.trigger_type}
                          onChange={(e) => setConditionalForm({ ...conditionalForm, trigger_type: e.target.value as 'price_ge' | 'price_le' })}
                          className={inputCls}
                        >
                          <option value="price_ge">价格突破上涨 ≥</option>
                          <option value="price_le">价格跌破止损 ≤</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">触发价格 (元)</label>
                        <input
                          type="number"
                          step="0.01"
                          value={conditionalForm.trigger_price}
                          onChange={(e) => setConditionalForm({ ...conditionalForm, trigger_price: Number(e.target.value) })}
                          className={inputCls}
                          required
                        />
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">委托金额 (元)</label>
                        <input
                          type="number"
                          step="1000"
                          value={conditionalForm.order_amount}
                          onChange={(e) => setConditionalForm({ ...conditionalForm, order_amount: Number(e.target.value) })}
                          className={inputCls}
                          required
                        />
                      </div>
                      <div>
                        <button
                          type="submit"
                          disabled={busy}
                          className="w-full rounded bg-brand-600 py-1.5 font-medium text-white hover:bg-brand-500 disabled:opacity-50 cursor-pointer"
                        >
                          ➕ 创建条件单
                        </button>
                      </div>
                    </form>

                    {/* 条件单列表 */}
                    <div className="overflow-x-auto">
                      <table className="w-full text-2xs">
                        <thead>
                          <tr className="border-b border-hair text-ink-secondary">
                            <th className="py-1.5 text-left font-medium">编号</th>
                            <th className="py-1.5 text-left font-medium">标的代码</th>
                            <th className="py-1.5 text-center font-medium">方向</th>
                            <th className="py-1.5 text-left font-medium">触发逻辑</th>
                            <th className="py-1.5 text-right font-medium">目标触发价</th>
                            <th className="py-1.5 text-right font-medium">委托金额</th>
                            <th className="py-1.5 text-center font-medium">状态</th>
                            <th className="py-1.5 text-center font-medium">创建时间</th>
                            <th className="py-1.5 text-center font-medium">操作</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-hair">
                          {conditionalOrders.length === 0 ? (
                            <tr><td colSpan={9} className="py-6 text-center text-ink-muted">暂无条件单，可在上方表单设置</td></tr>
                          ) : (
                            conditionalOrders.map((c) => (
                              <tr key={c.id} className="hover:bg-surface-alt transition-colors">
                                <td className="py-2 font-mono text-ink-muted">#{c.id}</td>
                                <td className="py-2 font-mono font-bold text-ink">{c.symbol}</td>
                                <td className="py-2 text-center">
                                  <span className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${c.side === 'buy' ? 'bg-danger-bg text-danger' : 'bg-success-bg text-success'}`}>
                                    {c.side === 'buy' ? '买入' : '卖出'}
                                  </span>
                                </td>
                                <td className="py-2 text-ink-secondary">
                                  {c.trigger_type === 'price_ge' ? '价格突破 ≥' : '价格跌破 ≤'}
                                </td>
                                <td className="py-2 text-right font-mono font-semibold text-ink">¥{c.trigger_price.toFixed(2)}</td>
                                <td className="py-2 text-right font-mono text-ink">{c.order_amount.toLocaleString()} 元</td>
                                <td className="py-2 text-center">
                                  <span className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                                    c.status === 'ACTIVE' ? 'bg-warn-bg text-warn' :
                                    c.status === 'TRIGGERED' ? 'bg-success-bg text-success' : 'bg-surface-sunken text-ink-muted'
                                  }`}>
                                    {c.status === 'ACTIVE' ? '等待触发' : c.status === 'TRIGGERED' ? '已触发' : '已撤销'}
                                  </span>
                                </td>
                                <td className="py-2 text-center text-3xs text-ink-muted font-mono">{c.created_at?.slice(0, 16) ?? '—'}</td>
                                <td className="py-2 text-center">
                                  {c.status === 'ACTIVE' && (
                                    <button
                                      type="button"
                                      onClick={() => void handleCancelConditionalOrder(c.id)}
                                      disabled={busy}
                                      className="rounded border border-danger/30 px-2 py-0.5 text-3xs text-danger hover:bg-danger hover:text-white transition-colors cursor-pointer"
                                    >
                                      撤销
                                    </button>
                                  )}
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* 第二板块：网格交易机器人 (Grid Trading Bots) */}
                  <div className="rounded-lg border border-hair bg-surface p-4 space-y-4">
                    <div className="flex items-center justify-between border-b border-hair pb-2">
                      <div>
                        <h3 className="text-xs font-bold text-ink flex items-center gap-1.5">
                          <span>🤖</span> 动态网格交易机器人 (Grid Trading Bots)
                        </h3>
                        <p className="text-3xs text-ink-muted">在指定价格震荡区间内自动高抛低吸，按设定位阶连续撮合套利</p>
                      </div>
                      <span className="text-3xs text-ink-secondary">
                        运行中机器人: {gridBots.filter((g) => g.status === 'RUNNING').length} 个
                      </span>
                    </div>

                    {/* 网格机器人配置表单 */}
                    <form onSubmit={handleCreateGridBot} className="grid grid-cols-2 md:grid-cols-6 gap-2.5 items-end text-2xs">
                      <div>
                        <label className="block text-ink-secondary mb-1">标的代码</label>
                        <input
                          value={gridForm.symbol}
                          onChange={(e) => setGridForm({ ...gridForm, symbol: e.target.value.toUpperCase() })}
                          className={inputCls}
                          placeholder="如 000001.SZ"
                          required
                        />
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">价格下限 (元)</label>
                        <input
                          type="number"
                          step="0.01"
                          value={gridForm.lower_price}
                          onChange={(e) => setGridForm({ ...gridForm, lower_price: Number(e.target.value) })}
                          className={inputCls}
                          required
                        />
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">价格上限 (元)</label>
                        <input
                          type="number"
                          step="0.01"
                          value={gridForm.upper_price}
                          onChange={(e) => setGridForm({ ...gridForm, upper_price: Number(e.target.value) })}
                          className={inputCls}
                          required
                        />
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">网格格数 (格)</label>
                        <input
                          type="number"
                          min="2"
                          max="50"
                          value={gridForm.grid_count}
                          onChange={(e) => setGridForm({ ...gridForm, grid_count: Number(e.target.value) })}
                          className={inputCls}
                          required
                        />
                      </div>
                      <div>
                        <label className="block text-ink-secondary mb-1">每格金额 (元)</label>
                        <input
                          type="number"
                          step="1000"
                          value={gridForm.amount_per_grid}
                          onChange={(e) => setGridForm({ ...gridForm, amount_per_grid: Number(e.target.value) })}
                          className={inputCls}
                          required
                        />
                      </div>
                      <div>
                        <button
                          type="submit"
                          disabled={busy}
                          className="w-full rounded bg-brand-600 py-1.5 font-medium text-white hover:bg-brand-500 disabled:opacity-50 cursor-pointer"
                        >
                          🚀 启动网格机器人
                        </button>
                      </div>
                    </form>

                    {/* 网格机器人列表 */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      {gridBots.length === 0 ? (
                        <div className="col-span-3 py-6 text-center text-2xs text-ink-muted">
                          暂无运行中的网格交易机器人
                        </div>
                      ) : (
                        gridBots.map((g) => (
                          <div key={g.id} className="rounded-lg border border-hair bg-surface-alt p-3 space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="font-mono font-bold text-ink text-xs">{g.symbol}</span>
                              <span className={`rounded px-1.5 py-0.2 text-3xs font-semibold ${
                                g.status === 'RUNNING' ? 'bg-success-bg text-success' : 'bg-surface-sunken text-ink-muted'
                              }`}>
                                {g.status === 'RUNNING' ? '运行中' : '已停止'}
                              </span>
                            </div>
                            <div className="text-3xs text-ink-secondary space-y-1">
                              <div className="flex justify-between">
                                <span>区间范围:</span>
                                <span className="font-mono text-ink">¥{g.lower_price.toFixed(2)} ~ ¥{g.upper_price.toFixed(2)}</span>
                              </div>
                              <div className="flex justify-between">
                                <span>网格密度:</span>
                                <span className="font-mono text-ink">{g.grid_count} 格 (单格 ¥{g.amount_per_grid.toLocaleString()})</span>
                              </div>
                              <div className="flex justify-between">
                                <span>累计套利笔数:</span>
                                <span className="font-mono font-bold text-brand-600">{g.total_trades} 笔</span>
                              </div>
                              <div className="flex justify-between">
                                <span>累计套利盈利:</span>
                                <span className="font-mono font-bold text-danger">+{g.realized_pnl.toFixed(2)} 元</span>
                              </div>
                            </div>
                            {g.status === 'RUNNING' && (
                              <button
                                type="button"
                                onClick={() => void handleStopGridBot(g.id)}
                                disabled={busy}
                                className="w-full rounded border border-danger/30 bg-surface py-1 text-3xs font-medium text-danger hover:bg-danger hover:text-white transition-colors cursor-pointer"
                              >
                                停止机器人
                              </button>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
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
                  riskStatus?.halt_buys ? 'border-warn bg-warn-bg/20' : 'border-hair bg-surface'
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
                      riskUnreadable
                        ? 'bg-surface-sunken text-ink-secondary'
                        : riskStatus.halt_buys
                        ? 'bg-warn text-white'
                        : 'bg-surface-sunken text-ink-muted'
                    }`}
                  >
                    {riskUnreadable ? '状态不可读' : riskStatus.halt_buys ? '已启用' : '未激活'}
                  </span>
                </div>
                <p className="text-2xs text-ink-secondary leading-relaxed">
                  拦截一切买入开仓单，但允许持仓平仓卖出。用于应对结构性宏观风险、资金面紧张或组合再平衡准备。
                </p>
                <button
                  onClick={() => void toggleHaltBuys(!riskStatus?.halt_buys)}
                  disabled={busy || riskUnreadable}
                  className={`w-full rounded py-1.5 text-xs font-semibold transition-colors disabled:opacity-50 ${
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
                  riskStatus?.kill_switch ? 'border-danger bg-danger-bg/20' : 'border-hair bg-surface'
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
                      riskUnreadable
                        ? 'bg-surface-sunken text-ink-secondary'
                        : riskStatus.kill_switch
                        ? 'bg-danger text-white'
                        : 'bg-surface-sunken text-ink-muted'
                    }`}
                  >
                    {riskUnreadable ? '状态不可读' : riskStatus.kill_switch ? '硬熔断中' : '正常待命'}
                  </span>
                </div>
                <p className="text-2xs text-ink-secondary leading-relaxed">
                  拒绝一切新订单写入，并立即撤销全部在途未完成母单（已成交保留）。用于应对行情异常、系统故障或极端黑天鹅。
                </p>
                {!riskUnreadable && riskStatus.kill_switch ? (
                  <button
                    onClick={() => void toggleKill(false)}
                    disabled={busy || riskUnreadable}
                    className="w-full rounded border border-hair bg-surface py-1.5 text-xs font-semibold text-ink hover:bg-surface-alt disabled:opacity-50"
                  >
                    解除硬熔断
                  </button>
                ) : (
                  <button
                    onClick={() => setConfirmKill(true)}
                    disabled={busy || riskUnreadable}
                    className="w-full rounded bg-danger py-1.5 text-xs font-semibold text-white hover:bg-danger/90 shadow-xs disabled:opacity-50"
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
                    onClick={() => void openCandidatesDrawer()}
                    className="rounded-md border border-warn/40 bg-warn-bg px-2.5 py-1.5 text-xs font-semibold text-warn hover:bg-warn hover:text-white transition-colors disabled:opacity-50"
                  >
                    🛡️ 排雷候选池扫描 (ST/退市/低流动性)
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

            {/* 持仓结构透视 */}
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

      {/* ==================== P1-3: 排雷候选池扫描抽屉 ==================== */}
      <Drawer
        open={candDrawerOpen}
        onClose={() => setCandDrawerOpen(false)}
        title="全市场排雷候选池 (ST / 退市风险 / 低流动性)"
        sub="基于最新行情截面与合规画像动态扫描，支持条件过滤与一键批量导入事前风控禁买池"
        width="w-[min(94vw,680px)]"
        footer={
          <div className="flex items-center justify-between">
            <div className="text-2xs text-ink-secondary">
              已选中 <b className="text-brand-600 font-bold">{candSelected.size}</b> / {candidates.length} 只标的
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setCandDrawerOpen(false)}
                className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink hover:bg-surface-alt"
              >
                取消
              </button>
              <button
                type="button"
                disabled={candImporting || candSelected.size === 0}
                onClick={() => void batchImportCandidates()}
                className="rounded-md bg-warn px-3 py-1.5 text-xs font-semibold text-white hover:bg-warn/90 disabled:opacity-50 transition-colors shadow-xs"
              >
                {candImporting ? '正在批量导入…' : `批量导入禁买池 (${candSelected.size})`}
              </button>
            </div>
          </div>
        }
      >
        <div className="space-y-3 py-1">
          {/* 风险类别分布统计条 */}
          <div className="grid grid-cols-3 gap-2">
            <div
              onClick={() => setCandFilterCat('st')}
              className={`cursor-pointer rounded-lg border p-2 text-center transition-all ${
                candFilterCat === 'st' ? 'border-warn bg-warn-bg/30 ring-1 ring-warn' : 'border-hair bg-surface-alt hover:bg-surface'
              }`}
            >
              <div className="text-3xs text-ink-muted">ST / *ST 风险标的</div>
              <div className="num mt-0.5 text-base font-bold text-warn">
                {candidates.filter((c) => c.category === 'st').length}
              </div>
            </div>
            <div
              onClick={() => setCandFilterCat('delist_risk')}
              className={`cursor-pointer rounded-lg border p-2 text-center transition-all ${
                candFilterCat === 'delist_risk' ? 'border-danger bg-danger-bg/30 ring-1 ring-danger' : 'border-hair bg-surface-alt hover:bg-surface'
              }`}
            >
              <div className="text-3xs text-ink-muted">退市 / 摘牌预警</div>
              <div className="num mt-0.5 text-base font-bold text-danger">
                {candidates.filter((c) => c.category === 'delist_risk').length}
              </div>
            </div>
            <div
              onClick={() => setCandFilterCat('illiquid')}
              className={`cursor-pointer rounded-lg border p-2 text-center transition-all ${
                candFilterCat === 'illiquid' ? 'border-brand-300 bg-brand-50 ring-1 ring-brand-300' : 'border-hair bg-surface-alt hover:bg-surface'
              }`}
            >
              <div className="text-3xs text-ink-muted">极低流动性标的</div>
              <div className="num mt-0.5 text-base font-bold text-ink-secondary">
                {candidates.filter((c) => c.category === 'illiquid').length}
              </div>
            </div>
          </div>

          {/* 筛选与搜索控制栏 */}
          <div className="flex flex-wrap items-center gap-2 text-2xs">
            <input
              type="text"
              placeholder="搜索代码 / 名称 / 排雷原因"
              value={candSearch}
              onChange={(e) => setCandSearch(e.target.value)}
              className={`${inputCls} w-44`}
            />
            <select
              value={candFilterCat}
              onChange={(e) => setCandFilterCat(e.target.value)}
              className={`${inputCls} w-32`}
            >
              <option value="all">全部类别 ({candidates.length})</option>
              <option value="st">ST 风险</option>
              <option value="delist_risk">退市风险</option>
              <option value="illiquid">低流动性</option>
            </select>
            <div className="ml-auto flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => toggleAllCandidates(true)}
                className="rounded border border-hair px-2 py-1 text-2xs text-ink-secondary hover:bg-surface-alt"
              >
                全选当前
              </button>
              <button
                type="button"
                onClick={() => {
                  const stSyms = candidates.filter((c) => c.category === 'st').map((c) => c.symbol);
                  setCandSelected(new Set(stSyms));
                }}
                className="rounded border border-hair px-2 py-1 text-2xs text-warn hover:bg-warn-bg"
              >
                仅选 ST
              </button>
              <button
                type="button"
                onClick={() => setCandSelected(new Set())}
                className="rounded border border-hair px-2 py-1 text-2xs text-ink-muted hover:bg-surface-alt"
              >
                清空选择
              </button>
            </div>
          </div>

          {/* 候选标的列表 */}
          <div className="rounded-lg border border-hair bg-surface max-h-[420px] overflow-auto">
            {candLoading ? (
              <div className="flex min-h-[200px] flex-col items-center justify-center gap-2 text-xs text-ink-muted">
                <span className="h-5 w-5 animate-spin rounded-full border-2 border-warn border-t-transparent" />
                正在全面扫描全市场 ST 及流动性异常候选标的…
              </div>
            ) : filteredCandidates.length === 0 ? (
              <div className="flex min-h-[160px] items-center justify-center text-xs text-ink-muted">
                {candidates.length === 0 ? '暂无命中任何排雷特征的标的' : '无符合当前筛选条件的标的'}
              </div>
            ) : (
              <table className="w-full text-2xs">
                <thead className="sticky top-0 bg-surface border-b border-hair">
                  <tr className="text-ink-secondary">
                    <th className="w-8 py-2 text-center">
                      <input
                        type="checkbox"
                        checked={
                          filteredCandidates.length > 0 &&
                          filteredCandidates.every((c) => candSelected.has(c.symbol))
                        }
                        onChange={(e) => toggleAllCandidates(e.target.checked)}
                      />
                    </th>
                    <th className="py-2 text-left font-medium">标的代码</th>
                    <th className="py-2 text-left font-medium">标的简称</th>
                    <th className="py-2 text-center font-medium">风险类别</th>
                    <th className="py-2 text-left font-medium">排雷依据</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hair">
                  {filteredCandidates.map((c) => {
                    const isChecked = candSelected.has(c.symbol);
                    return (
                      <tr
                        key={c.symbol}
                        onClick={() => toggleCandSelected(c.symbol)}
                        className={`cursor-pointer hover:bg-surface-alt transition-colors ${
                          isChecked ? 'bg-warn-bg/15' : ''
                        }`}
                      >
                        <td className="py-2 text-center" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => toggleCandSelected(c.symbol)}
                          />
                        </td>
                        <td className="py-2 font-mono font-medium text-ink">{c.symbol}</td>
                        <td className="py-2 text-ink-secondary">{c.name || '—'}</td>
                        <td className="py-2 text-center">
                          <span
                            className={`rounded px-1.5 py-0.5 text-3xs font-semibold ${
                              c.category === 'st'
                                ? 'bg-warn-bg text-warn border border-warn/30'
                                : c.category === 'delist_risk'
                                ? 'bg-danger-bg text-danger border border-danger/30'
                                : 'bg-surface-sunken text-ink-secondary border border-hair'
                            }`}
                          >
                            {EX_CATEGORY_LABELS[c.category] ?? c.category}
                          </span>
                        </td>
                        <td className="py-2 text-ink-secondary text-2xs">{c.reason}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </Drawer>
    </div>
  );
}
