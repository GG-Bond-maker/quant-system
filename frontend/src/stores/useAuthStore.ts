/**
 * 登录态（JWT）状态管理。
 *
 * 背景（审计 1.1）：此前前端没有登录页，也不调用 /auth/login 与 /auth/me，
 * 只在请求拦截里读一个用户手动塞进 localStorage 的静态字符串 AQP_ADMIN_TOKEN，
 * 既不校验会话也不校验过期 —— 后端的 JWT + RBAC 体系因此在生产上形同虚设。
 *
 * 现在：登录后由 /auth/login 签发 JWT，连同过期时刻持久化；
 * 请求拦截器统一附带，遇到 401xx 立即清空并跳转登录页。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/** 旧版本用户手动配置的静态 Token 键名（保留兼容，首次加载时迁移） */
const LEGACY_TOKEN_KEY = 'AQP_ADMIN_TOKEN';

export interface AuthUser {
  username: string;
  /** viewer / researcher / admin */
  role: string;
}

export interface AuthState {
  token: string | null;
  user: AuthUser | null;
  /**
   * 绝对过期时刻（ISO 8601 UTC）。
   * null 表示"过期时间未知"——仅出现在旧版手动配置的静态 Token 上，
   * 此时只能依赖后端返回 TOKEN_EXPIRED 来判定失效。
   */
  expiresAt: string | null;

  setSession: (token: string, user: AuthUser, expiresAt: string | null) => void;
  setUser: (user: AuthUser) => void;
  clear: () => void;
  /** 是否持有（且未过期）的凭证；只代表本地判断，真实校验由后端做 */
  isAuthenticated: () => boolean;
}

function readLegacyToken(): string | null {
  try {
    return localStorage.getItem(LEGACY_TOKEN_KEY);
  } catch {
    return null; // 隐私模式下 localStorage 可能不可用
  }
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      expiresAt: null,

      setSession: (token, user, expiresAt) => {
        set({ token, user, expiresAt });
        // 迁移完成后清理旧键，避免两份凭据并存造成歧义
        try {
          localStorage.removeItem(LEGACY_TOKEN_KEY);
        } catch { /* 忽略 */ }
      },

      setUser: (user) => set({ user }),

      clear: () => {
        set({ token: null, user: null, expiresAt: null });
        try {
          localStorage.removeItem(LEGACY_TOKEN_KEY);
        } catch { /* 忽略 */ }
      },

      isAuthenticated: () => {
        const { token, expiresAt } = get();
        if (!token) return false;
        // 过期时间未知时按有效处理，交由后端判定
        if (!expiresAt) return true;
        const ts = Date.parse(expiresAt);
        return Number.isNaN(ts) ? true : ts > Date.now();
      },
    }),
    { name: 'aqp-auth' },
  ),
);

/**
 * 兼容迁移：升级前用户可能已在 localStorage 手动配置 AQP_ADMIN_TOKEN。
 * 直接沿用以免升级后需要重新配置；后端对该 Token 的持有者同样视为 admin
 * （见 core.auth.require_auth 的 ADMIN_TOKEN 直通分支），因此角色标注是准确的。
 */
if (!useAuthStore.getState().token) {
  const legacy = readLegacyToken();
  if (legacy) {
    useAuthStore.getState().setSession(
      legacy,
      { username: 'admin', role: 'admin' },
      null, // 静态 Token 无过期时间，无法本地预判
    );
  }
}
