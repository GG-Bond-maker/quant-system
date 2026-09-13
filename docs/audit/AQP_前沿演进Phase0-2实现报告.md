# AQP 前沿演进 Phase 0-2 实现报告

> 实现时间：2026-09-03
> 范围：《AQP_前沿演进评审与落地路线》Phase 0-2 全部条目：
> 5-1 因子失效预警 + 4-2 半衰期调优 + 5-2 漂移检测/自动重训/调度入口 + 1-1 NL-to-Factor + 1-3 模板版 AI 日报
> 验证：pytest **299 passed / 5 skipped**（新增 17 例）· tsc 0 错 · vite build OK · 真实服务全链路实测

---

## 一、交付清单

### 后端（8 个文件改动 / 4 个新文件）

| 文件 | 内容 |
|---|---|
| `app/ml/monitor.py` **新** | 监控核心：逐日截面 RankIC（真实 predictions × hfq 前向收益， spearman）；多周期 IC（H=1/2/3/5/10）+ 指数衰减拟合半衰期；PSI 漂移（近 20 交易日 vs 前 250 交易日基线，分位分箱）；状态机 healthy/watch/degraded/unknown（反向或 1.5σ 外 → degraded，1σ 外 → watch；PSI>0.25 → degraded，>0.10 → watch）；快照 + 状态日志（20 条）持久化 app_state；状态变化经 `events` 推顶栏铃铛；自动重训（12h 间隔守卫 + 防并发 + promote 门禁） |
| `app/api/v1/monitor.py` **新** | `GET /monitor/health`（读快照）、`POST /monitor/run`（researcher，立即计算） |
| `app/api/v1/report.py` **新** | 模板版 AI 日报：数据面/因子健康度/模拟盘与执行（当日成交、冲击与基差 bps）/最近事件/风险提示 五节 + tips；结构化 sections + 等价 markdown；KV 持久化最近 30 期；`GET /report/daily`（含 history）、`POST /report/daily/generate`（researcher） |
| `app/core/scheduler.py` **新** | 晚间例行调度（默认 17:30，60s 检查循环，与 autoSync 同模式）：`build_universe→build_features→infer→screener_dump`（pipeline steps 子集，data_jobs 幂等）→ monitor → report；当日幂等（app_state 记录）、非交易日跳过、单步失败不阻断后续；`startup_catchup` 启动补跑缺失的快照/日报 |
| `app/api/v1/studio.py` | `POST /studio/nl-to-factor`（researcher）：LLM（ollama `/api/chat` 或 openai 兼容 `/v1/chat/completions`，httpx）→ 表达式提取（围栏/前缀清理）→ `alpha_expr.parse_expr` **AST 白名单静态校验**（拒绝属性访问/下标/未知算子与字段）→ 通过后 `evaluate_expr_detail` 真实截面 RankIC 评估（与 alpha-eval 同口径） |
| `app/core/config.py` | 新增 10 项配置：`EVENING_ROUTINE_ENABLED/TIME`、`AUTO_RETRAIN_ON_DRIFT`、`RETRAIN_MIN_INTERVAL_HOURS`、`LLM_PROVIDER/BASE_URL/API_KEY/MODEL/TIMEOUT_SECONDS`（.env 驱动，LLM_PROVIDER=none 时 NL 入口返回 53000 指引） |
| `app/core/errors.py` | 新错误码：`ERR_LLM_UNAVAILABLE=53000`、`ERR_EXPR_INVALID=53001` |
| `app/data/pipeline.py` | 新步骤 `step_build_universe`（修复报告 §11 遗留项落地）；`run_pipeline` 支持 `steps` 子集（晚间例行在 autoSync 同步后跳过 update_daily/validate）；未知步骤名 fail-fast；`build_universe` 依赖 instrument 表，故注册于 STEP_FUNCTIONS 但不进默认 STEPS（不破坏全新环境/测试） |
| `app/main.py` / `router.py` | lifespan 挂 evening_routine_scheduler + startup_catchup；注册 `/monitor`、`/report` 路由 |

### 前端（4 个新文件 / 4 个改动）

| 文件 | 内容 |
|---|---|
| `api/monitor.ts` **新** | MonitorSnapshot/DailyReport/NlFactorResult 类型 + API |
| `pages/Report/index.tsx` **新** | AI 日报页（`/report`）：期数选择（history）、风险提示卡、分节渲染（**加粗** 最小解析）、Markdown 原文折叠、重新生成（未授权展示信封错误） |
| `components/FactorHealthCard.tsx` **新** | 因子健康度卡：状态灯（健康/观察/降级）、近 15 日 MeanIC/ICIR、半衰期、PSI max 与最大漂移因子、立即运行按钮；挂在数据质量页顶部 |
| `pages/FactorStudio/NlFactorCard.tsx` **新** | NL-to-Factor 卡：自然语言输入 + 示例 + 校验徽标（合法/失败原因）+ 真实 RankIC 评估指标；挂在因子工作室顶部 |
| `App.tsx` / `Sidebar.tsx` / `Topbar.tsx` | `/report` 路由；侧边栏「AI 日报」入口（新文档图标）；铃铛事件标签新增 监控/日报 |

---

## 二、真实数据全链路实测（启动即触发，闭环验证）

服务重启后 `startup_catchup` 自动完成了一次完整闭环，**每个环节都是真实计算**：

```
startup_catchup → run_monitor（256 期 predictions × 42 因子 PSI）
  → IC 健康：近 15 日 Mean RankIC = 0.1478，ICIR = 1.256
  → PSI 漂移：max 0.7228（ma_gap_250）> 0.25 → 总体 state = degraded
  → 事件推送「因子健康度【降级】」→ 顶栏铃铛可见
  → 自动重训触发（守卫通过）：候选 20260903_014155_monitor
      valid RankIC = 0.0192，门禁「通过全部门槛且优于/不劣于当前生产模型」
  → promote = 通过（8 秒完成，early-stop 极早符合该因子现状）
  → AI 日报生成（2 条风险提示）→ 事件推送「AI 日报已生成」
```

半衰期返回 None +「IC 未随期数衰减（斜率非负）」——实测 MeanIC 随 H 上升（0.034→0.05），对该信号「无衰减」是诚实结论而非缺陷。IC 尾部末 2 日 `ic=null, n_symbols=121`：horizon-5 前向收益尚未到期，语义已修正为「预测覆盖数」与「可评估性」分开披露。

---

## 三、需要用户知悉的三件事

1. **生产模型已被自动替换**（设计行为）：`lgbm_v1_20260830_095940_repaired` → `20260903_014155_monitor`（门禁通过）。如不希望自动重训上线，`.env` 设 `AUTO_RETRAIN_ON_DRIFT=false`（监控/告警/日报不受影响）。PSI 0.72 的漂移（长窗口因子 ma_gap_250/vol_60 最大）本身是值得研究的真实信号——可能是市场机制变化，也可能是 8 月末数据补齐导致分布跳变，建议人工复核。
2. **predictions 落后（最新 20260828）**：晚间例行只对「今天」跑 infer，09-02 的预测缺口需手动补（`python -m app.data.pipeline --date 2026-09-02` 或等今晚 17:30 例行后自然只覆盖当日）。日报已把该情况列为风险提示。
3. **NL-to-Factor 的 LLM 路径未实测**（本机未配置 provider）：已实测 53000（未配置指引）/40100（未登录）契约；生成→校验→评估三层中，后两层由既有测试与真实评估器覆盖。`.env` 配置 `LLM_PROVIDER=ollama`（或 openai 兼容）+ `LLM_BASE_URL/MODEL` 后即可用，首次使用建议检查生成质量。

## 四、已知的边界与取舍

- KS 检验未实现（方案提了 PSI/KS 两个）：PSI 已覆盖漂移判定且无新依赖（scipy 不在依赖清单）；KS 可后续按需补。
- 日报的 Brinson 归因节未纳入（归因逻辑耦合在 desk 端点闭包内，复用需小重构）；当前以「持仓贡献 Top/Bottom + 执行质量」替代，字段均真实。
- 晚间例行在测试环境不会触发（时间门控 + conftest 隔离），`build_universe` 步骤在 instrument 空表环境会 fail-fast——这是有意的（不造数据）。
- 重训守卫：12h 内不重复、运行中不并发；重训失败会推送失败事件并记录 KV。
