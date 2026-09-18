# AQP 全栈审查 · 最终汇总报告

**审查日期**：2026-09-14
**审查范围**：后端 21 个路由模块 / 122 个注册端点、前端 18 条路由 19 个页面、产品文档与实现一致性
**审查方式**：5 路并行（后端架构、前端契约、产品合理性、后端实测、前端实测），全程只读，未修改任何业务源码
**审查产物**：本文件 + 5 份专项报告（同目录）

---

## 一、一句话结论

> **功能覆盖完整（无空壳页、无死链、契约 0 不一致），但「可用」与「可信」都不达标：**
> 存在 **3 类静默错误**（不报错、界面正常、结果却是错的）、**1 个模块功能下线**（ETF 详情）、
> **1 条核心路径首屏 35–40 分钟**，且**全量回归测试跑不完**。

- 前端页面可用率 **17/18（94.4%）**，`tsc` 零错误、构建通过
- 后端端点严格可用率 **66.9%**（宽松口径 95.9%，35 个写端点仅验鉴权未真实执行）
- 前后端契约 **107/112 完全一致，0 条 404、0 条字段类型不匹配**
- 问题总计：**P0 × 10、P1 × 26、P2 × 47**

---

## 二、五路结果一览

| 审查线 | 覆盖 | 结论 | P0/P1/P2 |
|---|---|---|---|
| 后端架构（高见远） | 21 模块 / 113 端点 | 工程底座水位高，但有 3 个静默错误 | 3 / 8 / 16 |
| 前端架构与契约（高见远） | 112 处前端调用 / 18 路由 | 架构合格、契约极干净；3 个 P0 让用户直接碰壁 | 3 / 9 / 12 |
| 产品与文档（许清楚） | 19 页面 / 111 端点 vs 文档 | 无空壳页；但文档与实现 18 处不符、合规强度倒挂 | 7 / — / — |
| 后端实测（严过关） | 122 端点冒烟 + pytest | RBAC 正确、Redis 降级有效；首屏与 ETF 严重超时；pytest 跑不完 | 2 / 6 / 9 |
| 前端实测（严过关） | 18 页 + 14 项交互闭环 | 13/14 交互通过；ETF 详情不可用；6 页首屏 ≥5s | 1 / 3 / 10 |

---

## 三、P0 问题清单（合并去重，按修复优先级排序）

| # | 问题 | 证据 | 影响 | 建议修法 | 预估 |
|---|---|---|---|---|---|
| 1 | **特征双版本污染**：`features` 无版本 rglob 同时吃进 v1 与 v2g | `research.py:87`、`studio.py:32` | 380 日内 **15.96 万条 (date,symbol) 重复行**；研究页 IC / 分位 / 相关性 / 因子重要性 / 压力测试**全部算错且无提示** | 显式传 `version`，读取前断言唯一性，重复即报错 | 小 |
| 2 | **首屏 `/market/overview` 冷缓存阻塞** | 实测 500 项串行外网抓取 **35–40 分钟**（README 称 48s）；主缓存键 `TTL=-2` 长期缺失 | 重启/缓存过期后首页打不开；关掉 Redis 后 45s 无响应 | 并发批量抓取 + 缓存预热 + 修 TTL + 兜底 stale-while-revalidate | 中 |
| 3 | **策略研究页基本不可用** | `Research/index.tsx:87-101`、`compute_guard.py:10-16`、`config.py:100-103` | 首屏 11 个并发重计算 POST（7 个抢 slot），后端 `COMPUTE_CONCURRENCY=2` 且超限**直接抛错不排队**，前端 `Promise.all` 首个 reject 即整页失败 | 改 `allSettled` + 限流 2 并发 + 40103 单独提示 + 按需计算 | 中 |
| 4 | **401 拦截器无条件整页跳登录** | `client.ts:26-32`、`Topbar.tsx:80` | 未登录也调 `/notify/recent` → **唯一公开的首页 1 秒内被踢到登录页**；与登录页「只读页面无需登录」文案及后端公开策略矛盾 | 拦截器只清会话不跳转；Topbar 未登录不建连接 | 小 |
| 5 | **SSE 实时推送从未工作**（两路独立验证） | `useNotifyStore.ts:34`、`notify.py:36` | EventSource 无法带 Authorization → 后端必返 JSON 401 → **100% onerror**，铃铛恒显示「连接中…」，每页 console 报错 | 后端支持一次性 query ticket，或改用可带头部的 SSE polyfill；store 补 `close()` | 中 |
| 6 | **ETF 详情全页不可用 + ETF 四端点超时** | `/etf/detail` 40s（前端 30s+ 判死）、overview 82.8s、performance 80s、scale 59.7s | **1383+ 只 ETF 详情页全部打不开**，等同模块下线 | 定位慢查询（疑全量扫描 Parquet）+ 加缓存与分页 | 中 |
| 7 | **抓取未纳入互斥锁** | `datacenter.py:752` `_run_fetch` | 写 `daily_bar/hfq` 不走 `pipeline_slot`，可与 sync/pipeline/训练**并发写、静默覆盖** | 纳入 `pipeline_slot` 统一互斥 | 小 |
| 8 | **因子分位预警永久失效** | `alerts.py:540` glob 写成 `features/year=*.parquet`（实际 `features/version=*/year=*`） | 恒空返回，**规则永不触发且无留痕**，用户以为在监控 | 修路径 + 加空结果告警 | 小 |
| 9 | **全量 pytest 无法跑完** | 两次全量分别挂在 `test_universe`（13min 无输出）与 `test_multi_source` | **回归基线不可得**，无法验证任何改动是否引入 regression | 加 `pytest-timeout` + `network` marker 隔离外网用例 | 小 |
| 10 | **产品误导与合规倒挂** | `screener.py:127`、`FilterPanel.tsx:14`；`config.py:40-47` | ①「风险等级」= 预测收益映射（score≥0.3 标「低风险」），**预测收益高 ≠ 风险低**；②给出「买什么」的选股榜 / AI 精选榜反而无模型声明，低风险页面倒是有；③`ADMIN_TOKEN` 默认 `aqp-dev-token-change-me` 且 `ALLOW_REGISTRATION` 默认 true | 风险等级改用真实波动率/回撤；补充 C.3/C.5 声明；改默认配置 | 小 |

---

## 四、P1 高频问题（节选，详见专项报告）

- **权限表达错乱（已整改）**：删除未被执行且与路由冲突的 `ROLE_PERMISSIONS`，后端以 `ROLE_RANK + require_role(...)` 为唯一授权口径；predict 明确为 researcher，前端路由守卫已与后端对齐
- **Prometheus 标签基数爆炸**：`main.py:127` 用原始 URL 路径（含 2500 个股票代码）做 label ≈ **28 万时间序列**，且 `/metrics` 免鉴权
- **假功能**：`/settings/apikeys/rotate` 生成的密钥全后端无校验点
- **隐性耦合**：`research.py:335` 硬编码读 `models/exp/` 而非 registry 记录的 `models/prod/`
- **超时重灾区**：`/portfolio/search` 175.7s 且返回空；`/datacenter/overview` 54s；`/watchlist/dashboard` 25s；`/stock/{sym}/panels` 20s
- **降级文案不友好**：今日仅 1 只股票有 Alpha 预测，前端直接把 `AQPException` 类名甩给用户
- **错误码冲突（已整改）**：`ERR_CREDENTIALS` 保持 40104；`PIPELINE_BUSY` 已迁移至独立冲突码 40900
- **设置页假失败**：`savePreferences`(viewer) 与 `saveEngine`(admin) 塞进同一 `Promise.all` → viewer 保存偏好必然整体失败（实际已写库）；`runSync` 的 `setInterval` 无 cleanup
- **数据现状**：覆盖 `covered_with_data` 2499/5552，行情滞后到 09-11 → 选股榜只剩 1 条空壳、推荐榜 unavailable，且接口无 status 降级提示

---

## 五、做得好的部分（值得保留）

- **契约质量罕见地干净**：112 处前端调用 vs 113 后端端点，0 条 404、0 条字段/类型不匹配，snake_case 全局统一
- **类型与构建**：`tsc --noEmit` 0 error，全项目仅 1 处真 `any`；`vite build` 22.5s 通过
- **后端工程底座**：`core` 零反向依赖（有测试守卫）、`domain` 纯函数域层、统一响应信封、Redis 三层降级（熔断 → SWR → 进程内 LRU）、Parquet 原子写、ML 三段切分 + gap 隔离 + 防泄漏守卫
- **前端工程**：18 页全 lazy + 骨架屏、ECharts 生命周期无泄漏
- **RBAC 实现正确**：viewer→40300、无效 token→40102、匿名→40100，边界没有被绕过
- **数据红线**：Screener / DataQuality 等页有「禁造假数据」约束，不编造填充

---

## 六、修复路线建议

**第一批（1 周内，止血）**：#1 特征污染、#4 登录跳转、#7 抓取互斥、#8 预警路径、#10 默认配置与合规声明 —— 都是小改动、高收益。

**第二批（2–3 周，恢复可用性）**：#2 首屏缓存、#3 研究页并发、#5 SSE、#6 ETF、#9 测试基线 —— 需要设计与回归验证。

**第三批（持续）**：P1 权限守卫补全、Prometheus 标签治理、拆 6 个超大前端文件（DataCenter 902 / KLineChart 752 / Screener 738 / StockDetail 700 / EtfDetail 694 / Settings 693 行）、移除 0 引用的 lightweight-charts 依赖。

> ⚠️ **前置条件**：在 #9（测试基线）修好之前，任何改动都无法被回归验证 —— 建议把 #9 提前到第一批。

---

## 七、审计遗留事项

1. 测试账号 `qaprobe_viewer` **已删除**（本次审计创建）；`users` 表仍残留 `qauser_686823`（2026-09-14 15:28 创建，早于本次审计，疑为更早会话遗留，请自行决定是否清理）
2. `frontend/dist/` 被本次构建刷新，`dist-audit/` 为保留副本
3. 后端 8000 端口实例由其他会话持有，未做变更；审计期间启动的 8124/8125 已停止
4. 35 个写端点（训练 / pipeline / truncate / 备份恢复等）**仅验证鉴权与参数校验，未真实执行**，其运行时正确性未覆盖
