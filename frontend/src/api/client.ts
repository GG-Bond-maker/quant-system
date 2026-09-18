/**
 * Axios 实例与统一响应处理。
 *
 * 约定：
 * - 后端 HTTP 恒 200，body = { code, message, data, trace_id, ts }；
 * - 响应拦截器把 resp.data 解包为业务 data 域；
 * - code !== 0 时抛出 ApiError（携带业务错误码与 trace_id），
 *   调用方只需 try/catch ApiError，无需感知响应包装。
 */
import axios, { AxiosError, AxiosInstance } from 'axios';
import type { APIResponse } from '@/types/api';
import { ERR } from '@/types/api';
import { useAuthStore } from '@/stores/useAuthStore';

/**
 * 认证类业务错误码（与 backend/app/core/errors.py 保持一致）。
 * 命中即视为“当前凭证不可用”：仅清空本地会话；跳转由路由守卫负责。
 */
const AUTH_ERROR_CODES: ReadonlySet<number> = new Set([
  ERR.UNAUTHORIZED,
  ERR.TOKEN_EXPIRED,
  ERR.INVALID_TOKEN,
  // ERR.FORBIDDEN：已登录但角色不足，由页面展示 ApiError，不清会话。
  // ERR.PIPELINE_BUSY：资源状态冲突，同样不得清除有效登录会话。
]);

/**
 * 清除无效本地会话。
 *
 * 响应拦截器不进行导航，避免某个可选请求的 401 中断公开页面及同批请求。
 * 受保护路由由 RequireAuth / RequireRole 根据已清除的会话状态显式跳转登录页。
 */
function handleAuthFailure(): void {
  useAuthStore.getState().clear();
}

/**
 * 屏蔽后端语言/框架异常类名与堆栈片段，避免内部实现细节进入用户界面。
 * 业务友好文案原样保留；疑似内部异常统一退回稳定提示。
 */
export function sanitizeApiMessage(message: string, fallback = '服务暂不可用，请稍后重试'): string {
  const text = String(message ?? '').trim();
  if (!text) return fallback;
  const internalPattern = /(?:AQPException|Traceback|File "|\b[A-Za-z_]*(?:Error|Exception)\b|sqlalchemy|polars\.)/i;
  return internalPattern.test(text) ? fallback : text;
}

function apiErrorMessage(body: APIResponse): string {
  if (body.code === ERR.PIPELINE_BUSY) {
    return '数据流水线正在执行，请稍后重试';
  }
  return body.message || `业务错误 code=${body.code}`;
}

/** 业务错误：code 为后端业务错误码（网络层错误为 -1）。 */
export class ApiError extends Error {
  readonly code: number;
  readonly traceId?: string;
  readonly data?: unknown;

  constructor(message: string, code: number, traceId?: string, data?: unknown) {
    super(sanitizeApiMessage(message));
    this.name = 'ApiError';
    this.code = code;
    this.traceId = traceId;
    this.data = data;
  }
}

function isApiEnvelope(v: unknown): v is APIResponse {
  return (
    typeof v === 'object' &&
    v !== null &&
    'code' in v &&
    typeof (v as APIResponse).code === 'number'
  );
}

const client: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

// 请求拦截：附带 JWT（登录后签发；旧版手动配置的静态 Token 会在 store 初始化时迁移进来）
client.interceptors.request.use((config) => {
  const { token, isAuthenticated } = useAuthStore.getState();
  if (token && isAuthenticated()) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截：解包 data 域 / 抛出 ApiError
client.interceptors.response.use(
  (resp) => {
    const body: unknown = resp.data;
    if (isApiEnvelope(body)) {
      // 认证失败先清会话再抛错：调用方 catch 到时登录态已是干净的
      if (AUTH_ERROR_CODES.has(body.code)) {
        handleAuthFailure();
      }
      if (body.code !== 0) {
        throw new ApiError(apiErrorMessage(body), body.code, body.trace_id, body.data);
      }
      // 直接解包：后续 resp.data 即业务数据
      // eslint-disable-next-line no-param-reassign
      resp.data = body.data;
      return resp;
    }
    // 非统一包装（如静态文件），原样放行
    return resp;
  },
  (error: AxiosError<APIResponse>) => {
    const body = error.response?.data;
    if (isApiEnvelope(body)) {
      if (AUTH_ERROR_CODES.has(body.code)) handleAuthFailure();
      throw new ApiError(apiErrorMessage(body), body.code, body.trace_id, body.data);
    }
    const msg =
      error.code === 'ECONNABORTED'
        ? '请求超时，请稍后重试'
        : error.response
          ? `服务异常（HTTP ${error.response.status}）`
          : '网络错误，请检查后端服务是否启动';
    throw new ApiError(msg, -1, undefined, error.cause);
  },
);

// ---------------- 便捷方法（返回值即业务 data 域） ----------------
/** 可选请求控制项。signal 用于在路由切换时取消仍在飞行的请求。 */
export interface RequestOptions {
  signal?: AbortSignal;
}

export async function get<T>(
  url: string,
  params?: Record<string, unknown>,
  /** 单次请求超时（毫秒）；聚合接口（如 /panels 需并发访问多个外部数据源）需放宽 */
  timeout?: number,
  options?: RequestOptions,
): Promise<T> {
  const resp = await client.get<T>(url, {
    params,
    ...(timeout ? { timeout } : {}),
    ...(options?.signal ? { signal: options.signal } : {}),
  });
  return resp.data;
}

export async function post<T>(
  url: string,
  data?: unknown,
  timeout?: number,
  options?: RequestOptions,
): Promise<T> {
  const resp = await client.post<T>(url, data, {
    ...(timeout ? { timeout } : {}),
    ...(options?.signal ? { signal: options.signal } : {}),
  });
  return resp.data;
}

export async function put<T>(
  url: string,
  data?: unknown,
  timeout?: number,
  options?: RequestOptions,
): Promise<T> {
  const resp = await client.put<T>(url, data, {
    ...(timeout ? { timeout } : {}),
    ...(options?.signal ? { signal: options.signal } : {}),
  });
  return resp.data;
}

export async function del<T>(url: string, timeout?: number): Promise<T> {
  const resp = await client.delete<T>(url, timeout ? { timeout } : undefined);
  return resp.data;
}

/**
 * 文件下载（xlsx 等二进制流）。
 *
 * 与 get/post 的区别：/export/* 返回的是文件字节而非统一信封，
 * axios 拦截器对 Blob 做不了 isApiEnvelope 解包，因此这里自行处理两件事：
 *   1) 从 Content-Disposition 解析后端给的文件名（解析不到再用 fallback）；
 *   2) 若后端实际返回的是 JSON 错误信封（HTTP 200 + code != 0，如导出时
 *      本地无预测数据），必须手动解包抛 ApiError，否则用户会下载到一个
 *      损坏的 xlsx 而毫无提示。
 */
export async function download(
  url: string,
  fallbackName: string,
  params?: Record<string, unknown>,
  payload?: unknown,
): Promise<void> {
  const config = { responseType: 'blob' as const, params, timeout: 180_000 };
  const resp = payload !== undefined
    ? await client.post(url, payload, config)
    : await client.get(url, config);

  const blob = resp.data as Blob;
  if (blob.type.includes('application/json')) {
    let message = '导出失败';
    try {
      const env = JSON.parse(await blob.text()) as APIResponse;
      if (typeof env.code === 'number' && env.code !== 0) message = env.message || message;
    } catch { /* 保持默认提示 */ }
    throw new ApiError(message, -2);
  }

  const cd = (resp.headers['content-disposition'] as string | undefined) ?? '';
  const matched = /filename="?([^";]+)"?/i.exec(cd);
  const filename = matched?.[1] ?? fallbackName;

  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

export default client;
