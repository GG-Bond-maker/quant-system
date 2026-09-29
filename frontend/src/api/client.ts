/**
 * Axios 实例与统一响应处理。
 *
 * 约定：
 * - 后端 HTTP 恒 200，body = { code, message, data, trace_id, ts }；
 * - 响应拦截器把 resp.data 解包为业务 data 域；
 * - code !== 0 时抛出 ApiError（携带业务错误码与 trace_id），
 *   调用方只需 try/catch ApiError，无需感知响应包装。
 */
import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios';
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

/** 已登记的业务码集合（来源：`types/api.ts` 的 `ERR`，与后端 `core/errors.py` 双向对账）。 */
const REGISTERED_CODES: ReadonlySet<number> = new Set(Object.values(ERR));

function apiErrorMessage(body: APIResponse): string {
  if (body.code === ERR.PIPELINE_BUSY) {
    return '数据流水线正在执行，请稍后重试';
  }
  const base = body.message || `业务错误 code=${body.code}`;
  // [AQP R9 错误码治理] 未知码**显式标记**：历史上 `datacenter`/`backtest` 曾抛出
  // 15 个两端码表都没有的裸码（`4002/5000/4003/5001/40010~40017`），前端只能拿到
  // 文案、无法分类，QA 也看不出"码表缺项"。裸码现已清零并有构建期断言兜底，这里
  // 再留一道运行时标记，让漏网的新码第一眼就能被识别。
  if (body.code !== 0 && !REGISTERED_CODES.has(body.code)) {
    return `${base}（未登记错误码 ${body.code}）`;
  }
  return base;
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

/** 网络层重试的退避基数（毫秒）；实际等待 = 基数 × [0.8, 1.2) 随机抖动。 */
const RETRY_BACKOFF_MS = 600;

/** 挂在 axios config 上的重试元数据：`retry` 为调用方 opt-out 开关，`__aqpRetried` 防重复重试。 */
interface RetryableRequestConfig {
  retry?: boolean;
  __aqpRetried?: boolean;
}

/**
 * 判定一次网络层错误是否值得重试（P2-3）。
 *
 * 仅覆盖两类「瞬时、且重放无副作用」的故障：
 *   1) 请求未拿到任何响应（连接失败 / 后端未启动 / DNS 失败）；
 *   2) 网关类 5xx：502 Bad Gateway / 503 Service Unavailable / 504 Gateway Timeout。
 *
 * 以下一律**不**重试（判定顺序关键：取消与超时的 `response` 同样为空，必须先排除）：
 *   - `ERR_CANCELED` 或 `config.signal.aborted`：用户主动取消 / 页面卸载，
 *     重试会复活已废弃的请求并可能写回已卸载组件。
 *   - `ECONNABORTED`（前端超时，client 默认 15s）——**本次设计的核心决策**：
 *     后端 HTTP 恒 200 且长任务是真在跑的（如冷算 20~25s 的聚合接口），
 *     前端 15s 断开只是「前端放弃等待」，服务端仍在继续计算；此时重试等于把同一份
 *     长计算再提交一次，只会加剧后端负载，而用户侧拿到的依旧是超时。
 *     故超时明确不重试，是否放宽 timeout 交由调用方（如聚合接口显式传 timeout）决定。
 *   - 4xx（401/403/404/422）：请求本身有问题，重放不会变好。
 *   - HTTP 500：后端真实异常，重试大概率仍是 500，且可能重复触发写副作用。
 *   - HTTP 200 但业务 code!==0：属业务错误，在成功分支已抛 ApiError，不会进入本函数。
 */
export function isRetryableNetworkError(error: AxiosError): boolean {
  // 取消优先：aborted 请求的 response 为空，不先拦截会被误判成「连接失败」。
  if (error.code === 'ERR_CANCELED' || error.config?.signal?.aborted === true) {
    return false;
  }
  // 前端超时不重试（理由见函数注释）。
  if (error.code === 'ECONNABORTED') {
    return false;
  }
  // 无响应 = 连接层失败，可安全重放。
  if (!error.response) {
    return true;
  }
  // 仅网关类 5xx 视为瞬时故障；其余状态码（含 500、4xx）不重试。
  return (
    error.response.status === 502 ||
    error.response.status === 503 ||
    error.response.status === 504
  );
}

/** 将 axios 网络层错误统一翻译为 ApiError（code = -1），供重试与非重试路径共用。 */
function toNetworkApiError(error: AxiosError): ApiError {
  const msg =
    error.code === 'ECONNABORTED'
      ? '请求超时，请稍后重试'
      : error.response
        ? `服务异常（HTTP ${error.response.status}）`
        : '网络错误，请检查后端服务是否启动';
  return new ApiError(msg, -1, undefined, error.cause);
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
  async (error: AxiosError<APIResponse>) => {
    const body = error.response?.data;
    if (isApiEnvelope(body)) {
      if (AUTH_ERROR_CODES.has(body.code)) handleAuthFailure();
      throw new ApiError(apiErrorMessage(body), body.code, body.trace_id, body.data);
    }

    // —— P2-3：幂等 GET/HEAD 的网络层受限重试 ——
    // 条件全部满足才重放：可重试的网络错误 && 方法为 GET/HEAD && 未 opt-out && 尚未重试过。
    const config = error.config as
      | (InternalAxiosRequestConfig & RetryableRequestConfig)
      | undefined;
    const method = config?.method?.toLowerCase();
    const shouldRetry =
      isRetryableNetworkError(error) &&
      (method === 'get' || method === 'head') &&
      config?.retry !== false &&
      config?.__aqpRetried !== true;

    if (config && shouldRetry) {
      // 退避期间请求可能已被取消（页面卸载）：abort 后不得再重放。
      if (config.signal?.aborted) {
        throw toNetworkApiError(error);
      }
      config.__aqpRetried = true;
      // ±20% 随机抖动，避免多个失败请求在同一时刻齐步重试形成流量尖峰。
      const delay = RETRY_BACKOFF_MS * (0.8 + Math.random() * 0.4);
      await new Promise((resolve) => setTimeout(resolve, delay));
      // 复用原 config 重放：params / timeout / signal / headers 全部保留。
      // 重放会再次经过本拦截器；若仍失败，__aqpRetried 已置位不会二次重试，
      // 其抛出的 ApiError 自然向上传播 —— 即「重试失败时以重试那次的错误为准」，
      // 因为它比首次错误更新、更接近当前后端状态。
      return client(config);
    }

    throw toNetworkApiError(error);
  },
);

// ---------------- 便捷方法（返回值即业务 data 域） ----------------
/** 可选请求控制项。signal 用于在路由切换时取消仍在飞行的请求。 */
export interface RequestOptions {
  signal?: AbortSignal;
  /**
   * 是否允许网络层受限重试（仅对 GET/HEAD 生效，默认 true）。
   * 传 false 可关闭，用于上层已有自己的重试策略时避免叠加放大请求量（如 SWR）。
   */
  retry?: boolean;
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
    // 仅显式 opt-out 时下发，保持默认行为（开启）不产生额外 config 字段。
    ...(options?.retry === false ? { retry: false } : {}),
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
