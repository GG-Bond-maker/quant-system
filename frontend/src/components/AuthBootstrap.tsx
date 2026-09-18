/**
 * 应用启动：校验本地 JWT（/auth/me）并预加载用户偏好（refresh_freq）。
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
    const { isAuthenticated, setUser, clear } = useAuthStore.getState();
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
