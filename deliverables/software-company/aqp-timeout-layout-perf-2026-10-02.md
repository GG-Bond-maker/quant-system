# AQP 超时一致性 · 界面布局 · 性能优化（2026-10-02）

> 三类问题的**定位原因**与**修复方案**，逐条附证据与验收。

---

## 一、TL;DR

| 类别 | 定位到的根因 | 修复 |
|---|---|---|
| **超时一致性** | 前端 **15 档**超时字面量散落在 ~25 个文件，**无单一登记点**；后端只能靠**手工快照**对齐（`timeout_guard.py` 注释里写「2026-09-27 核对」）⇒ **没有任何机器校验**，必然漂移 | 建**集中表** `src/api/timeouts.ts`；**57 处**字面量迁移；新增**跨栈一致性闸门**（4 项校验 + 自证伪）并接入 CI |
| **界面布局** | `PerformanceChart.tsx:199` **只监听 `window.resize`、缺 `ResizeObserver`** ⇒ 容器尺寸变化（数据到达 / 右栏变高 / 栅格切列）不触发 `chart.resize()`，而 canvas 像素尺寸在 `init` 时写死 ⇒ **图表被拉伸/压扁** | 改走全项目统一范式 `utils/useChart`（内部 `ResizeObserver` 观察**容器本身**） |
| **性能** | 两个 ETF 图表的 effect 依赖 `[option]` ⇒ **每次 option 变化都 `dispose()` + `init()` 重建 ECharts 实例**（实例抖动 + canvas 重建） | 同上：`useChart` **init 一次、复用实例**；新增 5 个测试把该语义钉死 |
| **性能 · 请求响应** | `research.py:609` **无条件 glob 全部年份全部标的**（10 倍成本量级），而同仓另 2 处都已正确裁剪 | 抽纯函数 + 终点按前瞻窗口外扩；新增 9 个测试（含**年末边界**）+ 2 组证伪 |
| **性能 · 资源加载** | — | **实测已良好，不改**：首屏只 277 KB，697 KB 的 echarts 已按需加载、未混入入口 |

> 布局与性能**指向同一处代码**，故一次改造同时修掉两个缺陷。

---

## 二、超时：前后端通信超时的一致性

### 2.1 定位原因（先取证，不猜）

**① 后端已声明不变量，但只靠手工清单维持**

`backend/app/core/timeout_guard.py` 模块 docstring 第 17-28 行原文：

> **不变量：服务端预算必须严格大于前端对同一路径的超时**
> 若服务端预算小于前端超时，就会出现"前端还在等、服务端已经回 504"的**误杀**。
> 全仓前端超时实测清单（`frontend/src/api/*.ts`，**2026-09-27 核对**）：…

`backend/app/core/config.py:105-112` 的注释同样是一段手工推算（「其余路径前端最长 180s，
故默认 240s 留有余量」）。**两处都是人写的、日期化的快照。**

**② 前端没有任何单一登记点**

实测全仓超时字面量分布：

```
17×120_000  15×30_000  9×60_000  6×180_000  4×20_000
 4×15_000    4×10_000  2×90_000  1×600_000  1×300_000  1×12_000  1×100_000
```

散落在 **~25 个文件**里（`src/api/*.ts` 15 个 + 页面若干）。**没有一处能回答"全仓最长前端超时是多少"。**

**③ 后果（真实的、非假设的）**

- 前端把某路径超时调大（如 180s → 300s）而**忘了**在后端 `_PATH_BUDGETS` 延长 ⇒
  后端仍按 240s 兜底 ⇒ **前端还在等、服务端已经回 504**（误杀）。
- 反向：后端留着一条前端早已不需要的预算（孤儿），说明**某一侧改过而另一侧没跟**。
- 也没有任何东西会 FAIL —— 这条不变量在闸门出现前是**纯注释**。

> ⚠️ 诚实披露一次**我自己的误判**：取证过程中我先用 `grep … | head -30` 看超时清单，
> 截断导致漏掉 `600_000`/`300_000`，一度得出"后端快照与实际不符"的结论。
> 全量重扫后确认**后端那两条配对是正确的**（660 > 600、330 > 300）。
> 这恰好证明问题所在：**靠手工比对两个散落的清单，连人都会读错**，必须机器校验。

### 2.2 修复方案

**① 新增集中表 `frontend/src/api/timeouts.ts`**

10 个语义化档位（数值即原生产值，**迁移零行为变更**）：

| 档位 | 值 | 语义 |
|---|---|---|
| `probe` | 10s | 事件流票据 / KPI 轻查询 |
| `default` | 15s | 客户端默认（`client.ts` 的 axios 默认值） |
| `aggregate` | 30s | 页内聚合：`/etf/*`（后端预算 4.5s）、`/desk/*` |
| `medium` | 60s | 数据集列表 / 血缘 / DAG 状态 |
| `extended` | 90s | 数据集与质量检查 |
| `research` | 100s | 研究页重查询 |
| `heavy` | 120s | 回测 / 训练 / 镜像 / 生产链路 |
| `export` | 180s | 导出与全量扫描 |
| `rerun` | 300s | 同步重跑全量流水线（后端预算 330s） |
| `optimize` | 600s | 参数寻优（后端预算 660s） |

外加 `LONG_PATHS`（**超过后端默认预算的路径必须显式登记**）与 `MAX_TIMEOUT_MS`。

**② 迁移 57 处字面量 / 15 个文件**（一次性脚本 + 逐文件计数，改完即删脚本）

```
backtest 2  client 2  datacenter 6  etf 1  market 1  monitor 5  notify 2
portfolio 2  production 12  research 11  screener 3  settings 5
stock 1  strategyBacktest 2  watchlist 2
```

**③ 新增跨栈一致性闸门 `frontend/scripts/check-timeout-consistency.mjs`**

解析前端 `TIMEOUT`/`LONG_PATHS` 与后端 `_PATH_BUDGETS`/`REQUEST_TIMEOUT_SECONDS`，**双向**校验：

| 编号 | 校验项 | 防的是什么 |
|---|---|---|
| **C1** | `LONG_PATHS` 每条路径，后端预算必须存在且**严格大于**该超时 | **误杀**（前端还在等，服务端已 504） |
| **C2** | 后端每条预算，前端必须有对应登记 | **孤儿预算**（一侧改过另一侧没跟） |
| **C3** | 后端默认预算必须**严格大于**所有未登记的档位 | 新增长超时却忘了延长后端 |
| **C4** | `src/api/**` 不得出现**裸超时字面量** | 「集中表」退化成摆设、值重新散落 |

**④ 修掉后端那份手工快照**

把 `timeout_guard.py` 里的「2026-09-27 核对清单」换成指向机器校验，并明确写下
**「不要再往本文件里抄前端超时值」** —— 消灭漂移源本身。

### 2.3 验收

```
前端 TIMEOUT 档位 10 个，LONG_PATHS 2 条；后端 _PATH_BUDGETS 2 条，默认预算 240s
✅ 超时一致性闸门通过（C1 误杀 / C2 孤儿 / C3 默认覆盖 / C4 集中登记）
```

**闸门自证伪 6/6**（`--self-test`）：基线通过 + C1×2 / C2 / C3 / C4 各一条违规夹具**全部被触发**。

🔴 闸门用**纯函数 + 合成夹具**做证伪，**刻意不改写任何生产文件** —— 本项目有过
"证伪脚本就地注入、宿主重复执行导致缺陷写回生产文件"的事故（2026-10-02）。

---

## 三、界面布局：元素被拉伸 / 尺寸失跟

### 3.1 定位原因

**症状**：图表容器"被拉扯放大"、与相邻卡片尺寸不一致。

**根因链（逐层有证据）**：

1. `frontend/src/pages/Etf/PerformanceChart.tsx:199-200`（修复前）：

   ```ts
   const onResize = () => chart.resize();
   window.addEventListener('resize', onResize);   // ← 只有这一个
   ```

2. 该图表的高度来自 **grid/flex 拉伸**（主区栅格 `flex-1` 分配自由空间）。
3. 「**窗口没变、容器却变高/变矮**」的时机 —— 数据加载完成、右栏内容变化、
   筛选切换、栅格从单列切双栏 —— **不会触发 `window.resize`**。
4. 而 zrender 的 canvas 是 `position:absolute`，**像素尺寸在 `init` 时刻写死**、
   不参与 CSS 自适应 ⇒ 图表停留在旧尺寸被拉伸/压扁。

**关键旁证**：同一页面的 `Etf/index.tsx` 的 `Chart` 组件**已经**用了
`ResizeObserver`，并在注释里写明了上述理由（"**必须**观察容器本身，只监听 window.resize 不够"）。
⇒ 即 **D-6 只修了一半**：`Etf/index.tsx` 修了，`PerformanceChart.tsx` 漏了。
项目内另有 4 处先例（`EtfDetail` / `MarketHeatmap` / `Portfolio` / `utils/useChart`）。

**同时核查的溢出裁剪（D-1 建议 #2）**：`Etf/index.tsx` 已落实 ——
主区栅格 `grid min-h-0 flex-1 …`（579）、内栅格（583/662）、图表容器（719）
一律带 `min-h-0`（解除 flex 子项默认 `min-height:auto` 造成的溢出），
滚动区显式 `overflow-y-auto`（665/761），表格 `overflow-x-auto overflow-y-auto`（624）。
**结论：无需再改**，属"已落实"而非"漏改"。

### 3.2 修复方案

把 `PerformanceChart.tsx` 的手写 effect 换成全项目统一范式 **`utils/useChart`**：

```ts
const ref = useChart(option);
```

`useChart` 的三条语义正好覆盖根因：
- `useEffect([node])` → `echarts.init(node)` **只在节点挂载时一次**；
- `new ResizeObserver(() => inst.current?.resize()); ro.observe(node)`
  → **观察容器本身**（而非窗口），容器一变就 `resize()`；
- `useEffect([node, option])` → `inst.current.setOption(option, true)` **复用实例**。

原文件里「**必须 notMerge**」的关键知识（本图 series 数量会变：热门榜 A 股不足 5 只、
`okSeries` 会剔除 `status !== 'ok'` 或 `points.length < 2` 的序列 ⇒ merge 会把上一次的曲线
**残留**在图上）已完整保留在注释中 —— `useChart` 内部正是 `setOption(o, true)`。

---

## 四、性能：渲染效率与请求

### 4.1 定位原因

**① 图表实例抖动（主因）**

两个图表的 effect 都依赖 `[option]`：

```ts
useEffect(() => {
  const chart = echarts.init(ref.current);   // ← 每次 option 变化都重建
  chart.setOption(option, true);
  …
  return () => { …; chart.dispose(); };      // ← 每次变化都销毁
}, [option]);
```

⇒ 数据一刷新（切周期 / 切排序 / 筛选 / 主题切换）就 **`dispose()` + `init()` 一遍**：
实例构建 + canvas 重建 + `ResizeObserver` 重挂。

`Etf/index.tsx` 的原注释**自己预留了这一步**：

> 「将来若把容器改成 init 一次以省掉实例抖动，此处的 notMerge 才会真正开始承担作用。」

### 4.2 修复方案

两个图表统一走 `useChart` ⇒ **init 一次、复用实例**，option 变化只走
`setOption(option, true)`。同时：
- 实例不再每次重建 ⇒ 消除实例抖动与 canvas 重建开销；
- notMerge 由"防御性加固"变为**真正生效**（series 变短时不再残留上一次的曲线）。

**请求侧（附带核查）**：审计 G-1「筛选参数无效导致无意义请求」已由前序修复覆盖 ——
`FilterPanel` 改为**草稿 + 显式应用**（不再逐字外呼、不再双请求），且 `/scale`、`/flow`
由后端**回显作用域**、前端如实渲染"不随筛选变化"。故本轮不再改动请求链路。

### 4.3 新增回归测试（`src/utils/useChart.test.tsx`，5 个）

jsdom 没有布局引擎，但**用 spy 可以精确观测**这三条语义：

| 断言 | 守的是 |
|---|---|
| `option` 连变两次后 `init` **仍只调用 1 次**、`dispose` **未被调用** | **G-5 不复发** |
| 每次 `setOption` 的第二参为 `true` | notMerge 语义（series 残留） |
| `ResizeObserver.observe` 的实参**是容器节点本身** | **D-6 不复发** |
| 未注册 `window.resize` 监听 | 不与 `ResizeObserver` 重复触发 |
| 卸载时 `disconnect` + `dispose` 各一次 | 无泄漏 |

同时给 `src/test/setup.ts` 补了 jsdom 缺失的 `ResizeObserver` 最小桩
（否则任何渲染图表的测试都会 `ResizeObserver is not defined` 崩掉）。

### 4.4 请求响应速度：分区读取裁剪审计（G-4）

审计 G-4：「批量读分区前按业务日期跨度裁剪（**无条件 glob = 10 倍成本**）；
⚠️ **只裁起点安全**，**终点必须留足前瞻窗口**，否则末日样本被截成 null、**静默丢样本**。」

**全仓排查**（`read_parquet_columns` 全部调用点，共 3 处）：

| 位置 | 现状 |
|---|---|
| `market.py:199` `_heat_from_local` | ✅ 已按年裁剪（`symbol=*/year={year}…`） |
| `market.py:624-628` 推荐评估 | ✅ 已裁剪**且留了前瞻窗口**（`_fwd_days = horizon * 3 + 10`） |
| **`research.py:609` `ml_lab_yearly`** | 🔴 **无条件 glob `symbol=*/year=*.parquet`**（全年份 × 全标的），而下游只用到与 predictions 相交的行 |

⇒ 3 处中 **2 处正确、1 处漏网**，且漏网的正是记忆里那条"元教训"所指的类型
（**修掉一个坏惯用法后必须全仓 grep 同型写法** —— 本次即由全仓 grep 定位）。

**修复方案**：把裁剪逻辑抽成两个**纯函数**，并让终点按前瞻窗口外扩：

```python
_BAR_FORWARD_WINDOW_DAYS = 10

def _bar_year_window(pred_min, pred_max, *, fwd_days=_BAR_FORWARD_WINDOW_DAYS) -> tuple[int, int]:
    """🔴 只裁起点安全；终点必须留足前瞻窗口 —— shift(-1) 需要 pred_max 之后的
    下一个交易日收盘价；卡在当年会让年末样本 ret=null，被 drop_nulls 静默丢弃。"""
    return pred_min.year, (pred_max + timedelta(days=fwd_days)).year

def _prune_bar_files(files, y_from, y_to):
    return [f for f in files if y_from <= int(f.name.split("year=")[1][:4]) <= y_to]
```

**为什么抽成纯函数**：该端点有 `n < 500` 的最小样本门槛，小夹具**无法**端到端跑出输出
⇒ 端到端测不了"裁剪是否正确"。抽成只依赖日期与文件名的纯函数后，
可以直接单测**最危险的那一环**（年末边界），无需文件系统。

**新增 `tests/test_partition_pruning_contract.py`（9 个测试）**，覆盖：
- 🔴 **年末边界**：`pred_max = 2022-12-30` ⇒ `y_to` **必须是 2023**（否则静默丢样本）；
- 前瞻窗口天数可调（`fwd_days=0/1/2` 的年份归属）；
- 闭区间包含两端、单年、无命中返回空、`year=2022.snappy.parquet` 的真实文件名解析；
- **对照断言**：裁剪后分区数严格少于未裁剪（证明裁剪真的省了分区，而非空转）。

### 4.5 资源加载：实测结论（**无需改动**）

不发明改动，先实测：

| 检查 | 结果 |
|---|---|
| 入口 `index.html` 引用 | 只有 `index-*.js`（277 KB raw / ~94 KB gzip）+ 1 个 CSS |
| echarts chunk（**697 KB** raw / 231 KB gzip） | **不在入口引用里** ⇒ 已拆为**按需加载**（仅图表页拉取） |
| 入口 chunk 内含 `zrender` / `lightweight-charts` | **0 命中** ⇒ 未混入图表库 |
| 入口 chunk 内 `react-dom` | 2 命中（正常） |

⇒ **资源加载已良好**：首屏只付 277 KB，最重的 echarts 延迟到图表页。
本轮**不做**投机性优化（如给图表 chunk 加 `modulepreload` 反而会让所有用户预取
他们可能永不打开的图表库）。

---

## 五、验收汇总

| 项 | 命令 | 结果 |
|---|---|---|
| 前端单测 | `npm test` | ✅ **21 passed**（3 文件：FilterPanel 6 / layoutContract 10 / useChart 5） |
| 风格闸门 | `npm run style:check` | ✅ 通过 |
| 导航闸门 | `npm run nav:check` | ✅ 通过 |
| **超时一致性闸门** | `npm run timeout:check` | ✅ 通过（C1/C2/C3/C4） |
| **闸门自证伪** | `npm run timeout:check -- --self-test` | ✅ **6/6 条规则可被夹具触发** |
| 类型 | `npx tsc -b --force` | ✅ rc=0 |
| 构建 | `npm run build` | ✅ `✓ built in 11.41s` |
| 体积上限 | `npm run bundle:check` | ✅ 通过（echarts 696 KB raw / 231 KB gzip） |
| 后端超时测试 | `pytest tests/test_timeout_guard.py` | ✅ **14 passed** |
| **分区裁剪测试** | `pytest tests/test_partition_pruning_contract.py` | ✅ **9 passed** |
| **后端契约证伪** | `python scripts/dev_falsify_contracts.py` | ✅ **4/4 全部捕获**，源文件逐字节还原 |
| 后端静态门禁 | `ruff check app tests scripts ../scripts --select F,E9` | ✅ All checks passed |

**CI 变更**（`.github/workflows/ci.yml`）：新增 `npm run timeout:check` 与
`npm run test`（前端单测），`bundle:check` 亦已接入；`npm run build`（含 `tsc -b`）原本已在。

---

## 六、遗留与说明

- **L-1（环境坑，会掩盖真实回归）**：`npm run build` 会被宿主 safe-delete shim 的
  `emptyDir(dist/assets)` 拦住（`genie-trash … ETIMEDOUT`）⇒ **必须先删 `dist` 再构建**。
  否则 build 失败而 `bundle:check` 读到**旧 dist** 会**假通过**。
- **L-2**：页面层仍有少量超时字面量（如 `DataCenter/index.tsx` 的 `12_000`）与
  轮询间隔（`1_000`/`5_000`）。本轮闸门只覆盖 `src/api/**`（超时位的误报风险最低）；
  若要一并收口，需先把"超时"与"轮询间隔"在语法上区分开（如后者改用 `POLL.*` 命名空间）。
- **L-3**：`_PATH_BUDGETS` 目前 2 条。闸门的 C1/C2 已保证两侧同步；**新增长超时路径时
  两侧都要改**，闸门会拦住漏改。
- **诚实声明**：布局修复的**真实几何表现**（容器变化后图表是否真的贴合）**无法**由 jsdom 断言
  （无布局引擎），本轮的证据是「结构 + spy 层面的不变量」；**仍需人工目视验收**。
