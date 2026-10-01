# 问题三复核报告：个股「资金流向」卡片空白 —— 免费替代源穷举与方案

> 调查员：flow-investigator　|　任务 #2　|　日期：2026-09-30
> 项目：`D:\Python_Project\Alpha Quant Platform`
> 探针：`backend/tmp_probe/probe_flow_sources.py` / `probe_flow_detail.py` /
> `probe_etf_north.py` / `probe_sina_etf.py` / `probe_hosts.py` /
> `probe_push2test.py` / `probe_push2test2.py`（结果 JSON：`probe_flow_result.json`）
> 方法：每个源在 **走代理（默认 httpx 读 HTTP_PROXY=http://127.0.0.1:58796）** 与
> **`trust_env=False` 直连** 两种模式下分别实测。

---

## ★ 摘要（一句话）

**上一轮「ETF 资金流无解」「只有东财单源且整组阻断」的结论被推翻**：
本次实测发现 **`push2test.eastmoney.com` 可达（代理+直连均可），返回完整日频资金流
（103 个交易日）、口径与东财报表 `RPT_DMSK_TS_STOCKNEW` 完全一致（差 0.00）、
且覆盖 5562 只 A 股 + 1363 只 ETF**。改为该主机即可**几乎零改动**修复「主力净流入」
与「ETF 资金流」。北向持股的 `datacenter-web` 报表本身可达（仅季频）。

---

## ① 候选源实测矩阵

图例：✅ 可达且有真实数据；⚠️ 可达但口径/频率受限；❌ 不可达或空。

| # | 源 | URL / report | 代理 | 直连 | 字段 | 口径 | 频率 | 覆盖 |
|---|---|---|---|---|---|---|---|---|
| **1** | **东财 `push2test` fflow/daykline** | `https://push2test.eastmoney.com/api/qt/stock/fflow/daykline/get` | ✅ | ✅ | 主力/超大/大/中/小 净额 + 净占比 + 收盘 | **与 push2 完全一致** | **日频（103 日）** | A股 + **ETF** |
| 2 | 东财 `push2test` clist 榜 | `…/api/qt/clist/get` `fs=b:MK0021,b:MK0022` | ✅ | ✅ | f12/f14/f62/f164/f174 | 主力净额 | 日频 | **ETF 1363 只 + A股 5562 只** |
| 3 | 东财 datacenter `RPT_DMSK_TS_STOCKNEW` | `datacenter-web…/api/data/v1/get` | ✅ | ✅ | PRIME_INFLOW / SUPERDEAL / BIGDEAL / TURNOVERRATE | 与 push2 一致 | **仅最新 1 日（count=1）** | 仅个股，**不含 ETF** |
| 4 | 东财旧 `push2` / `push2delay` / `push2his` | `push2*.eastmoney.com/api/qt/stock/fflow/…` | ❌ | ❌ | — | — | — | **整组 RemoteProtocolError** |
| 5 | 东财 `push2ex` | — | ❌ 404 | ❌ 404 | — | — | — | 无 |
| 6 | 新浪 `MoneyFlow.ssl_qsfx_zjlrqs`（个股/ETF） | `vip.stock.finance.sina.com.cn/…/ssl_qsfx_zjlrqs` | ✅ | ✅ | netamount / r0_net / turnover | **主力口径偏差 +38.9%** | 日频 | **个股 + ETF（15/15 覆盖）** |
| 7 | 新浪 `ssl_bkzj_bk`（板块） | 同上 | ⚠️ | ⚠️ | Input error | — | — | 需正确参数，本次未通 |
| 8 | 新浪 `ssl_dpzj_dpzj` / `ssl_hgtzjl` | 同上 | ❌ Service not found | ❌ | — | — | — | 服务已下线 |
| 9 | 同花顺 HTML 页 `data.10jqka.com.cn/funds/ggzjl/` | 页面 | ✅ 200 | ✅ 200 | HTML（需解析） | — | 日频 | 需 JS 渲染 |
| 10 | 同花顺 ajax `…/ggzjl/…/ajax/1/free/1/` | 同上 | ❌ **401** | ❌ **401** | — | — | — | **需 hexin-v 反爬 token** |
| 11 | 同花顺 `d.10jqka.com.cn/v6/line/hs_600519/01/last.js` | 行情（非资金流） | ✅ | ✅ | 日线行情 | 行情 | 日频 | A股 |
| 12 | 腾讯 `qt.gtimg.cn` | 行情快照 | ✅ | ✅ | 换手率/内外盘/市值/**无资金流** | — | 实时 | A股+ETF |
| 13 | 腾讯 `web.ifzq.gtimg.cn/appstock/app/fundflow/get` | — | ❌ | ❌ | `Can't load controller` | — | — | **无资金流接口** |
| 14 | 雪球 `stock.xueqiu.com/v5/stock/quote.json` | — | ❌ 400 | ❌ 400 | — | — | — | **需 cookie/token** |
| 15 | 东财北向 `RPT_MUTUAL_HOLDSTOCKNORTH_STA` | `datacenter-web…` | ✅ | ✅ | HOLD_SHARES / HOLD_SHARES_RATIO / HOLD_MARKET_CAP | 北向持股 | **季频（最新 2026-06-30）** | 个股 |
| 16 | 东财北向 `RPT_MUTUAL_HOLD_DET` | `datacenter-web…` | ✅ | ✅ | 按机构明细 | 北向持股 | 日频但**已停更至 2024-07** | 个股 |
| 17 | 东财北向报表名穷举 | `RPT_MUTUAL_STOCK_NORTHSTA` 等 | — | ❌ 服务器繁忙/不存在 | — | — | — | **无日频北向报表** |

### 关键补充数据
- `push2test` **完全无鉴权**：不带任何 UA/Referer（空 header）亦返回 200 + 完整数据 ⇒ 真正免费、可零配置替换。
- `push2test` fflow 连续 **10/10 成功**、5/5 成功（两轮）；`lmt` 支持 0/5/30/120/1000，
  最多返回 103 个交易日（2026-02-27 ~ 2026-09-30）。
- `push2test` ETF 资金流实测：`1.510300`（104 根）、`0.159915`（107 根）、`1.588000`（99 根）均 200。
- `push2test` clist：`b:MK0021,b:MK0022` total=**1363**；A股 `m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23` total=**5562**。
- 新浪 ETF 覆盖：测试 15 只主流 ETF（510300/510050/588000/512880/159915/159919/510500/
  518880/513100/511990/510880/159941/515790/512480/516160） **全部 15/15 返回数据**。
- 北向：600519 / 000001 / 300750 / 601318 / 002415 均返回**同一报告期 2026-06-30** ⇒ 印证季频。

---

## ② 对上一轮 5 条结论逐条裁决

| # | 上一轮结论 | 裁决 | 依据 |
|---|---|---|---|
| 1 | 「资金流空白是 3 个独立数据源问题」（主力/北向/腾讯字段） | **✅ 成立（核心）** | 代码确认 3 条独立路径：`fetch_main_fund_flow` / `fetch_north_holding` / `fetch_quote` |
| 2 | 方案 A：改用 `RPT_DMSK_TS_STOCKNEW` | **⚠️ 部分修正** | 口径确实一致（533,565,520 = PRIME_INFLOW，差 0.00），**但仅最新 1 日、不含 ETF**；**更优解是 `push2test` 主机**（同样口径 + 日频 + 含 ETF） |
| 3 | 方案 B 新浪偏差 +39% | **✅ 成立** | 复测 741,042,739.94 vs 533,565,520 ⇒ **+38.9%** |
| 4 | **ETF 资金流无解** | **❌ 推翻** | ① `push2test` fflow 可取 ETF 历史；② `push2test` clist 可出 **ETF 资金流榜（1363 只）**；③ 新浪 `ssl_qsfx_zjlrqs` ETF **15/15 覆盖** |
| 5 | 「同花顺兜底」=从没接线（docstring 虚构） | **✅ 成立** | akshare `stock_fund_flow_individual` 底层 ajax 接口 **401**（需 hexin-v），确实无法直连 |

**对「`_first_source` 容错语义」的核对（代码层）**：
`realtime._first_source`（`realtime.py:145`）按序调用，**任一源成功即返回**，全部失败抛
`DataSourceUnavailable`。当前 `fetch_main_fund_flow`（`realtime.py:455`）的两个源
`push2delay`/`push2his` **同属东财同一服务组**，整组阻断 ⇒ `_first_source` 无法救场
（印证上一轮"实为东财单源"）。**加入 `push2test` 后仍属同厂，但至少换到可达主机。**

---

## ③ ETF 与北向专项结论

### ETF 资金流 —— 结论：**有解，且有两个独立免费源**
1. **优先：东财 `push2test`**
   - 单只历史：`/api/qt/stock/fflow/daykline/get?secid=1.510300` ⇒ 103 根日频主力净额，**口径与个股完全一致**（可直接落地 `etf.fetch_etf_flow_history`）。
   - 全市场榜：`/api/qt/clist/get?fs=b:MK0021,b:MK0022&fid=f62` ⇒ **1363 只 ETF 资金流榜**，直接替换 `etf.fetch_flow()` 的 `_EM_CLIST` 主机即可。
2. **兜底：新浪 `ssl_qsfx_zjlrqs`（`daima=sh510300`）** —— 覆盖 15/15，日频，
   字段 `netamount`（净额）/`r0_net`（超大单净额）/`turnover`。
   ⚠️ **口径与东财主力不同（个股实测偏差 +38.9%）**，只能当**独立标注的辅助源**，不得静默替换。

### 北向持股 —— 结论：**无日频免费源；季频可用但须诚实标注**
- 旧函数 `fetch_north_holding` 抛 `SourceRetiredError` 的理由是"无**有界**数据源"。
  但 `datacenter-web` 的 `RPT_MUTUAL_HOLDSTOCKNORTH_STA` **本身可达**、**有界（pageSize 上限）**，
  只是**季频**（最新 2026-06-30，count=1）。
- **裁定**：把它当"永久下线"过于绝对。建议**降级重接**为**季频**源，并在前端**显式标注
  "北向持股（季度披露，截至 2026-06-30）"**；若产品要求日频则确实无解（东财已自 2024-08 起
  停更沪深港通日频持股明细，`RPT_MUTUAL_HOLD_DET` 最新仅到 2024-07-31）。
- **是否可接受季频**：可以接受，但**必须诚实标注频率**，避免用户误以为是当日数据。

---

## ④ 修复方案（分优先级）

### P0 - 主力净流入（个股卡片"主力净流入 -"）

**改动点**：`backend/app/data/realtime.py`
- `fetch_main_fund_flow`（`:455`）：源列表**前置 `push2test`**，保留旧主机作降级：
  ```python
  return _first_source(
      [("em-test", lambda: _fetch_em_fflow("https://push2test.eastmoney.com", symbol)),
       ("em-delay", lambda: _fetch_em_fflow("https://push2delay.eastmoney.com", symbol)),
       ("em-his",   lambda: _fetch_em_fflow("https://push2his.eastmoney.com", symbol))],
      "主力资金流",
  )
  ```
  `_fetch_em_fflow`（`:421`）**无需改动**（已实测：换主机即返回 `main_net=533565520`，
  与 `RPT_DMSK_TS_STOCKNEW.PRIME_INFLOW` 差 0.00）。
- **口径风险：零**（同厂同接口同字段）。
- **限制**：`lmt=0` 最多 103 个交易日；`main_net_ratio` 字段在 push2test 上返回正常（11.12）。

### P0 - ETF 资金流（`etf.py`）

**改动点**：`backend/app/data/etf.py`
- `_EM_CLIST`（`:79`）主源改 `https://push2test.eastmoney.com/api/qt/clist/get`，
  旧主机保留为 `_EM_CLIST_FALLBACK`。影响 `fetch_cn_etfs`（`:308`）与 `fetch_flow`（`:550`）两处。
- `fetch_etf_flow_history`（`:999`，URL 在 `:1030`）主机改 `push2test`；
  **列序已核对兼容**（`fflow/kline/get` 8 列：日期 + 5 净额 + 2 占比，`parts[1]`=主力净额）。
- 文档同步修正 `etf.py` 顶部注释（`:6`、`:11-13`）"不可达/无替代源"的过时表述。

### P1 - 北向持股（诚实化，二选一）
- **方案 N1（推荐）**：`fetch_north_holding`（`:465`）改为调用可达的
  `datacenter-web` `RPT_MUTUAL_HOLDSTOCKNORTH_STA`（可复用 `realtime._dc_get`），
  返回 `{date, hold_shares, hold_market_cap, pct_of_float, frequency:"quarterly"}`，
  `panels.build_north`（`panels.py:106`）`status="ok"` 并附
  `"note": "北向持股按季度披露"`；前端展示日期。
- **方案 N2**：维持现状 `retired`，但前端文案从"数据源已永久下线"改为
  "北向持股暂无日频免费源（东财已停更）"，避免用户以为永远不会有。

### P2 - 新浪兜底（可选，须标注）
- 若需独立第二源：`fetch_main_fund_flow` 加入新浪 `ssl_qsfx_zjlrqs`，
  **必须**在返回 `source="sina"` 且 `panels.build_money_flow` 附
  `"caliber_note": "新浪口径，与东财主力净额存在系统性差异"`，前端提示。
- ⚠️ **红线**：不得把它当东财等价源混用（不满足"兜底值不得冒充真实指标"）。

---

## ⑤ 存疑 / 未验证项（不编造）

1. **`push2test` 的持久性未知**：`push2test` 是东财"测试"域名，**生产可用性无官方承诺**，
   可能随时下线/限流。实测 10/10 + 5/5 稳定，但**未做 24h+ 长周期观测**，也**未做高并发压测**。
   ⇒ 落地时**必须保留 `push2delay`/`push2his` 降级链**，并加可观测告警。
2. **`push2test` 与旧主机的口径一致性只验证了 1 只票（600519）的 1 日**，
   未做全样本统计检验。建议补：随机 100 只票 × 最新日，比对 push2test 与
   `RPT_DMSK_TS_STOCKNEW`（后者本身只有当日）的 `f52 == PRIME_INFLOW` 一致性。
3. **`push2test` 是否受更严格限流**未验证（本机代理为已知易变因素）。10 次 0.8s 间隔无失败，
   但生产 QPS 下行为未知。
4. **新浪 `ssl_bkzj_bk`（板块）** 本次返回 `Input error`，**正确参数未探明**，未验证。
5. **新浪 ETF 口径**：只验证了"有数据 + 覆盖全"，**未与任何东财 ETF 口径交叉校准**
   （东财 ETF 资金流刚发现，可比对但本次未做）。
6. **北向季频**：仅确认 5 只票最新报告期一致（2026-06-30），**未验证季报发布时延**
   （即"最新一期"通常在季度结束后多少天可得）。
7. **`RPT_MUTUAL_HOLD_DET`** 返回日频明细但最新到 **2024-07-31**，**停更原因未查证**
   （可能是政策调整 / 接口弃用）。
8. **未验证**：`push2test` 对**北向/沪深港通**路径（`/api/qt/stock/hsgt/*` 返回 404），
   即 **push2test 不提供北向**。

---

## 附：修复前后对照（实测）

```
现状：panels.build_money_flow('600519.SH')
  → DataSourceUnavailable 主力资金流 全部数据源失败
     [em-delay:DataSourceUnavailable; em-his:DataSourceUnavailable]

加 push2test 后：rt._fetch_em_fflow('https://push2test.eastmoney.com','600519.SH')
  → {'date': '2026-09-30', 'main_net': 533565520.0, 'super_large_net': 412137616.0,
     'large_net': 121427904.0, 'medium_net': -533026464.0, 'small_net': -539065.0,
     'main_net_ratio': 11.12, 'close': 1258.62, 'source': 'eastmoney'}
  （f55+f56 = 533,565,520 = PRIME_INFLOW，口径差 0.00）
```
