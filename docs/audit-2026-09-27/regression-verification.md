# AQP 修复后回归验证报告

- **验证日期**：2026-09-27
- **执行人**：严过关（Yan）· QA 工程师（独立复测，不采信工程师自报数据）
- **项目根**：`D:\Python_Project\Alpha Quant Platform`
- **后端**：`backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`（本次全程保持运行）
- **前端 dev**：`http://localhost:5199`（Vite proxy → 127.0.0.1:8000）
- **运行时验证栈**：真实 Chromium（playwright-core 1.55 + 本机已缓存 chromium-1234）驱动真实前端 dev server 打真实后端
- **原始证据**：
  - `regression_lineage.py` / `regression_lineage.out`
  - `regression_portfolio.py` / `regression_portfolio.out`
  - `regression_ui.cjs` / `regression_ui.out`
  - `smoke-write-endpoints.csv` / `probe_write_endpoints.py` / `probe_write_endpoints.out`

---

## 0. 结论速览

| 修复项 | 结论 | 依据 |
|---|---|---|
| ① research.ts `labYearly` timeout→120s | ✅ PASS | 静态确认 + 端点实测 ~19s 返回 200 |
| ② Research `labYearlyError` 独立错误态 | ✅ PASS | 静态确认 + tsc/build 通过 |
| ③ production.ts `lineage` timeout=60s | ✅ PASS（注释过时，见 §4） | 静态确认 |
| ④ DataQuality 血缘严格三态 | ✅ PASS | **运行时**：渲染成功，非永久"加载中" |
| ⑤ MarketOverview 双失败提示 + 重试 | ✅ PASS | **运行时**：正常路径 0 误报 |
| ⑥ Topbar 未登录不发请求 + allSettled | ✅ PASS | **运行时**：未登录 0 请求 / 已登录正常出结果 |
| ⑦ 后端 `/ops/lineage` 跨请求缓存 | ✅ PASS | **独立实测** 稳态 avg 22.7ms（原 32~38s） |
| ⑧ 后端组合回测错误码分流 | ✅ PASS | 5 例实测，40000/51001/code=0 语义全对 |

- **前端**：`tsc -b --force` EXIT=0；`vite build`（outDir=dist-yan-regression）EXIT=0
- **回归**：**未发现功能性回归**（6 项 UI 断言全绿）；发现 1 处注释过时 + 1 处文案被前端 sanitize 冲淡（均非功能性）
- **写端点补测**：33 个中 14 已实测 / 14 参数校验型探测 / 5 跳过；**0 个真实超时**；副作用复核 PRE==POST 全部无变更

---

## 1. 后端 `/api/v1/ops/lineage` 独立复测

### 1.1 稳态耗时（Redis 正常，6 轮）

| 轮次 | 耗时 |
|---|---|
| 1 | 40.6 ms |
| 2 | 30.6 ms |
| 3 | 13.4 ms |
| 4 | 13.5 ms |
| 5 | 12.6 ms |
| 6 | 25.3 ms |
| **统计** | **min 12.6 / max 40.6 / avg 22.7 ms** |

- 全部 `from_cache=True`，`nodes=13`。
- 与工程师自报 23.9 / 33.6 / 29.0 ms **同量级**，独立确认"32~38s → 毫秒级"成立（修复前该端点 20s 探测必超时，复测 32~38s）。

### 1.2 冷路径（证明原问题真实存在）

- 独立脚本直调 `_compute_lineage()` 纯计算：**33.68s**（nodes=13, edges=16）。
- 说明：毫秒级来自缓存命中，**不是**把扫描本身变快了。

### 1.3 缓存失效策略验证

| 验证点 | 方法 | 结果 |
|---|---|---|
| TTL 兜底 | 读 Redis 主键 TTL | 261s（配置 300s，随请求递减）✅ |
| 影子键窗口 | 读 `:swr` TTL | 2061s（300+1800=2100）✅ |
| SWR 过期回旧值 | 仅删主键、保留影子键后请求 | 39.4ms 返回 `stale=True`，**请求路径未扛冷扫** ✅ |
| 后台重建自愈 | 上一步后等待 40s 复查 | 主键恢复（TTL 294s），再请求 15.1ms `stale=None` ✅ |
| 日界换键 | 静态：`k_ops_lineage(date.today())` | 键含自然日，跨日自动换键，脏数据不会永久锁死 ✅ |
| Redis 不可用降级 | **另起实例** `REDIS_ENABLED=false`（:8001，`/health` 报 `redis:degraded`） | 首请求 **32.66s**（同步重建，进程内 LRU 兜底）→ 后续 0.04 / 0.01s ✅ |

> 结论：缓存不会把脏数据永久锁死；Redis 全离线时仍有降级路径（进程内 LRU），最坏冷路径 32.66s。

---

## 2. 组合回测错误码语义（`POST /api/v1/portfolio/backtest`）

| 入参 code | 期望 | 实测 status | 耗时 | bizcode | message |
|---|---|---|---|---|---|
| `600519.SH.XX` | 40000 | 200 | 0.03s | **40000** ✅ | 非法标的代码…（期望 6 位数字，可带单段后缀） |
| `abc` | 40000 | 200 | 0.03s | **40000** ✅ | 非法标的代码… |
| `600519.SH` | code=0 | 200 | 14.96s | **0** ✅ | ok |
| `600519` | code=0 | 200 | 0.06s | **0** ✅ | ok |
| `999999` | 51001 | 200 | 4.32s | **51001** ✅ | 部分资产数据获取失败: 999999: IndexError |

- 入参归一化在调 domain **之前**拦截：`600519.SH.XX` / `abc` 立即 40000，不再被下游误报成"数据为空"。
- `999999` 格式合法但无数据 → domain `ValueError` 前缀命中 → 51001。语义分流正确。
- ⚠️ 次要：`999999` 的 51001 文案里带了内部异常类名 `IndexError`（见 §5 遗留问题 2）。

---

## 3. 前端构建与运行时回归

### 3.1 构建

- `npx tsc -b --force` → **EXIT=0**
- `npx vite build --outDir dist-yan-regression --emptyOutDir` → **EXIT=0**，`✓ built in 6.99s`，747 模块，0 error 0 warning（避开 `dist/` 的 node-safe-delete 护栏）

### 3.2 运行时（Chromium 真实渲染，7/7 PASS）

| 断言 | 结果 | 明细 |
|---|---|---|
| 未登录-搜索**不发请求** | PASS | `/stock/search`+`/etf/list` 命中 **0** 次 |
| 未登录-显示登录引导 | PASS | "登录后可搜索" 出现 1 处 |
| 已登录-搜索**发出请求** | PASS | 命中 2 次（stock+etf） |
| 已登录-搜索出结果 | PASS | 下拉 1 条（茅台） |
| 已登录-不误显示登录引导 | PASS | 登录引导 0 处 |
| DataQuality-血缘非永久"加载中" | PASS | 最终状态=rendered |
| DataQuality-成功渲染图谱 | PASS | 含标题 + "节点状态为实时扫描/扫描于…"（22 个 svg） |
| MarketOverview-正常路径不误报 | PASS | `role=alert` 0 个，含行情数据 |

- 覆盖原正常路径：Topbar 已登录搜索仍正常出结果；MarketOverview 正常情况不误报错误条；DataQuality 成功时仍渲染图谱。**未发现回归。**

---

## 4. 交叉检查：前端 lineage timeout=60s 是否仍合理

- 后端**稳态**已降至 ms 级（12.6~40.6ms），60s 是**极度宽松**的兜底。
- 后端**真实最坏冷路径**（Redis 关 + 无预热 + 空缓存）实测 **32.66s < 60s**。
- 判断：**60s 仍然合理**——它恰好覆盖了"缓存全丢、同步重建"这一唯一会真正跑满 32s 的场景，不会误超时。
- ⚠️ 但 `production.ts:176-177` 的注释仍写"后端实测 32~38s … 必须显式放宽到 60s"，**注释已过时**，会误导后续维护者以为后端仍是 32s 级。建议改为"稳态 ms 级；60s 仅为 Redis 全离线冷路径兜底"。（不修改代码，仅报告。）

---

## 5. 任务 B：写操作端点补测（33 个）

- **数据文件**：`smoke-write-endpoints.csv`（33 行）
- **分类**：已实测 **14** / 参数校验型探测 **14** / 跳过 **5**
- **真实超时**：**0 个**（最慢 `export/backtest` 1.33s，`studio/factors` 1.16s，`ops/quality-scan` 1.00s）
- **副作用复核**（PRE vs POST 全等）：

```
alerts_rules   0 -> 0      OK
exclusion      1 -> 1      OK
orders         6 -> 6      OK
factors        0 -> 0      OK
sync_running   False -> False  OK
train_running  False -> False  OK
kill_switch    False -> False  OK
```

### 5.1 参数校验型探测（14 个，全部命中预期错误码，无真实执行）

| 端点 | 探测入参 | bizcode |
|---|---|---|
| `POST /alerts/rules` | 非法 rule_type | 40000 |
| `POST /datacenter/mirror/rebuild` | dataset=123（类型错） | 40000 |
| `POST /datacenter/sync` | mode=非法 | 40000 |
| `POST /datacenter/sync/auto` | time=99:99 | 40000 |
| `POST /datacenter/sync/fetch` | start/end=bad | 40000 |
| `POST /datacenter/text/import` | 缺 docs | 40000 |
| `POST /datacenter/train/start` | 缺 model | 40000 |
| `POST /desk/exclusion` | 非法 category | 40000 |
| `POST /desk/orders` | 非法 side | 40000 |
| `POST /ops/dag/rerun` | trade_date=9999-99-99 | 40000 |
| `PUT /settings/engine` | commission_pct="abc" | 40000 |
| `PUT /settings/preferences` | refresh_freq="abc" | 40000 |
| `POST /studio/factors` | expression="1 +" | 53001 |
| `POST /studio/mining/start` | 不存在字段 | 51001 |

### 5.2 已实测（14 个，无害最小参数）

- `POST /alerts/events/read`（ids=[不存在]→marked 0）、`PUT/DELETE /alerts/rules/999999`（→40400）
- `POST /datacenter/sync/cancel`（无任务→cancelled:false）、`POST /datacenter/train/cancel`（空闲）
- `POST /desk/exclusion/toggle`（不存在 id→51001）、`POST /desk/kill-switch`（置为当前值 false，幂等 no-op）
- `POST /export/backtest`（→xlsx 10495B, 1.33s）、`POST /export/strategy-backtest`（有效区间→xlsx 9414B, 0.66s）
- `POST /ops/quality-scan`（只读扫描 1.00s）、`POST /report/daily/generate`（幂等 0.05s）
- `POST /settings/apikeys/rotate`（实现为硬编码拒绝→40000，无副作用）
- `DELETE /studio/factors/999999`（→51001）、`POST /studio/mining/cancel/{不存在}`（→51001）

### 5.3 跳过（5 个，原因）

| 端点 | 跳过原因 |
|---|---|
| `POST /desk/fills/run` | 高危：真实撮合改持仓/现金；无请求体，无法参数校验型探测 |
| `POST /monitor/run` | 高危：真实监控重算并写快照；无请求体 |
| `POST /settings/data/cache/clear` | 高危：清空 Redis `aqp:*` 与 LRU；无请求体 |
| `POST /settings/data/sync` | 高危：触发真实增量数据同步；无请求体 |
| `POST /settings/db/backup` | 高危：磁盘水位 93%，避免写入大备份文件；无请求体 |

> 对"无请求体"的高危端点无法做参数校验型探测（FastAPI 无 body 参数会忽略请求体），故如实跳过，未伪造覆盖。

---

## 6. 最严重的遗留问题（按优先级）

1. **重计算写端点仍是实测盲区**（中）：架构师静态识别的"无预算重计算端点"里，`desk/fills/run`、`monitor/run`、`ops/dag/rerun`（真实执行）因安全策略无法实测，其真实耗时/是否超预算**仍无实测数据**。本轮只能证明它们"存在且参数校验生效"。
2. **`production.ts:176-177` 注释过时**（低）：描述与后端现状（ms 级）矛盾，误导维护者。建议更新注释（不属功能缺陷）。
3. **51001 文案被前端 sanitize 冲淡**（低）：后端 `999999` 案例返回"部分资产数据获取失败: 999999: **IndexError**"，前端 `client.ts:73` 的 `sanitizeApiMessage` 命中 `*Error` 正则后替换为通用"服务暂不可用，请稍后重试"，用户看不到"无数据/未同步"的真实语义。建议后端 domain 错误文案不带异常类名。
4. **`/datacenter/train/cancel` 语义轻微怪异**（低）：无训练任务时仍返回 `cancel_requested: true, running: false`，无副作用但语义易误读。

---

## 7. 证据文件清单

| 文件 | 内容 |
|---|---|
| `regression-verification.md` | 本报告 |
| `regression_lineage.py` / `.out` | lineage 稳态/SWR/冷算/Redis 键复测脚本与输出 |
| `regression_portfolio.py` / `.out` | 组合回测错误码 5 例复测 |
| `regression_ui.cjs` / `.out` | Chromium 运行时 UI 回归（7/7 PASS） |
| `smoke-write-endpoints.csv` | 33 个写端点补测数据 |
| `probe_write_endpoints.py` / `.out` | 写端点补测脚本与副作用复核输出 |
