/**
 * 连板天梯榜单与炸板池卡片（LimitLadderCard）——对标 TSP limit-ladder 标准。
 *
 * 功能特性：
 * 1. 梯队阶梯阵列：按板数（5板+、4板、3板、2板、首板）从高到低分层展示；
 * 2. 标的信息丰富：股票代码、名称、现价、涨跌幅、连板数徽章、换手率、成交额；
 * 3. 炸板预警区：盘中曾触及涨停但现价炸板打开的标的池，直观监测短线分歧度；
 * 4. 统计概览：涨停总数、炸板总数、炸板率、最高板高度；
 * 5. 点击联动：点击任意股票直接跳转到 /stock/{symbol} 详情。
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { marketApi, type LimitLadderData } from '@/api/market';
import { fmtPct, pctClass } from '@/utils/format';

export default function LimitLadderCard() {
  const navigate = useNavigate();
  const [data, setData] = useState<LimitLadderData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'ladder' | 'broken'>('ladder');

  const fetchLadder = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await marketApi.limitLadder('up');
      setData(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '加载连板天梯数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchLadder();
    const timer = setInterval(() => {
      void fetchLadder();
    }, 30000);
    return () => clearInterval(timer);
  }, []);

  const totalZt = data?.total_limit_up ?? 0;
  const totalBroken = data?.total_broken ?? 0;
  const maxBoards = data?.max_boards ?? 0;
  const brokenRate = data?.broken_rate ?? 0;
  const tiers = data?.tiers ?? [];
  const brokenStocks = data?.broken_stocks ?? [];

  return (
    <div className="flex flex-col h-[480px] rounded-lg border border-hair bg-surface p-3.5 shadow-2xs [content-visibility:auto] [contain-intrinsic-size:auto_480px]">
      {/* 头部与操作栏 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-bold text-ink">A 股连板天梯全景阵列</h3>
          <span className="rounded bg-surface-alt px-1.5 py-0.5 text-3xs font-medium text-ink-muted border border-hair">
            短线接力 · 梯队博弈
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setActiveTab('ladder')}
            className={`rounded px-2.5 py-0.5 text-2xs font-medium transition-colors ${
              activeTab === 'ladder'
                ? 'bg-brand-50 text-brand-700 font-semibold border border-brand-200'
                : 'text-ink-muted hover:text-ink'
            }`}
          >
            连板梯队 ({totalZt})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('broken')}
            className={`rounded px-2.5 py-0.5 text-2xs font-medium transition-colors ${
              activeTab === 'broken'
                ? 'bg-amber-50 text-amber-700 font-semibold border border-amber-200'
                : 'text-ink-muted hover:text-ink'
            }`}
          >
            炸板池 ({totalBroken})
          </button>
          <button
            type="button"
            onClick={() => void fetchLadder()}
            className="ml-1 text-3xs text-brand-600 hover:text-brand-700 font-medium"
          >
            {loading ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      {/* 核心指标统计带 */}
      <div className="grid grid-cols-4 gap-2 border-b border-hair py-2 text-center text-2xs">
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">涨停家数</div>
          <div className="num font-bold text-up text-xs mt-0.5">{totalZt} <span className="text-3xs font-normal">只</span></div>
        </div>
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">炸板家数</div>
          <div className="num font-bold text-ink-secondary text-xs mt-0.5">{totalBroken} <span className="text-3xs font-normal">只</span></div>
        </div>
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">炸板率</div>
          <div className={`num font-bold text-xs mt-0.5 ${brokenRate > 35 ? 'text-down' : brokenRate > 20 ? 'text-warn' : 'text-up'}`}>
            {brokenRate.toFixed(1)}%
          </div>
        </div>
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">最高连板</div>
          <div className="num font-bold text-amber-600 text-xs mt-0.5">{maxBoards > 0 ? `${maxBoards}板` : '—'}</div>
        </div>
      </div>

      {/* 内容区域 */}
      <div className="mt-2.5 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable] pr-1">
        {loading && !data && (
          <div className="flex h-36 items-center justify-center text-xs text-ink-muted">
            连板天梯数据计算中…
          </div>
        )}

        {error && !data && (
          <div className="flex h-36 flex-col items-center justify-center gap-1.5 text-xs text-danger">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => void fetchLadder()}
              className="rounded bg-surface-alt px-2.5 py-1 text-2xs text-ink hover:bg-surface-sunken"
            >
              重试
            </button>
          </div>
        )}

        {!loading && data && activeTab === 'ladder' && (
          <div className="space-y-3">
            {tiers.map((tier) => (
              <div key={tier.boards} className="rounded-md border border-hair bg-surface-alt/25 p-2">
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`rounded px-1.5 py-0.2 text-3xs font-bold ${
                        tier.boards >= 5
                          ? 'bg-amber-500 text-white'
                          : tier.boards >= 3
                          ? 'bg-up text-white'
                          : tier.boards === 2
                          ? 'bg-brand-500 text-white'
                          : 'bg-surface-sunken text-ink-secondary'
                      }`}
                    >
                      {tier.title}
                    </span>
                    <span className="text-3xs text-ink-muted">共 {tier.count} 只标的</span>
                  </div>
                </div>

                {tier.stocks.length > 0 ? (
                  <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                    {tier.stocks.map((st) => (
                      <button
                        key={st.symbol}
                        type="button"
                        onClick={() => navigate(`/stock/${st.symbol}`)}
                        className="group flex flex-col justify-between rounded border border-hair bg-surface p-1.5 text-left transition-all hover:border-brand-300 hover:shadow-2xs"
                      >
                        <div className="flex items-baseline justify-between gap-1">
                          <span className="font-semibold text-2xs text-ink group-hover:text-brand-600 truncate">
                            {st.name}
                          </span>
                          <span className="num text-3xs font-mono text-ink-muted">
                            {st.symbol.split('.')[0]}
                          </span>
                        </div>
                        <div className="mt-1 flex items-baseline justify-between text-3xs font-mono">
                          <span className={`font-bold ${pctClass(st.pct)}`}>
                            ￥{st.price.toFixed(2)}
                          </span>
                          <span className={`font-semibold ${pctClass(st.pct)}`}>
                            {fmtPct(st.pct)}
                          </span>
                        </div>
                        <div className="mt-0.5 flex items-center justify-between text-3xs text-ink-muted">
                          <span>换手 {st.turnover ? `${st.turnover.toFixed(1)}%` : '—'}</span>
                          <span>{(st.amount / 100000000).toFixed(2)}亿</span>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="py-2 text-center text-3xs text-ink-muted">本梯队今日暂无标的晋级</div>
                )}
              </div>
            ))}
          </div>
        )}

        {!loading && data && activeTab === 'broken' && (
          <div className="space-y-2">
            {brokenStocks.length > 0 ? (
              <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {brokenStocks.map((st) => (
                  <button
                    key={st.symbol}
                    type="button"
                    onClick={() => navigate(`/stock/${st.symbol}`)}
                    className="group flex flex-col justify-between rounded border border-hair bg-surface p-1.5 text-left transition-all hover:border-amber-300 hover:shadow-2xs"
                  >
                    <div className="flex items-baseline justify-between gap-1">
                      <span className="font-semibold text-2xs text-ink group-hover:text-amber-600 truncate">
                        {st.name}
                      </span>
                      <span className="rounded bg-purple-50 px-1 text-3xs font-bold text-purple-700 border border-purple-200">
                        炸
                      </span>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between text-3xs font-mono">
                      <span className="font-medium text-ink">￥{st.price.toFixed(2)}</span>
                      <span className={`font-semibold ${pctClass(st.pct)}`}>{fmtPct(st.pct)}</span>
                    </div>
                    <div className="mt-0.5 flex items-center justify-between text-3xs text-ink-muted">
                      <span>最高 ￥{st.high ? st.high.toFixed(2) : '—'}</span>
                      <span>{(st.amount / 100000000).toFixed(2)}亿</span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="py-8 text-center text-xs text-ink-muted">
                今日暂无炸板标的，封板质量优良
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
