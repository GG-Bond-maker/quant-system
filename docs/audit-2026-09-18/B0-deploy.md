# B0 · 部署与运行链路审核报告（2026-09-18）

审核员：B0 批次（部署与运行链路：容器化/原生双路径、镜像构建、环境变量契约、健康检查、反向代理与运维脚本）
方法：逐文件通读 + `rg` 全仓交叉核对 + **可在宿主机执行的等价验证**（路径推导、`docker-compose config` 解析产物、环境变量解析、shell/Dockerfile 指令语义核查）
纪律：全程只读，未改任何源码/配置/数据；探针落 `$env:TEMP`。**未执行** `docker build` / `docker compose up`（本沙箱无 daemon），**未**跑全量 pytest，**未**启 dev server。

## 0. 范围映射与证据等级

| 任务书列出的路径 | 仓库实际 | 结论 |
|---|---|---|
| `backend/Dockerfile` | 存在 | 已读 |
| `frontend/Dockerfile` | 存在 | 已读 |
| `docker-compose.yml` | 存在 | 已读 + `config` 解析 |
| `deploy/`（nginx/systemd/supervisor/prometheus） | **整个目录不存在** | 见 DEP-19 / DEP-20 |
| `backend/run.md` | **不存在**；实际在仓库根 `run.md` | 已读 |
| `backend/.env.example` | **不存在**；实际在仓库根 `.env.example` | 已读 |
| `backend/scripts/{bootstrap,create_admin,backup,restore,purge_legacy_models,run_offline_tests.*}` | 全部存在 | 已读 |
| `.github/workflows/{ci,nightly}.yml` | 存在 | 已读（只记部署差异） |
| `backend/pytest.ini`、`frontend/vite.config.ts`、`frontend/nginx.conf` | 全部存在 | 已读 |
| `docs/部署*.md` | **不存在**；等价物是 `run.md` + `README.md:160-213` + `docs/项目开发文档.md` §13 | §9 单列 |

**本报告的证据等级**（纪律要求逐条标注）：
- **[工具输出]**：在宿主机真实跑出的输出（`docker-compose config --format json`、pydantic 字段解析、`git ls-files`、`Get-ChildItem` 体积统计）。
- **[静态确证]**：源码/配置逐行可判定的语义（含 nginx/loguru/Dockerfile 的公开语义），推理链完整。
- **[无法验证]**：必须有 docker daemon 或真实 Linux 宿主才能确认的部分 → 每条附「在有 docker 的机器上这样验证」命令。

**本沙箱已就绪的验证工具**（与任务书预期不同，值得记录）：

```
Docker Compose version v2.39.2-desktop.1     # docker / docker-compose CLI 均存在
docker present / docker-compose present      # 仅 daemon 不可用
docker-compose -f docker-compose.yml config --no-interpolate -q   → exit 0
docker-compose --env-file <empty> -f docker-compose.yml config    → exit 1（REDIS_PASSWORD 必填校验生效）
```

即：**compose 语法与变量插值链路已在本机真实验证**，只有「构建镜像 / 起容器」不可验证。

---

## 1. 两条部署路径的启动链路图（文字版）

### 1.1 容器路径（`docker-compose.yml`，任务书称「生产部署」）

```
docker compose up -d --build
│
├─[redis] redis:7-alpine
│    command: --requirepass ${REDIS_PASSWORD:?}     compose:18-21
│    ├─ 根 .env 缺失 → 插值期即报错退出（已实测）           ✅ 设计正确
│    └─ 根 .env 存在 → 正常起，healthcheck = redis-cli ping  compose:31-35 ✅
│
├─[aqp-api] build: backend/Dockerfile （context = 仓库根）
│    │  镜像构建期：
│    │    builder: python:3.11-slim + build-essential/wget/libgomp1      Dockerfile:8-10
│    │             pip install -r backend/requirements.txt               Dockerfile:20-22
│    │             ※ 未编译 TA-Lib（见 DEP-11，注释与指令不符）
│    │    runtime: COPY --from=builder /opt/venv /opt/venv               Dockerfile:39
│    │             apt: curl libgomp1 tzdata                             Dockerfile:33-37
│    │             COPY backend/{app,scripts,pytest.ini,tests}           Dockerfile:43-46
│    │             mkdir /app/{data/{parquet,models,sqlite},logs}        Dockerfile:48
│    │             useradd uid=10001 aqp + chown -R aqp:aqp /app         Dockerfile:49-50
│    │             USER aqp                                              Dockerfile:52
│    │
│    │  运行期（read_only: true + tmpfs /tmp + aqp(10001)） compose:43-45
│    │    CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1  Dockerfile:59
│    │    │
│    │    ├─【import app.main】→ app.api.v1.router → … → app.core.compute_guard
│    │    │     compute_guard.py:11   _slots = BoundedSemaphore(get_settings()...)
│    │    │       └─ get_settings()  config.py:229-237
│    │    │            Settings()  ← env (compose 只注入 5 个键) + env_file=" /.env "（不存在）
│    │    │            validate_runtime_safety()  ENV 默认 "dev" ⇒ 仅打 WARNING，不 raise
│    │    │            s.LOG_DIR.mkdir(parents=True)    LOG_DIR = /backend/logs   ❌ 见 F1
│    │    │            s.DATA_ROOT.mkdir(...)           = /data/parquet          ❌ 见 F2
│    │    │            s.MODEL_ROOT.mkdir(...)          = /data/models           ❌
│    │    │            s.SQLITE_PATH.parent.mkdir(...)  = /data/sqlite           ❌
│    │    │       ⇒ OSError(EROFS/PermissionError) 冒泡出模块导入
│    │    │       ⇒ uvicorn 启动失败、进程退出（restart: unless-stopped → 无限重启）
│    │    │
│    │    └─【lifespan】即使上面侥幸通过，仍有三个独立阻塞点
│    │         main.py:41 setup_logging(s) → logging.py:53 logger.add(LOG_DIR/"app.log")
│    │                      loguru 无 delay=True ⇒ 立即 open ⇒ ❌ 见 F3
│    │         main.py:43 init_database() → init_db.py:36 mkdir + :41 PRAGMA journal_mode=WAL
│    │                      ❌ 见 F4
│    │         main.py:84 warn_if_multi_worker / 91-93 evening_routine + catchup
│    │                      ❌ 见 F10（读只读卷的写任务）
│    │
│    └─ healthcheck（compose 覆盖镜像内 HEALTHCHECK）
│         compose:62  curl -fsS http://127.0.0.1:8000/health
│         Dockerfile:56-57  curl -fsS http://127.0.0.1:8000/health/ready
│         ├─ curl 存在（Dockerfile:34 装了）✅ 工具链没问题
│         ├─ 但两个端点都**恒返回 HTTP 200**（errors.py:57-59 + main.py:296）
│         └─ ⇒ 该 healthcheck 只能探测「进程是否 listen」，探测不到 ready 语义（见 DEP-07）
│
└─[aqp-web] build: frontend/Dockerfile
     ├─ depends_on: aqp-api: service_healthy    compose:76-78
     └─ aqp-api 永不 healthy ⇒ **aqp-web 永不被创建**
          ⇒ curl http://127.0.0.1:8080 → connection refused
          ⇒ 整个 stack 不可用（不是「降级」，是「全灭」）
```

**容器内「代码期望路径」 vs「compose 实际挂载」对照**（这是 F1/F2 的核心）：

| 语义 | 代码在容器内解析出（`[工具输出]`，宿主机用 `PurePosixPath` 等价推导） | compose 实际挂载 | 结果 |
|---|---|---|---|
| 项目根 `PROJECT_ROOT` | `/`（`config.py:24`，`/app/app/core/config.py` 的 `parents[3]`） | — | 所有默认路径直接落在 **根文件系统** |
| `LOG_DIR` | `/backend/logs` | `./backend/logs → /app/logs` | **不匹配**，挂载点废弃 |
| `DATA_ROOT` | `/data/parquet` | `./data → /app/data` | **不匹配**，挂载点废弃 |
| `MODEL_ROOT` | `/data/models` | （无） | 无挂载 |
| `SQLITE_PATH` | `/data/sqlite/aqp.db` | （无） | 无挂载 |
| 数据库备份目录 | `/backend/backups`（`app_settings.py:317`） | （无） | 无挂载 |
| 数据隔离区 | `/data/quarantine`（`repair.py:30`、`ops.py:205`） | （无） | 无挂载 |
| `env_file` | `/.env`（`config.py:31`） | compose 无 `env_file:`（`[工具输出]`） | 配置无法注入 |
| 可写目录 | 仅 `/tmp`（tmpfs）+ 上述两个**用不到的**挂载点 | — | 见 F1–F4 |

> **一句话**：`read_only: true` 把一个「路径本来就是错的」部署，从「静默把数据写进容器层」升级成「import 期直接崩」。两个挂载卷挂在了应用**从不引用**的路径上，正确性是 0，不是 50%。

### 1.2 原生路径（`run.md`）

```
run.md 步骤链（Windows 宿主机，全部为相对/绝对路径均正确）
│
├─ ② backend: python -m venv .venv → pip install -r requirements.txt        run.md:36-51
│     .env: Copy-Item ..\.env.example ..\.env                              run.md:63
│     └─ ✅ 原生路径下 PROJECT_ROOT = 仓库根，config.py:31 的 env_file 命中，.env 真的生效
├─ ③ frontend: npm install                                                 run.md:76
├─ ④ backend: python scripts/bootstrap.py  → 建目录 + SQLite + WAL         run.md:93
│              python -m app.data.ingest                                    run.md:96   ✅ __main__.py 存在
├─ ④'（可选）redis: docker start aqp-redis / docker-compose up -d redis     run.md:124-145
│     └─ ⚠️ 手工 `docker run` 与 compose 容器同名 aqp-redis（run.md:148 已自我提示）
├─ ⑤ 终端1: .venv\Scripts\python.exe -m uvicorn app.main:app \
│            --host 127.0.0.1 --port 8000                                   run.md:166
│   终端2: npm run dev  (:5173，vite proxy /api → 127.0.0.1:8000)           run.md:176
│     └─ ✅ vite.config.ts:17-26 代理存在；前端同源相对路径也可用（client.ts:80 baseURL ?? '/'）
├─ ⑥ 建管理员: scripts\create_admin.py admin your_password                 run.md:198
│     └─ ✅ 可用（但密码走 argv，见 N3）
└─ 验证: GET /health 看到 "code":0                                          run.md:184  ✅ 成立
```

**原生路径总体判定：按 run.md 步骤『能起来』**（未实测启动，但所需入口 `scripts/bootstrap.py`、`app.data.ingest.__main__`、`app.db.init_db.__main__` 均存在，端口/命令自洽）。缺陷集中在**运维化**（无进程守护、备份脚本危险、TA-Lib 文档幻觉），不在「起不来」。

### 1.3 三条链路的路径语义差异（为什么 CI 抓不到 F1/F2）

| 环境 | `PROJECT_ROOT` | `DATA_ROOT` | `SQLITE_URL` | 是否覆盖默认 |
|---|---|---|---|---|
| 原生 dev | 仓库根 | `<root>/data/parquet` | `<root>/data/sqlite/aqp.db` | 否 |
| CI (`ci.yml:31-33`) | `backend/`（cwd） | `./data/test_parquet`（相对！） | `sqlite+aiosqlite:///./data/sqlite/aqp_ci.db` | **是，两个都覆盖** |
| 容器 | **`/`** | `/data/parquet` | `sqlite+aiosqlite:////data/sqlite/aqp.db` | 否 |

⇒ CI 用**相对路径 + 显式覆盖**，恰好绕过 `PROJECT_ROOT` 推导出的所有绝对路径；容器用**未覆盖的默认值**。这是 CI 全绿而容器必崩的结构性原因（DEP-16）。

---

## 2. 必失败点清单（按「是否阻断启动」排序）

> 判定口径：**必失败** = 无需外部条件、在当前 compose/文档配置下 100% 触发。

### A. 容器路径 —— 阻断启动（P0）

| # | 触发点（文件:行） | 机制 | 置信度 |
|---|---|---|---|
| **F1** | `config.py:234` + `compute_guard.py:11` | `LOG_DIR=/backend/logs` → `mkdir` 在 `read_only:true` 根fs 上 → `EROFS`（非 root 还会先撞 `EACCES`）。**发生在 import 期**（`compute_guard` 模块级调用 `get_settings()`）⇒ uvicorn 启动阶段即失败，无 lifespan、无日志、无 healthcheck 响应。 | 确定（[工具输出]路径推导 + 静态确证 read_only） |
| **F2** | `config.py:235-237` | `DATA_ROOT=/data/parquet`、`MODEL_ROOT=/data/models`、`SQLITE_PATH.parent=/data/sqlite` 三个 `mkdir` 同上失败。**即使删掉 read_only 也会失败**（非 root uid 10001 无权在 `/` 建目录）。且 compose 的两个卷挂在 `/app/data`、`/app/logs`，**不覆盖这三个路径**。 | 确定 |
| **F3** | `logging.py:52-61,64-73` + `main.py:41` | 三个 sink 中两个是文件 sink；loguru `logger.add(path)` **未加 `delay=True`** ⇒ 立即 open ⇒ `LOG_DIR` 不存在/不可写 → 抛错。`main.py:41` 无 try/except ⇒ lifespan 异常 ⇒ 启动中止。**与 F1 相互独立**：即使把 get_settings 的 mkdir 改成容错，这里仍会炸。 | 确定 |
| **F4** | `init_db.py:36,41` + `main.py:43` | SQLite 目录不可建 + `PRAGMA journal_mode=WAL` 需要在 DB 目录写入 `-wal`/`-shm` ⇒ 失败。`/data` 未挂载。 | 确定 |
| **F5** | `docker-compose.yml:76-78` | `aqp-web depends_on aqp-api: service_healthy`，而 aqp-api 永不 healthy ⇒ **aqp-web 容器不会被创建**，8080 无监听。故障从「后端崩」放大成「全栈不可达」。 | 确定（[工具输出]：`aqp-web.healthcheck=False`，用的是镜像内 HEALTHCHECK；依赖条件已解析确认） |

### B. 容器路径 —— 不阻断启动但必然错误（P1）

| # | 触发点 | 机制 | 置信度 |
|---|---|---|---|
| **F6** | `docker-compose.yml:49-55`（**[工具输出]** 已解析） | `aqp-api.environment` 解析结果**只有 5 个键**：`REDIS_ENABLED/REDIS_HOST/REDIS_PASSWORD/REDIS_PORT/TZ`。**没有** `ENV`、`ADMIN_TOKEN`、`JWT_SECRET`、`ALLOW_ADMIN_TOKEN_LOGIN`、`ALLOW_REGISTRATION`、`DATA_ROOT`、`LOG_DIR`、`SQLITE_URL`。且 `env_file:` 键**不存在**（已解析确认）。 | 确定 |
| **F7** | `config.py:210-226` + F6 | `ENV` 默认 `"dev"` ⇒ `validate_runtime_safety` 走 `elif ENV=="dev"` 分支，**只打 WARNING、不 raise**。⇒ 所谓「生产部署」实际以 `ADMIN_TOKEN=aqp-dev-token-change-me`（公开已知默认值）、`ALLOW_ADMIN_TOKEN_LOGIN=true`、`ALLOW_REGISTRATION=true`、`JWT_SECRET=None`（由 ADMIN_TOKEN 派生）运行。**唯一的安全闸门完全失效。** | 确定 |
| **F8** | `compose:56-58` | 两个卷挂在 `/app/data`、`/app/logs`；代码从不引用这两个路径（已 `rg` 全仓：`APP_HOME` 变量**零读取方**）。⇒ 宿主机 `./data`（2.1 GB parquet）对容器**完全不可见**，即便启动成功也是空数据集。 | 确定 |
| **F9** | `Dockerfile:49-50` vs `compose:56-58` | 构建期 `chown -R aqp:aqp /app` 建立的属主关系，被 bind mount **遮蔽**：运行时用宿主机 `./data` 的属主。⇒ 修复 F1/F2（改成 `DATA_ROOT=/app/data/parquet`）后仍会失败，除非宿主机目录 owner=**10001**。**「改个环境变量就好」是错的**。 | 确定（Docker bind mount 语义） |
| **F10** | `config.py:159-166` + `main.py:90-93` | `EVENING_ROUTINE_ENABLED=True`、`AUTO_RETRAIN_ON_DRIFT=True`、`WARM_OVERVIEW_ON_STARTUP=True` 均为默认，容器里无法通过 `.env` 关闭（无 env_file）⇒ 每天 17:30 的例行任务 + 漂移重训会往只读/未挂载路径写 features/models。启动时 `startup_catchup()`（`main.py:93`）**立刻**触发一次。 | 静态确证（默认值 + 调度存在） |
| **F11** | `app_settings.py:317-318` | `POST /settings/db/backup`（admin）目标 `PROJECT_ROOT/backend/backups` = `/backend/backups` ⇒ `mkdir` 在只读根fs 失败。`_do()` 内无 local try ⇒ 冒泡到 `errors.py:160-174` 的全局 handler ⇒ **HTTP 200 + `code=50000`「系统暂不可用」**。运维看到的是「成功状态码 + 无信息错误」，**不是 5xx**。 | 确定 |
| **F12** | `repair.py:30,67`、`ops.py:205` | `/data/quarantine` 写入路径同样未挂载 ⇒ 数据修复/隔离功能在容器里必然失败。 | 静态确证 |
| **F13** | `Dockerfile:59` | `CMD` 缺 `--no-access-log`（主报告 F2 已记），**且** `frontend/nginx.conf:24-34` 的 `location /api/` **也没有** `access_log off`。⇒ SSE ticket 出现在 query string（`notify.py:83-91`，`/api/v1/notify/stream?ticket=...`）会**同时**落到 **uvicorn access log** 和 **nginx access log** 两处，两者都进容器 stdout。而应用自身的错误日志**是**做了 `_sanitize_query`（`errors.py:163`）的——**加固漏在了 access log 这一层**。 | 确定（两侧配置均已读） |

### C. 原生路径（P1/P2）

| # | 触发点 | 机制 | 置信度 |
|---|---|---|---|
| **N1** | `backup.py:57-73` + `README.md:208,213` | **`scripts/backup.py` 的 `main()` 不是纯备份**：`test_db.unlink()`（:67）先**删除生产 `data/sqlite/aqp.db`**，再 `restore_backup` 解压还原，且**无任何确认**。而 README:213 把它作为**每晚 cron 推荐命令**：`10 2 * * * cd /path/to/AQP/backend && python scripts/backup.py`。⇒ 已配 cron 的机器每晚都在删生产库。若 API 进程存活：unlink 后进程仍持有已删除 inode 的 fd，其后写入落入「幽灵文件」，还原的副本反而更旧（**静默丢写**）；WAL 模式下 tar 内含 `-wal`（`backup.py:26-29` 整体 add 目录）解压覆盖在既有 `-wal` 上，可能产出 `database disk image is malformed`。**仓库里已存在安全的演练脚本 `backup_drill.py:55-59`（用 `tempfile.TemporaryDirectory()`，不碰生产）**——功能重复且 `backup.py` 越界。 | 确定（代码逐行）；破坏性后果的严重度依赖运行态 |
| **N2** | `restore.py:14-31` | 无确认提示；`sys.argv.index("--file")` 若 `--file` 为末位则 `IndexError`、未给 `--file` 则 `ValueError`（裸 traceback）。仅在 `aqp.db` 存在时才自动预备份（:25）。用 `tar.extractall`（`backup.py:53`）直接覆盖 `data/`，无 `filter=`。 | 确定 |
| **N3** | `create_admin.py:39-41,53-54` + `run.md:198`、`README.md:175` | 密码经 `sys.argv[2]` 传入 ⇒ 进入 shell history 与进程列表；文档**教的正是这个写法**。且已存在用户时返回「已存在（跳过）」⇒ **无法用该脚本轮换/重置管理员密码**（无 `--reset` 参数）。 | 确定 |
| **N4** | `purge_legacy_models.py:71` | `DELETE FROM model_registry` —— **无 WHERE**，清空全表，包含 `is_production=1` 的行。脚本自己在 :50-54 把 `n_prod` 查出来并打印，**却从不据此设防**。docstring 断言「全部 is_production=0 无效」，属对当下数据的假设而非代码约束。默认干跑（`--apply` 才执行，:56）是唯一护栏，但不妨碍 `--apply` 一键抹掉生产模型注册表（`prod/` 目录被 `:43` 排除不被移动，注册表指针却没了）。 | 确定 |
| **N5** | 全仓 | **无任何进程守护**：无 systemd unit、无 supervisor、无 Windows 服务、无 `deploy/` 目录。（`run.md:160`「没有一键脚本，每一步都看得见」是**如实陈述**，不是幻觉——但意味着原生路径没有开机自启/崩溃重启。） | 确定 |
| **N6** | `run.md:55,231-235` | TA-Lib 排障段落（下载 whl、或「从 requirements.txt 删掉 `TA-Lib==0.4.28` 这一行」）——`backend/requirements.txt` **根本没有 TA-Lib**（60 行全文已读）。指向一个不存在的依赖，属陈旧文档（连带解释了 `Dockerfile:2,9` 的 `build-essential/wget` 与「TA-Lib 编译」注释为何悬空，见 DEP-11）。 | 确定 |
| **N7** | `run.md:13,15,110-116,243-249` | 「Redis 可选/没有也能跑」——**与代码一致**（`REDIS_ENABLED` 降级为内存 LRU，`/health` 显式报 `degraded`，`main.py:216`）。但 `compose:19` 的 `${REDIS_PASSWORD:?}` 使**容器路径下 Redis 变成强依赖**：不配 `.env` 连 compose 插值都过不了。两种模式对「Redis 可选性」的承诺不一致（文档未区分）。 | 确定 |
| **N8** | `run_offline_tests.sh:6-10` | 写死 `$BACKEND_ROOT/.venv/Scripts/python.exe`（Windows 布局），非可执行时回落 `python3`（:8-10）⇒ Linux 上**能跑**但依赖裸 `python3` 有全量依赖（而非项目 venv），易环境漂移。`run_offline_tests.ps1:4-8` 严格要求 `backend/.venv` 否则 throw（信息清晰，可接受）。`-q`（脚本）与 `pytest.ini:8` 的 `-v --tb=short`（addopts）叠加 → `-v` 生效，`-q` 形同虚设（纯观感问题）。 | 确定 |

---

## 3. `Settings` × `.env.example` × 运行期读取方 三向对账表

**方法**：`Settings.model_fields` 用项目 venv 实测导出（49 字段，`[工具输出]`），逐字段比对根 `.env.example`（73 行全文已读），再以 `rg` 确认真实读取方。`.env.example` 位置为**仓库根**（不是 `backend/`）。

### 3.1 主表（49 字段全覆盖）

图例：✅ = `.env.example` 有且生效；🟡 = 有但**不生效**（写了没用）；🔶 = 仅在注释里（键不在文件中）；❌ = `.env.example` **完全缺失**。

| # | `Settings` 字段 | 默认值 | `.env.example` | 真实读取方（单一事实源） | 备注 |
|---|---|---|---|---|---|
| 1 | `APP_NAME` | `AQP` | ✅ :6 | `main.py:213` | |
| 2 | `ENV` | **`dev`** | ✅ :7 | `config.py:210,224`、`main.py:214` | 🔴 容器里**没人设它**（F6/F7） |
| 3 | `DEBUG` | **`True`** | ✅ :8 | `logging.py:47-48` | 🔴 `diagnose=True` ⇒ 异常堆栈**带局部变量值**（含密钥）入日志 |
| 4 | `ADMIN_TOKEN` | **`aqp-dev-token-change-me`** | ✅ :11 | `config.py:203,242`、`auth.py` | 🔴 公开默认值；容器内生效 |
| 5 | `ALLOW_ADMIN_TOKEN_LOGIN` | **`True`** | ✅ :13 | `config.py:205` | 🔴 容器内生效 |
| 6 | `JWT_SECRET` | `None` | ✅ :15 | `auth.py:147`（`os.environ` 优先，回落 settings）✅ 生效 | |
| 7 | `JWT_EXPIRE_SECONDS` | `604800` | ✅ :16 | **`auth.py:154` 只读 `os.environ`** | 🟡 **写进 `.env` 完全无效**（B1/A-06 已记，部署视角：容器里也无从设置） |
| 8 | `ALLOW_REGISTRATION` | **`True`** | ✅ :18 | `config.py:207`、`api/v1/auth.py` | 🔴 容器内生效 |
| 9 | `REGISTER_DEFAULT_ROLE` | `viewer` | ✅ :20 | `api/v1/auth.py` | |
| 10 | `API_HOST` | `0.0.0.0` | ✅ :23 | **无读取方**（仅 `config.py:246` 告警字符串引用） | ⚪ 死配置（B1 已记）；run.md 用 CLI `--host` 规避 |
| 11 | `API_PORT` | `8000` | ✅ :24 | **无读取方** | ⚪ 死配置（B1 已记） |
| 12 | `CORS_ORIGINS` | `localhost:5173,127.0.0.1:5173` | ✅ :26 | `config.py:177-179` → `main.py:153` ✅ | 🟡 生产语义危险（含 dev 地址；prod 下会被 `:218-221` 拦下——但容器是 dev 所以不拦） |
| 13 | `METRICS_REQUIRE_AUTH` | `None` | 🔶 :29（注释） | `config.py:182-186` → `main.py:247` ✅ | 未设 ⇒ `ENV==prod` 才要求认证；容器 dev ⇒ **`/metrics` 免认证** |
| 14 | `SQLITE_URL` | `<root>/data/sqlite/aqp.db` | 🔶 :33（注释，且是 **Windows 绝对路径**） | `init_db.py:33-35`、`session.py` | 注释样例对 Linux 部署无指导价值 |
| 15 | `REDIS_ENABLED` | `True` | ✅ :36 | `main.py:275`、`redis_client` | |
| 16–20 | `REDIS_HOST/PORT/DB/PASSWORD/TIMEOUT` | `127.0.0.1/6379/0/None/1.0` | ✅ :37-43 | `cache/redis_client.py` | compose 覆盖 host/port/password（F6） |
| 21 | `LOG_LEVEL` | `INFO` | ✅ :46 | `logging.py:44,55,67` | |
| 22 | `LOG_DIR` | `<root>/backend/logs` | ❌ | `logging.py:52,64`、`config.py:234` | 🔴 **F1/F3 的根因；运维无从覆盖** |
| 23 | `DATA_ROOT` | `<root>/data/parquet` | ❌ | `config.py:235`、`main.py:270` | 🔴 **F2；运维无从覆盖** |
| 24 | `MODEL_ROOT` | `<root>/data/models` | ❌ | `config.py:236`、`ml/registry.py` | 🔴 同上 |
| 25 | `AKSHARE_RATE_LIMIT` | `1.2` | ✅ :49 | `data/` 抓取层 | |
| 26 | `AKSHARE_RETRY` | `3` | ✅ :50 | 同上 | |
| 27 | `WARM_OVERVIEW_ON_STARTUP` | `True` | ✅ :53 | `main.py` warmer | conftest 自行设 0（`conftest.py:179`）⇒ 文档的「测试必须设 0」是冗余说明，非缺陷 |
| 28 | `COMPUTE_ACQUIRE_TIMEOUT_SECONDS` | `10.0` | ✅ :62 | `compute_guard.py:26` | |
| 29 | `COMPUTE_CONCURRENCY` | `2` (ge=1,le=2) | ❌ | `compute_guard.py:11` | 资源上限不可配 |
| 30 | `FEATURE_VERSION` | `""`（空=按 mtime 隐式选） | 🔶 :67（注释） | `data/parquet_store.py` | 空值会静默换口径（`:79-86` 自述），生产建议固定 |
| 31 | `ML_LABEL_HORIZON` | `5` | ✅ :70 | `ml/` | |
| 32 | `ML_HOLDOUT_DAYS` | `252` | ✅ :71 | `ml/` | |
| 33 | `ML_TEST_DAYS` | `252` | ❌ | `ml/` | |
| 34 | `USD_CNY_RATE` | `7.2` | ❌ | `api/v1/etf.py` | 人工汇率口径不可配 |
| 35 | `QUOTES_TTL` | `15` | ❌ | `api/v1/market.py` | |
| 36 | `AQP_CPU_THREADS` | `None` | ❌（`README.md:85` 提了一句） | `__init__.py:29`、`config.py:260` | 线程统一开关未进模板 |
| 37 | `TUSHARE_TOKEN` | `None` | ❌ | `data/` 数据源 | |
| 38 | `NOTIFY_ENABLED` | `False` | ❌ | `scripts/alerter.py` | |
| 39 | `NOTIFY_WEBHOOK_URL` | `None` | ❌ | `scripts/alerter.py` | |
| 40 | `NOTIFY_EMAIL_TO` | `None` | ❌ | **无读取方** | ⚪ 死配置（B1 已记） |
| 41 | `EVENING_ROUTINE_ENABLED` | **`True`** | ❌ | `main.py:90-93` | 🔴 只读容器里必失败（F10） |
| 42 | `EVENING_ROUTINE_TIME` | `17:30` | ❌ | `jobs/evening_routine.py` | |
| 43 | `AUTO_RETRAIN_ON_DRIFT` | **`True`** | ❌ | `ml/monitor.py` | 🔴 同上 |
| 44 | `RETRAIN_MIN_INTERVAL_HOURS` | `12` | ❌ | `ml/monitor.py` | |
| 45–49 | `LLM_PROVIDER/BASE_URL/API_KEY/MODEL/TIMEOUT_SECONDS` | `none`/… | ❌ | `core/llm.py` | 公网部署若开 LLM，无模板可循 |

**统计**：✅ 生效 25 个 ／ 🟡 写了无效 1 个（`JWT_EXPIRE_SECONDS`）／ 🔶 仅注释 3 个（`METRICS_REQUIRE_AUTH`、`SQLITE_URL`、`FEATURE_VERSION`）／ ❌ **完全缺失 21 个**（其中 4 个是**容器路径类** `LOG_DIR/DATA_ROOT/MODEL_ROOT` + 生产安全类 `EVENING_ROUTINE_ENABLED/AUTO_RETRAIN_ON_DRIFT`）。

### 3.2 `.env.example` 有、但 `Settings` 没有（文档幻觉 / 静默丢弃）

| `.env.example` 键 | 行 | 真相 | 影响 |
|---|---|---|---|
| `TZ=Asia/Shanghai` | :73 | **不是 `Settings` 字段**（49 字段中无） | 放在「全局环境变量模板」里冒充应用配置；实际只对容器/OS 有效（compose 另有 `TZ`，`compose:55`）。`extra="ignore"`（`config.py:33`）静默吃掉 |
| `AQP_PANEL_BLOCK_TIMEOUT`（注释形态） | :59 | 非 `Settings` 字段；真源是 `stock.py:69` 的 `os.getenv(...)`，**且模块导入时求值** | 🟡 写进 `.env` **不生效**；必须 `export` 真环境变量。注释还宣称「默认 20→4.5」，属行为变更无正式文档（B1 已记） |

### 3.3 名字不一致 / 前缀类（同一个坑的两个来源）

| 文档里写的 | 代码实际接受 | 出处 | 后果 |
|---|---|---|---|
| `AQP_ALLOW_REGISTRATION` | `ALLOW_REGISTRATION` | `config.py:137` 字段描述、`api/v1/auth.py:4` docstring、**`docs/auth-register.md:38`** | `Settings` **无 `env_prefix`**（`config.py:30-34`），`AQP_` 前缀被 `extra="ignore"` 静默丢弃。**dev 下完全静默**；prod 下 `validate_runtime_safety` 会因 `ALLOW_REGISTRATION=True` 报错，但**错误信息不会提示「你变量名写错了」** ⇒ 运维卡在「我明明设了 false」（B1 已记 P3，此处补充：**这是唯一一处会把用户引向该坑的 user-facing 文档**） |
| `AQP_REGISTER_DEFAULT_ROLE` | `REGISTER_DEFAULT_ROLE` | `api/v1/auth.py:5`、**`docs/auth-register.md:39`**、`docs/audit-2026-09-18/B9c-frontend-ops.md:338`（同为错误建议） | 同上，静默降级为 `viewer` |
| `FEATURE_INCREMENTAL` | 未纳入 `Settings` | `orchestrator.py:110` `os.environ.get` | 只认真实环境变量；`.env`/`.env.example` 均无效 |
| `APP_HOME`（Dockerfile ENV） | 无读取方 | `Dockerfile:31` | 镜像里设了但代码不读 ⇒ 是 F8「挂载点废弃」的旁证 |

### 3.4 生产语义下危险的默认值（C-11）

| 字段 | 默认 | 危险点 | 容器内是否生效 |
|---|---|---|---|
| `DEBUG` | `True` | `logging.py:47-48` 的 `backtrace/diagnose=True` ⇒ loguru 输出**带局部变量值**的堆栈，可能含 `ADMIN_TOKEN`/`REDIS_PASSWORD`/`JWT_SECRET`（`config.py:242-248` 的告警本身就往日志里写安全上下文） | **生效**（compose 未设） |
| `ENV` | `dev` | 唯一的安全闸门 `validate_runtime_safety` 的 `raise` 分支（`config.py:210-223`）永不进入；`METRICS_REQUIRE_AUTH` 也因 `config.py:186` 判定为「不需认证」 | **生效** |
| `ADMIN_TOKEN` | 公开默认串 | `config.py:242` 仅告警；JWT 由它派生（`auth.py:147` 回落）⇒ **任何人可伪造 admin token** | **生效** |
| `ALLOW_ADMIN_TOKEN_LOGIN` / `ALLOW_REGISTRATION` | `True`/`True` | 开放 Bearer 直登 + 开放自助注册 | **生效** |
| `CORS_ORIGINS` | 含 `localhost:5173` | prod 下会被 `:218-221` 拦下；dev 下不拦 | 生效但不构成漏洞（nginx 同源，无需 CORS） |
| `AUTO_RETRAIN_ON_DRIFT` | `True` | 漂移即自动重训 ⇒ 往 `MODEL_ROOT` 写；只读容器必失败 | **生效** |
| `EVENING_ROUTINE_ENABLED` | `True` | 17:30 例行流水线 + 启动 `startup_catchup()` 立即补跑（`main.py:93`） | **生效** |
| `WARM_OVERVIEW_ON_STARTUP` | `True` | 无 Redis 时首建需外部聚合（`.env.example:51` 自述 48s+）；容器首启无 parquet ⇒ 长耗时外部抓取 | **生效** |

---

## 4. 镜像与容器逐项结论（对应任务 A1–A6）

| 项 | 结论 | 依据 |
|---|---|---|
| **A1-a** 前端构建是否需传 `VITE_API_BASE` | **不需要，且当前没传也不影响可用性。**`client.ts:80` `baseURL: import.meta.env.VITE_API_BASE ?? '/'` ⇒ 构建期未注入时回落到**同源相对路径 `/`**；`notify.ts:31` 的 SSE URL 同为相对 ⇒ 浏览器请求 `/api/v1/...` ⇒ 命中 nginx `location /api/`。**任务书提示的 `window.__DSH_BOOT__` 确属另一项目（DSH）机制，AQP 全仓无此符号**（已 `rg` 确认无关）。 | 静态确证 |
| **A1-b** 是否缺注入钩子 | ⚠️ 真缺口是「**没有 ARG/ENV 钩子**」：若将来要指向异源 API，`frontend/Dockerfile` 无 `ARG VITE_API_BASE` 传参通道；`vite.config.ts:19` 读的 `process.env.VITE_API_BASE` **只服务 dev-server 代理**，构建产物无关。 | 静态确证 |
| **A1-c** `npm run build` 产物可用性 | **可用**：`package.json:8` = `tsc -b && vite build`；Dockerfile 已 COPY `tsconfig.json/tsconfig.node.json/vite.config.ts/index.html/src`，`src/vite-env.d.ts`（`/// <reference types="vite/client" />`）随 `src` 一并进入 ⇒ `import.meta.env` 类型可得，`tsc -b` 不缺引用。 | 静态确证（CI `ci.yml:55` 亦跑同命令且已绿） |
| **A1-d** `frontend/public/` 未 COPY | ⚠️ 低：`public/ref-watchlist.png`（479 KB）未进镜像 ⇒ 构建产物缺该静态文件。但 **SPA 源码零引用**（全仓仅 `docs/audit-2026-09-14/frontend-qa-report.md:141` 提到）⇒ **无用户可见影响**。`vite` 的 `publicDir` 不存在不报错。 | 静态确证；不夸大 |
| **A1-e** `/api/` 反代 | ✅ `nginx.conf:24-25` `proxy_pass http://aqp-api:8000/api/;`（尾斜杠映射正确）+ `proxy_http_version 1.1`（:26）。 | 确定 |
| **A1-f** ⭐ SSE 是否被 Nginx 缓冲 | **结论：不会被缓冲——任务书假设的 P0「通知中心永远收不到事件」不成立。** 机制：nginx.conf:33 显式 `proxy_buffering on`，**但**应用在 SSE 响应上设了 `X-Accel-Buffering: no`（`notify.py:34`，应用于 `:100` 与 `:153`）；nginx 默认处理该响应头（`proxy_ignore_headers` 在本配置中**未设置**，故不屏蔽）⇒ 该响应缓冲被关闭。辅证：`gzip_types`（:14）不含 `text/event-stream` ⇒ 不被 gzip 包裹；心跳 15s（`notify.py:36`）< `proxy_read_timeout 180s`（:31）⇒ 不会被读超时切断。**但这是「靠一个响应头兜住」的脆弱等价**：无 SSE 专用 location、`proxy_buffer_size/proxy_buffers` 未设、容器路径无任何端到端测试覆盖（CI 从不 build 镜像）。置信度：机制「高」，端到端「中」。 | 静态确证 + nginx 语义 |
| **A2** `read_only` + `tmpfs /tmp` + 非 root 的写路径 | 见 §2 F1–F4/F11/F12。**可写**：`/tmp`（tmpfs）⇒ `tempfile`、polars spill、LightGBM 临时文件、joblib 均可用。**不可写且被代码需要**：`/backend/logs`、`/data/parquet`、`/data/models`、`/data/sqlite`、`/backend/backups`、`/data/quarantine`、`/data/models_legacy`。**挂载但代码不用**：`/app/data`、`/app/logs`。**关于 matplotlib/polars 临时目录**：`rg` 确认 **`app/` 不 import matplotlib**（仅 `scripts/icir_monitor.py:15` 用到）⇒ MPLCONFIGDIR 问题**不适用于 API 进程**；HOME(`/home/aqp`) 虽在只读根fs 上不可写，但 API 无写入方。 | 确定 |
| **A3** 挂载卷宿主机属主 vs uid 10001 | **必然冲突，且这是「修好路径也起不来」的第二个坑**（F9）。Linux 上 bind mount 以宿主机 owner 呈现；构建期 `chown -R aqp:aqp /app`（`Dockerfile:50`）被遮蔽。**`docs/项目开发文档.md:5944` 的官方步骤 `sudo chown -R $USER:$USER /opt/aqp` 恰好把它 chown 给了宿主用户（如 uid 1000）** ⇒ 即使路径改对，uid 10001 仍不可写。 | 确定（Docker 语义 + 文档反例） |
| **A4** HEALTHCHECK 语义与 `start_period` | ① **compose 覆盖了镜像内检查**：`Dockerfile:56-57` 用 `/health/ready`，`compose:62` 用 `/health` ⇒ 前者在 compose 模式下**是死配置**。② 两者语义**都不具备门控能力**：`/health`（`main.py:205-217`）恒 200（Redis 不通也是 200）；`/health/ready`（`main.py:254-296`）用 `ok()` 包装 ⇒ **即使 `checks["sqlite"]=="failed"` 也返回 HTTP 200**（`errors.py:57-59`；`fail()` 也刻意用 200）。③ `curl -fsS` 因此只在「连接被拒」时失败 ⇒ 实际退化为**liveness**。④ `curl` **已安装**（`Dockerfile:34`、`frontend/Dockerfile:23`）✅。⑤ `start_period`：镜像 20s / compose 30s；正常原生启动要连 Redis + `init_database`（WAL+建表）+ `refresh_calendar_cache` + `restore_sync_state` + 两次 stale 回收，**30s 偏紧但不致命**（有 3 次 retry × 30s interval）。 | 确定 |
| **A5** 多阶段构建依赖完整性 | ① `COPY --from=builder /opt/venv`（:39）**包含**在 builder 编译的 C 扩展（`hiredis`）✅。② runtime 装 `libgomp1`（:34）⇒ LightGBM/sklearn 的 OpenMP 依赖满足 ✅。③ `requirements.txt` **无任何只存在于 builder 的系统依赖**——特别是**不含 TA-Lib**（60 行全文已读）⇒ builder 的 `build-essential wget`（:9）**纯属死重量**，与 `Dockerfile:2` 的「TA-Lib 编译」注释、`run.md:55/231` 的排障段落三者互相印证的**陈旧残留**（DEP-11）。④ `tzdata` 已装（:34）✅。 | 确定 |
| **A6** `CMD` 加固缺口 | 缺 `--no-access-log`（F13，主报告 F2 已记）。**此外还缺**：`--proxy-headers` 与 `--forwarded-allow-ips`——nginx 已设 `X-Forwarded-For/X-Forwarded-Proto`（`nginx.conf:29-30`）但 uvicorn 未启用 `--proxy-headers` ⇒ 应用侧 `request.client.host` 拿到的是 **nginx 容器 IP**，审计/限流用的真实客户端 IP 丢失（注意：`docs/项目开发文档.md:5724` 的旧版 CMD **反而有** `--proxy-headers` + `--loop uvloop`）。`--limit-concurrency` 未设 ⇒ 无背压上限（配合 `COMPUTE_CONCURRENCY=2` 与 `asyncio.to_thread` 线程池，慢计算堆积时内存无界）。 | 确定 |

---

## 5. 原生部署逐项结论（对应任务 B7–B9）

| 项 | 结论 |
|---|---|
| **B7** 按文档能否起来 / 端口 / `ADMIN_TOKEN` / `ENV=prod` | **能起来**（入口脚本齐全，见 §1.2）。**文档从未要求 `ENV=prod`**：`run.md:66` 只说「公网/生产环境还必须关闭 `ALLOW_ADMIN_TOKEN_LOGIN` 与 `ALLOW_REGISTRATION`，并设置独立强 `JWT_SECRET`」——**漏了最关键的 `ENV=prod`**，而没有 `ENV=prod`，`config.py:210-223` 的 fail-fast 永不触发，运维「自认为配好了」也没人校验。`ADMIN_TOKEN` 生成方式：文档只让「设置为自己的随机长字符串」（`run.md:66`），**无生成命令**。**文档幻觉核查**：`run.md`/`README.md` 未声称任何 systemd unit / supervisor 存在（**没有**此类幻觉，N5 是「缺失」而非「虚假声称」）；`run.md:241` 称 compose 是「生产部署用的整体容器化方案」，而该方案**必崩**（§2）⇒ 这算虚假声称。 |
| **B8** 脚本参数/幂等/错误处理 | `bootstrap.py`：`check_env` 先 mkdir 并对 `OSError` 显式退出（:27-32）✅ 幂等。`create_admin.py`：`_ensure_roles` 幂等、重复用户跳过（:39-41）✅；**不能重置密码**（N3）。`backup.py`：**`main()` 破坏性**（N1，最高优先）。`restore.py`：**无确认**、argv 解析可抛裸异常、`extractall` 无 `filter=`（N2）。`purge_legacy_models.py`：**`DELETE FROM model_registry` 无 WHERE**（N4）。 |
| **B9** `run_offline_tests.*` 干净环境 | 依赖 `backend/.venv`（未创建时：`.ps1` throw 明确报错 ✅；`.sh` 回落 `python3` ⚠️）。**未漏 admin bootstrap**——从 `ci.yml:26-28`、`nightly.yml:29-31` 看，CI 会先跑 `create_admin.py citest citest123`，而离线脚本本身不含该步骤；若测试需要已存在用户而本地未建，则依赖 `conftest.py` 自建 fixture（未见缺陷）。与 `pytest.ini` addopts 的 `-v` 覆盖脚本 `-q`（观感）。 | 
| **B9-b** 与 pytest.ini 冲突 | 无功能性冲突。`pytest.ini:8` 的 `addopts = -v --tb=short -p faulthandler` + `faulthandler_timeout=600`（:9）与 `-m "not network"`（脚本）正交。 |

---

## 6. 可运维性缺口（对应任务 D12–D14）

### D12 日志
- 策略（`logging.py`）：控制台 stdout（:42-49）＋ `app.log` 10 MB 轮转/保留 30 天（:53-61）＋ `app.json.log` 20 MB/WARNING+/30 天（:64-73），全部 `enqueue=True`。
- **容器内唯一真正可用的出口是 stdout**（文件 sink 因 F1/F3 不可用）。三个 sink 都 `enqueue=True` ⇒ loguru 起后台线程 + 队列；`tmpfs /tmp` 足够，无阻塞风险。
- ⚠️ **compose 未配置任何日志驱动限制**（`[工具输出]`：redis/aqp-api/aqp-web 三服务 `logging=False`）⇒ 默认 `json-file` **无 `max-size`/`max-file`** ⇒ 容器日志无限增长。叠加 `LOG_LEVEL=INFO`（`config.py:95`）与 nginx access log 全部进 stdout（F13），是**磁盘耗尽型运维缺口**。
- **`LOG_DIR` 写不进去时是否阻塞启动**：**会，且是硬阻塞**——`main.py:41` `setup_logging(s)` 无 try/except，loguru `logger.add(文件路径)` 未设 `delay=True` ⇒ 立即 open 抛错 ⇒ lifespan 异常 ⇒ 启动失败。**没有任何降级到「仅 stdout」的路径**。这是 F3，也是「日志不可用导致整个服务不可用」的设计脆弱点（即便把目录权限修好，磁盘满时同样表现）。

### D13 备份
- 端点：`POST /api/v1/settings/db/backup`（admin，`app_settings.py:307-330`）。
- **只读容器内必然失败**：目标 `PROJECT_ROOT/backend/backups` = `/backend/backups`（未挂载 + 只读根fs），`mkdir(parents=True, exist_ok=True)` 在 `:318`。
- **错误码可见性**：`_do()` 内**无 try/except** ⇒ `OSError` 冒泡到 `errors.py:160-174` 的 `@app.exception_handler(Exception)` ⇒ **`HTTP 200` + `{"code":50000,"message":"系统暂不可用，请稍后重试"}`**。⇒ 编排/监控无法用状态码告警（一切正常返回 200），前端只能靠 `code` 字段；且**消息不提示「目录不可写」**，运维拿不到根因（`trace_id` 可 grep，但需能读日志——而日志正好也写不进去）。
- 与脚本路径的对比：端点用 `sqlite3` 的 `src.backup(dst)`（`:321-324`）**是安全的在线备份**；而 CLI `scripts/backup.py` 的 `main()` **是破坏性的**（N1）。⇒ 同仓库两套备份语义不一致，README 推荐了危险的那套。
- 保留策略：`backend/backups/` 现有 35.3 MB（主报告 §7.1 记 117 文件）无清理逻辑；`scripts/backup.py:21` 输出目录 `data/backup/` 同样无保留策略。

### D14 观测
- `/metrics` 存在（`main.py:241-251`），鉴权由 `settings.metrics_require_auth`（`config.py:182-186`）：显式设值优先，否则 `ENV=="prod"` 才要求 Bearer。
- **容器内 `ENV=dev` ⇒ `/metrics` 免认证**（F6/F7）。缓解因素：`compose:59-60` 只 `expose: 8000`（**未 publish 到宿主**），nginx 也**不代理** `/metrics`（只有 `/api/` 与 `/healthz`）⇒ 外部不可达，仅 compose 网络内可达。若将来加反代或 publish 8000 即暴露。
- **无任何抓取配置**：全仓**不存在** `deploy/` 目录，也不存在 `prometheus.yml`/`*.service`/`supervisord*`（`[工具输出]`：`Get-ChildItem -Recurse -Include prometheus*.yml,*.service,supervisord*` → ABSENT）⇒ `/metrics` 是「实现了但没接线」的指标端点。`README.md:197-203` 描述监控能力时**未承诺**抓取配置，措辞上不算幻觉。
- ⚠️ `README.md:200` 声称 `/health/ready`、`/health/live` 是「K8s readiness/liveness 探针」——**功能上是错的**：K8s 依据 **HTTP 状态码**判活，而 `/health/ready` 恒返 200（见 A4）⇒ 直接套用会让 `sqlite`/`data_root` 检查失败的 Pod 被判定为 **Ready** 并接流量。这是把「自述式健康端点」误当「探针端点」的文档级误导。

---

## 7. CI 与部署路径的差异（任务书 §6：只补部署相关，不重复 mypy/E2E 结论）

| # | 差异 | 位置 | 影响 |
|---|---|---|---|
| DEP-16 | **CI/Nightly 从不构建镜像、从不校验 compose** | `ci.yml`（56 行）/`nightly.yml`（68 行）全文**无** `docker build`/`docker compose config` 步骤 | ⇒ §2 的全部 P0 对 CI **结构性不可见**。这是「1065 passed 却起不来」的直接机制。加一条 `docker compose config && docker build` 即可拦住 F1–F5 |
| DEP-17 | CI 覆盖了容器**不覆盖**的两个关键变量 | `ci.yml:31-33`、`nightly.yml:36-38`：`SQLITE_URL`（显式）＋ `DATA_ROOT=./data/test_parquet`（**相对路径**） | 恰好绕过 `PROJECT_ROOT` 的绝对路径推导与真正的 `LOG_DIR`/`MODEL_ROOT`。生产容器**一个都没设** ⇒ CI 绿灯不构成任何容器可用性证据 |
| DEP-18 | Nightly 用 **dev server** 而非生产产物 | `nightly.yml:44-49` `nohup npm run dev -- --port 5173` | ⇒ `frontend/nginx.conf`、`frontend/Dockerfile`、SPA 静态产物、`/api/` 反代**全部零端到端覆盖**（SSE 经由 nginx 的行为自然也没测过） |
| DEP-19 | CI 无任何部署制品校验 | 无 hadolint / `docker build` / `nginx -t` / compose schema 校验 | Dockerfile 的悬空依赖（A5 的 `build-essential/wget`）、nginx 配置漂移（与 `docs/项目开发文档.md` §13.6 已不一致）无人拦截 |

---

## 8. 文档幻觉专项：`docs/项目开发文档.md` §13 是一份**过期且会主动误导**的部署契约

该文档 §13（`:5534` 起）自述「部署所需 6 份模板 + 10+ 步骤已全部给出」（`:5993`），但**与仓库中真实文件大面积冲突**。若运维照此执行，会得到**更坏**的结果：

| # | 文档写法 | 仓库事实 | 后果 |
|---|---|---|---|
| 1 | `DATABASE_URL=sqlite+aiosqlite:////app/data/aqp.db`（`:5564`） | 真字段是 **`SQLITE_URL`** | 静默丢弃（`extra="ignore"`），DB 路径不变 |
| 2 | `REDIS_URL=redis://redis:6379/0`（`:5567`） | 无此字段（用 `REDIS_HOST/PORT/DB`） | 静默丢弃 |
| 3 | `REDIS_CONNECT_TIMEOUT` / `REDIS_READ_TIMEOUT`（`:5570-5571`） | 只有单个 `REDIS_TIMEOUT` | 静默丢弃 |
| 4 | `APP_API_KEY=...`（`:5575`） | 真字段是 **`ADMIN_TOKEN`** | 静默丢弃 ⇒ **仍是公开默认 token** |
| 5 | **`APP_ENV=prod`**（`:5582`） | 真字段是 **`ENV`** | 🔴 **静默丢弃 ⇒ `validate_runtime_safety` 的 prod 分支永不执行**。这是同名 §3.4 全部危险的「总开关」 |
| 6 | `APP_PORT=8000`（`:5583`） | 真字段是 `API_PORT`（且无读取方） | 静默丢弃 |
| 7 | `DATA_DIR=/app/data`（`:5893`） | 无此字段（应为 **`DATA_ROOT`**） | 🔴 静默丢弃 ⇒ **文档版 compose 同样修不好 F2** |
| 8 | `CORS_ORIGINS=...`（`:5579`）、`LOG_LEVEL`、`TZ` | 与真字段一致 | ✅ 唯三正确项 |
| 9 | `env_file: - .env`（`:5889-5890`） | 真实 compose **没有** `env_file` | 文档版**反而更接近可用**（`env_file` 会把变量注入进程 environ，从而被 pydantic 读到）——说明真实 compose 是**从文档版退化而来**（F6） |
| 10 | `COPY pytest.ini` / `COPY tests`（`:5707-5708`，根相对） | 真文件在 `backend/` 下 | 照抄**构建直接失败**（文件不存在）；真实 Dockerfile 已修正为 `backend/pytest.ini` |
| 11 | `HEALTHCHECK ... /api/health`（`:5717`） | **无 `/api/health` 端点**（只有 `/health`、`/health/ready`、`/health/live`） | 照抄 ⇒ `curl -fsS` 404 ⇒ **容器永远 unhealthy** |
| 12 | `CMD uvicorn app.api.v1.router:app --loop uvloop --proxy-headers`（`:5720-5724`） | `router.py` 只有 `APIRouter`，**不是 ASGI app**；`uvloop` **不在 requirements.txt** | 照抄 ⇒ 启动即失败（两处） |
| 13 | `COPY --from=builder /usr/lib/libta_lib* /usr/lib/`（`:5698`）+ builder 编译 TA-Lib（`:5654-5661`） | 真实 Dockerfile 已删除 TA-Lib 构建，但留下 `wget`/注释残骸 | 解释 A5 的悬空依赖来源 |
| 14 | D5 `sudo chown -R $USER:$USER /opt/aqp`（`:5944`） | 容器 uid = **10001** | 🔴 文档步骤**制造** F9 的属主冲突 |
| 15 | D11 `curl http://127.0.0.1:8080/api/health` → 期望 `code=0`（`:5950`） | nginx 只代理 `/api/`，而 `/health` 不在 `/api/` 下；`/api/health` → 后端 404 → 经 `errors.py:136-142` 变成 **HTTP 200 + `code=40400`** | 「验收步骤」会显示 200 但 `code` 非 0，且不指出原因 |
| 16 | 前端反代描述「`/static/*` → 本地 dist」（`:5543`） | 真实 `nginx.conf:16` 是 **`/assets/`** | 拓扑图与实际不一致 |

> **注意正反两面**：第 9/10/11/12 项说明真实文件**比文档版更新、更正确**（真实 Dockerfile 的 `app.main:app`、`/health/ready`、`backend/pytest.ini` 都是对的）。因此 §13 **不是**「真实配置的说明书」，而是**上一轮迭代的快照**。风险在于 §13 自称「部署所需模板已全部给出」，且 `docs/项目开发文档.md:5` 的免责声明只说本文「包含早期开发步骤…不能单独作为当前运行/API 契约」——**没有点名 §13 已失效**，而 `:49` 又宣称「Docker Compose 一键部署」已达成。

---

## 9. 汇总表

> 置信度：**高** = 静态确证或已实测；**中** = 机制确定但端到端未跑（无 daemon）；**低** = 依赖运行态假设。
> 验证命令中 `$JWT` 为管理员 JWT；所有 docker 命令需在有 daemon 的机器执行。

| # | 严重度 | 类型 | 位置 | 现象 / 触发条件 | 置信度 | 验证命令 |
|---|---|---|---|---|---|---|
| DEP-01 | **P0** | 路径/只读 | `config.py:234` + `compute_guard.py:11` | 镜像内 `LOG_DIR=/backend/logs`（`PROJECT_ROOT=/`），import 期 `get_settings()` mkdir 撞 `read_only:true` ⇒ `EROFS` ⇒ **uvicorn 启动即崩、无限重启** | 高 | `docker build -t aqp-api -f backend/Dockerfile . && docker run --rm --read-only --tmpfs /tmp aqp-api python -c "from app.core.config import get_settings; get_settings()"` |
| DEP-02 | **P0** | 路径/挂载 | `compose:56-58` vs `config.py:235-237,64` | 卷挂 `/app/data`、`/app/logs`，代码用 `/data/parquet`、`/data/models`、`/data/sqlite`、`/backend/logs` ⇒ **挂载点 100% 废弃**；`mkdir` 目标全部未挂载 | 高 | `docker compose run --rm --entrypoint sh aqp-api -c 'ls -ld /data /backend/logs /app/data /app/logs'` |
| DEP-03 | **P0** | 日志 | `logging.py:52-61,64-73` + `main.py:41` | loguru 文件 sink 无 `delay=True` ⇒ 立即 open；`LOG_DIR` 不可写 ⇒ lifespan 抛错 ⇒ **启动中止**（与 DEP-01 独立的第二个阻塞点，且无 stdout 降级） | 高 | `docker run --rm --read-only --tmpfs /tmp -e LOG_DIR=/nope aqp-api python -c "from app.core.logging import setup_logging; setup_logging()"` |
| DEP-04 | **P0** | DB | `init_db.py:36,41` + `main.py:43` | `/data/sqlite` 未挂载 + 只读 ⇒ 建库/WAL 失败 | 高 | `docker compose run --rm aqp-api python -c "import asyncio;from app.db.init_db import init_database;asyncio.run(init_database())"` |
| DEP-05 | **P0** | 编排 | `compose:76-78` | `aqp-web depends_on aqp-api: service_healthy`，而 api 永不 healthy ⇒ **aqp-web 不被创建**，8080 无监听（全栈不可达） | 高 | `docker compose up -d && docker compose ps -a`（观察 aqp-web 不存在 / Created） |
| DEP-06 | **P0** | 配置注入 | `compose:49-55`（无 `env_file`） | `[工具输出]` 解析后 api 环境**仅 5 键**（REDIS_* + TZ）；`ADMIN_TOKEN/ENV/JWT_SECRET/ALLOW_*/DATA_ROOT/LOG_DIR/SQLITE_URL` 均无 ⇒ 配置层不可运维 | 高 | `docker compose config --format json \| jq '.services["aqp-api"].environment'` |
| DEP-07 | **P1** | 安全 | `config.py:210-226` + DEP-06 | `ENV` 默认 `dev` ⇒ `validate_runtime_safety` **只告警不 raise** ⇒ 「生产部署」实际用公开默认 `ADMIN_TOKEN`、`ALLOW_ADMIN_TOKEN_LOGIN=true`、`ALLOW_REGISTRATION=true`、`JWT_SECRET` 由默认 token 派生（可伪造 admin token） | 高 | `docker compose exec aqp-api python -c "from app.core.config import get_settings as g;s=g();print(s.ENV,s.ADMIN_TOKEN,s.ALLOW_ADMIN_TOKEN_LOGIN,s.ALLOW_REGISTRATION)"` |
| DEP-08 | **P1** | 权限/挂载 | `Dockerfile:49-50` + `compose:56-58` | 构建期 `chown aqp:aqp /app` 被 bind mount 遮蔽；宿主目录 owner 非 **10001** 时 uid 10001 不可写 ⇒ 「只改环境变量」不足以修复 DEP-01/02 | 高 | `docker compose exec aqp-api id -u; ls -ln ./data ./backend/logs` |
| DEP-09 | **P1** | 运维/数据 | `backup.py:57-73` + `README.md:213` | `scripts/backup.py` 的 `main()` **先 `unlink()` 生产 `aqp.db` 再解压还原**；README 把它作为**每晚 cron 推荐** ⇒ 已配 cron 的机器每晚删生产库；WAL 下还可能 `database disk image is malformed`。仓库已有安全版 `backup_drill.py`（用 tempdir） | 高 | `python -c "import ast,sys;t=ast.parse(open('backend/scripts/backup.py').read());print([n.func.attr if isinstance(n.func,ast.Attribute) else '' for n in ast.walk(t) if isinstance(n,ast.Call) and 'unlink' in ast.dump(n)])"`（或在数据副本上跑 `python scripts/backup.py` 观察 DB 被删） |
| DEP-10 | **P1** | 观测语义 | `main.py:254-296` + `errors.py:57-59` + `README.md:200` | `/health/ready` 恒返 **HTTP 200**（`ok()` 包装），`status:"not_ready"` 只在 body 里 ⇒ compose healthcheck 与 README 宣称的「K8s readiness 探针」**都无法据此判活**；`sqlite`/`data_root` 失败时 Pod 仍被判 Ready | 高 | `docker compose exec aqp-api sh -c 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health/ready; curl -s http://127.0.0.1:8000/health/ready'` |
| DEP-11 | **P1** | 密钥泄漏 | `Dockerfile:59` + `nginx.conf:24-34` | 缺 `--no-access-log`，且 nginx `/api/` **未 `access_log off`** ⇒ SSE 一次性 ticket（query string）**同时**落入 uvicorn 与 nginx 两份 access log（应用自身已做 `_sanitize_query`，加固漏在 access log 层） | 高 | `docker compose exec aqp-web grep -n "access_log\|location /api" /etc/nginx/conf.d/default.conf; docker compose logs \| grep -c "stream?ticket="` |
| DEP-12 | **P1** | 代理头 | `Dockerfile:59` | 缺 `--proxy-headers`/`--forwarded-allow-ips`（nginx 已发 `X-Forwarded-For`，`:29-30`）⇒ 应用侧真实客户端 IP 丢失（审计/限流失真） | 高 | `docker compose exec aqp-web curl -s -H 'X-Forwarded-For: 1.2.3.4' http://aqp-api:8000/health >/dev/null; docker compose logs aqp-api \| tail`（旧文档 `项目开发文档.md:5724` 反而带该参数） |
| DEP-13 | **P1** | 文档幻觉 | `docs/项目开发文档.md:5564-5583,5893,5944,5950` | §13 部署契约 6 个变量名不存在（含 **`APP_ENV`→真名 `ENV`**、`DATA_DIR`→真名 `DATA_ROOT`）⇒ 照做则**静默丢弃**、prod 安全闸门永不触发；D5 的 `chown $USER` 制造 DEP-08；D11 验收命令错误 | 高 | `python -c "from app.core.config import Settings as S;print([k for k in ('DATABASE_URL','REDIS_URL','APP_API_KEY','APP_ENV','APP_PORT','DATA_DIR') if k in S.model_fields])"` → `[]` |
| DEP-14 | **P1** | 文档幻觉 | `docs/项目开发文档.md:5707-5708,5717,5720-5724` | 该版 Dockerfile `COPY pytest.ini`/`tests`（根相对）、healthcheck `/api/health`（无此端点）、`CMD ...router:app --loop uvloop`（router 非 ASGI app、uvloop 未安装）⇒ **照抄构建/启动必失败** | 高 | `docker build -t aqp-doc -f <按文档生成的 Dockerfile> .` → 观察 `pytest.ini: not found` |
| DEP-15 | **P1** | 只读容器写任务 | `config.py:159-166` + `main.py:90-93` | `EVENING_ROUTINE_ENABLED=true`、`AUTO_RETRAIN_ON_DRIFT=true` 默认开启且容器无法关闭；启动 `startup_catchup()` 立即触发 + 每日 17:30 例行 ⇒ 往只读路径写 features/models | 中 | `docker compose exec aqp-api python -c "from app.core.config import get_settings as g;s=g();print(s.EVENING_ROUTINE_ENABLED,s.AUTO_RETRAIN_ON_DRIFT)"` |
| DEP-16 | **P2** | CI 缺口 | `ci.yml`/`nightly.yml` 全文 | **从不 `docker build` / `docker compose config`** ⇒ §2 全部 P0 对 CI 不可见；CI 用相对 `DATA_ROOT` + 显式 `SQLITE_URL` 绕过真实默认路径 | 高 | `rg -n "docker" .github/workflows/` → 仅 redis service image（无 build 步骤） |
| DEP-17 | **P2** | 前端镜像 | `frontend/Dockerfile:12-15` | 未 COPY `frontend/public/` ⇒ 镜像内缺 `ref-watchlist.png`；SPA 源码零引用 ⇒ 无用户可见影响（低危记录） | 高 | `docker run --rm --entrypoint ls aqp-web /usr/share/nginx/html`（对比 `frontend/dist/`） |
| DEP-18 | **P2** | 日志运维 | `compose` 三服务均无 `logging:` | 默认 `json-file` 无 `max-size/max-file`；容器内唯一出口是 stdout（DEP-03）⇒ 日志无限增长直至磁盘满 | 高 | `docker compose config --format json \| jq '[.services[] \| .logging]'` → `[null,null,null]` |
| DEP-19 | **P2** | 观测接线 | 全仓无 `deploy/`、无 `prometheus.yml` | `/metrics` 已实现但**无抓取配置**；且容器 `ENV=dev` ⇒ 免认证（未 publish 8000、nginx 不代理，暂不外泄） | 高 | `docker compose exec aqp-api curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/metrics` → `200`（免认证） |
| DEP-20 | **P2** | 备份 | `app_settings.py:317-318` | 只读容器内 `backend/backups` 不可建 ⇒ 端点必失败，且失败被 `errors.py:160-174` 转成 **HTTP 200 + code=50000**，无根因提示、监控无法按状态码告警 | 高 | `docker compose exec aqp-api sh -c 'curl -s -X POST -H "Authorization: Bearer $JWT" http://127.0.0.1:8000/api/v1/settings/db/backup; echo; echo "http=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $JWT" http://127.0.0.1:8000/api/v1/settings/db/backup)"'` |
| DEP-21 | **P2** | 模型治理 | `purge_legacy_models.py:71` | `DELETE FROM model_registry` **无 WHERE**，清空含 `is_production=1` 的行；脚本已查出 `n_prod` 却不设防；仅 `--apply` 作护栏，无备份、无确认 | 高 | `python scripts/purge_legacy_models.py`（干跑观察打印的 `is_production=1` 计数）；`python -c "import re;s=open('backend/scripts/purge_legacy_models.py').read();print(re.findall(r'DELETE FROM.*',s))"` |
| DEP-22 | **P2** | 恢复脚本 | `restore.py:14-31` + `backup.py:53` | 无确认提示；`--file` 缺失/末位时抛裸 `IndexError`/`ValueError`；`extractall` 无 `filter=` 直接覆盖生产 `data/` | 高 | `python scripts/restore.py`（无参）→ 观察用法提示后 `sys.exit(1)`；`python scripts/restore.py --file` → IndexError |
| DEP-23 | **P2** | 构建卫生 | 全仓无 `.dockerignore` | 构建上下文 = 仓库根，实测体积：`backend/.venv` **4.7 GB**、`data` **2.18 GB**、`frontend/node_modules` **149 MB**、`.git` 26 MB、`backend/backups` 35 MB ⇒ 每次 `docker build` 都把 **≈7 GB**（含未跟踪的 `.env` 与生产数据）送进 daemon；后续若有人加 `COPY . .` 即成密钥/数据泄漏 | 高 | `du -sh backend/.venv data frontend/node_modules .git; docker build -f backend/Dockerfile . 2>&1 \| head -3`（看 "Sending build context" 体积） |
| DEP-24 | **P2** | 前端配置 | `frontend/Dockerfile`（无 `ARG`/`ENV`） | 无 `VITE_API_BASE` 注入通道 ⇒ 无法产出指向异源 API 的镜像（当前同源默认可用，故非阻断） | 高 | `docker build -f frontend/Dockerfile --build-arg VITE_API_BASE=https://x .`（参数被忽略） |
| DEP-25 | **P2** | 原生运维 | 全仓无 systemd/supervisor | 原生路径无进程守护、无开机自启、无崩溃重启（`run.md:160` 如实说明「没有一键脚本」，非幻觉） | 高 | `Get-ChildItem -Recurse -Include *.service,supervisord*` → ABSENT |
| DEP-26 | **P3** | 文档/依赖陈旧 | `run.md:55,231-235`、`Dockerfile:2,9` | TA-Lib 排障段落指向 `requirements.txt` 中**不存在**的 `TA-Lib==0.4.28`；连带解释 builder 的 `build-essential/wget` 为死重量 | 高 | `rg -n -i "ta-lib" backend/requirements.txt` → 无匹配 |
| DEP-27 | **P3** | 凭据卫生 | `create_admin.py:53-54` + `run.md:198`、`README.md:175` | 密码经 `argv` 传入 ⇒ shell history/进程列表可见；且已存在用户时「跳过」⇒ **无法轮换管理员密码** | 高 | `python scripts/create_admin.py admin x 2>&1; python scripts/create_admin.py admin y 2>&1` → 第二次输出「已存在（跳过）」 |
| DEP-28 | **P3** | 契约漂移 | `.env.example:16` + `auth.py:154` | `JWT_EXPIRE_SECONDS` 写进 `.env` **无效**（真源是 `os.environ`）；容器内也无从设置 ⇒ 有效期恒 7 天 | 高 | `python -c "import os;os.environ.pop('JWT_EXPIRE_SECONDS',None);from app.core.auth import _jwt_expire_seconds as f;print(f())"`（或在 `.env` 写 1234 后验签 payload） |
| DEP-29 | **P3** | 命名幻觉 | `docs/auth-register.md:38-39` + `config.py:137`、`api/v1/auth.py:4-5` | 文档教写 `AQP_ALLOW_REGISTRATION` / `AQP_REGISTER_DEFAULT_ROLE`，但 `Settings` 无 `env_prefix` ⇒ 静默丢弃（dev 全静默；prod 报错也不提示变量名写错） | 高 | `AQP_ALLOW_REGISTRATION=false python -c "from app.core.config import Settings;print(Settings().ALLOW_REGISTRATION)"` → `True` |
| DEP-30 | **P3** | 脚本/观感 | `run_offline_tests.sh:6-10`、`pytest.ini:8` | `.sh` 硬编码 Windows venv 路径（回落裸 `python3` ⇒ 环境漂移风险）；`pytest.ini` 的 `-v` 覆盖脚本 `-q` | 高 | `bash -n backend/scripts/run_offline_tests.sh && echo OK` |
| DEP-31 | **P3** | 文档准确性 | `run.md:241`、`README.md:160-166` | `run.md:241` 称根 `docker-compose.yml` 是「**生产部署**用的整体容器化方案」、`README.md:160-166` 给出「Docker 部署（P0 最小）」三步，而该方案**必崩**（DEP-01–05）⇒ 虚假可用性声称（两处均未提示任何前置条件或已知限制） | 高 | 见 DEP-01 验证命令 |
| DEP-32 | **P3** | nginx 细节 | `nginx.conf:16-21` | `location /assets/` 内 `add_header Cache-Control` 会**覆盖** server 级 `add_header`（nginx 语义）⇒ `/assets/` 响应丢失 `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy` 三个安全头 | 高 | `curl -sI http://127.0.0.1:8080/assets/<hash>.js \| grep -i "x-content-type\|x-frame"` → 缺失 |

**不构成缺陷（已核查、避免误导）**：
- **SSE 不会被 nginx 缓冲**（A1-f）：`X-Accel-Buffering: no`（`notify.py:34`）生效，且本配置未设 `proxy_ignore_headers`。任务书假设的「通知中心永远收不到事件」**不成立**。残余风险仅为「靠单个响应头、无 SSE 专用 location、无容器侧测试覆盖」。
- **前端不需要构建期 `VITE_API_BASE`**（A1-a）：`client.ts:80` 回落 `/`；`window.__DSH_BOOT__` 与本仓无关。
- **两个 Dockerfile 的 healthcheck 工具链完整**：`curl` 在 backend/frontend runtime 均已安装（`:34` / `:23`）。
- **`run.md` 未声称任何不存在的 systemd/supervisor 单元**（仅「缺失」）。
- **`bootstrap.py` / `create_admin.py` 的幂等性正确**；`bootstrap.py:27-32` 对目录不可写会给出明确退出。
- **`/metrics` 当前不外泄**（未 publish 8000、nginx 不代理）。
- **`libgomp1`/`tzdata` 在 runtime 齐备**，多阶段 venv 传递完整（A5）。

---

## 10. 最小验证命令集（需有 docker daemon 的机器；一次到位）

```bash
# ---------- 0. 前置（compose 要求的必填插值）----------
cp .env.example .env
docker compose config --format json | jq '.services["aqp-api"].environment'   # 期望只看到 5 个键 => DEP-06

# ---------- 1. 容器内 PROJECT_ROOT / 路径推导（本机已用 PurePosixPath 等价确证）----------
docker build -t aqp-api -f backend/Dockerfile .
docker run --rm --entrypoint python aqp-api -c \
  "from app.core.config import PROJECT_ROOT,get_settings as g;s=g();print(PROJECT_ROOT,s.LOG_DIR,s.DATA_ROOT,s.MODEL_ROOT,s.SQLITE_PATH)"
# 期望 PROJECT_ROOT=/  LOG_DIR=/backend/logs  DATA_ROOT=/data/parquet ... => DEP-01/02

# ---------- 2. 只读 + 非 root 下 import 期必崩（DEP-01/03，最关键一条）----------
docker run --rm --read-only --tmpfs /tmp \
  -v "$PWD/data:/app/data" -v "$PWD/backend/logs:/app/logs" aqp-api \
  python -c "from app.core.config import get_settings; get_settings()"
# 期望：OSError [Errno 30] Read-only file system: '/backend/logs'

# ---------- 3. 整栈是否可达（DEP-04/05）----------
docker compose up -d --build
docker compose ps -a                      # 期望 aqp-api Restarting/Exited；aqp-web 不存在
docker compose logs --tail=40 aqp-api
curl -sv http://127.0.0.1:8080/           # 期望 connection refused

# ---------- 4. healthcheck 语义（DEP-10）----------
docker compose up -d && docker inspect --format '{{.State.Health.Status}}' aqp-api
docker compose exec aqp-api sh -c \
  'curl -s -o /dev/null -w "ready_http=%{http_code}\n" http://127.0.0.1:8000/health/ready; curl -s http://127.0.0.1:8000/health/ready'

# ---------- 5. SSE 经 nginx 是否真被缓冲（本报告结论：不会）----------
JWT=$(curl -s -X POST http://127.0.0.1:8080/api/v1/auth/login -H 'Content-Type: application/json' \
      -d '{"username":"admin","password":"<pw>"}' | jq -r .data.token)
TICKET=$(curl -s -X POST http://127.0.0.1:8080/api/v1/notify/stream-ticket -H "Authorization: Bearer $JWT" | jq -r .data.ticket)
timeout 40 curl -N -s "http://127.0.0.1:8080/api/v1/notify/stream?ticket=$TICKET" | head -c 300
# 期望：约 1s 内出现 ": connected"（若被缓冲则整段 40s 无输出）；15s 内出现 ": ping"
# 对照：docker compose exec aqp-web nginx -T | grep -n "proxy_buffering\|X-Accel"

# ---------- 6. 权限/属主（DEP-08）----------
docker compose exec aqp-api id -u            # 期望 10001
ls -ln ./data ./backend/logs                 # 期望 owner=10001 才能写

# ---------- 7. 备份端点错误码可见性（DEP-20）----------
docker compose exec aqp-api sh -c \
  'curl -s -o /tmp/b -w "http=%{http_code}\n" -X POST -H "Authorization: Bearer '"$JWT"'" http://127.0.0.1:8000/api/v1/settings/db/backup; cat /tmp/b'
# 期望：http=200 且 body.code=50000（不是 5xx）=> 监控无法告警

# ---------- 8. 构建上下文体积 / .dockerignore 缺失（DEP-23）----------
du -sh backend/.venv data frontend/node_modules .git
docker build -f backend/Dockerfile . 2>&1 | head -3     # 看 "Sending build context"

# ---------- 9. 日志驱动无上限（DEP-18）----------
docker inspect -f '{{.HostConfig.LogConfig}}' $(docker compose ps -q aqp-api)
```

**无 docker 时的等价（本报告已执行的部分）**：
```powershell
# 容器内 PROJECT_ROOT 与派生路径（已执行，输出 PROJECT_ROOT=/ 等）
python -c "from pathlib import PurePosixPath as P; print(P('/app/app/core/config.py').parents[3])"
# compose 契约与语法（已执行：config --no-interpolate exit 0；空 REDIS_PASSWORD 时 exit 1）
docker-compose --env-file <empty> -f docker-compose.yml config
```

---

## 11. 明确「无法验证」的部分（原因 + 验证方式）

| 无法验证项 | 原因 | 在有 docker 的机器上这样验证 |
|---|---|---|
| 镜像是否**真的**构建成功、层体积 | 无 daemon（禁止 `docker build`） | `docker build -f backend/Dockerfile .`；`docker images aqp-api` |
| F1–F5 的**实际异常文本与退出码** | 同上（推理链已完整，但未观察到真实堆栈） | §10 第 2 步命令；期望 `OSError [Errno 30] Read-only file system: '/backend/logs'` |
| `aqp-web` 是否真的「不被创建」而非「Created 后不再启动」 | 依赖 Compose 对 `depends_on: service_healthy` 的实现细节 | `docker compose up -d && docker compose ps -a` |
| SSE 端到端是否确实不缓冲 | 需真实 nginx + 反代链路（机制依据 nginx 文档语义，置信度「高」但非实测） | §10 第 5 步 `curl -N`；并 `nginx -T \| grep proxy_buffering` |
| nginx access log 是否实际记录 ticket | 需运行容器后读日志 | `docker compose logs aqp-web \| grep -c "stream?ticket="` |
| 宿主机 `./data` 在 Linux 上的真实属主与 uid 10001 是否冲突 | 本机是 Windows（NTFS 无 uid 语义）⇒ 该冲突**只在 Linux 成立** | `ls -ln ./data`（Linux 宿主）与 `docker compose exec aqp-api id -u` 对比 |
| `docs/项目开发文档.md` §13 版 Dockerfile「照抄必失败」的实际报错 | 无 daemon，且未生成临时 Dockerfile（纪律要求不改仓库文件） | 把 §13 片段存到 `$TMP/Dockerfile.doc` 后 `docker build -f $TMP/Dockerfile.doc .` |
| 备份 cron 在**真实并发写**下是否损坏 WAL DB | 需在有写入压力的库上复现，属破坏性操作，纪律禁止 | 在 `data` 副本上：起 API → `python scripts/backup.py` → 比对重启后 `PRAGMA integrity_check` |

---

## 12. 一句话根因链（给修复排序用）

```
compose 无 env_file（DEP-06）
   └─> ENV 默认 dev（DEP-07）──> 唯一安全闸门 validate_runtime_safety 的 raise 分支永不执行
   └─> 无法注入 DATA_ROOT/LOG_DIR/MODEL_ROOT/SQLITE_URL
          └─> 沿用 PROJECT_ROOT="/" 派生的 /data/*、/backend/logs（DEP-01/02）
                 └─> 与 compose 的 /app/data、/app/logs 挂载点错配（DEP-02）
                        └─> read_only:true 把「路径错误」升级为 import 期 EROFS 崩溃（DEP-01/03/04）
                               └─> aqp-api 永不 healthy ──> aqp-web 不被创建（DEP-05）──> 全栈不可达
          （修路径后仍有第二层）宿主属主 vs uid 10001（DEP-08）+ 文档教人 chown $USER（DEP-13）
```

**建议的最小修复集**（按性价比排序，供修复批次参考）：
1. compose 加 `env_file: - .env`；`.env.example` **补齐** `DATA_ROOT/MODEL_ROOT/LOG_DIR`（并在模板里给出容器专用示例值 `/app/data/parquet` 等）→ 一并解决 DEP-01/02/06/07。
2. compose 把卷挂到代码真实路径（或在 env 里把路径指到 `/app/data`、`/app/logs`）→ DEP-02/08。
3. `logging.py` 文件 sink 加 `delay=True`，并把 `setup_logging` 包进 try/except 降级为「仅 stdout」→ DEP-03。
4. CI 加 `docker compose config` + `docker build` 两步 → DEP-16 起拦截全部 P0。
5. `backup.py:main()` 去掉演练（或改为调用 `backup_drill.py` 的 tempdir 版）；`purge_legacy_models.py` 的 DELETE 加 `WHERE is_production=0`；`restore.py` 加确认 → DEP-09/21/22。
6. `CMD` 补 `--no-access-log --proxy-headers --forwarded-allow-ips=*`；`nginx.conf` 的 `/api/` 加 SSE 专用 location（`proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s;`）而非依赖响应头 → DEP-11/12 + A1-f 残余风险。
7. 把 `docs/项目开发文档.md` §13 整体替换为「以仓库真实文件为准」或删除 → DEP-13/14。