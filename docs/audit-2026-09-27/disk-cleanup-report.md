# 磁盘水位治理报告（Disk Cleanup Report）

- **执行人**：寇豆码（Kou）· 工程师 · 磁盘水位治理
- **日期**：2026-09-27
- **范围**：`D:\Python_Project\Alpha Quant Platform` 及其所在 D 盘
- **原则**：只读优先；仅清理本轮审计（2026-09-27）自产临时产物；不确定一律保留并列入待确认清单

---

## 一、水位现状

| 时间点 | 盘符 | 总容量 | 已用 | 可用 | 使用率 |
|---|---|---|---|---|---|
| 清理前 | D: | 652 GB | 608 GB | 45 GB | **94%** |
| 清理后 | D: | 652 GB | 608 GB | 45 GB | **94%** |

- 审计提到的 **93.3% 现已升至 94%**（可用 45 GB），对应盘符为 **D:**（项目所在盘）。
- 另有 C: 盘 301 GB / 已用 267 GB / 可用 34 GB（89%），非本次目标盘。
- **关键结论**：D 盘 608 GB 已用空间中，**本项目仅占约 7.7 GB（≈1.3%）**。
  水位偏高的主因在项目之外（见第五节）。因此本轮清理对整体水位影响可忽略，
  治理重点应是**项目外的用户数据**与**项目内的可归档/可轮转资产**。

---

## 二、本轮实际删除清单（已执行，可逆性说明见下）

> 说明：以下均为 2026-09-27 本轮审计自产、且被 `.gitignore` 忽略或未被任何报告引用的临时产物。
> 删除前逐项列出完整路径与体积，删除后复核。

| # | 路径 | 体积 | 类型 | 备注 |
|---|---|---|---|---|
| 1 | `frontend/dist-kou-verify/` | 1963 KB | 验证构建产物 | gitignored |
| 2 | `frontend/dist-verify-fix/` | 1963 KB | 验证构建产物 | gitignored |
| 3 | `frontend/dist-verify-fix2/` | 1967 KB | 验证构建产物 | gitignored |
| 4 | `frontend/dist-alerts-verify/` | 0 KB（空目录） | 验证构建产物 | gitignored |
| 5 | `frontend/dist-alerts-verify2/` | 1975 KB | 验证构建产物 | gitignored |
| 6 | `frontend/dist-qa-verify/` | 1963 KB | 验证构建产物 | gitignored |
| 7 | `frontend/dist-r2-verify/` | 1971 KB | 验证构建产物 | gitignored |
| 8 | `frontend/dist-yan-regression/` | 1967 KB | 验证构建产物 | gitignored |
| 9 | `frontend/vite.config.ts.timestamp-*.mjs` × 10 | ≈34 KB | Vite 临时配置 | 仅删 2026-09-27 生成者 |
| 10 | `docs/audit-2026-09-27/patch_csv.py` | 3334 B | 一次性脚本 | 无报告引用 |
| 11 | `docs/audit-2026-09-27/_r2_shot.mjs` | 2022 B | 一次性脚本 | 无报告引用 |
| 12 | `docs/audit-2026-09-27/frontend-build2.log` | 5316 B | 中间日志 | 无报告引用 |
| 13 | `docs/audit-2026-09-27/frontend-dev.log` | 480 B | 中间日志 | 无报告引用 |
| 14 | `docs/audit-2026-09-27/_r3_*.txt` × 7 | ≈7.7 KB | 中间日志 | 无报告引用 |
| 15 | `scripts/smoke_probe.py` | 11688 B | 本轮新增探测脚本 | 未被 git 跟踪、无报告引用 |

**本轮删除合计：约 13.5 MB**

### 复核
- 删除后 `frontend/` 仅剩 `dist/`（主构建，保留）与 `dist-audit/`（上一轮产物，见待确认）。
- `docs/audit-2026-09-27/` 下 **全部 `.md` 报告、`.csv` 数据、`openapi.json`、`.png` 截图均完整保留**。
- 未触碰 `data/`、`backend/data/`、任何 `.db`/`.sqlite`、源码、`node_modules`、`.venv`。

### ⚠️ 回滚提示
本轮删除均为可再生成产物（构建产物可 `npm run build` 重建；vite 临时文件自动生成）。
`scripts/smoke_probe.py` 若需恢复，请告知——该脚本为本轮 QA 自产、未提交 git，删除后需重写。

---

## 三、需用户确认才能删的候选（本轮**未**删除）

> 以下均有明确体积收益，但来源早于本轮、或属于证据/资产，**必须由用户确认后再动**。

### A. 项目内可安全回收（建议优先处理）

| 路径 | 体积 | 说明 | 风险 |
|---|---|---|---|
| `backend/backups/` | **61 MB**（213 个 `aqp_backup_*.db`） | 每日自动备份库快照，最老可追溯到 08-31 | 低。保留最近 N 份、清理历史即可；但删前确认无恢复演练依赖 |
| `backend/.mypy_cache/` + `.mypy_cache/` | **117 MB**（59+58） | mypy 静态检查缓存 | 极低，可随时重建 |
| `data/_purged_pre2022/` | **49 MB** | 2022 年前数据的 purge 落地区 | 中。**属行情数据**，删除前务必确认已归档到别处/确认不再需要 |
| `data/_backup_universe_daily_bt_20260921/` | **39 MB** | 9-21 回测用 universe 备份 | 中。确认回测已结束可删 |
| `backend/logs/` | **31 MB** | 其中 `app.json.2026-08-29_*.log` 单文件 **20 MB**（已轮转的旧日志） | 低。旧轮转日志可删 |
| `backend/.tmp_testrun/` `.tmp_probe_s4/` `.tmp_b3a/` `.tmp_b2/` | ≈18 MB | 9-21～9-22 审计临时目录 | 低，但**非本轮**产物，按红线未删 |
| `frontend/dist-audit/` | 2 MB | 9-11 审计验证构建 | 极低，gitignored |
| `frontend/vite.config.ts.timestamp-*.mjs`（旧 11 个） | ≈40 KB | 9-03～9-15 残留 | 极低 |
| 根目录散落日志 `backend-run.before-fix.log`(780KB)、`backend_qa.log`、`pytest-*.log`、`qa-*.log` | ≈1.2 MB | 历史运行日志 | 低 |
| `data/_backup_teamlead_pre2024/` | 2 MB | 9-11 数据库备份 | 中，含 `.db` 备份 |

**A 类合计约 320 MB。**

### B. 项目外 D 盘（体积大，属用户个人数据，**强烈建议用户自行处置**）

| 路径 | 体积 | 说明 |
|---|---|---|
| `D:\tmp_gitfix\` | **470 MB** | AQP 相关 git 修复临时目录（9-23） |
| `D:\tmp_gitrecover\` | **207 MB** | git 恢复临时目录（9-18） |
| `D:\tmp_gitrecover_0915\` | 10 MB | git 恢复临时目录 |
| `D:\tmp_aqp\` | 10 MB | AQP 临时目录（9-19） |
| `D:\tmp\`、`D:\tmp_qa_verify\` | 6 MB | 临时目录 |
| `D:\aqp-torch-dl\`、`D:\aqp-t16-curl\`、`D:\aqp-t15-wip-backup\` | 各 1 MB | AQP 历史临时目录 |
| `D:\node-v24.10.0-x64.msi` | 32 MB | 安装包，装完可删 |
| `D:\tmp_g*.out`、`D:\tmp_h*.out`、`D:\tmp_*.out` | ≈60 KB | 9-19 探测输出碎片 |

**B 类（AQP 相关临时）合计约 700 MB**，均可由用户确认后清理。

### C. 证据类（**本轮明确保留**）

`docs/audit-2026-09-27/` 下的其余脚本与日志（`probe.py`、`retest.py`、`regression_*.py/.cjs`、
`probe_write_endpoints.py`、`_r2_*.py/.mjs`、`_r3_endpoint_probe.py`、`_r3_helper_unit.py`、
`backend-kou.log`、`probe.log`、`retest.log`、`_r3_pytest*.out/txt` 等）
**均被本轮 `.md` 报告正文引用为证据**，且审计仍在进行中（含未完成任务 #33）。
删除将破坏可追溯性，故**保留**；建议待审计全部收尾、报告定稿后再统一清理。

另：`docs/audit-2026-09-27/lineage_cold_concurrency_probe.py`、`lineage_warm_share_probe.py`
正被**进行中的 lineage 修复任务（#33）**使用，**严禁删除**。

---

## 四、项目空间占用 TOP 10（仅统计，未删）

| 排名 | 目录 | 体积 | 性质 | 处置建议 |
|---|---|---|---|---|
| 1 | `backend/.venv/` | 4806 MB | 虚拟环境 | **保留**（必需） |
| 2 | `data/parquet/` | 2274 MB | 行情数据（约 4 万个 parquet） | **保留**（核心资产） |
| 3 | `frontend/node_modules/` | 165 MB | 依赖 | **保留** |
| 4 | `backend/backups/` | 61 MB | 数据库备份 ×213 | 见 A 类，建议轮转 |
| 5 | `backend/.mypy_cache/` | 59 MB | 静态检查缓存 | 可清（A 类） |
| 6 | `.mypy_cache/`（根） | 58 MB | 静态检查缓存 | 可清（A 类） |
| 7 | `data/_purged_pre2022/` | 49 MB | 行情 purge 落地区 | 需确认（A 类） |
| 8 | `data/_backup_universe_daily_bt_20260921/` | 39 MB | 回测备份 | 需确认（A 类） |
| 9 | `.git/` | 39 MB | 版本库 | 保留 |
| 10 | `backend/logs/` | 31 MB | 运行日志（含 20 MB 旧轮转日志） | 建议轮转 |

> 项目根合计约 **7.7 GB**；`backend/.venv` + `data/parquet` 两项即占 **7.0 GB（91%）**，均为不可删资产。

---

## 五、水位偏高的根因

D 盘 608 GB 已用中，项目仅 7.7 GB。**约 600 GB 来自项目之外的用户数据**，
主要是：游戏与平台（`三角洲行动`、`元神`、`QQ飞车`、`WeGameApps`、`Stean`）、
数据/开发环境（`anaconda3`、`ollama`、`Docker`、`虚拟机`、`vmware`、`llama`、`ComfyUI`）、
聊天/网盘缓存（`xwechat_files`、`Weixin`、`微信`、`BaiduNetdiskDownload`、`迅雷下载`）、
以及历史临时目录（见 B 类）。

> 注：上述大目录逐个 `du` 体积统计耗时过长（单目录即超 4 分钟），未逐一完成；
> 如需精确定位 TOP 占用，建议在**用户机器上**用 WizTree / TreeSize 之类工具做一次全盘扫描。

**结论：仅靠清理项目内文件无法显著改善 D 盘水位**，必须由用户处置项目外的个人数据。

---

## 六、后续建议

1. **日志轮转**：`backend/logs/` 已有轮转机制，但旧轮转文件（如 `app.json.2026-08-29_*.log` 20 MB）
   未自动清理。建议增加保留策略（如仅留最近 7 天 / 最多 5 份），并把 `logging` 的
   `backupCount` 显式配置到位。
2. **备份轮转**：`backend/backups/` 213 份 `.db` 备份应按时间/份数保留（如保留最近 7 份 + 每周 1 份），
   预计可回收 ≈55 MB。
3. **缓存目录**：`.mypy_cache`（117 MB）可纳入 `.gitignore` 之外的清理脚本，或定期 `--no-incremental` 后删除。
4. **parquet 冷数据归档**：`data/parquet` 2274 MB 是核心资产，但其中 2022 年前的冷数据
   （现落于 `data/_purged_pre2022` 49 MB）可考虑压缩归档（parquet+zstd / 打包）后移出热盘。
   **任何涉及 `data/` 的操作必须先备份、且需用户明确授权。**
5. **构建产物治理**：`frontend/dist-*` 验证构建已纳入 `.gitignore`，建议在 CI/脚本中
   每次验证前 `rm -rf dist-*`（仅限 `dist-*`，勿伤 `dist/`），避免累积。
6. **项目外清理**：建议用户用磁盘分析工具扫描 D 盘，优先清理 B 类临时目录（≈700 MB）与游戏/缓存。

---

## 七、安全声明

本轮严格遵守只读优先红线：
- ✅ 仅删除 2026-09-27 本轮自产、且无报告引用的临时产物；
- ✅ 未删除任何 `data/`、`backend/data/`、数据库、源码、`node_modules`、`.venv`；
- ✅ 未使用通配符批量删未知目录，所有删除均按显式完整路径逐项执行；
- ✅ 所有证据类文件与早于本轮的产物一律保留，并列入待确认清单。
