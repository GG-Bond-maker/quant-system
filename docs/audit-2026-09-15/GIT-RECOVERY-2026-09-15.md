# Git 仓库恢复报告（第四次外部破坏 · 2026-09-15 23:16）

- 事故时间：**2026-09-15 23:16（本地）** / 15:16 UTC
- 发现时间：2026-09-15 23:25（`git status` 报 `fatal: not a git repository`）
- 恢复完成：2026-09-16 00:07
- 结果：**零丢失**。master lineage 完整、头部 commits 全可达、工作区 119 项未提交改动全部保留

---

## 一、损坏范围

| 项目 | 状态 |
|---|---|
| `.git/refs/`（整个目录） | **被删** |
| `.git/logs/refs/**`（除 `logs/HEAD`） | **被删** |
| `.git/objects/**` | **部分被删**（回收站里有大量 loose object 与 pack 文件） |
| `.git/HEAD` / `config` / `index` / `FETCH_HEAD` / `ORIG_HEAD` | 幸存 |
| 工作区文件（含 `data/`） | **未受影响**（见 §4） |

`git status` 直接报 `not a git repository`——因为 HEAD 指向 `refs/heads/master` 而 refs 目录不存在。

## 二、取证：这是一个**反复发生**的破坏进程

对回收站 `$I` 元数据的 FILETIME（UTC + 8h = 本地）做时间分布，`.git` 相关条目共 **6183** 条：

| 本地时间 | 删除条目数 |
|---|---|
| 2026-09-12 23:24 | 1202 |
| 2026-09-14 15:51 | 1184 |
| **2026-09-15 23:16** | **1158** |
| 2026-09-14 15:27 | 1057 |
| 2026-09-13 23:44 | 1056 |
| 2026-09-13 02:12 | 99 |
| 2026-09-14 15:50 | 77 |
| 2026-09-13 01:00 | 50 |

即：**09-12、09-13×2、09-14×3、09-15** 共至少 7 次同类事件。与既有记录吻合（`2026-09-12 毁 .git`、`09-13 02:34 批删 data`、`09-13 23:44 再毁 .git`）。

**本次（23:16）的 1158 条中，非 `.git` 路径数 = 0** —— 相比早期事件不再波及 `data/` 的历史/u数据，纯针对 `.git`。

回收站还原统计：`missing=1338 / payload_isdir=877 / restored=1374 / size_diff=36 / already_ok=3932`。
取证脚本：`D:/tmp_gitrecover_0915/restore_git_from_recycle.py`（dry-run / `--apply` 两档）。

## 三、恢复步骤

1. **保全**：`cp -r .git/. D:/tmp_gitrecover_0915/pre/`（仓库外，7.6M）。
2. **回收站还原**：按 `$I` 记录的原始路径回填 object / pack / refs（脚本见上）。
   - 坑：`$I` 里的路径分隔符在 Git Bash heredoc 场景下会被转成 `//`，必须 `replace("/","\\")` + 折叠重复分隔符后再比对，否则**全部条目匹配不到**（表现为"还原=0"）。
3. **清陈旧锁**：回收站把历史各次事件遗留的 0 字节锁一起带回，共删
   `AUTO_MERGE.lock / config.lock / HEAD.lock / index.lock / packed-refs.lock` 与 `refs/heads/fix/t*.lock` 等。
4. **还原 master**：回收站带回的是**各次事件当时的旧引用**（含一堆早已消失的 `refs/heads/fix/t*`），
   master 被回填成了过期的 `66e4b43`。改用可靠的外部凭据：
   `git bundle verify D:/tmp_gitrecover/bundles/master-20260914-162901.bundle` → **The bundle records a complete history**，
   再 `git update-ref refs/heads/master 5fccce7d...`（该对象本地也存在），随后 `git reset --mixed` 同步索引（**不动工作区**）。
5. **垃圾引用体检**（回收站是"全量回填"，会带回历史垃圾）：
   - 删惰性 replace ref：`refs/replace/07afa909...`（两端对象都已不存在，会让 fetch/bundle 直接失败）
   - 删我自己留下的探活文件 `.git/refs/zzz_probe_refs.txt`（这条正是历史上写下的反面教材）
   - 清理已不存在分支的孤儿 reflog（`refs/heads/fix/*` 15 条，删到 6 条时撞上 safe-delete 守卫被 SIGTERM，剩余仅产生 fsck 噪音）
   - 重建 upstream：`refs/remotes/origin/master = 5fccce7`（父目录缺失导致 `update-ref` 静默无效，需先 `mkdir -p .git/refs/remotes/origin`）
6. **保险备份**：`D:/tmp_gitrecover/bundles/master-20260916-000711.bundle`（2.1M，`bundle verify` = OK）。

## 四、验收（全部通过）

| 检查项 | 结果 |
|---|---|
| `git rev-list --objects --missing=print master \| grep -c '^?'` | **0** |
| `git rev-list --objects HEAD \| cat-file --batch-check \| grep -c missing` | **0** |
| `git log --oneline` | 正常 walk 到 `a9c1905` 根提交，head = `5fccce7` |
| `git bundle create ... master` + `bundle verify` | 成功 2.1M / "is okay" → **可 push** |
| 工作区已跟踪文件删除数 | **0**（86 Modified + 33 Untracked = 119 项未提交改动完好） |
| `git status -sb` | `## master...origin/master`（upstream 已恢复同步） |

**残留（已证明与 master 无关，不影响 push/bundle）**：
- `refs/heads/t14followup`（missing=10）、`t15`（missing=13）、`t16`（missing=13）—— 历次事故遗留的断链 stub 分支，其 tree/commit 对象永久丢失；fsck 的 13 条 `broken link` 全部由这三个分支可达。建议确认无用后 `git branch -D` 清理。
- 少量 `invalid reflog entry 4c9abea6...` 噪音（剩余的 `refs/heads/fix/t*` 孤儿 reflog），无害。

## 五、建议（务必处理）

1. **立刻 `git push`**：目前本地 head `5fccce7` 与远端一致，但未提交改动有 119 项（含今日全部审计修复）——一旦 `.git` 再被清，这些就是唯一副本。
2. **这是第 7 次同类事件**：强烈建议排查本机定时/清理/同步类自动化进程（历史指证：批量 worktree、`refs/codex/**` 类自动化）。这类进程在 §2 的 7 个时间点集中删 `.git/**`。
3. **保留仓外 bundle 轮转**：`D:/tmp_gitrecover/bundles/master-<ts>.bundle`，每次重大改动后 `git bundle create` 一份（比本地引用可靠，Intact 且已 verify）。
4. **不要再在 `.git/refs/` 下写任何非标准命名的文件**（`.txt` 这类会被 git 当成引用，`git fetch` 会直接失败）。
