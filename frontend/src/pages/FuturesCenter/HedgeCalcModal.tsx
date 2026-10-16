/**
 * 期货中心 · 股票组合 Beta 套期保值与保证金测算浮层 (HedgeCalcModal)。
 */
import { useEffect, useState } from 'react';
import { futuresApi } from '@/api/futures';
import type { HedgeCalcResult } from '@/types/futures';
import { Modal } from '@/components/ui';
import { fmtNum } from '@/utils/format';

interface Props {
  open: boolean;
  onClose: () => void;
  defaultSymbol?: string;
  defaultPrice?: number;
}

export default function HedgeCalcModal({
  open,
  onClose,
  defaultSymbol = 'IF',
  defaultPrice = 3950.0,
}: Props) {
  const [portfolioValue, setPortfolioValue] = useState<number>(10_000_000);
  const [beta, setBeta] = useState<number>(1.0);
  const [symbol, setSymbol] = useState<string>(defaultSymbol);
  const [price, setPrice] = useState<number>(defaultPrice);
  const [result, setResult] = useState<HedgeCalcResult | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  useEffect(() => {
    setSymbol(defaultSymbol);
    setPrice(defaultPrice);
  }, [defaultSymbol, defaultPrice]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);

    futuresApi.hedgeCalc(portfolioValue, price, symbol, beta)
      .then((res) => {
        if (!cancelled) setResult(res);
      })
      .catch(() => {
        if (!cancelled) setResult(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, portfolioValue, price, symbol, beta]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="股指期货对冲与套期保值计算器"
      sub="对标机构级风险预算管理：精确测算股票多头组合系统性风险 Beta 对冲所需手数与保证金"
    >
      <div className="space-y-4">
        {/* 输入参数 */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-2xs text-ink-secondary block mb-1">股票组合待对冲市值 (元)</label>
            <input
              type="number"
              step="100000"
              value={portfolioValue}
              onChange={(e) => setPortfolioValue(Math.max(10000, Number(e.target.value)))}
              className="w-full rounded border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink num focus:border-brand-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-2xs text-ink-secondary block mb-1">组合资产 Beta 系数</label>
            <input
              type="number"
              step="0.05"
              value={beta}
              onChange={(e) => setBeta(Math.max(0.1, Number(e.target.value)))}
              className="w-full rounded border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink num focus:border-brand-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-2xs text-ink-secondary block mb-1">对冲合约品种</label>
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="w-full rounded border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink focus:border-brand-500 focus:outline-none"
            >
              <option value="IF">IF (沪深300股指, 300元/点)</option>
              <option value="IC">IC (中证500股指, 200元/点)</option>
              <option value="IM">IM (中证1000股指, 200元/点)</option>
              <option value="IH">IH (上证50股指, 300元/点)</option>
            </select>
          </div>

          <div>
            <label className="text-2xs text-ink-secondary block mb-1">期货参考报价 (点)</label>
            <input
              type="number"
              step="1"
              value={price}
              onChange={(e) => setPrice(Math.max(1, Number(e.target.value)))}
              className="w-full rounded border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink num focus:border-brand-500 focus:outline-none"
            />
          </div>
        </div>

        {/* 测算结果 */}
        <div className="rounded-lg border border-brand-200 bg-brand-50/50 p-3 space-y-2">
          <div className="text-xs font-semibold text-brand-900 flex items-center justify-between">
            <span>套保对冲方案测算结论</span>
            {loading && <span className="text-2xs text-ink-muted">实时计算中...</span>}
          </div>

          {result && (
            <div className="grid grid-cols-2 gap-2 text-2xs pt-1">
              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-muted block">建议做空合约手数</span>
                <span className="text-base font-bold text-brand-600 num">
                  {result.required_contracts} <span className="text-xs font-normal text-ink-secondary">手</span>
                </span>
              </div>

              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-muted block">开仓占用保证金估算</span>
                <span className="text-base font-bold text-ink num">
                  {fmtNum(result.total_margin_required)} <span className="text-xs font-normal text-ink-secondary">元</span>
                </span>
              </div>

              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-muted block">期货对冲名义总市值</span>
                <span className="text-xs font-semibold text-ink num">
                  {fmtNum(result.total_notional_value)} 元
                </span>
              </div>

              <div className="rounded bg-surface p-2 border border-hair">
                <span className="text-ink-muted block">套保对冲覆盖率</span>
                <span className="text-xs font-semibold text-success num">
                  {result.hedge_ratio_pct}%
                </span>
              </div>
            </div>
          )}
        </div>

        {/* 机构对冲风控提示 */}
        <div className="rounded bg-surface-alt p-2.5 text-2xs text-ink-muted space-y-1">
          <div className="font-medium text-ink-secondary">量化风控提示：</div>
          <div>1. 股指期货交易实行每日无负债盯市制度，若基差大幅波动需保留充足维持保证金以防追加；</div>
          <div>2. 现货若存在 Alpha 超额收益，通过做空等市值 Beta 的股指期货可对冲大盘系统性风险，提取纯净 Alpha 收益。</div>
        </div>
      </div>
    </Modal>
  );
}
