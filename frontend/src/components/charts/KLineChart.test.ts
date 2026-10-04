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
 *
 * ---
 *
 * KLineChart 成交量副图轴标签（2026-10-04，同文件第二组）。
 *
 * 成交量副图 y 轴原**也无** formatter。个股成交量约 1e7（`15,000,000`，9 字符）
 * 尚能挤进 62px 左边距；而 ETF 成交量以「份」计可达 1e10（`5,000,000,000`，
 * 13 字符）⇒ 溢出左边距，被 K 线根节点的 `overflow-hidden` **裁掉首位数字**
 * （实测渲染成 `,000,000,000`）。修法 = `fmtVolAxis` 缩写为亿/万。
 */
import { describe, expect, it } from 'vitest';

import { fmtVol, fmtVolAxis } from '@/utils/format';

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

describe('KLineChart 成交量轴标签：缩写为亿/万（否则 13 字符被裁掉首位）', () => {
  it('ETF 量级 5e9 不再渲染成 "5,000,000,000"（被裁成 ",000,000,000"）', () => {
    expect(fmtVolAxis(5_000_000_000)).toBe('50亿');
    // 回归护栏：标签必须短到能挤进 62px 左边距（原 13 字符放不下）
    expect(fmtVolAxis(5_000_000_000).length).toBeLessThanOrEqual(6);
  });

  it('三档分界与 fmtVol 完全一致（≥1e8→亿；≥1e4→万；否则原值）', () => {
    // 同一分档口径是硬要求：否则图例（`成交量 4.96亿`）与轴（`5亿`）各说各话
    for (const v of [1e10, 5e9, 4.96e8, 1e8, 9.9e7, 1e4, 9999, 1, 0]) {
      const axis = fmtVolAxis(v);
      const legend = fmtVol(v);
      if (Math.abs(v) >= 1e8) {
        expect(axis).toMatch(/亿$/);
        expect(legend).toMatch(/亿$/);
      } else if (Math.abs(v) >= 1e4) {
        expect(axis).toMatch(/万$/);
        expect(legend).toMatch(/万$/);
      } else {
        expect(axis).not.toMatch(/[亿万]/);
        expect(legend).not.toMatch(/[亿万]/);
      }
    }
  });

  it('具体取值：只比 fmtVol 少两位小数（轴刻度要短）', () => {
    expect(fmtVolAxis(4.96e8)).toBe('4.96亿');
    expect(fmtVolAxis(1e8)).toBe('1亿');          // 不补成 "1.00亿"
    expect(fmtVolAxis(3.2e4)).toBe('3.2万');
    expect(fmtVolAxis(9999)).toBe('9999');        // 不足 1 万：原值，不带单位
    expect(fmtVolAxis(0)).toBe('0');
  });

  it('非有限值返回空串 —— 不得用 "0" 冒充不可得', () => {
    expect(fmtVolAxis(Number.NaN)).toBe('');
    expect(fmtVolAxis(Number.POSITIVE_INFINITY)).toBe('');
    // 若哪天误用 `Number(v) || 0` 之类的兜底，这里会变成 "0"
    expect(fmtVolAxis(Number.NaN)).not.toBe('0');
  });

  it('负零归一为 "0"（不出现 "-0"）', () => {
    expect(fmtVolAxis(-0)).toBe('0');
  });
});
