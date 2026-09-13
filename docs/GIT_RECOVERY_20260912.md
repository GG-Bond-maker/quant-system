# .git 损坏事故取证与恢复报告

- 事故时间：2026-09-12 23:24:03–23:24:34（约 30 秒窗口）
- 发现时间：2026-09-12 23:24（提交被拒）
- 恢复完成：2026-09-13 01:0x
- 新根提交：`a2f0e2afc573facc83340ac651ecc71bdfe13ead`
- 被放弃的旧 HEAD：`5f4c52be6095f94e0ec85699352a7bbbd2df144c`

---

## 一、事故现象

`git commit` 报错：

```
error: invalid object 100644 f8d4fa88c801f569c12d94668bfc360ec47d9e8b for 'backend/tests/test_no_core_reverse_deps.py'
error: Error building trees
```

随后 `.git/refs` 与 `.git/objects` 大面积为空，`git` 一度报 "not a git repository"。
`git status` 直接失败：

```
fatal: unable to read tree (437598d1c7e2ef6e2028a0aa115846ca39c82b4a)
```

即 **HEAD 自身的 tree 已不完整**，暂存区亦引用已消失的对象，仓库无法提交。

## 二、取证

### 2.1 回收站分析（D: 盘）

解析 `D:\$Recycle.Bin\S-1-5-21-3686088530-1808216798-2721998104-1001` 下
全部 `3126` 条 `$I` 元数据记录（Windows `$I` 格式：`Q version, Q size,
Q FILETIME, I nlen, UTF-16-LE name`）：

| 项目 | 数量 |
|---|---|
| 回收站总条目 | 3126 |
| 其中 `.git` 相关 | 1265 |
| `objects/loose` | 1019 |
| `objects/pack` | 6 |
| `refs` | 37 |
| `logs` | 10 |
| `worktrees` | 127 |
| 其他（lock / COMMIT_EDITMSG 等） | 66 |

事故前一轮已从回收站恢复 1098 个 `.git` 文件；本轮再次全量扫描，
剩余 1265 条**全部仍可读**，但其中为当前 `.git` 所缺的 **64 项全是
0 字节 lock 文件**（`index.lock` × 30、`HEAD.lock` × 15、
`AUTO_MERGE.lock` × 7、`packed-refs.lock` × 7、`refs/heads/master.lock` × 1 …），
**没有任何可用 git 对象**剩余。

### 2.2 关键线索：lock 文件高频轮转

回收站在同一时段记录了约 **30 个 `index.lock`、15 个 `HEAD.lock`**
被反复创建又删除。正常人工操作绝不会产生这种密度；这是**自动化进程在
循环执行 git 写操作**的典型特征。

### 2.3 关键线索：仓库外的 17 个 worktree

`git worktree list` 显示主仓库之外存在 **17 个兄弟 worktree**，位于
`D:\Python_Project\aqp-wt-*`（**在仓库目录之外**）：

```
aqp-wt-t14fix   aqp-wt-task1   aqp-wt-task2   aqp-wt-task3
aqp-wt-task4    aqp-wt-task5   aqp-wt-task6   aqp-wt-task7
aqp-wt-task8    aqp-wt-task9   aqp-wt-task10  aqp-wt-task11
aqp-wt-task12   aqp-wt-task13  aqp-wt-task14  aqp-wt-task15
aqp-wt-task16
```

配套残留（现已随 `refs/` 被清空而消失）：分支 `fix/t6-delist`、
`fix/t7-macross-gates`、`fix/t8-ic-hfq`、`refs/codex/turn-diffs/**`。

这些 worktree 的状态**全部是坏的**：

- `aqp-wt-task6` / `aqp-wt-task9`：`fatal: not a git repository: (NULL)`
- 其余 15 个：HEAD 为 `0000000`（对象已丢失）或指向已损坏的提交
- 索引状态：整棵树以 `A`（新增、未提交）呈现，**无任何已提交历史**
- 逐个体检 `git status --short`：**没有发现任何未提交的有效业务改动**

结论：这批 worktree 是某个外部自动化工具留下的**废弃脚手架**，
不承载唯一工作成果。

### 2.4 损坏范围量化

`git fsck --full` 初始报告 **48** 处问题（24 个 missing tree、3 个
missing blob、若干 reflog 引用已消失的 commit）。

对**全部 20 个存活提交**逐一做 `git ls-tree -r <sha>` 验证：

```
BROKEN 5f4c52b   BROKEN 07afa90   BROKEN 493ee20   BROKEN 93fa52d
BROKEN 89cf3e4   BROKEN 6b9d5cc   BROKEN 754708e   BROKEN 25b9f20
BROKEN 732f9ec   BROKEN fa4227e   BROKEN e6a930d   BROKEN 3c9bc30
BROKEN d138639   BROKEN c30de37   BROKEN 60471b2   BROKEN 4083390
BROKEN 84e8388   BROKEN 4641cd8   BROKEN d396d8d   BROKEN c24ff00
```

**20/20 全部 BROKEN** —— 缺失子树是跨历史共享的（同一目录树在多个提交中
哈希相同），因此「回退到较早的干净提交」这条路走不通。

对 3 个 missing blob 与 1 个索引引用的 missing blob
（`4e72171a` / `c7afcf7f` / `d96e8699` / `f8d4fa88`）遍历工作区
2817 个文件重算 git blob 哈希：**全部 NOT FOUND**，即这些对象对应的
文件内容事后已被修改或删除，**无法由工作区重算复原**。

> 判定：旧 lineage 的对象图为**永久性不可恢复**。

## 三、恢复处置

### 3.1 保全

| 备份位置 | 内容 | 时间点 |
|---|---|---|
| `data/backup_gitstate_20260913/` | 事故当时的 `.git` 快照 | 23:24（损坏态） |
| `data/backup_git_20260912/` | 修复前 `.git` 全量（含回填的 refs/objects，17MB） | 恢复中 |
| `D:\tmp_gitrecover\` | 取证脚本、回收站清单 JSON、旧提交日志、根提交消息 | 全流程 |

`data/backup_*` 命中 `.gitignore` 的 `data/backup_*/` 规则，不会污染版本库。

### 3.2 以工作区重建为新的根提交

工作区源码、配置、测试、文档**完整无损**。为得到一个对象图完整、
可正常 `status/diff/log/commit/push` 的仓库，执行：

```bash
git read-tree --empty          # 丢弃引用丢失对象的旧索引
git add -A                     # 从工作区全量重建索引
git ls-files | wc -l           # 411 —— 与事故前索引清单逐行 diff 完全一致
TREE=$(git write-tree)         # 4cc57b57e6629b1eac28c7ae94c7f9772a540a65
NEW=$(git commit-tree $TREE -F <msg>)   # a2f0e2afc573facc83340ac651ecc71bdfe13ead
git update-ref refs/heads/master $NEW
```

**文件零丢失验证**：新索引的 411 条路径与事故前索引
（`git ls-files`）逐行 `diff` 结果 **IDENTICAL**。关键文件抽查全部 STAGED：
`backend/tests/test_no_core_reverse_deps.py`、`backend/tests/conftest.py`、
`backend/tests/test_screener_snapshot.py`、`backend/app/core/pipeline_lock.py`、
`data/universe_2500.json`。

### 3.3 清理

- 删除 graft 替换引用 `refs/replace/07afa909…`（新根提交不再需要 graft 桥接）
- `git reflog expire --expire=now --expire-unreachable=now --all`
  （清掉指向已消失提交的 reflog 条目）
- 删除过期派生缓存 `.git/objects/info/commit-graphs/`（git 会按需重建）
- `git fsck` 问题数：**48 → 20**

## 四、被放弃的旧提交（消息保留备查）

以下提交的对象已丢失，内容已并入新根提交 `a2f0e2a`：

```
5f4c52b fix(ml+api): feature_runs.feature_version 取 predictions 真实值并加白名单校验 (D-02/T-08)
07afa90 feat(security+concurrency): 读端点鉴权第二批(viewer) 与流水线阶段互斥
493ee20 fix(alerts+export+sync): 修复预警静默失效、导出阻塞事件循环与同步目标日滞后
93fa52d feat(security+ops): 生产安全加固、错误码归一与全量端点补测
89cf3e4 fix(sync): 增量同步目标日改用已收盘语义，修复当日行情滞后一天进库
6b9d5cc docs(acceptance): 补记 torch 重装后全量 pytest 复测结果(7:33, 429 passed/5 skipped/0 failed)
754708e docs(acceptance): 验收总门——全量 pytest 计时(4:56 全绿)与最终 A/B 六类归因分解(差异精确闭合)，T16 curl 证据归档
25b9f20 fix(test-infra): 移除 test_auth/test_rbac 模块顶层 os.environ 改写，消除全量套件顺序依赖失败
732f9ec refactor(a-p1-7): datacenter 同步编排与 market 外呼下沉 services
fa4227e refactor(a-p1-6b): ML 步骤上移编排层，解 data→ml 与 db→data 环
e6a930d fix(t9-followup): _sync_worker 终态持久化补齐 failed/finished 实参
3c9bc30 fix(t14-followup): 修复 scheduler/quotes_hub 迁移后全部消费方与内部导入断链
d138639 test(t14): quant_upgrade 组合回测跟进 loader 注入签名
c30de37 refactor(a-p1-1): domain 组合回测去 IO 化——价格由调用方注入，AST 守卫禁业务层反向依赖
60471b2 test(t12): 修复全量套件的三处顺序依赖失败
4083390 fix(t10): text_ingest 的 parquet_store 导入路径错误（.. → .），全量收集失败
84e8388 perf(a-p1-5b): 特征构建真增量——预热窗重算+重叠段守卫，全量回退兜底
4641cd8 fix(a-p1-5a): 全部 parquet 旁路写点收口原子写 atomic_write_parquet
d396d8d fix(a-p1-4): 同步断点续传区分 completed/failed，全败不再视为完成
c24ff00 fix(a-p1-9): 信号分析 IC 数据契约测试固化 hfq 口径（真实数据偏差 ≤0.0015 实测）
```

另有 6 个仅存在于 reflog、对象已消失的提交（内容同样已并入新根提交）：
`d9b5f43e`、`8e80ba75`、`3111b9e6`、`85c8c36`（A 组读端点鉴权 + trace_id）、
`31f0594`（pytest 退出挂起修复）、`8e793f5`（快照测试隔离修复）。

## 五、残留问题（不影响正常开发）

`git fsck --full` 仍报 20 处问题，**全部来自废弃的 worktree 与死分支**，
与主分支对象图无关：

1. 5 处 `invalid sha1 pointer in cache-tree of .git/worktrees/aqp-wt-*/index`
   —— 废弃 worktree 的索引缓存引用已消失的树。
2. `broken link from commit 398e01d9 / 4641cd88` 及其 missing tree
   —— 仅由废弃分支 `t14followup` / `t15` / `t16` 可达。

**对主分支无影响**：`git status` clean、`git log`/`git diff`/`git commit`
正常，`git push master`（未来加远端时）只打包 `a2f0e2a` 可达对象，
对象图完整，可正常推送。

## 六、建议（待确认后执行）

1. **清理 17 个废弃 worktree**（`D:\Python_Project\aqp-wt-*`）。
   它们已全部损坏、无唯一工作成果，且其配套的自动化工具高度疑似本次
   事故的肇因。清理需删除仓库**之外**的 17 个目录，涉及仓外数据，
   **必须由用户明确确认后**再执行。
2. **删除 3 个死分支** `t14followup` / `t15` / `t16`（内容已并入新根提交）。
3. **尽快配置远端仓库**（GitHub/Gitee 私有库）。本次事故说明单副本
   `.git` 无任何容错，配置远端后每日 `git push` 即可获得异地副本。
4. **停用/收口外部 git 自动化工具**（产生 `refs/codex/turn-diffs/**`、
   批量 worktree 者），避免再次并发写坏 `.git`。
