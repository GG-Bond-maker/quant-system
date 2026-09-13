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

/** 业务错误码常量（与后端 errors.py 对齐） */
export const ERR = {
  PARAMS: 40000,
  NOT_FOUND: 40400,
  UNAUTHORIZED: 40100,
  FORBIDDEN: 40300,
  SYSTEM: 50000,
  DATA_SOURCE: 51000,
  DATA_EMPTY: 51001,
  TRAIN: 52000,
  INFER: 52001,
} as const;
