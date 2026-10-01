# AQP 核心缺陷修复与「后端计算↔前端渲染」一致性交付报告

**日期**：2026-10-01
**场景**：全流程交付（缺陷排查 → 修复 → 验证）
**参与成员**：主理人（编排/汇编/实证）、后端修复工程师（backend-fixer）、前端修复工程师（frontend-fixer）
**用户诉求**：梳理未解决核心 bug 清单并按优先级排序；逐个修复（说明根因 / 修复方式 / 借鉴的量化项目设计模式）；重点保证后端计算结果与前端渲染一致；补充验证步骤证明彻底解决

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟢 **通过** —— 6 项确认缺陷全部修复并经独立验证，零回归。
- **核心洞察**：本轮最高危缺陷（P0）**不是崩溃，而是"看起来正常的错数"** —— 北向资金在休市日返回 `0`，后端不读 `交易状态` 列，前端据此渲染出红色「+0亿」。HTTP 200、无异常、无告警，**监控体系完全无法发现**。
- **验证结果**：后端 `1952 passed / 0 failed / 8 skipped`（修复前 `1942 passed / 10 failed`）；前端 4 道闸门全绿。
- **阻塞项**：0 条。
- **下一步**：将「量纲契约 + 涨跌方向三方一致」写入 CI 断言（见行动清单 A1/A2）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 **Go** |
| 修复缺陷数 | 6（P0 ×1 / P1 ×2 / P2 ×3） |
| 严重度分布 | 🔴 1 / 🟠 2 / 🟡 3 / 🟢 0 |
| 后端测试 | `1952 passed / 0 failed / 8 skipped`（551.67s） |
| 静态检查 | ruff `F,E9` ✓ · mypy `135 files` ✓ |
| 前端闸门 | `style:check` ✓ / `tsc -b --force` exit 0 ✓ / `build` ✓ 10.64s / `bundle:check` ✓ |
| 关键行动项 | 4 条（A1–A4） |
| 建议负责人 | 后端 owner（A1/A2 契约断言）+ 前端 owner（A3 评审清单） |

---

## 1. 各成员核心结论

### 🔧 后端修复工程师（backend-fixer）
- **核心判断**：本轮后端缺陷的共同根因是**「以 dtype / 字段名 / 注释作为判据」而非「以有效观测与跨字段恒等式作为判据」**。三处缺陷（北向休市、`sum() or 0`、baostock 量纲）全部源于此。
- **关键建议**：任何"0 是否可信"的判定，判据必须是 `len() - null_count() > 0`（有效观测数），而非 `issubclass(dtype, Number)`；任何"单位是什么"的判定，判据必须是跨字段恒等式（成交额 ≈ 价格 × 成交量），而非字段名或注释。
- **产出**：`market_service.py` 交易态守卫 + `market.py` 有效观测守卫 + `test_north_flow_non_trading.py`（7 例）。

### 🎨 前端修复工程师（frontend-fixer）
- **核心判断**：前端数据失真分两类 —— **量纲错配**（拿成交额格式器渲染成交量）与**方向未知被着色**（`?? false` 把 `null` 压成"下跌"）。两者都不会报错，只会安静地显示错数。
- **关键建议**：图表配色必须走 `chartPalette()`（Canvas 不参与 CSS 级联）；涨跌色必须走 `.t-up/.t-down`（跟随主题），而非 Tailwind `.text-up`（硬编码、暗色主题下对比度仅 3.66:1，不满足 WCAG AA 4.5:1）。
- **产出**：`fmtVol` 单一真源 + 25 处 `.text-up → .t-up` 迁移 + `MarketHeatmap` 空值守卫。

### 🧭 主理人（编排 / 实证）
- **核心判断**：两处独立发现被成员最初判为"非缺陷"或"风格债"，经**量化实证**后升级为真实缺陷：① 25 处 `text-up` 暗色主题对比度不达标（实测 3.66:1）；② `market.py` dtype 守卫**挡不住"Float64 但整列全 null"**（其 `sum()` 返回 `0.0` 而非 `None`）。
- **关键建议**：主理人假设必须交成员**对抗性验证**；成员"无需修复"的结论必须要求**给出反证或量化证据**。本轮 3 次纠正均由此产生。
- **产出**：完整性守卫补丁、`errs` 可观测性修复、7 个测试文件的语义化重写、本报告。

---

## 2. 缺陷清单与逐项修复（按优先级）

### 🔴 P0-1 · 北向资金休市日「0 冒充真实流入」

| 项 | 内容 |
|---|---|
| **位置** | `backend/app/services/market_service.py` → `build_money_flow()` |
| **根因** | 主源 `ak.stock_hsgt_fund_flow_summary_em()` 在休市日返回 `交易状态=4`，北向 `资金净流入=0.0`，**后端从不读取 `交易状态` 列**，把 `0.0` 当真实值汇总 ⇒ 前端显示红色「+0亿」。同一时刻南向为 `420.0`，对比之下更显"合理"。 |
| **实证** | 2026-10-01 实盘调用：`交易状态=4`，north `资金净流入=0.0` / south `420.0`。HTTP 200、无异常 ⇒ **零 5xx ≠ 健康**。 |
| **修复** | ① 新增 `_NORTH_NON_TRADING_STATUS = {4: "休市/未开盘", ...}` 与 `_non_trading_status_reason()`；② 非数值/未知取值 → 返回 `None`，**保守放行**（不误伤正常交易日）；③ 命中非交易态 → `raise ValueError("非交易态(交易状态=4:休市/未开盘)")` ⇒ 走既有降级链，`north_net_today=None`。 |
| **可观测性补丁（主理人）** | `errs.append(f"north:{type(e).__name__}")` → `errs.append(f"north:{e}")`。原写法把 `ValueError("非交易态…")` 压成 `north:ValueError`，而该 `errs` 会经 `reason` **原样透传到前端** ⇒ 等于**把可解释的降级变回不可解释**。 |
| **借鉴的量化设计模式** | **vnpy 的 `Exchange` / `TradingSession` 交易日历门控**：行情与资金流在非交易时段必须**先判会话有效性再取数**，而不是把休市快照当有效 tick。另一参照是 **QuantLib 的 `Calendar::isBusinessDay()` 前置校验**。 |
| **验证** | monkeypatch 注入休市帧后端到端确认：`status=unavailable`、`north_net_today=None`、`reason="north:非交易态(交易状态=4:休市/未开盘); …"`。新增 `tests/test_north_flow_non_trading.py`（7 例，全离线 fixture）。 |

### 🟠 P1-1 · ETF「成交量」按「成交额」格式化（量纲错配）

| 项 | 内容 |
|---|---|
| **位置** | `frontend/src/pages/EtfDetail/index.tsx`（tooltip `:227` / yAxis `:231`） |
| **根因** | 成交量走 `fmtAmountYi(v / 1e8)` ⇒ 后缀标「亿元」。成交量单位是「股」，除以 `1e8` 后标「亿」**在语义与数量级上双重错误**。 |
| **修复** | ① 在 `utils/format.ts` 抽出 `fmtVol()` 作为**全站唯一真源**（自动选 `亿/万/原值`，`null`/非有限值 → `-`），JSDoc 明示"勿与 `fmtAmountYi` 混用"；② `EtfDetail` / `KLineChart` 改用 `fmtVol`。 |
| **借鉴的量化设计模式** | **QuantLib 的 `Quantity` / vnpy 的 `ContractData`（乘数、最小变动、单位随合约携带）**：量纲必须与数值**绑定为契约**，不允许在渲染层隐式推断。对应到本项目即"一个量只有一个格式器"。 |
| **验证** | `tsc -b --force` exit 0；`grep` 确认 `EtfDetail` 无残留 `fmtAmountYi(…volume…)`。 |

### 🟠 P1-2 · 25 处涨跌色在暗色主题下对比度不达标（主理人独立发现）

| 项 | 内容 |
|---|---|
| **位置** | 8 个文件 25 处：`ui/index.tsx:114`、`Backtest/index.tsx:166,169,194×2,218,244`、`Backtest/resultParts.tsx:275`、`MarketOverview/AiPicksPanel.tsx:52,54,111×2,191,192`、`MarketOverview/BreadthPanel.tsx:127,147,148,155,157`、`StockDetail/index.tsx:313,366`、`Portfolio/index.tsx:421,440`、`Screener/index.tsx:358` |
| **根因** | 项目存在**两套涨跌色体系**：① 自定义 CSS `.t-up{color:var(--up)}`（跟随主题，**正确**）；② Tailwind `.text-up` → 编译为**硬编码** `rgb(217 43 43 / var(--tw-text-opacity))`，**没有 `html.theme-dark` 覆盖** ⇒ 暗色主题下不换色。 |
| **实证（WCAG）** | `text-up` 在暗色 `bg-card` 上对比度 **3.66:1**（**不满足** WCAG AA 4.5:1）；`.t-up` 为 **6.41:1**（满足）。 |
| **修复** | 25 处 `.text-up/.text-down` → `.t-up/.t-down`。 |
| **借鉴的量化设计模式** | **Bloomberg Terminal / TradingView 的"语义色板（semantic palette）"分层**：行情方向色与状态色（成功/危险/警告）必须是**两套独立 token**，且必须随主题重解析 —— 而不是把方向塞进通用调色板的某个色阶。 |
| **验证** | `grep -rnoE` 残留 = **0**；产物 CSS 中 `.text-up` 计数 = **0**（已被 purge），`.t-up{color:var(--up)}` 保留。主理人复核了成员 25 处的逐文件清单（成员初报"3-4 处"，实测 25 处）。 |
| **过程警示** | 成员首轮结论为"仅风格债、无需修复"。主理人要求量化证据后升级为缺陷 —— **"看起来能跑"不等于"符合规范"**。 |

### 🟡 P2-1 · `_heat_from_local` 的 `sum() or 0`（0 冒充不可得 + 潜在 500）

| 项 | 内容 |
|---|---|
| **位置** | `backend/app/api/v1/market.py:147-171` |
| **根因（比初判更尖锐）** | 初判是"`or 0` 让 0 冒充不可得"。实际更严重：polars 中**全 null 列 dtype 为 `Null`，其 `.sum()` 直接 raise** `InvalidOperationError` ⇒ 整个端点 500（`or 0` 根本执行不到，是死代码）。 |
| **残留缺口（主理人补齐）** | 成员只加了 dtype 守卫。但**显式 `Float64` 且整列全 null 时 `.sum()` 返回 `0.0`**（不是 `None`）⇒ dtype 守卫挡不住这一支，仍会退回"0 冒充不可得"。 |
| **修复** | 判据改为**有效观测数**：`if amt_col.len() - amt_col.null_count() > 0:` ⇒ 有观测才算，否则 `total_amount_yi = None`。 |
| **借鉴的量化设计模式** | **pandas/polars 生态的 `skipna` 语义显式化（vnpy 的 `ArrayManager` 与 QuantLib 的 `NaN` 处理约定）**：聚合前必须显式声明缺失值策略，"无数据"与"数据为 0"是两个不同的状态，不可由默认行为决定。 |
| **验证** | 新增 `test_local_heat_amount_all_null_float64_is_none_not_zero`。**证伪实验**：恢复旧守卫后该测试确实 FAIL（证明有判别力）。 |

### 🟡 P2-2 · `_scan_dataset` 静默吞掉失败文件数与总数

| 项 | 内容 |
|---|---|
| **位置** | `backend/app/api/v1/datacenter.py` → `_scan_dataset`（~225）/ `_summarize_manifest_entries`（~489） |
| **根因** | 读取失败的文件只记 WARNING。数据集 10 文件中 3 个损坏时，接口只报 7 个好文件的总行数，**前端无法察觉数据不完整** ⇒ 静默降级。 |
| **修复** | 两条返回路径均补 `skipped_files` / `total_files`，保持**同形状**（manifest 路径补 `"skipped_files": 0`）。 |
| **借鉴的量化设计模式** | **QuantLib 的 `Observable`/诊断与 vnpy 的 `DataEngine` 加载结果统计**：数据加载必须回传"请求 N / 成功 M / 跳过 K"，让消费方可判断完整性。 |
| **验证** | 更新 `test_hotpath_portfolio_datacenter.py:112` 的精确字典断言；本次改动曾导致 2 个测试失败，均已按**新的有意契约**修正。 |

### 🟡 P2-3 · `baostock_adapter` 对 `volume` 做 `÷100` 且注释误导

| 项 | 内容 |
|---|---|
| **位置** | `backend/app/data/ingest/baostock_adapter.py:20-21、:176、:195-196` |
| **根因** | 注释声称"东财 `volume` 单位=手，故 baostock（股）须 ÷100 对齐"。**前提是错的**：东财 `stock_zh_a_hist` 的 `volume` 单位本来就是**股**。 |
| **实证（决定性）** | 600519.SH 全量 **1142 个交易日**，`amount / (volume × close)` 中位数 = **1.0007**。若 volume 为「手」，该比值应 ≈100。⇒ 东财单位 = **股**。 |
| **修复** | ① 删除 `df["volume"] = (df["volume"] / 100.0)`；② 模块 docstring 增补**量纲契约**（明示"唯一口径是股、切勿换算"）；③ `_standardize_bs` docstring 改写。 |
| **影响面评估** | `multi_source._SOURCES` 实际为 akshare → eastmoney，**baostock 是 dead code**（所有 parquet `source` 均为 `akshare`）⇒ 非功能性、无存量数据污染。但仍修正，因为它**是一条会误导未来维护者的错误前提**。 |
| **借鉴的量化设计模式** | **QuantLib 的 `Unit`/`Quantity` 显式量纲 + FIX 协议（如 `LastQty` 的单位约定）**：单位属于接口契约本身，必须由**跨字段恒等式**验证，而非由注释或字段名声明。 |
| **验证** | 修正后断言"**原值直取、不换算**"`[123456.0, 234500.0]`，并加**跨字段恒等式守卫** `0.5 < amount/(volume×close) < 2.0`。**证伪实验**：重新注入 `÷100` → 测试 FAIL（`[1234.56, 2345.0] != [123456.0, 234500.0]`）；恢复 → 26 passed。 |

---

## 3. 排查中被**证伪**的疑似缺陷（同样重要）

为避免"宁可错杀"造成无谓改动，以下项经实证**排除**：

| 疑似项 | 排除依据 |
|---|---|
| `MarketOverview` 的 `{...daily, ...rt}` 浅合并覆盖 | `trade_date` 同源同值，无覆盖风险 |
| `BlockGrid` 240 格切片丢数据 | 与后端约定的一屏容量一致，是有意行为 |
| 复权口径不一致 | 全部序列实为 `daily_bar_hfq`，与 `_LABEL_PRICE_BASIS="hfq"` 自洽 |
| `setOption` 未传 `notMerge` | 均为"effect 内 `init`+`dispose`"，无实例复用 ⇒ 不存在残留 series |
| `echarts.init` 未传 theme | 配色统一由 `chartPalette()` 承担，路径正确、无双重来源 |
| `baostock_adapter` 是活跃链路 | `_SOURCES` 实际不含 baostock ⇒ dead code（故该缺陷降级为 P2 注释/健壮性） |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 验收判据 |
|---|------|--------|--------|---------|
| 1 | 把「休市/非交易态不得产出 0」做成 CI 断言模板，推广到**所有**外部源快照类接口（资金流、龙虎榜、分钟线） | 后端 owner | P0 | 新增源接入时必须有非交易态测试用例 |
| 2 | 落一条**量纲契约检查**：对 `daily_bar` 断言 `amount/(volume×close) ∈ (0.5, 2.0)`，纳入数据体检 | 后端 owner | P1 | 数据体检报告含该断言且通过 |
| 3 | 涨跌色评审清单扩展为**自动化**：新增/修改图表必须同时校验「文字 / 图形 / 图例」三方方向一致 | 前端 owner | P1 | `style:check` 覆盖 `.text-up` 类禁用规则 |
| 4 | 将 `chartPalette()` 从 `useMemo` 中解耦（或强制 `theme` 入依赖）加 ESLint 规则 | 前端 owner | P2 | lint 可拦截遗漏 |

---

## ⚠️ 待完善 / 已知局限

- **测试文件语义化重写的 7 处**：这些断言原本比对**重构前的字面量**（如 `'text-red-600'`、`'#94A3B8'`、`"import { useId } from 'react';"`），重构后必然失效。已改为**断言语义意图**（如"未知分支禁用绿色"、"`up` 必须可空且禁止 `?? false`"）。这提升了测试的**抗重构性**，但也意味着断言比字面量**更宽松** —— 后续若需要更严的把关，应补快照测试或 DOM 级测试。
- **`baostock_adapter` 为 dead code**：本次仅修正错误前提，未做集成验证。若未来启用 baostock 源，必须补一次**跨源同标的一致性对拍**（同 `symbol`+`date` 下 volume/amount 量级一致）。
- **并发编辑风险**：本轮出现一次并发编辑**回退掉主理人的守卫补丁**（由证伪脚本的 `assert old in s` 失败暴露）。团队并行写同一批文件时需注意。
- **`--select F,E9` 是刻意的收窄口径**：默认 ruff 规则集仍会多报约 58 条风格问题，**不在门禁范围内**，不应视为缺陷。
- **前端无 DOM 级测试**：25 处类名迁移靠 grep + 产物 CSS 计数验证，属静态证据；若要更强保障，建议引入组件快照测试。

---

## 4. 验证证据链（如何确认已彻底解决）

### 4.1 后端

```
修复前:  1942 passed /  10 failed / 8 skipped   ← 含 8 条重构后失效的陈旧断言
修复后:  1952 passed /   0 failed / 8 skipped   ← 实测 `1952 passed, 8 skipped, 0 failed in 551.67s`
基线:    1945 passed /   0 failed / 8 skipped   ← 2026-10-01 上午
```

- `+7` 净增 = 新增 `tests/test_north_flow_non_trading.py`（7 例）
- ruff `check app tests scripts --select F,E9` → `All checks passed!`
- mypy `app/ --ignore-missing-imports` → `Success: no issues found in 135 source files`

### 4.2 前端（4 道闸门全绿）

| 闸门 | 结果 |
|------|------|
| `npm run style:check` | 风格闸门通过 |
| `npx tsc -b --force` | exit 0 |
| `npm run build` | ✓ 10.64s |
| `npm run bundle:check` | passed |

产物 `dist/assets/index-*.css`：`.text-up` 计数 **0**（已 purge）；`.t-up{color:var(--up)}` 保留。

### 4.3 证伪实验（证明测试有判别力，而非"恰好通过"）

| 缺陷 | 注入历史 bug | 结果 |
|------|-------------|------|
| P2-1 有效观测守卫 | 改回 dtype-only 守卫 | 新测试 **FAIL** ✓ |
| P2-3 baostock 量纲 | 加回 `÷100` | `[1234.56, 2345.0] != [123456.0, 234500.0]` **FAIL** ✓ |

> 这是本项目最重要的验证原则：**"测试通过"本身不是证据，"注入 bug 后测试失败"才是。**

### 4.4 复现命令

```bash
# 后端
cd backend
./.venv/Scripts/python.exe -m pytest -q --tb=short -p no:randomly
./.venv/Scripts/python.exe -m pytest tests/test_north_flow_non_trading.py -q
./.venv/Scripts/python.exe -m ruff check app tests scripts --select F,E9

# 前端
cd frontend
npm run style:check && npx tsc -b --force && npm run build && npm run bundle:check
```

---

## 📚 成员产出索引

- **backend-fixer（后端修复工程师）**：`market_service.py` 交易态守卫、`market.py` 有效观测守卫、`datacenter.py` 跳过数披露、`test_north_flow_non_trading.py`（7 例）
- **frontend-fixer（前端修复工程师）**：`utils/format.ts` 的 `fmtVol` 单一真源、25 处 `.text-up → .t-up` 迁移、`MarketHeatmap` 空值守卫、`EtfDetail` 量纲修正
- **主理人（编排 / 实证）**：`errs` 可观测性修复、`market.py` 残留缺口（Float64 全 null）、`baostock_adapter` 量纲契约、7 个测试文件语义化、证伪实验设计、本报告

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
