/**
 * 认证 API：对接后端 JWT + RBAC（POST /auth/login、POST /auth/register、
 * GET /auth/register/status、GET /auth/me）。
 *
 * 这两个端点此前在前端 0 处引用（审计 1.1）——后端实现了完整的
 * viewer / researcher / admin 三级权限，前端却完全没有登录入口。
 */
import { get, post } from './client';

export interface AuthUser {
  username: string;
  /** viewer / researcher / admin */
  role: string;
}

export interface LoginResult {
  access_token: string;
  token_type: string;
  /** 有效期（秒） */
  expires_in: number;
  /** 绝对过期时刻（ISO 8601 UTC） */
  expires_at: string;
  user: AuthUser;
}

/** 自助注册规则（GET /auth/register/status）：前端据此决定是否展示注册入口 */
export interface RegisterStatus {
  enabled: boolean;
  /** 注册后默认角色（后端保证不会是 admin） */
  default_role: string;
  min_password_length: number;
  username_pattern: string;
}

/** 角色中文名，用于界面展示 */
export const ROLE_LABEL: Record<string, string> = {
  viewer: '只读',
  researcher: '研究员',
  admin: '管理员',
};

export const authApi = {
  login: (username: string, password: string) =>
    post<LoginResult>('/api/v1/auth/login', { username, password }),

  /**
   * 自助注册：成功即返回与登录同构的 LoginResult（后端直接签发 JWT），
   * 前端拿到后写入会话即可，无需再补一次登录请求。
   */
  register: (username: string, password: string) =>
    post<LoginResult>('/api/v1/auth/register', { username, password }),

  /** 自助注册开关与规则（公开端点，登录页挂载时拉取一次） */
  registerStatus: () => get<RegisterStatus>('/api/v1/auth/register/status'),

  /** 返回当前用户信息（需要有效 JWT）；用于校验本地 Token 是否仍然有效 */
  me: () => get<AuthUser>('/api/v1/auth/me'),
};
