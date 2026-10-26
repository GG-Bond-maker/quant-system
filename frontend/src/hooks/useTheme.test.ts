import { describe, expect, it, beforeEach } from 'vitest';
import { getTheme, setThemeMode, toggleTheme, DARK_CLASS, THEME_STORAGE_KEY } from './useTheme';

describe('useTheme hook and theme manager', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove(DARK_CLASS);
  });

  it('默认为 light 主题', () => {
    expect(getTheme()).toBe('light');
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(false);
  });

  it('setThemeMode 能够设置 dark 主题并持久化', () => {
    setThemeMode('dark');
    expect(getTheme()).toBe('dark');
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(true);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('setThemeMode 能够从 dark 切回 light', () => {
    setThemeMode('dark');
    expect(getTheme()).toBe('dark');

    setThemeMode('light');
    expect(getTheme()).toBe('light');
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(false);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });

  it('toggleTheme 支持在 light / dark 之间循环翻转', () => {
    expect(getTheme()).toBe('light');

    const next1 = toggleTheme();
    expect(next1).toBe('dark');
    expect(getTheme()).toBe('dark');
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(true);

    const next2 = toggleTheme();
    expect(next2).toBe('light');
    expect(getTheme()).toBe('light');
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(false);
  });
});
