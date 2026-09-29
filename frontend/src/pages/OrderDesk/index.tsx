/** 实盘/模拟盘执行中心 + 实时风控闸门 + 合规禁买池（模拟盘，真实撮合）。 */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';

import { ApiError } from '@/api/client';
import { deskApi, type ExclusionItemT, type PaperAccount, type PaperOrder } from '@/api/production';
import { useRefreshIntervalMs } from '@/hooks/useRefreshInterval';
import { SectionCard } from '@/components/ui';
import { useChart } from '@/utils/useChart';

const inputCls =
  'w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';

const STATUS_TONE: Record<string, string> = {
  FILLED: 'bg-emerald-50 text-emerald-700',
  PART_FILLED: 'bg-brand-50 text-brand-700',
  PENDING: 'bg-amber-50 text-amber-700',
  CANCELLED: 'bg-slate-100 text-ink-muted',
  REJECTED: 'bg-red-50 text-red-600',
};
const STATUS_OPTIONS = ['PENDING', 'PART_FILLED', 'FILLED', 'CANCELLED', 'REJECTED'];
/** 母单列表一次请求的取数上限（后端 limit 参数，默认仅 50）。
 *  后端已补 total/truncated（P2-8），页面据此展示真实总数与是否截断。 */
const ORDER_WINDOW = 200;
const EX_CATEGORY_LABELS: Record<string, string> = {
  manual_blacklist: '手工黑名单', st: 'ST 风险',
  delist_risk: '退市风险', illiquid: '流动性差',
};

export default function OrderDesk() {
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  /** 后端独立 COUNT 的母单真实总数（不受 ORDER_WINDOW 影响）；null=未取到 */
  const [ordersTotal, setOrdersTotal] = useState<number | null>(null);
  /** 后端披露的截断标记（真实总数 > 本次返回条数） */
  const [ordersTruncated, setOrdersTruncated] = useState(false);
  const [kill, setKill] = useState<{ kill_switch: boolean; pending_orders: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exclusions, setExclusions] = useState<ExclusionItemT[]>([]);
  const [newEx, setNewEx] = useState({ symbol: '', category: 'manual_blacklist', reason: '' });
  const [form, setForm] = useState({
    symbol: '000001.SZ', side: 'buy' as 'buy' | 'sell',
    algo: 'vwap' as 'market' | 'vwap' | 'twap' | 'pov',
    order_amount: 1_000_000, split_days: 5, participation_cap: 0.05,
  });

  // P1：Kill Switch 二次确认 modal
  const [confirmKill, setConfirmKill] = useState(false);
  // P1：自动刷新（默认关，开启后每 8s 刷新）
  const [autoRefresh, setAutoRefresh] = useState(false);
  // P1：订单表筛选 + 分页 + 子单展开
  const [orderSearch, setOrderSearch] = useState('');
  const [orderStatus, setOrderStatus] = useState<string>('');
  const [orderSide, setOrderSide] = useState<string>('');
  const [orderPage, setOrderPage] = useState(1);
  const orderPageSize = 10;
  const [expandedOrder, setExpandedOrder] = useState<number | null>(null);
  // P1：禁买池筛选 + 批量选择
  const [exSearch, setExSearch] = useState('');
  const [exCategory, setExCategory] = useState<string>('');
  const [selectedEx, setSelectedEx] = useState<Set<number>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const [acc, ords, ks, ex] = await Promise.all([
        deskApi.account(), deskApi.orders(ORDER_WINDOW), deskApi.killSwitch(),
        deskApi.exclusionList()]);
      setAccount(acc); setOrders(ords.items); setOrdersTotal(ords.total);
      setOrdersTruncated(ords.truncated); setKill(ks); setExclusions(ex);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '加载失败');
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  // P1：market 算法自动置 split_days=1 并禁用
  useEffect(() => {
    if (form.algo === 'market' && form.split_days !== 1) {
      setForm((f) => ({ ...f, split_days: 1 }));
    }
  }, [form.algo, form.split_days]);

  // P1：自动刷新（间隔消费系统设置 refresh_freq，2x 倍率避免高频打后端）
  const refreshMs = useRefreshIntervalMs(2);
  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(() => void refresh(), refreshMs);
    return () => clearInterval(t);
  }, [autoRefresh, refresh, refreshMs]);

  const submit = useCallback(async () => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const res = await deskApi.placeOrder(form);
      setMsg(res.ok ? `母单 #${res.order_id} 已提交（决策价 ${res.decision_price}）`
                    : `被拒绝：${res.reason}`);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '提交失败');
    } finally { setBusy(false); }
  }, [form, refresh]);

  const runFills = useCallback(async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await deskApi.runFills();
      setMsg(`撮合完成：成交 ${r.fills_created} 笔子单，顺延 ${r.deferred_children} 笔`
        + '（顺延 = 本地行情尚未同步到该执行日，数据更新后自动成交）');
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '撮合失败');
    } finally { setBusy(false); }
  }, [refresh]);

  const toggleKill = useCallback(async (active: boolean) => {
    setConfirmKill(false); // 关闭确认 modal
    try {
      const r = await deskApi.setKillSwitch(active, '控制台人工触发');
      setMsg(active ? `熔断已激活，撤销 ${r.cancelled_orders} 笔未完成母单`
                    : '熔断已解除');
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '操作失败');
    }
  }, [refresh]);

  // P1：订单表筛选 + 分页（纯前端派生）
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
  // 筛选条件变化时回到第一页
  useEffect(() => { setOrderPage(1); }, [orderSearch, orderStatus, orderSide]);

  // P1：禁买池筛选（纯前端派生）
  const filteredExclusions = useMemo(() => {
    let out = exclusions;
    if (exSearch.trim()) {
      const q = exSearch.trim().toUpperCase();
      out = out.filter((x) => x.symbol.toUpperCase().includes(q));
    }
    if (exCategory) out = out.filter((x) => x.category === exCategory);
    return out;
  }, [exclusions, exSearch, exCategory]);

  const toggleExSelected = (id: number) => setSelectedEx((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleAllEx = (checked: boolean) =>
    setSelectedEx(checked ? new Set(filteredExclusions.map((x) => x.id)) : new Set());

  const batchToggleEx = async (active: boolean) => {
    if (selectedEx.size === 0) return;
    setBusy(true); setErr(null);
    try {
      for (const id of selectedEx) {
        await deskApi.toggleExclusion(id, active);
      }
      setMsg(`已${active ? '启用' : '停用'} ${selectedEx.size} 条禁买标的`);
      setSelectedEx(new Set());
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '批量操作失败');
    } finally { setBusy(false); }
  };

  // P1：账户卡收益率（纯前端派生）
  const totalReturn = account && account.initial_cash > 0
    ? (account.equity - account.initial_cash) / account.initial_cash : null;

  // 净值曲线（后端逐日重建：cash + 持仓市值）
  const navOption = account && account.nav_series.length > 1
    ? ({
        grid: { left: 54, right: 8, top: 8, bottom: 20 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: account.nav_series.map((n) => n.date),
                 axisLabel: { fontSize: 8 } },
        yAxis: { type: 'value', scale: true,
                 axisLabel: { fontSize: 8,
                              formatter: (v: number) => `${(v / 10000).toFixed(0)}万` } },
        series: [{ name: '总权益', type: 'line', symbol: 'none',
                   data: account.nav_series.map((n) => n.equity),
                   lineStyle: { width: 1.4, color: '#2563EB' },
                   areaStyle: { opacity: 0.06 } }],
      } as echarts.EChartsOption)
    : null;
  const navRef = useChart(navOption);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-ink">
          执行中心 <span className="text-2xs font-normal text-amber-600">（模拟盘：本地撮合 · 真实行情 · 真实费用）</span>
        </h1>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-2xs text-ink-secondary">
            <input type="checkbox" checked={autoRefresh}
                   onChange={(e) => setAutoRefresh(e.target.checked)}
                   className="h-3 w-3" />
            自动刷新（8s）
          </label>
          <button onClick={() => void runFills()} disabled={busy}
                  className="rounded-md border border-hair px-3 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-60">
            撮合到期子单
          </button>
          <button onClick={() => void refresh()}
                  className="rounded-md border border-hair px-3 py-1.5 text-xs hover:bg-slate-50">
            刷新
          </button>
        </div>
      </div>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}
      {msg && <div className="rounded-md bg-brand-50 px-3 py-2 text-xs text-brand-700">{msg}</div>}

      {/* Kill Switch 二次确认 modal */}
      {confirmKill && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setConfirmKill(false)}>
          <div className="w-80 rounded-lg bg-white p-4 shadow-lg" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 text-sm font-semibold text-red-600">确认一键熔断？</div>
            <p className="mb-3 text-2xs text-ink-secondary">
              将撤销全部 <span className="font-semibold text-red-600">{kill?.pending_orders ?? 0}</span> 笔未完成母单（已成交保留），并禁止一切新订单提交。此操作不可撤销。
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmKill(false)}
                      className="rounded-md border border-hair px-3 py-1.5 text-xs hover:bg-slate-50">取消</button>
              <button onClick={() => void toggleKill(true)}
                      className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700">
                确认熔断
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        {/* 账户 + 下单 */}
        <div className="space-y-3 xl:col-span-4">
          <SectionCard title="模拟盘账户" bodyClassName="p-3">
            {account ? (
              <div className="grid grid-cols-2 gap-2 text-2xs">
                {/* P1-3：历史越卖成交（修复前会产生）曾凭空造出现金 ⇒ 必须显式披露，
                    否则虚高的现金/权益被当成真实数字呈现。正常为空数组，不显示。 */}
                {(account.integrity_warnings?.length ?? 0) > 0 && (
                  <div className="col-span-2 rounded border border-red-300 bg-red-50 px-2 py-1.5 text-red-700"
                       title="卖出股数超过当时持仓的成交会凭空造出现金，导致现金与权益偏高">
                    <div className="font-semibold">⚠️ 账目完整性告警（{account.integrity_warnings.length} 条）</div>
                    <ul className="mt-0.5 list-disc pl-4">
                      {account.integrity_warnings.slice(0, 3).map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                    {account.integrity_warnings.length > 3 && (
                      <div className="mt-0.5 text-ink-muted">…另有 {account.integrity_warnings.length - 3} 条</div>
                    )}
                  </div>
                )}
                <div><span className="text-ink-secondary">初始资金：</span>
                  <span className="num">{account.initial_cash.toLocaleString()}</span></div>
                <div><span className="text-ink-secondary">现金：</span>
                  <span className="num font-semibold">{account.cash.toLocaleString()}</span></div>
                <div><span className="text-ink-secondary">持仓市值：</span>
                  <span className="num font-semibold">{account.market_value.toLocaleString()}</span></div>
                <div><span className="text-ink-secondary">总权益：</span>
                  <span className="num font-semibold text-brand-700">{account.equity.toLocaleString()}</span></div>
                <div className="col-span-2 rounded bg-slate-50 px-2 py-1">
                  <span className="text-ink-secondary">累计收益率：</span>
                  {/* ⚠️ totalReturn 可为 null（:181 派生态；下方已显 '—'）：不得写 `!= null && >= 0 ? 红 : 绿`
                      —— null 会落进 text-emerald-600（绿）。null ⇒ 中性色。 */}
                  <span className={`num font-semibold ${totalReturn == null ? 'text-ink-muted' : totalReturn >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                    {totalReturn != null ? `${(totalReturn * 100).toFixed(2)}%` : '—'}
                  </span>
                  <span className="ml-2 text-ink-muted">（= (总权益 - 初始资金) / 初始资金，含未实现浮动盈亏）</span>
                </div>
                {/* 派生指标（后端由逐日净值序列计算；活跃 < 30 交易日显示"样本不足"） */}
                <div className="col-span-2 grid grid-cols-4 gap-2">
                  <div className="rounded bg-slate-50 px-2 py-1 text-center"
                       title="由逐日净值序列年化（252 交易日）">
                    <div className="text-ink-muted">年化收益</div>
                    {account.annualized_return != null ? (
                      <div className={`num font-semibold ${account.annualized_return >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                        {(account.annualized_return * 100).toFixed(2)}%
                      </div>
                    ) : <div className="text-ink-muted">样本不足</div>}
                  </div>
                  <div className="rounded bg-slate-50 px-2 py-1 text-center"
                       title="峰值到谷底的最大跌幅（负数）">
                    <div className="text-ink-muted">最大回撤</div>
                    {account.max_drawdown != null ? (
                      <div className="num font-semibold text-emerald-700">
                        {(account.max_drawdown * 100).toFixed(2)}%
                      </div>
                    ) : <div className="text-ink-muted">样本不足</div>}
                  </div>
                  <div className="rounded bg-slate-50 px-2 py-1 text-center"
                       title="日收益年化夏普（rf=0）">
                    <div className="text-ink-muted">夏普比率</div>
                    {account.sharpe != null ? (
                      <div className={`num font-semibold ${account.sharpe >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                        {account.sharpe.toFixed(2)}
                      </div>
                    ) : <div className="text-ink-muted">样本不足</div>}
                  </div>
                  <div className="rounded bg-slate-50 px-2 py-1 text-center"
                       title="自首笔成交以来的交易日数">
                    <div className="text-ink-muted">活跃天数</div>
                    <div className="num font-semibold">{account.n_active_days}</div>
                  </div>
                </div>
                {account.nav_series.length > 1 && (
                  <div className="col-span-2">
                    <div className="text-ink-muted">净值曲线（逐日重建：现金 + 持仓市值）</div>
                    <div ref={navRef} className="h-24 w-full" />
                  </div>
                )}
                <div><span className="text-ink-secondary">成交笔数：</span>
                  <span className="num">{account.n_fills}</span></div>
                <div><span className="text-ink-secondary">累计佣金：</span>
                  <span className="num">{account.total_fees.toLocaleString()}</span></div>
                <div className="col-span-2"><span className="text-ink-secondary">累计冲击成本：</span>
                  <span className="num">{account.total_impact_cost.toLocaleString()}</span></div>
                {Object.entries(account.positions).map(([sym, p]) => (
                  <div key={sym} className="col-span-2 rounded bg-slate-50 px-2 py-1 font-mono text-2xs">
                    {sym} × {p.qty} 股 @成本 {p.cost_price} · 现价 {p.last_price} ·
                    <span className={p.pnl >= 0 ? 'text-red-600' : 'text-emerald-600'}>
                      {p.pnl >= 0 ? '+' : ''}{p.pnl.toFixed(0)} 元
                      {p.cost_price > 0 && ` (${(p.pnl / (p.cost_price * p.qty) * 100).toFixed(2)}%)`}
                    </span>
                  </div>
                ))}
              </div>
            ) : <div className="py-6 text-center text-xs text-ink-muted">加载中…</div>}
          </SectionCard>

          <SectionCard title="提交母单（算法分批）" bodyClassName="p-3 space-y-2">
            <input value={form.symbol} placeholder="标的代码"
                   className={inputCls}
                   onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
            <div className="grid grid-cols-2 gap-2">
              <select value={form.side} className={inputCls}
                      onChange={(e) => setForm({ ...form, side: e.target.value as 'buy' | 'sell' })}>
                <option value="buy">BUY 买入</option>
                <option value="sell">SELL 卖出</option>
              </select>
              <select value={form.algo} className={inputCls}
                      onChange={(e) => setForm({ ...form, algo: e.target.value as typeof form.algo })}>
                <option value="market">Market 一次性</option>
                <option value="vwap">VWAP 分批</option>
                <option value="twap">TWAP 分批</option>
                <option value="pov">POV 分批</option>
              </select>
              <input type="number" min={10000} step={100000} value={form.order_amount}
                     className={inputCls}
                     onChange={(e) => setForm({ ...form, order_amount: Number(e.target.value) })} />
              <input type="number" min={1} max={20} value={form.split_days}
                     className={`${inputCls} ${form.algo === 'market' ? 'opacity-50' : ''}`}
                     disabled={form.algo === 'market'}
                     title={form.algo === 'market' ? 'market 算法恒为 1 天' : '分批天数'}
                     onChange={(e) => setForm({ ...form, split_days: Number(e.target.value) })} />
            </div>
            <label className="block text-2xs text-ink-secondary">
              参与率上限 {(form.participation_cap * 100).toFixed(1)}%（单日成交额占比）
              <input type="range" min={0.005} max={0.3} step={0.005} value={form.participation_cap}
                     className="w-full"
                     onChange={(e) => setForm({ ...form, participation_cap: Number(e.target.value) })} />
            </label>
            <button disabled={busy} onClick={() => void submit()}
                    className="w-full rounded-md bg-brand-500 py-2 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
              提交母单
            </button>
            <p className="text-2xs text-ink-muted">
              决策价 = 下单时点最新收盘价（回测理想价）；实际成交 = 执行日开盘价 ± sqrt 冲击，
              逐笔记录基差（basis）。子单在执行日行情同步后撮合（D0+1 起，无未来函数）。
            </p>
          </SectionCard>

          <SectionCard title="实时风控闸门（Kill Switch）" bodyClassName="p-3 space-y-2">
            <div className="flex items-center justify-between text-2xs">
              <span className="text-ink-secondary">状态</span>
              {/* C-20：读不到熔断状态（kill=null）必须显示"不可读"，
                  不能染绿成"正常运行"——把未知当安全是风控语义错误 */}
              <span className={`rounded px-2 py-0.5 font-semibold ${
                kill == null
                  ? 'bg-slate-100 text-ink-secondary'
                  : kill.kill_switch ? 'bg-red-100 text-red-700' : 'bg-emerald-50 text-emerald-700'}`}>
                {kill == null ? '状态不可读' : kill.kill_switch ? '熔断激活' : '正常运行'}
              </span>
            </div>
            <div className="text-2xs text-ink-secondary">
              未完成母单：{kill == null ? '状态不可读' : (kill.pending_orders ?? '—')}
            </div>
            {kill == null && (
              <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-2xs text-amber-700">
                熔断状态读取失败，页面不作"运行正常"假设；请刷新后确认。
              </div>
            )}
            <button onClick={() => setConfirmKill(true)}
                    className="w-full rounded-md bg-red-600 py-2 text-xs font-semibold text-white hover:bg-red-700">
              一键熔断（撤全部未完成母单 + 禁止新订单）
            </button>
            <button onClick={() => void toggleKill(false)} disabled={kill == null}
                    title={kill == null ? '熔断状态不可读，禁止盲目解除' : undefined}
                    className="w-full rounded-md border border-hair py-1.5 text-xs hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50">
              解除熔断
            </button>
          </SectionCard>
        </div>

        {/* 订单列表 + 合规禁买池（合容器，xl 下右 8 列） */}
        <div className="xl:col-span-8 space-y-3">
        <SectionCard title="母单 / 子单监控（成交价 vs 决策价 = 基差）" bodyClassName="p-3 space-y-2">
          {/* 筛选 + 搜索 + 分页 */}
          <div className="flex flex-wrap items-end gap-2 text-2xs">
            <input value={orderSearch} placeholder="搜索代码 / #ID"
                   className={`${inputCls} w-32`}
                   onChange={(e) => setOrderSearch(e.target.value)} />
            <select value={orderStatus} className={`${inputCls} w-32`}
                    onChange={(e) => setOrderStatus(e.target.value)}>
              <option value="">全部状态</option>
              {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={orderSide} className={`${inputCls} w-24`}
                    onChange={(e) => setOrderSide(e.target.value)}>
              <option value="">全部方向</option>
              <option value="buy">买</option>
              <option value="sell">卖</option>
            </select>
            <span className="text-ink-muted">
              {filteredOrders.length} 笔{filteredOrders.length !== orders.length && `（共 ${orders.length}）`}
            </span>
            {/* P2-8：后端已返回真实总数与截断标记，据此如实披露窗口性质 */}
            <span className={ordersTruncated ? 'text-amber-600' : 'text-ink-muted'}>
              {ordersTotal != null ? `母单总数 ${ordersTotal} 笔` : '母单总数未知'}
              {ordersTruncated
                ? `（本次仅取最近 ${orders.length} 条，筛选与分页仅在此窗口内成立）`
                : '（已完整取回）'}
            </span>
            {totalPages > 1 && (
              <div className="ml-auto flex items-center gap-1">
                <button onClick={() => setOrderPage((p) => Math.max(1, p - 1))} disabled={orderPage <= 1}
                        className="rounded border border-hair px-1.5 py-0.5 disabled:opacity-40">‹</button>
                <span className="text-ink-muted">{orderPage}/{totalPages}</span>
                <button onClick={() => setOrderPage((p) => Math.min(totalPages, p + 1))} disabled={orderPage >= totalPages}
                        className="rounded border border-hair px-1.5 py-0.5 disabled:opacity-40">›</button>
              </div>
            )}
          </div>
          <div className="max-h-[520px] overflow-auto">
            <table className="w-full text-2xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-ink-secondary">
                  <th className="text-left font-medium">#</th>
                  <th className="text-left font-medium">标的</th>
                  <th className="font-medium">方向</th>
                  <th className="font-medium">算法</th>
                  <th className="font-medium">金额</th>
                  <th className="font-medium">状态</th>
                  <th className="font-medium">决策价</th>
                  <th className="font-medium">子单</th>
                </tr>
              </thead>
              <tbody>
                {pagedOrders.map((o) => (
                  <Fragment key={o.id}>
                    <tr className="border-t border-hair align-top cursor-pointer hover:bg-slate-50"
                        onClick={() => setExpandedOrder(expandedOrder === o.id ? null : o.id)}>
                      <td className="py-1.5 text-ink-muted">{o.id}</td>
                      <td className="py-1.5 font-mono">{o.symbol}</td>
                      <td className={`text-center ${o.side === 'buy' ? 'text-red-600' : 'text-emerald-600'}`}>
                        {o.side === 'buy' ? '买' : '卖'}</td>
                      <td className="text-center uppercase">{o.algo}</td>
                      <td className="num text-center">{o.order_amount.toLocaleString()}</td>
                      <td className="text-center">
                        <span className={`rounded px-1 py-0.5 ${STATUS_TONE[o.status] ?? ''}`}>{o.status}</span>
                        {o.reject_reason && <div className="mt-0.5 text-2xs text-red-600">{o.reject_reason}</div>}
                      </td>
                      <td className="num text-center">{o.decision_price ?? '—'}</td>
                      <td className="text-center">
                        {o.fills.length ? (
                          <span className="text-brand-600 underline">{expandedOrder === o.id ? '收起' : `${o.fills.length} 笔 ▾`}</span>
                        ) : <span className="text-ink-muted">待撮合</span>}
                      </td>
                    </tr>
                    {expandedOrder === o.id && o.fills.length > 0 && (
                      <tr className="bg-slate-50">
                        <td colSpan={8} className="p-2">
                          <div className="space-y-0.5 font-mono text-2xs">
                            {o.fills.map((f, i) => (
                              <div key={i} className="flex justify-between border-b border-hair pb-0.5">
                                <span>{f.exec_date} · {f.qty}股 @ {f.price} · 金额 {f.amount.toLocaleString()}</span>
                                <span>
                                  冲击 {f.impact_bps}bps ·
                                  <span className={f.basis_bps >= 0 ? 'text-red-600' : 'text-emerald-600'}>
                                    基差 {f.basis_bps >= 0 ? '+' : ''}{f.basis_bps}bps
                                  </span>
                                </span>
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
                {!pagedOrders.length && (
                  <tr><td colSpan={8} className="py-10 text-center text-ink-muted">
                    {orders.length ? '无匹配订单（调整筛选条件）' : '暂无订单 —— 左侧提交第一笔母单'}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>

        <SectionCard title="合规禁买池（下单实时校验）" bodyClassName="p-3 space-y-2">
          <div className="flex flex-wrap items-end gap-2 text-2xs">
            <label>标的<input value={newEx.symbol} placeholder="如 600000.SH"
                     className={`${inputCls} w-28`}
                     onChange={(e) => setNewEx({ ...newEx, symbol: e.target.value })} /></label>
            <label>类别
              <select value={newEx.category} className={`${inputCls} w-40`}
                      onChange={(e) => setNewEx({ ...newEx, category: e.target.value })}>
                <option value="manual_blacklist">手工黑名单</option>
                <option value="st">ST 风险</option>
                <option value="delist_risk">退市风险</option>
                <option value="illiquid">流动性差</option>
              </select></label>
            <label>原因<input value={newEx.reason} placeholder="合规理由"
                     className={`${inputCls} w-48`}
                     onChange={(e) => setNewEx({ ...newEx, reason: e.target.value })} /></label>
            <button disabled={busy || !newEx.symbol}
                    onClick={async () => {
                      try {
                        await deskApi.addExclusion(newEx.symbol, newEx.category, newEx.reason);
                        setNewEx({ ...newEx, symbol: '', reason: '' });
                        await refresh();
                      } catch (e) { setErr(e instanceof ApiError ? e.message : '添加失败'); }
                    }}
                    className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
              加入禁买池
            </button>
            <button disabled={busy}
                    onClick={async () => {
                      try {
                        const cands = await deskApi.screenCandidates();
                        for (const c of cands.slice(0, 10)) {
                          await deskApi.addExclusion(c.symbol, c.category, c.reason);
                        }
                        await refresh();
                      } catch (e) { setErr(e instanceof ApiError ? e.message : '导入失败'); }
                    }}
                    className="rounded-md border border-hair px-3 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-60">
              一键导入 ST/流动性差候选（前 10）
            </button>
          </div>
          {/* 批量操作栏（仅在有选中时显示） */}
          {selectedEx.size > 0 && (
            <div className="flex items-center gap-2 rounded-md bg-brand-50 px-2 py-1 text-2xs text-brand-700">
              已选 {selectedEx.size} 条
              <button disabled={busy} onClick={() => void batchToggleEx(true)}
                      className="rounded border border-hair px-2 py-0.5 hover:bg-slate-50 disabled:opacity-60">批量启用</button>
              <button disabled={busy} onClick={() => void batchToggleEx(false)}
                      className="rounded border border-hair px-2 py-0.5 hover:bg-slate-50 disabled:opacity-60">批量停用</button>
              <button onClick={() => setSelectedEx(new Set())}
                      className="rounded border border-hair px-2 py-0.5 hover:bg-slate-50">清空选择</button>
            </div>
          )}
          {/* 筛选 + 搜索 */}
          <div className="flex flex-wrap items-end gap-2 text-2xs">
            <input value={exSearch} placeholder="搜索标的代码"
                   className={`${inputCls} w-28`}
                   onChange={(e) => setExSearch(e.target.value)} />
            <select value={exCategory} className={`${inputCls} w-32`}
                    onChange={(e) => setExCategory(e.target.value)}>
              <option value="">全部类别</option>
              {Object.entries(EX_CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <span className="text-ink-muted">
              {filteredExclusions.length} 条{filteredExclusions.length !== exclusions.length && `（共 ${exclusions.length}）`}
            </span>
          </div>
          <div className="max-h-52 overflow-auto">
            <table className="w-full text-2xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-ink-secondary">
                  <th className="w-6 text-center font-medium">
                    <input type="checkbox" className="h-3 w-3"
                           checked={filteredExclusions.length > 0 && selectedEx.size === filteredExclusions.length}
                           onChange={(e) => toggleAllEx(e.target.checked)} />
                  </th>
                  <th className="text-left font-medium">标的</th>
                  <th className="font-medium">类别</th>
                  <th className="text-left font-medium">原因</th>
                  <th className="font-medium">启用</th>
                </tr>
              </thead>
              <tbody>
                {filteredExclusions.map((x) => (
                  <tr key={x.id} className="border-t border-hair">
                    <td className="text-center">
                      <input type="checkbox" className="h-3 w-3"
                             checked={selectedEx.has(x.id)}
                             onChange={() => toggleExSelected(x.id)} />
                    </td>
                    <td className="py-1 font-mono">{x.symbol}</td>
                    <td className="text-center">{EX_CATEGORY_LABELS[x.category] ?? x.category}</td>
                    <td className="text-ink-secondary">{x.reason}</td>
                    <td className="text-center">
                      <button onClick={async () => {
                        await deskApi.toggleExclusion(x.id, !x.active);
                        await refresh();
                      }}
                        className={`rounded px-1.5 py-0.5 ${x.active ? 'bg-red-50 text-red-600' : 'bg-slate-100 text-ink-muted'}`}>
                        {x.active ? '生效中' : '已停用'}
                      </button>
                    </td>
                  </tr>
                ))}
                {!filteredExclusions.length && (
                  <tr><td colSpan={5} className="py-6 text-center text-ink-muted">
                    {exclusions.length ? '无匹配禁买标的' : '禁买池为空（下单不受合规限制）'}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>
        </div>
      </div>
    </div>
  );
}
