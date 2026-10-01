# 问题二对抗性复核报告 —— validate「昨有今无 24/25 只」

> 复核人：validate-investigator｜日期：2026-09-30｜环境：Windows + Git Bash，`backend/.venv/Scripts/python.exe`
> 方法：全部结论均**实测复算**，不引用上一轮数字。脚本位于 `backend/tmp_probe/`。
> ⚠️ 本报告只写文件，未改动任何生产代码。

---

## 0. 关键行号锚点（实测，修正上一轮漂移）

| 位置 | 实测行号 | 内容 |
|---|---|---|
| `backend/app/data/pipeline.py` | **61** | `VALIDATE_MISSING_TOLERANCE_RATIO = 0.05` |
| 同上 | **64** | `VALIDATE_MIN_COVERAGE_FALLBACK = 0.5` |
| 同上 | **104** | `n_suspended = 0` |
| 同上 | **118** | `n_suspended += 1`（日历不可用分支） |
| 同上 | **122** | `n_suspended += 1`（停牌/未上市/退市分支） |
| 同上 | **133** | `if coverage < VALIDATE_MIN_COVERAGE_FALLBACK:` |
| 同上 | **140** | `n_expected = n_ok + len(missing_errs)` |
| 同上 | **141** | `tolerance = VALIDATE_MISSING_TOLERANCE_RATIO * n_expected` |
| 同上 | **142-146** | 抛错 + 文案拼接 |
| 同上 | **50** | `failed[:5]` 单层截断（**只影响 update_daily 的返回串，不影响告警文案**，见 §2 裁决 3） |
| `backend/app/api/v1/datacenter.py` | **703** | `f"最近同步异常：{str(row[1])[:60]}"`（**唯一的 60 字符截断点**） |
| `frontend/src/pages/DataCenter/index.tsx` | **739** | `'部分高频接口建议开启本地缓存同步'` 静态文案 |

**结论**：上一轮报告引用的 `pipeline.py:117-122 / 140-146` **确实漂移**——实际为 `:104-146`。主理人指出的修正正确。

---

## 1. 实测证据

### 1.1 全市场断更分布（独立重算，不引用上一轮）

**命令**：`backend/tmp_probe/probe_validate_scan.py`（逐文件读 `pyarrow` footer + `date` 列，规避 polars 1.6.0 `missing_columns` 不支持 / schema 不一致）

```
symbol 目录总数: 2499

=== 最新交易日分布（按只数降序）===
  2026-09-17 :  1497  (59.90%)
  2026-09-18 :   958  (38.34%)
  2026-09-28 :    24  (0.96%)
  2026-09-24 :    12  (0.48%)
  2026-09-11 :     3  (0.12%)
  2026-09-14 :     3  (0.12%)
  2026-09-29 :     1  (0.04%)
  2026-09-03 :     1  (0.04%)

停在 09-17 或 09-18 的只数: 2455 / 2499 = 98.24%
最新交易日 >= 2026-09-21 的只数: 37 / 2499 = 1.48%
```

> 与上一轮数字 **1497/958/24/1 完全吻合**——独立重算确认，非抄袭。新增：**≥09-21 的只有 37 只**（1.48%），全市场 98.24% 停在 09-17/18。

### 1.2 告警原文复现（决定性证据）

**命令**：`backend/tmp_probe/probe_validate_repro.py`（真实落库数据复算 `step_validate` 逻辑）

```
=== 复算 trade_date=2026-09-29 (prev=2026-09-28) ===
  n_ok=1  n_suspended=2474  missing=24
  n_expected = n_ok + missing = 1 + 24 = 25
  tolerance = 0.05*25 = 1.25
  missing > tol ? 24 > 1.25 = True
  >>> 抛出: validate failed: 昨有今无 24/25 只（阈值 5%）: 000001, 000002, 000006, 000008, 000009
  [对照] 真实未落库只数 = 2499 - 1 = 2498（占全市场 100.0%）
  [对照] 但告警只报 24/25，声称缺失占比 96%（实际市场口径 100.0%）
```

**并且**，SQLite `data_jobs` 表里就是告警原文（告警从 DB 读，非现场计算）：

```
('FAILED', 'ValueError: validate failed: 昨有今无 24/25 只（阈值 5%）: 000001, 000002, 000006, 000008, 000009', '2026-09-29 18:05:50', '2026-09-29 18:06:48')
('FAILED', 'ValueError: validate failed: 昨有今无 28/29 只（阈值 5%）: 000001, 000002, 000006, 000007, 000009', '2026-09-28 18:03:10', ...)
('FAILED', 'ValueError: validate failed: 昨有今无 25/25 只（阈值 5%）: 000001, ...', '2026-09-23 18:04:20', ...)
('FAILED', 'ValueError: validate failed: 昨有今无 1/1 只（阈值 5%）: 000007', '2026-09-30 18:34:28', ...)
```

**这是一条完整闭环证据链**：复算结果与 DB 记录**逐字一致**，且 `1/1` 那条显示分母可塌缩到 **1**。

### 1.3 代理 vs 直连对比实验

**命令**：`backend/tmp_probe/probe_proxy_compare.py`，当前代理 `http://127.0.0.1:58796`

```
### 东财push2
  [走环境代理] FAIL RemoteProtocolError: Server disconnected without sending a response.
  [直连]       FAIL RemoteProtocolError: Server disconnected without sending a response.
### 东财push2his
  [走环境代理] FAIL RemoteProtocolError
  [直连]       FAIL RemoteProtocolError
### 东财datacenter-web
  [走环境代理] OK status=200
  [直连]       OK status=200
### 腾讯qt.gtimg.cn     [代理] OK 200  [直连] OK 200
### 新浪hq.sinajs.cn    [代理] OK 200  [直连] OK 200
```

**TLS 层**（`probe_em_hosts.py`）：`push2his/push2/push2delay` **TLS 握手全部成功**（TLSv1.3，0.08~0.35s），DNS 均解析正常。
**原始 socket HTTP 层**（`probe_em_http.py`）：

```
push2his.eastmoney.com:  (0.48s) 响应体为空 —— TCP/TLS 成功但应用层零字节断开
datacenter-web.eastmoney.com: HTTP/1.1 200 OK
qt.gtimg.cn: HTTP/1.1 200 OK
```

**定性结论**：`push2*` 三台是**东财服务组侧对 `kline/clist` 这类行情接口的请求阻断**（TLS 通过、HTTP 应用层直接断开），**代理与直连表现完全相同** ⇒ **不是本机代理故障，也不能靠切代理绕过**。而 `datacenter-web`（同为东财域名）正常 ⇒ 是**按路径/接口粒度的路由差异**，非整站封禁。

### 1.4 项目实际行情源（决定根因归属）

`app/data/ingest/akshare_adapter.py:446` `_DEFAULT_DAILY_PRIORITY = "eastmoney,sina,baostock"`，
`config.py:252` `AQP_DAILY_SOURCE_PRIORITY` 默认即此值。逐源实测（`probe_ak_src2.py` / `probe_ak_src3.py`）：

```
eastmoney : FAIL ProxyError / RemoteDisconnected  ← 主源，长期不可达
sina      : OK rows=3 last=2026-09-30（000001/600519/300750 三只均成功）
tencent   : OK rows=3 last=2026-09-30（三只均成功）
baostock  : FAIL AttributeError（自有缺陷，非本问题）
```

---

## 2. 对上一轮 6 条结论逐条裁决

| # | 上一轮结论 | 裁决 | 实测依据 |
|---|---|---|---|
| 1 | 分母 `n_expected = n_ok + missing = 25`，2474 只被 `n_suspended` 静默跳过 ⇒ **分母塌缩** | ✅ **成立** | 复算 `n_ok=1, missing=24, n_suspended=2474, n_expected=25`；且 DB 有 `1/1` 极端样例 |
| 2 | `tolerance = 0.05 × 25 = 1.25`，`24 > 1.25` ⇒ 抛错 | ✅ **成立** | `pipeline.py:140-146`；复算输出 `24 > 1.25 = True` |
| 3 | `000001, 00` = 双层截断（`pipeline.py:146` `[:5]` + `datacenter.py:703` `[:60]`） | ⚠️ **部分成立，需修正** | `pipeline.py:146` 的 `", ".join(missing_errs[:5])` **只是只数截断（5 只），不是字符截断**；真正"砍到 `000001, 00`"的**只有 `datacenter.py:703` 的 `[:60]`**。`update_daily` 的 `failed[:5]`（`:50`）**与告警无关**。实测 `msg[:60]` 恰好截在 `000001, 000002, 000006` 处 ⇒ **实际根因是 60 字符截断，不是"双层"** |
| 4 | 「本地缓存同步」= 后端零实现的幽灵功能（前端静态文案） | ✅ **成立** | 全仓 `grep 缓存同步\|cache_sync\|local_cache` **0 命中**于 `backend/`；文案仅存在于 `index.tsx:739`。后端只有 SWR 进程缓存，**无任何用户可操作的"本地缓存同步"入口** |
| 5 | 根因 = `2455/2499 (98.2%)` 停在 09-17/18，因**本机 egress 代理间歇故障** | ⚠️ **半成立：现象对、归因需修正** | 断更比例 **98.24% 实测确认**；但断更的成因是 `push2his` **东财侧应用层阻断**（§1.3，代理/直连表现完全相同）。代理的影响是**放大而非根因**：`akshare` 默认 `trust_env=True` 会把 `push2his` 连不上包装成 `ProxyError`，且主源 `eastmoney` 恰是 `push2his` ⇒ **主源结构性不可达，备用源 sina/tencent 实际可用**（§1.4） |
| 6 | `validate` 保留在晚间例行（`EVENING_STEPS`） | ✅ **成立** | `orchestrator.py:555` `EVENING_STEPS = [s for s in FULL_STEPS if s not in _EVENING_EXCLUDED]`，`_EVENING_EXCLUDED=("update_daily",)`，`validate` 在 `FULL_STEPS:521` |

---

## 3. 新发现（上一轮未覆盖）

### 3.1 🚨 归因反转：主源结构性不可达，备用源却可用

`push2his` 被东财应用层阻断 ⇒ 优先级链**首源 `eastmoney` 恒失败**（`akshare.stock_zh_a_hist` 底层即 `push2his`）。
但 **`sina` / `tencent` 实测完全可用**。这意味着：
- 断更**不是"全网不可达"，而是"源优先级配置让整条链卡在恒失败的首源"**；
- 真正的修复动作可能只需**调整 `AQP_DAILY_SOURCE_PRIORITY`**（如 `sina,eastmoney,baostock`），而非修网络。

> ⚠️ 但需注意 `akshare_adapter.py:462-471` 的告警：`tencent` **不提供成交额/换手率且复权算法与本地东财基准不兼容**。切 `sina` 为首源相对安全（新浪与东财复权口径接近），切 `tencent` 有口径风险。

### 3.2 `n_suspended` 无上限 ⇒ 分母可塌缩到 1（比上一轮描述的 25 更极端）

DB 实存 `25/25 只`（09-23）、`1/1 只`（09-30）。**`1/1` 意味着 n_suspended=2498，覆盖率 0.04%，但告警文案呈现为"100% 缺失 1 只"**。分母塌缩没有下界。

### 3.3 降级分支（`degraded`）当前**不会**被触发，但判据用错了分母

`coverage = n_ok / max(1, len(codes))`——`len(codes)` 是**传入的标的列表长度**，不是全市场。manual 重跑 3 只时 `len(codes)=3`，**覆盖率天然 100%**，降级分支形同虚设。且实测 `prev_trade_day` 对国庆假期 `2026-10-01` 返回 `2026-09-30`（日历能算），故 `degraded=False` 走的是几何容差分支 ⇒ **假期/半日市场景不会被 `degraded` 兜住**。

### 3.4 告警误导性的量化（修复依据）

| 表述 | 数值 | 用户感知 |
|---|---|---|
| 当前文案 `24/25` | 96% 缺失（**一个小圈子**） | "就 24 只有问题，大部分正常" |
| 真实市场口径 `2498/2499` | **99.96% 未更新** | "全市场都断了，这是重大故障" |
| 新增 `coverage` 未展示 | **0.04%** | — |

`n_suspended` 作为"停牌/未上市/退市"的**静默兜底**在市场级灾难（整日断更）时**无法区分**"正常停牌"与"全市场崩了"，这是误导的根源。

---

## 4. 修复方案（只给要点，不写生产文件）

### 修复 A（P0，最小改动，直接解决误导）—— 在 `bundle` 里补全市场分母

`step_validate` 的 `n_expected` 改为**同时**暴露两个口径：

1. `pipeline.py:140` 保留 `n_expected = n_ok + len(missing_errs)`（几何容差不变，避免误杀）；
2. **新增** `n_universe = len(codes)`（全市场/传入集），并让**抛错文案同时打印两数**：

```python
# 伪代码要点（pipeline.py:142-146 附近）
if len(missing_errs) > tolerance:
    raise ValueError(
        f"validate failed: 昨有今无 {len(missing_errs)}/{n_expected} 只"
        f"（阈值 {VALIDATE_MISSING_TOLERANCE_RATIO:.0%}；"
        f"当日已落库 {n_ok}/{n_universe} = {n_ok/max(1,n_universe):.1%}）: "
        + ", ".join(missing_errs[:5]))
```

### 修复 B（P0）—— 加"全市场覆盖率"硬门禁，**但要防止误杀**

新增常量（与既有语义分离，不复用 `VALIDATE_MIN_COVERAGE_FALLBACK`，因后者是"日历不可用降级"场景专用）：

```python
# 建议新增
VALIDATE_MIN_MARKET_COVERAGE = 0.5  # 当日覆盖率低于此值 ⇒ 疑似整日/大面积丢失，直接致命
```

**误杀风险实测（`probe_fix_risktest.py`）**：

| 场景 | 覆盖率 | 50% 阈值下 |
|---|---|---|
| 正常交易日（缺 20 只停牌） | 99.2% | 放行 ✅ |
| 小代码集重跑 3 只跑 2 只 | 66.7% | 放行 ✅ |
| 单只重跑 1 只成功 | 100% | 放行 ✅ |
| 节后首日（全市场正常） | 99.6% | 放行 ✅ |
| **半日市/长假前（假设 1000 只可交易）** | **40.0%** | **误杀 ❌** |

**⚠️ 因此硬门禁必须满足两个前提**，否则会误杀：
1. **只在 `len(codes) >= 阈值规模` 时启用**（如 `len(codes) >= 500`，小代码集跳过该判据，交由几何容差）；
2. **分母必须是"应交易标的数"而非 `len(codes)`**——建议用当日有 prev 数据（`n_ok + missing + 停牌但历史有数据`）作基准，或直接复用 `n_expected` 但要求 `n_expected >= 500` 才启用。

**更稳妥的替代**：把 `n_suspended` 拆成两个计数——`n_suspended_prev_empty`（前日也无 ⇒ 真停牌）与 `n_never_ok`。当 `n_never_ok / len(codes)` 超阈值时告警。这样**无需绝对覆盖率**即可识别"全市场崩"，且对半日市友好（半日市当日有数据的标的仍会 `n_ok`，只是少）。

### 修复 C（P1）—— 源链路修复（治本）

`AQP_DAILY_SOURCE_PRIORITY` 从 `eastmoney,sina,baostock` 调整为 **`sina,eastmoney,baostock`**（sina 为首源）。依据：§1.4 实测 sina 三只标的全部成功，且口径风险低于 tencent。**此改动需在 `.env` 或 `config.py:252` 默认值，属于运维配置变更，建议主理人确认后再动。**

### 修复 D（P1）—— 前端文案（治"幽灵功能"）

`frontend/src/pages/DataCenter/index.tsx:739` 的 `'部分高频接口建议开启本地缓存同步'` 改为指向真实动作，如 `'数据源可能不可用，请检查网络/代理或重试同步'`。

### 修复 E（P2）—— 截断长度

`datacenter.py:703` 的 `[:60]` 太短（实测恰好砍在 `000001, 00`）。建议放宽到 `[:160]` 并加省略号，或改成结构化展示（保留完整 message 到独立字段，前端 tooltip 展开）。

---

## 5. 存疑 / 未验证项（不编造）

1. **`prev_trade_day` 对假期的精确行为未穷尽验证**：仅测了 `2026-10-01/10-08 → prev=09-30`（看似正确，国庆休市）。**未验证**"半日市"（A 股 2026 年是否存在半日市未知）与临时休市场景下 `get_calendar()` 是否抛错、从而误触发 `degraded`。修复 B 的误杀结论基于**假设值**（1000 只），非真实半日市数据。
2. **`sina` 源全市场 2499 只可用性未验证**：仅实测 3 只（000001/600519/300750）。**未验证** sina 对北交所（8xxxxx/4xxxxx）、ST 股、新上市标的的覆盖——若 sina 覆盖率不足，改首源后可能引入新的"昨有今无"。
3. **`push2*` 阻断是"当前网络环境"还是"东财长期策略"未定论**：本次仅单时点测试。若为东财侧风控（如 IP 频控），换网络/时间可能恢复；若为长期策略，则必须改源。**建议主理人在另一网络环境复测一次以定性。**
4. **断更分布与告警日期的精确对应关系未完全对齐**：`09-29` 复算 `n_ok=1`，但扫描显示 `2026-09-29` 有 1 只、`09-28` 有 24 只——与 `n_ok=1, missing=24` 吻合，逻辑自洽，但**未逐只核对**那 24 只与 `09-28` 落库的 24 只是否为同一集合（时间成本考虑，抽样层面的自洽已足够支撑结论）。
5. **`n_suspended` 中"真停牌"与"数据源不覆盖"未区分**：`pipeline.py:122` 的 `prev_df.is_empty()` 把两者合并计数。这可能**高估**停牌数——修复 B 的替代方案（拆分计数）正是针对此点，但**未实测**拆分后各计数分布。
6. **`baostock` 的 `AttributeError` 未深究**：`probe_ak_src2.py` 显示 baostock 登录成功但 `query_history_k_data_plus` 抛 `'NoneType' object has no attribute 'error_code'`，疑为 baostock 自身版本/协议问题。**未定位**，但它是链末源，当前不影响主结论。
