/**
 * 个股财务排雷与破产预警卡片 (FinancialHealthCard)。
 * 
 * 基于真实季度财报披露与 Altman 修正模型：
 * 1. 综合健康得分 (0~100) 与风险分区 (安全 / 关注 / 高危)
 * 2. Altman Z-Score 破产预警指数
 * 3. 财务红线排雷标签与优势亮点
 * 4. 偿债、盈利、毛利、成长、稳定性五维诊断
 * 5. 真实季度财务指标对比与诚实降级
 */
import { useState } from 'react';
import type { FinancialHealthBlock } from '@/types/stock';
import { SectionCard, PanelEmpty, Modal } from '@/components/ui';
import { fmtAmountYi, fmtPct } from '@/utils/format';

interface Props {
  block?: FinancialHealthBlock;
}

export function FinancialHealthCard({ block }: Props) {
  const [showDetailModal, setShowDetailModal] = useState(false);

  if (!block || block.status === 'unavailable') {
    return (
      <SectionCard title="财务排雷与破产预警" bodyClassName="px-3 py-2.5">
        <PanelEmpty
          minH="min-h-[160px]"
          text={block?.reason || '暂无该标的历史财报披露数据'}
        />
      </SectionCard>
    );
  }

  const {
    score = 0,
    risk_level = 'safe',
    risk_label = '正常',
    altman_assessment,
    dimensions = [],
    red_flags = [],
    positive_flags = [],
    metrics,
  } = block;

  const badgeClass =
    risk_level === 'safe'
      ? 'bg-up-bg text-up border-up/30'
      : risk_level === 'moderate'
      ? 'bg-warn-bg text-warn border-warn/30'
      : 'bg-down-bg text-down border-down/30';

  const altmanZoneBadge =
    altman_assessment?.zone === 'safe'
      ? 'text-up font-semibold'
      : altman_assessment?.zone === 'grey'
      ? 'text-warn font-semibold'
      : 'text-down font-bold';

  return (
    <>
      <SectionCard
        title="财务排雷与破产预警"
        action={
          <button
            type="button"
            onClick={() => setShowDetailModal(true)}
            className="text-2xs text-brand-500 hover:text-brand-600 font-medium"
          >
            排雷明细 &gt;
          </button>
        }
        bodyClassName="px-3 py-2.5"
      >
        <div className="space-y-2.5 text-xs">
          {/* 综合健康得分与风险等级条 */}
          <div className="flex items-center justify-between rounded-md border border-hair bg-surface-alt/40 p-2">
            <div>
              <div className="text-2xs text-ink-muted">健康综合评分</div>
              <div className="flex items-baseline gap-1 mt-0.5">
                <span className="font-mono text-xl font-bold text-ink">
                  {score != null ? score.toFixed(1) : '--'}
                </span>
                <span className="text-2xs text-ink-muted">/ 100</span>
              </div>
            </div>
            <div className="text-right">
              <span
                className={`inline-block rounded border px-2 py-0.5 text-2xs font-semibold ${badgeClass}`}
              >
                {risk_label}
              </span>
              <div className="mt-1 text-2xs text-ink-muted font-mono">
                Altman Z: <span className={altmanZoneBadge}>{altman_assessment?.score ?? '--'}</span>
              </div>
            </div>
          </div>

          {/* 财务红线排雷与优势标签 */}
          {red_flags.length > 0 ? (
            <div className="space-y-1.5">
              {red_flags.map((flag, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-1.5 rounded border border-down/30 bg-down-bg/60 p-1.5 text-2xs"
                >
                  <span className="mt-0.5 text-down font-bold">⚠️</span>
                  <div>
                    <span className="font-semibold text-down">{flag.title}：</span>
                    <span className="text-ink-secondary">{flag.desc}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-center gap-1.5 rounded border border-up/20 bg-up-bg/40 px-2 py-1 text-2xs text-up">
              <span>✓</span>
              <span>未检测到严重财务退市或资不抵债红线，财务结构稳健</span>
            </div>
          )}

          {/* 5维健康诊断微型条形图 */}
          <div className="space-y-1.5 pt-0.5">
            {dimensions.map((dim) => {
              const barColor =
                dim.status === 'healthy'
                  ? 'bg-up'
                  : dim.status === 'warning'
                  ? 'bg-warn'
                  : 'bg-down';
              return (
                <div key={dim.key} className="space-y-0.5">
                  <div className="flex justify-between text-2xs">
                    <span className="text-ink-secondary">{dim.name}</span>
                    <span className="font-mono text-ink font-medium">
                      {dim.score.toFixed(0)}分
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${barColor}`}
                      style={{ width: `${Math.min(100, Math.max(5, dim.score))}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>

          {/* 核心指标底栏 (等宽数字对齐) */}
          {metrics && (
            <div className="grid grid-cols-3 gap-1 rounded border border-hair bg-surface-sunken/40 p-1.5 text-center text-2xs">
              <div>
                <span className="text-ink-muted">加权 ROE</span>
                <div className="font-mono font-medium text-ink">
                  {metrics.roe != null ? `${metrics.roe.toFixed(2)}%` : '--'}
                </div>
              </div>
              <div>
                <span className="text-ink-muted">销售毛利率</span>
                <div className="font-mono font-medium text-ink">
                  {metrics.gross_margin != null ? `${metrics.gross_margin.toFixed(2)}%` : '--'}
                </div>
              </div>
              <div>
                <span className="text-ink-muted">利润同比</span>
                <div
                  className={`font-mono font-medium ${
                    metrics.profit_yoy != null
                      ? metrics.profit_yoy > 0
                        ? 'text-up'
                        : 'text-down'
                      : 'text-ink'
                  }`}
                >
                  {metrics.profit_yoy != null ? fmtPct(metrics.profit_yoy) : '--'}
                </div>
              </div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* 财务排雷明细弹窗 */}
      <Modal
        open={showDetailModal}
        onClose={() => setShowDetailModal(false)}
        title="财务健康度与排雷诊断明细"
      >
        <div className="space-y-4 p-4 text-xs">
          {/* 模型口径说明 */}
          <div className="rounded border border-hair bg-surface-alt p-3">
            <div className="font-semibold text-ink">
              {altman_assessment?.formula || 'Altman 修正排雷模型'}
            </div>
            <div className="mt-1 text-2xs text-ink-secondary leading-relaxed">
              严格基于上市公司历期真实审计财报，穿透偿债安全垫、盈利持续性、毛利稳定性与增收不增利背离特征。
              安全区分界线：Altman &gt; {altman_assessment?.safe_threshold ?? 2.9}；高危预警分界线：Altman &lt; {altman_assessment?.distress_threshold ?? 1.8}。
            </div>
          </div>

          {/* 风险红线列表 */}
          {red_flags.length > 0 && (
            <div>
              <div className="font-semibold text-down mb-2">🚨 触碰排雷预警项 ({red_flags.length})</div>
              <div className="space-y-2">
                {red_flags.map((flag, idx) => (
                  <div
                    key={idx}
                    className="rounded border border-down/30 bg-down-bg/40 p-2.5 text-2xs"
                  >
                    <div className="font-bold text-down">{flag.title}</div>
                    <div className="mt-1 text-ink-secondary">{flag.desc}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 优势亮点 */}
          {positive_flags.length > 0 && (
            <div>
              <div className="font-semibold text-up mb-2">⭐ 财务护城河优势 ({positive_flags.length})</div>
              <div className="space-y-1.5">
                {positive_flags.map((flag, idx) => (
                  <div
                    key={idx}
                    className="rounded border border-up/20 bg-up-bg/30 p-2 text-2xs"
                  >
                    <div className="font-bold text-up">{flag.title}</div>
                    <div className="mt-0.5 text-ink-secondary">{flag.desc}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 五大维度详细评估 */}
          <div>
            <div className="font-semibold text-ink mb-2">📊 五大核心维度诊断</div>
            <div className="space-y-2">
              {dimensions.map((dim) => (
                <div
                  key={dim.key}
                  className="rounded border border-hair bg-surface-alt/40 p-2 text-2xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-ink">{dim.name}</span>
                    <span className="font-mono font-bold text-ink">{dim.score.toFixed(1)} 分</span>
                  </div>
                  <div className="mt-1 text-ink-muted">{dim.description}</div>
                </div>
              ))}
            </div>
          </div>

          {/* 最新审计指标 */}
          {metrics && (
            <div className="rounded border border-hair bg-surface-sunken p-2.5 text-2xs space-y-1">
              <div className="font-semibold text-ink mb-1">
                最新披露报告期：{metrics.latest_period || '--'}
              </div>
              <div className="grid grid-cols-2 gap-2 text-ink-secondary">
                <div>营业总收入: <span className="font-mono text-ink">{fmtAmountYi(metrics.revenue_yi)}</span></div>
                <div>归母净利润: <span className="font-mono text-ink">{fmtAmountYi(metrics.net_profit_yi)}</span></div>
                <div>加权 ROE: <span className="font-mono text-ink">{metrics.roe != null ? `${metrics.roe.toFixed(2)}%` : '--'}</span></div>
                <div>销售毛利率: <span className="font-mono text-ink">{metrics.gross_margin != null ? `${metrics.gross_margin.toFixed(2)}%` : '--'}</span></div>
                <div>每股净资产: <span className="font-mono text-ink">{metrics.bps != null ? `${metrics.bps.toFixed(2)} 元` : '--'}</span></div>
                <div>营收同比: <span className="font-mono text-ink">{metrics.revenue_yoy != null ? fmtPct(metrics.revenue_yoy) : '--'}</span></div>
              </div>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
