/**
 * 全局快捷键（键盘优先 —— 服务"长时间盯盘手不离键盘"）。
 *
 * 设计要点：
 *  1. **输入框避让**：`e.target` 是 input/textarea/select/contenteditable 时，
 *     除了 Esc 一律不触发单键快捷键 —— 否则用户在搜索框打字会疯狂跳页。
 *  2. **两段式跳页**（GitHub 范式）：`g` 后跟一个字母。避开浏览器单键冲突
 *     （单按字母会被聊天/浏览器扩展占用）。
 *  3. **不与图表冲突**：KLineChart 已占用 F8 / Ctrl+Q / Ctrl+B（且仅在图表聚焦时），
 *     本 hook 不注册任何带修饰键的组合，二者正交。
 *  4. 所有单键快捷键在**带修饰键**（Ctrl/Meta/Alt）时直接放行，交还浏览器。
 */
import { useEffect, useRef } from 'react';

export interface HotkeyHandlers {
  /** `/` 聚焦搜索 */
  onSearch?: () => void;
  /** `?` 打开/关闭帮助 */
  onHelp?: () => void;
  /** `Esc` 关闭浮层 */
  onEscape?: () => void;
  /** 两段式跳页：`g` 后跟的字母 → 目标路由 */
  goto?: Record<string, string>;
  /** 实际执行跳转（由调用方持有 navigate） */
  onNavigate?: (to: string) => void;
}

/** 判断事件源是否为"正在输入"的元素 —— 是则跳过单键快捷键 */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return target.isContentEditable;
}

/** `g` 前缀的有效期（毫秒）：超时未跟字母则取消，避免"上次按了 g，很久后按 d 跳页" */
const GOTO_PREFIX_TTL_MS = 1500;

export function useHotkeys(handlers: HotkeyHandlers): void {
  // 用 ref 保存最新 handlers，避免每次渲染重新绑定监听器导致的行为漂移
  const ref = useRef(handlers);
  ref.current = handlers;

  useEffect(() => {
    let gotoArmedAt = 0;

    const onKeyDown = (e: KeyboardEvent) => {
      const h = ref.current;

      // 带修饰键：一律放行（Ctrl+K 等留给浏览器/未来命令面板）
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      // 输入中：仅允许 Esc（关闭浮层），其余单键不拦截
      if (isTypingTarget(e.target)) {
        if (e.key === 'Escape') h.onEscape?.();
        return;
      }

      // Esc：关闭浮层（各页面若有自己的 Esc 行为，由页面自行监听，互不干扰）
      if (e.key === 'Escape') {
        h.onEscape?.();
        gotoArmedAt = 0;
        return;
      }

      // `/` 聚焦搜索
      if (e.key === '/') {
        e.preventDefault();
        h.onSearch?.();
        gotoArmedAt = 0;
        return;
      }

      // `?` 帮助（Shift+/ 在多数键盘布局即 ?，此处两种都接）
      if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
        e.preventDefault();
        h.onHelp?.();
        gotoArmedAt = 0;
        return;
      }

      // `n` 通知：点击顶栏铃铛（不新增状态，复用既有交互）
      if (e.key === 'n') {
        const bell = document.querySelector<HTMLButtonElement>('button[data-hotkey="notify"]');
        if (bell) { e.preventDefault(); bell.click(); }
        gotoArmedAt = 0;
        return;
      }

      // 两段式跳页
      const k = e.key.toLowerCase();
      if (gotoArmedAt > 0 && Date.now() - gotoArmedAt < GOTO_PREFIX_TTL_MS) {
        const to = h.goto?.[k];
        if (to) {
          e.preventDefault();
          h.onNavigate?.(to);
          gotoArmedAt = 0;
          return;
        }
      }
      if (k === 'g') {
        gotoArmedAt = Date.now();
        // 不 preventDefault：避免"按了 g 但改主意"时吞掉按键
        return;
      }
      gotoArmedAt = 0;
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);
}
