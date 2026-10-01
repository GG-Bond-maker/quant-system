/**
 * 主题状态（`light` / `dark`）的 React 订阅。
 *
 * 背景：本项目主题由 `html.theme-dark` 这个 **class** 驱动（见 `index.css` 的
 * CSS 变量覆盖），而不是存在 zustand store 里的状态。这对纯 CSS 组件完全够用，
 * 但对 **Canvas 渲染** 的组件（ECharts）不行 —— canvas 不参与 CSS 级联，
 * 必须在主题切换后主动重算色值并重绘。
 *
 * 因此这里用 `MutationObserver` 监听 `<html>` 的 class 变化，
 * 把主题暴露为响应式的 React state，供图表类组件作为 `useMemo` 依赖使用。
 */
import { useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

const DARK_CLASS = 'theme-dark';

/** 同步读取当前主题（避免首帧闪烁） */
function readTheme(): Theme {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.classList.contains(DARK_CLASS) ? 'dark' : 'light';
}

export function useTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    const root = document.documentElement;
    // 挂载时对齐一次（可能在首次 render 之后才应用主题）
    setTheme(readTheme());

    const observer = new MutationObserver(() => {
      setTheme(readTheme());
    });
    observer.observe(root, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  return theme;
}
