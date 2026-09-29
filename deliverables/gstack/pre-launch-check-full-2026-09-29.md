# Alpha Quant Platform · 上线前全检报告

**日期**：2026-09-29
**场景**：上线前全检（代码审查 + 安全审计 + QA测试 + 超时问题根因排查）
**参与成员**：产品评审员 + 安全官 + QA与发布 + 调查员（4 位全上场）
**审计对象**：`D:\Python_Project\Alpha Quant Platform`（133 个后端模块 / 163 个测试文件 / 97 个前端 TS 文件）
**主理人**：沽思航 · 软件工坊 CEO

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟡 **有条件通过（Conditional Go）** —— 无 🔴 P0 阻塞项，但有 **2 项 🟠 P1 必须在上线前处置**。
- **阻塞项数量**：**0 项 P0**；**2 项 P1**（1 项配置类、1 项依赖类）；**6 项 P2**。
- **最亮眼的正向信号**：后端测试套件 **1885 passed / 8 skipped / 0 failed**（447s，exit=0）；生产环境安全校验具备 **fail-fast** 机制；`.env` 未被 git 追踪，JWT_SECRET 为 64 位强密钥。
- **最大风险**：**裸机部署路径**（非 Docker）下 `ENV=dev` 不触发 fail-fast，`API_HOST=0.0.0.0` + `ALLOW_ADMIN_TOKEN_LOGIN=true` + 明文静态 `ADMIN_TOKEN`，会让**同网段任何人无密码取得管理员权限**。⚠️ 危险性在于该暴露**不会打任何告警**（因为 ADMIN_TOKEN 非默认值，绕过了高危告警分支）。
- **下一步**：执行下方「行动清单」的 A1、A2 两项（各约 1 分钟），即可转为 🟢 Go。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| **Go / No-Go** | 🟡 **条件 Go**（清完 2 项 P1 → 🟢 Go） |
| **严重度分布** | 🔴 0 / 🟠 2 / 🟡 6 / 🟢 12+（正向确认项） |
| **关键行动项** | 5 条（A1–A5） |
| **测试基线** | 1885 passed / 8 skipped / 3 deselected / **0 failed** |
| **建议负责人** | 部署执行者（A1、A2）、后端负责人（A3）、前端负责人（A4、A5） |
| **上线建议** | **优先走 docker-compose 路径**（已内置 ENV=prod 安全校验） |

---

## 1. 各成员核心结论

### 🔍 产品评审员（代码审查）
- **核心判断**：整体工程质量**高于同规模教学项目水准**。分层清晰（core/db/cache/domain/data/backtest/ml/api），异常与统一响应封装到位，防泄漏测试与域纯度测试真实存在。倾向 **Go**。
- **关键建议**：① ML 训练/推理链路需复核 asof 基准，确认因子提取无未来函数；② `vite.config.ts.timestamp-*.mjs` 等 **11 个临时构建残留**应清理并加入 `.gitignore`；③ 46 项未提交改动中包含对 `etf.py`（超时问题所在文件）的修改，**上线前需确认其已验证并提交**。
- **代码规模实证**：后端 `app/` 133 个 .py、`tests/` 165 个 .py、前端 `src/` 97 个 TS/TSX。

### 🛡️ 安全官（OWASP Top 10 + STRIDE）
- **核心判断**：安全设计**有真实纵深**——prod 环境配置校验 fail-fast（`config.py:290-313`）、登录有速率限制（`auth.py:59`）、CORS 用配置化白名单而非 `*`（`main.py:215`，`allow_credentials=True` 安全）、`.env` 已 gitignore。
- **关键建议**：**修复一处静默暴露**——裸机部署时 `API_HOST=0.0.0.0` + `ALLOW_ADMIN_TOKEN_LOGIN` 默认 True + 静态 admin token，构成局域网内无密码接管管理接口的路径，且**不触发告警**。一句话修复：`.env` 加 `ALLOW_ADMIN_TOKEN_LOGIN=false`（或 `API_HOST=127.0.0.1`）。
- **裁决**（经团队内二轮质证）：🟠 **P1，非 P0** —— 因为 docker-compose 路径（`ENV=prod`）已堵住，风险只在裸机部署形态下成立。

### ✅ QA与发布（测试与发布检查）
- **核心判断**：**Go**。后端全量测试 **1885 passed / 0 failed / 8 skipped / 3 deselected**，耗时 447s，exit=0（远优于历史基线 1612 passed）。跳过项为已标记的 network 类（离线约定），符合预期。
- **关键建议**：① 前端需补跑 `npx tsc --noEmit` + `npm run build` 并留档；② 发布走 docker-compose，注意 `REDIS_PASSWORD` 为 compose 必填校验项（缺失会直接报错而非静默起无密码实例）；③ 数据目录 `data/`（近 4 万个 parquet）与 SQLite 需确认卷挂载与首启初始化顺序。

### 🔧 调查员（超时问题根因）
- **核心判断**：超时**主要是设计内的优雅降级，非缺陷**；但存在一处**真实的外部源不稳定问题**。全局超时中间件（`core/timeout_guard.py`）设计严谨——默认 240s、豁免 SSE 流、按路径延长（回测寻优 660s、DAG 重跑 330s），且有"预算必须 > 前端超时"的不变量注释。
- **关键建议**：① 两类超时需区分——`请求路径超时（4.5s 预算）` 是 ETF 端点的**业务级降级**（返回结构化降级载荷、短 TTL、不自锁主缓存），属预期行为；`DataSourceUnavailable(ReadTimeout)` 是**外部数据源（AKShare）不稳定**导致，最新日志出现 **32 次**（/219 条请求行 ≈ 14.6%），较前一日（88 次/更早日志）已有改善但仍需关注；② `net_inflow 置为 null 而非 0` 是**正确的语义处理**（避免把"未知"伪装成"零"）；③ 建议为外部源加"降级率"可观测指标，并复核 ETF 端点的 4.5s 预算来源与重试次数是否匹配。

---

## 2. 综合审查发现（去重合并，按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🟠 P1 | 安全/配置 | `.env`（未设 `ALLOW_ADMIN_TOKEN_LOGIN`）+ `config.py:87,92` | 裸机部署时 `ENV=dev` 不 fail-fast，`API_HOST=0.0.0.0` 全网卡监听，`ALLOW_ADMIN_TOKEN_LOGIN` 默认 True，配合明文静态 `ADMIN_TOKEN` → 同网段可无密码取得 admin；因 token 非默认值而**不打告警**，属静默暴露 | `.env` 增加 `ALLOW_ADMIN_TOKEN_LOGIN=false` 与 `API_HOST=127.0.0.1`（或改 `ENV=prod`） | 安全官 |
| 2 | 🟠 P1 | 依赖 | `backend/requirements.txt` / `frontend/package.json` | 未提供依赖漏洞扫描结果（无 `pip-audit` / `npm audit` 留档），上线前无法排除已知 CVE | 上线前跑一次 `pip-audit` + `npm audit --omit=dev`，留档结果 | 安全官 + QA |
| 3 | 🟡 P2 | 工程/DX | 仓库根目录 | `vite.config.ts.timestamp-*.mjs` × 11 个临时残留文件污染工作区 | 删除并加入 `.gitignore`（`vite.config.ts.timestamp-*`） | 产品评审员 |
| 4 | 🟡 P2 | 工程 | `frontend/dist-audit/`、`dist-verify-p23/` | 多个构建产物快照目录并存，易误发布旧版本 | 保留单一 `dist/`，其余清理，构建流程固定产物路径 | 产品评审员 |
| 5 | 🟡 P2 | 发布风险 | git 工作区 | **46 项未提交改动**，含 `etf.py`（超时问题文件）、`app_settings.py`、`kpi_series.py` 及多项新测试 | 上线前统一评审并提交，打 tag 冻结发版点 | 产品评审员 + QA |
| 6 | 🟡 P2 | 可观测性 | `backend-run-0929.log` | 外部数据源失败缺乏聚合指标，全靠翻日志（32 次/219 行） | 增加 `datasource_failure_rate` / `degrade_total` Prometheus 指标 | 调查员 |
| 7 | 🟡 P2 | 配置 | `docker-compose.yml` | `REDIS_PASSWORD=123456` 弱密码；虽有必填校验但值本身弱 | 改用强随机串（compose 已有 `${REDIS_PASSWORD:?...}` 强制显式提供） | 安全官 |
| 8 | 🟡 P2 | 前端 | 未执行 | 本次未跑通前端 `tsc --noEmit` / `npm run build` 的实时验证 | 补跑并留档（历史 `tsc_check.log` 存在，需确认时效性） | QA |
| 9 | 🟢 正向 | 安全 | `config.py:290-313` | prod 环境 fail-fast 校验完备（ADMIN_TOKEN 长度、JWT_SECRET、CORS 禁开发地址） | 保持 | 安全官 |
| 10 | 🟢 正向 | 安全 | `.env` / git | `.env` 被 gitignore，`git ls-files` 0 命中；`JWT_SECRET` 64 位；token 未入库 | 保持 | 安全官 |
| 11 | 🟢 正向 | 质量 | 后端测试 | 1885 passed / 0 failed，较历史 1612 提升 | 保持 | QA |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| **A1** | 在 `.env` 增加 `ALLOW_ADMIN_TOKEN_LOGIN=false` 和 `API_HOST=127.0.0.1`（或统一按 `ENV=prod` 部署），消除裸机部署的管理端静默暴露 | 部署执行者 | **P1（上线前必做）** | 上线前 |
| **A2** | 运行 `pip-audit`（后端）与 `npm audit --omit=dev`（前端），修掉高危 CVE 或留档豁免说明 | 安全官 + QA | **P1（上线前必做）** | 上线前 |
| **A3** | 复核并提交 46 项未提交改动（重点 `etf.py`、`app_settings.py`、`kpi_series.py` + 新测试），打发版 tag 冻结 | 后端负责人 | P2 | 上线前 |
| **A4** | 清理 `vite.config.ts.timestamp-*.mjs`（11 个）与 `dist-audit/`、`dist-verify-p23/`，补 `.gitignore` 规则 | 前端负责人 | P2 | 上线前 |
| **A5** | 补跑前端 `tsc --noEmit` + `npm run build` 并留档；为外部数据源失败率加 Prometheus 指标；复核 ETF 4.5s 预算与重试配置 | 前端 + 后端 | P2 | 上线后一周内 |

---

## ⚠️ 待完善 / 已知局限

- **前端验证未实时跑通**：QA 成员报告后端测试已实测通过，但前端 `tsc`/`build` 的本次实时执行结果未取得；已有历史 `tsc_check.log` 但时效性未确认（A5）。
- **超时频率对比基准不严格**：`backend-run-0929.log`（32 次外部源失败 / 219 请求行）与 `backend-run-0928e.log`（88 次）的请求总量未完全对齐，比率对比仅供参考。
- **本次为静态审查 + 测试执行**，未做真实压力测试 / 渗透测试 / 并发压测；`data/` 下近 4 万 parquet 的加载耗时未实测。
- **RBAC 争议已裁定**：产品评审员与安全官对"写操作 RBAC 保护范围"曾有分歧（🟡 vs 🔴P0），经二轮证据质证后**统一为 🟠 P1 配置问题**（见发现 #1），非代码缺陷。
- 未覆盖：备份/回滚预案演练、`data/` 首启初始化时序、多进程并发下的 SQLite WAL 争用实测。

---

## 📚 成员产出索引

- **产品评审员**（代码审查）：分层架构复核、加载/异常封装核查、前端残留文件与工作区清理项、ML 防泄漏复核建议
- **安全官**（OWASP + STRIDE）：写操作端点盘查、`.env` 真实值核查、prod fail-fast 机制确认、部署形态差异裁定（最终 🟠P1）
- **QA与发布**：后端全量测试实测（1885 passed / 0 failed / 447s / exit=0）、docker-compose 发布检查清单、Redis 必填校验确认
- **调查员**（超时根因）：`timeout_guard.py` 预算体系解读（默认 240s / 回测 660s / DAG 330s）、两类超时归因（业务降级 vs 外部源 ReadTimeout）、日志频率统计（32/219）、降级语义正确性确认

---

> 本报告由软件工坊 AI 协作生成（4 位专家独立调查 + 主理人汇编 + 争议质证），关键决策请由工程负责人复核。
