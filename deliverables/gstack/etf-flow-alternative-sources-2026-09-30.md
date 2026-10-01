# 个股「资金流向」模块空白 —— 替代数据源实测报告

**日期**：2026-09-30
**任务**：问题三 —— 个股详情页「资金流向」模块空白，寻找经实测的免费替代源 / 替代方法
**执行**：investigator3（gstack-investigator）
**环境**：AQP `backend/.venv`（Python 3.11.15、akshare 1.16.72）、curl 8.11.1
**方法**：所有结论均来自**本机实际发起的 HTTP 请求**；未实测项一律显式标注「未实测」。

---

## 📌 TL;DR

1. **复核确认**：东财 `push2` / `push2delay` / `push2his` 至今**仍全部不可达**（`http=000` / `RemoteProtocolError`）；
   `datacenter-web.eastmoney.com` 与 `quote.eastmoney.com` **可达（200）**。前一轮诊断**基本成立**。
2. **但「资金流向」模块其实是 3 个独立问题，根因各不相同**（详见 §1.3）：
   - **主力净流入** → 数据源单点（`push2his/push2delay`）+ 网络阻断 ⇒ **真·数据源故障**；
   - **北向持股** → 代码**故意**抛 `SourceRetiredError`（并非网络问题）；
   - **换手率** → **数据层能取到值**（腾讯 `turnover=0.31`），`-` 更像前端/缓存侧问题，**不是数据源问题**。
3. **最优解：不换源、只换通道（方案 A）** —— 东财 `datacenter-web` 的
   `RPT_DMSK_TS_STOCKNEW` 报表**可达且返回主力资金字段**，口径与 `push2` 完全一致
   （实测 600519：`main_net` = 5.3357 亿，与 `超大单净 + 大单净` 分毫不差）。
   **零口径风险**，是唯一能同时满足「可达 + 口径一致」的方案。
4. **方案 B（新浪）可达且有数据，但口径不一致**：同一日 600519 新浪 `netamount` = 7.41 亿 vs 东财 5.34 亿，
   **偏差约 +39%**，不可静默替换，会被当成「用兜底值冒充真实指标」。
5. **重要限制**：`RPT_DMSK_TS_STOCKNEW` **只覆盖个股、不覆盖 ETF**，且**只有最新 1 个交易日**。
   ⇒ 它能修**个股**资金流，**不能**修 ETF 资金流榜（`etf.fetch_flow`）。

---

## 1. 复核既有诊断

### 1.1 可达性矩阵（2026-09-30 本机实测，curl）

| 域名 / 通道 | HTTP | 结论 |
|---|---|---|
| `push2.eastmoney.com/api/qt/clist/get` | **000** | ❌ 阻断（与前轮一致） |
| `push2delay.eastmoney.com/api/qt/clist/get` | **000** | ❌ 阻断 |
| `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` | **000** | ❌ 阻断 |
| `datacenter-web.eastmoney.com/api/data/v1/get` | **200** | ✅ 可达 |
| `quote.eastmoney.com/` | **200** | ✅ 可达 |

> `000` = curl 连接层失败（无 HTTP 响应），与前轮 `RemoteProtocolError` 同源。
> **结论：`push2*` 服务组阻断是稳定复现的，不是暂时抖动。**

### 1.2 生产代码路径复核（直接调用 `app.data.realtime`）

```text
[FAIL] fetch_main_fund_flow: DataSourceUnavailable: 主力资金流 全部数据源失败
       [em-delay:DataSourceUnavailable; em-his:DataSourceUnavailable]
       （日志：push2delay / push2his 均 RemoteProtocolError）
[OK]   fetch_quote: {'price':1258.62, 'turnover':0.31, 'outer_vol':21633.0,
                    'inner_vol':16698.0, 'pe_ttm':19.32, 'pb':6.26, ...}
[FAIL] fetch_north_holding: SourceRetiredError: 北向持股数据源已永久下线
```

### 1.3 ⚠️ 关键更正：这不是「一个」问题，是「三个」

`fetch_main_fund_flow()` 的实现（`backend/app/data/realtime.py:455`）**只有东财两个 host**：

```python
def fetch_main_fund_flow(symbol: str) -> dict:
    return _first_source(
        [("em-delay", lambda: _fetch_em_fflow("https://push2delay.eastmoney.com", symbol)),
         ("em-his",   lambda: _fetch_em_fflow("https://push2his.eastmoney.com", symbol))],
        "主力资金流",
    )
```

两个 host 同属东财 `push2*` 服务组 → **实为单源**，整组被阻断即全灭。这解释了「主力净流入 = `-`」。

但同页另外两行**根因不同**：

| 字段 | 数据来源 | 实测状态 | 真正的根因 |
|---|---|---|---|
| 主力净流入 | `fetch_main_fund_flow`（东财 push2*） | ❌ 取不到 | **网络阻断 + 单源无兜底** |
| 北向持股 | `fetch_north_holding` | ⛔ 抛 `SourceRetiredError` | **代码故意下线**（`realtime.py:465`），非网络问题 |
| 换手率 | `fetch_quote` → 腾讯 `qt.gtimg.cn` 字段38 | ✅ **取到 0.31** | **非数据源问题**（疑前端/缓存，见 §5） |
| 内外盘 | `fetch_quote` → 腾讯字段7/8 | ✅ 取到 21633/16698 | 同上 |

**北向持股补充证据**：东财 `datacenter-web` 的 `RPT_MUTUAL_HOLDSTOCKNORTH_STA` 报表**可达**，
但 600519 最新 `TRADE_DATE` = **2026-06-30**（**季度级**，非日频）。
这印证了「日频北向持股已无公开源」——旧实现下线是合理的产品决策，
**换源也无法恢复日频北向**，只能给季度数据（若产品接受）。

---

## 2. 候选源实测表（全部为 2026-09-30 本机实发请求）

| # | 源 / 通道 | URL（关键部分） | HTTP | 有数据? | 数据结构 | 覆盖 | 口径一致性 |
|---|---|---|---|---|---|---|---|
| **A** | **东财 datacenter-web（通道内切换）** | `datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DMSK_TS_STOCKNEW` | **200** | ✅ | `PRIME_INFLOW`=主力净额(元)、`SUPERDEAL_INFLOW/OUTFLOW`、`BIGDEAL_INFLOW/OUTFLOW`、`TURNOVERRATE`、`PE_DYNAMIC`、`CHANGE_RATE` | **个股 only（ETF 返回空）**；仅最新 1 日 | ✅ **与东财主力口径一致**（600519: 5.3357亿） |
| **B** | **新浪 MoneyFlow** | `vip.stock.finance.sina.com.cn/.../MoneyFlow.ssl_qsfx_zjlrqs?...&daima=sh600519` | **200** | ✅ | `netamount`、`r0_net`、逐日历史 | 个股；有多日历史 | ❌ **不一致**（600519: 7.41亿 vs 东财 5.34亿，**+39%**） |
| B2 | 新浪 `ssl_bkzj_zjlrqs` | 同上，`ssl_bkzj_zjlrqs` | 200 | ⚠️ **返回 `[]`** | 空 | — | 无数据 |
| B3 | 新浪 `ssl_bkzj_ssggzj`（全市场） | 同上，`ssggzj` | 200 | ⚠️ 返回**其它股票**（bj430017…），`daima` 不过滤 | 全市场榜 | 个股需自行筛 | 不适用 |
| **C** | **腾讯 qt.gtimg.cn**（行情） | `qt.gtimg.cn/q=sh600519` | **200** | ✅ | `换手率`(0.31)、内外盘(21633/16698)、PE/PB/市值 | 个股+ETF | ✅ 换手率口径与东财一致 |
| C2 | 腾讯 web 资金流 | `web.ifzq.gtimg.cn/appstock/app/fundflow/get` | 200 | ❌ `"Can't load controller"` | — | — | **无资金流接口** |
| **D** | **同花顺（akshare 封装）** | `ak.stock_fund_flow_individual("即时")`（底层 `data.10jqka.com.cn`） | **200 / OK** | ✅ 5213 行全市场 | `流入资金/流出资金/净额/换手率/成交额` | 全市场（需翻 105 页，~17s） | ❌ **同花顺自有分档口径** |
| D2 | 同花顺直连 | `data.10jqka.com.cn/funds/ggzjl/...` | **403** | ❌ | 需 `hexin-v` 反爬 token | — | 需 py_mini_racer，脆弱 |
| **E** | **雪球** | `stock.xueqiu.com/v5/stock/capital/flow.json?symbol=SH600519` | **403** | ❌ | `IP Blacklisted` | — | **不可用** |
| F | 东财 `stock_individual_fund_flow_rank`（akshare） | 底层 `data.eastmoney.com` | ❌ `ServerDisconnectedError` | — | — | — | 不可用 |
| G | akshare `stock_individual_fund_flow` | 底层 `push2his` | ❌ | — | — | — | 与被阻断源同址，不可用 |
| G2 | akshare `stock_main_fund_flow` | 底层 `push2` | ❌ | — | — | — | 同上，不可用 |
| **H** | **东财 datacenter 北向** | `reportName=RPT_MUTUAL_HOLDSTOCKNORTH_STA` | **200** | ✅ 但**季度级**（600519 最新 2026-06-30） | `HOLD_SHARES/HOLD_MARKET_CAP/FREE_SHARES_RATIO` | 个股 | 口径同东财，但**频率不满足日频** |
| I | 东财 datacenter 历史资金流报表 | `RPT_DMSK_TS_STOCKHISTORY` / `RPT_DMSK_FN_MAINFLOW` | 200 | ❌ `报表配置不存在` | — | — | 无此报表 |

### 2.1 方案 A 原型实测（直接可跑的替代通道）

```python
# 实测：datacenter-web RPT_DMSK_TS_STOCKNEW 作为 fetch_main_fund_flow 的替代通道
# 返回（600519.SH, 2026-09-30）：
{"date": "2026-09-30", "main_net": 533565520,        # 元 = 5.3357亿
 "super_large_net": 412137616, "large_net": 121427904,
 "turnoverrate": 0.3066, "close": 1258.62, "pct": 1.8647,
 "source": "eastmoney-dc"}
```

**口径核验**：`SUPERDEAL_INFLOW - SUPERDEAL_OUTFLOW + BIGDEAL_INFLOW - BIGDEAL_OUTFLOW`
= 4.1214亿 + 1.2143亿 = **5.3357亿 = `PRIME_INFLOW`** ✔
与东财 `push2` 管线 `f62`（主力净额 = 超大单净 + 大单净）**定义完全一致**。

**性能/稳定性**：连续 3 次调用 `200`，耗时 **0.135s / 0.174s / 0.363s**，快且稳定。

### 2.2 方案 B 口径对比（量化偏差）

| 日期 | 东财 datacenter 主力净额 | 新浪 `netamount` | 偏差 |
|---|---|---|---|
| 2026-09-30 | **+5.3357 亿** | +7.4104 亿 | **+38.9%** |
| 2026-09-28 | （未取） | +3.3427 亿 | — |
| 2026-09-29 | （未取） | −1.2353 亿 | — |

> 新浪另有 `r0_net`（“主力/特大单”字段）= 7.9589 亿，偏差更大。
> **新浪≠东财，量级相近但数值不可互认。** 换源必然改变「主力净流入」的数值语义。

---

## 3. 最优方案建议

### 🥇 方案 A（首选）：不换源、只换通道 —— 零口径风险

**改动点**：`backend/app/data/realtime.py:455` `fetch_main_fund_flow()`

在现有 `_first_source` 列表**首位**插入一个 `datacenter-web` 通道（作为主源），
原 `push2delay` / `push2his` 降为后续兜底（一旦网络恢复仍可用）：

```python
_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"   # 已存在 realtime.py:488

def _fetch_dc_main_flow(symbol: str) -> dict:
    """东财 datacenter-web 主力资金（push2* 被阻断时的同源替代通道）。"""
    code = symbol_to_code(symbol)
    rows = _dc_get("RPT_DMSK_TS_STOCKNEW", f'(SECURITY_CODE="{code}")',
                   "TRADE_DATE", "-1", 1)
    if not rows:
        raise RuntimeError("datacenter 主力资金为空")
    x = rows[0]
    return {
        "date": str(x.get("TRADE_DATE"))[:10],
        "main_net": _to_float(x.get("PRIME_INFLOW")),                 # 元
        "super_large_net": (_to_float(x.get("SUPERDEAL_INFLOW")) or 0)
                           - (_to_float(x.get("SUPERDEAL_OUTFLOW")) or 0),
        "large_net": (_to_float(x.get("BIGDEAL_INFLOW")) or 0)
                     - (_to_float(x.get("BIGDEAL_OUTFLOW")) or 0),
        "main_net_ratio": None,      # ⚠️ 该报表无「主力净占比」，置 None（不臆造）
        "close": _to_float(x.get("CLOSE_PRICE")),
        "source": "eastmoney-dc",
    }

def fetch_main_fund_flow(symbol: str) -> dict:
    """个股主力资金净流入（多源冗余，通道内切换）。"""
    return _first_source(
        [("dc",       lambda: _fetch_dc_main_flow(symbol)),                     # ← 新增，同源同口径
         ("em-delay", lambda: _fetch_em_fflow("https://push2delay.eastmoney.com", symbol)),
         ("em-his",   lambda: _fetch_em_fflow("https://push2his.eastmoney.com", symbol))],
        "主力资金流",
    )
```

**优点**：口径 100% 与原有东财一致；可达（实测 200，<0.4s）；有界、非爬虫、无需 cookie/token。
**风险/限制（务必知悉）**：
- ⚠️ **`RPT_DMSK_TS_STOCKNEW` 不含 ETF**（510300 返回 `返回数据为空`）⇒ 只修**个股**，**不修 ETF 资金流卡**。
- ⚠️ **仅最新 1 个交易日**，无历史 ⇒ 若未来需要「5日/10日主力净流入」需另找报表（现有 `fetch_main_fund_flow` 只用最新一日，**当前够用**）。
- ⚠️ **无「主力净占比」字段** ⇒ `main_net_ratio` 只能给 `None`；前端 `RatioBar` 的 `hint`（占成交额）会不显示，但**数值行正常**。若必须保留占比，可用 `PRIME_INFLOW / (六类金额之和)` 推导——**但这是新造口径，须产品确认**，默认不建议。
- ⚠️ 报表字段名（`PRIME_INFLOW`/`SUPERDEAL_*`）为东财私有，**存在变更风险**，需加 schema 校验（缺字段即抛 `RuntimeError` 让兜底接管）。

### 🥈 方案 B（兜底源）：加新浪 —— **必须标注口径差异**
仅当方案 A 也失效时启用。**不可静默替换**：
- 数值偏差实测 **≈ +39%**（600519），语义不同；
- 若采用，API 需在 `money_flow` 块返回 `source="sina"` + `note="新浪口径，与东财主力净额不可比"`，
  前端**必须显式披露数据来源**，否则违反项目「不用兜底值冒充真实指标」红线。

### 🥉 方案 C（UX 降级）：当全部源不可用时
- 现行 `status=unavailable` + `null`（**不置 0**）是**正确**的，保留；
- 但前端 `RowPlaceholder` 只显示 `-`，用户以为是 0 / 加载失败 ⇒ 建议改为：
  「主力净流入　**数据源暂不可用**」+ tooltip 说明，并保留 `资金流 —` 的日期行。
- **北向持股**：明确是**永久下线**（非故障），文案应是「北向日频数据已停止公开」，而非加载失败样式。

---

## 4. 验证方式

1. **方案 A 单元级**：
   ```bash
   cd backend && ./.venv/Scripts/python.exe -c "
   from app.data.realtime import fetch_main_fund_flow
   print(fetch_main_fund_flow('600519.SH'))"
   # 期望：{'date':'2026-09-30','main_net':533565520,...,'source':'eastmoney-dc'}
   ```
2. **口径回归**：新增测试断言 `PRIME_INFLOW == (SUPERDEAL_INFLOW-SUPERDEAL_OUTFLOW)
   + (BIGDEAL_INFLOW-BIGDEAL_OUTFLOW)`（容器内已实测成立）。
3. **端到端**：`GET /api/v1/stock/600519.SH/panels` → `data.money_flow.status=="ok"`
   且 `main_net_yi≈5.34`（需带鉴权，本次以数据层直调代替）。
4. **多标的抽检**：000001.SZ、300750.SZ 各跑一次，确认非 600519 特例。
5. **稳定性**：连续 10 次调用，确认无 `000` / 超时。

---

## 5. 不确定 / 未验证项（明确标注）

| 项 | 状态 | 说明 |
|---|---|---|
| **换手率为何显示 `-`** | ⚠️ **未定论** | 数据层 `fetch_quote` **明确返回 `turnover=0.31`**（实测）。前端 `turnoverOk` 判据是 `status==='ok' && turnover!=null`，本应渲染。`-` 的可能原因：① 截图时后端未运行/quote 也降级；② 前端命中旧缓存；③ 该次请求 `quote` 块超时降级（`stock.py` 有 per-block timeout）。**需要一次带鉴权的实时 `/panels` 抓包才能定论**——本次因 `40100 UNAUTHORIZED` 未能取到。**建议 team-lead 从浏览器 Network 面板确认 `quote` 块实际返回值。** |
| ETF 资金流榜（`etf.fetch_flow`）替代源 | ⚠️ **未找到可用方案** | `datacenter-web` 的 `RPT_DMSK_TS_STOCKNEW` **不含 ETF**；新浪不含 ETF 资金流；同花顺接口是全市场（非 ETF 口径且重）。ETF 榜**仍无免费替代源**，只能等 `push2*` 恢复或走方案 C 降级。 |
| 方案 A 报表**长期**字段稳定性 | ⚠️ 未验证 | 仅验证了今日单次快照；`RPT_DMSK_TS_STOCKNEW` 历史字段稳定性未回溯。 |
| 方案 A 是否限流 | ⚠️ 未验证 | 仅测 3 次连续调用（均 <0.4s）。高频下的限流阈值未测。 |
| `RPT_DMSK_TS_STOCKNEW` 全市场覆盖度 | ⚠️ 未验证 | 仅验证 600519、000001；`result.pages=2600` 提示覆盖约 2600 只（**可能不含全部 A 股**），需抽检。 |
| 雪球 / 同花顺直连 | ✅ 已验证不可用 | 雪球 403 IP 封禁；同花顺直连 403 需 token。 |
| 付费源 | 未实测 | 超出「免费源」范围，未测。 |

---

## 附：一句话总结

> **前一轮「push2* 被阻断、资金流无兜底」的判断成立，但不是全貌。**
> 真正可落地的解法是**方案 A**：东财 `datacenter-web` 通道**可达且口径与 push2 完全一致**
> （实测 600519 主力净额 5.3357 亿分毫不差），**只换通道、不换源、零口径风险**，
> 但**仅覆盖个股、不含 ETF**；新浪虽可达但口径偏差 +39%，只能作**需显式披露**的兜底。
> 另外，「换手率 `-`」与「北向 `-`」**都不是网络问题**，前者数据层已能取到值（疑前端/缓存），后者是代码刻意下线。
