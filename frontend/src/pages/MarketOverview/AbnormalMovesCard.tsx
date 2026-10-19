/**
 * 全时段异动与交易所偏离值监控看板（100% 免费纯量化模型，借鉴 TSP 异动页）。
 *
 * 聚合三条时间线：
 * 1. 竞价异动（盘前）：集合竞价大幅高开/低开、爆量异动与一字涨停封单
 * 2. 分时异动（盘中）：快速拉升、快速跳水、炸板、撬板与放量突破
 * 3. 监管偏离（多日）：交易所异常波动监控（主板3日20%/双创3日30%门槛接近度）
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { AbnormalMovesData } from '@/api/market';
import { useApi } from '@/api/swr';
import { isMarketOpen } from '@/hooks/useMarketSession';

export default function AbnormalMovesCard() {
  const [timeline, setTimeline] = useState<'all' | 'auction' | 'intraday' | 'deviation'>('auction');
  const open = isMarketOpen();

  const { data, isValidating, mutate } = useApi<AbnormalMovesData>(
    '/api/v1/market/abnormal',
    { timeline: 'all', limit: 30 },
    { refreshInterval: open ? 15_000 : 0 }
  );

  const auctionList = data?.auction ?? [];
  const intradayList = data?.intraday ?? [];
  const deviationList = data?.deviation ?? [];

  const fmtPct = (val?: number | null) => {
    if (val == null || isNaN(val)) return '--';
    return `${val >= 0 ? '+' : ''}${val.toFixed(2)}%`;
  };

  const pctClass = (val?: number | null) => {
    if (val == null || isNaN(val) || val === 0) return 'text-ink-muted';
    return val > 0 ? 't-up' : 't-down';
  };

  const severityBadge = (sev: string) => {
    switch (sev) {
      case 'critical':
        return <span className="rounded bg-rose-500/10 px-1.5 py-0.5 text-2xs font-semibold text-rose-500 border border-rose-500/20 animate-pulse">严重超标</span>;
      case 'warning':
        return <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-2xs font-medium text-amber-500 border border-amber-500/20">预警临界</span>;
      case 'watch':
        return <span className="rounded bg-blue-500/10 px-1.5 py-0.5 text-2xs font-medium text-blue-500 border border-blue-500/20">观察阶段</span>;
      default:
        return <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-ink-muted">正常</span>;
    }
  };

  const criticalCount = deviationList.filter(
    (d) => d.severity === 'critical' || d.severity === 'warning',
  ).length;

  return (
    <div className="flex flex-col h-[480px] rounded-lg border border-hair bg-surface p-3.5 shadow-2xs [content-visibility:auto] [contain-intrinsic-size:auto_480px]">
      {/* 头部：标题与时间线切换 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-bold text-ink">全时段异动与监管偏离雷达</h3>
          <span className="rounded bg-surface-alt px-1.5 py-0.5 text-3xs font-medium text-ink-muted border border-hair">
            三线监控 · 偏离红线
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          <div className="flex rounded-md border border-hair bg-surface-alt/70 p-0.5 text-xs">
            <button
              type="button"
              onClick={() => setTimeline('auction')}
              className={`rounded px-2.5 py-0.5 text-2xs transition-colors ${
                timeline === 'auction' ? 'bg-surface font-semibold text-brand-600 shadow-xs' : 'text-ink-secondary hover:text-ink'
              }`}
            >
              竞价异动 ({auctionList.length})
            </button>
            <button
              type="button"
              onClick={() => setTimeline('intraday')}
              className={`rounded px-2.5 py-0.5 text-2xs transition-colors ${
                timeline === 'intraday' ? 'bg-surface font-semibold text-brand-600 shadow-xs' : 'text-ink-secondary hover:text-ink'
              }`}
            >
              盘中脉冲 ({intradayList.length})
            </button>
            <button
              type="button"
              onClick={() => setTimeline('deviation')}
              className={`rounded px-2.5 py-0.5 text-2xs transition-colors ${
                timeline === 'deviation' ? 'bg-surface font-semibold text-brand-600 shadow-xs' : 'text-ink-secondary hover:text-ink'
              }`}
            >
              交易所偏离风险 ({deviationList.length})
            </button>
          </div>

          <button
            type="button"
            onClick={() => void mutate()}
            disabled={isValidating}
            className="ml-1 text-3xs text-brand-600 hover:text-brand-700 font-medium disabled:opacity-50"
            title="手动刷新"
          >
            {isValidating ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      {/* 核心指标统计带（对齐连板天梯卡片） */}
      <div className="grid grid-cols-4 gap-2 border-b border-hair py-2 text-center text-2xs">
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">竞价异动</div>
          <div className="num font-bold text-xs mt-0.5 text-brand-600">{auctionList.length} <span className="text-3xs font-normal">只</span></div>
        </div>
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">盘中脉冲</div>
          <div className="num font-bold text-xs mt-0.5 text-amber-600">{intradayList.length} <span className="text-3xs font-normal">只</span></div>
        </div>
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">偏离监控</div>
          <div className="num font-bold text-xs mt-0.5 text-ink-secondary">{deviationList.length} <span className="text-3xs font-normal">只</span></div>
        </div>
        <div className="rounded bg-surface-alt/40 p-1.5">
          <div className="text-3xs text-ink-muted">超标/预警</div>
          <div className={`num font-bold text-xs mt-0.5 ${criticalCount > 0 ? 'text-danger font-extrabold' : 'text-success'}`}>
            {criticalCount} <span className="text-3xs font-normal">只</span>
          </div>
        </div>
      </div>

      {/* 内容表格区 */}
      <div className="mt-2.5 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable] pr-1">
        {timeline === 'auction' && (
          <table className="w-full text-left text-xs quant-table">
            <thead className="sticky top-0 bg-surface z-10 border-b border-hair">
              <tr>
                <th>标的</th>
                <th className="num-r">竞价价</th>
                <th className="num-r">昨收</th>
                <th className="num-r">竞价涨幅</th>
                <th>异动信号</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {auctionList.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-ink-muted">
                    暂未探测到竞价大幅异动标的
                  </td>
                </tr>
              ) : (
                auctionList.map((item) => (
                  <tr key={item.symbol} className="hover:bg-surface-alt/50">
                    <td>
                      <Link to={`/stock/${item.symbol}`} className="font-medium text-brand-600 hover:underline">
                        {item.name}
                      </Link>
                      <span className="ml-1 text-2xs text-ink-muted">{item.symbol}</span>
                    </td>
                    <td className="num-r font-mono">{item.open_price.toFixed(2)}</td>
                    <td className="num-r font-mono text-ink-muted">{item.pre_close.toFixed(2)}</td>
                    <td className={`num-r font-mono font-medium ${pctClass(item.change_pct)}`}>
                      {fmtPct(item.change_pct)}
                    </td>
                    <td>
                      <span className="rounded bg-brand-50 px-1.5 py-0.5 text-2xs text-brand-600 font-medium">
                        {item.signal_type === 'limit_up_bid' ? '一字涨停竞价' : item.signal_type === 'high_open' ? '高开放量' : '大幅低开'}
                      </span>
                    </td>
                    <td className="text-ink-secondary text-2xs">{item.description}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}

        {timeline === 'intraday' && (
          <table className="w-full text-left text-xs quant-table">
            <thead className="sticky top-0 bg-surface z-10 border-b border-hair">
              <tr>
                <th>标的</th>
                <th className="num-r">最新价</th>
                <th className="num-r">当日涨幅</th>
                <th className="num-r">分时拉升</th>
                <th>信号类别</th>
                <th>时点</th>
                <th>异动详情</th>
              </tr>
            </thead>
            <tbody>
              {intradayList.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-ink-muted">
                    暂无分时脉冲拉升或炸板信号
                  </td>
                </tr>
              ) : (
                intradayList.map((item, idx) => (
                  <tr key={`${item.symbol}-${idx}`} className="hover:bg-surface-alt/50">
                    <td>
                      <Link to={`/stock/${item.symbol}`} className="font-medium text-brand-600 hover:underline">
                        {item.name}
                      </Link>
                      <span className="ml-1 text-2xs text-ink-muted">{item.symbol}</span>
                    </td>
                    <td className="num-r font-mono">{item.price.toFixed(2)}</td>
                    <td className={`num-r font-mono font-medium ${pctClass(item.change_pct)}`}>
                      {fmtPct(item.change_pct)}
                    </td>
                    <td className={`num-r font-mono font-bold ${pctClass(item.surge_pct)}`}>
                      {fmtPct(item.surge_pct)}
                    </td>
                    <td>
                      <span className="rounded bg-surface-alt px-1.5 py-0.5 text-2xs text-ink-secondary font-medium">
                        {item.signal_type === 'rapid_surge' ? '快速拉升' : item.signal_type === 'limit_break' ? '涨停开板(炸板)' : '跳水下挫'}
                      </span>
                    </td>
                    <td className="text-ink-muted text-2xs font-mono">{item.timestamp}</td>
                    <td className="text-ink-secondary text-2xs">{item.description}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}

        {timeline === 'deviation' && (
          <table className="w-full text-left text-xs quant-table">
            <thead className="sticky top-0 bg-surface z-10 border-b border-hair">
              <tr>
                <th>标的</th>
                <th>板块</th>
                <th className="num-r">3日标的走势</th>
                <th className="num-r">3日偏离值</th>
                <th className="num-r">法定门槛</th>
                <th className="num-r">接近度</th>
                <th>风险等级</th>
                <th>监管风控建议</th>
              </tr>
            </thead>
            <tbody>
              {deviationList.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-ink-muted">
                    当前全市场标的偏离值均在安全阈值以内
                  </td>
                </tr>
              ) : (
                deviationList.map((item) => (
                  <tr key={item.symbol} className="hover:bg-surface-alt/50">
                    <td>
                      <Link to={`/stock/${item.symbol}`} className="font-medium text-brand-600 hover:underline">
                        {item.name}
                      </Link>
                      <span className="ml-1 text-2xs text-ink-muted">{item.symbol}</span>
                    </td>
                    <td className="text-ink-muted text-2xs">{item.board === 'main' ? '主板' : '双创板'}</td>
                    <td className={`num-r font-mono ${pctClass(item.cum_3d_pct)}`}>
                      {fmtPct(item.cum_3d_pct)}
                    </td>
                    <td className={`num-r font-mono font-semibold ${pctClass(item.deviation_3d_pct)}`}>
                      {fmtPct(item.deviation_3d_pct)}
                    </td>
                    <td className="num-r font-mono text-ink-muted">±{item.threshold_3d_pct.toFixed(0)}%</td>
                    <td className="num-r font-mono font-bold">
                      <span className={item.ratio_3d >= 0.7 ? 'text-amber-500' : 'text-ink-secondary'}>
                        {(item.ratio_3d * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td>{severityBadge(item.severity)}</td>
                    <td className="text-ink-secondary text-2xs">{item.detail}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
