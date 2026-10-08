import type { ReportSynthesis } from '@/api/monitor';
import { SectionCard } from '@/components/ui';

interface Props {
  synthesis?: ReportSynthesis | null;
  tips?: string[];
}

export function AiSynthesisCard({ synthesis, tips = [] }: Props) {
  if (!synthesis && (!tips || tips.length === 0)) return null;

  return (
    <SectionCard
      title="AI 投委会智能综述与多维决策指引"
      action={
        <div className="flex items-center gap-2">
          <span className="rounded bg-brand-500/10 px-2 py-0.5 text-xs font-semibold text-brand-600 dark:text-brand-400">
            投研决策中枢 · 自动化提炼
          </span>
        </div>
      }
    >
      <div className="space-y-3.5">
        {/* 4 维智能综述看板 */}
        {synthesis && (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {/* 1. 宏观与微观情绪 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3 transition-colors hover:border-hair-strong">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink">
                  🌐 宏观流动性与微观情绪
                </span>
                <span className="text-3xs text-ink-muted">盘面全景</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-secondary">
                {synthesis.macro_summary}
              </p>
            </div>

            {/* 2. 业绩归因与超额透视 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3 transition-colors hover:border-hair-strong">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink">
                  🎯 业绩归因与 Alpha 透视
                </span>
                <span className="text-3xs text-ink-muted">Brinson 解构</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-secondary">
                {synthesis.attribution_takeaway}
              </p>
            </div>

            {/* 3. 因子健康与模型演化 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3 transition-colors hover:border-hair-strong">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink">
                  🧬 因子健康与模型状态
                </span>
                <span className="text-3xs text-ink-muted">漂移诊断</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-secondary">
                {synthesis.factor_health_verdict}
              </p>
            </div>

            {/* 4. 次日执行与风控指引 */}
            <div className="rounded-lg border border-hair bg-surface-alt p-3 transition-colors hover:border-hair-strong">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink">
                  ⚡ 次日调仓与风控执行
                </span>
                <span className="text-3xs text-ink-muted">算法拆单建议</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-secondary">
                {synthesis.execution_guidance}
              </p>
            </div>
          </div>
        )}

        {/* 风险提示列表 */}
        {tips.length > 0 && (
          <div className="rounded-lg border border-warn/30 bg-warn-bg p-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-warn">
              <span>⚠️ 投委会特别风控提示 ({tips.length} 项)</span>
            </div>
            <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-warn">
              {tips.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
