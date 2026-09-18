# 诊断报告：生产选股榜（/screener）返回 0 条

- 日期：2026-09-15
- 结论类型：**数据未物化（运维/流水线缺陷）**，非选股接口代码缺陷
- 严重度：**P0**（生产主功能空榜 + 全市场覆盖被静默砍掉 771 只）

---

## 一、症状

前端选股页展示为空，`GET /api/v1/screener` 返回 0 条。

## 二、证据链（逐环节可复现）

| 环节 | 落盘位置 | 09-14 实际 | 对照 |
|---|---|---|---|
| 行情（前复权） | `daily_bar_hfq/symbol=*/year=2026.snappy.parquet` | 仅 **23** 只标的有 09-14/09-15 行 | 09-11 有 1747 只 |
| 特征 | `features/version=alpha_basic_v2g/year=2026.parquet` | 09-14 = **1 行**（`000050.SZ`） | 09-11 = 1729 行 |
| 预测 | `predictions/date=20260914.parquet` | **1 行**（pred_score=-0.0559） | 09-04 = 1127 行 |
| 快照 | `screener_snapshot` | 2 行（board=all/main），`close/pct/amount` 全 **NULL** | 09-04 = 600 行 |
| 接口 | `/screener` | **0 条**（NULL 行情行被行情校验剔除） | — |

即：`1 只 → 1 行特征 → 1 行预测 → 1 行无行情快照 → 被过滤 → 空榜`。

## 三、根因

### 根因 A（直接原因）：2026-09-14 的增量同步没有真正执行

`data_jobs`：

```
sync_incremental  2026-09-14  SUCCESS  duration_ms=1109
```

同期有 **2476 只**标的的最后交易日 ≤ 2026-09-11，全部需要联网回补。而实测「逐标的读一个年分区」的耗时外推即 **2.5 s**（300 文件 0.30 s → 2499 文件 2.5 s），1.1 s 连把 2499 只遍历一遍都不够，更不可能完成 2476 次网络抓取（正常一次 ≈ 0.9 s，09-07 的同类任务耗时 **36 分钟**）。

→ 该任务记录为 **假成功（SUCCESS with no work）**。晚间流水线（`evening_routine`，steps 不含 `update_daily`）随后只能对已有的 1 只标的建特征。

### 根因 B（代码缺陷 P0）：增量特征构建不回填「最新日」

`backend/app/orchestrator.py :: step_build_features`：

```python
d_last   = existing["date"].max()
new_rows = feats_new.filter(pl.col("date") > d_last)   # ← 严格大于
merged   = pl.concat([existing, new_rows], ...)
```

一旦某日被**部分写入**（09-14 先落 1 行），`d_last` 即锁定为 09-14；此后无论补多少行情，`date > d_last` 都不会再产生 09-14 的行 —— **该交易日永久停在残缺状态**，重跑流水线也修不好（除非全量重算）。

这解释了为什么 09-14 是「1 行」而不是后来的「23 行」：后补的 22 只标的永远进不去。

### 根因 C（代码缺陷 P0）：manifest 少登记 771 只标的，全市场覆盖被静默砍掉

`step_build_features` 用 `read_all_symbols("daily_bar_hfq")` 取标的池，而该函数优先信任 `data/parquet/.manifest.json`：

| 来源 | `daily_bar_hfq` 标的数 |
|---|---|
| 磁盘 `symbol=*` 目录（全部 rows>0，无空目录） | **2500** |
| `.manifest.json` 登记 | **1729** |
| 缺失（有数据但不登记） | **771**（如 `000016.SZ` `000759.SZ` `000776.SZ` `000792.SZ`） |

**交叉验证**：`features` 09-11 的行数 = **1729**，与 manifest 计数完全吻合 → 证明特征构建的标的池就是被 manifest 卡在 1729。

根因：manifest 只在写路径 `_manifest_record()` 增量登记，且仅当 dataset 键**缺失**时才全量重扫（`list_symbols_with_data`）。凡绕过 `write_partition` 的写入（离线重建 / 扩容脚本）都不会登记，且永不自动修复。

### 根因 D（代码缺陷 P1）：同步任务缺少「实际工作量」断言

`backend/app/services/sync_service.py :: _sync_worker` 只在「未 cancel 且未抛异常」时记 `SUCCESS`，**不校验实际落库行数 / 完成标的数**。因此 0 产出的空跑也会点亮 `/overview` 健康灯、写进 AI 日报与 task-stats，掩盖真实故障。

## 四、附带发现（非本次空榜主因，但需记录）

- `predictions` 只对 `trade_date` 单日推理：09-08 ~ 09-11 有特征但**无预测分区**（`predictions/` 缺 `date=20260908..20260911`），故选股榜天然只有「目标日」一条，历史断档。设计如此，但叠加根因 A 会放大空榜。
- `screener_snapshot` 现存 09-14 行的 `risk='high'`、`stats_json.high_risk` 仍是旧字段（写入于改名之前），已由 `_translate_signal_strength()` 兼容映射，UI 显示正常。
- 快照 `strategy` 字段仍为 `alpha_basic_v1`，而该批预测的 `feature_version=alpha_basic_v2g`；`step_screener_dump` 已改为从 predictions 分区读取真实 fv，需重跑后才会一致。

## 五、修复建议（按优先级）

1. **P0-B 增量特征回填**：`step_build_features` 改为「先删除 `>= guard_start` 区间的旧行再合并新行」，或对 `date == d_last` 的日期按 `(date, symbol)` 做**补齐式**合并（保留已有 + 补入新 symbol），保证最新日可被补全。
2. **P0-C manifest 自愈**：`list_symbols_with_data` 增加「manifest 条目数 < 目录数」的一致性校验（或 `rows>0` 集合为空/偏少时强制重扫）；给绕过写路径的脚本加 `manifest_invalidate()` 调用。
3. **P1-D 同步真实性断言**：`_sync_worker` 在写 `SUCCESS` 前断言「本次落库行数 > 0 或 skipped-completed 数 == 总数」，否则记 `FAILED/NOOP` 并在日志中明示。
4. **P1-A 数据回补（运维）**：跑一次全市场增量同步（约 2500 只，按 0.9 s/只估计 ≥ 35 min），再重跑 `build_features → infer → screener_dump`。
5. **P2 覆盖率守门**：`step_infer` 增加「当日特征行数 ≥ 全市场 N%」的下限守卫，低于阈值时判 `FAILED` 而非写出 1 行预测污染快照。

## 六、复现命令

```bash
cd backend && ./.venv/Scripts/python.exe -c "
import polars as pl,glob
fe=pl.read_parquet('../data/parquet/features/version=alpha_basic_v2g/year=2026.parquet',columns=['date','symbol'])
print(fe.group_by('date').agg(pl.len().alias('n')).sort('date').tail(8))
"
```
