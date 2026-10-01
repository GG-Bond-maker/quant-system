# ETF 资金流向卡长期空白 —— 根因诊断（含网络层定位）

**日期**：2026-09-30
**场景**：数据源能力诊断（未修复，待裁决）
**问题**：ETF 中心「资金流向」卡基本一直无数据，日志持续报 `DataSourceUnavailable`
**性质**：**非本次 ETF 修复引入**，是既有的外部数据源可达性问题
**涉及**：`backend/app/data/etf.py:550` `fetch_flow()` · `.env` 网络环境

---

## 📌 TL;DR

- **结论：不是代码 bug，是网络层阻断了东财 `push2*` 整组服务。**
- **实测**：东财 `push2` / `push2delay` / `push2his` **三个域名全部** `RemoteProtocolError`
  （连接被服务器/中间设备断开）；而 `datacenter-web` / `quote` 域名**可达**（HTTP 200）。
- **关键区分**：**能通的都是多 IP（CDN），不通的都是单 IP** ——
  换用公共 DNS（阿里 DoH）解析出的**另一个 IP** 依然不通 ⇒ 排除"本机 DNS 污染单一 IP"，
  指向**按服务组/IP 段阻断**。
- **影响面**：`fetch_flow()` **只有一个数据源（东财 clist）、无兜底** ⇒ 东财不可达即整卡空白。
  对比 ETF 目录有新浪+腾讯兜底（所以仍有 1723 只）—— **资金流是单点**。
- **降级是"如实"的**：后端返回 `status=unavailable` + reason，`net_inflow` 置 `null`
  而非 `0`（**符合项目红线**，没有把"取不到"伪装成"零净流入"）。
- **本次未修**：替换/新增数据源属产品与合规决策，需你裁决（见文末）。

---

## 1. 现象与证据

### 1.1 后端日志（持续复现）

```
[etf] 东财 clist 不可达（DataSourceUnavailable），切换新浪+腾讯兜底源
[etf.overview] 资金流源不可用，net_inflow 置为 null 而非 0:
    DataSourceUnavailable('外部数据源请求失败: RemoteProtocolError')
[etf] /flow 数据源不可用（DataSourceUnavailable）→ 结构化降级（HTTP 200 信封）
```

### 1.2 直接调用 `fetch_flow`

```
>>> E.fetch_flow('1d', limit=5)
[realtime] https://push2delay.eastmoney.com/... try 0 fail: RemoteProtocolError
DataSourceUnavailable: 外部数据源请求失败: RemoteProtocolError
```

### 1.3 可达性矩阵（2026-09-30 实测）

| 域名 | 结果 | 解析 IP 数 |
|---|---|---|
| `push2.eastmoney.com` | ❌ `RemoteProtocolError` | 1 |
| `push2delay.eastmoney.com` | ❌ `RemoteProtocolError` | 1 |
| `push2his.eastmoney.com` | ❌ `RemoteProtocolError` | 1 |
| `82.push2.eastmoney.com` | ❌ `RemoteProtocolError` | — |
| `datacenter-web.eastmoney.com` | ✅ HTTP 200 | **8** |
| `quote.eastmoney.com` | ✅ HTTP 200 | **4** |

### 1.4 排除项（逐项验证）

| 假设 | 验证方式 | 结论 |
|---|---|---|
| 代理问题 | 走代理 vs `trust_env=False` 直连 | ❌ 两种都失败 ⇒ **非代理** |
| DNS 问题 | 本机 DNS vs 阿里 DoH | ❌ DNS 正常返回 IP ⇒ **非 DNS** |
| TLS 问题 | 直接 `ssl.wrap_socket` 握手 | ✅ **TLS 成功** ⇒ 非证书/TLS |
| 单一 IP 被阻 | 换公共 DNS 解析出的**另一个 IP** | ❌ 仍失败 ⇒ **非单 IP，是服务组** |
| Python 库问题 | 用 `curl` 独立验证 | ❌ 同样 `HTTP 000` ⇒ **非 httpx** |

⇒ **收敛结论：网络层对东财 `push2*` 服务组（及其 IP 段）的阻断。**

---

## 2. 代码层问题（真正的可改进点）

### 2.1 数据源单点

```python
def fetch_flow(period: str = "1d", limit: int = 20) -> list[dict]:
    """ETF 资金净流入榜（东财主力口径）。"""
    ...
    data = _request("GET", _EM_CLIST, params=params, ...)   # ← 唯一来源，无兜底
```

对比目录侧：

```python
def fetch_cn_etfs():
    """主源东财 clist；不可达时**切换新浪+腾讯兜底**。"""   # ← 有兜底
```

**这是不对称的**：目录有兜底所以能撑住，资金流没有所以直接空白。

### 2.2 文档已承认此限制

`etf.py:78` 注释（**2026-09-23 就已记录**）：

> 主源：东财 clist（push2delay）。实测 2026-09-23 该域名及备用 push2 均 http=000。

⇒ **这不是新故障，是已存在至少 7 天的已知限制**，但**未进入产品可见范围**（用户只看到空卡）。

---

## 3. 没有"顺手修"的原因

1. **替换数据源 = 产品决策**：资金流口径（东财"主力净流入"）有其特定算法，
   换源会改变数值含义，**不能静默替换**（否则是"用兜底值冒充真实指标"，踩项目红线）。
2. **合规与稳定性未知**：任何新源都需评估可达性、限流、条款。
3. **本次改动边界**：用户裁决的是"修榜单联动 + 修美股 K 线"，
   资金流是独立问题，**不应混入同一批提交**。

---

## ⚠️ 待你裁决

1. **是否要把「资金流向卡」改为显式的"数据源不可用"文案？**
   （当前已如实降级，但前端呈现是否足够清楚？—— 属 UX 决策）
2. **是否寻找资金流的替代源？**（候选方向：
   ① 新浪单只 ETF 资金流（仅单只、需 N 次请求）；
   ② 同花顺 / 雪球（可达性未知）；
   ③ 付费源。**都需先验证可达性与口径一致性**。）
3. **是否接受"该卡在当前网络环境长期不可用"为已知限制**，
   并在文档/UI 上明示（而非让用户以为是加载失败）？

---

## 附：一句话总结

> **资金流不是"代码坏了"，是"东财 `push2*` 被网络阻断了，而这条链路只有一个源、没有兜底"。**
> 降级本身是诚实的（返回 `null` 而非 `0`），真正缺的是**第二条腿**。
