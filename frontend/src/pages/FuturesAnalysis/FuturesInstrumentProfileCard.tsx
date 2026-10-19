/**
 * 单期货品种微观投研画像与日K线+持仓量(OI)分析卡片。
 *
 * 包含：
 * 1. 合约微观要素规范（乘数、最小跳价价值、保证金门槛、手续费率、名义合约价值）；
 * 2. 波动率与微观流动性指标（HV20 年化历史波动率、ATR14 真实波幅、OI 未平仓持仓量动向）；
 * 3. 机构级专业 K 线图组件 (FuturesKLineChart)：对标个股分析，日蜡烛图 + 均线/BOLL + 成交量 + OI持仓量与增减仓全景。
 */
import { SectionCard } from '@/components/ui';
import type { FuturesInstrumentProfile } from '@/types/futures';
import { fmtNum, fmtPct } from '@/utils/format';
import { FuturesKLineChart } from './FuturesKLineChart';

interface Props {
  profile: FuturesInstrumentProfile | null;
  loading?: boolean;
  continuous?: boolean;
  activeRange?: number;
  onToggleContinuous?: (c: boolean) => void;
  onRangeChange?: (days: number) => void;
}

export function FuturesInstrumentProfileCard({
  profile,
  loading = false,
  continuous = true,
  activeRange = 250,
  onToggleContinuous,
  onRangeChange,
}: Props) {
  if (loading && !profile) {
    return (
      <SectionCard title="标的微观画像与量价持仓全景">
        <div className="flex h-64 items-center justify-center text-sm text-text-muted">
          正在加载标的微观投研画像数据与 K 线序列...
        </div>
      </SectionCard>
    );
  }

  if (!profile) {
    return (
      <SectionCard title="标的微观画像与量价持仓全景">
        <div className="flex h-32 items-center justify-center text-sm text-text-muted">
          暂无标的微观数据
        </div>
      </SectionCard>
    );
  }

  const { volatility_stats: vs, open_interest_stats: ois } = profile;

  return (
    <SectionCard
      title={`${profile.name} (${profile.symbol_prefix}) 微观投研画像与量价K线全景`}
      action={
        <div className="flex items-center gap-2">
          {loading && (
            <span className="text-2xs text-primary animate-pulse font-medium">
              数据刷新中...
            </span>
          )}
          <span className="text-2xs text-text-muted">
            交易所: {profile.exchange} | 标的代码: {profile.symbol_prefix} | 最新价: {fmtNum(profile.latest_price)}
          </span>
        </div>
      }
    >
      <div className="space-y-4">
        {/* 微观规格与要素看板 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">合约乘数 / 计价单位</div>
            <div className="mt-1 font-mono text-sm font-semibold text-text">
              {profile.multiplier}
              <span className="ml-1 text-xs font-normal text-text-muted">元/点</span>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">最小变动跳 / 价值</div>
            <div className="mt-1 font-mono text-sm font-semibold text-text">
              {profile.tick_size} 点
              <span className="ml-1 text-xs text-primary font-medium">({profile.tick_value}元/跳)</span>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">保证金率 / 一手占用</div>
            <div className="mt-1 font-mono text-sm font-semibold text-text">
              {profile.margin_rate_pct}%
              <span className="ml-1 text-xs text-text-muted">({fmtNum(profile.margin_per_lot, 0)}元)</span>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">单手名义本金市值</div>
            <div className="mt-1 font-mono text-sm font-semibold text-text">
              {fmtNum(profile.contract_value, 0)}
              <span className="ml-1 text-xs font-normal text-text-muted">元</span>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">HV20 年化历史波动率</div>
            <div className="mt-1 font-mono text-sm font-semibold text-amber-500">
              {fmtPct(vs.hv20_pct)}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">ATR14 真实波幅</div>
            <div className="mt-1 font-mono text-sm font-semibold text-blue-500">
              {profile.volatility_stats.atr14}
              <span className="ml-1 text-xs text-text-muted">({fmtPct(vs.atr_pct)})</span>
            </div>
          </div>
        </div>

        {/* 持仓量结构状态栏 */}
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-surface-raised px-4 py-2.5 text-xs">
          <div className="flex items-center gap-3">
            <span className="font-medium text-text">未平仓持仓量 (OI):</span>
            <span className="font-mono text-sm font-bold text-text">{fmtNum(ois.latest_oi, 0)} 手</span>
            <span className="text-text-muted">日增减:</span>
            <span className={`font-mono font-medium ${ois.oi_change >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
              {ois.oi_change >= 0 ? `+${fmtNum(ois.oi_change, 0)}` : fmtNum(ois.oi_change, 0)} 手 ({fmtPct(ois.oi_change_pct)})
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-text-muted">量价持仓结构研判:</span>
            <span className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-0.5 font-medium text-primary">
              {ois.structure}
            </span>
          </div>
        </div>

        {/* 机构级专业 K 线图组件 (FuturesKLineChart) */}
        <FuturesKLineChart
          bars={profile.kline || []}
          symbol={profile.symbol_prefix}
          name={profile.name}
          continuous={continuous}
          activeRange={activeRange}
          onToggleContinuous={onToggleContinuous}
          onRangeChange={onRangeChange}
          height={540}
        />
      </div>
    </SectionCard>
  );
}
