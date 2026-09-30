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

## 7. 恢复执行结果（同日追加，用户授权后执行）

> 本报告 §1–§6 为**只读诊断**。以下为**经用户授权后实际执行的恢复**，另附其验收证据。

### 7.1 执行步骤

| # | 操作 | 结果 |
|---|------|------|
| 0 | `cp -r .git D:\tmp_aqp_perf\git_backup_20260930\.git` | 备份 7.8M（回滚保险） |
| 1 | 直连 `git fetch origin` | ❌ **失败**：15 分钟仅到 67% 即 `protocol error: bad pack header`（超时 rc=124） |
| 2 | 改经镜像 `git fetch --depth=1 https://ghfast.top/https://github.com/...` | ✅ 619 对象 / 6.4MB，约 45 秒 |
| 3 | `git fetch --unshallow <镜像>` | ✅ 补 418 对象，取回**完整 33 提交历史** |
| 4 | `git update-ref refs/heads/master f7c9410` | ✅ 恢复分支引用 |
| 5 | `git read-tree f7c9410` | ✅ 由树直接重建索引（556 条，**0 缺失**） |
| 6 | `git add -A` + `git commit` | ✅ 新提交 **`475fde9`** |
| 7 | 建立 `refs/remotes/origin/master` | ⚠️ `update-ref` **静默失效**（只建目录不建文件）⇒ 改为**手动写 ref 文件**，成功 |

> **步骤 5 的必要性**：`git reset --mixed f7c9410` **失败**（`fatal: unable to read 06091042…`），
> 因旧索引 339 个 blob 缺失，reset 无法计算差异。`read-tree` 不需要读旧 blob，故可用。

> **步骤 7 的环境坑（值得记录）**：本环境下 `git update-ref refs/remotes/**` 返回 **exit 0 但不创建文件**
> （`refs/heads/**` 正常）。手动 `printf '<sha>\n' > .git/refs/remotes/origin/master` 后 git 即正常解析。
> 排查过"是否有外部进程在删 ref"（时序测试 T0/T+3s/T+10s）—— **文件从未出现，故排除该可能**。

### 7.2 验收证据

| 检查项 | 结果 |
|---|---|
| 历史深度 | **34**（33 个真实历史 + 1 个本次提交） |
| `git log` | ✅ `475fde9 → f7c9410 → 7da2fe0 → …`（33 个真实提交全部可读） |
| `git status` | ✅ **clean**（0 条未跟踪/未暂存） |
| 对象图 | ✅ `git fsck` **无 missing / broken**（仅 `dangling` 残留 + 旧 reflog 条目提示） |
| 索引完整性 | ✅ 776 个唯一 blob，**0 缺失** |
| 树文件数 | **785**（= 基线 556 + 新增 229） |
| 追踪关系 | ✅ `master 475fde9 [origin/master: ahead 1]` ⇒ **`git push` 将是干净的 fast-forward** |

### 7.3 关键情报：远端是「原始历史」的完整副本

`git log f7c9410` 显示 **33 个真实提交（2026-09-14 → 2026-09-20）**，
且本地 `ORIG_HEAD` 的 `5fccce7` **正在其中** ⇒ 这批提交是**被清空前原始历史的完整副本**，
前两次重建时被白白放弃。本次恢复把它拿了回来。

**内容核验**（f7c9410 的 556 个文件 vs 本地工作区）：

| 项 | 数量 |
|---|---|
| 本地不存在的远端文件 | **0**（⇒ 无任何资产丢失） |
| 内容完全相同 | 347 |
| 内容有差异 | 209 |
| 仅本地有（远端之后新增） | 229 |

### 7.4 已丢失的部分（不可恢复）

- **2026-09-23 ~ 2026-09-29 的 23 个提交**，其**对象永久丢失**（无法 checkout / diff）
- 其**提交信息**已从 reflog 抢救并落盘：
  **`deliverables/gstack/recovered-history-2026-09-30.md`**（可直接作为 `CHANGELOG.md` 素材）
- ⚠️ **务必不要执行** `git reflog expire --expire=now --all` / `git gc --prune=now`
  —— reflog 是这 23 条记录的唯一来源

### 7.5 ⏳ 尚未执行（需你决定）

| # | 动作 | 说明 |
|---|------|------|
| 1 | **`git push`** | 现在是干净的 fast-forward（ahead 1）。**这是唯一能打破"清空→重建"循环的动作** |
| 2 | ~~建议把 `remote.origin.url` **改为镜像地址**~~ | 🔴 **此建议已被证伪并撤回**，见 §8。**请勿执行** |
| 3 | 补 `VERSION` / `CHANGELOG.md` / 打 tag | 消除 B5"无回滚基线"；素材见 §7.4 |
| 4 | **处置根因** | 收口写 `.git` 的第三方工具（`gk`/`cursor`/`opencode`）、恢复 `gc.auto`、排查谁在删 refs |

### 7.6 仓库清理观察（**未执行**，仅提示）

本次按"**原样提交、不擅自丢弃内容**"原则执行，但暂存区里混有若干非源码产物，建议后续单独清理：

- `backend/.tmp_diag.txt`、`backend/.tmp_git.txt`（`.gitignore` 的 `backend/.tmp_*/` 只匹配**目录**，不匹配文件）
- `backend/tests/_qa_*.txt`、`docs/audit/_verify_*.txt`（QA/验证临时输出）
- `.bugfix_backup/20260923/*.py`（4 个源文件的备份副本）
- `verify_data_page_20260929.png`（根目录的一次性验证截图）

> 已按既有约定（`data/backup_*/` 已被忽略）补登 `.gitignore`：`.cleanup_backup_*/`、`backup/`
> —— 避免 793KB 的 `backup/aqp-*.tar.gz` 二进制归档进入历史。**若你不同意，`git revert` 即可。**

---

## 8. 🔴 重大订正：§7.5 第 2 条建议被证伪（2026-09-30 追加）

> **性质**：本节**推翻**本报告 §7.1 与 §7.5 中关于"直连 github.com 不可用"的**归因**，
> 并**撤回**"把 `remote.origin.url` 改为镜像地址"这条建议。原始错误结论保留在 §7.5（已划除），
> 以示订正轨迹。

### 8.1 被推翻的结论

原文（§7.5 第 2 条）：

> 「建议把 `remote.origin.url` 改为镜像地址 —— 直连 github.com 的 pack 传输在本机不可用（步骤 1 实测失败）；
> 否则下次 push 大概率同样超时。」

### 8.2 订正后的实测证据

同一台机、同一时段重测：

| # | 实验 | 结果 | 说明 |
|---|------|------|------|
| 1 | `curl https://github.com`（走代理） | **HTTP 200，0.76s** | 代理链路正常 |
| 2 | `curl --noproxy '*' https://github.com` | **HTTP 200，0.44s** | **直连也正常** ⇒ 推翻"直连不可用" |
| 3 | `curl ".../quant-system.git/info/refs?service=git-receive-pack"` | **HTTP 401，0.64s** | 端点**可达**，仅需认证 |
| 4 | `curl https://api.github.com/repos/GG-Bond-maker/quant-system` | **HTTP 200** | **仓库是公开的** |
| 5 | `GIT_TERMINAL_PROMPT=0 git -c credential.helper= ls-remote origin HEAD` | **exit=0，秒回 `f7c9410`** | **匿名读完全正常** |
| 6 | `GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c core.askPass= push --dry-run` | **exit=128**，`fatal: could not read Username for 'https://github.com': terminal prompts disabled` | **秒级明确报错** |
| 7 | `git push --dry-run origin master`（原样，带 GCM） | **exit=124 挂死，零输出**（45s 上限） | 对照 #6 ⇒ **挂死来自 GCM** |
| 8 | `git -c http.proxy= -c https.proxy= push --dry-run`（显式禁代理） | **exit=124 仍挂死** | 排除代理因素 |
| 9 | `printf 'protocol=https\nhost=github.com\n\n' \| git credential fill` | **exit=124 挂死** | **GCM 自身挂死**，与 git 无关 |

### 8.3 订正后的根因

| 故障 | 原归因（**错**） | 订正后根因 |
|------|----------------|-----------|
| **push 挂死** | 直连 github.com 的 pack 传输不可用 | **`git-credential-manager` 在非交互沙箱中永久等待凭据**（想弹 GUI/浏览器认证）。本机**无任何凭据**：GCM 无缓存、无 `GITHUB_TOKEN`/`GH_TOKEN`/`GIT_ASKPASS`、无 `gh` CLI、Windows 凭据管理器无条目 |
| **fetch `bad pack header`** | 同上 | ⏳ **未定性**。排障手判定现有日志不足以区分「本地代理 `HTTPS_PROXY=127.0.0.1:49890` 截断大 pack 流」/「GitHub 限速」/「网络抖动」，需 `GIT_TRACE_CURL` 或重试复现。**待验证，勿据此下结论** |

### 8.4 撤回的建议 → 替换为

| # | ~~原建议~~ | ✅ 订正后做法 |
|---|-----------|-------------|
| 1 | ~~把 `remote.origin.url` 改为镜像~~ | **保持 `origin` 指向 github.com 不动**。理由：仓库公开、链路可达；改镜像**解决不了凭据问题**，反而会改变认证方式、破坏 LFS 等资源地址 |
| 2 | — | 推送前设 `GIT_TERMINAL_PROMPT=0`（配合 `GCM_INTERACTIVE=never`），让凭据缺失**秒级报错**而非挂死 |
| 3 | — | 加 `http.lowSpeedLimit` / `http.lowSpeedTime`，把网络停滞转成**超时报错**而非无限等待 |

### 8.5 责任声明

- §8.2 的 9 项实验由**主理人**亲自执行，输出为原始终端回显。
- 根因裁决由 **排障手（gstack-investigator）** 出具；其**明确声明**未亲自复现，结论基于主理人证据 + GCM 行为常识的推断。
- §7.1 步骤 1 的 `fetch` 失败**是客观事实**，但当时给出的**归因是错的** —— 这是本报告唯一的实质性错误，
  教训：**"慢/挂"不等于"不通"，必须用对照实验区分「链路故障」与「应用层阻塞」**。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
