# 个股「资金流向」四字段 —— 数据源分工、接线状态与 git 取证

**日期**：2026-09-30
**任务**：team-lead 追问（问题三续）—— 逐字段坐实分工 / 接线状态 / git 历史 / 行情实测
**执行**：investigator3（gstack-investigator）
**说明**：本环境 Bash/PowerShell 的 **stdout 捕获已失效**（`echo` 亦返回空），全部证据通过
「**Python 子进程 → 写入文件 → Read**」取证；git 二进制不在 PATH（`D:\Git\cmd\git.exe`），同样经 Python subprocess 调用。

---

## 📌 TL;DR

1. **资金流向 4 个字段，来自 3 条完全独立的链路**，且各自命运不同：

| 字段 | 提供函数（当前） | 接线状态 | 实测结果（600519.SH, 2026-09-30） |
|---|---|---|---|
| **换手率** | `realtime.fetch_quote` → 腾讯字段38 | ✅ 已接线，**通** | ✅ `0.31`（腾讯通） |
| **内外盘** | `realtime.fetch_quote` → 腾讯字段7/8 | ✅ 已接线，**通** | ✅ `外盘56.44% / 内盘43.56%` |
| **主力净流入** | `realtime.fetch_main_fund_flow` → 东财 push2\* | ✅ 已接线，**断** | ❌ `DataSourceUnavailable`（push2\* 阻断） |
| **北向持股** | `realtime.fetch_north_holding` | ✅ 已接线，**代码主动抛异常** | ⛔ `SourceRetiredError`（永久下线，非网络问题） |

2. **"没接的两个源"的准确表述**（这是 team-lead 问的核心）：
   - **「同花顺 stock_fund_flow_individual」作为主力资金兜底 —— 从来只写在注释里，代码从未调用过**（**"从没接线"**）。
     证据：远端基线 `f7c9410`（≤2026-09-20）的 docstring 就声称 `主力净流入：… -> 同花顺 stock_fund_flow_individual`，
     但其 `fetch_main_fund_flow` **函数体只有两个东财 host**，与今天**逐字相同** ⇒ 是**文档虚构的兜底**。
   - **「北向持股」——曾经实现过（旧 AKShare 全量接口），后被代码主动摘除**（**"接线后被摘掉"**），
     改为抛 `SourceRetiredError`。基线与当前**均已是抛异常版**（该摘除早于 2026-09-20）。
   - **「东财 push2delay 行情快照兜底」——曾经接线，后被摘除**（**"接线后被摘掉"**）。
     基线 `fetch_quote = 腾讯 → 东财(push2delay/_fetch_em_quote)`；当前 `= 腾讯 → 新浪`，
     `_fetch_em_quote` 在现网 `app/` 中**零引用**（全仓搜索确认），已被整体删除。

3. **换手率/内外盘实测：通** —— 腾讯 `qt.gtimg.cn` 4 标的全通、连续 5/5 成功，字段齐全。
   ⇒ **"修一下就能亮"**（数据层已能取到 0.31）。但**兜底链有盲区**：一旦腾讯挂，兜底的新浪
   `fetch_sina_quote` **明确返回 `turnover=None`** ⇒ 换手率必然变 `-`。这是「能亮」与「有隐患」并存的真相。

4. **git 取证受限但已突破**：2026-09-23~09-29 的 23 个提交**对象永久丢失**（第三次 `.git` 被清空），
   无法 `git log -p` 比对其间改动；但**远端基线 `f7c9410`（33 提交，09-14~09-20）已 fetch 回本地、可 checkout**，
   足以判定"改动**之前**长什么样"。**接线状态结论因此有据可依**（见 §3）。

---

## 1. 逐字段分工表（含调用点与零调用点）

### 1.1 调用链（全部已接线）

```
GET /api/v1/stock/{symbol}/panels          # backend/app/api/v1/stock.py:256
  └─ jobs["quote"]       → _cached_block → panels.build_quote(symbol)        # panels.py:60
  │     └─ realtime.fetch_quote(symbol)                                     # realtime.py:256
  │           ├─ fetch_tencent_quote   → 腾讯 qt.gtimg.cn（字段38换手/7-8内外盘）
  │           └─ fetch_sina_quote      → 新浪 hq.sinajs.cn（turnover 恒 None）
  ├─ jobs["money_flow"]  → panels.build_money_flow(symbol)                   # panels.py:83
  │     └─ realtime.fetch_main_fund_flow(symbol)                            # realtime.py:455
  │           ├─ _fetch_em_fflow("https://push2delay.eastmoney.com")        # realtime.py:421
  │           └─ _fetch_em_fflow("https://push2his.eastmoney.com")
  └─ jobs["north"]       → panels.build_north(symbol)                        # panels.py:106
        └─ realtime.fetch_north_holding(symbol)                             # realtime.py:465
              └─ raise SourceRetiredError                                   # 主动下线
```

### 1.2 每个函数的调用点计数（全仓 `*.py` 搜索）

| 函数 | 定义 | 生产调用点 | 测试调用点 | 判定 |
|---|---|---|---|---|
| `fetch_quote` | realtime.py:256 | `panels.build_quote:62`、`build_fundamentals:157` | 有 | ✅ 已接线 |
| `fetch_tencent_quote` | realtime.py:181 | 经 `fetch_quote` | 有 | ✅ 已接线 |
| `fetch_sina_quote` | realtime.py:227 | 经 `fetch_quote`（兜底） | 有 | ✅ 已接线 |
| `fetch_main_fund_flow` | realtime.py:455 | `panels.build_money_flow:85`；`watchlist` 另走 `_fund_flow_one_fast` | 有 | ✅ 已接线 |
| `fetch_north_holding` | realtime.py:465 | `panels.build_north:118` | 有 | ✅ 已接线（但抛异常） |
| **`_fetch_em_quote`** | **基线 f7c9410 有** | **0（现网 app/ 无定义、无引用）** | 0 | ❌ **已删除**（接线后被摘掉） |
| `fetch_etf_flow_history` | etf.py:999 | `api/v1/etf.py:1139` | 有 | ✅ 已接线（ETF 详情，非本题） |
| `_fund_flow_one_fast` | watchlist.py:220 | `watchlist._fund_flow_total:256` | 有 | ✅ 已接线（**第 3 份 push2delay 单源**） |
| `market_service.build_money_flow` | market_service.py:16 | `api/v1/market.py:566` | 有 | ✅ 已接线（**市场概览页**，非个股页） |

> **零调用点结论**：**仅 `_fetch_em_quote` 是零引用（已被删除）**。
> 不存在"某个资金流函数定义了但完全没被任何地方调用"的情况——四字段的链路都是**接通的**。

---

## 2. 「三个数据源混在一起」的坐实

资金流向卡在**同一个 UI 卡片**里并排显示 4 行，但它们**不是同一数据源**：

| 行 | UI 判据（`StockDetail/index.tsx`） | 数据块 | 数据源域 | 当前状态 |
|---|---|---|---|---|
| 主力净流入 | `mainOk = money.status==='ok' && main_net_yi!=null` (L377) | `panels.money_flow` | 东财 push2\* | ❌ 断 |
| 北向持股 | `northOk = north.status==='ok' && pct_of_float!=null` (L378) | `panels.north` | 旧 AKShare（已下线） | ⛔ 主动下线 |
| 换手率 | `turnoverOk = quote.status==='ok' && turnover!=null` (L379) | `panels.quote` | 腾讯 qt.gtimg.cn | ✅ 通 |
| 内外盘 | `splitOk = quote.status==='ok' && outer_ratio!=null` (L380) | `panels.quote` | 腾讯 qt.gtimg.cn | ✅ 通 |

⇒ **同一张卡 = 3 个独立数据源**，此前"整块空白=单一数据源阻断"的认知**确实需要更正**：
只有 **主力净流入** 是"服务组被阻"；北向是"产品主动下线"；换手率/内外盘是"腾讯行情，**现在是通的**"。

---

## 3. git 历史取证（"从没实现 / 实现了没接线 / 接线后被摘掉"）

### 3.1 取证方法与环境约束

- `git` 不在 PATH；实际二进制在 **`D:\Git\cmd\git.exe`**（version 2.52.0.windows.1）。
- 经 venv Python `subprocess` 调用成功（rc=0），输出落盘取证。
- ⚠️ **约束**：2026-09-23~09-29 的 23 个提交**对象永久丢失**（`.git` 第三次被外部进程清空，
  见 `git-forensics-readonly-2026-09-30.md`）⇒ **无法对这段做 `git log -p` 逐行比对**。
- ✅ **可用**：远端基线 `f7c9410`（33 提交，2026-09-14→09-20）已 fetch 回并可 `git show`。

### 3.2 `realtime.py` 的提交可见历史（`git log --oneline --all -- realtime.py`）

```
475fde9 recover(git): 恢复 master 至远端真实基线 f7c9410 并提交当前工作区   # 本次恢复
c453025 fix(quant): 修复同步完整性/选股信号/调度隔离等 7 项缺陷与 2 项回归
a9c1905 feat(auth): 登录页新增自助注册（后端 /auth/register + 前端注册 Tab）
```
> 归属 09-23~09-29 的 23 个提交（含 09-26 的"东财降级容错——接入腾讯/新浪"）**不在此列表**，
> 其对象已丢失 ⇒ 这段的逐行 diff **不可考**（**未实测，仅可依据当前代码注释与基线快照推断**）。

### 3.3 基线 `f7c9410`（≤2026-09-20）快照 —— 决定性证据

**（a）主力资金兜底：注释声称有同花顺，函数体却没有**

基线 docstring（`git show f7c9410:backend/app/data/realtime.py`）：
```
- 内外盘 / 换手率 / 总市值 / 流通市值：腾讯 qt.gtimg.cn   -> 东财 push2delay
- 主力净流入：东财 push2delay fflow -> push2his fflow     -> 同花顺 stock_fund_flow_individual
```
基线函数体（同处）：
```python
def fetch_main_fund_flow(symbol: str) -> dict:
    return _first_source(
        [("em-delay", lambda: _fetch_em_fflow("https://push2delay.eastmoney.com", symbol)),
         ("em-his",   lambda: _fetch_em_fflow("https://push2his.eastmoney.com", symbol))],
        "主力资金流")
```
⇒ **docstring 写着的"同花顺"第三源，函数体里根本不存在**。与当前代码**逐字相同**。
当前 docstring 已**如实更正**（`realtime.py:12-14`）："…原先注释所称的「同花顺 stock_fund_flow_individual」
**从未接线**，2026-09-26 如实更正"。
**判定：同花顺兜底 = 从没接线（仅注释虚构）。**

**（b）北向持股：基线与当前都已是"主动抛异常"**

`git show f7c9410:.../realtime.py` 中 `fetch_north_holding` 与当前**同样**是
`raise SourceRetiredError("北向持股数据源已永久下线（无有界数据源）")`，
且注释同样提到"旧实现调用 AKShare 全量接口且无网络超时，单块取消后线程仍持续扫描"。
**判定：北向 = 接线后被（早于 09-20）摘掉，改为显式下线。**

**（c）实时快照的东财兜底：**接线后被摘掉****

| 版本 | `fetch_quote` 兜底链 | `_fetch_em_quote` |
|---|---|---|
| 基线 `f7c9410`（≤09-20） | `tencent → eastmoney(push2delay)` | **存在**（realtime.py 内定义了 `_fetch_em_quote`，用 `f168=换手率`） |
| 当前 | `tencent → sina` | **已删除**（现网 `app/` 全仓零引用） |

当前 docstring 明说："东财 push2delay 备源已移除"。
**判定：东财快照兜底 = 接线后被摘掉（在 push2\* 被阻断后移除），改用新浪兜底。**

### 3.4 接线状态总判（逐源）

| 源 | 判定 | 依据 |
|---|---|---|
| 同花顺 `stock_fund_flow_individual`（主力资金兜底） | **从没实现/从没接线** | 基线 docstring 有、函数体无；现网注释自认"从未接线" |
| 北向旧 AKShare 源 | **接线后被摘掉** | 基线已是 `raise SourceRetiredError` + 注释述旧实现 |
| 东财 push2delay 快照兜底 | **接线后被摘掉** | 基线有 `_fetch_em_quote`，现网零引用、注释"已移除" |
| 腾讯/新浪行情（换手率、内外盘） | **已接线且当前通** | 见 §4 实测 |

---

## 4. 换手率 / 内外盘 行情实测（问题 3）

通过**生产代码** `realtime.fetch_quote` / `fetch_tencent_quote` / `fetch_sina_quote` 实调：

```
[fetch_quote OK] 600519.SH source=tencent turnover=0.31 pe=19.32 outer=21633.0 inner=16698.0
[fetch_quote OK] 000001.SZ source=tencent turnover=0.54 pe=5.17  outer=673452.0 inner=371906.0
[fetch_quote OK] 300750.SZ source=tencent turnover=0.7  pe=15.85 outer=169839.0 inner=127156.0
[fetch_quote OK] 510300.SH source=tencent turnover=2.05 pe=None  outer=2473683.0 inner=2484861.0
[tencent stress] 5/5 成功
[sina OK] 600519.SH turnover=None pe=None price=1258.62     ← 新浪不提供换手率
[sina OK] 000001.SZ turnover=None pe=None price=11.57
```

**结论**：
- **腾讯 `qt.gtimg.cn` 通**，换手率/内外盘/PE 齐全 ⇒ **"修一下就能亮"**（数据层已能取到换手率 0.31）。
- **兜底盲区**：`fetch_quote` 兜底链为 `腾讯→新浪`，**新浪 `turnover=None`** ⇒ 腾讯一旦挂，换手率必变 `-`。
- 对比 `push2*`（`000`/阻断）与 `datacenter-web`（`200`）：**行情组（腾讯/新浪）通，东财 push2 组不通**。

### 4.1 组合层实测（panels，最接近真实响应）

生产 `panels.build_*` 直接调用（600519.SH）：
```
### build_quote       {"status":"ok","turnover":0.31,"outer_ratio":56.44,"inner_ratio":43.56,"source":"tencent"}
### build_money_flow  [EXC] DataSourceUnavailable: 主力资金流 全部数据源失败 [em-delay; em-his]
### build_north       {"status":"unavailable","reason":"data_source_retired","retired":true,...}
```
⇒ **换手率/内外盘在组合层是 `ok` 的**，**应在 UI 显示**；只有主力净流入/北向为 `-`。

### 4.2 ⚠️ 关于"截图上换手率也是 `-`"的解释（未定论，需前端抓包）

数据层换手率明确为 `ok(0.31)`，理论上 UI 应显示。截图却显示 `-`，**可能原因（未实测验证）**：
1. **单块超时降级**：`stock.py:303` 每块 `_PANEL_BLOCK_TIMEOUT=4.5s`；若某次 `quote` 块恰好超时，
   会被改写为 `unavailable` ⇒ 换手率 `-`（而其他块若也降级，`anyOk` 仍可能为真，故仍显示 `-` 行而非整卡空）。
2. **前端缓存/SWR 旧值**：展示了更早（腾讯也不通时）的降级快照。
3. **截图时后端未运行**：实测当前 **:8000/8011…8015 全部无法连接**（见 §5），
   若截图时后端不可用，前端拿到的是错误/空 → 全 `-`。
⇒ **建议以一次带鉴权的 `/panels` 实时抓包定论**（本次因后端未运行 + 鉴权，未能端到端复现）。

---

## 5. 环境侧实测补充

- **后端未运行**：socket 探测 `127.0.0.1:8000/8011/8012/8013/8014/8015` **全部连接超时**；
  唯一开放端口为沙箱代理 `127.0.0.1:63978`。
- **出网经沙箱代理**：`HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:63978`。
  ⚠️ 因此**对外部源的连通性判定，必须以"经该代理的真实 TLS 请求结果"为准**（本报告 §4 采用生产 httpx 实调，可信）。
- **Bash/PowerShell stdout 失效**：本会话 `echo hello` 亦返回空；所有取证改为「Python→写文件→Read」。
  这本身是一条**环境事故线索**（工具输出管道异常），建议 team-lead 知悉。

---

## 6. 结论与建议（对 team-lead 四问的直答）

1. **逐字段分工/调用点**：见 §1。四字段链路**均已接线**；唯一"零调用点"的是已被删除的 `_fetch_em_quote`。
2. **"没接"的性质**：
   - 同花顺主力资金兜底 = **从没接线**（注释虚构，git 基线可证）；
   - 北向旧源 = **接线后被摘掉**（改为 `SourceRetiredError`）；
   - 东财快照兜底 = **接线后被摘掉**（`_fetch_em_quote` 已删除，改新浪）。
3. **行情实测**：腾讯/新浪**均通**，换手率/内外盘**能亮**；但**兜底新浪无换手率**，是隐患。
4. **替代源**：延续 `etf-flow-alternative-sources-2026-09-30.md`——
   最优仍是**方案 A**（东财 `datacenter-web` 的 `RPT_DMSK_TS_STOCKNEW`，同源同口径，仅个股、仅最新 1 日）。

### 建议动作
- **主力净流入**：按方案 A 在 `realtime.py:455` 插入 `datacenter-web` 通道（零口径风险）。
- **换手率兜底**：`fetch_sina_quote` 补不回换手率；若要在腾讯挂时仍显换手率，
  可用东财 `datacenter-web`（`RPT_DMSK_TS_STOCKNEW.TURNOVERRATE` 实测 = 0.3066%，与腾讯 0.31 一致）作为
  **换手率的第二源**——这是笔"顺带修"的收益（**但需产品确认该通道作为行情源**）。
- **北向**：维持"永久下线"语义，仅改 UI 文案（"北向日频数据已停止公开"）。

---

## 7. 不确定 / 未实测项

| 项 | 状态 |
|---|---|
| 截图上"换手率 `-`"的确切原因 | ⚠️ **未定论**（后端未运行+鉴权，未能端到端抓包）；已给 3 个候选假设 |
| 09-23~09-29 的 23 个提交逐行 diff | ⚠️ **不可考**（对象永久丢失）；结论基于基线快照 + 当前注释推断 |
| `RPT_DMSK_TS_STOCKNEW` 作为**换手率**第二源的长期稳定性/限流 | ⚠️ **未实测**（仅单次快照验证 0.3066%） |
| 各外部源经**沙箱代理**的长期稳定性 | ⚠️ 未验证（仅即时单次/5 次压测） |

---

## 附：一句话总结

> **资金流向卡的 4 行来自 3 条独立链路：换手率/内外盘（腾讯，通）、主力净流入（东财 push2，断）、北向（代码主动下线）。**
> 代码上**没有"定义了却没调用"的资金流函数**——唯一"没接"的同花顺兜底是**注释虚构、从没接线**（git 基线可证），
> 而东财行情兜底与北向旧源则是**接线后被摘掉**。换手率数据层已能取到（0.31），**修/连通即可亮**，但兜底新浪无换手率是隐患。
