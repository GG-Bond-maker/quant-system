/**
 * 选股中心 · 股票列表（全市场）。
 *
 * 位置：Screener 主区 12 栅格之下，全宽，与 ETF 中心的「ETF 列表」位置完全对应。
 *
 * 与 Alpha 榜的区别（必须严格分清，不要混用）：
 * - Alpha 榜 = 模型预测 Top-N，客户端排序（数据量小、一次性返回）；
 * - 股票列表 = 全市场在册证券（约 1157 只），服务端筛选 + 排序 + 分页，
 *   前端只维护排序 / 筛选状态并回传给接口，**绝不复用 Alpha 榜的 sortRows**
 *   （否则只会排当前页，用户会以为是全市场排序，属于误导）。
 *
 * 数据口径红线：
 * - 所有数值缺失一律显示「—」，禁止用 0 / 占位值兜底；
 * - 实时行情源不可达时后端返回 degraded=true，此处如实提示已回退日终截面；
 * - 总市值 / 流通市值来自外部实时行情快照，本地无股本数据，不离线推算。
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { screenerApi } from '@/api/screener';
import { useWatchlistStore, DEFAULT_GROUP } from '@/stores/useWatchlistStore';
import { PanelEmpty, Pager, SortHeader } from '@/components/ui';
import type { StockListResult } from '@/types/p1';
import { fmtAmountYi, fmtNum, fmtPct, pctClass } from '@/utils/format';

/* ==================== 常量 ==================== */
const PAGE_SIZE = 20;

/** 板块 code -> 中文名（与后端 board 取值一致） */
const BOARD_LABEL: Record<string, string> = {
  main: '主板',
  chinext_star: '创业/科创',
  bse: '北交所',
};

/** 排序状态；null = 未启用排序（sort 传空串，走后端默认 code 升序） */
type SortState = { key: string; dir: 'asc' | 'desc' } | null;

/** 「总市值(亿)」列 ⓘ 悬浮说明（口径同源声明） */
const CAP_HINT = '与 ETF 中心的「规模」列同源（外部实时行情快照），本地无股本数据不可离线推算';

/** 列头 ⓘ 小图标：hover 显示口径说明，不引入图标依赖 */
function InfoHint({ text }: { text: string }) {
  return (
    <span title={text}
      className="inline-flex h-3 w-3 shrink-0 cursor-help items-center justify-center rounded-full border border-slate-300 text-[8px] leading-none text-ink-muted">
      ⓘ
    </span>
  );
}

/** 统一 Card 壳（与 ETF 中心 / Alpha 榜一致的头部 border-b + extra） */
function Card({ title, extra, children, bodyCls = '' }: {
  title: React.ReactNode; extra?: React.ReactNode; children: React.ReactNode; bodyCls?: string;
}) {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
        {extra}
      </div>
      <div className={`min-w-0 flex-1 ${bodyCls}`}>{children}</div>
    </div>
  );
}

/* ==================== 主组件 ==================== */
export default function StockList({ board }: { board: string }) {
  /** 筛选：行业 / 关键词 / 剔除 ST（board 由父组件板块 Tab 联动传入） */
  const [industry, setIndustry] = useState('all');
  const [qInput, setQInput] = useState('');
  const [kw, setKw] = useState('');
  const [excludeSt, setExcludeSt] = useState(false);
  /** 服务端排序状态；改变后必须回到第 1 页 */
  const [sort, setSort] = useState<SortState>(null);
  const [page, setPage] = useState(1);

  const [res, setRes] = useState<StockListResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { addTo, removeFrom, contains } = useWatchlistStore();

  /** 点击可排序列头：降序 → 升序 → 取消（回到后端默认顺序） */
  const toggleSort = (key: string) => {
    setPage(1);   // 排序结果整体变化，停留在旧页码没有意义
    setSort((cur) => {
      if (cur?.key !== key) return { key, dir: 'desc' };
      if (cur.dir === 'desc') return { key, dir: 'asc' };
      return null;
    });
  };

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setRes(await screenerApi.stocks({
        board,
        industry,
        page,
        page_size: PAGE_SIZE,
        sort: sort?.key ?? '',
        dir: sort?.dir ?? 'desc',
        exclude_st: excludeSt ? 1 : 0,
        ...(kw ? { q: kw } : {}),
        ...(refresh ? { refresh: 1 } : {}),
      }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '股票列表加载失败');
      setRes(null);
    } finally {
      setLoading(false);
    }
  }, [board, industry, kw, excludeSt, sort, page]);

  useEffect(() => { void load(); }, [load]);

  /** 改变排序或任一筛选条件 → 回到第 1 页（否则会停在一个不存在的页码上） */
  useEffect(() => { setPage(1); }, [board, industry, kw, excludeSt, sort]);

  const submitSearch = () => {
    setKw(qInput.trim().slice(0, 32));
    setPage(1);
  };

  const items = res?.items ?? [];
  const total = res?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const industries = res?.options?.industries ?? [];
  const tradeDate = res?.trade_date ?? '';
  const degraded = res?.degraded === true;
  const basisDesc = res?.basis_desc ?? '';
  /** 当前页市值两列全为 null：列头提示「暂无市值数据」，但仍允许点排序 */
  const noCap = items.length > 0
    && items.every((it) => it.total_cap_yi == null && it.float_cap_yi == null);

  return (
    <Card
      title={`股票列表（${total}）`}
      extra={
        <div className="flex flex-wrap items-center justify-end gap-2 text-2xs text-ink-muted">
          {basisDesc && (
            <span className="max-w-[24rem] truncate" title={basisDesc}>{basisDesc}</span>
          )}
          <select value={industry} onChange={(e) => { setIndustry(e.target.value); setPage(1); }}
            className="rounded border border-hair bg-white px-1.5 py-0.5 text-2xs text-ink">
            <option value="all">全部行业</option>
            {industries.map((ind) => <option key={ind} value={ind}>{ind}</option>)}
          </select>
          <input value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submitSearch(); }}
            placeholder="搜索代码 / 名称"
            className="w-32 rounded border border-hair bg-white px-2 py-0.5 text-2xs outline-none focus:border-brand-300" />
          <button onClick={submitSearch}
            className="rounded border border-hair bg-white px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
            搜索
          </button>
          <label className="flex cursor-pointer items-center gap-1 text-2xs text-ink-secondary">
            <input type="checkbox" checked={excludeSt}
              onChange={(e) => { setExcludeSt(e.target.checked); setPage(1); }}
              className="h-3 w-3 accent-brand-500" />
            剔除 ST
          </label>
          <button onClick={() => void load(true)} disabled={loading}
            title="强制重拉一次行情快照"
            className="text-2xs text-brand-600 hover:underline disabled:opacity-50">
            {loading ? '…' : '刷新'}
          </button>
        </div>
      }
      bodyCls="p-0"
    >
      {/* 降级提示：实时源不可达时如实披露，绝不假装数据是实时的 */}
      {degraded && (
        <div className="border-b border-amber-100 bg-amber-50 px-3 py-2 text-2xs leading-relaxed text-amber-700">
          实时行情源不可达，涨跌幅 / 成交额已回退本地日终截面（{tradeDate || '—'} 收盘）；总市值 / 流通市值暂无数据
        </div>
      )}
      {error && (
        <div className="border-b border-amber-100 bg-amber-50 px-3 py-2 text-2xs text-amber-700">{error}</div>
      )}

      <div className="overflow-x-auto">
        <table className="quant-table dense w-full">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>行业</th>
              <th>板块</th>
              <th className="text-right">最新价</th>
              <th className="text-right">
                <div className="flex items-center justify-end">
                  <SortHeader label="涨跌幅" sortKey="pct" sort={sort} onSort={toggleSort} align="right" />
                </div>
              </th>
              <th className="text-right">
                <div className="flex items-center justify-end gap-1">
                  <SortHeader label="总市值(亿)" sortKey="total_cap_yi" sort={sort} onSort={toggleSort} align="right" />
                  <InfoHint text={CAP_HINT} />
                  {noCap && <span className="text-2xs font-normal text-ink-muted">暂无市值数据</span>}
                </div>
              </th>
              <th className="text-right">
                <div className="flex items-center justify-end gap-1">
                  <SortHeader label="流通市值(亿)" sortKey="float_cap_yi" sort={sort} onSort={toggleSort} align="right" />
                  <InfoHint text={CAP_HINT} />
                </div>
              </th>
              <th className="text-right">
                <div className="flex items-center justify-end">
                  <SortHeader label="成交额" sortKey="amount" sort={sort} onSort={toggleSort} align="right" />
                </div>
              </th>
              <th className="text-center">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.length ? items.map((it) => (
              <tr key={it.symbol} className="hover:bg-slate-50">
                <td>
                  <Link to={`/stock/${it.symbol}`}
                    className="num text-brand-600 hover:underline">
                    {it.symbol.split('.')[0]}
                  </Link>
                </td>
                <td className="max-w-[10rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>
                  {it.name ?? '—'}
                </td>
                <td className="max-w-[7rem] truncate text-2xs text-ink-muted" title={it.industry ?? ''}>
                  {it.industry ?? '—'}
                </td>
                <td className="text-2xs text-ink-muted">
                  {(it.board != null ? BOARD_LABEL[it.board] : undefined) ?? it.board ?? '—'}
                </td>
                <td className="num text-right text-xs text-ink">{fmtNum(it.close)}</td>
                <td className="text-right">
                  <span className={`num ${pctClass(it.pct)}`}>{fmtPct(it.pct)}</span>
                </td>
                <td className="num text-right text-xs text-ink-secondary">
                  {it.total_cap_yi != null ? it.total_cap_yi.toFixed(2) : '—'}
                </td>
                <td className="num text-right text-xs text-ink-secondary">
                  {it.float_cap_yi != null ? it.float_cap_yi.toFixed(2) : '—'}
                </td>
                <td className="num text-right text-xs text-ink-secondary">{fmtAmountYi(it.amount_yi)}</td>
                <td className="whitespace-nowrap text-center">
                  <button onClick={() => {
                    if (contains(it.symbol)) removeFrom(DEFAULT_GROUP, it.symbol);
                    else addTo(DEFAULT_GROUP, it.symbol);
                  }}
                    title={contains(it.symbol) ? '移出自选' : '加入自选'}
                    className={`mr-1.5 text-xs ${
                      contains(it.symbol) ? 'text-amber-500' : 'text-ink-muted hover:text-amber-500'}`}>
                    {contains(it.symbol) ? '★' : '☆'}
                  </button>
                  <Link to={`/stock/${it.symbol}`} title="进入个股分析"
                    className="rounded border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-2xs text-brand-600 hover:bg-brand-100">
                    分析
                  </Link>
                </td>
              </tr>
            )) : (
              <tr>
                <td colSpan={10}>
                  <PanelEmpty text={loading ? '加载中…' : '无匹配结果'} minH="min-h-[140px]" />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Pager page={page} totalPages={totalPages} onChange={setPage} />
    </Card>
  );
}
