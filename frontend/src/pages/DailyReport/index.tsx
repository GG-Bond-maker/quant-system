/**
 * AI 投研日报工作台（Daily Quant Report）。
 *
 * 对标主流量化机构每日盘后投研综述：
 * 1. 历史期数日历选择与归档切换；
 * 2. 市场微观流动性、情绪罗盘与成交额基尼集中度；
 * 3. Barra 核心风格因子日度收益与申万一级行业轮动热力；
 * 4. 组合 95% VaR (在险价值) 与多级风控暴露雷达；
 * 5. 凸优化次日调仓推演（含个股级 Almgren-Chriss 冲击滑点测算）；
 * 6. 一键重新计算研报与自动化多渠道 Webhook (飞书/企微/钉钉) 广播。
 */
import { useEffect, useState } from 'react';
import { PageHeader, SectionCard, MetricCard } from '@/components/ui';
import { reportApi } from '@/api/report';
import type { DailyReportItem } from '@/types/report';
import { fmtPct, pctClass } from '@/utils/format';

export default function DailyReport() {
  const [report, setReport] = useState<DailyReportItem | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'warn' | 'error' } | null>(null);
  const [activeTab, setActiveTab] = useState<'synthesis' | 'rebalance' | 'style' | 'full'>('synthesis');

  const fetchReport = (date?: string) => {
    setLoading(true);
    setMessage(null);
    reportApi.daily(date)
      .then((res) => {
        setReport(res.report);
        setHistory(res.history || []);
        if (res.report?.date && !selectedDate) {
          setSelectedDate(res.report.date);
        }
      })
      .catch((err) => {
        setMessage({
          text: err instanceof Error ? err.message : '获取投研日报失败',
          type: 'error',
        });
      })
      .finally(() => {
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchReport(selectedDate || undefined);
  }, [selectedDate]);

  const handleGenerate = () => {
    setGenerating(true);
    setMessage(null);
    reportApi.generate()
      .then((res) => {
        setReport(res);
        if (res.date) {
          setSelectedDate(res.date);
          if (!history.includes(res.date)) {
            setHistory([res.date, ...history]);
          }
        }
        setMessage({ text: `🎉 成功生成 ${res.date} 盘后投研日报！`, type: 'success' });
      })
      .catch((err) => {
        setMessage({ text: err instanceof Error ? err.message : '生成日报失败', type: 'error' });
      })
      .finally(() => {
        setGenerating(false);
      });
  };

  const handleDispatch = () => {
    setDispatching(true);
    setMessage(null);
    reportApi.dispatch()
      .then((res) => {
        setMessage({
          text: res.dispatched ? `✅ ${res.message}` : `ℹ️ ${res.message}`,
          type: res.dispatched ? 'success' : 'warn',
        });
      })
      .catch((err) => {
        setMessage({ text: err instanceof Error ? err.message : 'Webhook 推送失败', type: 'error' });
      })
      .finally(() => {
        setDispatching(false);
      });
  };

  const compass = report?.market_compass;
  const risk = report?.portfolio_risk;
  const signal = report?.signal_preview;
  const syn = report?.synthesis;
  const style = report?.style_factors;

  return (
    <div className="min-h-full space-y-4">
      {/* 顶栏控制台 */}
      <PageHeader
        title="AI 投研日报 (Daily Quant Report)"
        sub="聚合微观市场情绪、Barra风格演变、次日凸优化调仓推演与 95% 在险价值 (VaR) 监控"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {/* 历史期数选择 */}
            <div className="flex items-center gap-1.5">
              <label className="text-2xs text-ink-muted">期数：</label>
              <select
                value={selectedDate}
                onChange={(e) => setSelectedDate(e.target.value)}
                className="rounded border border-hair bg-surface px-2 py-1 text-xs text-ink focus:border-brand-500"
              >
                {history.length ? (
                  history.map((d) => (
                    <option key={d} value={d}>
                      {d} 期
                    </option>
                  ))
                ) : (
                  <option value="">最新期</option>
                )}
              </select>
            </div>

            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating}
              className="rounded bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-brand-500 transition-colors disabled:opacity-50"
            >
              {generating ? '正在计算中...' : '⚡ 重新生成今日研报'}
            </button>

            <button
              type="button"
              onClick={handleDispatch}
              disabled={dispatching}
              className="rounded border border-brand-300 bg-brand-50 px-3 py-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-100 transition-colors disabled:opacity-50"
            >
              {dispatching ? '正在推送...' : '📤 推送 Webhook (飞书/企微/钉钉)'}
            </button>
          </div>
        }
      />

      {/* 提示信息 */}
      {message && (
        <div
          className={`rounded border px-3.5 py-2 text-xs flex items-center justify-between ${
            message.type === 'success'
              ? 'border-success/30 bg-success-bg text-success'
              : message.type === 'warn'
              ? 'border-warn/30 bg-warn-bg text-warn'
              : 'border-danger/30 bg-danger-bg text-danger'
          }`}
        >
          <span>{message.text}</span>
          <button type="button" onClick={() => setMessage(null)} className="text-2xs underline">
            关闭
          </button>
        </div>
      )}

      {loading && !report && (
        <div className="py-24 text-center text-xs text-ink-muted animate-pulse">
          正在加载投研日报数据与风险归因模型...
        </div>
      )}

      {report && (
        <>
          {/* 四大核心量化指标卡片 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <MetricCard
              label="微观市场情绪罗盘"
              value={compass ? `${compass.sentiment_score} 分` : '--'}
              sub={compass ? `${compass.sentiment_label} · 腾落ADL: ${compass.adl_change > 0 ? '+' : ''}${compass.adl_change}` : '暂无数据'}
              tone={compass && compass.sentiment_score >= 60 ? 'positive' : compass && compass.sentiment_score <= 40 ? 'negative' : 'neutral'}
            />
            <MetricCard
              label="组合 95% Parametric VaR"
              value={risk ? `${risk.portfolio_var_95}%` : '--'}
              sub={risk ? `组合Beta: ${risk.portfolio_beta} · 评级[${risk.status}]` : '暂无数据'}
              tone={risk && risk.portfolio_var_95 < 2.5 ? 'positive' : 'negative'}
            />
            <MetricCard
              label="次日预期单边换手率"
              value={signal ? fmtPct(signal.expected_turnover * 100) : '--'}
              sub={signal ? `预估冲击滑点: ${signal.estimated_slippage_bps} bps` : '无调仓信号'}
              tone="neutral"
            />
            <MetricCard
              label="投委会研判策略基调"
              value={syn?.stance || '震荡防守'}
              sub={`报告生成于 ${report.generated_at.split('T')[1] || ''}`}
              tone={syn?.stance?.includes('多') ? 'positive' : syn?.stance?.includes('空') ? 'negative' : 'neutral'}
            />
          </div>

          {/* Tab 导航 */}
          <div className="flex border-b border-hair gap-4 text-xs font-medium">
            <button
              type="button"
              onClick={() => setActiveTab('synthesis')}
              className={`pb-2 transition-colors ${
                activeTab === 'synthesis'
                  ? 'border-b-2 border-brand-600 text-brand-600 font-semibold'
                  : 'text-ink-secondary hover:text-ink'
              }`}
            >
              🎯 投委会研判综述与情绪罗盘
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('rebalance')}
              className={`pb-2 transition-colors ${
                activeTab === 'rebalance'
                  ? 'border-b-2 border-brand-600 text-brand-600 font-semibold'
                  : 'text-ink-secondary hover:text-ink'
              }`}
            >
              ⚖️ 次日凸优化调仓推演
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('style')}
              className={`pb-2 transition-colors ${
                activeTab === 'style'
                  ? 'border-b-2 border-brand-600 text-brand-600 font-semibold'
                  : 'text-ink-secondary hover:text-ink'
              }`}
            >
              🌐 Barra 风格因子与行业轮动
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('full')}
              className={`pb-2 transition-colors ${
                activeTab === 'full'
                  ? 'border-b-2 border-brand-600 text-brand-600 font-semibold'
                  : 'text-ink-secondary hover:text-ink'
              }`}
            >
              📄 完整投研研报全文
            </button>
          </div>

          {/* Tab 1: 投委会研判与情绪罗盘 */}
          {activeTab === 'synthesis' && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <SectionCard title="AI 投委会核心研判结论">
                <div className="space-y-3 text-xs leading-relaxed">
                  <div className="rounded bg-brand-50/50 border border-brand-200/50 p-3">
                    <div className="text-2xs font-semibold text-brand-700 uppercase tracking-wider mb-1">
                      基准立场与宏观视点
                    </div>
                    <div className="text-ink">{syn?.market_view || '今日各宽基指数维持震荡整理态势，市场流动性分布平稳。'}</div>
                  </div>

                  <div className="rounded bg-warn-bg/50 border border-warn/30 p-3">
                    <div className="text-2xs font-semibold text-warn uppercase tracking-wider mb-1">
                      潜在风险预警
                    </div>
                    <div className="text-ink">{syn?.risk_alert || '关注高估值板块获利了结与海外宏观利率扰动。'}</div>
                  </div>

                  <div className="rounded bg-surface-alt border border-hair p-3">
                    <div className="text-2xs font-semibold text-ink-muted uppercase tracking-wider mb-1">
                      次日操作策略建议
                    </div>
                    <div className="text-ink font-medium">{syn?.action_recommendation || '建议维持既定风险平价多头敞口，轻微再平衡。'}</div>
                  </div>
                </div>
              </SectionCard>

              <SectionCard title="市场微观流动性与情绪罗盘">
                {compass ? (
                  <div className="space-y-3">
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="rounded bg-up-bg/60 border border-up/20 p-2">
                        <div className="text-2xs text-up font-medium">上涨标的</div>
                        <div className="text-base font-bold text-up num mt-0.5">{compass.up_count} 只</div>
                      </div>
                      <div className="rounded bg-surface-sunken border border-hair p-2">
                        <div className="text-2xs text-ink-muted">平盘标的</div>
                        <div className="text-base font-bold text-ink-muted num mt-0.5">{compass.flat_count} 只</div>
                      </div>
                      <div className="rounded bg-down-bg/60 border border-down/20 p-2">
                        <div className="text-2xs text-down font-medium">下跌标的</div>
                        <div className="text-base font-bold text-down num mt-0.5">{compass.down_count} 只</div>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded border border-hair p-2.5">
                        <div className="text-2xs text-ink-muted">涨停 / 跌停家数</div>
                        <div className="font-semibold text-ink mt-0.5">
                          <span className="text-up num">{compass.limit_up_count}</span> 涨停 /{' '}
                          <span className="text-down num">{compass.limit_down_count}</span> 跌停
                        </div>
                      </div>
                      <div className="rounded border border-hair p-2.5">
                        <div className="text-2xs text-ink-muted">成交额基尼集中度</div>
                        <div className="font-semibold text-ink mt-0.5 num">
                          {compass.turnover_gini}{' '}
                          <span className="text-2xs font-normal text-ink-muted">
                            {compass.turnover_gini > 0.65 ? '(显著抱团)' : '(分布均衡)'}
                          </span>
                        </div>
                      </div>
                    </div>

                    {compass.histogram && compass.histogram.length > 0 && (
                      <div>
                        <div className="text-2xs text-ink-muted mb-1.5 font-medium">8 分段截面涨跌幅分布直方图</div>
                        <div className="space-y-1">
                          {compass.histogram.map((bin) => {
                            const maxCnt = Math.max(...(compass.histogram?.map((b) => b.count) || [1]));
                            const pct = Math.min(100, (bin.count / maxCnt) * 100);
                            return (
                              <div key={bin.bin} className="flex items-center gap-2 text-2xs">
                                <span className="w-20 text-ink-muted text-right">{bin.bin}</span>
                                <div className="flex-1 bg-surface-sunken h-3.5 rounded overflow-hidden">
                                  <div
                                    className={`h-full ${bin.bin.startsWith('+') ? 'bg-up/70' : bin.bin.startsWith('-') ? 'bg-down/70' : 'bg-brand-500/70'}`}
                                    style={{ width: `${pct}%` }}
                                  />
                                </div>
                                <span className="w-10 num text-ink">{bin.count}</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="py-12 text-center text-xs text-ink-muted">暂无微观情绪数据</div>
                )}
              </SectionCard>
            </div>
          )}

          {/* Tab 2: 次日凸优化调仓推演 */}
          {activeTab === 'rebalance' && (
            <div className="space-y-4">
              <SectionCard
                title={`次日调仓执行推演（基准日 ${signal?.target_date || report.date}）`}
                action={
                  <div className="text-2xs text-ink-muted">
                    预计单边换手: <b className="text-brand-600 num">{signal ? fmtPct(signal.expected_turnover * 100) : '--'}</b> ·
                    Almgren-Chriss 冲击滑点: <b className="text-ink num">{signal?.estimated_slippage_bps ?? '--'} bps</b>
                  </div>
                }
              >
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* 买入/增持列表 */}
                  <div>
                    <h3 className="text-xs font-semibold text-up mb-2 flex items-center gap-1.5">
                      <span>🟢 建议增持 / 买入标的</span>
                      <span className="text-2xs font-normal text-ink-muted">({signal?.buys.length || 0} 只)</span>
                    </h3>
                    <div className="overflow-x-auto rounded border border-hair">
                      <table className="quant-table dense w-full">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th className="text-right">目标权重</th>
                            <th className="text-right">增持幅度</th>
                            <th className="text-right">预估滑点</th>
                          </tr>
                        </thead>
                        <tbody>
                          {signal?.buys.length ? (
                            signal.buys.map((b) => (
                              <tr key={b.symbol}>
                                <td className="font-semibold text-brand-600 num">{b.symbol}</td>
                                <td className="text-right num text-xs">{fmtPct(b.target_weight * 100)}</td>
                                <td className="text-right num text-xs text-up font-medium">+{fmtPct(b.delta_weight * 100)}</td>
                                <td className="text-right num text-2xs text-ink-muted">{b.slippage_bps ?? 4.5} bps</td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={4} className="py-4 text-center text-2xs text-ink-muted">
                                次日无增持调仓建议
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* 卖出/减持列表 */}
                  <div>
                    <h3 className="text-xs font-semibold text-down mb-2 flex items-center gap-1.5">
                      <span>🔴 建议减持 / 卖出标的</span>
                      <span className="text-2xs font-normal text-ink-muted">({signal?.sells.length || 0} 只)</span>
                    </h3>
                    <div className="overflow-x-auto rounded border border-hair">
                      <table className="quant-table dense w-full">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th className="text-right">原持权重</th>
                            <th className="text-right">减持幅度</th>
                            <th className="text-right">目标权重</th>
                          </tr>
                        </thead>
                        <tbody>
                          {signal?.sells.length ? (
                            signal.sells.map((s) => (
                              <tr key={s.symbol}>
                                <td className="font-semibold text-ink num">{s.symbol}</td>
                                <td className="text-right num text-xs">{fmtPct(s.current_weight * 100)}</td>
                                <td className="text-right num text-xs text-down font-medium">-{fmtPct(Math.abs(s.delta_weight) * 100)}</td>
                                <td className="text-right num text-2xs text-ink-muted">{fmtPct(s.target_weight * 100)}</td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={4} className="py-4 text-center text-2xs text-ink-muted">
                                次日无减持调仓建议
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </SectionCard>
            </div>
          )}

          {/* Tab 3: Barra 风格因子与行业轮动 */}
          {activeTab === 'style' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <SectionCard title="Barra 核心风格因子日度收益暴露">
                {style?.styles.length ? (
                  <div className="space-y-2">
                    {style.styles.map((s) => (
                      <div key={s.style} className="flex items-center justify-between text-xs py-1 border-b border-hair">
                        <span className="text-ink font-medium">{s.name} ({s.style})</span>
                        <span className={`num font-semibold ${pctClass(s.return * 100)}`}>
                          {s.return >= 0 ? `+${fmtPct(s.return * 100)}` : fmtPct(s.return * 100)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="py-8 text-center text-xs text-ink-muted">暂无风格因子收益数据</div>
                )}
              </SectionCard>

              <SectionCard title="申万一级行业日度轮动排行">
                <div className="space-y-4">
                  <div>
                    <div className="text-2xs text-up font-semibold mb-1.5">🔥 领涨行业 Top 3</div>
                    <div className="space-y-1">
                      {style?.top_industries.map((ind) => (
                        <div key={ind.industry} className="flex items-center justify-between text-xs py-0.5">
                          <span className="text-ink">{ind.industry}</span>
                          <span className="text-up num font-semibold">+{fmtPct(ind.return * 100)}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <div className="text-2xs text-down font-semibold mb-1.5">❄️ 领跌行业 Top 3</div>
                    <div className="space-y-1">
                      {style?.bottom_industries.map((ind) => (
                        <div key={ind.industry} className="flex items-center justify-between text-xs py-0.5">
                          <span className="text-ink">{ind.industry}</span>
                          <span className="text-down num font-semibold">{fmtPct(ind.return * 100)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </SectionCard>
            </div>
          )}

          {/* Tab 4: 完整研报全文 */}
          {activeTab === 'full' && (
            <SectionCard title="投研日报全文归档">
              <div className="prose prose-sm dark:prose-invert max-w-none space-y-4 text-xs leading-relaxed text-ink">
                {report.sections.map((sec) => (
                  <div key={sec.title} className="rounded border border-hair bg-surface p-3.5">
                    <h3 className="text-sm font-semibold text-brand-700 mb-2">{sec.title}</h3>
                    <ul className="space-y-1 text-xs text-ink-secondary list-disc pl-4">
                      {sec.lines.map((l, idx) => (
                        <li key={idx} className="leading-5">
                          {l.replace(/^[-\s*]+/, '')}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </SectionCard>
          )}
        </>
      )}
    </div>
  );
}
