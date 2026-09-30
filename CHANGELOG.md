# Changelog

Alpha Quant Platform（AQP）的重要变更记录。

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 结构，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> ⚠️ **历史完整性声明（务必阅读）**
>
> 本仓 `.git` 对象库曾**三次被外部进程清空**（最近一次：2026-09-29 15:39）。
> 因此本文件的早期版本段由**两类不同可信度的来源**构成，已分别标注：
>
> | 标记 | 含义 | 可信度 |
> |------|------|--------|
> | ✅ **对象完整** | 提交对象可 `checkout` / `diff`，历史可完整回溯 | 高 |
> | ⚠️ **仅存提交信息** | 提交**对象已永久丢失**，仅从 `.git/logs/HEAD` reflog 抢救出提交信息 | 中（内容无法复核） |
>
> 抢救出的原始记录见 `deliverables/gstack/recovered-history-2026-09-30.md`。
> **请勿**对本仓执行 `git reflog expire --expire=now --all` 或 `git gc --prune=now`，
> 否则 2026-09-23 ~ 09-29 这段历史将彻底不可再生。

---

## [Unreleased] — 2026-09-30 · 上线前全检修复

上线前全检（代码审查 / 安全审计 / QA / 超时 / 性能）后的修复落地轮。
共 **16 个文件**（1 新建 + 15 修改），全部 7 项 P0 阻塞已解除并通过实测验收。
详见 `deliverables/gstack/remediation-applied-2026-09-30.md`。

### Security

- **依赖 CVE 升级**（A5）：`pyarrow 17.0.0 → 25.0.1`（CVE-2026-25087，修于 23.0.1）、
  `lightgbm 4.5.0 → 4.7.0`（CVE-2024-43598，修于 4.6.0），新增传递依赖 `narwhals==2.26.0`。
  复核后 `pip-audit` 中 lightgbm / pyarrow 已完全消失。
- **校验错误不再回显输入**：`errors.py` 新增 `_SAFE_KEYS = ("type", "loc", "msg", "ctx")` 白名单，
  剥离可能含敏感值的 `input` 字段。
- **日志不再落盘诊断变量**：`logging.py` 两个文件 sink 显式 `diagnose=False` + `compression="zip"`，
  避免异常栈把局部变量（含 token / 连接串）写进磁盘。
- **`/overview` 补鉴权**：加 `require_role("viewer")`；`/overview/rt`、`/overview/daily` 按设计保持匿名。

### Fixed

- **线程池泄漏导致全站级联挂死**（B1/B7）：`asyncio.wait_for` 超时后 `to_thread` 的 worker 不可取消，
  默认池（`min(32, cpu+4)=22`）被占满后全站饿死。
  - 新建 `backend/app/core/compute_pool.py`：专用计算池（6 槽，可重建懒加载），
    `market.py`×3 + `etf.py`×2 改走该池 —— 实测池占满 6/6 时默认池短任务仍仅 1.2ms。
  - `socket.setdefaulttimeout(10)`（新增配置 `SOCKET_DEFAULT_TIMEOUT_SECONDS`）——
    这是**唯一被实测证伪后留下的有效方案**：进程退出挂起 rc 124 → **0**。
    （daemon 化与 `pool.shutdown(wait=False)` 实测**均无效**，详见报告 §4。）
- **Parquet 扫描串行**（B3）：`parquet_store.py` 的 `_scan_files_entry` 与 `read_parquet_columns`
  改为并行（`_SCAN_MAX_WORKERS = 8`）。
- **nginx 超时链过短**（B3）：`proxy_read_timeout` 180s → **700s**，
  并补 SSE 专用 location、`/metrics` 与 `/health*` 可达、`/healthz` 改真实反代。

### Changed

- **修复从未运行的 CI 门禁**（结构性缺陷）：`.github/workflows/ci.yml` 调用 mypy / ruff，
  但 `requirements.txt` 中**并无这两个包** ⇒ `command not found` ⇒ 门禁从未生效。
  新建 `backend/requirements-dev.txt`（`-r requirements.txt` + `mypy==2.3.1` + `ruff==0.16.6`），CI 改装它。
- `backend/.env` / `.env.example`：`DEBUG=true → false`，加 `LOGURU_DIAGNOSE=0`、`LOG_LEVEL=INFO`、
  `SOCKET_DEFAULT_TIMEOUT_SECONDS=10`。
- `docs/项目开发文档.md`：同步依赖版本，消除文档漂移。
- 清理 5 处 `F401` 未使用导入（`kpi_series.py`、`upgrade_security_deps.py`、2 个测试文件）。
- `.gitignore`：补登 `.cleanup_backup_*/`、`backup/`（避免二进制归档污染历史）。

### Added

- `backend/requirements-dev.txt` —— 开发/门禁依赖独立声明。
- `backend/app/core/compute_pool.py` —— 专用计算池。

### Verification

- 全量回归：**1906 passed / 8 skipped / 0 failed**（419.18s），**零回归**
  （总收集 1914 = 离线 1911 + `network` 3）。
- 静态门禁：`ruff --select F,E9` **All checks passed**；`mypy app/ --ignore-missing-imports`
  **134 source files, no issues**。
- 功能冒烟：pyarrow 读 300 文件 / 392,882 行；**8 个真实 LightGBM 模型**（6 prod + 2 exp）加载 + predict；
  polars 与 pyarrow 行数一致（1031 = 1031）；`app` 导入 **112** 个 OpenAPI path。

---

## [0.2.0] — 2026-09-23 ~ 2026-09-29 · ⚠️ 仅存提交信息

> 本段 23 个提交的**对象已永久丢失**（第三次 `.git` 清空），无法 `checkout` / `diff`。
> 以下内容摘自 reflog 抢救记录，**未经二次复核**。
> 本段内**包含两次从工作区重建 master 的根提交**（09-23、09-27），即第二、三次清空事故。

### Added

- **ETF 中心**：`/hot` 支持 `sort=amount|pct`，排序与详情页完善。
- **数据源多源降级**：接入腾讯 / 新浪 / BaoStock，**彻底移除 Tushare**（`feat(datasource)`）。
- **全局请求超时中间件**：纯 ASGI 实现，豁免 SSE / 导出 / 长任务。
- **组合取数复权口径** `price_basis` 披露，删除硬编码「数据来源：AKShare」。
- KPI 卡片改真实序列（`feat(kpi/ui)`）。

### Fixed

- **市场模块**：预算与熔断参数定稿；修 `_should_retry` 成功路径真 bug；补两处缺失守卫。
- **领域层**：统一代码 → 交易所前缀映射，修**深市 ETF 落库成 `.SH`**。
- **数据中心**：footer 统计 + 单飞（singleflight）+ 长 TTL，并收口长 TTL 的陈旧窗口；
  ETF 抓取多源降级与口径门控；`failed_count` 改本轮口径并补全失败上报。
- **前端**：
  - P2-3 幂等 GET 网络层受限重试（超时 / abort **不**重试，SWR opt-out）。
  - P2-4 长任务页面接入 `AbortController`（卸载时中断在途请求）。
- **前后端可用性审计 P1/P2**：lineage 缓存、错误码语义、死端点下线；超时预算、UI 补全、错误可见性。
- **上线前修复**：数据中心页超时根因修复（`fix(pre-launch)`）。

### Docs

- 前后端可用性审计报告 + P2-3 / P2-4 改造与验证。
- 第十四章未做项清单（全局超时中间件 / RBAC / 降级 status）。
- 第十五章：全局超时中间件 / 降级 status / **git 事故记录** / 全量回归 1818 passed。
- 第十六章：RBAC 开启影响面评估（只读调研）。
- 纠正 RBAC 误判 —— 系 2026-09-23 用户**主动放开**，非缺陷。
- 上线前全检 / P1 修复 / 数据中心超时根因 三份报告（`docs(gstack)`）。

### ⚠️ 事故

- **2026-09-23 22:25** 与 **2026-09-27 22:18**：两次因 `.git` 对象库被外部进程清空，
  从完整工作区重建 `master`（产生两个新的根提交，历史 lineage 中断）。
  两次事故后**均未 push 到远端** ⇒ 无异地副本 ⇒ 每次只能全量重建。

---

## [0.1.0] — 2026-09-14 ~ 2026-09-20 · ✅ 对象完整

> 本段 33 个提交对象**完整可回溯**（2026-09-30 经 `f7c9410` 从远端取回）。
> 这是被清空前**原始历史的完整副本**，前两次重建时曾被放弃。

### Added

- **自助注册**：后端 `/auth/register` + 前端登录页注册 Tab。
- **选股中心全市场股票列表**：新增全市场股票列表端点，修复 `universe` 路径导致的过滤失效；
  前端新增全市场列表，ETF 列表支持三列排序。
- **panic 收口（A–D 四段）**：
  - A：根因守卫 —— 脏列（缺列 / `dtype=Null`）走既有降级分支，封掉 polars 单列 `sort` 的裸 500。
  - B：全局兜底 ASGI 中间件 —— 把不可捕获的 `BaseException` 转成契约内 `50001`。
  - C：`step_screener_dump` 就地守卫 —— raw 分区直排早于 `filter_universe`，A 段覆盖不到。
  - D：后台长驻循环 `BaseException` 韧性兜底 —— 封掉「panic 静默停摆」。
- **单实例前提护栅** `warn_if_multi_worker`：从 `argv` 识别 workers，
  Dockerfile 断言改为**可证伪**；兼容 gunicorn `-w`，补齐 `python -m` / 连写短选项。

### Fixed

- **量化核心**：修复同步完整性 / 选股信号 / 调度隔离等 **7 项缺陷与 2 项回归**。
- **选股**：`_watchlist_quotes` 守卫按**列可用性**判定，封掉 `dtype=Null` 的裸 500；
  参考集为空时降级 `signal_strength` 为 `None`，堵住「全 weak」静默复现。
- **流水线**：入口统一规范化 `codes`，修复晚间例行因 symbol 后缀导致的**每晚失败**。
- **overview**：后台重建不得继承请求预算；超时可观测；warmer 提前续期。
- **数据中心**：数据集日期列可配置 + 逐文件隔离，修复 `announcements` 整行消失；
  overview 不再把 `RUNNING` 当绿灯，`PENDING` 单独分支。
- **韧性兜底**：`quotes_hub._quotes_loop`、`monitor._retrain_worker` 补 `BaseException` 兜底 +
  `finally` 终态落库。
- **僵尸态回收**：启动时回收陈旧重训状态（消除进程退出导致的 7200s 静默阻断）；
  启动时回收遗留 `running` 后台任务（消除 `/sync/tasks` 僵尸态）。
- **时区**：`data_jobs` 首插补齐 `updated_at`，消除与 `created_at` 的 **8 小时时区差**。
- **告警**：
  - pipeline 健康检查把陈旧 `RUNNING` 计入失败告警；
  - 非终态行绕过 24h 查询窗口，陈旧 `RUNNING` 不再有**告警死区**；
  - 修掉补捞去重的少计与提示被截断，`PENDING` 不再误报为故障；
  - 补捞改**最旧优先**并如实标注截断，饱和时不再谎报条数；
  - 主查询截断也如实标注，补上 `recent_truncated`。
- **数据库**：状态写入 `sqlite3.connect` 超时对齐到 30s。
- **启动器识别**：取消 `-m` 的魔法窗口，改为取首个 `-m` 的模块位；
  启动器校验只看启动器位置；重复 `--workers` 改 **last-wins**；
  补住 `args[:4]` 魔法窗口的**假绿**；订正 docstring 里与实现不符的边界示例并把边界钉成用例。

### Docs

- 自助注册功能说明（端点 / 校验 / 开关 / 错误码）。

---

## 版本基线说明

| 版本 | 时间跨度 | 对象完整性 | 说明 |
|------|---------|-----------|------|
| `Unreleased` | 2026-09-30 | ✅ | 上线前全检修复轮，**尚未发布** |
| `0.2.0` | 2026-09-23 ~ 09-29 | ⚠️ 仅存信息 | 含第二、三次 `.git` 清空事故 |
| `0.1.0` | 2026-09-14 ~ 09-20 | ✅ | 原始历史完整副本 |

> 版本号 `0.1.0` / `0.2.0` 为**事后补定**（此前从未打过 tag）。
> 若需正式发布基线，建议在 `Unreleased` 内容验收后打 `v0.3.0`。

<!-- 维护提示：
     1. 新变更请追加到 [Unreleased] 段，发布时再切分为正式版本段。
     2. 若某次提交的对象丢失，请在该版本段标注 ⚠️ 并在 recovered-history 文件中留存提交信息。
     3. 发布 tag 前请确认 `git push` 已成功 —— 这是打破「清空→重建」循环的唯一动作。 -->
