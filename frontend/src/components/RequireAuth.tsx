/**
 * 路由守卫：未登录（或凭证已过期）时跳转登录页并记录原目标地址。
 *
 * RequireRole：在 RequireAuth 基础上校验最低角色（viewer < researcher < admin）。
 */
import type { ReactNode } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { ROLE_LABEL } from '@/api/auth';
import { useAuthStore } from '@/stores/useAuthStore';

/** 与 backend/core/auth.py 角色等级一致 */
export const ROLE_RANK: Record<string, number> = {
  viewer: 0,
  researcher: 1,
  admin: 2,
};

export function hasMinimumRole(role: string | undefined, minimum: string): boolean {
  const userRank = ROLE_RANK[role ?? ''] ?? -1;
  const required = ROLE_RANK[minimum] ?? 99;
  return userRank >= required;
}

export default function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const expiresAt = useAuthStore((s) => s.expiresAt);
  const location = useLocation();

  const expired = !!expiresAt && Date.parse(expiresAt) <= Date.now();
  if (!token || expired) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <>{children}</>;
}

export function RequireRole({
  children,
  minimum = 'researcher',
}: {
  children: ReactNode;
  /** 最低角色：researcher 可执行下单/同步/挖掘等写操作 */
  minimum?: keyof typeof ROLE_RANK;
}) {
  const token = useAuthStore((s) => s.token);
  const expiresAt = useAuthStore((s) => s.expiresAt);
  const user = useAuthStore((s) => s.user);
  const location = useLocation();

  const expired = !!expiresAt && Date.parse(expiresAt) <= Date.now();
  if (!token || expired) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  if (!hasMinimumRole(user?.role, minimum)) {
    const need = ROLE_LABEL[minimum] ?? minimum;
    const have = user?.role ? (ROLE_LABEL[user.role] ?? user.role) : '未登录';
    return (
      <div className="mx-auto max-w-lg rounded-lg border border-amber-200 bg-amber-50 px-4 py-6 text-center">
        <h2 className="text-sm font-semibold text-amber-900">权限不足</h2>
        <p className="mt-2 text-xs text-amber-800">
          此功能需要 <strong>{need}</strong> 及以上角色；当前为 <strong>{have}</strong>。
        </p>
        <p className="mt-1 text-2xs text-amber-700">
          请使用研究员或管理员账号登录后重试。
        </p>
        <Link
          to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`}
          className="mt-4 inline-block rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
        >
          前往登录
        </Link>
      </div>
    );
  }

  return <>{children}</>;
}
