/**
 * 统一后端响应结构（与 backend/app/core/errors.py 的 APIResponse 一一对应）。
 * 约定：HTTP 恒为 200，业务错误通过 code 字段区分（0 = 成功）。
 */
export interface APIResponse<T = unknown> {
  /** 0 表示成功；非 0 为业务错误码（40000 参数 / 40400 不存在 / 51001 数据为空 ...） */
  code: number;
  /** "ok" 或错误提示 */
  message: string;
  /** 业务数据 */
  data: T | null;
  /** 请求链路 Trace ID（12 位 hex），可用于日志检索 */
  trace_id: string;
  /** 服务端毫秒时间戳 */
  ts: number;
}

/** 业务错误码常量（与后端 errors.py 对齐；由 `test_error_code_governance.py` 双向对账） */
export const ERR = {
  PARAMS: 40000,
  /** 策略回测专用（40010–40019）：数据/区间/参数校验，R9 起双向登记 */
  BT_SYMBOL_NO_DATA: 40010,
  BT_SYMBOL_INSUFFICIENT: 40011,
  BT_RANGE_OUT_OF_DATA: 40012,
  BT_WINDOW_ORDER: 40013,
  BT_MA_ORDER: 40014,
  BT_STRATEGY_UNKNOWN: 40015,
  BT_OPTIMIZE_PARAM: 40016,
  BT_WF_TOO_SHORT: 40017,
  BT_OPTIMIZE_NO_GRID: 40018,
  BT_OPTIMIZE_FAILED: 40019,
  UNAUTHORIZED: 40100,
  TOKEN_EXPIRED: 40101,
  INVALID_TOKEN: 40102,
  RATE_LIMITED: 40103,
  CREDENTIALS: 40104,
  USER_EXISTS: 40105,
  REGISTER_DISABLED: 40106,
  REGISTER_LIMITED: 40107,
  FORBIDDEN: 40300,
  NOT_FOUND: 40400,
  PIPELINE_BUSY: 40900,
  SYSTEM: 50000,
  /** 未捕获的 BaseException 已被兜底中间件接住（panic containment） */
  PANIC_CONTAINED: 50001,
  /** 服务未就绪（运维探针专用：`/health/ready` 同时回 HTTP 503；业务端点不会返回它） */
  NOT_READY: 50300,
  /** 全局请求超时兜底（后端 `core/timeout_guard.py` 同时回 HTTP 504；基础设施层码） */
  REQUEST_TIMEOUT: 50400,
  DATA_SOURCE: 51000,
  DATA_EMPTY: 51001,
  TRAIN: 52000,
  INFER: 52001,
  LLM_UNAVAILABLE: 53000,
  EXPR_INVALID: 53001,
} as const;
