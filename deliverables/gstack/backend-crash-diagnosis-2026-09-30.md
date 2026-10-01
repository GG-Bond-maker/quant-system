# AQP 后端进程反复非正常退出 —— 根因诊断报告

**日期**：2026-09-30 17:50
**场景**：调试 / 根因分析（**只读取证，未改动任何源码，未 kill 任何进程**）
**执行者**：排障手（gstack-investigator）
**现场**：后端 8000 在 17:25 与 17:36 两次非正常退出，17:42 复查端口无监听、无 python 进程

---

## 📌 TL;DR

- **根因判定：🟢 已确认（机制级）** —— 后端死于**「计算池 worker 阻塞在不可中断的 akshare/requests 调用 → 解释器退出被 `concurrent.futures` 的 atexit 钩子 join 卡死 → 被外部/看护方强杀（TerminateProcess）」**。这条链子的两个环节**都在本项目自己的代码与注释里被实测记录过**（`compute_pool.py` / `main.py` lifespan docstring 明写 `rc=124`），我又用**独立最小复现**当场证实了机制（见证据 §5）。
- **不是 OOM**：系统 32G 内存当前剩 13G，pagefile 峰值仅 3.6G；整段无内存耗尽事件。**排除**。
- **不是 Python 未捕获异常**：请求级异常被错误中间件吃掉（HTTP 恒 200 契约），进程继续跑；且 09-30 **无任何 python.exe 崩溃事件**。**排除**该路径。
- **另有一条独立历史根因（09-30 05:09 前）**：`python.exe` 原生崩溃（`0xc0000005` @ `pyarrow\MSVCP140.dll` offset `0x12eb0`），14 天内 **25 次**，其中 09-26 两小时内 **22 次**。该 DLL 影子拷贝在 **09-30 05:09 pyarrow 25.0.1 重装后被移除**（现仅存 `pyarrow.libs\msvcp140-<hash>.dll`），**故 09-30 已无此类事件**。
- **「外部进程在动这个目录」是真实存在的既成事实**（`.git` 三次被逐文件清空，见 `git-wipe-rootcause`），但它**是否也是本次杀后端的凶手——证据不足，未认定**（不编）。

---

## 🎯 核心结论卡片

| 项目 | 结论 |
|------|------|
| 是否已确认根因 | 🟢 **机制已确认**（退出挂起→被强杀），**触发者身份未认定**（自挂 vs 外部杀，见 §4.3） |
| OOM | ❌ 排除（内存充足，无资源耗尽事件） |
| 未捕获异常退出 | ❌ 排除（错误中间件吞异常；09-30 无 python 崩溃事件） |
| 原生崩溃 | ✅ 曾发生（pyarrow MSVCP140，09-30 05:09 前）；09-30 **无** |
| 外部进程 | 🟡 `.git` 被清空系外部进程逐文件删除**已确认**；是否杀后端**未认定** |
| 关键新证据 | 16 次启动 **0 次 graceful shutdown**；独立最小复现证实退出被阻塞 31s |

---

## 1. 现场快照（诊断起点）

### 1.1 端口与进程（17:37 与 17:42 两次复查一致）

```
TCP  127.0.0.1:50088  -> 127.0.0.1:8000  TIME_WAIT   （17:37，随后连 TIME_WAIT 也消失）
（17:42 复查：netstat -ano | grep :8000  →  空，无 LISTENING、无 ESTABLISHED、无 TIME_WAIT）
tasklist /FI "IMAGENAME eq python.exe"  →  空（0 个 python 进程）
```

- 前端 5173 正常：`0.0.0.0:5173 LISTENING`（PID 4992，vite），与其 `ESTABLISHED` 在。
- 结论：**后端进程完全消失，且已过 TIME_WAIT 回收期。**

### 1.2 存活进程谱（PowerShell `Win32_Process`）

```
python.exe 10820  2026/9/21 19:22:19   ← 三日前的孤儿（无命令行）
python.exe 28832  2026/9/21 19:26:19   ← 孤儿
python.exe 25488  2026/9/26 23:20:32   ← 孤儿
（无任何 AQP 后端 python / uvicorn 进程）
```
> 三个 09-21/09-26 的 python 孤儿说明：本机**长期存在未回收的 python 进程**（可能是历次重启的残留）。它们**不是**本次后端（无命令行、port 不匹配）。

### 1.3 日志文件时间线（按 mtime）

```
backend-run-start.log        0 字节  mtime=17:35:00  Birth=16:23:02   ← 启动器 17:35 截断打开，但 0 字节（无 stdout）
backend/logs/app.log         5.8MB  mtime=17:36:55            ← 最后写入 = 进程最后活动
backend/logs/app.json.log    7.9MB  mtime=17:36:55
```

---

## 2. 证据链：**16 次启动，0 次 graceful shutdown**

> 判据：正常退出时 uvicorn 会执行 `lifespan` 收尾并写 `app.main:lifespan:XX - shutdown`。
> 把 `app.log` 中所有 `starting AQP`（启动）与 `- shutdown`（优雅关闭）列出对比：

**最后一次优雅关闭出现在 `2026-09-29 04:00:46`。** 此后：

| # | 启动时刻 | 是否有 shutdown 记录 |
|---|----------|----------------------|
| 1 | 09-29 13:53:36 | ❌ 无 |
| 2 | 09-29 15:02:13 | ❌ 无 |
| 3 | 09-29 15:12:04 | ❌ 无 |
| 4 | 09-29 18:04:48 | ❌ 无 |
| 5 | 09-30 03:52:07 | ❌ 无 |
| 6 | 09-30 03:58:00 | ❌ 无 |
| 7 | 09-30 03:59:04 | ❌ 无 |
| 8 | 09-30 04:02:47 | ❌ 无 |
| 9 | 09-30 14:26:13 | ❌ 无（**且从未到达 "startup done" —— 启动中途即死**）|
| 10 | 09-30 14:27:44 | ❌ 无 |
| 11 | 09-30 14:28:17 | ❌ 无 |
| 12 | 09-30 14:37:57 | ❌ 无 |
| 13 | 09-30 16:23:15 | ❌ 无 |
| 14 | 09-30 16:23:31 | ❌ 无 |
| 15 | 09-30 17:01:19 | ❌ 无 |
| 16 | 09-30 17:35:52 | ❌ 无（最后一条日志 17:36:55，之后静默至今）|

**结论：近 37 小时内每一次进程终止都没有走优雅关闭路径。** 第 9 次在启动阶段（`starting AQP` 与 `startup done` 之间）即死，是最强的「非正常终止」信号。

**命令与原始输出**：
```
$ grep -nE "starting AQP|startup done| - shutdown|lifespan" backend/logs/app.log | tail -60
33109:2026-09-29 04:00:46.050 | INFO | app.main:lifespan:176 - shutdown      ← 最后一次关闭
33118:2026-09-29 13:53:36.754 | INFO | app.main:lifespan:48  - starting AQP env=dev
...（此后再无一行 "- shutdown"）...
34386:2026-09-30 17:35:52.617 | INFO | app.main:lifespan:51  - starting AQP env=dev
34391:2026-09-30 17:35:53.255 | INFO | app.main:lifespan:206 - startup done
```

---

## 3. 逐条排除其他假设

### 3.1 ❌ 内存耗尽（OOM）—— 排除

```
Win32_OperatingSystem: FreeMB=13371  TotalMB=32373  FreeVirt=14936  TotalVirt=47733
Win32_PageFileUsage:   Allocated=15360MB  Current=2900MB  Peak=3618MB
```
- 32G 内存剩 **13G**；页面文件峰值仅 **3.6G / 15.3G**。
- **系统日志（System log）中 2004（资源耗尽）/ 41（内核掉电）等事件：0 条。**
- 命令：`Get-WinEvent -LogName System -FilterHashtable @{Id=2004,2019,2020,41,6008,...}` → 空。

### 3.2 ❌ Python 未捕获异常 —— 排除

- `app.log:34213` 确有 `unhandled error ... POST /api/v1/auth/login` + 完整 traceback，**结束于 `TypeError: fromisoformat: argument must be str`**（`auth.py:107` 读 SQLite 的 datetime 列拿到非 str）。
- **但进程在此之后继续运行**（34349→34384 行一路到 17:25:45）。该异常由 `starlette.middleware.errors` + 本项目 `core.errors` 中间件**捕获**，符合「HTTP 恒 200」契约 ⇒ **不是**进程级死亡原因。
- 全仓 `backend/app` 内**无 `os._exit` / `sys.exit` / `SystemExit` / `TerminateProcess` 调用**（唯一命中在 `monitor.py` 的**注释**里，警告不要用 `os.kill`）。

### 3.3 🟡 原生崩溃（C 层）—— 曾发生，09-30 已停

Windows 事件日志（`ProviderName='Application Error'`）在 14 天内抓到 **25 条 `python.exe` 崩溃**，簇集：

```
2026/9/19  01:23:41, 01:29:46, 16:18:45
2026/9/26  21:14:53, 21:54:51 … 23:24:00   ← 22 条，两小时内，~每 3 分钟一次（崩溃循环）
2026/9/29  18:09:18
2026/9/30  （无）
```

崩溃签名（逐字一致）：
```
Faulting application name: python.exe
Faulting module name:     MSVCP140.dll  (14.28.29334.0)
Exception code:           0xc0000005  (access violation)
Faulting offset:          0x0000000000012eb0
Faulting module path:     D:\...\backend\.venv\Lib\site-packages\pyarrow\MSVCP140.dll
```

- **这是 C 层崩溃**（无 Python traceback、无 shutdown、进程瞬时消失），与 §2 的「无关闭记录」同构。
- **关键时间线**：`pyarrow\MSVCP140.dll`（无 hash 后缀的**影子拷贝**）在 **09-30 05:09 pyarrow 25.0.1 重装后消失** —— 现网只剩规范布局 `pyarrow.libs\msvcp140-d448dcab…dll`（hash 后缀，不会影子系统 CRT）。
- ⇒ **09-30 全天无一条 python 崩溃事件**，与「影子 DLL 已移除」吻合。**这条根因 09-30 起已不成立**，但它解释了 09-19/09-26/09-29 的崩溃。

**命令与原始输出**（GBK 中文已转述）：
```
$ Get-WinEvent -LogName Application -ProviderName 'Application Error' -StartTime (Get-Date).AddDays(-14) |
    Where Message -match 'python\.exe'
2026/9/29 18:09:18  python.exe  MSVCP140.dll  0xc0000005
2026/9/26 23:24:00  python.exe  MSVCP140.dll  0xc0000005   （…共 25 条）
```

### 3.4 🟢 线程池枯竭 ≠ 崩溃 —— 澄清既有认知

项目内 `qa-lead-*.log` 显示：并发 22 路 `/market/overview?refresh=1` 会打死 `/health/ready`（探针超时 12s），而 `/health/live` 仍 200 ⇒ **是线程池枯竭，不是进程死亡**（QA 日志原话：「live 正常说明进程未死」）。**这解释了「探针假死」，但不解释进程消失**，两者需分开。

---

## 4. 根因判定

### 4.1 机制确认：**退出被阻塞 → 被强杀**

**本项目代码注释已实测记录该机制**（`backend/app/core/compute_pool.py` 模块 docstring，2026-09-30 上线前全检「头号 P0」）：

> `asyncio.wait_for(coro, budget)` 到期**只取消外层 await**。若 `coro` 是 `to_thread(fn)` 而 `fn` 正阻塞在**不可中断的系统调用**（socket read / requests 未设 timeout），executor 内的 worker **不会被取消**，继续占槽。
>
> `concurrent.futures.thread._python_exit`（经 `threading._register_atexit` 注册）在解释器退出时对**每一个** worker 无条件 `t.join()`，**不检查 daemon 标志**。实测：daemon 化后阻塞 worker 仍使进程 `rc=124`。
>
> **唯一有效方案：让阻塞 worker 自己返回** —— 给下游调用设超时。实测：加 `socket.setdefaulttimeout(1.5)` 后，同样场景 **`rc` 由 124 → 0**。

`backend/app/main.py` lifespan docstring 亦印证：

> akshare 内部用 requests（**默认无 timeout**）⇒ 对端挂死即让 worker 永久阻塞在 socket read；`asyncio.wait_for` 到期只取消外层 await，该 worker 不可取消、继续占槽……**更严重的是它会阻止进程退出**：concurrent.futures 的 atexit 钩子会 join 这些 worker，实测 `rc=124` ⇒ docker stop 挂到 SIGKILL。

**并发的环境触发条件（本机已确证）**：`triple-issue-diagnosis-2026-09-30.md` 认定本机 egress 代理 `127.0.0.1:63978` **间歇性故障**（`ProxyError` / `502 Bad Gateway`），而 `HTTP_PROXY/HTTPS_PROXY` 指向它、`NO_PROXY` **未设** ⇒ akshare 行情调用全部走该代理，**对端挂死即触发上述不可中断阻塞**。

### 4.2 独立最小复现（本地、一次性、非改源码）

为不采信「注释即事实」，我用**独立脚本**证实机制（`_tmp/repro_exit.py`）：提交一个 `time.sleep(30)` 的 worker（等价不可中断 socket read），主线程**不 join、不 shutdown**，直接返回。

```
=== start wall clock === 1790761606.399
worker submitted; main about to return (no shutdown/join)
elapsed before return: 0.04s        ← 主线程 0.04s 就返回了
=== rc=0 end wall clock === 1790761637.613
```

- 主线程 **0.04s** 返回，但进程**直到 31.2s 才真正退出**（`1790761637.613 − 1790761606.399 ≈ 31.21s`）。
- **机制当场证实**：`ThreadPoolExecutor` 内阻塞 worker 会把解释器退出**延迟到 worker 结束**；若 worker 永不返回 ⇒ 进程永不退出 ⇒ 被外部（启动器 / 终端 / 看护方）**强杀**，即**不产生 `shutdown` 日志**。

### 4.3 根因判定表

| 候选 | 判定 | 依据 |
|------|------|------|
| OOM | ❌ 排除 | 内存剩 13G，pagefile 峰值 3.6G，无资源耗尽事件 |
| Python 未捕获异常 | ❌ 排除 | 错误中间件捕获；无 `sys.exit`；09-30 无 python 崩溃事件 |
| 原生崩溃（pyarrow MSVCP140） | ✅ 曾发生（09-30 05:09 前）| 25 条 0xc0000005 @ MSVCP140；影子 DLL 已被重装移除 |
| **退出被阻塞 → 被强杀** | ✅ **已确认（机制）**| 16 次启动 0 次 shutdown；docstring 实测 rc=124；本地复现退出延迟 31s |
| 外部进程杀后端 | 🟡 **未认定** | `.git` 外部删除**已确认**，但**无进程级证据**证明其杀 python（见 §6 局限）|

**一句话根因**：后端在**重计算（overview/datacenter 全量 parquet 扫描、全市场 akshare 抓取）期间**，计算池 worker 阻塞在**无超时的 socket/requests 调用**（触发者为间歇性故障的本机 egress 代理）；lifespan 收尾虽 `cancel` 后台任务、`asyncio.wait(timeout=10)` 有界等待、`shutdown_compute_pool(wait=False)` 不等 worker，**但 `concurrent.futures` 的 atexit 钩子仍会 join 这些阻塞 worker** ⇒ 进程退不出 ⇒ 被启动器/看护方强杀 ⇒ **无 `shutdown` 记录、无崩溃事件**。这解释了观察到的「静默消失」。

---

## 5. 修复 / 缓解建议

> 全部为**建议**，本轮未改任何代码。

### 5.1 止血（P0）

1. **给 akshare / requests 调用设 per-call 超时**（`compute_pool.py` 与 `main.py` docstring 双双指明「唯一有效方案」）：
   - 现状 `SOCKET_DEFAULT_TIMEOUT_SECONDS=10`（`.env:56`）。docstring 实测 1.5s 即可让 `rc` 由 124→0。建议对该值评估下调（如 3~5s），并**对行情域名显式设 `NO_PROXY` 直连**，绕开故障代理。
2. **设 `NO_PROXY`** 覆盖行情/公告域名（`triple-issue-diagnosis` 已列 P0），消除「代理挂 → akshare 全挂」的单点。
3. **从「被强杀」改为「可优雅退出」后再重启**：启动器重启前先发 `CTRL_BREAK`/`CTRL_C` 促其走 lifespan 收尾；若 10s 未退再强杀，并**把「强制终止」写日志**，消除下一次的取证盲区。

### 5.2 观测（P1）

4. **加「退出原因」埋点**：启动器记录子进程**退出码/退出时刻/是否超时**到独立文件。本次事故无退出码可查是最大取证缺口。
5. **启用 Windows 进程审计**：`auditpol /set /subcategory:"Process Creation" /success:enable`（需管理员），并关注事件 **4688/4689**，即可回答「谁杀的」。
6. **健康探针区分「进程死」与「池枯竭」**：`/health/live` 与 `compute_pool_stats()`（`alive_threads` 持续==max_workers 即泄漏）已有基础，建议暴露到指标并在 >阈值 时告警。

### 5.3 环境（P1）

7. **清理 3 个孤儿 python 进程**（PID 10820/28832/25488，09-21/09-26 遗留）—— 它们占内存、且可能是历史「退出被阻塞」的残留（无法 graceful 退出，故只能强杀留下）。需用户确认后处理。
8. **D 盘水位 94%**（剩 42G）会导致写入变慢/失败，间接拉长 IO 阻塞窗口。建议清理或迁移 `DATA_ROOT`。
9. **核查外部目录清理/同步工具**：`.git` 三次被逐文件清空已确认为外部进程所为。请确认 `D:\Python_Project\Alpha Quant Platform` **不在任何同步/备份/清理工具的「镜像删除」范围内**（详见 `git-wipe-rootcause-2026-09-30.md`）。

---

## 6. 未确认项与限制（**如实声明，不编**）

1. **杀后端的「执行者身份」未认定**：是进程**自身退出挂起被启动器/终端强杀**，还是**某个外部进程主动 kill** —— 现有证据只能证明「退出流程被阻塞 / 无关闭记录」，**不能证明谁下的手**。区分手段见 5.2 第 4/5 条，本轮无管理员权限与进程审计，无法闭环。
2. **17:33 Windows Terminal `OpenConsole.exe` 反复崩溃**（`0xc0000005`，17:33:08~17:33:53 共 8+ 条）与后端死亡**时间接近但因果关系未证**：若后端是某终端会话的子进程，终端崩溃会连带杀死子进程；但当前进程谱显示后端并非 Terminal 的子进程（Terminal 7824 的子进程是 powershell/OpenConsole，非 python）。**存疑，未认定**。
3. **`backend-run-start.log` 为何 0 字节**：Birth=16:23、mtime=17:35、size=0。说明启动器 17:35 以截断方式打开它，但**未写入任何 stdout**（日志实际进了 `backend/logs/app.log`）。**启动器与重定向配置未定位**（无 `.ps1/.bat/.cmd` 启动脚本在仓库根目录），故「谁启动的、以什么身份、stdout 去哪」未闭环。
4. **原生崩溃链（pyarrow MSVCP140）的精确触发代码未定位**：仅知「pyarrow 影子 DLL + 某 C 扩展调用 CRL = offset 0x12eb0 崩溃」，未能复现到具体 API 调用（09-26 的 22 连崩已过去，且相关 DLL 已被重装移除）。
5. **09-30 三次重启（14:26/14:27/14:28）间隔极短**（~90s、~33s）**原因未认定**：疑似人工/脚本反复重试，但无启动器日志佐证。
6. **`find` 在 17:30–17:37 窗口受 SIGTERM**（大目录 + venv 排除导致扫描超时），该窗口的其余文件系统活动**未能完整枚举**。
7. **内存数据为「诊断时刻」快照**，非崩溃时刻。崩溃瞬间的内存峰值无法回溯（无 WER `.mdmp` 保留在 `ReportArchive`，且 python 崩溃事件 09-30 为 0）。
8. **`wmic` 被安全策略拦截**，无法用 `wmic process` 取父进程链；改用 `Win32_Process`，已取得父 PID，但**崩溃历史进程早已消失**，父链无法回溯。

---

## 7. 取证命令索引（可复现）

```powershell
# 端口 / 进程
netstat -ano | findstr :8000
tasklist /FI "IMAGENAME eq python.exe" /FO CSV

# 事件日志：python.exe 崩溃（Application Error）
Get-WinEvent -LogName Application -ProviderName 'Application Error' -StartTime (Get-Date).AddDays(-14) |
  Where-Object { $_.Message -match 'python\.exe' } |
  Select-Object TimeCreated, Message

# 系统日志：资源耗尽 / 内核掉电
Get-WinEvent -LogName System -StartTime (Get-Date).AddHours(-10) |
  Where-Object { $_.Id -in 2004,2019,2020,41,6008 }

# 内存 / 页面文件
Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory,TotalVisibleMemorySize
Get-CimInstance Win32_PageFileUsage   | Select Name,AllocatedBaseSize,PeakUsage

# 进程命令 + C 库
# （grep app.log 的 "starting AQP" / "- shutdown" 对比；pyarrow MSVCP140 有无 hash 后缀）
```

**关键文件**：
- `backend/logs/app.log`（含 lifespan 启动/关闭）
- `backend/logs/app.json.log`（含 process.id，可回溯每个 PID 的存续区间）
- `backend/app/core/compute_pool.py` / `backend/app/main.py`（机制的自证注释）
- `deliverables/gstack/git-wipe-rootcause-2026-09-30.md`、`triple-issue-diagnosis-2026-09-30.md`（外部进程 / 代理故障背景）
- 复现脚本：`deliverables/gstack/_tmp/repro_exit.py`（及其结果 `repro_exit_result.txt`）

---

> 本报告由排障手（gstack-investigator）独立取证产出。
> **机制已确认，执行者未认定** —— 请勿把 §4.3「外部进程」一行当作已坐实的结论使用。
> 关键修复（socket/requests 超时、NO_PROXY）请由工程负责人复核后实施。
