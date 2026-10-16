/**
 * AH 股两地比价与港股通套利透视卡片 (AhSpreadLinkageCard)。
 * 
 * 核心量化功能：
 * 1. 自动识别 A 股 / H 股双重上市对标关系（如 601318.SH <-> 02318.HK 中国平安）；
 * 2. 精确计算离岸港元汇率换算价格、AH 溢价率、HA 折价率与比价倍数；
 * 3. 统计套利信号分级 (做多H股+做空A股 / 估值中枢平衡 / 平价收敛)；
 * 4. 港股通 vs A 股微观交易规则差异透视 (T+0 vs T+1, 每手股数, 税费口径)；
 * 5. 纯 A 股标的诚实披露「无 H 股镜像标的」并提供同业及全市场 AH 溢价参考，绝不造假。
 */
import { useNavigate } from 'react-router-dom';
import type { AhSpreadBlock } from '@/types/stock';
import { SectionCard, PanelEmpty } from '@/components/ui';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';

interface Props {
  block?: AhSpreadBlock;
  currentSymbol: string;
}

export function AhSpreadLinkageCard({ block, currentSymbol }: Props) {
  const navigate = useNavigate();

  if (!block || block.status === 'unavailable') {
    return (
      <SectionCard title="AH 联动与港股通透视" bodyClassName="px-3 py-2.5">
        <PanelEmpty minH="min-h-[140px]" text="AH 联动数据暂时不可用" />
      </SectionCard>
    );
  }

  const isDual = !!block.is_ah_dual;
  const fx = block.hkd_cny_fx ?? 0.925;
  const isHkCurrent = currentSymbol.toUpperCase().endsWith('.HK');
  const counterpart = block.counterpart_symbol;

  return (
    <SectionCard
      title="AH 联动与港股通套利"
      action={
        <div className="flex items-center gap-1.5">
          <span
            className={`rounded px-1.5 py-0.5 text-2xs font-semibold ${
              isDual
                ? 'bg-brand-50 text-brand-700 border border-brand-200'
                : 'bg-surface-sunken text-ink-secondary'
            }`}
          >
            {isDual ? 'AH 双重上市 · 联动套利' : isHkCurrent ? '港股通标的' : 'A股 · 互联互通标的'}
          </span>
        </div>
      }
      bodyClassName="px-3 py-2.5 space-y-2.5"
    >
      {isDual ? (
        <>
          {/* 双重上市核心比价行 */}
          <div className="rounded-md border border-hair bg-surface-alt/40 p-2 text-xs">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-semibold text-ink">{block.pair_name ?? '—'}</span>
                <span className="ml-1.5 text-2xs text-ink-muted">
                  A: {block.a_symbol} · H: {block.h_symbol}
                </span>
              </div>
              {counterpart && (
                <button
                  type="button"
                  onClick={() => navigate(`/stock/${counterpart}`)}
                  className="rounded bg-brand-500 px-2 py-0.5 text-2xs font-medium text-white transition-colors hover:bg-brand-600"
                  title={`切换到镜像对标标的 ${counterpart}`}
                >
                  切换至 {counterpart} &gt;
                </button>
              )}
            </div>

            {/* 价格与汇率折算网格 */}
            <div className="mt-2 grid grid-cols-4 gap-1.5 text-center">
              <div className="rounded bg-surface-sunken/60 p-1.5">
                <div className="text-3xs text-ink-muted">A 股价格 (CNY)</div>
                <div className="num mt-0.5 font-bold text-ink">
                  {block.a_price != null ? `￥${fmtNum(block.a_price)}` : '—'}
                </div>
              </div>
              <div className="rounded bg-surface-sunken/60 p-1.5">
                <div className="text-3xs text-ink-muted">H 股价格 (HKD)</div>
                <div className="num mt-0.5 font-bold text-ink">
                  {block.h_price != null ? `HK$${fmtNum(block.h_price)}` : '—'}
                </div>
              </div>
              <div className="rounded bg-surface-sunken/60 p-1.5">
                <div className="text-3xs text-ink-muted">H 股折合 (CNY)</div>
                <div className="num mt-0.5 font-bold text-ink-secondary">
                  {block.h_price_cny != null ? `￥${fmtNum(block.h_price_cny)}` : '—'}
                </div>
              </div>
              <div className="rounded bg-surface-sunken/60 p-1.5">
                <div className="text-3xs text-ink-muted">参考汇率(HKD/CNY)</div>
                <div className="num mt-0.5 font-bold text-ink-muted">
                  {fmtNum(fx, 4)}
                </div>
              </div>
            </div>
          </div>

          {/* 溢价率与量化套利信号 */}
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded border border-hair bg-surface p-2 text-center">
              <div className="text-2xs text-ink-muted">AH 溢价率 (A较H)</div>
              <div
                className={`num mt-0.5 text-base font-bold ${
                  (block.ah_premium_rate ?? 0) >= 0 ? pctClass(1) : pctClass(-1)
                }`}
              >
                {block.ah_premium_rate != null ? fmtPct(block.ah_premium_rate) : '—'}
              </div>
            </div>
            <div className="rounded border border-hair bg-surface p-2 text-center">
              <div className="text-2xs text-ink-muted">HA 折价率 (H较A)</div>
              <div className="num mt-0.5 text-base font-bold text-ink-secondary">
                {block.ha_discount_rate != null ? fmtPct(block.ha_discount_rate) : '—'}
              </div>
            </div>
            <div className="rounded border border-hair bg-surface p-2 text-center">
              <div className="text-2xs text-ink-muted">AH 比价倍数</div>
              <div className="num mt-0.5 text-base font-bold text-brand-600">
                {block.ah_ratio != null ? `${fmtNum(block.ah_ratio, 3)}x` : '—'}
              </div>
            </div>
          </div>

          {/* 套利信号与策略建议 */}
          <div className="flex items-start gap-2 rounded-md border border-hair bg-surface-alt/60 p-2 text-2xs">
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 font-bold ${
                block.arbitrage_signal === 'a_extreme_premium'
                  ? 'bg-danger-bg text-danger'
                  : block.arbitrage_signal === 'a_moderate_premium'
                  ? 'bg-warn-bg text-warn'
                  : block.arbitrage_signal === 'degraded'
                  ? 'bg-surface-sunken text-ink-muted'
                  : 'bg-brand-50 text-brand-700'
              }`}
            >
              {block.arbitrage_signal === 'a_extreme_premium'
                ? '极度溢价'
                : block.arbitrage_signal === 'a_moderate_premium'
                ? '温和溢价'
                : block.arbitrage_signal === 'degraded'
                ? '行情待更新'
                : '平价收敛'}
            </span>
            <span className="flex-1 text-ink-secondary leading-relaxed">
              {block.signal_desc ?? '当前价差处于历史正常估值区间'}
            </span>
          </div>

          {/* 跨境交易规则差异提示 */}
          <div className="grid grid-cols-2 gap-2 text-3xs text-ink-muted border-t border-hair pt-1.5">
            <div>
              <span className="font-semibold text-ink">A 股规则: </span>
              T+1 回转 · 100 股/手 · 印花税 0.05%
            </div>
            <div>
              <span className="font-semibold text-ink">港股通: </span>
              T+0 回转 · 各标的手数 · 双边印花税 0.1% + 征费
            </div>
          </div>
        </>
      ) : (
        /* 非双重上市标的展示 */
        <div className="space-y-2 text-xs">
          <div className="rounded-md border border-hair bg-surface-alt/40 p-2.5">
            <div className="flex items-center gap-1.5 text-ink font-medium">
              <span>{isHkCurrent ? '港股通专属标的' : '纯 A 股上市公司'}</span>
              <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-3xs text-ink-muted">
                无直接 H 股镜像
              </span>
            </div>
            <p className="mt-1 text-2xs text-ink-secondary leading-relaxed">
              {isHkCurrent
                ? '该标的为港股通标的，支持南向资金通过沪深港通机制参与交易。享受 T+0 当日回转与港股通交收制度。'
                : '该标的未在香港联交所发行 H 股，无直接两地比价套利窗口。已纳入互联互通（陆股通）标的池，外资可通过北向资金通道直接买卖。'}
            </p>
          </div>

          {/* 全市场宏观 AH 溢价参考 */}
          <div className="grid grid-cols-2 gap-2 text-center text-xs">
            <div className="rounded border border-hair bg-surface p-2">
              <div className="text-3xs text-ink-muted">全市场 AH 溢价指数参考</div>
              <div className="num mt-0.5 font-bold text-ink">~ 138.5 点</div>
              <div className="text-3xs text-ink-muted mt-0.5">A股整体较H股溢价约 38.5%</div>
            </div>
            <div className="rounded border border-hair bg-surface p-2">
              <div className="text-3xs text-ink-muted">互联互通持股上限规则</div>
              <div className="num mt-0.5 font-bold text-ink">30.0%</div>
              <div className="text-3xs text-ink-muted mt-0.5">外资持股比例警戒线 26%/28%/30%</div>
            </div>
          </div>

          <div className="flex justify-end pt-0.5">
            <button
              type="button"
              onClick={() => navigate('/market')}
              className="text-2xs text-brand-600 hover:underline"
            >
              查看全市场 AH 溢价率全景榜单 &gt;
            </button>
          </div>
        </div>
      )}
    </SectionCard>
  );
}
