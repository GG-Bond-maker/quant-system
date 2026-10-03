/**
 * 前端请求超时**集中表**（A-6 / F-3 修复，2026-10-02）。
 *
 * ## 为什么要有这个文件
 *
 * 修复前：全仓 **15 档**超时字面量（`10_000 … 600_000`）散落在 ~25 个文件里，
 * **没有任何单一登记点**。而后端 `core/timeout_guard.py` 只能靠**手工快照**对齐 ——
 * 它的模块 docstring 里写着「全仓前端超时实测清单（`frontend/src/api/*.ts`，2026-09-27 核对）」。
 * 手工快照必然漂移，且**没有任何机器校验**：前端把某个路径的超时调大、或新增一条长超时，
 * 后端不会知道 ⇒ 出现「前端还在等、服务端已经回 504」的**误杀**。
 *
 * ## 不变量（与后端 `timeout_guard.py` 模块 docstring 完全一致）
 *
 * > **服务端预算必须严格大于前端对同一路径的超时。**
 *
 * 该不变量**不是靠注释维持**，而是由 `scripts/check-timeout-consistency.mjs` 机器校验：
 *  - 它解析本文件的 `TIMEOUT` / `LONG_PATHS`，与后端 `_PATH_BUDGETS` /
 *    `REQUEST_TIMEOUT_SECONDS` 做双向比对；
 *  - 并强制 **`src/api/**` 不得再出现裸超时字面量**（必须引用本表）。
 *
 * ## 改超时的正确姿势
 *
 * 1. 普通端点：改这里对应的档位即可（`src/api/**` 里全部引用本表，一处生效）。
 * 2. **超过后端默认预算（`REQUEST_TIMEOUT_SECONDS`，默认 240s）的路径**：
 *    除了在这里加档位，**必须**同时在 `LONG_PATHS` 登记 `路径 → 超时`，
 *    并在后端 `_PATH_BUDGETS` 里给出**严格更大**的预算。漏了任一侧，
 *    `check-timeout-consistency` 会直接 FAIL。
 *
 * ## 边界约定：**请求超时只在 `src/api/**` 声明**（2026-10-02）
 *
 * 页面层（`src/pages/**`、`src/components/**`）**不得**出现请求超时 ——
 * 需要放宽就改本表。闸门的 C4 强制 `src/api/**` 无裸超时字面量，这条边界由此保证。
 *
 * ⚠️ **不要**把页面层的 `_000` 字面量搬进本表。取证（2026-10-02）显示页面层的
 * `_000` 绝大多数是**业务数值**，与超时无关：
 * `initCash: 1_000_000`（初始资金）、`order_amount: 20_000_000`（下单金额）、
 * `v >= 10_000`（亿→万亿单位换算）、`step={5_000_000}`（输入框步长）、
 * `BASE_MS = 1500 / MAX_MS = 12_000`（**轮询退避上限**）。
 *
 * 若将来要治理**轮询间隔**散落，那是**正交**问题：应另立 `POLL.*` 命名空间与独立闸门 ——
 * 间隔**没有**"服务端预算"这个对偶量，其不变量与本文件完全不同，混在一张表里会概念混淆。
 */

/** 各档位语义（毫秒）。数值即当前生产值，迁移不改行为。 */
export const TIMEOUT = {
  /** 10s — 轻量探针：事件流一次性票据、KPI 轻查询 */
  probe: 10_000,
  /** 15s — 客户端默认（`client.ts` 的 axios 默认值）：普通查询 / 设置读写 */
  default: 15_000,
  /** 30s — 页内聚合：`/etf/*`（后端预算 `_ETF_ENDPOINT_BUDGET_SECONDS=4.5s`，超时即结构化降级）、`/desk/*` */
  aggregate: 30_000,
  /** 60s — 中等聚合：数据集列表 / 血缘 / DAG 状态 / 因子挖掘启动 */
  medium: 60_000,
  /** 90s — 数据集与质量检查 */
  extended: 90_000,
  /** 100s — 研究页重查询 */
  research: 100_000,
  /** 120s — 重计算：回测 / 训练 / 镜像 / 生产链路 */
  heavy: 120_000,
  /** 180s — 导出与全量扫描：文件下载 / 质量扫描 / 归因 / 压力测试 */
  export: 180_000,
  /** 300s — 同步重跑全量流水线（后端 `_PATH_BUDGETS` 给 330s） */
  rerun: 300_000,
  /** 600s — 参数寻优（网格组合数 × 单次回测；后端 `_PATH_BUDGETS` 给 660s） */
  optimize: 600_000,
} as const;

/**
 * **超过后端默认预算的路径**必须在此登记（`路径 → 前端超时`）。
 *
 * 后端 `_PATH_BUDGETS` 必须给出**严格大于**此值的预算；两侧互为镜像，由闸门双向校验：
 *  - 前端有、后端无 ⇒ 该路径会被后端默认预算**误杀** ⇒ FAIL；
 *  - 后端有、前端无 ⇒ 后端留下**孤儿预算**（前端已不需要那么久）⇒ FAIL。
 *
 * ⚠️ 路径必须与后端 `_PATH_BUDGETS` 的键**逐字一致**（含 `/api/v1` 前缀）。
 */
export const LONG_PATHS: Readonly<Record<string, number>> = {
  '/api/v1/backtest/strategy-run': TIMEOUT.optimize,
  '/api/v1/ops/dag/rerun': TIMEOUT.rerun,
};

/** 全表最大值 —— 供闸门与文档引用，避免再出现"手工快照"。 */
export const MAX_TIMEOUT_MS = Math.max(...Object.values(TIMEOUT));

/**
 * 取**长超时路径**的超时值；路径未登记即**抛错**。
 *
 * ## 为什么需要它（而不是直接写 `TIMEOUT.rerun`）
 *
 * 闸门最初的 C3 用「**档位值**」做豁免：只要某档位的值出现在 `LONG_PATHS` 里，就跳过
 * "是否超过后端默认预算"的检查。这留下一个**真实漏洞** ——
 * 若有人给一条**新路径**直接写 `TIMEOUT.rerun`（300s）却忘了登记 `LONG_PATHS`，
 * 闸门会**放行**，而该路径会被后端 240s 默认预算**误杀**（前端还在等、服务端已回 504）。
 *
 * 修法：长超时**只能**经本函数取得 —— 它把**路径**变成实参，于是：
 *  - **运行时**：未登记立即抛错（开发期就炸，不会静默上线）；
 *  - **闸门（C5）**：可静态校验「`longTimeout` 的实参必须登记」+
 *    「保留档位不得在 `src/api/**` 裸用」+「登记的路径必须真被用到（无孤儿）」。
 *
 * 用法：`post('/api/v1/backtest/strategy-run', req, longTimeout('/api/v1/backtest/strategy-run'))`
 */
export function longTimeout(path: string): number {
  const ms = LONG_PATHS[path];
  if (ms === undefined) {
    throw new Error(
      `[timeouts] 路径 ${path} 未在 LONG_PATHS 登记。`
      + '超过后端默认预算（240s）的路径必须显式登记，'
      + '否则会被后端默认预算误杀（前端还在等、服务端已回 504）。',
    );
  }
  return ms;
}

/** 保留给 `LONG_PATHS` 的档位值 —— 这些值**不得**在 `src/api/**` 被裸用（闸门 C5）。 */
export const RESERVED_LONG_TIMEOUT_MS: ReadonlySet<number> = new Set(Object.values(LONG_PATHS));
