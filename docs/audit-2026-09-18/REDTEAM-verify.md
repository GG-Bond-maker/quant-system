# AQP 审核团 · 红队复核报告（REDTEAM-verify）

> **角色**：红队复核员。任务**不是**再找新问题，而是对主报告 [AQP-全栈审核报告.md](AQP-全栈审核报告.md) §2（P0 六条）与 §3（P1 表）中最严重的 12 条做**证伪尝试**：找被夸大的严重度、错误的触发条件、站不住的结论。
> **日期**：2026-09-21 · **只读纪律**：未改任何源码/测试/生产数据；全部探针落在 `$env:TEMP`（后端命令用 `backend\.venv\Scripts\python.exe`，`PYTHONPATH=$base`，`TEMP/TMP=$base\.tmp_testrun`）。
> **未做的事（诚实边界）**：未跑全量 pytest；未起真服务（P0-2 用函数级直调，P0-3/P1-23 用 `fastapi.testclient` ASGI 直调）；未对生产库做任何写操作（P1-3 用**内存 sqlite**）。

## 0. 判定汇总（12 行）

| 编号 | 判定 | 一句话依据 |
|---|---|---|
| P0-1 | **维持** | 导入期 `mkdir` 链实证成立（`import app.main` 即触发 4 次 `os.mkdir`），首个失败点是 `LOG_DIR=/backend` 而非报告写的 `/data/parquet`；compose/Dockerfile 未设任何覆盖项 ⇒ 无旁路 |
| P0-2 | **维持** | 函数级实测：默认 `ADMIN_TOKEN` 直通返回 `{'username':'admin','role':'admin'}`，`aqp-derive:<默认token>` 伪造 JWT 亦通过；compose 无 `env_file`、镜像不含 `.env` |
| P0-3 | **维持** | ASGI 匿名实测 `code=0`，泄露设备名/绝对 features 路径/样本量/边数；兄弟端点 `40100`；无全局鉴权中间件（仅 PanicGuard+CORS+计时） |
| P0-4 | **部分推翻（下调）** | "与真实 0% 基准不可区分"不成立：合成路径 `risk.alpha/beta/info_ratio` 全 null 且 `nav_curve.benchmark` 是字面常量 1.0；真实 0% 基准在我的实测里 α=0.696、β=-0.036、IR=5.32。降级无披露仍成立 |
| P0-5 | **维持（触发条件须改）** | 实测 72 B/组合、笛卡尔积先于守卫确认；但报告举例 `150^6` 经 HTTP **不可达**（白名单只允许 2–3 键），真实可达样本更小：**22.5 KB** JSON → 1e9 组合 → ~72 GB |
| P0-6 | **推翻定级（非 bug）** | 分支顺序正确且 `board=bse` 实测命中 `ok/no_matching_signals`；前端已区分两态并给中文文案 ⇒ 是契约冲突，应从 §2 P0 降为裁决项（P2） |
| P1-1 | **维持（更强）** | 独立复算到分：21 交易日仅 1 天成交（turnover 0.95），decay 却在 17 天各扣一次，合计 **16027.77 元**（我的手算逐日累加完全一致）；单次应为 ~950 元 ⇒ 16× 重复扣 |
| P1-3 | **维持** | 内存 sqlite 独立复现：裸卖 10 万元 → 1 笔成交 8400 股 → 权益 1,000,000 → **1,098,871.19**（持仓 `{}`）；`place_order`/`run_fills`/API 三层均无持仓校验 |
| P1-8 | **推翻（结论错误）** | 真实端点**不抛错**：走 `response_model` 时 Pydantic 把 nan/inf 渲染成 **null**（http=200 code=0）；"恒 50000"只在不带 response_model 的 4 条路由上可能，而它们不产 NaN。建议降 P2 |
| P1-14 | **维持（置信度标注修正）** | 算术独立复现 +29.4392%（主报告 +29.4% 正确，B3b 的 29.5% 是舍入笔误）；`f55+f56 == f52` 精确成立 ⇒ f52 是主力**净额**。原始响应无法独立取数（无外网），系数值来自 B3b 落盘单点引用 |
| P1-22 | **维持（影响面收窄）** | 机制确证（首个 option 必丢，React 依赖数组语义 + `react-dom.development.js:26731-26739` 提交前 flush 旧 passive effects）；但"图表空白"只在**条件挂载 + useMemo option** 结构出现（Research 页 4 图 + IC 衰减图），inline option 的 8 处调用点正常 |
| P1-23 | **维持（两处细节修正）** | 实测 7 个 GET 对 viewer 返回 `40300`、匿名 `40100`；路由仅要求 viewer + 按钮无角色门禁已逐点核对。修正："1.5s 轮询持续失败"不成立；desk 端点被 `<RequireRole>` 挡在 viewer 之外 |

---

## P0-1 容器内 `PROJECT_ROOT` 退化为 `/` ⇒ 只读根 fs `mkdir` 失败

**结论：维持（缺陷与崩溃链成立）。三处需修正细节。**

### 独立方法
1. **纯路径推导 + 容器目录模拟**：在本机按容器层级造 `<root>/app/app/core/config.py`（Dockerfile `COPY backend/app $APP_HOME/app`，APP_HOME=/app ⇒ 镜像内 `/app/app/core/config.py`），以 `PYTHONPATH=<root>/app` 导入**真实的** `config.py`，读回 `PROJECT_ROOT` 与四个派生路径。
2. **代码执行链实证**（不是静态推断）：在 `import` 前给 `os.mkdir` 打 spy，看导入期到底有没有 `mkdir`。
3. **首次失败点定位**：在模拟根下把 `backend` 造成**普通文件**，让 `LOG_DIR` 的 `mkdir` 失败，看异常类型与首个目标路径。
4. **旁路验证**：分别只覆盖 `LOG_DIR`、再覆盖 `LOG_DIR+DATA_ROOT+MODEL_ROOT+SQLITE_URL`，看能否绕过。

### 真实输出
```
=== A: container-analog（root/app/app/core/config.py，PYTHONPATH=root/app）===
parents[3] of config.py = <TEMP>\rt_p01e_c8877d71          ← 容器中该值 = "/"
PROJECT_ROOT = <TEMP>\rt_p01e_c8877d71    <-- analog of "/" in container
   LOG_DIR     = <root>\backend\logs
   DATA_ROOT   = <root>\data\parquet
   MODEL_ROOT  = <root>\data\models
   SQLITE_PATH = <root>\data\sqlite\aqp.db

=== C1: import app.core.compute_guard 是否调用 get_settings() ===
get_settings cache_info during compute_guard import = CacheInfo(hits=0, misses=1, maxsize=1, currsize=1)
os.mkdir calls = ['D:\...\backend\logs', 'D:\...\data\parquet', 'D:\...\data\models', 'D:\...\data\sqlite']

=== C2: import app.main（uvicorn app.main:app 的导入入口）===
app.main imported OK
os.mkdir calls during import app.main = ['D:\...\backend\logs', 'D:\...\data\parquet',
                                         'D:\...\data\models', 'D:\...\data\sqlite',
                                         'C:\Users\HY\.matplotlib']

=== B: 阻断 <root>/backend ===
  File "...\pathlib.py", line 1116, in mkdir
    os.mkdir(self, mode)
FileExistsError: ... 'C:\...\rt_p01f_e381032b\backend'      ← 首个 mkdir 目标是 <root>/backend
（只覆盖 LOG_DIR 时：NO ERROR；覆盖全部四个 env 时：NO ERROR）
```

### 判定依据（含修正）
- **执行链完全成立**：`app.main` → `api/v1/router.py:7` → `api/v1/backtest.py:23` → `core/compute_guard.py:11` 模块级 `get_settings()` → `config.py:234-237` 四次 `mkdir(parents=True)`。**导入期**即触发（uvicorn 尚未 bind 端口），在没有 `try/except` 的情况下直接崩进程。
- **修正 1（首个失败点）**：报告写"只读根文件系统上 `mkdir('/data/parquet')` 必失败"，实际**第一条 `os.mkdir` 目标是 `LOG_DIR` 的顶层 `/backend`**（probe B 实证：`os.mkdir(self)` 先建 `<root>/backend`）。结论不变，但修/测时别只盯 DATA_ROOT。
- **修正 2（旁路条件）**：环境变量**确实**能覆盖（pydantic-settings 按字段名读取，无 env_prefix；探针 D/E 实证：覆盖后 `NO ERROR`）。但请注意探针只对 `LOG_DIR` 造了阻断，所以"只覆盖 LOG_DIR 即通过"是模拟实验的人工产物；在**真实只读根**下 4 条路径（`LOG_DIR`/`DATA_ROOT`/`MODEL_ROOT`/`SQLITE_URL`）**全部** `EROFS`，必须同时覆盖 4 个才可能启动 —— 而 compose 是 `read_only: true`，`/data`、`/backend` 既不能创建也没有挂载，**即使 4 个 env 全给仍然失败**（除非同时加卷挂载或关掉 read_only）。**结论：shipped compose 无旁路。**
- **修正 3（挂载错位复核）**：`docker-compose.yml:56-58` 挂 `/app/data`、`/app/logs`，代码用 `/data`、`/backend/logs` —— 错位属实；Dockerfile 无 `DATA_ROOT/MODEL_ROOT/LOG_DIR/SQLITE_URL/ENV AQP_PROJECT_ROOT`，compose 的 `environment` 只有 `REDIS_*`+`TZ`。

**建议最终定级：P0 维持。** 理由：启动即崩、无旁路、修复面明确（修法照报告即可）。

---

## P0-2 compose 默认姿态 = 默认 ADMIN_TOKEN 可登录 + JWT 可伪造

**结论：维持。**

### 独立方法
1. 配置面核对：compose 是否有 `env_file`；Dockerfile 是否 COPY 根 `.env`（含 `.dockerignore` 检查）。
2. 用 `Settings(_env_file=None)` 复刻"容器内无 `/.env`、无 env"的姿态。
3. **函数级直调**（不起服务）：把 `app.core.auth.get_settings` 与 `_jwt_secret` 指向该默认 Settings，直接调 `require_auth(HTTPAuthorizationCredentials(...))`；再用 `aqp-derive:<默认 token>` 自签 admin JWT 过一遍。

### 真实输出
```
compose env_file    : 无（docker-compose.yml 全文无 env_file 键）
Dockerfile COPY     : backend/app → /app/app；backend/scripts；pytest.ini；tests
                      （根 .env 从不进镜像；仓库无 .dockerignore）
Settings(_env_file=None) → ENV=dev | ADMIN_TOKEN==DEFAULT: True | ALLOW_ADMIN_TOKEN_LOGIN=True
                           | ALLOW_REGISTRATION=True | JWT_SECRET=None
validate() in dev   → OK（warn only）；ENV=prod 才 raise

1) raw ADMIN_TOKEN accepted as: {'username': 'admin', 'role': 'admin'}
2) forged JWT (secret = aqp-derive:<default token>) -> {'username': 'attacker', 'role': 'admin'}
3) wrong token -> 拒绝 INVALID_TOKEN
```

### 判定依据
- 与报告一致：`auth.py:198` 在 `ALLOW_ADMIN_TOKEN_LOGIN` 为真时 `hmac.compare_digest(token, settings.ADMIN_TOKEN)` 直通 admin；JWT 密钥回退 `"aqp-derive:" + ADMIN_TOKEN`（`auth.py:150`）⇒ 公开默认值 ⇒ 可伪造任意角色。
- **可达性如实收窄**（与报告一致，此处给出证据）：`aqp-api` 只 `expose: 8000`（不发布到宿主），外部只能经 `aqp-web` 的 nginx `location /api/`（`frontend/nginx.conf`）访问，而 nginx 只 `127.0.0.1:8080:80`。**本机/内网另起的端口发布或运维改绑即等于把 admin 交出去**；报告已注明"是否外露取决于部署网络"，不予升级也不下调。

**建议最终定级：P0 维持**（默认凭据姿态 + 仅 warn + 可伪造 JWT 三者叠加）。

---

## P0-3 `GET /datacenter/train/readiness` 无鉴权

**结论：维持。**

### 独立方法
ASGI 直调（`fastapi.testclient`，`raise_server_exceptions=False`），匿名访问 readiness 与其兄弟端点，并**逐个排查全局中间件/路由级依赖**（读 `main.py:140-157,227` 的中间件栈与 `include_router` 调用）。

### 真实输出
```
/api/v1/datacenter/train/readiness  http=200 code=0     data_keys=['torch_ready','torch_device','torch_install_hint',
                                                         'features','est_samples','min_samples','sample_ok','gnn_edges']
/api/v1/datacenter/train/status     http=200 code=40100
/api/v1/datacenter/overview         http=200 code=40100
匿名 payload（截断）：{"torch_device":{"device":"xpu","device_name":"Intel(R) Arc(TM) Graphics",
  "torch_version":"2.14.0+xpu"}, "features":{"rows":2481630,"symbols":2500,"dates":2115,
  "last_date":"2026-09-17 00:00:00","dir":"D:\\Python_Project\\Alpha Quant Platform\\data\\parquet\\features\\version=alpha_basic_v2g"},
  "est_samples":5202500, "gnn_edges":71944, ...}
```
- `datacenter.py:1124-1129` 函数签名无 `_user` 依赖（兄弟 `train/status:1150-1152` 有 `require_role("researcher")`）。
- **无间接保护**：`main.py` 只注册 `PanicGuardMiddleware`（:147）、`CORSMiddleware`（:151）、计时/trace 的 `@app.middleware("http")`（:172）；`app.include_router(v1_router, prefix="/api/v1")`（:227）**未传 `dependencies=`**。CORS 不是鉴权。

**建议最终定级：P0 维持**（信息泄露起点：绝对路径+设备+样本量，且三轮未修）。

---

## P0-4 基准与复权口径不可辨（`close=1.0` ⇒ `annual_benchmark=0.0`）

**结论：缺陷维持，但"与真实 0% 不可区分"这一强表述被推翻 ⇒ 严重度下调（P2 级披露）。**

### 独立方法
用**真实的** `run_strategy` 跑三个对照（自造 80 天恒定斜率行情 + `MaCrossStrategy`）：
A = `benchmark=None`；B = 复刻 API 降级路径（`backtest.py:525-528` 的 1 行 `close=1.0`）；C = 形状真实、区间收益恰好 0% 的基准。逐一打印 `risk` 与 `nav_curve` 的基准取值集合。

### 真实输出
```
--- A. benchmark=None ---
annual_benchmark=0.0   alpha=nan beta=nan info_ratio=nan   bench_uniq=[nan]
--- B. API synthetic fallback（单行 close=1.0）---
annual_benchmark=0.0   alpha=nan beta=nan info_ratio=nan   bench_uniq=[1.]
--- C. real-shaped benchmark, exactly 0% over window ---
annual_benchmark=0.0   alpha=0.6961 beta=-0.03575 info_ratio=5.3233   bench_uniq=[0.97, 0.970031, ...]
```

### 判定依据
- `strategy_base.py:480-481`（`if not np.isfinite(bench_n).any(): bench_n = np.ones_like(strat)`）与 `:296-298` 的 `annual_return` ⇒ `annual_benchmark` 恰为 **0.0**（不是 NaN），报告这条**成立**。
- **推翻点**：合成基准在这条链上有两处**可辨信号**，与"真实 0% 基准不可区分"矛盾：
  1. `risk.alpha / beta / info_ratio` 全为 `null`（真实基准下有值：我这组 α=0.696、β=-0.036、IR=5.32）；
  2. `nav_curve[].benchmark` 是**字面常量 1.0**（真实基准逐日波动，探针 C 首值 0.97）。
  所以"不可区分"仅在**只看 `kpi` 卡片**时成立。
- 复权那半条报告已自降 P2（`source` 跟踪不读、F841、当前回退分支不可达），红队不重复追究。

**建议最终定级：P2（披露/一致性）**，措辞改为"基准降级时无显式 `basis` 披露，仅在 `risk.alpha/beta` 与常量 1.0 曲线间**间接**可辨"。**理由**：没有 50000/崩溃/错值，只有"缺少显式披露"；若坚持 P0 级，需给出"仅看 KPI 卡的用户"这一具体受害路径。

---

## P0-5 `grid_search` 先物化笛卡尔积 ⇒ OOM

**结论：缺陷维持（P0 可用性），但报告的触发示例经 HTTP 不可达，须改。**

### 独立方法
1. **内存实测**：`tracemalloc` 量 30³/50³/80³ 的 `list(itertools.product(...))` 峰值，得 B/组合；再用 `sys.getsizeof` 交叉校验；用 `psutil`（未装）改为纯算式外推。
2. **守卫顺序证明**：故意用 30³（27000 > max_trials=500）触发守卫，读守卫抛出的组合数。
3. **上游限制排查**：`StrategyBacktestRequest.optimize_params` 的 pydantic 约束、键白名单、请求体 JSON 大小、nginx 体积限制。
4. **影响面**：读 compose 的 `mem_limit`。

### 真实输出
```
sys.getsizeof((1,2,3)) = 64 bytes
N     combos    tracemalloc_peak_B   B/combo
30    27000     1947928              72.1
50    125000    8887352              71.1
80    512000    36809664             71.9
⇒ 150³ = 3,375,000 × 72 B = 243 MB（与 B5 实测 243MB 完全吻合）

27000 combos -> ValueError: 网格组合数 27000 超过上限 500，请缩小参数空间   ← 守卫在 list() 之后

accepted keys = ['short_ma', 'long_ma', 'trailing_stop_pct']；lens = [1000, 1000, 1000]
JSON body = 23037 bytes -> 22.5 KB；cartesian = 1000000000 combos -> 72.0 GB at 72B/combo
unknown keys: ACCEPTED by pydantic（稍后在 backtest.py:535 被白名单拒）
nginx: 无 client_max_body_size（=默认 1m）
compose aqp-api: mem_limit: 2g
```

### 判定依据（含修正）
- `param_search.py:81` 先 `list(itertools.product(...))`，`:84` 才比 `max_trials` ⇒ 守卫越界前已物化，**成立**；72 B/组合与 B5 的 243MB 互证。
- **修正 1（触发条件错误）**：报告举的 `150^6 ≈ 1.1e13` 通过 HTTP **不可达** —— `backtest.py:535` 用 `_STRATEGY_PARAM_KEYS` 白名单把键限到 2–3 个（ma_cross: short_ma/long_ma/trailing_stop_pct）。真实可达的样本**更小更容易**：3 键 × 1000 候选 = **22.5 KB** 请求体 → 1e9 组合 → ~72 GB。**这比报告的例子更值得写进主报告。**
- **修正 2（上游无拦截）**：`max_length=6` 只约束**键数**（实测 3 个键各 1000 值被接受）；无 body-size 中间件；nginx 默认 1m 允许约 3.3 万个浮点（3×11000 → 1e12 组合）。⇒ 影响面**不因上游而收窄**。
- **修正 3（影响措辞）**：`docker-compose.yml:67` `mem_limit: 2g` ⇒ OOM 只杀 `aqp-api` 容器（宿主与 `aqp-web` 存活；`restart: unless-stopped` 会拉起）。"打死整站"改成"打死 API 服务（容器重启风暴）"；非容器部署（无 cgroup 上限）才可能拖垮整机。

**建议最终定级：P0 维持**（单请求、极小 body、无上游拦截），示例与措辞按上修正。

---

## P0-6 空榜终态 `ok` 与契约冲突

**结论：推翻"P0 · 必须立刻修"的定级 —— 这是契约裁决项，不是 bug。**

### 独立方法
1. 分支可达性：直调 `_screen(None,'alpha_basic_v1',50,'bse')`（**真实只读数据**）与 `_finalize_screener_payload`，看 `status/reason`。
2. 上游是否抢先置 `unavailable`：读 `screening.py:90-132`（`filter_universe` 的 `ERR_DATA_EMPTY` 只在缺 `pred_score` 列 / `require_universe` 时抛）与 `_unavailable_body` 的早退分支 `screener.py:303-304`。
3. 前端消费点：`Screener/index.tsx:212-214`、`AiPicksPanel.tsx:100-103`。

### 真实输出
```
unit: {'status': 'ok', 'reason': 'no_matching_signals', 'message': '当前没有满足条件的有效信号', 'count': 0}
board=all  items=50  → finalize: status=degraded  reason=data_stale      （行情陈旧）
board=bse  items=0   → finalize: status=ok        reason=no_matching_signals
```
```tsx
// frontend/src/pages/Screener/index.tsx:213
const resultMessage = result?.message
  ?? (result?.status === 'ok' ? '当前没有满足条件的有效信号' : '选股数据暂不可用');
```

### 判定依据
- `screener.py:303-309` **顺序正确且可达**：先保留上游 `unavailable`（`_unavailable_body` 的 `status='unavailable'` 被 :303 早退保护），再判 `total==0`。`board=bse` 的实测路径是"快照无该 board 行 → `return None`（`screening.py:385-386`）→ 实时路径 → 池 0 → `total==0`" ⇒ 确实落到 `ok/no_matching_signals`。
- **前端不是"被谎报"**：两处消费点都对 `status==='ok'` 显示中文"当前没有满足条件的有效信号"，对其它状态显示"选股数据暂不可用"；且 `_finalize` 已经写死 `message` 字段。⇒ 用户可见行为无误。
- 报告自己也写了"这不是代码 bug"——**那就不能占 §2「必须立刻修」的位**。真正需要的是产品裁决（并同步改 `tests/test_data_freshness_degradation.py:111-127`，该测试断言现行为）。

**建议最终定级：P2（契约裁决，移入 §10 待确认清单）**。理由：无用户可见错误、有测试守护、前端已区分；把它留在 P0 会稀释 P0 的可信度。

---

## P1-1 非交易日沿用 `last_day_turnover` ⇒ decay 重复扣

**结论：维持（并被我独立复算加固）。**

### 独立方法
自造最小用例（**不照抄 B5 探针**）：21 个连续工作日、恒定价 10 元、`top_k=1`、`rebalance_freq="weekly"`、`signal_lag=1`、信号仅自第 3 日起给 A（使成交只发生在第 1 个周五）；开启 `BrokerConfig(enabled=True, decay_bps=10, slippage_bps=0)`。
然后**自己**按 `cost_d = turnover_row[d] × (10/10000) × equity[d-1]` 逐日累加，与 `result.friction_costs['decay']` 对比；并从 `result.trades` 独立取"真实成交日"集合。

### 真实输出
```
actual filled trade dates = ['2024-01-05']                ← 只有 1 天成交
decay total (broker.friction_costs) = 16027.7656
idx date        turnover_row   equity        mine_decay_if_charged
  4 2024-01-05  0.950000       999045.00         950.00
  5 2024-01-08  0.950000       998095.91         949.09
 ... （6..19 同形，turnover 恒 0.95）
 20 2024-01-29  0.950000       983967.23         935.66
sum of my per-day recomputation = 16027.7656  (vs actual 16027.7656)
days charged decay = 17 ; 与真实成交日交集 = 1
charged on NON-trade days = ['2024-01-08','2024-01-09',...,'2024-01-29']   ← 16 天
equity 1,000,000 → 983,967（-1.6%），sharpe=-36.8
```

### 判定依据
- `broker.py:397` 只在 `match()` 内赋值 `last_day_turnover`；`engine.py:368-371` 却**每天**无条件 `apply_decay_cost(broker.last_day_turnover, prev_equity)` ⇒ 非调仓日沿用旧值重复扣。我的手算逐日累加与 broker 记账**到分吻合**（16027.7656），互证了机制就是"陈旧值复用"。
- 量级：正确口径（仅成交日计一次）约 950 元，实扣 16027.77 元 ⇒ **约 16×**；报告所述"21 日 1 笔成交却扣 1882 元 / Sharpe −3.55→−240"是其自身参数下的同类结果（数值不必一致，机制一致）。

**建议最终定级：P1 维持**（甚至可注明"实测高估 16 倍"以增强说服力）。

---

## P1-3 模拟盘裸卖造钱

**结论：维持。**

### 独立方法
**内存 sqlite**（`create_engine('sqlite://')` + `Base.metadata.create_all`）隔离于生产库，走**真实链路**：`place_order(side='sell', order_amount=100000)` → 回填 `created_at` 到 2026-01-05（使 `run_fills` 有可成交的次日）→ `run_fills` → `account_summary`。行情用仓库只读 `daily_bar`（600000.SH）。**未对生产库写入任何数据。**

### 真实输出
```
INIT_CASH = 1000000.0
place_order -> {'ok': True, 'order_id': 1, 'decision_price': 9.06}
run_fills   -> {'orders_scanned': 1, 'fills_created': 1, 'deferred_children': 0}
  FILL side=sell qty=8400 px=11.7799 amount=98951.18 fee=79.16
after naked sell: cash=1098871.19 mv=0.00 equity=1098871.19 positions={} n_fills=1
EQUITY DELTA vs INIT_CASH = +98871.19   （positive => money created from nothing）
```
```
上层排查：place_order（paper.py:118-147）无持仓校验；run_fills（:158-248）无持仓校验；
account_summary（:334-341）remaining<=0 直接 pop 持仓（空头被静默抹掉）；
API 层 desk.py:178-194 仅 require_role("researcher") + OrderRequest(side 允许 sell, amount<=1e10)。
唯一调用点是 tests/test_production.py（且只用 buy）。
```

### 判定依据
- 缺陷确证；且**没有上层拦截**（`run_fills`/`account_summary`/API 三层逐一读过）。
- 一处措辞修正：API 参数是 `order_amount`（金额）而非股数，成交股数是 `floor(amt/px/100)*100` 推导出来的（我这次是 8400 股）。报告"无持仓卖 10000 股"应写成"无持仓卖出 10 万元（成交 8400 股）"。

**建议最终定级：P1 维持**（净值/夏普/MDD 全失真，且 `paper.py` 无单测）。

---

## P1-8 基准首值 0 ⇒ NaN/Inf ⇒ 响应渲染抛 ValueError

**结论：推翻报告结论（"该接口对该输入恒返回 `code=50000`"）。建议降为 P2 披露类。**

### 独立方法
1. **域层复现**：给 `run_portfolio_backtest` 注入自造 `price_loader`/`benchmark_loader`（首值分别设 0、NaN、正常），打印 `nav_curve/annual_returns` 的基准值。
2. **真实渲染路径**：建一个最小 FastAPI app，加 `register_error_handlers`，路由**照抄** `api/v1/portfolio.py:105` 的声明 `response_model=APIResponse[dict]` + `return ok(result)`，用 `TestClient` 打，看 http 与 body。
3. **机制对照**：同一 payload 分别走 (a) `response_model=APIResponse[dict]`、(b) 无 response_model、(c) 直接 `JSONResponse`。
4. **暴露面**：运行时枚举 114 个 `/api/v1` 路由中**没有** `response_model` 的有哪些。

### 真实输出
```
raw bm values: [nan, inf, inf, inf]      ← 域层确实产出 nan/inf
raw annual   : [nan]

(a) response_model=APIResponse[dict] → http=200 body={"code":0,"message":"ok","data":{"x":null,"y":null},...}
(b) 无 response_model               → http=200 body={"code":50000,...}
(c) 直接 JSONResponse               → http=200 body={"code":50000,...}
JSONResponse(inf) raises ValueError: Out of range float values are not JSON compliant

真实端点复刻（portfolio/backtest 声明 + first=0 的基准）：
  body contains Infinity: False | contains NaN: False
  nav_curve snippet: "nav_curve":[{"date":"2024-01-01","nav":999200.6502,"benchmark":null}, ...
  annual snippet   : "annual_returns":[{"year":"2024","portfolio":0.077984,"benchmark":null}]
  http=200 code=0

pydantic 2.9.2（APIResponse 未覆写 ser_json_inf_nan ⇒ 默认 'null'）
routes WITHOUT response_model = 4：/api/v1/notify/stream、/api/v1/export/{screener,backtest,strategy-backtest}
```

### 判定依据（推翻）
- **域层结论正确**（`portfolio.py:365-366` 在首值 0 时产出 nan/inf），但**端点不会 50000**：`response_model=APIResponse[dict]` 走 FastAPI→Pydantic v2 序列化，`ser_json_inf_nan` 默认把 `inf/nan` 写成 **`null`**，Starlette 的 `allow_nan=False` 根本看不到非合规浮点。
- 父审核员/`00-BASELINE §6` 的 `JSONResponse({'x': float('nan')}) → ValueError` 是**真结论但用错了路径**：全站只有 4 条路由没有 `response_model`（SSE 流 + 3 个导出），而它们返回的是流/文件、不产 NaN 浮点 ⇒ **"该接口对该输入恒返回 50000"在本应用中不可达**。
- **触发条件也被否证**：建议场景"基准窗口首日停牌/缺失"**不会**触发 —— `portfolio.py:201` `benchmark.reindex(union_idx).ffill().dropna()` 会丢掉首部 NaN，`benchmark.iloc[0]` 恒为首个**有效**收盘（我实测 first=NaN → `[1000000.0, 1000333.33, …]`、code=0）。只有外部源返回**字面 0**（或未来改成 `fillna(0)`）才会触发。

**建议最终定级：P2（披露/健壮性）**：真实缺陷 = "基准出现 0/异常时静默把整条 benchmark 置 `null`，无 `basis`/`degraded` 披露，前端图表基准线凭空消失"。**理由**：无 50000、无渲染异常、无 HTTP 5xx；把它写成"恒 50000"会让修的人找错方向（去查序列化，而真问题在上游不该产生 0/NaN 与缺披露）。
**同时建议修正 `00-BASELINE-AND-VERIFICATION.md §6` 的裁决表述**（直测 `JSONResponse` ≠ 端点真实渲染路径）。

---

## P1-14 ETF 资金流字段口径错、偏差 +29.4%

**结论：维持（语义+算术已独立确证；原始数值来源为单点引用，置信度标注需修正）。**

### 独立方法
1. 读同仓**同一接口**的第二处解读 `realtime.py:427-457`（列序注释）与 `etf.py:677-688`（被审代码）做交叉对照。
2. **手算恒等式**：取 B3b 落盘的那根原始 kline（`B3b-data.md:125`），自己算 `f55+f56` 与 `f52`、`f53+f54` 与 `-f52`，再算代码输出与偏差。
3. 影响面：读 `api/v1/etf.py:662-674` 的 degraded/note 逻辑。

### 真实输出
```
raw = 2026-09-21,274198704.0,-80721950.0,-193476752.0,65674576.0,208524128.0
f52 (code takes as main_in)  = 274198704.0
f53 (code takes as main_out) = -80721950.0
identity f55+f56 == f52  -> 274198704.0 == 274198704.0 : True      ← f52 = 大单+超大单 = 主力【净额】
identity f53+f54 == -f52 -> -274198702.0 vs -274198704.0 （差 2 元，源数据舍入）
code output net_inflow = f52-f53 = 354920654.0
correct (main net)     = f52     = 274198704.0
deviation = 29.4392%
```
```
api/v1/etf.py:672  status = "ok" if len(items) >= 5 else "degraded"
api/v1/etf.py:673  note   = None if len(items) >= 5 else "仅获取到最近 1 个交易日的主力净流入"
```

### 判定依据
- **口径确证**：`f55+f56 ≡ f52` 精确成立（274,198,704），说明 f52 是**净额**而非 "main_in"；`f53+f54 ≈ -f52`（差 2 元为源数据舍入）说明 f53/f54 也是净额且四类净额互补。同仓 `realtime.py:449-450` 的列序注释与之一致 ⇒ **etf.py:678 的注释是唯一异类**，代码 `f52 − f53` = 主力净额 − 小单净额，无任何业务含义。
- **偏差确证**：+29.4392%，主报告写的 **+29.4% 正确**；B3b 的"29.5%"是四舍五入笔误，建议统一为 29.4%。
- **诚实边界**：本沙箱**无外网**，我**无法独立取数**——原始 kline 来自 `B3b-data.md:125` 的落盘证据（单点引用）。因此：**语义=确定；偏差幅度=算术独立复算通过，但输入未独立取数**。建议主报告把该条置信度从"确定"细化为"口径确定 / 数值复算通过（原始响应单点引用，无外网）"。
- 影响面：报告已如实收窄（`len(items)<5` 恒 `degraded`+note），红队不升级。

**建议最终定级：P1 维持**（数值错 ~30% 且符号可错，修 1 行）。

---

## P1-22 `useChart.ts` 首个 option 永不 setOption ⇒ 图表空白

**结论：机制维持；"图表空白"的影响面被夸大，须加限定条件。**

### 独立方法
1. **源码级时序证明**（无 jsdom/无浏览器，`node_modules` 里只有 react/react-dom/esbuild）：读 `frontend/node_modules/react-dom/cjs/react-dom.development.js` 的提交与 passive-effect flush 顺序。
2. **依赖数组语义**（无需执行器即可确定）：`useEffect(fn, [option])` 在 option **引用不变**时不重跑。
3. **调用点普查**：把全仓 36 处 `useChart` 逐一按"option 是否 memo / 是否条件挂载 / 数据是否挂载后才到"分类（`Select-String` 全量列表 + 逐个读上下文）。
4. **对照实现**：读三份局部 `useChart`（useRef 版）的 deps。

### 真实输出
```
=== react-dom.development.js:26730-26739（commitRootImpl 起始）===
26730 : function commitRootImpl(root, recoverableErrors, transitions, renderPriorityLevel) {
26731 :   do {
26732 :     // `flushPassiveEffects` will call `flushSyncUpdateQueue` at the end, which
26733 :     // means `flushPassiveEffects` will sometimes result in additional
26734 :     // passive effects. So we need to keep flushing in a loop until there are
26735 :     // no more pending effects.
26738 :     flushPassiveEffects();
26739 :   } while (rootWithPendingPassiveEffects !== null);
=== react-dom.development.js:26106-26115（performSyncWorkOnRoot）===
26106 : function performSyncWorkOnRoot(root) {
26115 :   flushPassiveEffects();

=== 调用点分类（36 处）===
inline（非 memo，每次 render 新对象；不受影响）：
  FactorStudio/index.tsx:159,188,206 ｜ OrderDesk:177 ｜ CapacityAttribution:85
  SignalAnalysisPanel:39 ｜ FactorLab:71 ｜ DataQuality/index.tsx:37/71
useMemo + 条件挂载（受影响：真正空白）：
  Research/index.tsx:400 {corr && <CorrHeatmap/>}   → parts.tsx:72 useMemo([factors,matrix,highPairs])
  Research/index.tsx:416 {quantile && <QuantileChart/>} → parts.tsx:116 useMemo([curves,labels])
  Research/index.tsx:537 {optimize && <ExposureChart/>} → parts.tsx:230 useMemo([before,after])
  Research/index.tsx:635 {impact && <ImpactChart/>} → parts.tsx:256 useMemo([priceSeries,fills,side])
  parts.tsx:15-28（IC 衰减，div 只在 rows 非空时渲染；需再点因子才更新 option）
局部 useRef 版（正常）：DataCenter:52 / EtfDetail:47 / Portfolio:52
```

### 判定依据（可被推翻的推理链）
1. 挂载时 `node=null`（`useState`），`div` 的 ref 回调 `setNode(el)` 触发**第二次 commit**；
2. `commitRootImpl` 起始即 flush 上一 commit 的 passive effects（上面 26731-26739）⇒ 第一次的 `eff1`（`!node` 早退）与 `eff2`（`inst.current===null` 空转）先跑完；
3. 第二次 commit：`eff1` deps `[node]` 变了 → `echarts.init(node)`；`eff2` deps `[option]` **没变** → **不跑**；
4. ⇒ **首个 option 永不被 `setOption`**；只有 option 引用再变时才应用。
   - *注：即使不依赖第 2 步的那条 React 保证，第 3 步的依赖数组语义仍足以得出同一结论* —— 这条链比 B8 的论证更稳健。
5. **`useRef` 为何不同**：ref 回调只写 `nodeRef.current`（无 setState ⇒ 不产生额外 commit）。挂载时所有 effect **各跑一次且按声明顺序**：`eff1` 先 `init`（此时 `nodeRef.current` 已由布局阶段的 ref 回调写好），`eff2` 后 `setOption` ⇒ 首个 option 正常生效。这正是三份局部实现"看起来没问题"的原因。
6. **但"图表空白"要看结构**：inline（未 memo）option 的调用点，`setNode` 触发的第二次 render 会产生**新的 option 身份** ⇒ 该 commit 里 `eff1` 先 init、`eff2` 再 setOption ⇒ **图表正常**。真空白只发生在"**组件在数据就绪后才挂载（`{data && <Comp/>}`）且 option 用 `useMemo`、挂载后身份不变**"的路径 —— Research 页的 4 张图 + IC 衰减图即此结构（该页只在 mount 时拉一次数据、无轮询，除非用户触发重算）。

**建议最终定级：P1 维持**（修法仍是一行：deps 改 `[node, option]`），但报告里的"图表空白"应加限定："首值丢失必然发生；用户可见空白限于'条件挂载 + memo option'的 5 处（Research 页 4 图 + IC 衰减图），inline option 的 8 处调用点不受影响。" **理由**：不加限定会让"全站图表空白"被当成事实，而这是可被当场证伪的（改个筛选项就出来）。

---

## P1-23 viewer 必然 40300 的角色错配簇

**结论：维持（实测 7 个端点）；两处细节修正。**

### 独立方法
1. **运行时路由内省**：遍历 `app.routes` 的 `APIRoute`，从依赖闭包 `checker` 的 `__closure__` 取 `minimum_role`，列出所有 GET 的角色分布。
2. **ASGI 直调**（只发 GET，不起服务、不触发写）：用 `create_jwt_token('rtviewer','viewer')` 签 viewer JWT，对 10 个端点各发"匿名 / viewer"两次，打印 `code`。
3. **前端**：读 `App.tsx` 路由的 `RequireRole minimum`、逐点核对按钮是否无条件渲染、核对是否有父组件拦掉。

### 真实输出
```
Counter({'viewer': 31, 'researcher': 23, 'NONE': 6, 'authenticated': 1})   ← GET 路由角色分布

endpoint                                       anon     viewer
/api/v1/datacenter/logs                        40100    40300
/api/v1/datacenter/sync/status                 40100    40300
/api/v1/datacenter/sync/auto                   40100    40300
/api/v1/datacenter/train/status                40100    40300
/api/v1/export/screener                        40100    40300
/api/v1/desk/account                           40100    40300
/api/v1/research/overview                      40100    40300
/api/v1/settings                               40100    0
/api/v1/portfolio/search                       40100    0
/api/v1/datacenter/overview                    40100    0
```
```
App.tsx:  /data /settings /screener /portfolio /report → <RequireRole minimum="viewer">  ⇒ viewer 可进入
          /desk /backtest /research … → <RequireRole>（RequireAuth.tsx:28 default minimum='researcher'）
按钮无角色门禁：Screener:338-342 导出 ｜ Portfolio:387 回测 ｜ Report:83-87 重新生成 ｜ Settings:588-599 清缓存/备份
对照已有门禁：Settings:477 {isAdmin && (…)}（量化引擎卡）
Settings 挂载即自动测连接：index.tsx:193-206（Promise.allSettled 吞掉 403）
后端角色：/settings/connectors/test=researcher、/data/cache/clear=admin、/db/backup=admin、
          /report/daily/generate=researcher、/portfolio/backtest=researcher
```

### 判定依据（含修正）
- **错配簇确证**：viewer（`/data` 页面的合法角色）一进页就有 3 个 GET 必 40300（logs/sync/status/sync/auto 中的 3 个），`Promise.allSettled` 把它们拼成横幅（`DataCenter:426-434`），报文即 `FORBIDDEN`（`errors.py:94` 映射 detail 原文）⇒ "英文 FORBIDDEN 横幅"属实；`/screener` 导出按钮、`/portfolio` 回测按钮、`/report` 重新生成按钮、`/settings` 清缓存/备份按钮对 viewer 可见可点，后端全 40300（前两条 POST 我只能静态确证角色，未发写请求）。
- **修正 1**：报告写"1.5s 轮询持续失败"**不成立**。轮询仅在 `sync?.running` 为真时 `setInterval`（`DataCenter:441-444`），而 `sync` 来自失败的 `sync/status`，恒为 `null` ⇒ **轮询根本不启动**，不存在"持续失败"。
- **修正 2**：`/desk` 路由是 `<RequireRole>`（默认 researcher）⇒ viewer 进不去 OrderDesk，`desk/account`、`desk/orders` 等 40300 **不在 viewer 影响面内**；报告列举的 16 处应剔除 desk 相关点或明确标注"researcher 才可达"。

**建议最终定级：P1 维持**（角色错配客观存在且前端无门禁，修法=补 `hasMinimumRole` 或后端放宽只读端点）。

---

## 附：本次复核未验证/无法验证的事项（诚实边界）

| 事项 | 原因 | 置信度建议 |
|---|---|---|
| Docker 内实机启动（P0-1 的 `EROFS`） | 本机无 docker daemon | 静态链+目录模拟已足以确证；实机验证仍需 docker |
| P0-2 经 nginx/真服务的端到端登录 | 遵守"不起真服务" | 函数级直调已确证分支；网络可达性属部署面 |
| P1-14 的原始 fflow 响应 | 沙箱无外网 | 语义确定；数值=算术复算通过、输入单点引用 |
| P1-3 的 API 层端到端写请求 | 只读纪律（会写生产库） | 内存 sqlite 全链路 + 静态核对三层调用方 |
| P1-22 的真浏览器 DOM 行为 | 无 jsdom/happy-dom/react-test-renderer/浏览器 | React 源码时序 + 依赖数组语义 + 调用点普查；推理链已给出证伪条件 |