/**
 * 全局快捷键帮助浮层（`?` 呼出）。
 * 目的：可发现性 —— 快捷键若无提示等于不存在。
 */
import { useEffect } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
}

interface Row {
  keys: string;
  desc: string;
}

const GROUPS: { title: string; rows: Row[] }[] = [
  {
    title: '全局',
    rows: [
      { keys: '/', desc: '聚焦顶部搜索' },
      { keys: 'g → d', desc: '市场概览' },
      { keys: 'g → w', desc: '自选池' },
      { keys: 'g → o', desc: '执行中心' },
      { keys: 'g → b', desc: '策略回测' },
      { keys: 'g → s', desc: '选股中心' },
      { keys: 'g → a', desc: '预警中心' },
      { keys: 'g → r', desc: '策略研究' },
      { keys: 'g → f', desc: '因子工作室' },
      { keys: 'g → e', desc: 'ETF 中心' },
      { keys: 'g → p', desc: '组合回测（多资产）' },
      { keys: 'g → k', desc: '容量与归因' },
      { keys: 'g → c', desc: '数据中心' },
      { keys: 'g → q', desc: '数据质量' },
      { keys: 'g → t', desc: '任务调度' },
      { keys: 'n', desc: '打开通知' },
      { keys: '?', desc: '本帮助' },
      { keys: 'Esc', desc: '关闭浮层 / 弹窗' },
    ],
  },
  {
    title: 'K 线图（图表聚焦时）',
    rows: [
      { keys: 'F8', desc: '循环切换周期' },
      { keys: 'Ctrl + Q', desc: '前复权' },
      { keys: 'Ctrl + B', desc: '后复权' },
      { keys: '滚轮 / 拖动', desc: '缩放 / 平移' },
    ],
  },
];

export default function HotkeyHelp({ open, onClose }: Props) {
  // Esc 关闭（与全局 hook 的 onEscape 双保险；本组件在浮层内聚焦时也能关）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="键盘快捷键"
      className="fixed inset-0 z-[60] flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-ink/40" />
      <div
        className="relative z-10 max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-hair bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-center justify-between border-b border-hair bg-surface px-4 py-3">
          <h3 className="text-lg font-semibold text-ink">键盘快捷键</h3>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="rounded p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <div className="mb-2 text-2xs font-medium uppercase tracking-wide text-ink-muted">
                {g.title}
              </div>
              <div className="space-y-1">
                {g.rows.map((r) => (
                  <div key={r.keys} className="flex items-baseline justify-between gap-3">
                    <kbd className="shrink-0 rounded border border-hair2 bg-surface-alt px-1.5 py-0.5 font-mono text-2xs text-ink-secondary">
                      {r.keys}
                    </kbd>
                    <span className="text-right text-xs text-ink-secondary">{r.desc}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="border-t border-hair px-4 py-2 text-2xs text-ink-muted">
          在输入框内仅生效 Esc；带修饰键的组合键交还浏览器处理。
        </div>
      </div>
    </div>
  );
}
