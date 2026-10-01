/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx,js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ── 背景层级（3 级：页面底 / 卡片 / 内嵌块）──────────────────
        // canvas 比原 surface(#F5F7FA) 略深，让白卡在白底上浮起
        canvas: '#F1F4F8',
        surface: { DEFAULT: '#FFFFFF', alt: '#F8FAFC', sunken: '#EEF2F7' },

        // ── 文字层级 ──────────────────────────────────────────────
        // secondary 由 #64748B 加深到 #475569（提升对比度，久盯更清晰）
        ink: { DEFAULT: '#0F172A', secondary: '#475569', muted: '#94A3B8', inverse: '#FFFFFF' },

        // ── 边框 ──────────────────────────────────────────────────
        hair: '#E2E8F0',
        hair2: '#CBD5E1',

        // ── 涨跌语义系（A 股红涨绿跌）仅用于数字 / K线 / 涨跌箭头 ──────
        // 色相方向保留（红涨绿跌），色值微调降刺眼：DC2626→D92B2B，16A34A→12995B
        up: { DEFAULT: '#D92B2B', soft: '#FEE2E2', bg: '#FEF2F2', text: '#B91C1C' },
        down: { DEFAULT: '#12995B', soft: '#DCFCE7', bg: '#F0FDF4', text: '#047857' },
        flat: { DEFAULT: '#94A3B8', soft: '#F1F5F9', bg: '#F8FAFC', text: '#64748B' },

        // ── 系统状态语义系（与涨跌系正交解耦！）仅用于按钮 / 徽章 / 提示条 ──
        // danger 偏玫红（区别 up 纯红），success 偏青绿（区别 down 股市绿）
        danger: { DEFAULT: '#E11D48', soft: '#FFE4E6', bg: '#FFF1F2', text: '#BE123C' },
        success: { DEFAULT: '#0D9488', soft: '#CCFBF1', bg: '#F0FDFA', text: '#0F766E' },
        warn: { DEFAULT: '#D97706', soft: '#FEF3C7', bg: '#FFFBEB', text: '#B45309' },
        info: { DEFAULT: '#2563EB', soft: '#DBEAFE', bg: '#EFF6FF', text: '#1D4ED8' },

        // ── 品牌交互色 ────────────────────────────────────────────
        brand: {
          50: '#EFF6FF', 100: '#DBEAFE', 200: '#BFDBFE', 300: '#93C5FD',
          500: '#2563EB', 600: '#1D4ED8', 700: '#1E40AF',
        },
      },
      fontFamily: {
        sans: ['"Inter"', '"PingFang SC"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Menlo', 'Consolas', 'ui-monospace', 'monospace'],
      },
      // 6 档字号阶梯（收敛裸 px：8/9/10/11 全部并入 2xs 或 3xs）
      fontSize: {
        '3xs': ['0.625rem', { lineHeight: '0.875rem' }],   // 10px —— 徽章/角标下限
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],      // 11px —— 单位/caption/表头/时间戳
      },
      // 卡片圆角只用 3 档：卡片 lg / 内部块 md / 徽章(默认 rounded)
      borderRadius: {
        card: '0.5rem',
      },
      maxWidth: {
        terminal: '1440px',
        // 信息型页面（表格/研究）居中上限；行情型页面允许铺满
        content: '1600px',
        chart: '1720px',
      },
    },
  },
  plugins: [],
};
