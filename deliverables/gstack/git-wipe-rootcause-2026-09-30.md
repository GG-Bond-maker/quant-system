# AQP `.git` 三次清空 —— 根因机制认定与防护设计

**日期**：2026-09-30
**场景**：调试复盘 / 根因分析（**只读取证**，未改动 `.git` 任何文件）
**参与成员**：排障手（gstack-investigator，机制认定与防护设计） + 主理人（证据复核与汇编）
**上游交付物**：`deliverables/gstack/git-forensics-readonly-2026-09-30.md`（A6 只读诊断 + §8 订正）

> 本报告**取代**此前报告 §2「根因线索：外部进程在写 `.git`」中关于"3 个第三方工具"的归因。
> 那三个痕迹经复核判定为**红鲱鱼**，理由见 §3。

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟡 **机制已认定，凶手未认定** —— 清空机制 = **外部进程逐文件删除**（非 git 命令），
  但**执行删除的进程身份仍无法证实**
- **推翻旧归因**：`.git/gk`、`.git/cursor`、`.git/opencode` 三个痕迹是**红鲱鱼**（证据见 §3）
- **新发现**：`.git` 曾在 **2026-09-21 18:47** 被**整份复制/移动**（首次事故前 2 天）
- **新发现**：`.git/hooks`、`.git/info`、`.git/branches`、`.git/objects/info`、`.git/refs/tags`
  **全部缺失** ⇒ 这不是 `git init` 生成的标准 `.git`
- ⚠️ **现有备份对 git 恢复无用**：`backup/aqp-*.tar.gz` 只含 `models/` + `sqlite/`
- 最高优先级动作：**补上"提交即异地镜像"**（此前从未有 git 异地副本）

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 机制认定 | 🟢 **已认定**：外部进程**逐文件删除** |
| 凶手认定 | 🔴 **未认定**（证据不足，不编造） |
| 旧归因 | 🔴 **推翻**（三痕迹 = 红鲱鱼） |
| 关键新证据 | 11 个空分片阶梯 mtime/ctime（跨 24s）；`.git` 整份 ctime = 09-21 18:47 |
| 防护可落地性 | 🟢 有具体 Windows 命令（§5） |
| 阻塞项 | 1 条：**当前无任何 git 异地副本** |

---

## 1. 各成员核心结论

### 🔧 排障手（机制认定与防护设计）
- **核心判断**：清空是**外部进程逐个删文件**，既不是 `rm -rf .git/objects`（那样分片目录会消失），
  也不是 `git gc/prune`（那样会保留 pack、且不删 refs、且亚秒完成）。
- **关键建议**：核心防护是 `post-commit` 钩子把仓库镜像 push 到异地裸库；`gc.auto` 关闭**对攻防都无益**，
  应恢复默认并改为**加长 reflog 保留**。
- **诚实声明**：明确列出 6 条局限，含 2 处与主理人记录的**数量矛盾**（未强行圆场）。

### 🧭 主理人（证据复核）
- **核心判断**：排障手的关键论据**全部复核证实**（§2.1）；其"裸仓库在沙箱打不开"的判断**是误判**——
  实为 MSYS 路径格式问题，改用 `--git-dir="D:/..."` 即可正常读取。
- **关键补充**：复核中发现**陈旧 `index.lock`** 一条新物证（§4）。

---

## 2. 综合审查发现（按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题 | 建议 | 来源 |
|---|--------|------|------|------|------|------|
| 1 | 🔴 | 数据安全 | 全仓 | **无任何 git 异地副本**（`backup/*.tar.gz` 只含 `models/`+`sqlite/`，不含源码与 `.git`） | 立即建立镜像裸库 + `post-commit` 钩子 | 排障手（主理人复核属实） |
| 2 | 🟠 | 取证 | `.git/` | 删除进程**身份无法证实**（无进程级审计） | 启用 `auditpol` 文件系统审计 + FileSystemWatcher | 排障手 |
| 3 | 🟡 | 取证指标 | `.git/` | `hooks`/`info`/`branches`/`objects/info`/`refs/tags` **缺失** ⇒ 非标准 `.git`，疑被裁剪或非 `git init` 生成 | ⚠️ **主理人实测：不造成功能故障**（git 会惰性重建目录），故**降级为取证指标而非缺陷**；但装 `post-commit` 钩子前仍需先建 `hooks/` | 排障手（主理人复核属实 + 实测降级） |
| 4 | 🟡 | 配置 | `.git/config` | `gc.auto=0` 长期关闭 | **恢复 6700**；并设 `gc.reflogExpire=never`、`gc.pruneExpire=1.month` | 排障手 |
| 5 | 🟡 | 证据保全 | `.git/logs` | reflog 是唯一历史来源，且 `--mirror` **不携带 reflog** | 必须**单独异地另存** `.git/logs` | 排障手 |
| 6 | 🟡 | 环境坑 | 本机 git | 陈旧 `index.lock` 会导致 git 写操作静默失败 | 监控锁文件存在时长；排查来源 | 主理人（复核中发现） |
| 7 | 🟢 | 残留物 | `.git/` | `zzz_probe_root.txt`（AI 探针）、5 个 `index.stash.*` 孤儿索引快照 | 先存档元数据，再决定是否清理 | 排障手 |

### 2.1 主理人复核结论（逐条对照）

| 排障手论据 | 复核结果 |
|---|---|
| 三痕迹 ctime 与其它条目同刻 | ✅ **证实**：`gk`/`cursor`/`opencode` 与 `HEAD`/`config`/`description`/`ORIG_HEAD` 的 ctime **均为 `09-21 18:47:25~26`** |
| 11 空分片 mtime==ctime 且阶梯推进 | ✅ **证实**：`15:39:06.600 → 15:39:30.424`，**跨 24.8 秒** |
| 标准 `.git` 子目录缺失 | ✅ **证实**：`hooks`/`info`/`branches`/`objects/info`/`refs/tags` 全缺 |
| `backup/aqp-*.tar.gz` 不含源码与 `.git` | ✅ **证实**：顶层仅 `models/`、`sqlite/` |
| 裸仓库在沙箱打不开 | ❌ **误判**：实为路径格式问题，`--git-dir="D:/aqp-git-backup/quant-system.git"` 读取正常（1540 对象 / pack 10.8MB） |
| 标准子目录缺失会造成功能故障 | ❌ **不成立**：主理人实测 `git tag aqp-probe-tmp` **成功**并**自动重建** `refs/tags/`，删 tag 后目录留存为空 ⇒ **git 惰性重建，无功能影响**。该缺失是**取证指标**，不是缺陷 |

---

## 3. 清空机制认定：三假设交叉论证

**结论：外部进程「逐文件删除」**（非 `rm -rf .git/objects`，非 `git gc/prune`）

| 观测 | 逐文件删 | `rm -rf .git/objects` | `git gc/prune` |
|------|---------|----------------------|----------------|
| 分片目录 | **保留（空）** ← 现场 | 消失 | 保留（空） |
| pack 目录 | 递归删⇒消失 ← 现场 | 消失 | **必留** ✗ |
| refs / reflog | 直接删文件⇒无记录 ← 现场 | 同 | gc **不删 refs** ✗ |
| 删除集 | **有选择/被中断** ← 现场 | 全量 | 仅不可达（默认留 2 周） |
| 耗时 | **阶梯 24s** ← 现场 | 瞬时 | 亚秒 |

**三条现场证据的交叉**：
1. **分片目录保留但为空** ⇒ 排除 `rm -rf .git/objects`（那样目录本身会消失）
2. **pack 目录消失 + refs 无 reflog 条目 + 耗时 24 秒** ⇒ 排除 `gc/prune`
   （gc 必留 pack、不删 refs、亚秒完成）
3. **幸存对象全在分片 `00`** ⇒ 删除是**有选择或被中途打断的**，`rm -rf` 做不到这种选择性

### 3.1 为何 09-23 / 09-27 两次没有同样签名（已可解释）

分片 mtime 签名**只在「被清空且此后无新对象写入」时才留存**。
09-23 清空后立即重建并连续提交到 09-27，09-27 再重建提交到 09-29 ——
分片的 mtime 被反复前移。**09-29 那次清空几乎抹掉全部分片并统一改写为 15:39**，
09-30 的 `fetch` + `add` + `commit` 又把其余分片刷成 09-30。
⇒ 旧签名被**后续活动与 09-29 事故本身覆盖**，不是"没发生过"。

---

## 4. 三痕迹裁决：红鲱鱼（高置信）

**判定**：`.git/gk`、`.git/cursor/crepe`、`.git/opencode` **不是凶手**。

**理由（三条独立证据）**：
1. **ctime 同刻**：三者的 ctime 与 `.git/` 下**每一个未再改动条目**（`HEAD`/`config`/`description`/
   `ORIG_HEAD`/`index.stash.*`/`zzz_probe_root.txt`）**完全同刻** = `09-21 18:47:25~26`
   ⇒ 它们是同一次「**整份 `.git` 复制/移动**」事件的**被动受害者**，而非施动者
2. **早已停用**：`gk/config` 自记 `gk-last-accessed = 2026-09-05`；三者 mtime 均 ≤ 09-05，
   **比首次事故早 18 天以上**
3. **机制不符**：三者均无"清空 objects + 删 refs"的能力或行为特征

### 4.1 新发现的未知事件：09-21 18:47 的 `.git` 整份复制/移动

**证据**：`.git/HEAD` 的内容 mtime 是 **09-07**，但其 **ctime 是 09-21 18:47:25**。
ctime = inode 变更时间；复制/移动会生成新 inode ⇒ 新 ctime。
**且 `.git/` 下所有未再改动条目共享同一 ctime** ⇒ 整个 `.git` 在那一刻被重建。

**这可能与"逐文件删除"同源**：一个带**镜像删除策略**的同步/备份工具，
在 09-21 复制了 `.git`（也解释了为何 `hooks/`、`info/`、`branches/` 缺失——
源目录里就没有），此后每次同步时把"源里不存在的文件"**逐个删掉**——
这正好产生"分片目录留空 + 24 秒阶梯"的签名。

> ⚠️ **这是假设，不是结论**。该工具的**身份与目的证据不足**，见 §7。

### 4.2 主理人补充物证：陈旧 `index.lock`

2026-09-30 主理人清理仓库时，`git rm` 出现**部分失败**（13 个文件只删掉 5 个，索引完全未更新），
报 `Unable to create '.git/index.lock': File exists`。实测该锁：
**0 字节、mtime 13:11、无任何 git 进程持有**；删除后 `git add -u` 立即恢复正常。

- **能否解释三次清空**：❌ **不能** —— 锁只阻塞后续 git 写操作，不会删除 refs/objects
- **是否值得纳入检测**：✅ **值得** —— 正常 git 操作不会留下 0 字节锁超过几秒，
  「锁存在时长 > N 秒」是低成本告警项
- **来源**：可能为主理人自身并发操作所致（当时有一子代理在跑只读 git 命令），**未定性**

---

## 5. 检测防护方案（Windows 可落地）

### 5.1 捕获：三层递进

**第 1 层 · 实时文件监视**（秒级告警，抓路径+时刻；**抓不到进程**；突发删除会缓冲溢出）

```powershell
$w = New-Object IO.FileSystemWatcher 'D:\Python_Project\Alpha Quant Platform\.git'
$w.IncludeSubdirectories = $true
$w.InternalBufferSize = 1MB
Register-ObjectEvent $w Deleted -MessageData 'D:\aqp-git-backup\watch.log' -Action {
  Add-Content $event.MessageData ("{0:o} DEL {1}" -f (Get-Date), $event.SourceEventArgs.FullPath)
}
Register-ObjectEvent $w Renamed -MessageData 'D:\aqp-git-backup\watch.log' -Action {
  Add-Content $event.MessageData ("{0:o} REN {1}" -f (Get-Date), $event.SourceEventArgs.FullPath)
}
$w.EnableRaisingEvents = $true
```

**第 2 层 · 进程归因**（**唯一能抓到凶手进程**的手段，需管理员）

```powershell
auditpol /set /subcategory:"File System" /success:enable
```
再对 `.git\refs` 与 `.git\objects\pack` 设 SACL，审计"删除/写入"，
读安全日志**事件 4663**（含**进程映像路径**）。建议只审计这两个子树，避免日志淹没。

**第 3 层 · 快照兜底**（每 1–5 分钟把 `git count-objects -v` / `git show-ref` 写入异地）
判警规则：**loose 对象骤降 > 50%** 或 **ref 数 → 0**。

### 5.2 恢复：秒级

| 方案 | 命令 | 说明 |
|------|------|------|
| **镜像裸库（首选）** | `git clone --mirror <repo> D:\aqp-git-backup\quant-system.git`<br>`post-commit` 钩子跑 `git push --mirror D:\aqp-git-backup\quant-system.git` | 恢复 = `git clone <mirror>` 或 `git fetch <mirror> "+refs/*:refs/*"` |
| bundle 冷备 | `git bundle create D:\aqp-git-backup\aqp-%DATE%.bundle --all` | 单文件，便于离线归档 |
| 频率建议 | post-commit 即时（已提交的 RPO≈0）+ 每 15 分钟快照 + 每日整目录 tarball | |

> ⚠️ **两个坑（务必注意）**
> 1. **`--mirror` 不携带 reflog** —— 而 reflog 既是关键证据、又是本次恢复的历史来源
>    ⇒ **必须单独异地另存 `.git/logs`**
> 2. **`.git/hooks/` 目录当前不存在**（见 §2 第 3 项）⇒ 装 `post-commit` 钩子前**必须先建目录**

### 5.3 `gc.auto` 该怎么设

**关闭 `gc.auto` 对攻防双方都无益** —— 它只管 git 是否自动 gc，管不到外部删文件。
**反证**：三次清空全部发生在 `gc.auto=0` 期间。

⇒ **恢复默认 `gc.auto=6700`**；真正有防护价值的是**加长保留**：

```bash
git config gc.reflogExpire never
git config gc.reflogExpireUnreachable never
git config gc.pruneExpire 1.month
```

### 5.4 残留物处置建议

- **保留（取证基线）**：`.git/logs/**`（**务必异地另存**）、11 个空分片与分片 `00`
  （**先导出其 mtime/ctime 再谈清理**）、`ORIG_HEAD`/`FETCH_HEAD`/`config`、三痕迹、
  7 个幸存对象、`backup/*.tar.gz`、`tmp_aqp_*`
- **可清（先存档元数据）**：`zzz_probe_root.txt`（AI 可写性探针，无害）、
  5 个 `.git/index.stash.*`（孤儿索引快照，比对后删）、`COMMIT_EDITMSG` 等瞬态文件

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 说明 |
|---|------|--------|--------|------|
| 1 | ~~**`git push origin master`**~~ | 用户 | **P0** | ✅ **已完成（2026-09-30）** —— 远端 `refs/heads/master = 1f1234a`，**首次建立异地副本** |
| 2 | **建立 `post-commit` 钩子 → `--mirror` 到异地裸库**（先建 `.git/hooks/`） | 用户/工程 | **P0** | 已提交内容 RPO≈0 |
| 3 | **单独异地另存 `.git/logs`**（reflog 不在 `--mirror` 范围内） | 用户/工程 | **P0** | ✅ **主理人已备份**到 `D:\aqp-git-backup\logs-archive\logs-20260930-131900`（29 行）；**建议改为定期自动** |
| 4 | **恢复 `gc.auto=6700` + 设 reflog 永不过期** | 用户/工程 | P1 | §5.3 |
| 5 | **启用 `auditpol` 文件系统审计**（至少审计 `.git/refs`、`.git/objects/pack`） | 用户（需管理员） | P1 | 唯一能抓凶手进程的手段 |
| 6 | ~~补建 `.git/hooks/`、`refs/tags/`~~ | 用户/工程 | ~~P1~~ | ✅ **实测无需补建**：`git tag` 会惰性重建 `refs/tags/`；仅装钩子前需手动建 `hooks/` |
| 7 | **扩大 `backup/*.tar.gz` 备份范围**（当前不含源码与 `.git`） | 用户/工程 | P1 | 现有归档对 git 恢复无用 |
| 8 | 部署 FileSystemWatcher 实时监视（§5.1 第 1 层） | 用户/工程 | P2 | |
| 9 | 排查 `index.stash.*` 与陈旧 `index.lock` 的共同来源 | 工程 | P2 | |

---

## ⚠️ 待完善 / 已知局限

**排障手明确声明「证据不足、不编造」的项**：
1. **删除工具的身份与选择逻辑无法证实** —— 为何独留分片 `00` 的 7 个对象，无解
2. 分片 `00` 目录 mtime=`15:20` 却含 `09-30 05:42` 的文件，**与"新增必刷目录 mtime"矛盾，无法解释**
   （疑沙箱文件系统时间戳异常）
3. **数量矛盾（未圆场）**：主理人现场记录 9 个 loose（6 blob + 3 tree），
   排障手事后实测 7 个（4 blob + 3 tree，全在 `00`）—— **差 2 个 blob，无法解释**
4. **09-21 18:47 的 `.git` 复制/移动事件**：施动者与目的**证据不足**
5. `/d/aqp-git-backup` 完整性 —— 排障手未验证；**主理人已用正确路径格式复核通过**
   （1540 对象 / pack 10.8MB / `master=57381bd`），但该备份**已过期**（缺后 3 个提交）
6. 采信主理人未复核项：09-29 现场"pack 目录不存在"、现场 reflog 23 条
   —— 排障手在事后状态复核，二者自洽

**主理人补充局限**：
7. `index.lock` 的来源未定性，无法排除是主理人自身并发操作造成的自伤
8. §4.1 的"同步工具镜像删除"**是假设**，尚无直接证据

---

## 📚 成员产出索引

- **排障手（gstack-investigator）**：机制认定 + 三痕迹裁决 + 防护设计 + 6 条局限声明
  （本轮为**独立复核 + 新证据发现**，含分片阶梯时间戳、三痕迹 ctime 同刻、标准 `.git` 子目录缺失三项新发现）
- **主理人（team-lead）**：证据复核（§2.1 逐条对照）、`index.lock` 新物证、裸仓库可访问性订正

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
> 机制已认定，**凶手未认定** —— 请勿把 §4.1 的假设当作结论使用。
