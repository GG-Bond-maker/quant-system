/**
 * 登录态（JWT）状态管理。
 *
 * 背景（审计 1.1）：此前前端没有登录页，也不调用 /auth/login 与 /auth/me，
 * 只在请求拦截里读一个用户手动塞进 localStorage 的静态字符串，
 * 既不校验会话也不校验过期 —— 后端的 JWT + RBAC 体系因此在生产上形同虚设。
 *
 * 现在：登录后由 /auth/login 签发 JWT，连同过期时刻持久化；
 * 请求拦截器统一附带，遇到 401xx 立即清空并跳转登录页。
 *
 * P1-24：不再把 localStorage 里遗留的静态运维 Token 迁移成会话 ——
 * 该 Token 只是运维直连凭据、不是前端登录凭据（生产
 * ALLOW_ADMIN_TOKEN_LOGIN=false 时会出现「UI 说 admin、后端说不是」）。
 * 会话只能由 /auth/login 建立。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface AuthUser {
  username: string;
  /** viewer / researcher / admin */
  role: string;
}

/**
 * 凭证是否已过期的**唯一判据**（所有"是否登录"的判断都必须经过它）。
 *
 * 历史 Bug：路由守卫 RequireRole 按 expiresAt 判过期后跳 /login?next=xxx，
 * 而登录页只判断 token 非空就立刻回跳 next —— 本地留存一份已过期 token 时，
 * 二者互相推送形成重定向死循环（后端 5 分钟内收到 899 次 register/status、
 * 0 次 /auth/me，页面表现为点击任意受保护路由都"没反应"）。
 * 因此把过期判定收敛到本函数，登录页与守卫共用同一把尺子。
 */
export function isExpiredAt(expiresAt: string | null): boolean {
  // 过期时刻未知（null/解析失败）时按未过期处理，真实失效交由后端判定
  if (!expiresAt) return false;
  const ts = Date.parse(expiresAt);
  return Number.isNaN(ts) ? false : ts <= Date.now();
}

export interface AuthState {
  token: string | null;
  user: AuthUser | null;
  /** 绝对过期时刻（ISO 8601 UTC）；null 表示未知，交由后端判定失效 */
  expiresAt: string | null;

  setSession: (token: string, user: AuthUser, expiresAt: string | null) => void;
  setUser: (user: AuthUser) => void;
  clear: () => void;
  /** 是否持有（且未过期）的凭证；只代表本地判断，真实校验由后端做 */
  isAuthenticated: () => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      expiresAt: null,

      setSession: (token, user, expiresAt) => set({ token, user, expiresAt }),

      setUser: (user) => set({ user }),

      clear: () => set({ token: null, user: null, expiresAt: null }),

      isAuthenticated: () => {
        const { token, expiresAt } = get();
        if (!token) return false;
        return !isExpiredAt(expiresAt);
      },
    }),
    { name: 'aqp-auth' },
  ),
);