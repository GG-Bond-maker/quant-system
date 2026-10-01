# 上线前全检 · 4 项阻塞修复交付报告

**日期**：2026-10-01
**场景**：全流程交付（代码修复 + 验证落盘）
**基线报告**：`deliverables/gstack/pre-launch-fullcheck-2026-10-01.md`（结论 🟡 条件 Go，4 项 🔴 阻塞）
**执行方式**：GStack 主理人单成员执行（修复属实现工作，非多专家评审场景，不再另建团队）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 **4 项阻塞全部解除，门禁全绿，基线无损**
- 阻塞项数量：**0**（原 4 项 🔴 全部关闭）
- 额外发现并修复 **2 个**在修复过程中暴露/引出的真缺陷（详见 §3）
- 下一步：可进入发布流程；⚠️ 仍有 3 项 🟠 非阻塞遗留（见 §5）

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 **Go**（4 阻塞已清） |
| 严重度分布（本轮修复） | 🔴 6 已修 / 🟠 0 / 🟡 0 |
| 关键行动项 | 已全部完成；遗留 3 项 🟠 转下轮 |
| 建议负责人 | 后端负责人复核 `_pick_stat_day` 阈值口径；运维核对生产 `.env` |

---

## 1. 修复项逐条结论

### 🔴 B1 · mypy 19 errors + 全市场统计日被 `max(date)` 劫持

**问题**：`market.py` 两处 polars 联合标量类型未收窄 ⇒ mypy 硬门禁必红；
且 `_pick_stat_day` 与 `_build_ai_stats` 存在"统计日取 `max(date)`"的历史坏惯用法。

**修改**：
- `app/api/v1/market.py`：引入 `cast`；`_pick_stat_day` 的 `cov["n"].max()`、
  `_build_ai_stats` 的 `pred["date"].min()/max()` 用 `cast(...)` 收窄。
- `_build_ai_stats` 年份跨度裁剪补前瞻窗 `horizon*3+10` 天，防末日样本 `future_close`
  被截成 null 而**静默丢样本**。

**验收**：mypy `19 errors → Success: no issues found in 135 source files`。

---

### 🔴 B2 · 市场级资金流走错 host（数据假性缺失）

**问题**：`build_money_flow` 的 `ak().stock_market_fund_flow()` /
`stock_sector_fund_flow_rank()` 内部硬编码 `push2his` / `push2`，
在本机出网代理下**整组阻断**；而项目已在 `realtime.py` / `etf.py` 用 `push2test` 取得同数据
⇒ **唯独 market_service 漏改**，"取得到却报取不到"。

**修改**：
- `app/data/realtime.py`：新增 `_fetch_em_market_fflow(host)`、`_fetch_em_sector_fflow(host, type)`、
  对外 `fetch_market_fund_flow()` / `fetch_sector_fund_flow(sector_type)`，
  经 `_first_source` 走 **`push2test → push2delay → push2his`** 降级链。
- `app/services/market_service.py`：改调新适配器；**无有效净额观测显式 raise**（不以 0 冒充）。

**验收（实测）**：
```
status: degraded | n_ok: 2
main_net_today: -59.27
sector_flows: 8  [创新药 main_yi=41.5, 重组蛋白 main_yi=11.7, ...]
north: None   reason: 资金源部分可用(2/3)：north:非交易态(交易状态=4:休市/未开盘)
```
北向在休市日**正确降级**（读 `交易状态` 列，不把 0 当 0 成交）。

---

### 🔴 B3 · ETF 日期口径错位（`date.today()` 冒充数据日）

**问题**：`etf.py` 三处用 `date.today()` 当数据日；同项目 market / stock / portfolio
均用 `today_trade_date_or_last()`，**仅 etf 异类** ⇒ 节假日/周末把"今天"当数据日。

**修改**：`app/api/v1/etf.py` — 删除 `from datetime import date`，
`_etf_data_date()` 改用 `today_trade_date_or_last()`；两处调用点改为 `_etf_data_date()`。

**验收**：`_etf_data_date()` 返回 `2026-09-30`（= 仓库最新交易日，非今天 10-01）。

---

### 🔴 B4 · 框架策略回测完全无流动性闸门（本轮最重）

**问题**：`StrategyBacktestRequest` **根本没有** `max_participation` 字段，
`_run_single` 调 `run_strategy` 也**不透传** ⇒ 形参默认 `0.0` = 不限制
⇒ 大资金回测把远超市场承接量的订单当全部成交，**系统性高估收益**。
而 `run_strategy` docstring 谎称"策略回测路径已走 5% 默认"——**注释 ≠ 代码**。

**修改**（4 处 + 1 处缓存键）：
1. `backtest.py`：`StrategyBacktestRequest` 补 `max_participation: float = Field(0.05, ge=0, le=1)`
   （显式 `0.0` 可关闭，向后兼容）。
2. `_run_single`：两处 `run_strategy` 调用均透传 `max_participation=req.max_participation`。
3. `_friction_config`：**解耦**流动性闸门与摩擦成本 —— `enable_friction=False` 但
   `max_participation > 0` 时**保留闸门**、仅把滑点/衰减/冲击系数置 0
   （原实现关摩擦会**顺手关掉闸门**，0.05 默认值在默认路径下是死参数）。
4. `strategy_base.py`：docstring 更正为"两条 API 路径均已显式透传"。
5. 🔴 **`_strategy_cache_key` 补 `mp{req.max_participation}`**：该字段本轮才**第一次生效**，
   若不入键则 `0.05` 与 `0.0` 在 600s TTL 内**互取缓存**，返回另一套流动性约束下的
   收益/成交结构，而载荷 `liquidity.participation_cap` 披露会与实际不符
   （同 P0-1 根因链）。

**验收（证伪性，关键）**：
```
① 修复在位  → 7 passed
② 注入 bug（删掉两处透传）→ 2 FAILED
     AssertionError: 透传值不符：期望 0.05，实得 0.0（若为 0.0 说明透传被回退，闸门静默失效）
③ 恢复修复  → 7 passed
```
**"测试通过"不是证据，"注入 bug 后测试失败"才是** —— 已按此纪律复核。

---

## 2. 新增/修改测试

| 文件 | 类型 | 用例 | 说明 |
|------|------|------|------|
| `tests/test_strategy_max_participation_wiring.py` | **新建** | 7 | 捕获 `_run_single` 实传给 `run_strategy` 的 `max_participation`；含字段契约 + 行为层 + legacy 分支诚实性 |
| `tests/test_backtest_cache_key.py` | 修改 | +1 | `test_strategy_key_differs_by_max_participation`：固化"闸门值必须入键" |
| `tests/test_pipeline_state_truth.py` | 修改 | 0（修 6 失败） | 见 §3 |

**技术要点**：`_run_single` 内是**运行时局部导入** `from ...backtest.strategy_base import run_strategy`
⇒ 用 `monkeypatch.setattr(strategy_base, "run_strategy", spy)` 即可拦截。

---

## 3. 🔴 修复过程中额外发现并关闭的 2 个真缺陷

### 3.1 B4 的缓存键缺口（见 §1 B4 第 5 点）

`max_participation` 不入 `_strategy_cache_key`。**这是修复本身引出的新暴露面**：
该字段修复前恒无效（不存在），修复后第一次真正生效，于是"不入键"从无害变成有害。
**已在同一轮一并修复并加测试固化。**

### 3.2 B2 的 monkeypatch 失效（由**全量套件**抓到，非单测）

**现象**：4 项修复完成后跑全量，基线 1971P/8S → **6 failed**（`test_pipeline_state_truth.py`）。

**根因**：B2 把 `build_money_flow` 的调用从 `ak().stock_market_fund_flow()` 改成
**模块级 `from ... import`** 的 `fetch_market_fund_flow`。而该测试用
`monkeypatch.setattr(ms, "get_akshare", ...)` 注入"子源失败" ——
**直接 import 的符号绕过 patch** ⇒ 适配器打到**真实网络** ⇒
被注入为"失败"的源其实**成功返回了真数据** ⇒ `n_ok` / `status` 全错
（断言 `1/3 可用却报 ok`）。

**修改**：
- `market_service.py` 改为**模块属性访问** `_realtime.fetch_market_fund_flow(...)`
  （`from ..data import realtime as _realtime`），与同文件 `get_akshare` 约定一致。
- `test_pipeline_state_truth.py` 的 `_sf` 桩返回**东财原始字段名**（`f14`/`f66`/`f72`/`f78`/`f84`），
  早前的 akshare 中文列名（`名称`/`超大单净流入-净额`）契约已随适配器切换失效。

**验收**：`test_pipeline_state_truth.py` → **34 passed**；B2 线上行为**逐位不变**。

> 🔴 **元教训（已写入 CHANGELOG / memory）**：把"直连外部源"改成"模块级 import 的适配器"
> 会**静默破坏所有靠 monkeypatch 注入失败的测试** —— 单测可能**假绿**
> （注入失效、恰好真网络也失败）或**假红**。改这类调用点必须同时排查同名
> `monkeypatch.setattr` 点。
> 这是「测试通过不是证据」的反面：**测试失败也可能是测试自己坏了**。
> ⚠️ 若只跑单测（`pytest tests/test_pipeline_state_truth.py` 之外的定向子集），
> 该缺陷会**直接漏到发布**。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 状态 |
|---|------|--------|--------|------|
| 1 | B1 mypy 收窄 + 统计日口径 | 后端 | P0 | ✅ 完成 |
| 2 | B2 资金流多源适配 + 模块属性访问 | 后端 | P0 | ✅ 完成 |
| 3 | B3 ETF 交易日口径统一 | 后端 | P0 | ✅ 完成 |
| 4 | B4 闸门接线 + 解耦 + 入缓存键 | 后端 | P0 | ✅ 完成 |
| 5 | 修 `test_pipeline_state_truth.py` 6 失败 + 桩契约 | 后端 | P0 | ✅ 完成 |
| 6 | 生产 `.env` 核对（`ADMIN_TOKEN`/`JWT_SECRET`/`RBAC_ENFORCE`/`ALLOW_ADMIN_TOKEN_LOGIN`） | 运维 | 🟠 P1 | ⬜ 待办（非本轮） |
| 7 | `/overview/daily` 补 `background_build`（`market.py:1140`） | 后端 | 🟠 P1 | ⬜ 待办（非本轮，当前未触发） |
| 8 | `etf.py` `sum(x.get("size_yi") or 0)` 静默退化 | 后端 | 🟠 P1 | ⬜ 待办（非本轮） |
| 9 | 外呼全局串行（`_throttle` 持锁 sleep）+ 重复调用去重 | 后端 | 🟡 P2 | ⬜ 待办（非本轮） |

---

## ⚠️ 待完善 / 已知局限

1. **`_pick_stat_day` 阈值口径需业务复核**：当前"覆盖度阈值 = 回看窗最大覆盖度的一半"是
   **相对判据**（不硬编码标的池，可自适应），但"一半"这个比例本身是经验值，
   建议由研究负责人确认是否与"全市场宽度"的业务定义相符。
2. **北向资金结构性缺失未解**：北向持股数据源已永久下线，属**诚实披露**而非可修缺陷；
   `north=None` + `reason` 是当前正解。
3. **B4 默认值变更的行为影响**：`StrategyBacktestRequest` 新增字段默认 0.05，
   会**改变既有框架策略回测的成交笔数/数量结构**（同一组数据可能由 1 笔拆成 2 笔）。
   这是**有意为之**（修复前是错的），但会让历史回测结果与旧版不可比 —— 需在发布说明中提示。
4. **后端静默消失问题未解**：主理人实测复现（8022 启后调 rt，进程消失，
   `starting`=3 / `shutdown`=0，无异常），**不属本轮 4 项阻塞**，仍待专项排查。
5. **本机验证限制**：全量 pytest 在**本机**通过（1979P/8S），
   但**生产环境的出网代理策略可能不同** ⇒ B2 的降级链在生产需实测确认
   （`push2test` 可达性）。
6. **`size_yi` 语义**：`500000000000.0` 疑似**单位标注为亿、实为元**（≈5017 亿 vs 真实 ~5017 万美元）。
   本轮未动（非阻塞），已记入下轮。

---

## 📚 产出索引

- 基线全检报告：`deliverables/gstack/pre-launch-fullcheck-2026-10-01.md`
- 本修复交付报告：`deliverables/gstack/fix-prelaunch-4blockers-2026-10-01.md`（本文件）
- 指令清单：`CHANGELOG.md` → `[Unreleased] — 2026-10-01 · 上线前全检 4 项阻塞修复`
- 工作日志：`.workbuddy-ai/memory/2026-10-01.md`

**变更文件清单（9 个）**

| 文件 | 变更 |
|------|------|
| `backend/app/api/v1/market.py` | `cast` 收窄 + 前瞻窗 |
| `backend/app/api/v1/etf.py` | `_etf_data_date()` 口径统一 |
| `backend/app/api/v1/backtest.py` | B4 字段/透传/解耦 + 缓存键 |
| `backend/app/backtest/strategy_base.py` | docstring 更正 |
| `backend/app/data/realtime.py` | 新增 2 个多源适配器 |
| `backend/app/services/market_service.py` | 改调适配器 + 模块属性访问 |
| `backend/tests/test_strategy_max_participation_wiring.py` | **新建** |
| `backend/tests/test_backtest_cache_key.py` | +1 用例 |
| `backend/tests/test_pipeline_state_truth.py` | 桩契约更正 |
| `CHANGELOG.md` | 新增版本段 |

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
