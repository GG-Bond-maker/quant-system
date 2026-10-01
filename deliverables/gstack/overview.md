# AQP 上线前全检 · 4 项阻塞修复

**日期**：2026-10-01 ｜ **结论**：🟢 **Go**（4 项 🔴 阻塞全部解除）

---

## 结果一览

| 项 | 缺陷 | 修复 | 验收证据 |
|---|---|---|---|
| **B1** | mypy 19 errors（CI 硬门禁必红）+ 统计日被 `max(date)` 劫持 | `cast` 收窄；`_pick_stat_day` 相对阈值；裁剪补前瞻窗 | mypy → **Success 135 files** |
| **B2** | 市场级资金流硬编码 `push2his`/`push2`（本机整组阻断）⇒ 数据假性缺失 | 新增多源适配器，降级链 `push2test→push2delay→push2his` | 实测 `main_net=−59.27亿`、8 板块；北向休市**正确降级** |
| **B3** | ETF 用 `date.today()` 冒充数据日 | 统一 `today_trade_date_or_last()` | 返回 `2026-09-30`（非今天 10-01） |
| **B4** | `StrategyBacktestRequest` 无 `max_participation` 字段、`_run_single` 不透传 ⇒ **框架策略回测完全无流动性闸门**，系统性高估收益 | 补字段(默认0.05)+透传+解耦摩擦+**入缓存键** | **注入 bug → 2 FAIL（期望0.05实得0.0）→ 恢复转绿** |

---

## 修复中额外关闭的 2 个真缺陷

1. **B4 缓存键缺口**：`max_participation` 未入 `_strategy_cache_key` ⇒ 0.05 与 0.0 在
   600s TTL 内**互取缓存**、返回另一套流动性约束的结果。该字段**本轮才第一次生效**，
   故"不入键"从无害变成有害。
2. **B2 的 monkeypatch 失效**（由**全量套件**抓到 6 failed）：把调用改成
   **模块级 `from ... import`** 后，测试的 `monkeypatch.setattr(ms, "get_akshare", ...)`
   **注入失效** ⇒ 适配器打到真实网络 ⇒ 假红。
   → 改为模块属性访问 `_realtime.fetch_...`，并更正测试桩为东财原始字段名。

> 🔴 **元教训**：把"直连外部源"改成"模块级 import 的适配器"会**静默破坏所有靠
> monkeypatch 注入失败的测试**，单测可能假绿或假红。**只跑单测会漏到发布。**

---

## 门禁与基线

| 门禁 | 结果 |
|---|---|
| `ruff check app tests scripts --select F,E9` | ✅ All checks passed |
| `mypy app/ --ignore-missing-imports` | ✅ Success: no issues found in **135** source files（原 19 errors） |
| 全量 `pytest -q -p no:randomly` | ✅ **1979 passed / 8 skipped / 0 failed**（基线 1971 → +8 = 本轮新增测试） |

---

## 交付物

- 本修复报告：`deliverables/gstack/fix-prelaunch-4blockers-2026-10-01.md`
- 基线全检报告：`deliverables/gstack/pre-launch-fullcheck-2026-10-01.md`
- `CHANGELOG.md` → `[Unreleased] — 2026-10-01 · 上线前全检 4 项阻塞修复`

**变更 10 个文件**：`market.py` / `etf.py` / `backtest.py` / `strategy_base.py` /
`realtime.py` / `market_service.py` / 3 个测试文件 / `CHANGELOG.md`

---

## 转下轮的 🟠 非阻塞遗留

- 生产 `.env` 核对（`ADMIN_TOKEN` / `JWT_SECRET` / `RBAC_ENFORCE` / `ALLOW_ADMIN_TOKEN_LOGIN`）
- `/overview/daily` 补 `background_build`（`market.py:1140`；当前 4.76s < 20s 预算，未触发）
- `etf.py` `sum(x.get("size_yi") or 0)` 静默退化（`us_size_yi=0` 与 `us_count=16` 自相矛盾）
- 外呼全局串行（`_throttle` 持锁 sleep）+ `stock_zh_a_spot_em` 重复调用

⚠️ **B4 行为影响提示**：新增字段默认 0.05 会改变既有框架策略回测的成交笔数/数量结构
（修复前是错的），历史结果与旧版**不可比**，发布说明需提示。
