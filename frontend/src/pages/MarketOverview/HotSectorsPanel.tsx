/**
 * 热门板块排行榜：名称 | 涨跌比例条 | 涨跌幅 | 样本/主力量 | 领涨股。
 * 点击行跳转选股中心对应板块。
 */
import { useNavigate } from 'react-router-dom';
import type { SectorsBlock } from '@/types/stock';
import { fmtPct, pctClass } from '@/utils/format';

export default function HotSectorsPanel({ sectors, loading }: {
  sectors?: SectorsBlock; loading: boolean;
}) {
  const navigate = useNavigate();
  const ok = sectors?.status === 'ok';
  const items = sectors?.items ?? [];
  const maxAbs = Math.max(...items.map((s) => Math.abs(s.pct)), 0.01);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">热门板块</h2>
        <span className="text-2xs text-ink-muted">按涨跌幅排行</span>
      </div>
      <div className="min-w-0 flex-1 overflow-x-auto p-0">
        {ok && items.length ? (
          <table className="w-full text-xs">
            <thead><tr className="border-b border-hair text-2xs text-ink-muted">
              <th className="px-4 py-2 text-left font-normal">名称</th>
              <th className="py-2 text-left font-normal">板块涨幅</th>
              <th className="py-2 text-right font-normal">涨跌幅</th>
              <th className="py-2 text-right font-normal">样本数</th>
              <th className="px-4 py-2 text-right font-normal">领涨股</th>
            </tr></thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.name} title={`${s.name} · 板块均涨跌幅 ${fmtPct(s.pct)}`}
                  className="cursor-pointer border-b border-hair/50 hover:bg-slate-50"
                  onClick={() => navigate(`/screener?board=${encodeURIComponent(s.name)}`)}>
                  <td className="max-w-[7rem] truncate px-4 py-2 font-medium text-ink">{s.name}</td>
                  <td className="py-2 pr-3">
                    <div className="relative h-2.5 w-28 overflow-hidden rounded-sm bg-slate-100">
                      <div className={`absolute top-0 h-full ${s.pct >= 0 ? 'bg-up left-1/2' : 'bg-down right-1/2'}`}
                        style={{ width: `${s.pct === 0 ? 0 : Math.max((Math.abs(s.pct) / maxAbs) * 50, 2.5)}%` }} />
                      <div className="absolute left-1/2 top-0 h-full w-px bg-slate-300" />
                    </div>
                  </td>
                  <td className={`num py-2 text-right font-medium ${pctClass(s.pct)}`}>{fmtPct(s.pct)}</td>
                  <td className="num py-2 text-right text-ink-muted">{s.count ?? '—'}</td>
                  <td className="max-w-[8rem] truncate px-4 py-2 text-right text-ink-secondary" title={s.leader ?? ''}>
                    {s.leader ?? '—'}
                    {s.leader_pct != null && (
                      <span className={`num ml-1 text-2xs ${pctClass(s.leader_pct)}`}>{fmtPct(s.leader_pct)}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : loading ? (
          <div className="animate-pulse space-y-2.5 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-4 rounded bg-slate-100" style={{ width: `${92 - i * 7}%` }} />
            ))}
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-xs text-ink-muted">
            {sectors?.reason ?? '板块数据暂不可用'}
          </div>
        )}
      </div>
      {sectors?.note && (
        <div className="border-t border-hair px-4 py-1.5 text-2xs text-ink-muted">※ {sectors.note}</div>
      )}
    </div>
  );
}
