/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx,js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 背景
        surface: { DEFAULT: '#F5F7FA', alt: '#F8FAFC' },
        // 文字
        ink: { DEFAULT: '#0F172A', secondary: '#64748B', muted: '#94A3B8' },
        // A 股涨跌（红涨绿跌）
        up: { DEFAULT: '#DC2626', soft: '#FEE2E2', bg: '#FEF2F2' },
        down: { DEFAULT: '#16A34A', soft: '#DCFCE7', bg: '#F0FDF4' },
        flat: { DEFAULT: '#94A3B8', soft: '#F1F5F9' },
        // 品牌交互色
        brand: {
          50: '#EFF6FF', 100: '#DBEAFE', 200: '#BFDBFE',
          500: '#2563EB', 600: '#1D4ED8', 700: '#1E40AF',
        },
        // 边框
        hair: '#E2E8F0',
      },
      fontFamily: {
        sans: ['"Inter"', '"PingFang SC"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Menlo', 'Consolas', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      maxWidth: {
        terminal: '1440px',
      },
    },
  },
  plugins: [],
};
