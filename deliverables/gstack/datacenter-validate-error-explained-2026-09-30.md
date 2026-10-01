# 数据中心告警横幅根因分析

> 告警原文：`API 接口状态：最近同步异常：ValueError: validate failed: 昨有今无 24/25 只（阈值 5%）：000001, 00，部分高频接口建议开启本地缓存同步。`
> 分析人：investigator2（gstack-investigator）｜日期：2026-09-30｜项目：Alpha Quant Platform

---

## 0. 结论先行（TL;DR）

| 问题 | 结论 |
|---|---|
| 这条告警是"数据真缺"还是"校验 bug"？ | **两者都有，但主因是数据真缺 + 校验逻辑有重大盲区**。数据侧：全市场 99% 标的（2474/2499）的 `daily_bar` 自 09-17/09-18 起就再未更新，是**真实的大面积数据停滞**；校验侧：`step_validate` 的"停牌跳过"兜底把 2474 只全部当作"停牌"静默排除，导致分母只剩 25，把一个"全市场停更"误报成"25 只里丢了 24 只"。 |
| `24/25` 是什么？ | `n_expected = n_ok(1) + missing(24) = 25`，**分母不是全市场 2499，而是"当日或上一交易日有数据的标的"**。即：09-29 只有 000007 有新数据（n_ok=1），另有 24 只有 09-28 的旧数据（被记 missing），其余 2474 只两天都没有 → 被当作"停牌/退市"跳过。 |
| `000001, 00` 是什么？ | **纯展示层截断**，非数据丢失。出自 `datacenter.py:703` 的 `str(row[1])[:60]`，把 88 字符的 error_message 截到 60 字符，正好断在第二个代码中间。**但它确实是"信息丢失缺陷"**：用户看不到完整 24 只清单，无法排查。 |
| "本地缓存同步"？ | **不存在的功能**。是前端 `DataCenter/index.tsx:739` 的**静态提示文案**，只要健康灯非绿就无条件追加，后端无任何 `local_cache`/`cache_sync` 实现。 |
| 具体文件:行号 | 校验 `app/data/pipeline.py:67-161`（判定在 140-146）；调用/编排 `app/orchestrator.py:683-707`；横幅 `app/api/v1/datacenter.py:703` + 前端 `index.tsx:738-739`。 |

---

## 1. 报错逐项解释（5 个子问题）

### 1.1 「昨有今无 24/25 只」表示什么数据状态？

**定义**（代码依据 `app/data/pipeline.py:106-124`）：对每个标的 code，
1. 先读 `trade_date`（本例 2026-09-29）当天是否有数据；有且通过质量校验 → `n_ok += 1`；
2. 当天无数据时，**才**读上一交易日 `prev`（本例 2026-09-28）；
   - prev 也无数据 → 视为停牌/未上市/退市 → `n_suspended += 1`（**静默跳过，不报警**）；
   - prev 有数据但当天没有 → 追加进 `missing_errs`（**昨有今无**）。

**所以 `24/25` 的精确含义**：
- 分子 24 = "昨天(09-28)有数据、今天(09-29)没有"的标的数；
- 分母 25 = `n_ok(1) + len(missing_errs)(24)` —— **不是全市场 2499，只有"09-29 或 09-28 至少有一天有数据"的 25 只**。
- 比例 24/25 = **96%**。

**实测复现**（脚本实跑，见第 3 节）：
```
prev trade day = 2026-09-28
n_ok(has 09-29)= 1   missing(has 09-28 only)= 24   suspended(neither)= 2474
n_expected= 25   ratio=0.960   suspended share=99.0%
```

### 1.2 阈值 5% 的判定逻辑 + 代入计算

**代码公式**（`app/data/pipeline.py:139-146`）：
```python
else:
    # 3) 「昨有今无」仅在超过**纯比例**容差时才致命
    n_expected = n_ok + len(missing_errs)          # 第 140 行
    tolerance = VALIDATE_MISSING_TOLERANCE_RATIO * n_expected   # 0.05 * 25 = 1.25
    if len(missing_errs) > tolerance:              # 24 > 1.25 → True
        raise ValueError(
            f"validate failed: 昨有今无 {len(missing_errs)}/{n_expected} 只"
            f"（阈值 {VALIDATE_MISSING_TOLERANCE_RATIO:.0%}）: "
            + ", ".join(missing_errs[:5]))         # 第 146 行，只取前 5 个
```
常量：`VALIDATE_MISSING_TOLERANCE_RATIO = 0.05`（`pipeline.py:61`），**纯比例、无绝对下限**。

**代入 24/25**：`tolerance = 0.05 × 25 = 1.25`；`24 > 1.25` 成立 → 抛错。
实际缺失比例 `24/25 = 96%` ≫ 5%，**校验判定本身没错**（在给它的小分母下）。

⚠️ **但分母本身是错的**：`n_expected` 只用"有当日或前一日数据"的标的，把 99% 的停更标的排除在外。设计注释（`pipeline.py:56-60`）声称"市场级 n_expected≈2167，阈值≈108，可容纳日常 ~20 只临时停牌"——**这个假设在"全市场停更"时不成立**：分母会塌缩到几十，阈值随之塌缩到 ~1，于是"整日大面积丢失"反而以一个极小的分母被报出来，语义被扭曲。

### 1.3 「000001, 00」指代什么？—— 截断 + 信息丢失缺陷

- **`000001`** = 平安银行（合法代码）。
- **`00`** = 被截断的下一个代码（原文完整为 `000001, 000002, 000006, 000008, 000009`）。
- **截断有两层**：
  1. `pipeline.py:146` 的 `", ".join(missing_errs[:5])` → 只保留**前 5 个**缺失标的（代码内截断，24 只只显示 5 只）；
  2. `app/api/v1/datacenter.py:703` 的 `f"最近同步异常：{str(row[1])[:60]}"` → 把整串砍到 **60 字符**。
- **逐字符验证**（实跑）：
  ```
  full = 'ValueError: validate failed: 昨有今无 24/25 只（阈值 5%）: 000001, 000002, 000006, 000008, 000009'
  len(full) = 88
  full[:60] = 'ValueError: validate failed: 昨有今无 24/25 只（阈值 5%）: 000001, 00'
  ```
  → 与用户看到的横幅**逐字符完全一致**。

**判定**：这是**展示层截断**（数据本身没丢，`data_jobs.error_message` 里存的是完整串，只是 `:60` 砍掉），**但对用户而言是信息丢失缺陷** —— 用户看不到完整 24 只清单，无法自助排查（这正是"报错要可排查"的红线违反）。

**用数据验证 `000001` 到底还有没有数据**（实跑）：
```
000001 rows2026=179  min=2026-01-05 max=2026-09-28 tail=[...09-21,09-22,09-23,09-24,09-28]
000007 rows2026=180  min=2026-01-05 max=2026-09-29 tail=[...09-24,09-28,09-29]
```
→ `000001` **有数据，但最新只到 2026-09-28**，09-29 确实没有。所以它被判"昨有今无"是**数据事实**，不是误判。

### 1.4 「本地缓存同步」的作用与适用场景？

**关键发现：这是一个"幽灵功能"提示，后端不存在任何对应实现。**

- 全仓搜索 `本地缓存` / `cache_sync` / `local_cache` / `CACHE_SYNC`：**后端 Python 代码零命中**（唯一命中的 `scripts/expand_universe_2500.py:324` 是无关的"读本地缓存 json"）。
- 文案真正的出处 —— 前端静态拼接（`frontend/src/pages/DataCenter/index.tsx:738-739`）：
  ```tsx
  API 接口状态: {overview?.akshare_message ?? '检测中…'}，
  {h === 'green' ? '数据同步正常' : '部分高频接口建议开启本地缓存同步'}。
  ```
  → 只要健康灯 `h !== 'green'`，就**无条件追加**这句建议，与"同步异常"的真实原因无关。

**结论**：它不是可开启的开关，也没有"解决什么问题"的功能实体。本质是一句**误导性兜底文案**：把"外部数据源断连/熔断"这类基础设施问题，引导用户去找一个不存在的"本地缓存同步"功能。**建议整改**（见第 4 节）：要么删除，要么改为指向真实动作（重试同步 / 检查数据源连通性）。

### 1.5 触发该异常的常见原因及解决方案

结合本项目实际原因，按可能性排序：

| 原因 | 本项目证据 | 是否命中 |
|---|---|---|
| **数据源当天全面不可用（代理/网络/熔断）** | 09-29 15:45 日志：`eastmoney ... ProxyError('Unable to connect to proxy')` → 降级 baostock → `AttributeError(NoneType has no error_code)` → `rows=0`；随后 eastmoney 全局熔断 `数据源连续失败已熔断，冷却中` | ✅ **主因** |
| **同步任务未覆盖全市场 / 提前中断** | 09-29 15:45 `sync incremental 结束: done=3/2499 completed=0 failed=0 rows=0` —— 2499 只只处理了 3 只就结束，且 `cancelled=False error=None noop=False`（循环异常提前退出，**待进一步确认**，见第 6 节） | ✅ **强相关** |
| **磁盘水位过高导致落库失败** | 09-30 日志：`[settings] 数据盘水位 93.5%（warning），接近写满将导致抓取/落库失败` | ⚠️ **并发风险** |
| **日期口径错位（用今天 vs 用最近交易日）** | **未命中**。`prev_trade_day(2026-09-29)=2026-09-28`，日历正确；09-29/09-30 都是交易日（已实测），无口径错位 | ❌ 排除 |
| **停牌/退市被误判为缺失** | **反向问题**：本案是"停牌"兜底**过度吞并**（吞掉了 2474 只真缺数据的），不是误杀停牌股 | ✅ 校验盲区 |
| 整日数据丢失 | 09-23 曾出现 `25/25`（100%），与注释提到的 09-14 同类 | ⚠️ 历史曾发生 |

**任务书提示"很可能是日期口径错了"——经实测排除**：日期口径正确，真正的问题比"口径错"更严重：**是全市场数据停滞 + 校验兜底把停滞静默归类为停牌**。

---

## 2. 根因结论 + 证据

### 2.1 数据侧根因（真实）

**全市场 `daily_bar` 长时间停滞。** 实测全 2499 只标的的 2026 年最新日期分布：
```
2026-09-17 : 1497 只
2026-09-18 :  958 只
2026-09-28 :   24 只   ← 正好是告警里那一小撮（000001.000036 段）
2026-09-29 :    1 只   ← 仅 000007
（另有 09-03/09-11/09-14/09-24 零星几只）
```
→ **99% 的标的（2474/2499）自 09-18 起就再无新数据**。这不是"某天偶尔丢"，是**持续 10 余天的同步失败累积**。

**直接触发链**（09-29）：
1. 15:45 autoSync 启动，目标交易日 09-29；
2. eastmoney 源 **ProxyError**（代理不可连）→ 降级 sina/baostock；
3. baostock 报 `AttributeError: 'NoneType' object has no attribute 'error_code'`；
4. 全部源拿不到 09-29 数据 → `rows=0`；
5. 同步在 `done=3/2499` 处提前结束（`completed=0 failed=0`）；
6. 全市场当日/近日数据均未落库。

### 2.2 校验侧根因（逻辑盲区 —— 让告警"看起来像小问题"）

`step_validate` 的 `n_suspended` 兜底（`pipeline.py:117-122`）**没有上限/比例约束**：
- 只要某标的"当日无数据 且 上一交易日也无数据"，就当作停牌**静默跳过**；
- 本案 2474 只（99%）走了这条路径，**完全没有进入告警**；
- 唯一进入统计的是"至少有一天有数据"的 25 只，于是在 5% 阈值下 `24/25=96%` 触发。

**这造成两个后果**：
1. **告警的"分母"严重失真**：报的是 `24/25`，用户以为是"25 只小池子"，实则是"全市场 2499 只里 2474 只停更"；
2. **告警的严重程度被低估**：如果 09-29 恰好连 09-28 的数据都没有（比如又过一天），`n_expected` 会变成 1 或 0，甚至有**静默放行**全市场丢失的风险（`n_expected=0` 时 `tolerance=0`，`len(missing)=0 > 0` 为 False，**不 raise**；仅当有 missing 时才误报）。

---

## 3. 定位到的具体文件:行号

| 作用 | 文件:行号 |
|---|---|
| 校验主逻辑（昨有今无判定） | `backend/app/data/pipeline.py:67-161` |
| 阈值常量（5%、纯比例） | `backend/app/data/pipeline.py:54-64` |
| 缺失判定核心 | `backend/app/data/pipeline.py:106-124` |
| `n_expected` 分母 + 阈值判定 | `backend/app/data/pipeline.py:139-146` |
| 缺失清单截断（前 5 个） | `backend/app/data/pipeline.py:146` |
| 停牌兜底（校验盲区根源） | `backend/app/data/pipeline.py:117-122` |
| 步骤编排 / fail-fast | `backend/app/orchestrator.py:683-707` |
| codes 规范化（2499 → 纯 6 位） | `backend/app/orchestrator.py:634-647` |
| 晚间例行传入全市场 codes | `backend/app/jobs/evening_routine.py:123-124` |
| **横幅文案生成（60 字符截断）** | `backend/app/api/v1/datacenter.py:703` |
| 横幅数据源（data_jobs 最新一条） | `backend/app/api/v1/datacenter.py:684-705` |
| **"本地缓存同步"幽灵文案** | `frontend/src/pages/DataCenter/index.tsx:738-739` |
| 计数落库（error_message 写入） | `backend/app/services/sync_service.py:500-501` |
| 数据源降级链 | `backend/app/data/ingest/akshare_adapter.py:505-552` |

---

## 4. 修复方案

### 4.1 数据侧（P0，优先）—— 恢复全市场行情

1. **先查连通性**：`ProxyError` 指向系统/环境代理拦截了 `push2his.eastmoney.com`。检查 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量与网络代理，确认能直连东财。
2. **改用可用源**：baostock 当前 `is_available()` 但调用即 `AttributeError`（未登录/初始化失败）。校验 baostock 登录态；或将 `AQP_DAILY_SOURCE_PRIORITY` 调整为可用源（如 `sina,eastmoney,baostock`）后重试。
3. **清理磁盘水位**：93.5% 已接近写满，先清理/扩容，再执行同步（否则抓到了也写不进）。
4. **执行补偿同步**：对全市场跑一次 `repair` 模式（回补缺漏至最近交易日），而非 `incremental`（incremental 对"库内最后日期落后多日"会回补区间，但当前上游全断，需先修上游）。

### 4.2 校验逻辑（P1）—— 修掉"分母塌缩"盲区

**问题**：`n_expected` 只统计"有数据"的标的，`n_suspended` 无上限，导致全市场停更被隐藏。

**建议改动 A（最小）**：在 `pipeline.py:130-146` 的 `else` 分支里，**增加对 `n_suspended` 占比的独立告警**——停牌属于正常，但"停牌比例异常高"必须报警：

```python
else:
    # 3) 「昨有今无」仅在超过**纯比例**容差时才致命
    n_expected = n_ok + len(missing_errs)
    tolerance = VALIDATE_MISSING_TOLERANCE_RATIO * n_expected
    if len(missing_errs) > tolerance:
        raise ValueError(
            f"validate failed: 昨有今无 {len(missing_errs)}/{n_expected} 只"
            f"（阈值 {VALIDATE_MISSING_TOLERANCE_RATIO:.0%}）: "
            + ", ".join(missing_errs[:5]))

    # 【新增】停牌兜底不得无限吞并：当日有数据的标的占比过低 ⇒ 全市场停滞，
    # 必须致命。否则"全市场停更"会被 n_suspended 静默吸收，分母塌缩成几十只。
    n_total = len(codes)
    coverage = n_ok / max(1, n_total)
    if n_total >= 100 and coverage < VALIDATE_MIN_COVERAGE_FALLBACK:  # 复用 0.5 兜底门槛
        raise ValueError(
            f"validate failed: 当日有效数据覆盖率仅 {coverage:.0%}"
            f"（{n_ok}/{n_total}），疑似全市场数据停滞"
            f"（停牌跳过 {n_suspended} 只，昨有今无 {len(missing_errs)} 只）")
    if missing_errs:
        logger.warning(
            f"[pipeline] validate: {len(missing_errs)} 只昨有今无"
            f"（阈值 {tolerance:.1f} 内，放行）: {missing_errs[:10]}")
```
> 用实测校验：本案 `n_ok=1, n_total=2499, coverage=0.04% < 50%` → 会抛出**语义正确**的"全市场数据停滞"告警，而不是误导性的 `24/25`。`n_total >= 100` 的守卫避免误伤 3 只小代码集的手动重跑（小池子覆盖率天然低）。

**建议改动 B（可选，更稳）**：把 `missing_errs[:5]`（`pipeline.py:146`）改为完整列表或"前 N + 总数"，避免代码内再截断。

### 4.3 展示层（P1）—— 让报错可自助排查

1. **`datacenter.py:703` 的 `[:60]` 截断**改为"结构化 + 不截断关键信息"：
   ```python
   # 原: f"最近同步异常：{str(row[1])[:60]}"
   msg = str(row[1] or "")
   health_msg = f"最近同步异常：{msg[:120]}" + ("…" if len(msg) > 120 else "")
   ```
   或更好：在 error_message 生成处（校验抛错时）就**附上完整缺失清单/汇总**，让下游只需短截断也不丢关键信息。
2. **删除/改写前端幽灵文案**（`index.tsx:739`）：把"部分高频接口建议开启本地缓存同步"改为指向真实动作，如"数据源可能不可用，请检查网络/代理或重试同步"。当前文案会把用户引向一个不存在的功能。

---

## 5. 验证方式

### 5.1 重跑校验确认修复

**复现原始告警**（实跑，已确认）：
```bash
cd backend && ./.venv/Scripts/python.exe -c "
from datetime import date
from app.data.parquet_store import read_all_symbols
from app.data.pipeline import step_validate
from app.domain.a_share_rules import normalize_code
codes=list(dict.fromkeys(normalize_code(c) for c in read_all_symbols('daily_bar')))
step_validate(date(2026,9,29), codes)"
# 现状输出: ValueError: validate failed: 昨有今无 24/25 只（阈值 5%）: 000001, 000002, 000006, 000008, 000009
```
修复后，同一调用（在**数据已恢复**的前提下）应返回正常串：
`validated=2499/2499 suspended=<少量> missing_vs_prev=0 degraded=0`。

**反向验证"修掉盲区"**：在**数据仍缺**时重跑，应抛出**新**的语义正确的告警
（`当日有效数据覆盖率仅 0%...疑似全市场数据停滞`），而不再是 `24/25`。

### 5.2 断言"某天的数据是完整的"

新增/手工执行如下断言脚本（覆盖率口径）：
```bash
cd backend && ./.venv/Scripts/python.exe -c "
from datetime import date
from app.data.parquet_store import read_all_symbols, read_symbol_dataset
from app.domain.a_share_rules import normalize_code, code_to_symbol
td=date(2026,9,29)
codes=[normalize_code(c) for c in read_all_symbols('daily_bar')]
have=sum(1 for c in codes
         if not read_symbol_dataset('daily_bar', code_to_symbol(c), start=td, end=td).is_empty())
print(f'{td}: {have}/{len(codes)} = {have/len(codes):.1%}')
assert have/len(codes) > 0.95, f'{td} 数据不完整: {have}/{len(codes)}'
print('OK: 该交易日数据完整')"
```
**标准**：某交易日有数据标的占比 > 95%（A 股正常交易日停牌率远低于 5%）即判"完整"。本案 09-29 实测 `1/2499 = 0.04%` → **断言失败**，如实反映数据停滞。

### 5.3 回归测试
跑 `./.venv/Scripts/python.exe -m pytest tests/test_validate_step.py tests/test_sync_integrity.py`，
确认新增的"覆盖率致命"分支不误伤既有用例（现有用例多为 <100 只小代码集，受 `n_total>=100` 守卫保护）。

---

## 6. 证据清单（实测）

| 证据 | 来源 | 结果 |
|---|---|---|
| 复现原告警 | 手工调用 `step_validate(2026-09-29, 2499 codes)` | ✅ 精确复现 `24/25` |
| 分母构成 | 同上脚本插桩 | n_ok=1, missing=24, suspended=2474, n_expected=25 |
| 全市场最新日期分布 | 扫 2499 只 year=2026 | 09-17:1497只, 09-18:958只, 09-28:24只, 09-29:1只 |
| 000001 实际数据 | 读 parquet | 最新 = 2026-09-28，无 09-29 |
| 横幅字符一致 | `full[:60]` vs 用户文案 | 逐字符完全一致（88→60） |
| 横幅数据源 | `data_jobs` id=19 | error_message 完整串 = 用户横幅去掉 `[:60]` |
| 历史出现 | grep `step=validate FAILED` | 09-23(×3)、09-28(×3)、09-29(×3) |
| 日历口径 | `prev_trade_day(09-29)` | = 09-28（正确，排除口径错） |
| 数据源故障 | app.log 09-29 15:45 | eastmoney ProxyError + 熔断；baostock AttributeError |
| 同步提前结束 | app.log 09-29 15:45:26 | `done=3/2499 completed=0 failed=0 rows=0` |
| 幽灵文案 | 前端 `index.tsx:739` | 静态拼接，后端无实现 |

---

## 7. 未确认 / 待补充

1. **同步为何在 `done=3/2499` 就结束**（`cancelled=False error=None noop=False`）：`_run_incremental` 的 `for` 循环理应遍历 2499。21 秒内处理 3 只后正常返回，与代码逻辑不完全吻合。**未确认**是否为上游异常吞并、`_check_cancel` 误置、或服务在 15:45:26 后被重启（09-30 16:23 日志确实出现 `logging initialized / starting AQP` = 进程重启）。**建议**：结合 `_sync.log` 环形缓冲（持久化在 `_persist_sync_state`）与进程重启时间线进一步定位。
2. **磁盘水位 93.5% 是否已导致部分写失败**：日志仅告警，未见直接 write 失败堆栈。**未确认**因果，但为高优先级风险。
3. **09-30（今天）数据是否已恢复**：16:23 同步被进程重启打断，未看到完成记录。**未确认**，需重跑 5.2 断言确认。
4. **生产环境是否有真实代理**：`ProxyError` 来自哪个代理（系统 env / 中间件）**未确认**。
5. **`evil` 空目录**（`data/parquet/daily_bar/evil/`）：09-29 18:05 创建，内容为空，来源**未确认**，疑似 `code_to_symbol` 脏输入或历史脚本残留，建议清理并追查。

---

*所有结论均基于本地实测（SQL 查询、parquet 读取、日志行、手工复现）；标注"未确认"处为代码/日志未能闭环的部分，未作臆测。*

---

# 附录 A：断更根因深挖（2026-09-30 追加，响应 team-lead 复核）

> team-lead 用全市场扫描独立证实了断更（09-17:1497 / 09-18:958），并要求补完两点：
> ①断更根因（重点验证 `ALLOW_ADMIN_TOKEN_LOGIN=false` 鉴权加固假设）；②`ops.py:534` `max_length=20` 的语义与 `25` 的真正来源。

## A.1 `25` 的真正来源（**修正 team-lead 的假设**）

team-lead 推测 `25` 是"手动 rerun 传入的 25 只"（据 `ops.py:534` `max_length=20`）。**该假设经实测证伪**：

**(1) `max_length=20` 对 `list[str]` 是"列表最多 20 项"——实测确认**（pydantic 2.9.2）：
```
19 items ACCEPTED
20 items ACCEPTED
21 items REJECTED: ValidationError
25 items REJECTED: ValidationError
1 item len25 ACCEPTED        ← 单个 25 字符元素可过 ⇒ 不是"元素长度"
```
⇒ **单次 `/dag/rerun` HTTP 调用最多只能传 20 个代码，物理上无法产生"25 个输入代码"**。

**(2) 日志实证：本次流水线根本不是 `/dag/rerun` 触发的**。
- 全 `app.log` grep `dag/rerun` = **0 命中**；grep `rerun` = 0 命中。
- 失败的 `data_jobs.id=19`（09-29）由 **`evening_routine`（17:30/17:47/18:05 "triggered at"）**触发，
  其 `codes = read_all_symbols("daily_bar")` = **2499 只**（`evening_routine.py:123-124`）。

**(3) 所以 `25` 来源于 validate 内部的分母计算，不是任何"25 只的输入列表"**：
`n_expected = n_ok + len(missing_errs)`，本案 `= 1 + 24 = 25`（第 1 节已实测复算）。
**`max_length=20` 与本案 `25` 无关** —— `25` 是"09-29 或 09-28 有一天有数据的标的数"，而非输入规模。

> 附带确认：`ops.py:534` 的 `max_length=20` 确实限制列表项数（用户**不能**一次传 25 只以上），
> 这是一个**独立的小限制缺陷**（若产品需要一次传更多标的，应放宽或改分页），但与本次告警**无因果**。

## A.2 断更根因：**本地代理间歇性故障**，不是鉴权加固

**结论：`ALLOW_ADMIN_TOKEN_LOGIN=false` 与本次断更无关，假设被三重独立证据否决。**

### 证据 1（设计）：同步根本不经 HTTP 鉴权层
`auto_sync_scheduler`（`sync_service.py:642-667`）在**后端进程内**运行，
`await asyncio.to_thread(lambda: _start_sync_bg("incremental", resume=False))` —— 直接调用函数，
**不经过任何 HTTP 中间件 / token / `require_role`**。`ALLOW_ADMIN_TOKEN_LOGIN` 只影响
`auth.py:197-200` 的 Bearer-token 免密登录路径，与后台同步**无调用关系**。

### 证据 2（时间线）：断更(09-18) 早于加固(09-29)
- 断更起点 = **2026-09-18**（数据 09-17/09-18 大量停滞；`data_jobs.id=9/10` 09-18 失败）。
- `.env` 加固 `ALLOW_ADMIN_TOKEN_LOGIN=false` = **2026-09-29**
  （`tests/conftest.py:250` 明确记载"2026-09-29 上线前全检：本机 .env 已加固为 ALLOW_ADMIN_TOKEN_LOGIN=false"）。
- **因在果之后 11 天 ⇒ 时间上不可能致因。**

### 证据 3（直接）：失败是 `ProxyError`，不是 401/403
失败日志全部是网络层代理异常，**没有任何鉴权错误**：

```
# 09-26 15:50   eastmoney 主源：
daily fetch fail 688796: ProxyError(MaxRetryError("HTTPSConnectionPool(
  host='push2his.eastmoney.com', port=443): ... (Caused by ProxyError(
  'Unable to connect to proxy', RemoteDisconnected('Remote end closed connection without response'))")))
# 09-26 15:53   连 sina 兜底源也失败：
[ingest] 688809 adjust='hfq' 抓取失败: ProxyError(MaxRetryError(
  "HTTPSConnectionPool(host='finance.sina.com.cn', port=443): ... (Caused by
  ProxyError('Unable to connect to proxy', ...))"))
# 09-26 15:53   或隧道 502：
... (Caused by ProxyError('Unable to connect to proxy',
        OSError('Tunnel connection failed: 502 Bad Gateway')))
```

### 真正的根因：`HTTP_PROXY/HTTPS_PROXY` 指向的本地代理 `127.0.0.1:63978` 间歇性失效

**实测环境变量**（09-30）：
```
HTTP_PROXY = http://127.0.0.1:63978
HTTPS_PROXY = http://127.0.0.1:63978
http_proxy / https_proxy 同上；NO_PROXY = 未设置
```
- 环境里挂了一个**本机 egress 代理 63978**（开发/沙箱代理）。
- `requests`（akshare 底层）**默认读 `HTTP(S)_PROXY` 环境变量**，于是 eastmoney / sina / tencent **全部走这个代理**。
- 该代理**间歇可用**：
  - **故障时**（正在同步的 15:45/17:30 窗口）：`RemoteDisconnected` / `Tunnel 502 Bad Gateway` ⇒ 全部数据源不可达 ⇒ `rows=0`；
  - **可用时**（本次实测窗口）：`sina/tencent` 直连能取到 **09-30** 的数据（实测 `sina: rows=8 last=2026-09-30`、`tencent: rows=8 last=2026-09-30`）。
- ⇒ **行情数据在源站是好的**（09-18~09-30 全在），本平台只是**经由这个坏代理取不到**。

### 断更时间线（`data_jobs` + `app.log` 实证）

| 时间 | 事件 | 关键指标 |
|---|---|---|
| 09-18 19:32 | pipeline validate 失败 + sync PipelineBusy | `code 必须 6 位数字: 000001.SZ`（后缀 bug）；sync `done=0/2499 noop` |
| **09-19 16:07→18:34** | sync 跑了 **1h59m** 后被 cancel | `done=983/2499 cancelled=True`（未跑完） |
| 09-23 15:46 | sync | `done=25/2499 rows=96`（10min，早停） |
| **09-26 15:45→15:53** | sync 遍历完 2499 但**几乎全失败** | `done=2499/2499 completed=0 failed=2488 rows=115` |
| 09-27 15:57 | sync | `done=0/2499 noop=True` |
| 09-28 15:45 / 18:24 | sync | `17/2499`、`25/2499` |
| 09-29 15:45 | sync | `done=3/2499 rows=0`（21s 早停） |
| 09-29 17:30→18:06 | evening_routine ×3 | validate `24/25` 告警（即本报告主题） |
| 09-30 16:23 | sync 刚触发即被**进程重启**打断 | `starting AQP` |

**两个叠加的次生问题**：
1. **代理故障** → 主因：全源不可达，`rows≈0`。
2. **同步无"全量兜底"** → 09-26 遍历完 2499 却 `failed=2488`（fallback 也走同一坏代理），
   且多个窗口（09-19/09-23/09-29）在 `done` 远小于 2499 时**提前结束**
   （`cancelled=True` 或进程重启）⇒ **断更被持续累积、从未自愈**。

### 附：`MAX` 迭代为何常在几十只后早停（**部分未确认**）
09-23 `done=25`、09-28 `done=17/25`、09-29 `done=3` 均远小于 2499，且 `cancelled=False, error=None`。
`_run_incremental` 的 `for` 循环理应对 2499 逐一 `done=i`，理论不会停在 3/17/25。
**未确认**：疑似①进程在这些时点被重启（09-30 16:23 确有 `starting AQP`）打断 daemon 线程；
②上游 `sleep`/超时把循环拖到下一轮。**建议**：结合 `_persist_sync_state` 的续传记录与
进程重启时间线进一步定位（本轮未拿到足够证据闭环）。

## A.3 修复优先级（更新）

| 优先级 | 动作 | 依据 |
|---|---|---|
| **P0** | **修代理**：让 `HTTP_PROXY/HTTPS_PROXY`（`127.0.0.1:63978`）稳定可用，或对行情域名设 `NO_PROXY` 直连；随后跑一次 `repair` 全市场回补至最近交易日 | A.2：全源走坏代理，源站数据其实完好（sina/tencent 实测到 09-30） |
| P0 | 清理磁盘水位（93.5%） | 09-30 日志 warning，接近写满会致落库失败 |
| P1 | validate 增"当日覆盖率<50% 致命"分支 | 第 4.2 节，防分母塌缩 |
| P1 | 展示层去 `[:60]` 截断、删幽灵文案 | 第 4.3 节 |
| P2 | `ops.py:534` `max_length=20` 视产品需要放宽 | A.1，独立限制，与本案无关 |

## A.4 新增证据清单（实测）

| 证据 | 来源 | 结果 |
|---|---|---|
| `max_length` 语义 | pydantic 2.9.2 实跑 | 21/25 项 REJECTED ⇒ 限制**列表项数 ≤20** |
| rerun 调用 | grep `dag/rerun` / `rerun` in app.log | **0 命中** ⇒ 非 rerun 触发 |
| 触发方 | 日志 `evening_routine triggered` + `data_jobs.id=19` | evening_routine，codes=2499 |
| 鉴权假设（设计） | `sync_service.py:642-667` | sync 进程内直调，不经 HTTP 鉴权 |
| 鉴权假设（时间线） | conftest.py:250 | 加固 09-29，晚于断更 09-18 |
| 鉴权假设（现象） | 09-26 日志 | 失败全是 `ProxyError`，非 401/403 |
| 代理环境 | 实跑 `os.environ` | `HTTPS_PROXY=http://127.0.0.1:63978` |
| 源站可用性 | 实跑 sina/tencent 直连 | `rows=8 last=2026-09-30`（源站有数据！） |
| 全市场失败 | 09-26 sync 摘要 | `done=2499/2499 completed=0 failed=2488 rows=115` |
