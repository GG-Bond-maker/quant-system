# Alpha Quant Platform · 全检问题修复与验证报告

**日期**：2026-09-30
**场景**：全流程交付（修复实施 + 实测验证）
**参与成员**：安全官（安全卫士）· 调查员（排障手）· 主理人（收口）
**工作区**：`D:\Python_Project\Alpha Quant Platform`
**前置报告**：`pre-launch-check-final-2026-09-30.md`（🔴 No-Go，3 项阻塞）
**本报告结论**：🟢 **Go（阻塞项已全部清除）**

---

## 📌 TL;DR（执行摘要）

- **整体结论：🟢 Go**。前报告 **3 项阻塞项全部清除**，另修复 4 项 P1。
- **全量回归实测**：`1913 passed, 8 skipped, 0 failed`（406s），`PYTEST_EXIT=0`。
  对比修复前 `3 failed, 1894 passed, 9 errors` ⇒ **零回归 + 净修复**。
- **B-1（验收数字不成立）已消解**：本报告给出了**可复现的全绿证据链**，且新增 CI 步骤
  让「环境门禁先于测试」成为机器强制，不再依赖人工判断。
- **B-1b（venv 残缺）已修复**：`pip check` → `No broken requirements found.`。
  一并清掉 **9 个包各两套 dist-info 并存**的隐蔽残留（前报告未发现）。
- **B-2（mirror/status 冷扫 >120s）已修复**：改走 `_gated_scan`，与同文件其它端点口径一致。
- **共 6 次提交**，全部经实测验证；所有环境改动均**先 `mv` 到备份**（可逆）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| **Go / No-Go** | 🟢 **Go（阻塞项已清除）** |
| 阻塞项清除 | **3 / 3** |
| P1 修复 | **4 / 4**（P1-a 归档逃逸 · P1-b 专用池 · P1-c 明文泄漏 · P1-d docs+RBAC） |
| 全量回归 | **1913 passed / 8 skipped / 0 failed**（`PYTEST_EXIT=0`） |
| `pip check` | **无输出**（原 4 项不一致全清） |
| ruff 门禁 | `All checks passed!` |
| 提交数 | 6（`add4baf` `45b2b72` `8531597` `e98cd7d` `4e1a77d` `e04a9f5`） |
| 遗留 | 105 处 `asyncio.to_thread`（非上线路径）· 零安全事件日志（P2，未修） |

---

## 1. 各成员核心结论

### 🛡️ 安全官（OWASP + STRIDE 视角）

- 本阶段主修 **3 项**：归档逃逸（P0-a）、prod 明文日志（P1-c）、prod docs/RBAC（P1-d）。
- 核心判断：三项均为「**配置/边界类**」缺陷，共同点是**代码本身没错，但默认值或
  部署侧配置让防护失效**。例如 `diagnose=True` 只在 `DEBUG` 下关闭，
  而 `DEBUG` 在 prod 可被误设 ⇒ 防护必须**按 ENV 硬性收口**，不能只靠一个开关。
- 关键建议：`docker-compose` 遗漏 env 是本次教训的**结构性根因**
  —— 修复写在代码里、容器却吃默认值，等于没修。已在 compose 补齐 4 个关键键。

### 🔧 调查员（调试与根因）

- 核心判断：**B-1b 的「`mypy` 空壳」结论是误判，必须订正。**
  实测 `mypy 2.3.1 (compiled: yes)`、`metadata.files=1802`、`mypy.api.run` 可用
  ⇒ 它是 **mypyc 编译版**，`dir(mypy)` **天然为 0**。
  用 `len(dir(mod))` 判空壳对编译扩展包会**系统性误报**。
- 已把该教训写回技能 `env-changing-vs-corrupted`（含正确判据三选一）。
- 关键建议：**判据要选「与包的实现方式无关」的观测量**
  —— 功能探针 / `metadata.files` 优于数属性名。

---

## 2. 综合修复发现（按阻塞项编号）

| # | 严重度 | 类别 | 位置 | 问题 | 修复 | 验证证据 |
|---|--------|------|------|------|------|---------|
| P0-a | 🔴 | 安全 | `backend/scripts/backup.py` | `tar.extractall` 裸调用 ⇒ symlink/hardlink 成员可穿越写出目录（CVE-2007-4559 类） | ① `_check_member_paths` 拒绝 symlink/hardlink/非普通文件；② `extractall(filter="data")`（3.12+） | PoC 构造含 symlink 归档 → `✅ 已拒绝: 归档包含链接类成员（禁止）: sqlite`；正常归档放行 |
| B-1b | 🔴 | 依赖 | `backend/.venv` | `pip check` 4 项不一致；9 个包各两套 dist-info 并存 | 移出孤儿 `openai`；`--force-reinstall jsonpath`；按 pin/mtime 判据移除 9 套多余 dist-info | `pip check` → `No broken requirements found.` |
| B-2 | 🔴 | 性能 | `datacenter.py:1572` | `/datacenter/mirror/status` 冷扫 >120s（不进闸门 / 不吃 manifest 快路径 / 落 22 槽默认池） | 改走 `_gated_scan(..., scan_needed=_parquet_rescan_needed)` | 与同文件其它端点口径一致；热路径 0.034s |
| P1-b | 🟠 | 性能 | `market.py` ×6 / `etf.py` ×8 | `asyncio.to_thread` 落 22 槽默认池 ⇒ `compute_slot` 闸门失效 | 全部改 `run_in_executor(get_compute_pool(), ...)` | market/etf `to_thread` 已清零 |
| P1-c | 🟠 | 安全 | `logging.py` + `docker-compose.yml` | prod 控制台 `diagnose=True` 打印局部变量（含 token） | `_console_diagnose = DEBUG and ENV != "prod"`；compose 补 `DEBUG`/`LOGURU_DIAGNOSE` | 真值表：dev/true→T、dev/false→F、**prod/true→F**、prod/false→F |
| P1-d | 🟠 | 安全 | `main.py` + `config.py` + compose | ① prod 无条件暴露 `/docs`/`/redoc`/`/openapi.json`；② prod 允许 `RBAC_ENFORCE=false` | ① `_docs_paths()` 按 ENV 收口；② prod 校验 RBAC，需逃生舱 `AQP_ALLOW_UNENFORCED_RBAC=1` | prod→三路径全 `None`；dev→正常；RBAC 无逃生舱→fail-fast，有→放行 |
| — | 🟢 | 门禁 | `scripts/check_deps.py` + `tests/test_dependency_integrity.py` + `ci.yml` | 环境损坏对测试**完全隐形** | 新增三层门禁（pip check / import 冒烟+探针 / dist-info 唯一）+ pin 版本对账，CI 中**先于 pytest** | 门禁 exit=0；pytest 4 passed；CI YAML 校验通过 |

---

## 3. 全量回归实测（B-1 收口证据）

```
============================= test session starts =============================
collected 1921 items
...
========== 1913 passed, 8 skipped, 217 warnings in 406.39s (0:06:46) ==========
```

| 指标 | 修复前（前报告实测） | 修复后（本报告实测） |
|---|---|---|
| passed | 1894 | **1913** |
| failed | **3** | **0** ✅ |
| errors | **9** | **0** ✅ |
| skipped | 8 | 8 |
| `PYTEST_EXIT` | **1** | **0** ✅ |

> 期间唯一出现的 1 次失败（`test_runtime_safety.py::test_prod_baseline_passes`）
> 已定性为**新增 RBAC 闸门在正确工作**（该测试的 `_prod()` 基线未设
> `RBAC_ENFORCE=True`），并已适配（`4e1a77d`），非回归缺陷。

### 门禁与静态检查

| 检查 | 结果 |
|---|---|
| `pip check` | `No broken requirements found.` |
| `python scripts/check_deps.py` | ✅ 三层全过（exit=0） |
| `pytest tests/test_dependency_integrity.py` | 4 passed |
| `pytest tests/test_prod_config_guard.py` | 10 passed |
| `pytest tests/test_runtime_safety.py` | 14 passed |
| `ruff check app tests scripts --select F,E9` | `All checks passed!` |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 状态 |
|---|------|--------|--------|------|
| 1 | 归档还原加固（拒绝链接成员 + `filter="data"`） | 后端 | P0 | ✅ 完成 |
| 2 | 清理 venv 残留 + 依赖完整性门禁入 CI | 后端/CI | P0 | ✅ 完成 |
| 3 | `/datacenter/mirror/status` 并入重扫闸门 | 后端 | P0 | ✅ 完成 |
| 4 | prod 收口 docs / RBAC / 明文日志 + compose 补 env | 后端/运维 | P0 | ✅ 完成 |
| 5 | market/etf 专用池迁移（14 处） | 后端 | P1 | ✅ 完成 |
| 6 | 全量回归复跑（零失败证据） | QA | P0 | ✅ 完成（1913/0/8） |
| 7 | `auth.py` 安全事件日志（OWASP A09 盲区） | 后端 | P2 | ⬜ 未做 |
| 8 | 剩余 105 处 `asyncio.to_thread` 增量迁移 | 后端 | P2 | ⬜ 未做 |
| 9 | `errors` 数组限长 / `facade/core/main.py:31` 加鉴权 | 后端 | P2 | ⬜ 未做 |
| 10 | 推送 6 个提交到 origin（需人工凭据） | 用户 | P1 | ⬜ 待用户操作 |

---

## ⚠️ 待完善 / 已知局限

- **`mypy` 判据订正**：前报告「`mypy` 空壳」为**误判**。它是 mypyc 编译版
  （`compiled: yes`，1802 文件），`dir()` 天然为 0。已在门禁中改用
  **子模块导入探针**（`importlib.import_module("mypy.api")` 后校验 `run`）。
- **门禁 `[C]` 的坑**：`importlib.metadata.distributions()` 在**同一进程内返回值
  会不一致**，曾导致一次误报 13 个重复（真实 9 个）。故改为**直接扫盘**
  `site-packages/*.dist-info/`。
- **未真正修复**：`openai` 已被移出而非「修好」。判断依据是**无任何包依赖它**
  且其声明的 `httpx2`/`jiter` 在 PyPI 不存在 ⇒ 它是**畸形/孤儿包**，移除是正确处理。
  若后续需要 OpenAI SDK，应显式加入 `requirements.txt` 并锁定真实版本。
- **105 处 `asyncio.to_thread`** 仍在（不在本次上线路径），建议排期增量迁移。
- **零安全事件日志**（`auth.py` 无 `logger.` 调用）未修，属 P2。
- 环境侧备份：`D:\tmp_aqp_perf\b1b_backup_20260930\`（含 openai / 陈旧 aiohttp
  dist-info / 9 套多余 dist-info / 裸 jsonpath.py）。**建议确认无误后再清理。**

---

## 📚 成员产出索引

- 安全官（`gstack-security-officer`）：P0-a / P1-c / P1-d 修复与真值表实测
- 调查员（`gstack-investigator`）：B-1b 归因订正（mypyc 判据）+ 环境残留清点
- 主理人收口：B-1 全量回归证据 · B-2 修复 · 门禁落地 · 报告汇编

---

## 附录：提交清单

| hash | 内容 | 验证 |
|---|---|---|
| `add4baf` | B-1b 依赖完整性门禁 + 环境残留清理 | `pip check` 干净；门禁 exit=0 |
| `45b2b72` | P0-a 归档逃逸加固 | symlink PoC 被拒 |
| `8531597` | P1-c/P1-d prod 收口 + compose 补 env | 真值表 + prod/dev 实测 |
| `e98cd7d` | P1-b/B-2 专用池 14 处 + mirror 闸门 | market/etf to_thread 清零 |
| `4e1a77d` | 适配 prod RBAC fail-fast 新契约 | 14 passed |
| `e04a9f5` | 全检报告与成员原始产出归档 | — |

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
