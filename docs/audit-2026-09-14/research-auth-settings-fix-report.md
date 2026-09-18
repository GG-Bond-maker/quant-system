# 策略研究、路由权限与设置页修复报告

日期：2026-09-14  
范围：仅前端（`frontend/`），未变更后端 API。

## 修复内容

### 1. 研究页首屏计算资源争抢（P0）

- 将 `Research` 页原先一次 `Promise.all` 触发的 11 个请求重构为：
  - 轻量请求（总览、实验列表、年度稳定性、特征重要性）先行独立加载；
  - 因子、CV、组合优化、执行/压力测试拆分为 4 个可独立失败的区块；
  - 页面级 `ComputeQueue` 将昂贵重算请求限制在最多 2 个并发，符合后端 `COMPUTE_CONCURRENCY=2`；
  - 首屏与交互触发的因子重算、CV、优化和冲击模拟共用同一计算队列。
- 对业务码 `40103` 映射为局部提示“计算资源繁忙，可稍后重试”。失败不会覆盖整页，每个区块标题均提供“重试”。
- 每一个初始与交互计算请求均使用 `AbortController`；组件卸载时取消进行中和排队请求，所有异步状态更新均由 mounted 标记保护。
- `api/client.ts` 新增可选 `RequestOptions.signal`，`researchApi` 各端点透传取消信号，不改变现有调用行为。

### 2. 路由认证与角色表达（P1）

- `/` 与兼容别名 `/market` 保持公开。
- viewer 及以上：`/report`、`/stock/:symbol`、`/screener`、`/etf`、`/etf/:code`、`/portfolio`、`/data`、`/watchlist`、`/settings`。
- researcher 及以上：`/backtest`、`/research`、`/alerts`、`/studio`、`/dataquality`、`/desk`、`/pipeline`、`/capacity`。
- 匿名访问受保护页通过 `RequireRole` 跳到带 URL 编码 `next` 参数的登录页；登录但角色不足时保持会话，展示明确权限不足说明，不调用 store.clear。
- 调整权限提示文案，避免错误地提示已登录低权限用户“使用研究员账号登录”，明确会话仍保持登录。

### 3. Settings 假失败与同步轮询（P1）

- `saveAll` 改为先保存所有用户都具备权限的 preferences，并立即同步 refresh frequency store；仅管理员才渲染与调用引擎配置保存接口。
- viewer 保存个人偏好时不再请求管理员 `/settings/engine`，故不会出现偏好已落库但 UI 显示整体失败的情况。
- `runSync` 使用 `syncPollTimer` ref 追踪轮询；卸载时统一清理 toast 和轮询定时器；任务完成、取消或失败时主动停止轮询，短暂网络错误则保持轮询以避免错误判定任务结束。

## 关键交互状态机

### 研究计算

`idle → queued (最多2个active) → loading → success | section-error → retry → queued`

- `unmount` 从任意状态进入 `aborted`，不执行后续状态更新。
- `40103` 进入 `section-error` 且文案为“计算资源繁忙，可稍后重试”。

### 同步轮询

`idle → starting → polling → running | succeeded | cancelled | failed`

- `succeeded/cancelled/failed/unmount` 均执行 `clearInterval`；
- 单次轮询网络错误保持 `polling`，不将未知状态误报成成功或失败。

## 验证证据

执行目录：`D:/Python_Project/Alpha Quant Platform/frontend`

| 检查 | 结果 |
| --- | --- |
| `npx tsc --noEmit` | 首次在项目根执行时检测到错误目录/本地依赖解析问题；改在前端目录用项目脚本验证。 |
| `npm run type-check` | 通过（TypeScript 无错误）。 |
| `npm run build` | 通过（Vite 5.4.6，746 modules transformed）。 |
| 临时 dev 服务 | 已以端口 5192 启动后关闭，未占用 5173。 |
| 浏览器自动化 | 未完成：环境中不存在 `agent-browser` 可执行文件（Exit 127）；已关闭临时服务。 |

## 可复现页面级验证步骤

1. 以匿名状态打开 `/market`：应直接展示市场页；打开 `/research`：应跳转 `/login?next=%2Fresearch`。
2. 以 viewer 登录后访问 `/research`、`/backtest` 或 `/alerts`：显示“权限不足”，再检查 localStorage 中 `aqp-auth` 仍存在；访问 `/screener`、`/data`、`/settings`：允许进入。
3. 以 researcher 登录 `/research`：网络面板中昂贵 `research/*` 请求同时在飞不超过 2 个；令某个请求返回 `40103`，仅对应象限展示“计算资源繁忙，可稍后重试”和重试按钮，其他象限继续展示数据。
4. 在研究页重算期间立即路由离开：浏览器网络面板应看到请求取消，控制台不应出现 React unmounted state update 警告。
5. 以 viewer 打开 `/settings` 修改主题或刷新频率并保存：仅调用 `/settings/preferences`，显示成功 toast，且刷新频率即时生效；不应调用 `/settings/engine`。
6. 以 admin 打开 `/settings`：可见“量化引擎设置”，保存时 preferences 成功后再独立保存 engine。点击同步后离开页面：轮询定时器被清理。

## 残余风险

- 自动化浏览器工具在当前环境缺失，需在具备 Chromium 自动化能力的 QA 环境完成上述交互断言及截图。
- 后端资源槽无排队机制；当前前端限流防止单页过量抢占，但多个浏览器会话仍可能收到 `40103`，已提供局部可见错误与重试路径。
