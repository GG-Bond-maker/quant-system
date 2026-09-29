# B2 · 领域纯函数层审核报告（2026-09-18）

审核员：B2（domain 层）　范围：`backend/app/domain/*.py`（14 个模块，约 2570 行）
方法：逐文件静态阅读 + **真实运行验证**（合成数据小片段 + 只读核对生产 parquet）。
纪律：只报告可定位到「文件 + 行号 + 触发条件」的问题；未跑通的标注「疑似，需验证」。

实测命令统一前缀：

```powershell
$base='D:\Python_Project\Alpha Quant Platform\backend'
& "$base\.venv\Scripts\python.exe" ...      # 在 backend/ 下执行
```

定向 pytest（父审核员提供的补丁，已实测通过）：

```powershell
$env:PYTHONPATH="$base\.tmp_testrun"; $env:TEMP="$base\.tmp_testrun"; $env:TMP=$env:TEMP
& "$base\.venv\Scripts\python.exe" -m pytest tests/test_domain.py tests/test_domain_purity.py `
    tests/test_equal_weight.py tests/test_attribution_weight_conservation.py -q -p audit_mkdtemp_fix
# → 32 passed in 1.96s
```

---

## 0. 逐文件结论行

| 文件 | 结论 |
|---|---|
| `limit.py` | **P1 ×1**：新股特殊期按自然日判定；**P2 ×3**：920 段北交所误判、`MARK_UP["bse"]` 永不可达、ST 无历史口径；核心公式与 ROUND_HALF_UP 正确（已实测）。真实调用方 `universe.py` 在多个口径上偏离本模块。 |
| `adjust.py` | **P1 ×1**：`fill_null(1.0)` 使因子缺行日退化为不复权。公式与除权日处理正确（已实测）。**全模块在生产路径下 0 调用方**（死代码）。 |
| `a_share_rules.py` | **P1 ×1**：920 段北交所代码→`.SH`；**P3 ×4**：4 个无调用方函数、无过户费、后缀静默丢弃。印花税单边（仅卖出）实现正确。 |
| `calendar.py` | 该文件未发现 P0–P2 问题（纯函数、周末过滤 + 集合判定、20 天上限有显式报错）。唯一系统性风险在数据层：`trade_calendar` 为空时降级为「全非交易日」，见 F9。 |
| `chip.py` | **P3 ×2**（`current_price` 真值判断、全零成交量时仍产出筹码）。换手率口径经真实数据核对**正确**（小数口径），本文件未发现 P0–P2。 |
| `factor_processing.py` | **P2 ×2**：完全共线列无法正交化（输出重复列）、`inf` 不被去极值（NaN 被保留而 inf 漏过）。截面处理链无水泄漏，`CrossSectionalScaler` 无拟合状态（PIT 安全）。 |
| `indicators.py` | **该文件未发现 P0–P2 问题**（已实测：无中心化窗口、无前视、初始窗口为 null、除零有保护）。 |
| `metrics.py` | 该文件未发现 P0–P2 问题；**P3 ×1**（`if pct` 真值判断的同类隐患在 limit/universe 侧）。PSR/DSR 四阶矩公式已核对正确。 |
| `neutralize.py` | **P1 ×1**：`apply_industry_neutral` 在多日期分组下必然 `IndexError`（升序索引写回 numpy）。中性化数学本身正确（残差行业均值 ~5e-14，与 log(mcap) 相关 0）。**全模块生产 0 调用方**。 |
| `optimizer.py` | **P1 ×1**：`weight_cap` 不可行时静默返回 `Σw<1`（50% 闲置现金）；**P2 ×1**：`mvo` 在 μ=0 时对 `risk_aversion` 完全不敏感。LW 收缩/`_project_simplex` 数学已核对正确；RMT 去噪经真实结构复算**未发现**破坏相关的证据（我最初的一版判读有误，已纠正，见 §2.10）。 |
| `portfolio.py` | **P1 ×1**：`data_warnings` 的「疑似退市/停牌缺口」分支永不触发（ffill 后自比较）；**P1 ×1**：基准首值为 0 时整条 `nav_curve` 的 benchmark 变成 NaN/Inf（触发运行时告警 + 非 JSON 合规）。**P2 ×1**：接受 Σw∈[0.99,1.01] 但不归一。 |
| `research.py` | **P1 ×1**：`style_exposure` 对缺列返回 `0.0`（假装无暴露）；**P2 ×2**：`execution_impact_sim` 在成交额缺失时输出 `unfilled=NaN`、`avg_impact_bps=0.0`；`factor_ic_table` 的去极值按列（时序）而非按截面。 |
| `risk.py` | **P2 ×1**：年化夏普口径混用（分子年化 rf、分母日度 rf）；**P2 ×1**：`close>0` 过滤把 0 价整根剔除，收益序列出现跨日合成跳变。`benchmark="沪深300"` 硬编码标签。 |

---

## 1. 实测证据索引（全部真实输出）

| 探针 | 覆盖内容 |
|---|---|
| `.tmp_b2/b2_probe1.py` | limit 板块/新股/舍入、code_to_symbol、normalize_code |
| `.tmp_b2/b2_probe2.py` | adjust 的 qfq/hfq 基准与因子缺行；indicators 初始化窗口 |
| `.tmp_b2/b2_probe3.py` | Brinson 守恒、style_factor_returns、capacity、optimizer 约束、risk_metrics |
| `.tmp_b2/b2_probe4.py` / `probe5.py` | LW+RMT 协方差在单因子结构下的行为（含一次自我纠错） |
| `.tmp_b2/b2_probe6.py` | neutralize / factor_processing / research |
| `.tmp_b2/b2_probe7.py` / `probe8.py` | chip 换手率口径、portfolio 权重/告警/基准 |
| `.tmp_b2/b2_probe9.py` | weight_cap 可行性矩阵、cov_window 影响、mvo μ=0 |
| `.tmp_b2/b2_probe10.py` | NaN/Inf 传播、`_project_simplex` |
| `.tmp_b2/b2_probe11.py` | `symmetric_orthogonalize` 不变量 |
| `.tmp_b2/b2_probe12/13/14.py` | **只读**核对生产 parquet：turnover 口径、涨跌停舍入、哨兵行 |

---

## 2. 逐条问题（完整论证）

### 2.1 【P1 · Bug · 确定】limit.py:69-73 —— 新股「前 N 日不设涨跌幅」按自然日而非交易日

```python
# limit.py:69-73
def is_new_issue_first_n_days(attrs, today, n) -> bool:
    if not attrs.list_date or today < attrs.list_date:
        return False
    return (today - attrs.list_date).days < n          # ← 自然日
```

交易所口径是**前 5 个交易日**（北交所首日）。凡上市日临近长假的新股，长假一过就已「过期」，
本应无涨跌幅的交易日被错误地套上 ±20%/±10%。

实测（`b2_probe1.py`，2024-09-30 上市的创业板新股，国庆假期 10/1–10/7）：

```
2024-09-30 days_since_list= 0 -> limit_pct=0    limit_up=99999999.99
2024-10-08 days_since_list= 8 -> limit_pct=0.20 limit_up=12.00     ← 10/8~10/14 是真实的第 1~5 个交易日
2024-10-14 days_since_list=14 -> limit_pct=0.20 limit_up=12.00
```

10/8 是上市后**第 1 个交易日**（应不设涨跌幅），却算成 ±20%。同一函数被同一自然日口径复制到
数据层（`universe.py:219-224` 与 `425-430`），因此全历史 universe 的 `is_new_issue`
在长假后同样提前失效。反向情形（上市日为周日、`days_since_list=0`）会给出不设限，见
`b2_probe1.py` 的 P3 段输出（2025-01-05 为周日）。

最小验证：`calc_limit_prices(10.0, InstrumentAttrs("301234.SZ","301234",list_date=date(2024,9,30)), date(2024,10,8))`
期望 `pct==0`（不设限），实测 `pct==Decimal("0.20")`。

### 2.2 【P2 · Bug · 确定】limit.py:62-66 —— 北交所新代码段 920xxx 被判为主板；MARK_UP["bse"] 不可达

```python
if re.fullmatch(r"(30\d{4}|688\d{3})", code):   # 62
    return "chinext_star"
if re.fullmatch(r"[48]\d{5}", code):            # 64
    return "bse"
return "main"
```

北交所自 2024 年起启用 `920xxx` 代码段，既不匹配 `[48]\d{5}` 也不匹配创业板/科创板，
落到 `return "main"`。实测 `b2_probe1.py` P2：

```
920001: board=main   code_to_symbol -> 920001.SH
```

影响面在**回测**而非全市场涨跌分布：`app/api/v1/backtest.py:333-337` 按 `board_of()` 取
`pct = {"bse":0.30,"chinext_star":0.20}.get(board, 0.10)` —— 920 段标的会拿到 ±10% 闸门
（真实 ±30%），把本应放行的成交拒掉，同时 `universe.py` 的 `limit_up/down` 也按主板算。

同一根因的第二个证据：`universe_daily` 全量 516 万行中 `board` 只有 `main`/`chinext_star`
（`b2_probe13.py` E6），`MARK_UP["bse"]`（0.30）在真实数据里从未被使用过 —— 属「永不成立的分支」
（目标 B）。这与简报【二、属于有意设计】第 5 条「bse 板块池为 0 是需求」并不冲突：
代码段判定错误是独立的缺陷，即使池为空也应在规则层正确。
另注：需要验证 `code_to_symbol("920001") -> "920001.SH"` 是否会让北交所标的在
**入库阶段**就落到 `.SH`（`a_share_rules.py:18-25`，同为 P2，见 2.11）。

### 2.3 【P2 · Bug · 确定】真实数据核对：涨跌停舍入与 ROUND_HALF_UP 不一致（0.03%/0.39%）

`limit.py:50-52` 的 `ROUND_HALF_UP` 实现**正确**（实测 `10.55*1.1→11.61`，`7.85*1.1→8.64`
vs Python `round()` 的 8.63）。但**生产数据不是用它生成的**。

`app/data/universe.py:93-100` 的向量化实现：

```python
return ((x * 100.0 + 0.5).floor() / 100.0)
```

在二分位（.xx5）上受 float64 表示误差影响会**少一分**。只读核对生产 `universe_daily` 2024 年
60.3 万行、抽样 20 万行（`b2_probe13.py` E2 + `b2_probe14.py` F4）：

```
抽样 200000: limit_up 不一致 61 (0.0305%), limit_down 不一致 782 (0.3910%)
001287.SZ 2024-01-25: 真实昨收=17.15 存储 limit_up=18.86 | domain 计算 up=18.87
002005.SZ 2024-08-06: 真实昨收=1.15  存储 limit_up=1.26  | domain 计算 up=1.27
600157.SH 2024-08-15: 真实昨收=1.15  存储 limit_up=1.26  | domain 计算 up=1.27
```

穷举 0.50–49.99 元 × {5%,10%,20%} 共 14850 组（`b2_probe14.py` F2）：向量化实现与
`Decimal ROUND_HALF_UP` 不一致的有 **10 组（0.0673%）**，如 `(1.15,0.1) → 1.26 vs 1.27`、
`(4.3,0.05) → 4.51 vs 4.52`、`(33.3,0.05) → 34.96 vs 34.97`。

后果：`broker.py:312` 用 `open_ >= limit_up*0.9999` 判涨停买单、`broker.py:270` 用
`open_ <= limit_down*1.0001` 判跌停卖单；闸门基准少一分钱 ⇒ 该拒的成交被放行 / 该放行的被拒。
1.15 元这类低价股在 2024 年命中率最高（0.39% 的跌停价错一分）。

第三次舍入口径分裂：`app/api/v1/backtest.py:336-337` 用 Polars 的 `.round(2)`（**银行家舍入**，
注释还明确写着不能用），同一份数据在「回测闸门」与「domain/limit」之间**永远差一分**。
第 4 套在 `app/api/v1/market.py:96-97`：涨跌家数直接用 `p >= 9.8` / `p <= -9.8` 判涨停家数，
完全没有板块概念（创业板 20%、北交所 30% 全被漏计），文件自己的 note（`:246`）写明是
「±9.8% 近似口径」，属已知简化，但**与 `domain/limit.py` 已具备的精确规则并存**：
`universe.py:6` 写着「唯一规则来源，杜绝第二套编码」，实际全仓有 4 套。
修复方向统一为 §4.1。

`920xxx` 段（北交所 2024 年新代码段）除 §2.2 的板块误判外，还需检查入库端：
`data/ingest/akshare_adapter.py:_sina_symbol`（`code.startswith(("6","9")) -> sh`）
与 `a_share_rules.code_to_symbol` 同样把 `9` 开头一律归沪市 —— 即 920 段标的可能
**在抓取阶段就被写成 `.SH`**，从而永远进不了北交所规则分支（与简报「bse 池为 0 属需求」
无关，这是代码段映射问题）。置信度「疑似，需验证」：需要在有 920 段标的的环境里
核对 `instrument.symbol` 的实际后缀。

### 2.4 【P2 · 一致性 · 确定】真实数据核对：主板 2023-04 前上市首日被当成「不设涨跌幅」

`universe.py:114-120` 的 `_limit_pct_expr` 对 `is_new_issue` 一律给 `pct=0.0`，
`universe.py:219-224` 又对**所有板块**用 `days_since_list < 5` 判定 `is_new_issue`。
而 `domain/limit.py:99-106` 明确区分：主板 2023-04 注册制**之前**上市的新股首日是 ±44%/-36%。

只读核对（`b2_probe13.py` E1）：

```
limit_up > 1e6（不设限哨兵）共 473 行；days_since_list ∈ {0,1,2,3,4}
按 (board, list_date) 分组: main/2022-01-04 → 460 行；main/2024-06-03 → 5 行
list_date < 2023-04-01 的哨兵行: 468 行（其中主板 460 行）
```

即 460 个「主板注册制前首日」被记为不设限（真实 ±44%），另 8 行为创业板老规则首日
（2018-06-11 起 `days_since_list` 0~4，真实应为首日 ±44%、次日起 ±10%，见 2.2 的板块分支问题）。
后果方向是**乐观**：回测中这些日子的涨跌停闸门被完全打开。

`_limit_pct_expr(board, is_st, days_since_list, is_new_issue)`（`universe.py:103`）的
`days_since_list` 参数**在函数体内从未使用** —— 正是「注册制前主板首日」分支被遗漏的直接证据。

### 2.5 【P1 · Bug · 确定】adjust.py:64 —— 复权因子缺行时 `fill_null(1.0)`，整段退化为不复权

```python
# adjust.py:60-65
merged = (... .join(fac, on=["symbol","date"], how="left")
          .with_columns(pl.col("adj_factor").fill_null(1.0)))     # ← 缺行填 1
```

因子表在某日缺行（停牌日因子缺失、除权日与行情日不对齐、因子表更新滞后）时，
该日因子被当作 **1.0**，而不是**最近可得因子前向填充**。实测（`b2_probe2.py` A2）：

```
因子表缺 1/3 -> hfq close = [10.0, 10.0, 11.0]    ← 1/3 的因子被当成 1.0，后复权序列在此出现假跳变
              qfq close = [9.0909, 9.0909, 10.0]
```

只要因子表覆盖不完整，hfq/qfq 都会在该日产生**虚构的单日收益**，直接污染收益率、波动率、
IC 与标签。正确做法是 `fill_null(strategy="forward")`（再对首行之前的空值填 1）。

需要数据层确认的信息（标注「疑似，需验证」其发生频率）：`daily_bar_hfq`/`daily_bar_qfq`
（真实数据中只有 ffill 后的价格，没有 adj_factor 列，`b2_probe12.py` D2 已核对列集合）
由 `repair.py:build_qfq_dataset` 以 `hfq/raw` 逐日推导，停牌日两侧都有行时不会缺；
真正会缺的是「行情有行、因子无行」的不对齐情形。**本条的触发风险取决于该数据集是否严格对齐**
—— 但函数本身对缺行的处理是错的（静默），且无任何告警。

### 2.6 【目标 B · 死代码 · 确定】adjust.py 全模块在生产路径 0 调用方

`rg -n "apply_adjust_df" backend` 全仓结果只有定义处与两个测试文件
（`tests/test_domain.py:15,48,52,57,64`、`tests/test_feature_asof.py:22,49,90,91`），
`app/` 内**零调用**。生产复权走 `data/repair.py:build_qfq_dataset`（自带 `hfq/F_last` 推导）
与 `data/parquet_store.py`，与本模块的约定只是「文档上一致」（`repair.py:194`）。
本批重点要求的「前复权 asof 稳定性」问题因此**只存在于测试守护的代码里**，
但 `tests/test_feature_asof.py` 正是在断言这个模块的 asof 稳定性 —— 一个生产不用、
测试守护的实现，属被取代的旧路径（应在 B3a/P4 与数据层对账）。

同类的 qfq 基准日依赖（`b2_probe2.py` A1，确定）：只要因子表出现新的最新因子，
整段历史 qfq 都会被改写（`10.0 → 9.0909`）；同时 A3 证明 qfq 的基准取的是
**行情表内最后一行**的因子，若因子表比行情新（1/4 有除权、行情到 1/3），历史值不会按
全表最新因子调整。这与「qfq 本质随基准日变化」是同一个已知权衡，但函数既没有
`asof` 参数也没有告警。

### 2.7 【P1 · Bug · 确定】neutralize.py:62-69 —— 升序索引写回 numpy，多日期分组必崩

```python
out = np.full(len(pdf), np.nan)                       # 61
for _, g in pdf.groupby(date_col):                    # 62
    idx = g.index.to_numpy()                          # 63  ← 标签索引
    ...
    out[idx] = res                                   # 69  ← 当位置索引用
```

实测（`b2_probe6.py` NE3）：

```
日1(0..3) 正常；把 index 设为 [10..17] -> IndexError: index 10 is out of bounds for axis 0 with size 8
pdf.iloc[2:]（index 2..7）          -> IndexError: index 6 is out of bounds for axis 0 with size 6
```

即：**只要调用方传入的 DataFrame 索引不是恰好 `0..n-1`（例如先用 `dropna()`/过滤取了子集），
函数必然抛错**。当前 `app/` 内无人调用（`rg` 仅命中定义处与 `tests/test_p2_rest.py:17,51`），
所以是潜伏缺陷；一旦接线就是 P0。修复：`pos = pdf.index.get_indexer(idx)` 后再写。

同一函数族的确定性错误（`apply_industry_neutral` 内）：先用 `np.log(mcap.clip(min=1e-9))`
再进 `neutralize_cross_section`，逻辑正确；`neutralize_cross_section` 本身的数学**经实测正确**
（`b2_probe6.py` NE1：三个行业的残差均值 5.6e-14，残差与 `log(mcap)` 相关系数 -0.0）。

### 2.8 【P2 · 死代码/一致性 · 确定】neutralize.py 整体未被接线

`factor_processing.py:7` 的模块文档声称处理链含「行业/市值中性化（见 neutralize.py，OLS 残差）」，
但 `orthogonalize_factor_panel` 只做 Lowdin 正交化，**从不调用 neutralize**。
`app/` 内 `neutralize.py` 的引用为 0（`rg` 仅命中自身与测试）。实际投产的市值中性化是
`research.py:neutralize_by_size_cs`（只有 size，无行业虚元），行业中性化在 ML 侧另有实现。

也就是说：**本批重点要求的「中性化是否在正确截面上做」的答案是「domain 的实现正确但没被用」**。
`neutralize_cross_section` 按日逐截面独立回归（无全样本统计量 ⇒ 无泄漏），
`apply_industry_neutral` 也是按 `date` 分组 —— 设计正确，缺的是接线。

### 2.9 【P1 · Bug · 确定】optimizer.py:116-138 —— weight_cap 不可行时静默返回 Σw<1

```python
# optimizer.py:116-138
if cap <= 0 or cap >= 1.0: return w
out = np.clip(..., 0.0, None); out = _normalize(out)
for _ in range(max_iter):
    over = out > cap
    if not over.any(): break
    ...
    if total_room <= 1e-15 or excess <= 1e-15:
        break                                    # ← 不可行时直接退出，不报错也不补回 1
return out
```

当 `cap * len(w) < 1` 时，数学上无解，函数返回的权重之和等于 `cap*N`。实测
（`b2_probe9.py` W1）：

```
N=10 cap=0.05 (N*cap=0.50) -> Σw=0.500000 【不可行: 大量闲置现金】
N=30 cap=0.03 (N*cap=0.90) -> Σw=0.900000
N=50 cap=0.01 (N*cap=0.50) -> Σw=0.500000
N=10 cap=0.10 (N*cap=1.00) -> Σw=1.000000 ✓
```

API 允许任意 `0 ≤ weight_cap ≤ 1`（`api/v1/backtest.py:49`、`api/v1/research.py:390`），
所以「研究台设 10 只标的 × 单股上限 5%」会得到**一半资金闲置**的组合，且
`compute_weights_from_returns` 正常返回、API `status="ok"`、`rebalance_log.fallback=False`
（`portfolio.py:266-271`）—— 没有任何迹象表明约束不可行。这是目标 C（组合约束现实性）里
最容易被误用的一条。修复：`cap = max(cap, 1.0/N)` 并向调用方返回 `feasible=False`。

### 2.10 【P2 · 策略 · 确定】optimizer.py:222-261 + 调用方 —— mvo 在 μ=0 时 `risk_aversion` 完全无效

`compute_weights_from_returns`（`optimizer.py:302-306`）在 `expected_returns is None` 时传
`np.zeros(N)`；`engine.py:276`、`research.py:421-425` 都**不传** `expected_returns`：

```python
# optimizer.py:302-306
mu = (np.full(R.shape[1], 0.0) if expected_returns is None
      else np.asarray(expected_returns, dtype=np.float64))
w = mean_variance_weights(mu, cov, weight_cap=weight_cap, ...)   # risk_aversion 固定默认 8.0
```

实测（`b2_probe9.py` W4，N=8，T=250）：

```
μ=0, λ=8  -> [0.1253 0.1249 0.1259 0.1245 0.1244 0.1246 0.1256 0.1248] 组合波动 5.512%
μ=0, λ=50 ->   完全相同                                                组合波动 5.512%
等权      -> [0.125 ... ]                                              组合波动 5.512%
```

μ=0 时目标函数只剩 `-λw'Σw - γ‖w-w_prev‖²`，投影梯度用**固定步长**
`step = 1/(2λ·tr(C)/N + 2γ + 1e-6)`（`optimizer.py:253`），λ 与步长同时缩放 ⇒ 迭代轨迹几乎不变，
解停在等权附近，风险厌恶参数对结果没有可观测影响。实测 `λ=8` 与 `λ=50` 的四位小数完全相同。
后果：`/research/optimize` 与回测里的 `weighting="mvo"` 名义上是均值-方差优化，
实际是「等权 + 事后上限裁剪」，无任何 α 信息参与（目标 C 的「权重是拍定的还是回归/IC 加权的」
答案：**这两处是拍定的**）。修复见 §4.2。

**关于协方差奇异性（自我纠错记录）**：我第一版探针（`b2_probe4.py` R2）因自身
`np.round(arr)[-6:]` 的写法误报了「RMT 把最大特征值抹掉」。用正确写法复算
（`b2_probe5.py`）后，单因子结构（N=20, T=252, ρ=0.6）下：

```
样本相关最大特征值 11.747 > MP 边缘 1.643（仅 1 个特征值被保留）
去噪后非对角相关均值 0.565（原始 0.5651），对角 1.0，trace 20.0
去噪后 corr[0,1]=0.569 vs 原始 0.5614（重归一化带来的轻微上偏）
```

即 **RMT 去噪行为正确**，未破坏跨截面相关结构；`T ≤ N` 时 `rmt_denoise` 直接返回原矩阵
（`optimizer.py:85-86`），LW 收缩在常数资产上给出 `δ=0.247`（非退化）。
因此**没有**「协方差被抹平」这一缺陷，报告中不列该条。仍成立的较弱观察：
`robust_cov` 隐含的组合波动在合成单因子测试中比样本实现低约 1~4%
（`b2_probe4.py` R3，ρ=0.3 时 8.800% vs 9.022%），属 shrinkage 的正常保守方向，
标 P3，不单独成条。

### 2.11 【P3 · Bug · 确定】a_share_rules.py:18-25 / 69-79 —— 920 段映射错误、交易所后缀被静默丢弃

```python
# a_share_rules.py:18-25
if code.startswith(("6", "9")): return f"{code}.SH"    # 920xxx（北交所）→ .SH
```

`b2_probe1.py` P2/P6：

```
920001: code_to_symbol -> 920001.SH        ← 北交所新代码段被判为沪市
normalize_code('600519.SZ') = '600519' -> code_to_symbol = '600519.SH'   ← 矛盾后缀被静默忽略
normalize_code('000001.SH') = '000001' -> code_to_symbol = '000001.SZ'
```

`normalize_code` 的 docstring 明确它只取前缀，所以「忽略后缀」是有意为之；但
`orchestrator.py:476-497` 把它当**入口门禁**用，于是「`600519.SZ`」这类脏数据被
静默纠正成 `.SH` 而不是被拒绝 —— 与它「deliberately 更严格」的设计目标相反
（更严格只体现在多段后缀）。属低危但确定的一致性缺口。

### 2.12 【目标 B · 死代码 · 确定】a_share_rules.py 四个函数全仓 0 调用方；无过户费

逐函数 `rg` 计数（`backend/` 全树，仅计命中的文件数）：

```
stamp_duty_rate          files=1  (仅定义处)
commission_rate_default  files=1
is_t_plus_one            files=1
settlement_days          files=1
```

即四个导出函数在生产与测试中都无人调用。`portfolio.py:8-11` 的模块文档写
「双边佣金（最低 5 元）、卖出印花税、滑点、整手约束 —— 口径与 backtest/broker.py 保持一致」，
但**过户费在 domain 与 backtest 两侧都不存在**：全仓 `rg "transfer_fee|过户费|0\.00001"` → **0 命中**。
现行 A 股过户费为成交金额的 0.001%（双边，2022-04-29 起沪深统一）。
600519 一笔 100 万元成交约 10 元，对低频策略可忽略，但**对高换手小市值策略会系统性低估成本**。
`stamp_duty_rate`（仅卖出 0.05%）与 `commission_rate_default`（万 3）的**数值本身正确**，
只是无人使用、真正的常量硬编码在 `portfolio.py:47-50` 与 `backtest/broker.py`。

### 2.13 【P1 · Bug · 确定】research.py:173-182 —— style_exposure 对缺列返回 0.0，且全模块无人调用

```python
# research.py:177-181
for style, col in STYLE_FACTOR_MAP.items():
    num = sum(w * float(zrow.get(f"{col}_z", np.nan) or np.nan)
              for sym, w in weights.items()
              if (f"{col}_z") in zrow.index)
    out[style] = round(num, 4) if np.isfinite(num) else None
```

两处缺陷（`b2_probe6.py` RS3）：

```
缺列:        {'Momentum': 1.0, 'Volatility': 0, 'Size': 0, 'Reversal': 0}   ← 缺失被报成"零暴露"
zrow 含 NaN: {'Momentum': None, 'Volatility': 0, 'Size': 0, 'Reversal': 0}  ← 同一截面两种语义
```

空生成器 `sum(...) == 0` 是有限浮点，于是「因子列不存在」与「真实暴露恰为 0」不可区分。
`float(x) or np.nan` 还会把合法的 `0.0` z 值变成 NaN。
且 `rg -n "style_exposure" backend` 全仓**只有定义处** —— 生产路径用的是
`api/v1/research.py:432-448` 的**另一份正确实现**（对缺列返回 None、显式 `np.isfinite` 过滤）。
所以这是「被后续实现取代的旧路径 + 自身有语义错误」的死代码，应删除或替换为 API 版。

### 2.14 【P2 · Bug · 确定】research.py:199-261 —— execution_impact_sim 在成交额缺失时输出 NaN/0

`b2_probe10.py` N5（`amount=[1e7, 0, NaN]`）：

```
market: fills=1 unfilled=800000.0 avg_bps=1.41
vwap  : fills=2 unfilled=nan      avg_bps=0.0
twap  : fills=2 unfilled=nan      avg_bps=0.0
```

原因是 `cash_left -= amt` 中 `amt` 为 NaN（`amt = amount × cap`），且
`avg_bps` 的三元表达式 `if fills and sum(...) > 0 else 0.0` 对 NaN 比较为 False ⇒ 报 0.0。
`fills` 里也照常记录了一条含 NaN 的成交（`amount: nan`）。
这是「降级路径产生看似正常实则为空/为 0 的数据」（简报【二】第 3 条明确要求报的类别）：
前端看到「冲击成本 0 bps、成交量 0 元」，会误判为「零冲击」。
修复：`amount`/`volume` 非有限时跳过该日并把原因记入输出（或整单返回 `status`）。

### 2.15 【P2 · Bug · 确定】factor_processing.py:137-140 —— 完全共线列无法正交化，反而输出重复列

```python
U, S, Vt = np.linalg.svd(M, full_matrices=False)
keep = S > tol * max(S[0] if S.size else 1.0, 1.0)
Uk, Vtk = U[:, keep], Vt[keep, :]
return Uk @ Vtk
```

`U[:, keep]` 用**布尔掩码**索引二维数组时，Numpy 会把结果广播回原列数
（`U[:, keep].shape == (n, k)`，`Vt[keep, :].shape == (r, k)`，`k` 为原列数），
乘出来仍是 `(n, k)` 且各列只差一个标量因子。实测（`b2_probe11.py` S2/S3）：

```
F = [x, 2x]（精确共线，秩 1）: 奇异值 = [15.13, 1.16e-15]，keep = [True, False]
输出 shape = (50, 2)，F_ortho'F_ortho = [[0.2, 0.4], [0.4, 0.8]]  ← 不是 I
```

经 `orthogonalize_factor_panel`（`b2_probe11.py` S4，d1 截面 f1 = 2·f2）：

```
d1 f1 与 f2 相关: 1.0
```

即**该函数在它唯一存在的理由（消除多重共线性）上失效**：两个完全共线的因子正交化后
仍然完全相关。满秩情形正确（`S1`：`F'F = I`，列空间投影残差 0）。
修复：用 `idx = np.flatnonzero(keep)` 后 `U[:, idx] @ Vt[idx, :]`，并在秩缺失时
返回有效列数 + 通道告知（`orthogonalize_factor_panel` 的正则化 `std[std<1e-12]=1.0`
会对全零列产生 0/1=0 的静默列）。

相关但较轻：`mad_winsorize`（`factor_processing.py:28-38`）用 `np.isfinite` 保留 NaN，
但 **inf 会穿过**（`b2_probe10.py` N4：输入 `[1, inf, 3]` → 输出 `[1, inf, 3]`），
随后 `cross_sectional_zscore` 让整列变 inf，最终 `symmetric_orthogonalize` 抛
`ValueError`（`factor_processing.py:135-136`）—— 即上游一个 inf 会让整条特征处理链失败。
`CrossSectionalScaler` 用重复 index 会抛 `InvalidIndexError`（同 N4），属边界。

### 2.16 【P1 · Bug · 确定】portfolio.py:209-228 —— data_warnings 的退市/停牌缺口分支永不触发

```python
union_idx = prices.index.union(benchmark.index).sort_values()   # 198
raw_prices = prices.reindex(union_idx)                          # 199
prices = raw_prices.ffill()                                     # 200
...
col = raw_prices.get(code)                                      # 212
...
lv = col.loc[fv:].last_valid_index()                            # 225
if lv is not None and lv < union_idx[-1]:                       # 226
    data_warnings.append(f"{code} 行情止于 ...（疑似退市/长期停牌）...")
```

`raw_prices` 的 index 是 `union_idx`（含基准的完整轴），所以 `col.last_valid_index()`
**恒为** `union_idx[-1]`（NaN 只是值，不影响 index），`lv < union_idx[-1]` 永远为假。
「停牌/缺口」那条（`:222-224`，`col.loc[fv:].isna().sum()`）同理：`raw_prices` 未 ffill 时
确实能数出 NaN，所以第 2 条能触发，但第 1、3 条（晚上市 / 疑似退市）不能。

实测（`b2_probe8.py` G2/G4，loader 返回 `[10.0]*30 + [nan]*30`，净值无任何异常提示）：

```
AAA 后半段 NaN        -> data_warnings = []
AAA 前 30 天无行情    -> data_warnings = []
AAA 后 20 天断档      -> data_warnings = []
```

而模块文档（`portfolio.py:11-12`、`:414`）明确宣称「不再静默截断」「此前为静默处理」。
真实后果：退市/断档标的以最后价一直估值（`prices.ffill()` 后 `raw_prices` 与 `prices` 分离，
估值用的是 ffill 价格），组合净值被"冻结"，且**没有任何告警**——
这正是简报【二】第 3 条点名要报的「看似正常实则为空的数据」。

### 2.17 【P1 · Bug · 确定】portfolio.py:365-366 —— 基准首值为 0 时整条 bridge 变 NaN/Inf

```python
bm_nav = initial_cash / benchmark.iloc[0] * benchmark        # 366
```

`benchmark` 在上游仅做了 `.ffill().dropna()`（`:201`），不校验首值 > 0。
实测（`b2_probe8.py` G3，基准序列首值 0）：

```
RuntimeWarning: divide by zero encountered in scalar divide  (portfolio.py:366)
nav_curve[0]  = {'date': '2024-01-01', 'nav': 999200.6502, 'benchmark': nan}
nav_curve[-1] = {'date': '2024-03-22', 'nav': 999200.6502, 'benchmark': inf}
json.dumps(allow_nan=False) -> ValueError: Out of range float values are not JSON compliant
```

同一现象在 `research.py:portfolio_stress_replay`（无此问题）与 benchmark 相关的
`_compute_metrics`（NaN 会吞掉 alpha/beta 分支）之外，会直接把 NaN/Inf 送进响应体。
本项目契约要求 HTTP 恒 200 + JSON 信封，NaN 经 Starlette 默认 `JSONResponse`
（`allow_nan=True`）会以裸 `NaN` 字面量输出，**不符合 JSON 规范**，前端 `JSON.parse` 会抛错。
最小修复：基准首值非正时回退到首个正值，并在 `data_warnings` 记一条。

### 2.18 【P2 · 一致性 · 确定】portfolio.py:169-171 + 279-334 —— 接受 Σw∈[0.99,1.01] 但不归一

```python
total_weight = sum(float(a.get("weight", 0)) for a in assets)
if not (0.99 <= total_weight <= 1.01):
    raise ValueError(...)
...
target_value = equity * target_weights        # 285，不归一
```

实测（`b2_probe7.py` PF3，weights = 0.5 / 0.495）：

```
Σw=0.995 被接受，nav 末值 = 999205.4511
holdings_drift 末行 = {'AAA': 0.499397, 'BBB': 0.494393}   ← 恒有 0.6% 闲置现金
```

对 60 个交易日、零波动资产，组合净值因闲置现金少赚约 0.08%。
更隐蔽的后果是 `holdings_drift` 与用户的「目标权重」永久不一致（用户以为满仓）。
修复：`target_weights = weights / weights.sum()` 后再分配。

### 2.19 【P2 · Bug · 确定】risk.py:39-46 与 115-121 —— 夏普口径混用 + 0 价过滤产生合成跳变

其一（`risk.py:36,39-46`）：`_annual_vol` 年化 `std*√252`，`_sharpe` 用
`mean(r - rf/252)/std(r - rf/252)*√252`；而调用方 `portfolio.py:94` 用的是
**年化分子** `(ret.mean()*252 - rf)/vol`。两个「夏普」在全站并存且数值不同
（`b2_probe2.py` M1：`sharpe(rf=0)=0.9069` vs `sharpe(rf=0.03)=0.7176`）。
`risk.py:14` 的公式注释与实际实现一致，问题在**两条口径并存**，同一指标在不同页面不可比。

其二（`risk.py:116-121`）：

```python
close = np.nan_to_num(d["close"]..., nan=0.0)
close = close[close > 0]                      # 119  ← 整根剔除，不重置窗口
if close.size < 20: raise ValueError(...)
```

实测（`b2_probe3.py` RK1，中间一根 close=0）：`window=251`、`annual_vol=0.154829` ——
被过滤掉的那一天使前后两天变成「跨日收益」，`r` 里出现一个不属于任何真实交易日的跳变；
`window` 也仍按过滤后的长度报告（少 1 天）。同时 `np.nan_to_num(nan=0.0)` 会把
真实缺失价静默当作 0 再丢弃。属 P2（数值失真但幅度小）。

其三（`risk.py:145`）：`bench_name = "沪深300"` **硬编码**，只要调用方传入任何基准
（例如中证500、行业指数）都会被标成沪深300；而 `api/v1/stock.py` 的基准来自请求参数。
这条直接违反契约第 5/6 条「派生指标必须披露真实口径，不允许冒充」。

### 2.20 【P2 · Bug · 确定】attribution.py:27-37 —— 守恒的前提未被强制/未披露

`brindon_attribution` 不校验权重和，也不归一：

```python
idx = sorted(set(portfolio_w) | set(benchmark_w))          # 27
wp = pd.Series(portfolio_w).reindex(idx).fillna(0.0)       # 28
wb = pd.Series(benchmark_w).reindex(idx).fillna(0.0)       # 29
r = returns.reindex(idx).dropna()                          # 30  ← 收益缺失的标的被整行丢弃
wp, wb = wp.reindex(r.index), wb.reindex(r.index)          # 31  ← 权重随之丢失，无告警
```

实测（`b2_probe3.py`）：

```
AT1 Σwp=1.0, Σwb=1.0, 收益完整:  excess=0.008 三项和=0.008001  residual=0.0        ✓ 守恒
AT2 Σwp=0.995:                  residual=-0.000155   ← portfolio.py 允许的区间会直接破坏守恒
AT3 Σwb=0.8:                    residual=0.0046      ← 基准缺口全部落进"纯 Alpha"
AT4 持仓 'b' 收益 NaN:           portfolio_return=0.054（少了 0.3 权重）residual=0.0
```

结论（目标 C「归因是否守恒」）：
1. **权重和为 1 且收益完整时严格守恒**（残差 0.0，已实测），Brinson-Fachler 三分解实现正确；
2. 但 `residual_alpha`（`:71-72`）被文档描述为「纯粹 Alpha」，实际它**混合了**
   权重未归一化误差与被丢弃标的的权重；AT3 中 0.0046 全被记成「Alpha」；
3. AT4 是真实会发生的场景（停牌/退市标的窗口收益缺失），此时 `portfolio_return`
   少算 0.3 权重且 residual 仍为 0（因为丢弃是对称的）—— 报告看起来"守恒"，其实两边都错。

建议：入口处 `wp/=wp.sum(); wb/=wb.sum()`，把丢弃标的的权重与原因显式放进
`summary.dropped_symbols`，并把残差改名为 `unexplained`。

另外两个确定的小问题：
* `attribution.py:83` `style_factor_returns` 的分母是 `aligned.notna().sum()`（**个数**），
  而分子是 `(z*ret).sum()`（**z 加权**）。实测（`b2_probe6.py`/AT6）：
  z={2,−2}（总暴露 0）时因子收益为 0.08（非 0）；z={+1,−1} 时得 0.04，恰好等于
  `(0.10−0.02)/2`。语义上「zscore 加权的等权市场收益」应为 `Σ(z·r)/Σ|z|`，
  当前分母在 z 偏态截面上会系统性放大/缩小因子收益的量纲（不影响 β，影响 α 年化值）。
* 函数名 `brindon_attribution` 是 `brinson` 的拼写错误（英文文档写 Brinson-Fachler），
  已被 `api/v1/desk.py:430`、`api/v1/report.py` 引用 ⇒ 改名需连带，列为 P3。

### 2.21 【P3 ×n · 确定】

| # | 位置 | 现象 |
|---|---|---|
| a | `research.py:68-69` | `factor_ic_table` 的 MAD 去极值用 `fw.apply(...)` 按**列（单标的时序）**去极值；docstring 写的是「截面处理流水线」。虽然后续用 Rank IC（秩）使影响很小，但时序 winsorize 会把某只股票的极端历史值截断，与「截面去极值」不是一回事。 |
| b | `portfolio.py:87` | `cagr = (nav[-1]/nav[0])**(252/max(1,n))` 用 `n=len(nav)` 而非 `len(nav)-1`（收益期数）；64 个交易日、+16% 收益时高估约 6bp，同时传染 `calmar`。 |
| c | `chip.py:98` | `current = float(current_price if current_price else close[-1])`：显式传 `current_price=0` 会被静默替换为最新收盘（本应抛 `ValueError("现价必须 > 0")`）。同类 `if pct` 真值判断在 `universe.py:71`（`limit_pct=0.0` → `None`）。 |
| d | `chip.py:123-124` | 全零成交量（长期停牌）时 `_turnover_series` 走量能估算路径，仍产出非零筹码分布（`avg_cost=10.63, profit_ratio=0.75`），不给任何提示。 |
| e | `calendar.py:39-43` + `data/calendar_store.py:33-45` | `is_trade_day` 需要 `cal` 非空；`load_from_db_sync` 在 DB 缺失/表缺失时 `build_calendar([])` 且只 warning ⇒ 降级为「所有日期都非交易日」，`prev_trade_day` 搜 20 天后抛 ValueError。`parquet_store.py:222` 的注释（「日历不可用时退化为仅周末」）描述的是**另一个**实现，读起来会误导。 |
| f | `metrics.py:169`/`portfolio.py:109-115` | PSR/DSR 在 `r.size<3` 返回 NaN，调用方用 `psr == psr` 判 NaN（可读性），`n_trials=1` 时 DSR≡PSR（已实测 0.829154 == 0.829154），语义上应显式说明「未做多重试验惩罚」。 |

---

## 3. 三个目标的分项回答

### 目标 A（Bug）
本批共 **P1 ×6 / P2 ×12 / P3 ×9**（去重后），全部附最小验证。最严重的三条：
`neutralize.py:62-69`（必然崩溃，潜伏）、`portfolio.py:365-366`（NaN/Inf 进入响应）、
`portfolio.py:209-228`（退市/断档静默，违反自己的文档承诺）。
`limit.py`/`adjust.py` 的**公式**本身经实测正确，问题集中在「特殊情形判定」与「数据层
复制了第二/第三套实现」。

### 目标 B（死代码）
1. `adjust.py` 全模块（`apply_adjust_df` 等）：`app/` 零调用，仅测试引用；
2. `neutralize.py` 全模块：`app/` 零调用（`factor_processing.py` 文档声称使用它，实际未接线）；
3. `research.style_exposure`：零调用，且被 `api/v1/research.py:432` 的正确实现取代；
4. `a_share_rules` 的 `stamp_duty_rate` / `commission_rate_default` / `is_t_plus_one` / `settlement_days`：零调用；
5. `limit.py` 的 `MARK_UP["bse"]` 与 `board=="bse"` 分支：真实数据中 `board` 只有 main/chinext_star（516 万行全量核对）；
6. `optimizer.inverse_vol_weights` 被分发表覆盖（**在用**，不计死代码）；
   `max_diversification_weights`/`risk_contributions`/`diversification_ratio`/`sample_cov`/`rmt_denoise` 仅被测试引用 → 属「导出但未接线」，标 P3。
7. 跨批提示：`data/universe.py:build_universe_daily`（含 `domain.limit.calc_limit_prices` 的唯一真实调用点）也**零生产调用方**（`orchestrator.py:267` 只调 `build_universe_history`）—— 这条归 B3a，但它是「`limit.py` 的规则从未进入生产」的直接证据。

### 目标 C（策略合理性）→ 五要素建议见 §4

---

## 4. 优化建议（每条：当前问题 / 具体改法 / 预期收益 / 引入风险 / 验证方式）

### 4.1 统一涨跌停规则为单一实现（P0 级优先级）
- **当前问题**：同一规则有 4 份实现（`domain/limit.py`、`data/universe.py:93-120`、
  `api/v1/backtest.py:336-337`、`data/realtime.py:280` 的东财原值），实测 2024 年
  `limit_down` 有 **0.39%** 的行差一分钱，且 460 行主板注册制前首日被误标为不设限。
- **具体改法**：`universe.py` 的两个 builder 改为调用 `domain/limit.py` 的
  `calc_limit_prices`（可先保留向量化路径但把舍入改成 `Decimal` 或
  `floor(round(x,10)*100+0.5)/100`），`api/v1/backtest.py:335-338` 删除自算，
  改为从 `universe_daily_bt` 取 `limit_up/down`（该数据集已含 hfq 域哨兵）。
- **预期收益**：消除 0.4% 的闸门误判（涨停拒单/跌停不拒单）；注册制前新股首日不再被
  当成无涨跌幅（这些日子真实振幅可达 44%，会显著影响历史回测的收益与回撤）。
- **引入风险**：改 `universe_daily` 需要重建全历史分区（516 万行，IO 密集）；
  hfq 域闸门与 raw 域闸门的换算需保持 `factor` 列同时更新。
- **验证方式**：对全量 `universe_daily` 重算 `limit_up/down` 并与 `Decimal ROUND_HALF_UP`
  比对，断言 **0 不一致**（用本报告的 `b2_probe13.py` E2 循环）；
  另断言 `list_date < 2023-04-01 and board=="main" and days_since_list==0` 的行
  `limit_pct==0.44`。

### 4.2 让「风险厌恶」和「预期收益」真正进入 MVO
- **当前问题**：`engine.py:276` 与 `research.py:421` 都不传 `expected_returns`，
  μ=0 使 λ 无效（实测 λ=8 与 λ=50 权重四位小数相同，结果≈等权）；名义 MVO 实为等权。
- **具体改法**：两处调用改为传 `expected_returns`：(a) 用 `signal.pred_score` 的截面
  zscore × 目标年化 IC × 目标波动（`score_weighted` 已有 rank 版可复用）；
  (b) 或直接用 `score` 的截面分位映射到 `[0, 2σ_target]`。同时把 `risk_aversion`
  从 `compute_weights_from_returns` 暴露到 API（当前被写死 8.0）。
- **预期收益**：MVO 从「等权 + 裁上限」变成真正的 α-风险权衡，同一风险预算下
  预期 IR 提升量级 0.1~0.3（取决于 score 的 IC，**需实测 IC 才能给准数**）；
  如果 IC 接近 0，则应当直接删掉 mvo 选项而不是保留一个无效果的开关。
- **引入风险**：μ 的估计误差会被 MVO 放大（经典 error-maximization），
  必须配合 `weight_cap` 与 `turnover_penalty`；μ 的尺度需与 λ 匹配，否则解会顶到上限。
- **验证方式**：固定协方差，对同一 `score` 跑 λ∈{2,8,50}，断言权重向量的
  两两 L1 距离随 λ 单调增大（当前为 0）；再做一次样本外 IC 对比（等权 vs MVO）。

### 4.3 weight_cap 可行性守卫
- **当前问题**：`N*cap < 1` 时静默返回 `Σw = N*cap`（实测 N=10/cap=5% → 0.5，一半现金）。
- **具体改法**：`compute_weights_from_returns` 入口 `cap = max(weight_cap, 1.0/N)`；
  或在 `apply_weight_cap` 返回 `(w, feasible)` 并在 API 的 `rebalance_log`/响应里带 `feasible=false`。
- **预期收益**：消除「约束不可行 → 半仓」的静默错误；避免用户以为在满仓做约束优化。
- **引入风险**：自动放宽 cap 会掩盖「用户约束本身矛盾」的事实，故必须同时回传可行标志。
- **验证方式**：断言对任意 `N∈[2,60]`、`cap∈(0,1)`，`Σw ∈ {1.0} ∪ {N*cap}` 且
  `feasible == (N*cap >= 1)`；`b2_probe9.py` W1 的矩阵即为回归用例。

### 4.4 归因入口强制归一化 + 丢弃披露
- **当前问题**：`Σwb≠1` 时缺口 0.0046 被记成「纯 Alpha」；持仓收益缺失则整行丢弃
  （`portfolio_return` 少算权重）且无告警。
- **具体改法**：`brindon_attribution` 开头 `wp/=wp.sum(); wb/=wb.sum()`（和为 0 时抛错），
  把 `returns==NaN` 的标的收进 `summary.dropped_symbols`，`residual_alpha` 更名
  `unexplained` 并在 `|unexplained|>1e-6` 时置 `summary.status="degraded"`。
- **预期收益**：归因报告可直接用于业绩归因（当前 residual 会被误读为选股能力）。
- **引入风险**：归一化会掩盖上游权重口径错误 —— 用 `dropped_symbols` + 断言兜住。
- **验证方式**：`Σwb=0.8` 的用例断言 `abs(residual) < 1e-9`；NaN 用例断言
  `portfolio_return` 与手工 `Σ(wp·r)` 一致且 `dropped_symbols` 非空。

### 4.5 风险模型窗口与基准口径
- **当前问题**：回测默认 `cov_window=60`（`api/v1/backtest.py:48` 下限 20），
  实测 T=20/N=20 时样本协方差最小特征值 3.8e-20（数值奇异）、risk_parity 退化为等权；
  `risk.py:145` 硬编码 `benchmark="沪深300"`。
- **具体改法**：默认窗口提到 120~250（`research.py` 已是 120），并断言 `T >= 3N`；
  在 `risk_metrics` 增 `benchmark_symbol` 参数，从调用方传入真实代码，禁止硬编码标签。
- **预期收益**：避免 N 接近 T 时的伪分散化；口径披露合规（契约第 5/6 条）。
- **引入风险**：更长窗口在风格切换期反应更慢（可保留 `short_window` 分位做提示）。
- **验证方式**：对 T∈{20,60,120,250} 断言 `min(eigvalsh(cov)) > 1e-12`；
  对 `risk_metrics(..., benchmark=<中证500>)` 断言返回的 `benchmark` 字段等于传入代码。

### 4.6 补齐交易成本口径
- **当前问题**：过户费（0.001%，双边）在 domain 与 backtest 均缺失（全仓 0 命中）；
  下限 5 元佣金已实现。
- **具体改法**：在 `a_share_rules.py` 增加 `transfer_fee_rate(instrument_type)`（沪深深市均
  0.00001，ETF 同收），并把现有四个零调用函数与 `backtest/broker.py`/`portfolio.py`
  的硬编码常量统一到该模块（顺带解决死代码）。
- **预期收益**：单边成本 +1bp 量级；对年换手 1000% 的策略，年化成本低估约 0.2%，
  高换手小市值策略更多。
- **引入风险**：会下调历史回测收益（方向正确但会改变既有结论），需重跑基准。
- **验证方式**：100 万元成交额断言费用 = 300（佣金）+ 500（印花税，仅卖）+ 10（过户费）。

---

## 5. 未发现问题 / 已排除的怀疑（避免父审核员重复投入）

1. **`indicators.py`**：无中心化窗口、无前视；`boll_mid` 前 19 日为 `null`、3 行数据的
   MA 全 `null`；`RSI` 常数序列给 0.0（`avg_loss+1e-12` 生效）；MACD 以首值为 EMA 种子
   （与 pandas 默认一致，属可视化可接受近似）。
2. **`metrics.py`**：PSR/DSR 的四阶矩公式与 de Prado 口径逐项核对一致
   （`kurt` 用非超额峰度、`/(n-1)`），`_norm_ppf` 为 Acklam 逼近，`n_trials=1` 时 DSR≡PSR 合理。
3. **`chip.py` 换手率口径**：真实 `daily_bar` 的 `turnover` 2024 年实测为**小数**
   （000001.SZ 中位 0.0062、均值 0.0073、>1 占比 0），与 `chip.py:16` 的 docstring 一致，
   启发式（均值>1 才 ÷100）也能兼容百分数口径。**该处不是缺陷**（我最初怀疑它并按 2.12
   的方式做了真实数据核对后排除）。
4. **`optimizer.py` 的 RMT 去噪**：见 §2.10 的自我纠错 —— 单因子结构下最大特征值
   11.747 被正确保留、跨截面相关均值 0.565 未被破坏、trace 守恒、对角归一。
   不列为缺陷。
5. **`adjust.py` 的公式**：`hfq = raw·f`、`qfq = raw·f/f_last`、除权日当日处理
   （raw [20,10] + f [1,2] → hfq [20,20]）均正确；问题只在缺行填充与死代码。
6. **`calendar.py`**：纯函数、无 IO、周末前置过滤、20 天上限显式报错 —— 该文件本身无问题。
7. **`neutralize_cross_section` 的数学**：残差行业均值 ~5.6e-14、与 log(mcap) 相关 -0.0
   （`b2_probe6.py` NE1），零初始化设计（`:30-32` 注释）确实规避了共线 bug。
8. **`portfolio.py` 的 T-1 信号 / T 日撮合**：`pending_rebalance` 逻辑逐行核对正确
   （`i==0` 当日建仓、其后次日执行），佣金的 `max(5, rate*amount)`、卖出印花税 ETF 豁免、
   整手 `floor(shares/LOT_SIZE)*LOT_SIZE` 均正确。

---

## 6. 复现全部证据

```powershell
$base='D:\Python_Project\Alpha Quant Platform\backend'
$env:PYTHONIOENCODING='utf-8'
foreach ($i in 1..11) {
  & "$base\.venv\Scripts\python.exe" "$base\.tmp_b2\b2_probe$i.py"
}
# 12~14 需要读取 D:\Python_Project\Alpha Quant Platform\data\parquet（只读）
foreach ($i in 12..14) {
  & "$base\.venv\Scripts\python.exe" "$base\.tmp_b2\b2_probe$i.py"
}
```

> 探针只写 `backend/.tmp_b2/`（会话工作区内），未修改任何源码，未写入任何仓库 `data/`。