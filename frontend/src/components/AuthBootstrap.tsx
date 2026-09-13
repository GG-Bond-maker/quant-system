/**
 * 应用启动：校验本地 JWT（/auth/me）并预加载用户偏好（refresh_freq）。
 */
import { useEffect } from 'react';
import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import { useAuthStore } from '@/stores/useAuthStore';
import { usePreferencesStore } from '@/stores/usePreferencesStore';

const AUTH_FAIL_CODES = new Set([40100, 40101, 40102]);

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
