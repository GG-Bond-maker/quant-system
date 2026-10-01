/**
 * 路由守卫：未登录（或凭证已过期）时跳转登录页并记录原目标地址。
 *
 * RequireRole：校验最低角色（viewer < researcher < admin）。
 *
 * ⚠️ 2026-09-23 用户裁决「只要登录即可用全部功能（含下单 / 同步 / 训练等写操作）」
 *     ⇒ 默认**放开角色校验**，只保留"登录"这道门：放开时 hasMinimumRole 对任何已登录
 *     角色返回 true，RequireRole 不再渲染"权限不足"卡片。与后端 core/auth.ensure_role
 *     的单一判定点同构（后端开关 = Settings.RBAC_ENFORCE）。
 *     回滚：构建/运行时设 VITE_RBAC_ENFORCE=true 即恢复分级校验（无需改代码）。
 * 注：旧的 default export `RequireAuth`（无角色校验）已无调用方，本轮删除；
 * App.tsx 仅使用 RequireRole。
 */
import type { ReactNode } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { ROLE_LABEL } from '@/api/auth';
import { isExpiredAt, useAuthStore } from '@/stores/useAuthStore';

/** 与 backend/core/auth.py 角色等级一致 */
export const ROLE_RANK: Record<string, number> = {
  viewer: 0,
  researcher: 1,
  admin: 2,
};

/** 前端 RBAC 开关（与后端 Settings.RBAC_ENFORCE 语义一致，默认放开）。
 *  回滚：设 VITE_RBAC_ENFORCE=true 即恢复 viewer<researcher<admin 分级校验。 */
const RBAC_ENFORCE = /^(1|true|yes|on)$/i.test(
  String(import.meta.env.VITE_RBAC_ENFORCE ?? ''),
);

export function hasMinimumRole(role: string | undefined, minimum: string): boolean {
  // 全面放开：只要已登录（存在角色）即视为满足任何最低角色。
  // 各页的 `hasMinimumRole(role, '...')` 调用点保持不变（缩小改动面），仅在此处按
  // 开关切换语义 —— 与后端 ensure_role 单一判定点同构，便于一键回滚。
  if (!RBAC_ENFORCE) return !!role;
  const userRank = ROLE_RANK[role ?? ''] ?? -1;
  const required = ROLE_RANK[minimum] ?? 99;
  return userRank >= required;
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

  // 判据与 useAuthStore.isAuthenticated() 完全一致（共用 isExpiredAt）：
  // 过期凭证等同于未登录。守卫与登录页若各用一把尺子，就会出现
  // /report → /login?next=/report → /report 的重定向死循环。
  const expired = isExpiredAt(expiresAt);
  if (!token || expired) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  if (!hasMinimumRole(user?.role, minimum)) {
    const need = ROLE_LABEL[minimum] ?? minimum;
    const have = user?.role ? (ROLE_LABEL[user.role] ?? user.role) : '未登录';
    return (
      <div className="mx-auto max-w-lg rounded-lg border border-warn/30 bg-warn-bg px-4 py-6 text-center">
        <h2 className="text-sm font-semibold text-warn">权限不足</h2>
        <p className="mt-2 text-xs text-warn">
          此功能需要 <strong>{need}</strong> 及以上角色；当前为 <strong>{have}</strong>。
        </p>
        <p className="mt-1 text-2xs text-warn">
          当前会话仍保持登录；请切换至具备所需角色的账号后重试。
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
