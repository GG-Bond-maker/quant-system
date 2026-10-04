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
  /** 20s — 批量实时行情：`/market/quotes`（≤200 只，多页面共享一次抓取）
   *
   * 2026-10-04 上线前全检（T-2）：该值原先**裸写**在 `market.ts:91`（`20_000`），
   * 而当时的闸门正则 `\d[\d_]{2,}_000|\d{4,}` 恰好漏掉全部 10s~90s 档位，
   * 于是"`src/api/**` 不得出现裸超时字面量"这条边界对它**静默失效**。
   * 正则已修（见 `RAW_TIMEOUT` 注释），此处补登记档位以消除该违规。 */
  quote: 20_000,
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
 * **服务端预算被刻意收紧到本表超时之下**的路径（`路径 → 前端超时`）。
 *
 * ## 为什么需要第二张登记表（2026-10-04 上线前全检 T-1）
 *
 * `LONG_PATHS` 只守「**误杀**」方向：服务端预算必须 **>** 前端超时。
 * 但反方向从未设防 —— 若服务端预算**远大于**前端超时，用户早已断开，
 * 服务端还在烧 worker（且 `asyncio.to_thread` 里的重算**不可取消**）。
 *
 * 真实代价（`backend-run-1003.log` / `backend-run-1003-r6.log` / `backend-run.log`）：
 * 这 4 条路径原先都落回 240s 默认预算，实测共 6 次
 * `[timeout-guard] …超时（>240s）：已取消处理并回写 HTTP 504`，
 * 而前端超时只有 60~120s ⇒ **白烧 120~180s 的 worker**，用户还只拿到网络错误
 * （而非带 `code=50400` 的结构化错误）。
 *
 * ## 两侧约定
 *
 * 后端 `timeout_guard.py` 的 `_PATH_MAX_BUDGETS` 给出预算，且必须
 * **严格小于**本表的值（否则等于没收紧）。闸门 C6 双向校验：
 *  - 本表有、后端 `_PATH_MAX_BUDGETS` 无 ⇒ FAIL（收紧未生效）；
 *  - 后端 `_PATH_MAX_BUDGETS` 有、本表无 ⇒ FAIL（孤儿收紧）；
 *  - 后端预算 ≥ 本表值 ⇒ FAIL（没有真正低于前端超时）。
 *
 * ⚠️ **同一路径不得同时出现在 `LONG_PATHS` 与 `CAPPED_PATHS`** ——
 * 语义互斥（一个要求调大、一个要求压低），闸门 C6 会直接 FAIL。
 */
export const CAPPED_PATHS: Readonly<Record<string, number>> = {
  // 前端 60s（TIMEOUT.medium）→ 后端 48s = 60 × 0.8
  '/api/v1/settings': TIMEOUT.medium,
  // 前端 60s（TIMEOUT.medium）→ 后端 48s；冷路径 = 全量 parquet footer 扫描
  '/api/v1/ops/lineage': TIMEOUT.medium,
  // 前端 90s（TIMEOUT.extended）→ 后端 72s；冷扫 3.65 万 footer
  '/api/v1/datacenter/datasets': TIMEOUT.extended,
  // 前端 120s（TIMEOUT.heavy）→ 后端 96s；含 45s 扫描闸门等待 + 冷扫
  '/api/v1/datacenter/mirror/status': TIMEOUT.heavy,

  // ---- P-8a 追加（2026-10-04）：新迁入专用计算池的重活端点 ----
  //
  // 与上面 4 条**性质不同**：上面是"被并发拖慢"的只读扫描；下面 4 条是
  // **用户发起的真实重计算**（回测 / 信号分析 / 参数寻优 / 压力测试），
  // 耗时即真实工作量。故这里的收紧是**行为变更**：
  // 若某次回测本身需要 >96s，原先前端 120s 能等到结果，现在服务端 96s 先回 504。
  //
  // 取舍依据（详见后端 `timeout_guard._PATH_MAX_BUDGETS` 的同段注释）：
  //   ① 这 4 条此前**零延迟样本**（本机 metrics 从未出现调用）；
  //   ② 它们原先落回 240s 默认预算而前端只等 120/180s ⇒ 变慢时用户早断、
  //      服务端白烧 60~120s，且 4 条会一起吃专用池（默认 6 槽）。
  // 上线后若实测确有 >96s 的合法回测，**上调**这些值即可（只要仍 < 前端超时，
  // C6 闸门就继续成立）——不要为了消警告而删掉登记。
  //
  // 前端 120s（TIMEOUT.heavy）→ 后端 96s
  '/api/v1/backtest/run': TIMEOUT.heavy,
  // 前端 120s（TIMEOUT.heavy）→ 后端 96s
  '/api/v1/backtest/signal-analysis': TIMEOUT.heavy,
  // 前端 180s（TIMEOUT.export）→ 后端 144s
  '/api/v1/research/optimize': TIMEOUT.export,
  // 前端 180s（TIMEOUT.export）→ 后端 144s
  '/api/v1/research/stress-test': TIMEOUT.export,

  // ---- P1 追加（2026-10-04 上线前全检 T-1 续）：重活端点"白烧方向"补登记 ----
  //
  // 上述 8 条之外，审计还点名一批**重活端点**：它们此前全部落回后端 240s 默认预算，
  // 而前端超时只有 20~180s ⇒ 用户早断、服务端白烧 60~225s 的 worker
  // （`asyncio.to_thread` / 计算池里的重算**不可取消**）。
  //
  // 逐条**回前端源码核实**了真实超时（不照抄审计数字），路径与后端路由**逐字一致**
  // （含 `/api/v1` 前缀）。每条后端上限 = 本值 × 0.8（见 `CAPPED_BUDGET_RATIO`）。
  //
  // ⚠️ 同一路径若已在 `LONG_PATHS` 出现则**不得**在此登记（语义互斥，闸门 C6 FAIL）。
  // 下列路径均不在 `LONG_PATHS`。
  //
  // 批量实时行情（`market.ts:91` = TIMEOUT.quote 20s）→ 16s
  '/api/v1/market/quotes': TIMEOUT.quote,
  // 选股三兄弟（`screener.ts:26/42/63` = TIMEOUT.aggregate 30s）→ 24s
  '/api/v1/screener': TIMEOUT.aggregate,
  '/api/v1/screener/stats/series': TIMEOUT.aggregate,
  '/api/v1/screener/stocks': TIMEOUT.aggregate,
  // 执行中心（`production.ts:276/282/284` = TIMEOUT.aggregate 30s）→ 24s
  // ⚠️ `/desk/orders` 的 GET(30s) 与 POST placeOrder(默认 15s) **共用同一路径**；
  // 上限按较长的 GET 取 24s。POST 是轻量 DB 写入，24s 预算对它无害。
  '/api/v1/desk/fills/run': TIMEOUT.aggregate,
  '/api/v1/desk/orders': TIMEOUT.aggregate,
  '/api/v1/desk/account': TIMEOUT.aggregate,
  // 归因（`production.ts:298` = TIMEOUT.export 180s）→ 144s
  '/api/v1/desk/attribution': TIMEOUT.export,
  // 研究总览（`research.ts:29` = TIMEOUT.aggregate 30s）→ 24s
  '/api/v1/research/overview': TIMEOUT.aggregate,
  // 因子工作室（`production.ts:47` = TIMEOUT.medium 60s）→ 48s
  '/api/v1/studio/mining/start': TIMEOUT.medium,
  // 因子工作室重算（`production.ts:56/61/67` = TIMEOUT.heavy 120s）→ 96s
  // ⚠️ `/studio/factors` 的 GET(默认 15s) 与 POST saveFactor(120s) 共用路径；
  // 上限按 POST 取 96s，GET 是轻量列表查询，不受影响。
  '/api/v1/studio/alpha-eval': TIMEOUT.heavy,
  '/api/v1/studio/factor-report': TIMEOUT.heavy,
  '/api/v1/studio/factors': TIMEOUT.heavy,
  // NL→因子（`monitor.ts:127` = TIMEOUT.heavy 120s）→ 96s
  '/api/v1/studio/nl-to-factor': TIMEOUT.heavy,
  // 因子健康度真实计算（`monitor.ts:120` = TIMEOUT.heavy 120s）→ 96s
  '/api/v1/monitor/run': TIMEOUT.heavy,
  // AI 日报生成（`monitor.ts:125` = TIMEOUT.medium 60s）→ 48s
  '/api/v1/report/daily/generate': TIMEOUT.medium,
  // 组合回测（`portfolio.ts:17` = TIMEOUT.heavy 120s）→ 96s
  '/api/v1/portfolio/backtest': TIMEOUT.heavy,
  // 数据质量全量扫描（`production.ts:177` = TIMEOUT.export 180s）→ 144s
  '/api/v1/ops/quality-scan': TIMEOUT.export,
  // 数据中心质量块（`datacenter.ts:74` = TIMEOUT.extended 90s）→ 72s
  '/api/v1/datacenter/quality': TIMEOUT.extended,
  // 导出三兄弟（`export.ts:15/27/37`，`download()` 固定 TIMEOUT.export 180s）→ 144s
  '/api/v1/export/screener': TIMEOUT.export,
  '/api/v1/export/backtest': TIMEOUT.export,
  '/api/v1/export/strategy-backtest': TIMEOUT.export,
};

/** 收紧口径：后端预算必须 ≤ 前端超时 × 本系数（留出余量，避免两侧同时到点）。 */
export const CAPPED_BUDGET_RATIO = 0.8;

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
