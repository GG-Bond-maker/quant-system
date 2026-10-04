# 数据源可用性验证报告 —— 美/日/韩大盘 + A 股大盘（ds-verify）

- 日期：2026-10-04
- 任务：#5 数据源可用性验证
- 结论一句话：**外盘块（日经225 / 韩国KOSPI / 道琼斯 / 纳斯达克 / 标普500）全部可用、逐项 `data_date` 正确；A 股大盘指数原始数据（新浪）全部可用；唯一不可用的是 `/overview/rt` 的「异动」子块（东财 `clist/get` 源站断开），且在冷/慢时会把整块（含可用的指数）拖成超时降级。**

> ⚠️ 数据集时间线为 2026 年（非现实当下）。样本值仅作"取到有效数值"的证据，**不做跨"现实 vs 数据集"的量级比较**。

---

## 0. 取证方式与环境

| 项 | 结论 |
|---|---|
| 沙箱网络 | **可用**（非无网络） |
| 公网对照 | `https://www.baidu.com` 200 (0.16s)；`https://qt.gtimg.cn/q=usDJI` 200 (0.17s) |
| DNS | `qt.gtimg.cn` / `hq.sinajs.cn` / `finance.sina.com.cn` / `push2.eastmoney.com` 均解析成功 |
| 方法 | ① 离线直调适配器/构建函数（主）；② in-process ASGI 端到端 HTTP（不绑端口、不跑 lifespan 副作用，辅助确认）；③ 东财主机直连定性 |
| 未起真实服务 | 未绑定端口启动 uvicorn（避免与其它 teammate 的实例冲突及 lifespan 预热副作用），改用 ASGI in-process 调用真实路由 |

探针脚本（均只读，落在 `deliverables/gstack/_tmp/`）：
- `ds_probe_2026-10-04.py`（+ `_out_*.txt`）：公网对照 + 外盘/A股逐个直调 + `_build_global_indices` / `_build_indices`
- `ds_probe_http_2026-10-04.py`（+ `_out_*.txt`）：ASGI 端到端 `/overview/global`、`/overview/rt`
- `ds_probe_rt_2026-10-04.py`（+ `_out_*.txt`）：东财直连 + `_build_heat` / `_build_money_flow`
- `ds_probe_rt_repro_2026-10-04.py`（+ `_out_*.txt`）：`/overview/rt` 连续 2 次复现

---

## 1. 外盘块 `/api/v1/market/overview/global` —— ✅ 全部正常

前端路径：`frontend/src/api/market.ts:78 overviewGlobal` → `GlobalIndices.tsx`（5 张卡按 `code` 合并）。
后端构建：`market.py:81 _build_global_indices`；数据源：美股=腾讯报价(主)+新浪 EOD(托底)，日经/KOSPI=新浪 EOD。

离线直调 `_build_global_indices`：**status=ok, failed=[], 6.10s**（akshare 已热）；HTTP 端到端：**200 / code=0 / status=ok**。

| 数据项 | 前端调用路径 | 能否正常返回 | 样本值（close / pct） | data_date | 数据源 | 失败原因 |
|---|---|---|---|---|---|---|
| 日经225 (NKY) | global → items[code=`日经225指数`] | ✅ | 68309.46 / -0.94% | 2026-10-02 | 新浪 EOD (`index_global_hist_sina`) | — |
| 韩国KOSPI | global → items[code=`首尔综合指数`] | ✅ | 7003.74 / +0.46% | 2026-10-02 | 新浪 EOD (`index_global_hist_sina`) | — |
| 道琼斯 (.DJI) | global → items[code=`.DJI`] | ✅ | 51176.96 / +0.49% | 2026-10-02 | 腾讯报价(主, as_of=2026-10-02 17:00:17) + 新浪 EOD(托底/走势) | — |
| 纳斯达克 (.IXIC) | global → items[code=`.IXIC`] | ✅ | 27190.86 / +1.19% | 2026-10-02 | 腾讯报价(主, as_of=2026-10-02 17:15:59) + 新浪 EOD | — |
| 标普500 (.INX) | global → items[code=`.INX`] | ✅ | 7722.72 / +0.73% | 2026-10-02 | 腾讯报价(主, as_of=2026-10-02 16:59:13) + 新浪 EOD | — |

补充：5 项均带 `sparkline`（60 点）；日经/KOSPI 恒为 `source=sina`（腾讯不覆盖，符合设计）；美股 3 项 `source=tencent`。

---

## 2. A 股大盘指数（原始数据，新浪）—— ✅ 全部正常

离线直调 `fetch_index_daily`（`CORE_INDICES`）/ `_build_indices`：**status=ok, failed=[], 6.98s**。

| 数据项 | 前端调用路径 | 能否正常返回 | 样本值（close / pct） | data_date | 数据源 | 失败原因 |
|---|---|---|---|---|---|---|
| 上证指数 sh000001 | `/overview/rt` → indices / daily 聚合 | ✅（原始数据可取，见 §3 注） | 3842.195 / +0.31% | 2026-09-30 | 新浪 `stock_zh_index_daily` | — |
| 深证成指 sz399001 | 同上 | ✅ | 12887.62 / -0.11% | 2026-09-30 | 新浪 | — |
| 创业板指 sz399006 | 同上 | ✅ | 3135.277 / -0.23% | 2026-09-30 | 新浪 | — |
| 科创50 sh000688 | 同上 | ✅ | 1530.006 / -2.51% | 2026-09-30 | 新浪 | — |
| 沪深300 sh000300 | 同上 | ✅ | 4357.616 / +0.29% | 2026-09-30 | 新浪 | — |

（A 股最后交易日 2026-09-30，其后为国庆假期，`data_date` 正确。）

---

## 3. ⚠️ `/overview/rt` 端点整体降级（A 股实时块）—— 唯一不可用项

HTTP 端到端（连续 3 次）：

| 次数 | 耗时 | 顶层 status | indices | money_flow | anomalies |
|---|---|---|---|---|---|
| #1（冷，同进程首次） | 15.05s（撞 15s 预算） | degraded | **unavailable** | **unavailable** | **unavailable** |
| #2（复现，第 1 次） | 12.09s | degraded | ok | ok | **degraded** |
| #3（复现，refresh=1） | 9.65s | degraded | ok | ok | **degraded** |

**根因（实测，非推断）**：
1. 东财**全市场列表**接口 `push2.eastmoney.com/api/qt/clist/get` 源站断开：
   `RemoteProtocolError: Server disconnected without sending a response`（直连 0.24s 即断）。
   → `_build_heat` 回退本地，**耗时 10.50s**（日志 `market.py:395`）。
2. 东财**指定指数**接口 `ulist.np/get` **正常 200**（0.23s）⇒ **不是沙箱无网络**，是**该重接口源站侧断开**（与代码注释记载的东财快速失败一致）。
3. `anomalies` 依赖 `stock_zh_a_spot_em`（=clist/get）⇒ **恒降级**，并触发熔断（`数据源连续失败已熔断…stock_zh_a_spot_em`）。
4. 全局 `_throttle()` 把 5 次新浪指数（≈6s）与 heat 的失败重试**串行化** ⇒ 冷/慢时合计超 15s ⇒ 整块被替换为降级 stub，**连本可用的新浪指数也一并丢弃**（#1）。

**结论**：`/overview/rt` 的 `indices` / `money_flow` 原始数据**可取**，但 `anomalies` **不可用**；且块级超时会把三者一起降级。**这是"块级超时把可用数据也丢了"的形态，不是指数数据本身取不到。**

**修复建议（根因在代码，仅指出位置，未改动）**：
- `backend/app/api/v1/market.py:873 _build_rt`：三个子块虽已并发，但 heat 的东财失败重试仍耗 10.5s，拖垮整块 → 归 be-budget（P1 超时预算）域。
- `backend/app/api/v1/market.py:1230 附近 _build()` 超时分支：整体替换为 stub，**丢弃已完成的 `indices`/`money_flow`**。建议超时降级时**保留已完成子块**（部分结果优于全空）。
- `stock_zh_a_spot_em`（东财 clist）在本环境源站断开：可考虑缩短该接口重试/退避，或直接以本地口径承接 `anomalies`（`_build_heat` 已有本地回退，`anomalies` 可复用同一 heat）。

---

## 4. 已知局限

- **未起真实 uvicorn 服务**：用 ASGI in-process 调用真实路由代替，规避端口/lifespan 预热副作用；结论与离线直调一致。未验证反向代理/网关层。
- `/overview/global` 首次 HTTP 调用 0.12s，**疑为 SWR 缓存命中**；冷构建路径已由离线直调 ④ 独立证实（6.10s，status=ok）。
- 东财 `clist/get` 断开可能是源站对高频访问的临时封禁（代码注释："东方财富会封临时 IP"）；本报告记录的是**当下实测**状态。
- `anomalies` 未单独直调（依赖 heat），其降级由 rt 端到端与日志证据判定。
- 未覆盖 `/market/quotes`（行情快照）与 `/overview/daily`（本地 parquet，零外部依赖）；后者预期不受外部源影响，未实测。
