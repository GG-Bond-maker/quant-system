/**
 * 应用启动：校验本地 JWT（/auth/me）并预加载用户偏好（refresh_freq）。
 *
 * 注意：App.tsx 在 /login 路由下不渲染本组件，因此过期会话的自愈只在受保护页
 * 首次挂载时发生——这已足够，因为登录页本身不再依赖过期 token 做回跳判定。
 */
import { useEffect } from 'react';
import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import { useAuthStore } from '@/stores/useAuthStore';
import { usePreferencesStore } from '@/stores/usePreferencesStore';
import { ERR } from '@/types/api';

const AUTH_FAIL_CODES: ReadonlySet<number> = new Set([
  ERR.UNAUTHORIZED,
  ERR.TOKEN_EXPIRED,
  ERR.INVALID_TOKEN,
]);

export default function AuthBootstrap() {
  useEffect(() => {
    const { token, isAuthenticated, setUser, clear } = useAuthStore.getState();

    // 自愈：本地留有一份「已过期」凭证时立即清空。
    // 否则 isAuthenticated() 为 false ⇒ 不会发 /auth/me ⇒ 永远走不到下面的 clear()
    // 分支，过期会话会一直躺在 localStorage 里，让守卫反复跳登录页。
    if (token && !isAuthenticated()) {
      clear();
      return;
    }
    if (!isAuthenticated()) return;

    void (async () => {
      try {
        const user = await authApi.me();
        setUser(user);
        await usePreferencesStore.getState().loadFromServer();
      } catch (e) {
        if (e instanceof ApiError && AUTH_FAIL_CODES.has(e.code)) {
          clear();
        }
      }
    })();
  }, []);

  return null;
}
