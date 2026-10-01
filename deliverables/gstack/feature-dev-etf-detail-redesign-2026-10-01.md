# ETF 分析界面按目标设计改造 —— 可行性评估与交付报告

**日期**：2026-10-01
**场景**：全流程交付（可行性评审 → 数据核实 → 实施 → 独立验证）
**参与成员**：产品官（gstack-product-reviewer）+ 排障手（gstack-investigator）+ 设计师（gstack-designer）+ 质量门神（gstack-qa-lead）
**输入方案**：`deliverables/gstack/feasibility-etf-detail-redesign-2026-10-01.md`
**参考稿**：华夏沪深300ETF（510300）详情页设计稿

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟡 **有条件通过** —— 目标设计可落地，但方案文档的"三层拆分"需要修正：**文档称"必须放弃"的部分里，有 1 项实际可得；文档称"可直接实现"的部分里，有 2 项前提是错的**。
- 阻塞项数量：**3 条**（`tracking`+`valuation` 块降级率过高 / 无浏览器自动化导致无法自截图 / 口径归一方向待产品决策）。
- 已完成实施：**10 个文件**（6 后端 + 4 前端），全部门禁绿灯，`pytest 1981 passed / 8 skipped / 0 failed`，且**重启进程后端到端验证通过**（真实 HTTP；kline `volume` 与独立源同日仅差 19 股）。
- 🔴 **额外收获**：在核查 `volume` 消费方时发现并修复一个**与本次改动无关的既有 🔴 缺陷** —— `volume_spike` 告警规则把「股」与「手」直接比较，**数学上永不触发**（静默死功能），且其测试因合成数据单位无关而**长期开了绿灯**。
- 下一步：需**人工目视验收**（本机 Windows 无法自动化截图），并决策 `tracking`/`valuation` 两个高降级率块的产品口径。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟡 **条件 Go**（已实施，待人工目视验收） |
| 严重度分布 | 🔴 3 / 🟠 3 / 🟡 5 / 🟢 5 |
| 关键行动项 | 7 条 |
| 建议负责人 | 前端负责人（布局/可视化）+ 数据负责人（口径已修，需回归） |
| 改动文件 | 10 个（后端 6 / 前端 4） |
| 门禁 | 前端 5 项 ✅ / 后端 3 项 ✅ / 端到端 ✅ |
| 证伪实验 | 2 次（均按预期 FAIL） |

---

## 1. 各成员核心结论

### 🔍 产品官（产品评审）
- **核心判断**：🟡 条件可行。目标设计**约 70% 可直接落地**，但方案文档把"参考稿有、本项目没有"的字段一律归为"必须放弃"，**没有区分"数据缺失"与"数据口径不对"**。
- **关键建议**：
  1. **缺失字段必须在结构上消失**，不得填 `—` 占位（`inception`/`manager` 实测 **0/1363** 全缺失 ⇒ 应从布局删除，而非显示"—"）。
  2. **删除全部假交互**。中部 `KLINE_TABS`（`['基本信息','重仓明细','行业配置','资金流向']`）实测**点击零反应**——`klineTab` state 只有 2 处引用（L375 声明 + L602 自身高亮），无任何消费者。同类的还有 `trackPeriod`、复权下拉、"更多"入口。
  3. **禁止偷换概念**：`sentiment` 块是**公告标题关键词派生**，不得渲染成"用户讨论帖"（头像/时间轴）⇒ 卡片应改名"**公告情绪**"。

### 🔧 排障手（调试与根因）
- **核心判断**：方案文档有 **3 条结论被实测推翻**，其中 1 条若不纠正会造成**数据错误**。
- **关键建议**：
  1. 🔴 **`fetch_kline` 的 `volume` 单位是【手】，不是【股】**。同日恒等式实测：`amount/(volume×close)` 归一前 = **99.94**（7 只 ETF 一致），归一后 = **0.9994** ⇒ 必须 ×100。
  2. 🔴 **但直接改 ×100 不安全** —— `backend/app/api/v1/watchlist.py:302` **已按「手」×100**，不同步删除会造成 **10000 倍**双重换算。已同步修复。
  3. ❌ **文档称 `exchange` 可替代"成立日期"，错误** —— `EtfItem.exchange` 是**硬编码常量** `"SH"/"SZ"`（1363/1363 同一取值），**不是真实交易所字段**，不可当数据用。
  4. ✅ **文档称估值"历史均值"不可得，错误** —— legulegu 有 **258 期真实序列**（PE 均值 34.8997 / PB 均值 3.7368，**月度**粒度）⇒ 该项从"必须放弃"升级为"需新增数据源"。

### 🎨 设计师（设计系统与视觉）
- **核心判断**：目标设计的主要视觉差距**不在配色，而在断点与图表可读性**。
- **关键建议**：
  1. 🔴 **断点选错**：侧栏 `w-40`(160px) + `main` 的 `px-4` ⇒ 1440px@125% 缩放后仅约 **1152 CSS px**、内容区 **≈960px** ⇒ `xl:`(1280) 与 `2xl:`(1536) **双双失效**，应统一降到 `lg:`(1024)。
  2. **甜甜圈图例被裁**：原 `legend.show=false` 丢失信息 ⇒ 改为底部横排 `type:'scroll'` + 名称截断 5 字 + 关闭扇区外 label，`center:['50%','40%']`、`radius:['38%','58%']`。
  3. **补零轴基准线**：跟踪误差图与资金流图加 `markLine { yAxis: 0 }`（`HAIR2`、dashed、`label.show=false`），否则正负无法目视判读。
  4. **ECharts 纪律**：canvas 不参与 CSS 级联 ⇒ 取色**必须**走 `chartPalette()`；`setOption(option, true)` 必须 notMerge；`theme` 必须进 `useMemo` 依赖数组。

### ✅ 质量门神（QA测试与发布）
- **核心判断**：🟢 门禁全绿，但**两个数据块的降级率高到会影响产品可用性**。
- **关键建议**：
  1. 🔴 **抽样 30 只 CN ETF 降级率实测**：`flow` 100% / `kline` 96.7% / `holdings` 90% / `news` 83.3% / `chain` 76.7% / **`tracking` 仅 16.7% ok** / **`valuation` 仅 12.5% ok** ⇒ 跟踪误差卡与估值卡在**绝大多数标的上是降级态**，必须显式渲染 `DegradedBadge`/`PanelEmpty`，绝不能显示 `0`。
  2. **独立证伪 volume 修复**：注释掉 `vol *= 100` ⇒ 新测试 **FAIL**（`AssertionError: A 股 volume 未归一到「股」：4958544.0`）⇒ 证明测试**真的在守**，不是空断言。
  3. **资金流四档恒等式实证**：5 只 ETF × 60 日 = **300/300 成立**，`net_inflow == super_large_net + large_net`，**最大残差 0.00 元**；`net_inflow==0` 的 6 行经查是**真零**（四档非零相抵，如 `-1982600/+1982600/0/0`），**非"缺失写 0"** ⇒ 四档堆叠柱可安全渲染，缺失档传 `null` 不传 `0`。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| 1 | 🔴 | 数据口径 | `backend/app/data/etf.py:544` | `fetch_kline` 的 `volume` 是【手】，与全仓统一口径【股】差 100 倍 | 已修：A 股分支 `vol *= 100`，美股不换算 | 排障手 |
| 2 | 🔴 | 数据口径 | `backend/app/api/v1/watchlist.py:302` | 已按「手」×100，与上条修复叠加将造成 **10000 倍** | 已修：去掉 `* 100` | 排障手 |
| 3 | 🔴 | 数据口径 | `backend/app/api/v1/alerts.py:611` | 🔴 **排查中的额外发现（超出原任务范围）**：`volume_spike` 把 `daily_bar` 的【股】与快照的【手】直接比较 ⇒ 差 100 倍；因 `k∈[1.2,20]`，判据退化为 `1/100 ≥ k` ⇒ **规则数学上永不触发（静默死功能）** | 已修：日均量「股→手」归一；补真实单位回归测试 + 证伪 | 排障手 |
| 4 | 🟠 | 产品可用性 | `tracking` / `valuation` 块 | 抽样 ok 率仅 **16.7%** / **12.5%** | 显式降级渲染 + 决策是否补数据源 | 质量门神 |
| 5 | 🟠 | 假交互 | `EtfDetail/index.tsx` 中部 | `KLINE_TABS` 4 个 Tab 点击零反应 | 已修：整块删除 | 产品官 |
| 6 | 🟠 | 假交互 | `EtfDetail/index.tsx` | `trackPeriod` 切换不改变数据；复权下拉不可用；"更多"入口无跳转 | 已修：`trackPeriod` 接真实裁剪 / 复权改只读 / 移除"更多" | 产品官 |
| 7 | 🟡 | 契约/字段 | `EtfItem.exchange` | 硬编码常量 `"SH"/"SZ"`，被误当"交易所"使用 | 不得当数据字段；如需交易所须新增真实源 | 排障手 |
| 8 | 🟡 | 注释失实 | `realtime.py:308` / `quotes_hub.py:77` / `market.ts:23` | 三处均称快照 volume「与 daily_bar 对齐」，**实为差 100 倍** | 已修：三处注释改为显式披露差异 | 排障手 |
| 9 | 🟡 | 断点 | `EtfDetail/index.tsx` | `xl:` 在 1440@125% 下不生效 ⇒ 布局错位 | 已修：统一降为 `lg:` | 设计师 |
| 10 | 🟡 | 视觉 | 甜甜圈图 | `legend.show=false` 丢失板块信息 | 已修：底部横排 scroll 图例 | 设计师 |
| 11 | 🟡 | 视觉 | 跟踪误差图 | 无零轴基准线，正负不可读 | 已修：加 `markLine { yAxis: 0 }` | 设计师 |
| 12 | 🟢 | 测试盲区 | `tests/test_alerts.py:195` | `volume_spike` 测试两侧都写 10,000 ⇒ **单位无关**，给死规则开了绿灯 | 已修：合成数据改用真实按源单位（股/手）+ 加 `avg_volume_hand` 断言 | 质量门神 |
| 13 | 🟢 | 概念 | `sentiment` 块 | 公告关键词派生被渲染成"讨论情绪" | 已修：卡片改名"公告情绪" | 产品官 |
| 14 | 🟢 | 字段 | 顶部指标行 | `inception` 实测 **0/1363** 全缺失 | 已修：换为真实可得的 `custody_fee`（8/8 有值） | 产品官 |
| 15 | 🟢 | 图表 | `GaugeQuad` | 四宫格仪表盘信息密度低、无法表达四档对比 | 已修：`FundFlowChart` 四档堆叠柱替代 | 设计师 |
| 16 | 🟢 | 代码 | `EtfDetail/index.tsx` | 单文件 783 行、多块耦合 | 拆分 `FundFlowChart` / `flowYi` / `TRACK_PERIOD_DAYS`，增至 914 行但结构更清晰 | 设计师 |

---

## 2.5 🔴 额外发现：`volume_spike` 告警规则是「静默死功能」（超出原任务范围）

**发现路径**：本交付的 P1 行动项要求"全仓核查 `fetch_kline.volume` 消费方"。核查中发现 `alerts.py` 同时消费 `daily_bar` 与实时快照**两个源**，遂对其做单位审计。

**缺陷**：`backend/app/api/v1/alerts.py:604-618`

```python
bars = await asyncio.to_thread(read_symbol_dataset, "daily_bar", sym)  # volume = 【股】
hist = [float(v) for v in bars["volume"].to_list()[:-1] if v and v == v]
avg  = sum(hist) / len(hist)                    # ← 股
if avg > 0 and float(q["volume"]) >= avg * k:   # ← q["volume"] 是【手】
```

- `daily_bar.volume` = **股**（同日恒等式实测 `amount/(volume×close)` = **1.000653** / **1.000192**）
- 快照 `q["volume"]` = **手**（腾讯 `f[6]` 原生手；新浪路径 `realtime.py:380` 已 `/100` 归到手）
- ⇒ 判据实际为 `今日股/100 ≥ 日均股 × k` ⇔ **`1/100 ≥ k`**
- 而 `alerts.py:98` 强制 `k ∈ [1.2, 20]` ⇒ **规则数学上永不触发**

**错误前提的来源 —— 3 处注释互相印证同一个错误**：

| 位置 | 原文 |
|---|---|
| `alerts.py:6` | "快照 + daily_bar，**单位均为手**" |
| `realtime.py:308` | "口径与 daily_bar 对齐：volume=手（volume_spike 规则直接可比）" |
| `quotes_hub.py:77` | "volume 单位=手，与 daily_bar 对齐" |

⇒ 这正是「注释里写的 ≠ 代码里做的」与「两个适配器互相印证同一个错误前提」的**复合形态**。

**为什么长期没被发现（测试盲区）**：`tests/test_alerts.py:195` 两侧都合成 `10,000` ⇒ 判据**单位无关**，无论 `daily_bar` 是股还是手都能通过。**"测试通过"在这里毫无证明力。**

**影响评估**：
- 当前 `alert_rules` 表 **0 行** ⇒ **无存量用户受影响**（属潜伏缺陷）
- 但 `Alerts/index.tsx:314` 渲染「现量 X 手」、API 承诺该规则可用 ⇒ 用户一旦配置即遇"永不触发"
- 附带缺陷：payload `avg_volume_hand` 名为「手」实为「股」（**字段名与单位不符**）

**修复（最小爆炸半径，不改任何 API 契约）**：
1. `alerts.py`：日均量「股→手」归一后再比 ⇒ 两侧同口径；`avg_volume_hand` 现在名副其实
2. `tests/test_alerts.py`：合成数据改用**真实按源单位**（daily_bar=股 `1,000,000` / 快照=手 `50,000`）+ 新增 `avg_volume_hand == 10000.0` 断言
3. 3 处错误注释改为显式披露差异

**证伪**：去掉 `/ 100.0` ⇒ `test_volume_spike_trigger` FAIL（`assert 0 == 1`，零触发）⇒ 缺陷与修复均被证明。

**遗留决策（行动项 #4）**：`realtime` 快照仍是「手」孤岛。建议统一为「股」（项目已完成 `daily_bar`/`fetch_kline` 的股归一），但会**改变告警行为**，需产品确认。

---

## 3. 交付清单

### 3.1 代码变更

| 文件 | 行数 | 变更 |
|------|------|------|
| `backend/app/data/etf.py` | ~L544 | A 股 `volume` 归一 ×100；docstring 标注「单位=股」 |
| `backend/app/api/v1/watchlist.py` | L302 | 去掉重复的 `* 100` |
| `backend/tests/test_etf.py` | +47 | 新增 2 用例：`test_fetch_kline_cn_volume_normalized_to_shares`（断言 `495_854_400.0`）/ `test_fetch_kline_us_volume_left_untouched` |
| `frontend/src/pages/EtfDetail/index.tsx` | 783 → **914** | 删假 Tab / 换 `custody_fee` / 改名"公告情绪" / 甜甜圈图例 / 零轴 markLine / `FundFlowChart` 替代 `GaugeQuad` / `trackPeriod` 接真裁剪 / `xl:`→`lg:` |
| `frontend/src/components/charts/KLineChart.tsx` | 833 → **852** | 新增 `showAdjust?: boolean`（默认 `true`，不影响 `StockDetail` 调用点） |
| `backend/app/api/v1/alerts.py` | L6, L604-618 | **额外发现**：`volume_spike` 日均量「股→手」归一（修「永不触发」的静默死功能）；docstring 修正单位说明 |
| `backend/tests/test_alerts.py` | +5 | 合成数据改用**真实按源单位**（daily_bar=股、快照=手）；新增 `avg_volume_hand == 10000.0` 断言 |
| `backend/app/data/realtime.py` | L308 | 注释修正：显式披露「快照=手 / daily_bar=股，差 100 倍」 |
| `backend/app/data/quotes_hub.py` | L77 | 同上 |
| `frontend/src/api/market.ts` | L23 | 同上（`LiveQuote.volume` 单位披露） |

### 3.2 测试覆盖

- **新增**：2 个后端用例（`fetch_kline` volume 单位归一 + 美股不换算）
- **加固**：1 个既有用例（`test_volume_spike_trigger`）——原合成数据两侧同为 10,000，**单位无关** ⇒ 无法捕捉真实错配；改用真实按源单位（daily_bar=股、快照=手）并加 `avg_volume_hand` 断言
- **证伪实验 ×2**（证明断言真的在守，而非空断言）：
  1. 注释掉 `etf.py` 的 `vol *= 100` ⇒ `test_fetch_kline_cn_volume_normalized_to_shares` FAIL（`4958544.0`）
  2. 去掉 `alerts.py` 的 `/ 100.0` ⇒ `test_volume_spike_trigger` FAIL（`assert 0 == 1`，规则零触发）
- **回归**：`pytest -q --tb=short -p no:randomly` ⇒ **1981 passed / 8 skipped / 0 failed**（401.66s）
  （总数与基线持平是正确的：本次是**修改**既有用例而非新增，故 `passed` 计数不变）

### 3.3 发布检查清单

- [x] 前端 `npm run style:check` —— 通过（无 `<h1>` 手写、无硬编码 hex）
- [x] 前端 `npm run nav:check` —— 通过（20 条路径至多一个高亮）
- [x] 前端 `npx tsc -b --force` —— **EXIT=0**
- [x] 前端 `npm run build` —— 通过
- [x] 前端 `npm run bundle:check` —— 通过（raw ≤ 768000 B / gzip ≤ 256000 B）
- [x] 后端 `ruff check app tests scripts --select F,E9` —— 通过
- [x] 后端 `mypy app/ --ignore-missing-imports` —— 通过（135 files）
- [x] 后端 `pytest` —— **1981 passed / 8 skipped / 0 failed**
- [x] 后端**端到端**（重启进程 + 真实 HTTP）—— 9 块全 ok；kline `volume` 与独立源同日吻合（差 19 股）✅ 见 §3.5
- [ ] **人工目视验收**（本机 Windows 无浏览器自动化，见 §5 局限）

### 3.4 回滚预案

- 纯代码改动，**无 schema 迁移、无数据写入** ⇒ 回滚即 `git checkout` 上述 10 个文件。
- ⚠️ **成对约束 1**：`etf.py` 的 ×100 与 `watchlist.py` 的去掉 ×100 **必须成对回滚**，否则成交额差 100 倍。
- ⚠️ **成对约束 2**：`alerts.py` 的「股→手」归一与 `tests/test_alerts.py` 的按源单位合成数据**必须成对回滚**，否则测试必红。
- ℹ️ **告警行为变化**：`alerts.py` 修复让 `volume_spike` 从「永不触发」变为「正常触发」。
  当前 `alert_rules` 表 **0 行** ⇒ **无存量规则受影响**；但用户一旦配置该规则，行为与修复前不同（这正是修复目的）。

### 3.5 端到端验收（重启进程后走真实 HTTP）✅

**为什么要做**：uvicorn **无 `--reload`** ⇒ 改完后端**必须重启进程**再验收，否则拿旧代码的响应误判"修复无效"。

**方法**：`netstat` 确认 8000 未监听（无旧进程）⇒ 启动新进程 ⇒ `/health/ready` 返回 `ready`（sqlite / data_root / core_snapshot / redis 全 ok）⇒ 用应用自身 `create_jwt_token` 签发 token 后请求**真实接口**。

**结果（510300，9 个块）**：`header` / `kline` / `holdings` / `tracking` / `flow` / `news` / `sentiment` / `chain` = **ok**；
`valuation` 首轮为 `unavailable（数据源响应超时）`、复测恢复 `ok` ⇒ **降级是瞬态且可自愈**（且**未用 0 冒充**）。

**🔴 关键交叉验证 —— kline `volume` 单位**（该块**不返回 `amount`**，故改用「接口末条 vs 独立源同日」）：

| 来源 | 日期 | close | volume |
|---|---|---|---|
| 运行中接口 `/api/v1/etf/detail/510300` 末条 | 2026-09-30 | 4.432 | **495,854,400** |
| 新浪同日快照（独立源；同日恒等式 **0.999364** ⇒ 股） | 2026-09-30 | 4.432 | **495,854,381** |

⇒ 相差 **19 股（0.0000038%）**、`close` 完全一致 ⇒ **运行中的后端确实以「股」返回**，修复在**生产路径**上生效。

**其他字段实测**：`custody_fee` = `0.05`（510300）/ `0.1`（512880）**真实可得**；`inception = None`（确认结构性缺失）；
`flow.items = 60` 且四档全非空 **60/60**；`tracking_error = 1.7874`、`points = 252`。

**跨标的降级态实测**：512880 / 159915 的 `tracking` = `unavailable（无法识别跟踪指数）`、
`valuation` = `unavailable（无法获取估值数据）` ⇒ **印证** §5 局限 #2 的 16.7% / 12.5% 结论。

> 验收用的后端进程已在验收后**停止**，环境恢复原状。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | **人工目视验收** ETF 详情页（1440@125% 与 1920 两档视口），确认布局、图例、零轴、四档堆叠柱 | 前端负责人 | **P0** | 本日 |
| 2 | 决策 `tracking`(16.7%)/`valuation`(12.5%) 高降级率块的产品口径：补数据源 or 显式降级文案 | 数据负责人 + 产品 | **P0** | 本周 |
| 3 | ✅ **已闭环**：全仓核查 `fetch_kline.volume` 全部 6 个消费点 ⇒ 其中 2 个只用 `close`、`watchlist` 用比值 ⇒ **无第三处按「手」处理** | 排障手 | ~~P1~~ | 已完成 |
| 4 | **决策口径归一方向**：`realtime` 快照仍是「手」孤岛（`daily_bar`/`fetch_kline` 已统一为「股」）。建议统一为「股」（`realtime.py` 去 `/100` + `LiveQuote.volume` 契约 + `Alerts` 文案同步）；⚠️ 会**改变告警行为**，需产品确认 | 数据负责人 + 前端 | **P1** | 本周 |
| 5 | 为 `tracking`/`valuation` 降级路径补测试覆盖（长期无覆盖的分支会积累死代码级 bug） | QA | P1 | 本周 |
| 6 | 复核 `EtfItem.exchange` 的其他消费方，确认无人把它当"交易所"用 | 排障手 | P2 | 本周 |
| 7 | 清理 `frontend/scripts/_fix_jsx_comments.py`（本次一次性脚本，无复用价值） | 前端负责人 | P2 | 本周 |

---

## ⚠️ 待完善 / 已知局限

1. 🔴 **无法自截图**：本机（Windows）浏览器自动化不可用 ⇒ 前端改动**只能靠「视口推导 + 源码断言 + 闸门全绿」三重间接证据**。**本报告不声称已目视通过**，须人工验收（行动项 #1）。
2. 🟠 **`tracking`/`valuation` 降级率高**：抽样 30 只 CN ETF，ok 率仅 **16.7% / 12.5%**。这两张卡在多数标的上是降级态，产品体验与"看起来能用"存在落差，需产品决策。
3. 🟡 **估值历史均值粒度是「月」**：legulegu 258 期序列为月度 ⇒ 若 UI 标注"历史均值"需注明粒度，否则易被误读为日频分位。
4. 🟡 **`inception`/`manager` 结构性缺失**：**0/1363**，无法通过任何现有源补齐 ⇒ 参考稿的这两项**必须从布局删除**（已执行）。
5. 🟡 **`tracking_index` 缺失 1177/1363**：跟踪指数字段也不完整，若后续要做"跟踪指数"展示需先解决。
6. 🟢 **未做真实浏览器 E2E**：`tsc` + `build` 只能保证类型与打包，不能保证运行时渲染。建议后续在有 Linux/macOS 环境的 CI 上补 Playwright 截图回归。
7. 🟡 **`volume_spike` 修复是"行为变更"**：该规则从「永不触发」变为「正常触发」。当前 `alert_rules` 为 **0 行**故无存量影响，但若后续有人配置该规则，其行为与修复前不同 —— 这是**修复的目的**，仍建议产品知悉。
8. 🟡 **「手」孤岛未消除**：本次只做了**归一**（比对前换算），未做**统一**（改源口径）。`realtime`/`quotes_hub`/`LiveQuote` 仍是「手」，`daily_bar`/`fetch_kline` 是「股」⇒ 未来新增消费方仍可能踩同一坑（行动项 #4）。

---

## 📚 成员产出索引

- **gstack-product-reviewer（产品官）**：可行性三层拆分复核、假交互清单（`KLINE_TABS`/`trackPeriod`/复权下拉/"更多"）、缺失字段"结构上消失"判据、概念对齐（讨论情绪→公告情绪）
- **gstack-investigator（排障手）**：volume 单位同日恒等式实测（99.94→0.9994，7 只 ETF）、`watchlist.py` 双重换算风险定位、`exchange` 硬编码常量核实（1363/1363）、估值历史均值可得性核实（legulegu 258 期）、资金流四档恒等式 300/300、**`volume_spike` 静默死功能根因定位（股/手 100 倍错配，见 §2.5）**
- **gstack-designer（设计师）**：断点纪律（`lg` vs `xl` vs `2xl`）、甜甜圈图例方案、零轴 `markLine` 规范、`FundFlowChart` 四档堆叠柱设计、ECharts 取色/依赖纪律
- **gstack-qa-lead（质量门神）**：30 只 CN ETF 抽样降级率矩阵、volume 修复独立证伪实验、资金流 `net_inflow==0` 真零判定、全门禁执行与结果确认

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
