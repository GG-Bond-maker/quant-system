# A6 —— git 对象库只读诊断报告

**日期**：2026-09-30
**范围**：**只读诊断**（未执行任何写入 / 未改动任何历史 / 未 fetch / 未建 ref）
**触发**：上线前全检 B5（"仓库 0 提交、无 VERSION/CHANGELOG、无 tag ⇒ 无回滚基线"）
**诊断命令**：`git fsck --full` / `git cat-file --batch-check` / `git ls-remote` / 直读 `.git/` 文件

---

## 📌 TL;DR

- 结论：🔴 **本地对象库已第三次被外部进程清空** —— refs 全空、提交对象全丢、索引 99.4% 悬空
- **工作区源码完好无损**（这是最重要的：业务资产没有丢）
- ✅ **好消息**：**reflog 完整保留 23 条提交信息**，且 **GitHub remote 可达**（`refs/heads/master` 存在）
- ⚠️ 但 remote 只到**第一次重建时期**（`f7c9410`），**local 从未 push 过** ⇒ 远端拿不回最近 22 个提交
- 🔴 **根因线索明确**：`.git/` 内有 **3 个第三方工具**在写入（`gk` / `cursor` / `opencode`），且 `gc.auto=0`
- 下一步建议：`git fetch origin`（**纯增量、安全**）→ 评估可恢复范围 → 再决定重建方式

---

## 1. 当前仓库状态（客观事实）

| 项目 | 观测值 | 含义 |
|------|--------|------|
| `git rev-parse HEAD` | ❌ `unknown revision` | HEAD 未出生（unborn branch） |
| `git branch -a` | **空** | 零分支 |
| `git tag` | **空** | 零 tag |
| `refs/` 下文件数 | **0** | refs 被清空 |
| `packed-refs` | **不存在** | 无 packed refs |
| `HEAD` 内容 | `ref: refs/heads/master` | 指向一个**不存在**的 ref |
| loose objects | **9** 个（6 blob + 3 tree，**零 commit**） | 对象库几乎全空 |
| `.git/objects/pack/` | **不存在** | 无 packfile |
| 索引条目 | **702** 条 | 索引本身还在 |
| 索引 blob 完整性 | 唯一 blob **697** 个 ⇒ **存在 4 / 缺失 693**（**99.4% 悬空**） | 这解释了 `git diff` 的 `unable to read 78f5a281...` |
| `git fsck --full` | 大量 `invalid reflog entry`（非致命，reflog 指向已丢对象） | 无 `missing object` 报错，因 refs 为空、无可达图 |

> **关键区分**：`fsck` 只对**可达对象**做完整性校验。refs 为空 ⇒ 可达图为空 ⇒ `fsck` **不会**报"missing object"，
> 但这**不代表健康**。真正的问题是"整个对象图都不可达了"。

---

## 2. 🔍 根因线索：外部进程在写 `.git/`

### 2.1 三个第三方工具的痕迹

| 路径 | 性质 |
|------|------|
| `.git/gk/config` | 第三方工具 `gk` 的配置 |
| `.git/cursor/crepe/c1b26c78.../{index.bin, metadata.json, postings.bin}` | **Cursor IDE** 的代码索引缓存 |
| `.git/opencode` | `opencode` 工具写入的文件 |
| `.git/zzz_probe_root.txt`（内容：`probe`） | 有人在探测 `.git` 根目录可写性 |

### 2.2 可疑配置

```ini
[gc]
	auto = 0              # 关闭自动 gc ⇒ loose 对象永不打包
[http]
	schannelCheckRevoke = false
```

### 2.3 事件时间戳

- `.git/refs` 与 `.git/objects` 的 mtime 均为 **2026-09-29 15:39** ⇒ 清空发生于该时刻

### 2.4 ⚠️ 决定性证据：ref 删除**没有留下 reflog**

`HEAD` reflog 共 23 条，**最后一条是 commit**（`16b208a → d022548`），
**没有任何 `delete` / `reset` 条目**。

⇒ 若 refs 是被 git 命令删除的，reflog 必然留下记录。**没有记录 ⇒ 是外部进程直接删了文件**，
绕过了 git 的全部审计。这与 09-12 那次事故（"外部进程清空 `.git`"）**同一机理**。

---

## 3. ✅ 可恢复性评估

### 3.1 reflog 完整保留 23 条提交信息（**信息资产**）

reflog 记录了两次 **root commit 创建**（old SHA = `0000...`）：

| # | 事件 | 说明 |
|---|------|------|
| 1 | `0000… → 7ce9efe` | **第一次重建根提交**（提交信息：`对象库被外部进程清空 .git ——`） |
| 2–16 | 15 次 commit | 正常开发 |
| **17** | `0000… → 8940058` | **第二次重建根提交**（提交信息：`…master（.git 从完整工作区重建`）⇒ **二次清空** |
| 18–23 | 6 次 commit | 至 tip `d022548`（2026-09-29） |

⇒ **本次是第三次清空**。历史提交信息可从 reflog 全文抄录保留（价值等同 CHANGELOG）。

### 3.2 远端可用，但**只到第一次重建时期**

```
git ls-remote origin  →
  f7c9410119f6646f3b5a9c58737101e5ac72e1da   HEAD
  f7c9410119f6646f3b5a9c58737101e5ac72e1da   refs/heads/master
```

- remote：`https://github.com/GG-Bond-maker/quant-system.git`
- `origin/master` 的 reflog **只有 1 条**（一次 `fetch -v https://ghfast.top`），**没有任何 `push` 记录**
  ⇒ **本地从未 push 过** ⇒ 远端停留在**早期状态**（`f7c9410`，时间上贴近第一次重建的 `7ce9efe`）
- 因此：**fetch 只能拿回早期基线，拿不回最近约 22 个提交**

### 3.3 工作区完好（最重要的好消息）

- 702 个受版本控制的文件**全部存在于工作区**
- 本轮所有修复（14 个文件的改动）**都已在磁盘上**
- ⇒ **业务资产零丢失**；丢的只是**提交 lineage 与历史粒度**

### 3.4 可恢复性总结

| 资产 | 状态 |
|------|------|
| 工作区源码（含本轮全部修复） | ✅ **完好** |
| 最近 23 个提交的**提交信息** | ✅ 可从 reflog 抄录 |
| 最近 23 个提交的**对象/内容快照** | ❌ **永久丢失**（loose 只剩 9 个，无 pack） |
| 早期基线（`f7c9410`） | ✅ **可经 fetch 从远端取回** |
| tag / VERSION / CHANGELOG | ❌ 从未存在过（`VERSION`/`CHANGELOG.md`/`CHANGELOG`/`HISTORY.md` 全缺失） |

---

## 4. 建议的恢复路径（**未执行，待你裁决**）

### 步骤 1（低风险，建议先做）：`git fetch origin`
- **性质**：**纯增量** —— 只下载对象 + 更新 `refs/remotes/origin/master`
- **不动**：`refs/heads/master`、索引、工作区
- **收益**：拿回 `f7c9410` 及其历史，据此判断它与本地工作区的关系
- 之后可 `git diff f7c9410 --stat` 看清"本地相对早期基线改了什么"

### 步骤 2：选择重建方式（**二选一，需你决定**）

| 方案 | 做法 | 优点 | 代价 |
|------|------|------|------|
| **A. 以远端为基** | fetch 后 `git reset --soft f7c9410` + 提交当前工作区 | 保留早期 lineage | 中间 22 个提交的粒度仍丢 |
| **B. 重建根提交** | 以工作区新建根提交（**前两次已这么做**） | 简单、确定、可 push | 放弃全部旧 lineage |

> **⚠️ 强烈建议先做步骤 1 再决定**：若 `f7c9410` 与工作区差异巨大，说明它是很旧的快照，
> 方案 B 更干脆；若差异很小，方案 A 更划算。**不 fetch 就无法判断**。

### 步骤 3（无论选哪个都要做）
1. **备份 `.git`**：`cp -r .git D:\tmp_aqp_perf\.git_backup_20260930`
2. **建 tag + VERSION + CHANGELOG**（补上从未存在的版本基线）：
   - 把 reflog 的 23 条提交信息抄录成 `CHANGELOG.md`
   - 打 `v0.1.0` 之类的 tag
3. **首次 `git push -u origin master`** —— 这是**唯一能打破"第三次清空"循环**的动作
   （前两次清空后都没 push，所以每次都只能从工作区重建）

---

## 5. 🔴 必须处置的根因（否则会有第四次）

| # | 措施 | 理由 |
|---|------|------|
| 1 | **配置并坚持 push 到远端** | 前两次清空后都没 push ⇒ 无异地副本 ⇒ 每次全量重建。**这是最高优先级** |
| 2 | **收口/停用写入 `.git` 的第三方工具**（`gk` / `cursor` / `opencode`） | `.git/` 内 3 处工具痕迹 + `zzz_probe_root.txt` 探测痕迹 |
| 3 | **恢复 `gc.auto`**（当前为 `0`） | 自动 gc 被关，loose 对象不打包，放大丢失面 |
| 4 | **排查是谁在删 refs** | ref 删除**无 reflog 记录** ⇒ 外部文件操作，需从系统层面定位 |

---

## 6. 本次诊断的纪律声明

- ✅ **全程只读**：仅执行 `fsck` / `cat-file` / `ls-remote` / 直读文件；**未** fetch、**未** update-ref、
  **未** 改动索引或工作区
- ✅ `git ls-remote` 是只读网络查询，**不会**写入本地 `.git`
- ⚠️ 报告中的"可恢复性"判断基于**本地可观测证据**；`f7c9410` 的实际内容需 fetch 后才能确认

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
