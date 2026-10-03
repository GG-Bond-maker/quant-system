/**
 * KLineChart 价格轴自适应精度（2026-10-03）。
 *
 * ## 被守的缺陷
 *
 * 主图左轴（价格）原**无** `axisLabel.formatter` ⇒ ECharts 默认 `{value}` 把
 * `updatePriceScale()` 定出的显式浮点 `min/max` 上的刻度原样直出，实测出现
 * `4.35512` / `5.13688` 这类**带尾噪**的原始浮点标签。
 *
 * ## 判据
 *
 * `priceAxisDecimals(span)` 按跨度选位数：≥0.5→2 位；≥0.05→3 位；否则→4 位；
 * 非有限/非正跨度退化为 2 位。`formatPriceAxis(v, span)` = 按该位数走 `fmtAxisNum`
 * （四舍五入去尾噪、归一 `-0`、不补零）。这些都是**纯函数**，与布局引擎无关。
 */
import { describe, expect, it } from 'vitest';

import { formatPriceAxis, priceAxisDecimals } from './KLineChart';

describe('KLineChart 价格轴精度选择（按可视区间跨度）', () => {
  it('三档跨度映射正确：≥0.5→2；≥0.05→3；否则→4', () => {
    expect(priceAxisDecimals(2)).toBe(2);       // 大盘价位，2 位足够
    expect(priceAxisDecimals(0.5)).toBe(2);     // 边界（含）
    expect(priceAxisDecimals(0.4999)).toBe(3);  // 刚跌破 0.5
    expect(priceAxisDecimals(0.05)).toBe(3);    // 边界（含）
    expect(priceAxisDecimals(0.0499)).toBe(4);  // 极窄区间，需 4 位
    expect(priceAxisDecimals(0.001)).toBe(4);
  });

  it('非有限 / 非正跨度退化为 2 位（避免 NaN 位数）', () => {
    expect(priceAxisDecimals(Number.NaN)).toBe(2);
    expect(priceAxisDecimals(Number.POSITIVE_INFINITY)).toBe(2);
    expect(priceAxisDecimals(0)).toBe(2);
    expect(priceAxisDecimals(-1)).toBe(2);
  });

  it('去掉浮点尾噪：4.35512 在 3 位档→"4.355"，4 位档→"4.3551"', () => {
    expect(formatPriceAxis(4.35512, 0.1)).toBe('4.355');    // span 0.1 → 3 位
    expect(formatPriceAxis(4.35512, 0.01)).toBe('4.3551');  // span 0.01 → 4 位
    expect(formatPriceAxis(5.13688, 2)).toBe('5.14');       // span 2 → 2 位
  });

  it('与 fmtAxisNum 同口径：不产生负零、不补零', () => {
    expect(formatPriceAxis(-0.0000001, 1)).toBe('0');   // -0 归一为 "0"
    expect(formatPriceAxis(4.1, 1)).toBe('4.1');         // 不补成 "4.10"
    expect(formatPriceAxis(250, 1)).toBe('250');         // 不补成 "250.00"
  });
});
