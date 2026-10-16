/**
 * AH 股溢价与跨市场套利监控看板（P2/P1 扩展）。
 *
 * 核心功能：
 * 1. AH 股溢价率 (AH Premium Rate) 实时监控与统计分布；
 * 2. 考虑离岸港元汇率 (HKD/CNY) 的折合价格精确测算；
 * 3. 统计套利信号分级 (做多H股+做空A股 / 适度溢价 / 平价 / H股溢价)；
 * 4. 严格遵循数据真实性红线：无虚假报价，行情降级如实提示。
 */
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { AhSpreadItem, AhSpreadResult } from '@/types/stock';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';
import { PanelEmpty } from '@/components/ui';

interface Props {
  data?: AhSpreadResult;
  loading: boolean;
  onRefresh?: () => void;
}

const SIGNAL_FILTER_OPTIONS = [
  { key: 'all', label: '全部标的' },
  { key: 'a_extreme_premium', label: '极度溢价 (>100%)' },
  { key: 'a_moderate_premium', label: '中度溢价 (30%~100%)' },
  { key: 'ah_near_parity', label: '平价区间 (-5%~30%)' },
  { key: 'h_premium', label: 'H股溢价 (<-5%)' },
] as const;

function SignalBadge({ signal, desc }: { signal: AhSpreadItem['arbitrage_signal']; desc: string | null }) {
  if (!signal || signal === 'unknown') {
    return <span className="text-2xs text-ink-muted">—</span>;
  }
  let cls = 'bg-surface-alt text-ink-secondary border-hair';
  if (signal === 'a_extreme_premium') {
    cls = 'bg-danger-bg text-danger border-danger/30';
  } else if (signal === 'a_moderate_premium') {
    cls = 'bg-warn-bg text-warn border-warn/30';
  } else if (signal === 'ah_near_parity') {
    cls = 'bg-brand-50 text-brand-700 border-brand-200';
  } else if (signal === 'h_premium') {
    cls = 'bg-success-bg text-success border-success/30';
  }

  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none ${cls}`}
      title={desc ?? ''}
    >
      {desc ?? signal}
    </span>
  );
}

export default function AhSpreadPanel({ data, loading, onRefresh }: Props) {
  const [signalFilter, setSignalFilter] = useState<string>('all');
  const [keyword, setKeyword] = useState<string>('');
  const [sortKey, setSortKey] = useState<'ah_premium_rate' | 'ah_ratio'>('ah_premium_rate');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const items = data?.items ?? [];
  const summary = data?.summary;
  const fx = data?.hkd_cny_fx ?? 0.915;
  const asOf = data?.as_of ?? '—';
  const isFallback = data?.source === 'local_cache';

  const filteredItems = useMemo(() => {
    let res = items;
    if (signalFilter !== 'all') {
      res = res.filter((it) => it.arbitrage_signal === signalFilter);
    }
    if (keyword.trim()) {
      const q = keyword.trim().toLowerCase();
      res = res.filter(
        (it) =>
          it.name.toLowerCase().includes(q) ||
          it.a_symbol.toLowerCase().includes(q) ||
          it.h_symbol.toLowerCase().includes(q) ||
          it.industry.toLowerCase().includes(q),
      );
    }
    return [...res].sort((a, b) => {
      const va = a[sortKey];
      const vb = b[sortKey];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return sortDir === 'asc' ? va - vb : vb - va;
    });
  }, [items, signalFilter, keyword, sortKey, sortDir]);

  const toggleSort = (key: 'ah_premium_rate' | 'ah_ratio') => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      {/* 头部与操作区 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">AH 股溢价与跨市场套利看板</h2>
          <span className="rounded bg-brand-50 px-1.5 py-0.5 text-2xs font-medium text-brand-700 border border-brand-200">
            港股通对标
          </span>
          <span className="text-2xs text-ink-muted">汇率参考: 1 HKD = {fx.toFixed(4)} CNY</span>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-2xs text-ink-muted">
          <span>快照: {asOf}</span>
          {onRefresh && (
            <button
              onClick={onRefresh}
              disabled={loading}
              className="text-2xs text-brand-600 hover:underline disabled:opacity-50"
            >
              {loading ? '…' : '刷新'}
            </button>
          )}
        </div>
      </div>

      {/* 降级警示 */}
      {isFallback && (
        <div className="border-b border-warn/30 bg-warn-bg px-4 py-1.5 text-2xs text-warn">
          实时外盘行情源不可达，已如实回退本地截面数据，价格与溢价率可能存在时滞。
        </div>
      )}

      {/* 顶部统计摘要 KPI 栏 */}
      {summary && (
        <div className="grid grid-cols-2 gap-2 border-b border-hair/50 bg-surface-alt/40 p-3 sm:grid-cols-5">
          <div className="rounded border border-hair bg-surface p-2">
            <div className="text-2xs text-ink-muted">纳入监控对数</div>
            <div className="mt-0.5 num text-sm font-semibold text-ink">
              {summary.valid_pairs} <span className="text-2xs font-normal text-ink-muted">/ {summary.total_pairs}</span>
            </div>
          </div>
          <div className="rounded border border-hair bg-surface p-2">
            <div className="text-2xs text-ink-muted">AH 平均溢价率</div>
            <div className="mt-0.5 num text-sm font-semibold text-danger">
              {summary.avg_premium_rate != null ? `+${summary.avg_premium_rate.toFixed(1)}%` : '—'}
            </div>
          </div>
          <div className="rounded border border-hair bg-surface p-2">
            <div className="text-2xs text-ink-muted">最高溢价标的</div>
            <div className="mt-0.5 truncate text-xs font-semibold text-danger" title={summary.max_premium_stock ?? ''}>
              {summary.max_premium_stock ?? '—'}
            </div>
          </div>
          <div className="rounded border border-hair bg-surface p-2">
            <div className="text-2xs text-ink-muted">最低溢价 / 折价标的</div>
            <div className="mt-0.5 truncate text-xs font-semibold text-brand-600" title={summary.min_premium_stock ?? ''}>
              {summary.min_premium_stock ?? '—'}
            </div>
          </div>
          <div className="rounded border border-hair bg-surface p-2">
            <div className="text-2xs text-ink-muted">套利策略提示</div>
            <div className="mt-0.5 text-2xs text-ink-secondary">
              A股高溢价时关注做空A/做多H配对
            </div>
          </div>
        </div>
      )}

      {/* 筛选与搜索工具条 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair/50 px-4 py-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {SIGNAL_FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              onClick={() => setSignalFilter(opt.key)}
              className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                signalFilter === opt.key
                  ? 'bg-brand-500 font-medium text-white shadow-xs'
                  : 'bg-surface-alt text-ink-secondary hover:bg-surface-sunken'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="搜索标的 / 代码 / 行业"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            className="w-40 rounded border border-hair bg-surface px-2 py-0.5 text-2xs outline-none focus:border-brand-300"
          />
        </div>
      </div>

      {/* 数据表格 */}
      <div className="min-w-0 flex-1 overflow-x-auto p-0">
        <table className="quant-table dense w-full">
          <thead>
            <tr>
              <th>标的名称 / 行业</th>
              <th className="text-right">A股最新价</th>
              <th className="text-right">A股涨跌</th>
              <th className="text-right">H股最新(HKD)</th>
              <th className="text-right">H股折合(CNY)</th>
              <th className="text-right">H股涨跌</th>
              <th
                className="cursor-pointer text-right hover:text-brand-600"
                onClick={() => toggleSort('ah_premium_rate')}
                title="点击按 AH 溢价率排序"
              >
                AH 溢价率 {sortKey === 'ah_premium_rate' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
              </th>
              <th className="text-right">H/A 折价率</th>
              <th
                className="cursor-pointer text-right hover:text-brand-600"
                onClick={() => toggleSort('ah_ratio')}
                title="点击按 AH 比价 (A/H) 排序"
              >
                比价(A/H) {sortKey === 'ah_ratio' ? (sortDir === 'desc' ? '↓' : '↑') : ''}
              </th>
              <th className="text-center">量化套利信号</th>
              <th className="text-center">跨市场分析</th>
            </tr>
          </thead>
          <tbody data-testid="ah-spread-tbody">
            {filteredItems.length > 0 ? (
              filteredItems.map((pair) => (
                <tr key={`${pair.a_symbol}-${pair.h_symbol}`} className="hover:bg-surface-alt">
                  <td>
                    <div className="flex flex-col">
                      <span className="text-xs font-medium text-ink">{pair.name}</span>
                      <span className="text-[10px] text-ink-muted">{pair.industry}</span>
                    </div>
                  </td>
                  <td className="num text-right text-xs text-ink">
                    {pair.a_price != null ? `¥${fmtNum(pair.a_price)}` : '—'}
                  </td>
                  <td className="text-right">
                    <span className={`num text-xs ${pctClass(pair.a_pct)}`}>{fmtPct(pair.a_pct)}</span>
                  </td>
                  <td className="num text-right text-xs text-ink">
                    {pair.h_price != null ? `HK$${fmtNum(pair.h_price)}` : '—'}
                  </td>
                  <td className="num text-right text-xs text-ink-secondary">
                    {pair.h_price_cny != null ? `¥${fmtNum(pair.h_price_cny)}` : '—'}
                  </td>
                  <td className="text-right">
                    <span className={`num text-xs ${pctClass(pair.h_pct)}`}>{fmtPct(pair.h_pct)}</span>
                  </td>
                  <td className="num text-right text-xs font-semibold">
                    {pair.ah_premium_rate != null ? (
                      <span className={pair.ah_premium_rate >= 0 ? 't-up' : 't-down'}>
                        {pair.ah_premium_rate >= 0 ? '+' : ''}
                        {pair.ah_premium_rate.toFixed(2)}%
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="num text-right text-xs text-ink-muted">
                    {pair.ha_discount_rate != null ? `${pair.ha_discount_rate.toFixed(2)}%` : '—'}
                  </td>
                  <td className="num text-right text-xs text-ink-secondary">
                    {pair.ah_ratio != null ? pair.ah_ratio.toFixed(3) : '—'}
                  </td>
                  <td className="text-center">
                    <SignalBadge signal={pair.arbitrage_signal} desc={pair.signal_desc} />
                  </td>
                  <td className="whitespace-nowrap text-center text-2xs">
                    <div className="flex items-center justify-center gap-1">
                      <Link
                        to={`/stock/${pair.a_symbol}`}
                        className="rounded border border-brand-200 bg-brand-50 px-1 py-0.5 text-brand-600 hover:bg-brand-100"
                        title="查看 A 股详情"
                      >
                        A股
                      </Link>
                      <Link
                        to={`/stock/${pair.h_symbol}`}
                        className="rounded border border-hair bg-surface-alt px-1 py-0.5 text-ink-secondary hover:bg-surface-sunken"
                        title="查看 H 股详情"
                      >
                        H股
                      </Link>
                    </div>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={11}>
                  <PanelEmpty text={loading ? 'AH 溢价计算中…' : '无符合条件的 AH 标的'} minH="min-h-[140px]" />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
