/** 展示层格式化工具（涨跌色 / 百分比 / 大额成交额）。 */

/** A 股习惯：涨红跌绿，返回 Tailwind 文本色类名 */
export function pctClass(p: number | null | undefined): string {
  if (p == null || Math.abs(p) < 1e-9) return 't-flat';
  return p > 0 ? 't-up' : 't-down';
}

/** 1.23 -> "+1.23%"；null -> "—" */
export function fmtPct(p: number | null | undefined, digits = 2): string {
  if (p == null || !Number.isFinite(p)) return '—';
  const sign = p > 0 ? '+' : '';
  return `${sign}${p.toFixed(digits)}%`;
}

/** 等宽数字格式化，null -> "—" */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 成交额（亿）：10560.1 -> "1.06 万亿" / "10560.1 亿" */
export function fmtAmountYi(yi: number | null | undefined): string {
  if (yi == null || !Number.isFinite(yi)) return '—';
  if (Math.abs(yi) >= 10000) return `${(yi / 10000).toFixed(2)} 万亿`;
  return `${yi.toFixed(1)} 亿`;
}

/**
 * 成交量格式化（**非成交额**，单位口径与 `fmtAmountYi` 严格区分）。
 *
 * 输入为原始成交量数值（股数 / 手数，取决于数据源列口径），按数量级分档：
 *   12345678 -> "1234.57万" · 256000000 -> "2.56亿" · 3800 -> "3800" · null -> "-"
 *
 * ⚠️ **不得**把本函数与 `fmtAmountYi` 混用：成交量是「数量」，成交额是「金额」，
 * 二者量纲不同。历史上 EtfDetail 的成交量图误用 `fmtAmountYi(v / 1e8)`，
 * 把股数标成「亿元」——本函数即该缺陷修复后的唯一正确格式化入口。
 */
export function fmtVol(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '-';
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return String(Math.round(v));
}

/** 因子英文名 -> 中文可读名（未收录的回退原名） */
const FACTOR_NAMES: Record<string, string> = {
  ret_1: '昨日收益', ret_3: '3日收益', ret_5: '5日收益', ret_10: '10日收益',
  ret_20: '20日收益', ret_60: '60日收益', overnight_gap: '隔夜跳空',
  ma_gap_5: 'MA5偏离', ma_gap_10: 'MA10偏离', ma_gap_20: 'MA20偏离',
  ma_gap_60: 'MA60偏离', ma_gap_120: 'MA120偏离', ma_gap_250: 'MA250偏离',
  ma_slope_20: 'MA20斜率', vol_5: '5日波动率', vol_10: '10日波动率',
  vol_20: '20日波动率', vol_60: '60日波动率', hl_range: '日振幅',
  co_range: '日实体', v_ma_gap_5: '量比5日', v_ma_gap_10: '量比10日',
  v_ma_gap_20: '量比20日', v_ma_gap_60: '量比60日', v_rank_20: '量能分位',
  v_cv_5: '量能波动', amount_per_share: '每手金额', macd_dif: 'MACD DIF',
  macd_dea: 'MACD DEA', macd_bar: 'MACD 柱', rsi_6: 'RSI(6)', rsi_14: 'RSI(14)',
  boll_pos: '布林位置', boll_w: '布林带宽', atr_14: 'ATR(14)',
  rank_close_20: '20日价格分位', rank_close_60: '60日价格分位',
  rank_volume_20: '20日量能分位', skew_ret_20: '20日偏度', skew_ret_60: '60日偏度',
  kurt_ret_20: '20日峰度', kurt_ret_60: '60日峰度',
};

export function factorName(key: string): string {
  return FACTOR_NAMES[key] ?? key;
}
