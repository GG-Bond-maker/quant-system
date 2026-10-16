/**
 * 期货中心（Futures Center）。
 *
 * 对标 Wind / Bloomberg / 聚宽 机构级衍生品投研终端：
 * 1. 【宏观全景】四大交易所主力合约全景监控与自动轮询刷新；
 * 2. 【主从联动】左侧主力行情大表 + 右侧深度分析工作台（行点击联动切换）；
 * 3. 【代码直达】点击任意期货品种代码直接跳转至对应品种的“期货分析 (Futures Analysis)”页面；
 * 4. 【四大新量化组件】：
 *    - 主力席位持仓龙虎榜 (COT Analysis)
 *    - 产业链与跨品种比价套利 (Inter-Commodity Spreads)
 *    - 全市场年化展期收益率矩阵 (Roll Yield Matrix)
 *    - 历史基差走势与分位数雷达 (Basis Percentile)
 * 5. 【机构工具】股票组合 Beta 套保手数与逐日盯市保证金计算器联动。
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageHeader, SectionCard, MetricCard } from '@/components/ui';
import { futuresApi } from '@/api/futures';
import { useWatchlistStore, DEFAULT_GROUP } from '@/stores/useWatchlistStore';
import type { FuturesContractItem, FuturesMarketSnapshot } from '@/types/futures';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';
import TermStructureCard from './TermStructureCard';
import CalendarSpreadCard from './CalendarSpreadCard';
import HedgeCalcModal from './HedgeCalcModal';
import CotRankCard from './CotRankCard';
import InterCommoditySpreadCard from './InterCommoditySpreadCard';
import RollYieldMatrixCard from './RollYieldMatrixCard';
import BasisHistoryCard from './BasisHistoryCard';
import RollOverRadarCard from './RollOverRadarCard';

const CATEGORIES = [
  { key: 'all', label: '全部品种' },
  { key: 'index', label: '股指期货 (CFFEX)' },
  { key: 'bond', label: '国债期货 (CFFEX)' },
  { key: 'precious', label: '贵金属 (SHFE)' },
  { key: 'ferrous', label: '黑色建材 (SHFE/DCE)' },
  { key: 'metal', label: '有色金属 (SHFE)' },
  { key: 'energy', label: '能源化工 (INE/CZCE)' },
] as const;

type SortField = 'close' | 'change_pct' | 'annualized_basis_rate' | 'basis_percentile' | 'volume' | 'open_interest';

export default function FuturesCenter() {
  const [snapshot, setSnapshot] = useState<FuturesMarketSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState<string>('all');

  // 主从联动选中品种（默认沪深300股指 IF）
  const [selectedSymbol, setSelectedSymbol] = useState<string>('IF');

  // 右侧深度工坊子选项卡
  const [detailTab, setDetailTab] = useState<'term_structure' | 'calendar_spread' | 'basis_history'>('term_structure');

  // 底部全市场量化分析模块选项卡
  const [macroTab, setMacroTab] = useState<'cot' | 'roll_yield' | 'inter_commodity'>('cot');

  // 自动轮询刷新控制 (0 为关闭，15/30 为秒数)
  const [refreshInterval, setRefreshInterval] = useState<number>(0);

  // 表格排序状态
  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // 套保测算弹窗状态
  const [hedgeModalOpen, setHedgeModalOpen] = useState(false);
  const [selectedHedgeSymbol, setSelectedHedgeSymbol] = useState('IF');
  const [selectedHedgePrice, setSelectedHedgePrice] = useState(3950.0);

  // 收藏与自选管理
  const { addTo, removeFrom, contains } = useWatchlistStore();

  const loadData = (showSkeleton = true) => {
    if (showSkeleton) setLoading(true);
    setError(null);
    futuresApi.contracts(activeCategory)
      .then((res) => {
        setSnapshot(res);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : '加载期货行情失败');
      })
      .finally(() => {
        if (showSkeleton) setLoading(false);
      });
  };

  useEffect(() => {
    loadData(true);
  }, [activeCategory]);

  // 自动轮询定时器
  useEffect(() => {
    if (refreshInterval <= 0) return;
    const timer = setInterval(() => {
      loadData(false);
    }, refreshInterval * 1000);
    return () => clearInterval(timer);
  }, [refreshInterval, activeCategory]);

  const rawItems = snapshot?.items ?? [];

  // 计算顶部汇总指标
  const ifContract = rawItems.find((it) => it.prefix === 'IF');
  const totalVolume = rawItems.reduce((acc, it) => acc + (it.volume || 0), 0);
  const totalOpenInterest = rawItems.reduce((acc, it) => acc + (it.open_interest || 0), 0);
  const avgBasisRate = rawItems.length
    ? rawItems.reduce((acc, it) => acc + (it.basis_rate || 0), 0) / rawItems.length
    : 0;

  // 排序处理
  const sortedItems = useMemo(() => {
    if (!sortField) return rawItems;
    return [...rawItems].sort((a, b) => {
      const vA = (a as any)[sortField] ?? 0;
      const vB = (b as any)[sortField] ?? 0;
      return sortOrder === 'asc' ? vA - vB : vB - vA;
    });
  }, [rawItems, sortField, sortOrder]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder('desc');
    }
  };

  const handleOpenHedge = (it: FuturesContractItem) => {
    setSelectedHedgeSymbol(it.prefix);
    setSelectedHedgePrice(it.close);
    setHedgeModalOpen(true);
  };

  // 获取当前选中品种对象
  const activeContractItem = rawItems.find((it) => it.prefix === selectedSymbol) ?? ifContract;

  return (
    <div className="min-h-full space-y-4">
      {/* 顶栏 */}
      <PageHeader
        title="期货中心 (Futures Center)"
        sub="对标 Wind / 聚宽 机构衍生品终端：全景行情 · 期限结构 · 主力席位龙虎榜 · 展期矩阵 · 跨品种套利"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {/* 自动轮询刷新控制 */}
            <div className="flex items-center rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink-secondary">
              <span className="mr-1.5 text-ink-muted">轮询:</span>
              <button
                type="button"
                onClick={() => setRefreshInterval(0)}
                className={`px-1.5 py-0.5 rounded ${refreshInterval === 0 ? 'bg-surface-sunken font-bold text-ink' : 'text-ink-muted'}`}
              >
                关
              </button>
              <button
                type="button"
                onClick={() => setRefreshInterval(15)}
                className={`px-1.5 py-0.5 rounded ${refreshInterval === 15 ? 'bg-brand-50 text-brand-700 font-bold' : 'text-ink-muted'}`}
              >
                15s
              </button>
              <button
                type="button"
                onClick={() => setRefreshInterval(30)}
                className={`px-1.5 py-0.5 rounded ${refreshInterval === 30 ? 'bg-brand-50 text-brand-700 font-bold' : 'text-ink-muted'}`}
              >
                30s
              </button>
              <button
                type="button"
                onClick={() => loadData(false)}
                title="立即刷新行情"
                className="ml-1 text-ink-muted hover:text-brand-600 transition-colors"
              >
                🔄
              </button>
            </div>

            <button
              type="button"
              onClick={() => {
                setSelectedHedgeSymbol(selectedSymbol);
                setSelectedHedgePrice(activeContractItem?.close ?? 3950);
                setHedgeModalOpen(true);
              }}
              className="rounded bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-brand-500 transition-colors"
            >
              🛡️ 组合套保与对冲测算
            </button>
            <Link
              to={`/futures/analysis?symbol=${selectedSymbol}`}
              className="rounded border border-brand-300 bg-brand-50 px-3 py-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-100 transition-colors"
            >
              📊 CTA多空策略分析
            </Link>
          </div>
        }
      />

      {/* 状态与降级提醒 */}
      {snapshot?.degraded && (
        <div className="rounded border border-warn/30 bg-warn-bg px-3.5 py-2 text-2xs text-warn flex items-center justify-between">
          <span>⚠️ 实时外网行情源暂时脱机，已无缝降级至本地保真数据湖，核心公式与基差计算保持正常。</span>
          <span className="num">{snapshot.as_of}</span>
        </div>
      )}

      {/* 顶部统计指标卡片 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard
          label="沪深300主力 (IF)"
          value={ifContract ? `${fmtNum(ifContract.close)}` : '—'}
          tone={ifContract && ifContract.change_pct > 0 ? 'text-up' : ifContract && ifContract.change_pct < 0 ? 'text-down' : undefined}
          sub={ifContract ? `基差 ${ifContract.basis} 点 · 年化 ${fmtPct(ifContract.annualized_basis_rate)}` : '主力合约'}
        />
        <MetricCard
          label="主力基差平均升贴水率"
          value={`${fmtPct(avgBasisRate)}`}
          sub="正值为贴水 (现货>期货) · 负值为升水"
        />
        <MetricCard
          label="全市场沉淀持仓量"
          value={fmtNum(totalOpenInterest)}
          sub="主力合约未平仓单总量"
        />
        <MetricCard
          label="主力合约日成交规模"
          value={fmtNum(totalVolume)}
          sub="全市场主力合约成交手总量"
        />
      </div>

      {/* 主工作区：左侧行情大表 (60%) + 右侧单品种深度工坊 (40%) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-stretch">
        {/* 左侧：主力合约全景看板 (Master View) */}
        <div className="lg:col-span-7 flex flex-col h-full min-h-0">
          <SectionCard
            title="主力合约全景看板"
            className="h-full flex-1 flex flex-col"
            bodyClassName="flex-1 flex flex-col min-h-0"
            action={
              <div className="flex flex-wrap items-center gap-1">
                {CATEGORIES.map((cat) => (
                  <button
                    key={cat.key}
                    type="button"
                    onClick={() => setActiveCategory(cat.key)}
                    className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                      activeCategory === cat.key
                        ? 'bg-brand-600 text-white font-medium'
                        : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
                    }`}
                  >
                    {cat.label}
                  </button>
                ))}
              </div>
            }
          >
            <div className="mb-2 text-2xs text-ink-muted flex items-center justify-between shrink-0">
              <span>💡 提示：点击<strong>品种代码</strong>直接跳转至“期货分析”；点击<strong>任意行</strong>可在右侧深度工坊联动切换。</span>
              <span className="num">当前联动: <strong className="text-brand-600">{selectedSymbol}</strong></span>
            </div>

            {loading && (
              <div className="flex-1 flex items-center justify-center py-16 text-center text-xs text-ink-muted animate-pulse">
                正在拉取全市场主力合约行情...
              </div>
            )}

            {error && (
              <div className="flex-1 flex items-center justify-center py-12 text-center text-xs text-warn">
                {error}
              </div>
            )}

            {!loading && !error && (
              <div className="flex-1 flex flex-col min-h-0 justify-between">
                <div className="overflow-x-auto overflow-y-auto flex-1 min-h-0">
                  <table className="quant-table dense w-full">
                    <thead>
                      <tr>
                        <th>代码 (点击直达分析)</th>
                        <th>品种</th>
                        <th className="text-right cursor-pointer select-none" onClick={() => handleSort('close')}>
                          最新价 {sortField === 'close' && (sortOrder === 'asc' ? '↑' : '↓')}
                        </th>
                        <th className="text-right cursor-pointer select-none" onClick={() => handleSort('change_pct')}>
                          涨跌幅 {sortField === 'change_pct' && (sortOrder === 'asc' ? '↑' : '↓')}
                        </th>
                        <th className="text-right">基差 (点)</th>
                        <th className="text-right cursor-pointer select-none" onClick={() => handleSort('annualized_basis_rate')}>
                          年化贴水 {sortField === 'annualized_basis_rate' && (sortOrder === 'asc' ? '↑' : '↓')}
                        </th>
                        <th className="text-right cursor-pointer select-none" onClick={() => handleSort('basis_percentile')}>
                          基差分位 {sortField === 'basis_percentile' && (sortOrder === 'asc' ? '↑' : '↓')}
                        </th>
                        <th className="text-right">交割(DTE)</th>
                        <th className="text-right">每手保证金</th>
                        <th className="text-right cursor-pointer select-none" onClick={() => handleSort('volume')}>
                          成交量 {sortField === 'volume' && (sortOrder === 'asc' ? '↑' : '↓')}
                        </th>
                        <th className="text-center">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedItems.length ? (
                        sortedItems.map((it) => {
                          const isSelected = selectedSymbol === it.prefix;
                          return (
                            <tr
                              key={it.prefix}
                              onClick={() => setSelectedSymbol(it.prefix)}
                              className={`cursor-pointer transition-colors ${
                                isSelected
                                  ? 'bg-brand-50/70 dark:bg-brand-950/30 ring-1 ring-inset ring-brand-300 font-medium'
                                  : 'hover:bg-surface-alt'
                              }`}
                            >
                              <td className="font-semibold num">
                                <Link
                                  to={`/futures/analysis?symbol=${it.prefix}`}
                                  onClick={(e) => e.stopPropagation()}
                                  className="font-bold text-brand-600 hover:text-brand-500 hover:underline inline-flex items-center gap-1 group"
                                  title={`点击跳转至 ${it.name} (${it.prefix}) 的期货分析界面`}
                                >
                                  <span>{it.symbol}</span>
                                  <span className="text-2xs opacity-60 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all">↗</span>
                                </Link>
                                {it.contract_code && (
                                  <div className="text-2xs text-ink-muted num font-normal">
                                    {it.contract_code}
                                  </div>
                                )}
                              </td>
                              <td className="text-xs text-ink">
                                <div>{it.name}</div>
                                <span className="text-2xs text-ink-muted">{it.exchange}</span>
                              </td>
                              <td className="num text-right text-xs font-semibold text-ink">
                                {fmtNum(it.close)}
                              </td>
                              <td className={`num text-right text-xs font-medium ${pctClass(it.change_pct)}`}>
                                {it.change_pct > 0 ? `+${it.change_pct}%` : `${it.change_pct}%`}
                              </td>
                              <td className="num text-right text-2xs text-ink">
                                {fmtNum(it.basis)}
                              </td>
                              <td className={`num text-right text-2xs font-semibold ${pctClass(it.annualized_basis_rate)}`}>
                                {fmtPct(it.annualized_basis_rate)}
                              </td>
                              <td className="num text-right text-2xs">
                                {it.basis_percentile !== undefined ? (
                                  <span
                                    className={`rounded px-1.5 py-0.2 font-medium ${
                                      it.basis_percentile >= 80
                                        ? 'bg-up-bg text-up'
                                        : it.basis_percentile <= 20
                                        ? 'bg-down-bg text-down'
                                        : 'text-ink-secondary'
                                    }`}
                                  >
                                    {it.basis_percentile}%
                                  </span>
                                ) : (
                                  '--'
                                )}
                              </td>
                              <td className="num text-right text-2xs text-ink-muted">
                                {it.days_to_expiry ? `${it.days_to_expiry}d` : '--'}
                              </td>
                              <td className="num text-right text-xs text-brand-700 font-medium">
                                {fmtNum(it.margin_per_lot)}
                              </td>
                              <td className="num text-right text-2xs text-ink-muted">
                                {fmtNum(it.volume)}
                              </td>
                              <td className="text-center" onClick={(e) => e.stopPropagation()}>
                                <div className="flex items-center justify-center gap-1">
                                  <button
                                    type="button"
                                    onClick={() => {
                                      if (contains(it.prefix)) removeFrom(DEFAULT_GROUP, it.prefix);
                                      else addTo(DEFAULT_GROUP, it.prefix);
                                    }}
                                    title={contains(it.prefix) ? '移出自选池' : '加入自选池'}
                                    className={`rounded p-1 text-xs transition-colors ${
                                      contains(it.prefix) ? 'text-warn' : 'text-ink-muted hover:text-warn'
                                    }`}
                                  >
                                    {contains(it.prefix) ? '★' : '☆'}
                                  </button>
                                  <Link
                                    to={`/futures/analysis?symbol=${it.prefix}`}
                                    className="rounded px-1.5 py-0.5 text-2xs font-medium text-brand-600 hover:bg-brand-50 border border-brand-200"
                                    title="进入 CTA 回测与微观画像分析"
                                  >
                                    分析
                                  </Link>
                                  <Link
                                    to={`/desk?symbol=${it.prefix}&side=buy`}
                                    className="rounded px-1.5 py-0.5 text-2xs font-medium text-ink-secondary hover:bg-surface-sunken border border-hair"
                                    title="带入交易大厅挂单/执行"
                                  >
                                    交易
                                  </Link>
                                  {it.category === 'index' && (
                                    <button
                                      type="button"
                                      onClick={() => handleOpenHedge(it)}
                                      className="rounded px-1.5 py-0.5 text-2xs text-ink-secondary hover:bg-surface-sunken border border-hair"
                                      title="一键测算股票组合对冲"
                                    >
                                      套保
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      ) : (
                        <tr>
                          <td colSpan={11} className="py-8 text-center text-xs text-ink-muted">
                            暂无该分类主力合约数据
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                {/* 底部行情状态与口径统计栏：保持拉长后底部视觉饱满且与右列底部平齐对齐 */}
                <div className="mt-2.5 pt-2 border-t border-hair flex flex-wrap items-center justify-between text-2xs text-ink-muted shrink-0">
                  <div className="flex items-center gap-3">
                    <span>当前展示: <strong className="text-ink font-semibold num">{sortedItems.length}</strong> 只主力合约</span>
                    <span className="hidden sm:inline">分类: <strong className="text-ink-secondary">{CATEGORIES.find((c) => c.key === activeCategory)?.label ?? '全部'}</strong></span>
                    <span>均值年化贴水: <strong className={`num font-semibold ${pctClass(avgBasisRate)}`}>{fmtPct(avgBasisRate)}</strong></span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-ink-secondary">点击代码直达「期货分析」深度投研</span>
                  </div>
                </div>
              </div>
            )}
          </SectionCard>
        </div>

        {/* 右侧：单品种深度分析工坊 (Detail View) */}
        <div className="lg:col-span-5 space-y-3">
          <div className="flex items-center justify-between rounded-lg border border-hair bg-surface p-2.5 shadow-sm">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-brand-700">
                🎯 当前选中品种：{activeContractItem?.name ?? selectedSymbol} ({selectedSymbol})
              </span>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setDetailTab('term_structure')}
                className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                  detailTab === 'term_structure'
                    ? 'bg-brand-600 text-white font-medium'
                    : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
                }`}
              >
                🧬 期限结构
              </button>
              <button
                type="button"
                onClick={() => setDetailTab('calendar_spread')}
                className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                  detailTab === 'calendar_spread'
                    ? 'bg-brand-600 text-white font-medium'
                    : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
                }`}
              >
                ⚖️ 跨期价差
              </button>
              <button
                type="button"
                onClick={() => setDetailTab('basis_history')}
                className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                  detailTab === 'basis_history'
                    ? 'bg-brand-600 text-white font-medium'
                    : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
                }`}
              >
                📉 历史分位
              </button>
            </div>
          </div>

          {/* 右侧视窗根据 Tab 显示对应组件 */}
          {detailTab === 'term_structure' && (
            <TermStructureCard initialSymbol={selectedSymbol} />
          )}

          {detailTab === 'calendar_spread' && (
            <CalendarSpreadCard initialSymbol={selectedSymbol} />
          )}

          {detailTab === 'basis_history' && (
            <BasisHistoryCard symbol={selectedSymbol} />
          )}

          {/* 新增：品种移仓换月时机与理论无套利定价雷达（填补右侧下方空白） */}
          <RollOverRadarCard
            symbol={selectedSymbol}
            activeContract={activeContractItem}
          />
        </div>
      </div>

      {/* 底部全市场量化投研扩展模块（对标 Wind / 聚宽三大新组件） */}
      <div className="space-y-3">
        <div className="flex items-center justify-between border-b border-hair pb-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-ink">全市场高级量化分析工坊</span>
            <span className="text-2xs text-ink-muted">对标机构终端核心能力：主力席位龙虎榜 · 展期天梯 · 产业链套利</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setMacroTab('cot')}
              className={`rounded px-3 py-1 text-2xs transition-colors ${
                macroTab === 'cot'
                  ? 'bg-brand-600 text-white font-medium'
                  : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
              }`}
            >
              🏛️ 主力席位龙虎榜 (COT)
            </button>
            <button
              type="button"
              onClick={() => setMacroTab('roll_yield')}
              className={`rounded px-3 py-1 text-2xs transition-colors ${
                macroTab === 'roll_yield'
                  ? 'bg-brand-600 text-white font-medium'
                  : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
              }`}
            >
              📊 全市场展期收益率天梯榜
            </button>
            <button
              type="button"
              onClick={() => setMacroTab('inter_commodity')}
              className={`rounded px-3 py-1 text-2xs transition-colors ${
                macroTab === 'inter_commodity'
                  ? 'bg-brand-600 text-white font-medium'
                  : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
              }`}
            >
              ⛓️ 产业链与跨品种套利
            </button>
          </div>
        </div>

        {macroTab === 'cot' && (
          <CotRankCard
            symbol={selectedSymbol}
            onSymbolChange={(sym) => setSelectedSymbol(sym)}
          />
        )}

        {macroTab === 'roll_yield' && (
          <RollYieldMatrixCard
            onSelectSymbol={(sym) => {
              setSelectedSymbol(sym);
              window.scrollTo({ top: 200, behavior: 'smooth' });
            }}
          />
        )}

        {macroTab === 'inter_commodity' && (
          <InterCommoditySpreadCard />
        )}
      </div>

      {/* 套期保值测算浮层 */}
      <HedgeCalcModal
        open={hedgeModalOpen}
        onClose={() => setHedgeModalOpen(false)}
        defaultSymbol={selectedHedgeSymbol}
        defaultPrice={selectedHedgePrice}
      />
    </div>
  );
}
