/**
 * 登录页（/login）：对接 POST /api/v1/auth/login + POST /api/v1/auth/register。
 *
 * 背景（审计 1.1）：后端实现了完整的 JWT + RBAC（viewer/researcher/admin），
 * 但前端此前没有任何登录入口，权限体系形同虚设。本页补齐该断层：
 * 登录成功后把 JWT、用户信息与绝对过期时刻写入 useAuthStore（持久化），
 * 之后所有请求由 client.ts 统一附带 Bearer Token。
 *
 * 注册：登录页内置「注册」Tab，调用自助注册端点；注册成功后端直接签发 JWT
 * （与登录同构），故复用同一套 setSession 逻辑。入口是否展示由后端
 * ALLOW_REGISTRATION 开关决定（GET /auth/register/status），关闭时自动隐藏。
 */
import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { authApi } from '@/api/auth';
import { useAuthStore } from '@/stores/useAuthStore';

type Mode = 'login' | 'register';

export default function Login() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = useAuthStore((s) => s.token);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setSession = useAuthStore((s) => s.setSession);

  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // null = 尚未拉取到开关；true/false = 后端返回值。拉取失败按"不展示"处理，
  // 避免后端注册关闭时仍露出入口。
  const [registerEnabled, setRegisterEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    authApi
      .registerStatus()
      .then((s) => {
        if (alive) setRegisterEnabled(Boolean(s?.enabled));
      })
      .catch(() => {
        if (alive) setRegisterEnabled(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 回跳目标兜底：next 缺失或指向登录页自身时一律回首页。
  // 即便将来再次出现重定向环，也不会变成 /login → /login 的自跳把浏览器打满。
  const rawNext = params.get('next');
  const nextPath = rawNext && rawNext !== '/login' && !rawNext.startsWith('/login?') ? rawNext : '/';

  // 已登录（且凭证未过期）才回跳。
  // 这里的判据必须与 RequireRole 完全一致：过去只看 token 非空就回跳，
  // 而守卫认为已过期，于是 /report → /login?next=/report → /report 无限循环。
  if (token && isAuthenticated()) {
    return <Navigate to={nextPath} replace />;
  }

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setConfirm('');
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError('请输入用户名与密码');
      return;
    }
    if (mode === 'register') {
      if (password.length < 6) {
        setError('密码至少 6 位');
        return;
      }
      if (password !== confirm) {
        setError('两次输入的密码不一致');
        return;
      }
    }
    setLoading(true);
    setError(null);
    try {
      const r =
        mode === 'login'
          ? await authApi.login(username.trim(), password)
          : await authApi.register(username.trim(), password);
      setSession(r.access_token, r.user, r.expires_at ?? null);
      navigate(nextPath, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '请求失败，请检查后端服务');
    } finally {
      setLoading(false);
    }
  };

  const inputCls =
    'w-full rounded-md border border-hair bg-surface px-3 py-2 text-xs text-ink outline-none transition-colors focus:border-brand-400';

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm rounded-lg border border-hair bg-surface p-6 shadow-sm">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-500 text-sm font-bold text-white">
            AQ
          </div>
          <div>
            <h1 className="text-base font-semibold text-ink">Alpha Quant Platform</h1>
            <p className="text-2xs text-ink-muted">
              {mode === 'login' ? '请登录以继续' : '注册新账号'}
            </p>
          </div>
        </div>

        {registerEnabled && (
          <div className="mt-4 grid grid-cols-2 gap-1 rounded-md bg-surface-sunken p-1">
            <button
              type="button"
              onClick={() => switchMode('login')}
              className={`rounded px-3 py-1.5 text-xs transition-colors ${
                mode === 'login'
                  ? 'bg-surface font-medium text-ink shadow-sm'
                  : 'text-ink-muted hover:text-ink-secondary'
              }`}>
              登录
            </button>
            <button
              type="button"
              onClick={() => switchMode('register')}
              className={`rounded px-3 py-1.5 text-xs transition-colors ${
                mode === 'register'
                  ? 'bg-surface font-medium text-ink shadow-sm'
                  : 'text-ink-muted hover:text-ink-secondary'
              }`}>
              注册
            </button>
          </div>
        )}

        <form onSubmit={submit} className="mt-4 space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs text-ink-secondary">用户名</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder={mode === 'login' ? '如 testadmin' : '3~64 位字母/数字/_. -'}
              className={inputCls}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-ink-secondary">密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              placeholder={mode === 'register' ? '至少 6 位' : '••••••••'}
              className={inputCls}
            />
          </label>
          {mode === 'register' && (
            <label className="block">
              <span className="mb-1 block text-xs text-ink-secondary">确认密码</span>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                placeholder="再次输入密码"
                className={inputCls}
              />
            </label>
          )}

          {error && (
            <div className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-xs text-danger">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-brand-500 py-2 text-xs font-medium text-white
              transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:bg-hair2">
            {loading ? (mode === 'login' ? '登录中…' : '注册中…') : mode === 'login' ? '登录' : '注册并登录'}
          </button>
        </form>

        <div className="mt-4 border-t border-hair pt-3 text-2xs leading-relaxed text-ink-muted">
          {mode === 'login' ? (
            <>
              <p>
                角色权限：<span className="text-ink-secondary">只读 / 研究员 / 管理员</span>
                （由后端 RBAC 控制）。管理员可用
                <code className="mx-1 rounded bg-surface-sunken px-1">scripts/create_admin.py</code>
                创建账号。
              </p>
              <p className="mt-1.5">
                登录后才可访问「系统设置」等需要鉴权的页面；
                行情、选股等只读页面无需登录。
              </p>
              {registerEnabled === false && (
                <p className="mt-1.5">自助注册已由管理员关闭，如需账号请联系管理员。</p>
              )}
            </>
          ) : (
            <>
              {/* 披露口径：注册账号的默认权限由后端决定，避免用户误以为注册即管理员 */}
              <p>
                注册账号默认角色为
                <span className="mx-1 text-ink-secondary">只读（viewer）</span>
                ；研究员/管理员权限需由管理员在后端提升。
              </p>
              <p className="mt-1.5">注册成功后将自动登录。</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
