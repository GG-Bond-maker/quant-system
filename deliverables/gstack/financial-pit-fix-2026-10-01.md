# 财务 PIT 链路修复 + 参与率闸门默认值（P0-1 / P0-2 / P1）

**日期**：2026-10-01
**场景**：缺陷修复（数据链路 + 特征接线 + 风控默认值）
**参与成员**：数据工程师（data-engineer）+ 特征工程师（feature-engineer）+ 审计员（auditor）
**依据**：`deliverables/gstack/capability-audit-quant-checklist-2026-10-01.md` 的能力审计行动项

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟢 **通过** —— 三项修复全部落地并独立验证，全量回归 **1945 passed / 0 failed**（基线 1903）
- **核心成果**：财务链路从「能力写好了但从未接线、且日期字段是未来函数」变为「真实公告日 + PIT 安全 asof join + 12 期真实数据」
- **阻塞项数量**：0
- **最重要的意外发现**：原计划使用的 `stock_yjbb_em.最新公告日期` 是**未来函数（晚 1 年）**，若直接采用会把前视偏差从 +45 天**放大到 12 个月**。已在接入前证伪并替换为 `stock_report_disclosure`
- **下一步**：财务因子当前**默认关闭**（`FEATURE_FINANCIAL=0`）；若要进生产需按报告 §5 的 4 步流程开启并重训

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 **Go**（修复本身可上线；因子启用需独立决策） |
| 严重度分布 | 🔴 0 / 🟠 0 / 🟡 2 / 🟢 若干 |
| 关键行动项 | 4 条（见 §4） |
| 全量回归 | **1945 passed / 8 skipped / 0 failed**（基线 1903 passed） |
| 代码门禁 | ruff `--select F,E9` ✅ All checks passed；mypy ✅ no issues in 135 files |
| 数据规模 | `financial_report`：**63,594 行 / 12 期**（2023Q1–2025Q4） |

---

## 1. 各成员核心结论

### 🔧 数据工程师（data-engineer）
- **核心判断**：财务 PIT 链路的正确解法是**披露日与数值分离取源** —— 日期用巨潮预约披露（`stock_report_disclosure`，整市场按报告期一次拉取，~1.4–3.1s/期），数值用东财业绩报表（`stock_yjbb_em`，但**只用其数值列**）。
- **关键建议**：`announce_date` 采用**双分支**——`实际披露` 非空则用（`actual`），否则回落 `首次预约`（`scheduled`，事前可知）。
- **额外发现（超出任务范围）**：旧的 sina 实现里 `col_map` 引用的 `主营业务收入(元)`/`净利润(元)`/`负债总额(元)`/`每股收益(元/股)` **四个列名在接口返回中根本不存在** ⇒ 旧代码静默写入 NULL。实测确认（`净资产收益率(%)` 存在，其余 4 列 `列不存在`）。
- **诚实记录**：新源的 `roa`/`total_assets`/`total_liability` 填充率为 **0**（`stock_yjbb_em` 不提供资产负债表项），选择「不编造」而非「用代理值填」。

### ✅ 特征工程师（feature-engineer）
- **核心判断**：直接往生产 `build_factors`（`FEATURE_VERSION="alpha_basic_v2g"`）里塞财务因子会造成**静默改变生产特征语义** ⇒ 采用**新增独立模块 + 显式开关（默认关闭）**，沿用项目已验证的 `features_v2` 模式。
- **关键建议**：新建 `app/ml/financial_factors.py`，提供 `build_financial_factors(panel, source, enabled)`，默认关闭由环境变量 `FEATURE_FINANCIAL` 控制。
- **最重要的洞察（oracle 陷阱）**：旧的代理公告日 `period + 45天` 与 A 股法定截止日**结构性冲突** —— Q1 报告期 + 30 天即 4/30 为法定截止日，而代理值 `period+45天` 必然更晚 ⇒ **asof join 在旧数据上永远 join 不到任何行**，训练时因子恒 NaN、推理时却有值 ⇒ **train/serve skew（假因子）**。**这解释了为什么这条链一直没被接线。**

### 🛡️ 审计员（auditor）
- **核心判断**：**前视偏差已真实消除**。关键证据是自建的三日边界矩阵：公告**前**（2025-04-02）不可见、公告**当天**（2025-04-03）可见、公告**后**可见。
- **关键建议**：`run_strategy` 路径应**显式暴露** `max_participation` 参数，而非让它静默继承 `BrokerConfig` 的新默认值。
- **对抗性贡献**：**用真实数据推翻了自建的 P0**。基于合成数据曾判定「高 ROE 在公告日当天可见」为 P0 前视泄露；改用真实数据对照组（真实 `asof=2026-10-01` 返回 14 行 vs 合成 1 行）后**自行证伪**，降级为设计如此（同日可见为正确 PIT 语义）。
- **独立证实未来函数结论**：用 3 只股票 × 2 个报告期独立复检，确认 `最新公告日期` 晚于真实披露日约 1 年。
- **对 P0-2 修法的最终裁决**：审计员复核主理人的实现后**主动修正了自己的原建议** —— 认为「新增显式参数 + 默认 0.0」**优于**其原先提出的「改 3 个测试断言」，理由是**零测试改动即通过**、完整保留 `run_strategy` 的 legacy 语义、且把 `BrokerConfig` 的 5% 默认仅限定在 API 路径，符合项目「行为变化须显式表达」的既有约定（`strategy_base.py:453-457` 的同类修复先例）。

---

## 2. 综合审查发现（去重合并，按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 处置 | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🟡 | 数据/PIT | `financials.py` 旧实现 | 代理披露日 `period+45天` 与 A 股法定截止日结构性冲突 ⇒ asof join 永不命中 ⇒ train/serve skew | ✅ 改用真实公告日 | 特征工程师 |
| 2 | 🟡 | 数据质量 | `financials.py` 旧实现 | `col_map` 中 4 个列名在 sina 返回中不存在 ⇒ `revenue`/`net_profit`/`total_liability`/`eps` 静默写 NULL | ✅ 新实现修复（实测确认） | 数据工程师 |
| 3 | 🟢 | 兼容性 | `strategy_base.py:368` | 改 `max_participation` 默认值后，该路径未传参 ⇒ 静默继承 0.05 ⇒ 3 个测试失败（buys 1→2 笔） | ✅ 新增显式参数，默认 0.0（审计员复核后判定优于其原方案） | 审计员 + 主理人 |
| 4 | 🟢 | 数据质量 | `financial_report` | `roa`/`total_assets`/`total_liability` 填充率 0% | ⚠️ 已知局限（无因子依赖） | 数据工程师 |
| 5 | 🟢 | 数据质量 | `financial_report` | `f_revenue_yoy` 需去年同期 ⇒ 仅 2 期数据时 100% 空缺 | ✅ 回补 12 期后已可算 | 主理人 |
| 6 | 🟢 | 测试卫生 | `tests/test_p1_data.py` | `PITTEST.SH` 测试夹具污染生产 DB（2 行） | ✅ 已随回补清除 | 审计员 |

---

## 3. 关键证据链

### 3.1 未来函数证伪（最重要）

原定方案 `ak.stock_yjbb_em(date='20241231')` 的 `最新公告日期` 字段：

| 报告期 | `最新公告日期`（600519） | 真实披露日 | 偏差 |
|---|---|---|---|
| 2024-03-31 | 2025-04-30 | 2024-04-03 | +1 年 |
| 2024-06-30 | 2025-08-13 | 2024-08-02 | +1 年 |
| 2024-09-30 | 2025-10-30 | 2024-10-25 | +1 年 |
| 2024-12-31 | 2026-04-17 | **2025-04-03** | +1 年 |
| 2025-03-31 | 2026-04-25 | 2025-04-25 | 0（巧合） |

**ground truth 来源**：`ak.stock_zh_a_disclosure_report_cninfo(symbol='600519', start_date='20250301', end_date='20250531')` → 「贵州茅台2024年年度报告 **2025-04-03**」，与 `stock_report_disclosure` 的 `实际披露` **完全一致**。

**对照组（同一报告期两个字段的取值窗口）**：
- `2024年报`：`实际披露` 范围 2025-01-25 ~ 2025-07-05（正常窗口）；`最新公告日期` 给 600519 → 2026-04-17
- 「实际披露比首次预约晚 >180 天」的行数 = **0**（无后续修订污染）

⇒ **结论：采用 `最新公告日期` 会引入 12 个月前视偏差，严格劣于被替换的 +45 天代理值。**

### 3.2 覆盖率互补性（数据源分工的依据）

```
stock_report_disclosure '2024年报': rows=5370   （6/0/3 开头，4/8 开头=0）
stock_yjbb_em(date='20241231'):     rows=11638  （多出 4/8 开头=5979，北交所/新三板）
交集 = 5370  ← 与 disclosure 行数完全相等
```
⇒ **disclosure 是 yjbb 在「沪深京」上的完全覆盖子集，零缺失**；两者互补（日期用前者、数值用后者）。

### 3.3 PIT 端到端验证（真实数据）

回补 12 期后，用真实贵州茅台数据验证（此为**主理人独立复跑**，非成员测试）：

```
date         f_roe   f_revenue_yoy   f_log_revenue   说明
2025-03-28   26.09   0.169           25.536          2024Q3 财报
2025-04-02   26.09   0.169           25.536          ← 年报公告前，仍是旧期 ✅
2025-04-03   36.02   0.157           25.883          ← 年报公告当天跳变 ✅
2025-04-30   10.92   0.107           24.664          2025Q1 财报
2025-09-01   17.89   0.092           25.235          2025H1 财报
```
- `f_roe` 从 26.09 → **36.02** 的跳变**精确发生在 2025-04-03**（已验证的披露日）
- `f_revenue_yoy` **5/5 填充**（回补前 100% 空缺）

### 3.4 对照实验：`max_participation` 默认值的影响

同一组 bars、同一策略，仅改 `max_participation`：

| `max_participation` | buys 笔数 | qty |
|---|---|---|
| **0.0**（旧默认） | **1** | 87,100 |
| **0.05**（新默认） | **2** | 49,900 + 34,500 |

总手数相同（87,100），被 5% 闸门拆成两天 ⇒ **确证 3 个测试失败的根因是默认值变更**，与 `budget_cap`/`enabled` 改动无关。

### 3.5 数据填充率（回补后）

| 字段 | 填充数 / 63,594 | 状态 |
|---|---|---|
| `net_profit` | 63,593 | ✅ |
| `bps` | 63,592 | ✅ |
| `revenue` | 63,582 | ✅ 99.98% |
| `eps` | 63,555 | ✅ 99.9% |
| `roe` | 63,232 | ✅ 99.4% |
| `gross_margin` | 62,374 | ✅ 98.1% |
| `roa` / `total_assets` / `total_liability` | **0** | ⚠️ 无源（见 §5） |

`announce_basis` 分布：`actual` **63,588** / `scheduled` **6**（6 条为尚未披露的 2025 年报，属正确 PIT 行为）。

---

## 4. ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 状态 |
|---|------|--------|--------|------|
| 1 | `financials.py` 改用 `stock_report_disclosure` 真实公告日 + 双分支 PIT SQL | data-engineer | P0 | ✅ 完成 |
| 2 | `max_participation` 默认 0.0 → 0.05（API + BrokerConfig 两层） | 主理人 | P0 | ✅ 完成 |
| 3 | `run_strategy` 显式暴露 `max_participation`（默认 0.0 保持 legacy） | 主理人 | P0 | ✅ 完成 |
| 4 | 新增 `financial_factors.py`（PIT asof join，默认关闭） | feature-engineer | P1 | ✅ 完成 |
| 5 | 回补 12 期真实财报数据（63,594 行） | data-engineer | P1 | ✅ 完成 |
| 6 | **决策：是否开启 `FEATURE_FINANCIAL` 并重训** | 产品/工程负责人 | P2 | ⏳ 待决（见 §5） |

---

## 5. ⚠️ 待完善 / 已知局限

1. **财务因子默认关闭** —— `FEATURE_FINANCIAL=0`。这是 feature-engineer 的**刻意决策**（避免静默改变生产特征语义）。启用需按序执行：
   1. 复核代理比例（应已全部为真实公告日）
   2. 设 `FEATURE_FINANCIAL=1` 重跑 `scripts/build_features.py`
   3. bump `FEATURE_VERSION`（`alpha_basic_v2g` → 含财务因子空间的新版本）并登记进 `KNOWN_FEATURE_VERSIONS`
   4. **必须重训** —— 特征空间变了，旧模型 features.json 与新面板不匹配会导致线上 predict 500

2. **资产负债表三项无数据** —— `roa`/`total_assets`/`total_liability` 填充率 0%。`stock_yjbb_em` 不提供资产负债表项。**当前因子集（`f_roe`/`f_revenue_yoy`/`f_log_revenue`）均不依赖它们**，故非阻塞。若将来需要 ROA 类因子，需另接数据源。

3. **北交所标的披露日** —— 4/8 开头的标的（~5,979 只/期）不在 `stock_report_disclosure` 覆盖内，其日期走 `scheduled` 回落或保守代理，精度低于沪深。

4. **`actual` 占比高带来的解释成本** —— 63,588/63,594 为 `actual`。回测中若使用 `asof` 早于披露日，`load_financials_asof` 的双分支 SQL 会正确屏蔽；但若有人**直接查表**而不走该函数，仍会看到未来数据。**该表不应被绕过 PIT 函数直接消费。**

5. **测试夹具写生产 DB** —— 本次发现 `PITTEST.SH` 两行污染生产库。虽已清除，但 `tests/test_p1_data.py` 的隔离机制值得复查（本次未深查，属遗留观察项）。

---

## 6. 变更清单（文件级）

| 文件 | 类型 | 说明 |
|---|---|---|
| `backend/app/data/ingest/financials.py` | 重写 | 79 → 345 行；真实公告日 + 双分支 PIT SQL + `coverage_summary` |
| `backend/app/ml/financial_factors.py` | **新增** | 363 行；PIT asof join + 默认关闭开关 |
| `backend/tests/test_financial_factors.py` | **新增** | 289 行；10 tests（含 oracle 陷阱守卫） |
| `backend/app/api/v1/backtest.py` | 修改 | `max_participation` 默认 0.0 → **0.05** |
| `backend/app/backtest/broker.py` | 修改 | `max_participation` 默认 0.0 → **0.05** |
| `backend/app/backtest/strategy_base.py` | 修改 | `run_strategy` 新增 `max_participation` 参数（默认 **0.0**） |
| `backend/tests/test_backtest_cache_key.py` | 修改 | 测试值 0.05→0.10；新增默认值防回归测试 |
| `backend/tests/test_p1_data.py` | 修改 | 旧 sina 契约测试改写为巨潮契约（含 600519 ground-truth 断言） |

---

## 7. 验证证据汇总

| 验证项 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `pytest -q -p no:randomly` | **1945 passed / 8 skipped / 0 failed** |
| ruff 门禁 | `ruff check app tests scripts --select F,E9` | All checks passed |
| mypy 门禁 | `mypy app/ --ignore-missing-imports` | no issues in 135 source files |
| P1 数据测试 | `pytest tests/test_p1_data.py` | 7 passed（含 ground-truth 断言） |
| 财务因子测试 | `pytest tests/test_financial_factors.py` | 10 passed |
| 缓存键 + 冲击成本 + 门闸 | `pytest test_backtest_cache_key + test_impact_cost_e2e + test_ma_cross_gates` | 13 passed |
| PIT 边界（主理人独立复跑） | 自建 600519 三日场景 | 公告前 NaN / 当天 36.02 / 后 36.02 ✅ |
| 端到端真实数据 | 12 期回补 + `build_financial_factors` | YoY 5/5 填充 ✅ |

---

## ⚠️ 免责声明

> 本报告由软件工坊 AI 协作生成。参与成员为独立 AI 审计者，其结论已由主理人交叉复核（含独立复跑对照实验）。关键决策（尤其是**是否开启财务因子并重训**）请由工程负责人复核后再执行。

---

## 📚 成员产出索引

- **data-engineer（数据工程师）**：`financials.py` 重写（真实公告日 + 双分支 PIT SQL）+ 测试契约改写 + 12 期数据回补 + 数值填充率体检
- **feature-engineer（特征工程师）**：`app/ml/financial_factors.py` + `tests/test_financial_factors.py`（363 + 289 行）+ oracle 陷阱识别 + 默认关闭决策
- **auditor（审计员）**：未来函数独立复检（3 股 × 2 期）、PIT 边界矩阵、`max_participation` 泄漏点归因、自我证伪 1 次
- **team-lead（主理人）**：数据源调研与证伪、P0-2 实现、`run_strategy` 修复、端到端验证、报告汇编
