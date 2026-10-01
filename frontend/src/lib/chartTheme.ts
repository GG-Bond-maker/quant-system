/**
 * 图表主题取色（共享，全站图表的唯一色值来源）。
 *
 * ## 为什么需要这个模块
 *
 * 本项目暗色主题由 `html.theme-dark` 覆盖 `:root` 上的 CSS 变量实现
 * （见 `index.css`）。**ECharts 的 canvas 渲染不参与 CSS 级联**——
 * 写 `color: '#DC2626'` 的图表在暗色下会继续画亮红，既发光溢出，
 * 又与页面其余部分配色脱节。因此图表色值**必须**运行时从 CSS 变量读。
 *
 * 此前 `cssVar()` 是 `KLineChart.tsx` 的**私有函数**，其它图表想合规也无从调用，
 * 结果 `MarketHeatmap` / `Backtest/resultParts` / `CapacityAttribution` /
 * `FactorStudio` / `DataCenter` / `DataQuality` 等 8 个文件里散落了 24 处
 * 硬编码色（部分还与 token 值不一致）。抽到共享模块就是为了让"写对"变成最省事的路径。
 *
 * ## 用法
 *
 * ```ts
 * import { cssVar, chartPalette } from '@/lib/chartTheme';
 *
 * // 单个取色
 * const up = cssVar('--up', '#D92B2B');
 *
 * // 一次性拿一组语义色（推荐）
 * const p = chartPalette();
 * option.series[0].itemStyle.color = p.UP;
 * ```
 *
 * ## 纪律
 *
 * - 语义色（涨跌 / 状态）**一律**经本模块读取，禁止硬编码 hex。
 * - 类别色（同一图内区分"是哪条线"，如 MA5/MA10）不跟随主题，属例外——
 *   它们保持固定色相以便跨主题辨识，但**取值应通过 `CATEGORY_COLORS`** 集中管理。
 * - 取色在**渲染时**进行（不是模块加载时），主题切换后重渲染即生效。
 */

/** 读取 `:root` 上的 CSS 自定义属性；缺失时回落 `fallback`。 */
export function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** 当前是否暗色主题（`html.theme-dark`）。 */
export function isDarkTheme(): boolean {
  if (typeof document === 'undefined') return false;
  return document.documentElement.classList.contains('theme-dark');
}

/**
 * 把 `#RRGGBB` 转成 `rgba(...)`。
 *
 * 为什么需要：ECharts 的 `areaStyle` / `markPoint.label` 等处需要**半透明**
 * 或**固定白字**，不能直接塞 CSS 变量名（canvas 不解析 `var()`）。
 * 此前这些位置各写一套字面量（`'#fff'`、`'rgba(59,130,246,0.08)'`），
 * 既容易写错又无法跟随主题。统一由本函数从 token 推出透明度变体。
 *
 * 解析失败（非 hex、或已是 rgba/变量名）时原样返回，保证不炸。
 */
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/.exec(color.trim());
  if (!m) return color;
  const hex = m[1].length === 3 ? m[1].split('').map((c) => c + c).join('') : m[1];
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * 逐次调用都现读 DOM，因此主题切换后只要触发重渲染即可拿到新值。
 *
 * 回落值与 `tailwind.config.js` / `index.css` 的亮色 token 保持一致，
 * 保证在极早期（DOM 未就绪）渲染也不会出现刺眼的错误色。
 */
export function chartPalette() {
  return {
    // 涨跌语义（仅数字 / K 线 / 涨跌色块）
    UP: cssVar('--up', '#D92B2B'),
    DOWN: cssVar('--down', '#12995B'),
    FLAT: cssVar('--flat', '#94A3B8'),
    // 系统状态（与涨跌系正交解耦）
    DANGER: cssVar('--danger', '#E11D48'),
    SUCCESS: cssVar('--success', '#0D9488'),
    WARN: cssVar('--warn', '#D97706'),
    INFO: cssVar('--info', '#2563EB'),
    BRAND: cssVar('--brand', '#2563EB'),
    // 底层结构色
    CARD: cssVar('--bg-card', '#FFFFFF'),
    ALT: cssVar('--bg-alt', '#F8FAFC'),
    SUNKEN: cssVar('--bg-sunken', '#EEF2F7'),
    CANVAS: cssVar('--bg-canvas', '#F1F4F8'),
    HAIR: cssVar('--hair', '#E2E8F0'),
    HAIR2: cssVar('--hair2', '#CBD5E1'),
    INK: cssVar('--ink', '#0F172A'),
    INK2: cssVar('--ink-secondary', '#475569'),
    INKM: cssVar('--ink-muted', '#94A3B8'),
    // 浮层（需半透明，故不用 --bg-card 的实色）
    TIP_BG: isDarkTheme() ? 'rgba(17,24,39,0.97)' : 'rgba(255,255,255,0.97)',
    TIP_TEXT: cssVar('--ink', '#0F172A'),
    TIP_BORDER: cssVar('--hair', '#E2E8F0'),
    /** 白字（浮层内文字 / K 线标注文字），暗色下仍是浅色 */
    ON_SOLID: cssVar('--ink-inverse', '#FFFFFF'),
  };
}

/**
 * 类别色板：用于同一张图内区分「是哪条线 / 哪一根柱」。
 *
 * ⚠️ 与语义色不同，这些**刻意不跟随主题**——若跟随，暗色下多条指标线
 * 会一起变亮而互相撞色，反而更难分辨。此处集中定义，避免各文件各写一套。
 */
export const CATEGORY_COLORS = {
  /** 主色（类别 1）：MA5 / KDJ-K / RSI6 / DIF */
  C1: '#1f2329',
  /** 次色（类别 2）：MA10 / KDJ-D / RSI12 / DEA */
  C2: '#d48806',
  /** 三色（类别 3）：MA20 / KDJ-J / RSI24 */
  C3: '#9c27b0',
  /** 四色（类别 4）：MA60 */
  C4: '#2ba471',
  /** 序列色（用于连续型分类，如 Top-N 排名） */
  SEQ: ['#2563EB', '#0D9488', '#D97706', '#9c27b0', '#E11D48', '#64748B'],
} as const;

/**
 * 涨跌色（按数值符号取色）。图表里 `>= 0 红 / < 0 绿` 的高频写法，
 * 收敛为一个函数以避免各处重复判断时漏掉 `0` 的归属。
 */
export function upDownColor(value: number | null | undefined): string {
  const p = chartPalette();
  if (value == null) return p.FLAT;
  return value >= 0 ? p.UP : p.DOWN;
}
