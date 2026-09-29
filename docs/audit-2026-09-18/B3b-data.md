# B3b 批次审核报告 · 数据层：采集与质量（含选股核心）

- **审核人**：B3b 子审核员（目标 A/B/C）
- **基线**：`git rev-parse --short HEAD` = **f7c9410**（工作区另有其它审核员临时目录，未改动任何源码）
- **范围**：`backend/app/data/ingest/*.py`（8 个）、`pipeline.py`、`quality.py`、`repair.py`、`realtime.py`、`quotes_hub.py`、`screening.py`、`etf.py`、`announcements.py`、`text_ingest.py`
- **纪律**：每条标「确定」的结论都附**真实执行过的命令与原始输出**；只能静态推理或触发条件依赖现场数据的，一律标「疑似，需验证」。
  未运行全量 pytest；`python -c` 合成数据均写入临时目录，**未触碰仓库 `data/`**。
- **契约基准**：`docs/chatgpt-full-review-prompt.md` §1；本批重点见派单说明。

---

## 0. 逐文件结论（先给结论行）

| 文件 | 结论 |
|---|---|
| `ingest/akshare_adapter.py` | **P2 ×1**（主源结构性异常被静默降级 + `source` 误标，B3b-A5）；`fetch_daily_bar_batch*` 无任何调用方（B3b-B8）。限速/退避本身正确 |
| `ingest/multi_source.py` | **P2 ×1**：整个「三源冗余 P1-2」**未接线**，且 `_from_akshare` 把「空数据」当失败，与既定语义相反（B3b-B1） |
| `ingest/tasks.py` | **P2 ×1**：写入门禁不含**日期/行数完整性**校验，残缺抓取会被当完整数据合并（B3b-A7）；`write_daily_bars` 增量合并与「一口径一 df」修复正确 |
| `ingest/validate.py` | **P3 ×1**：`check_pct_limit` 永不触发（依赖从不落盘的 `pct` 列），且阈值为百分数/小数混用（B3b-B5） |
| `ingest/dividends.py` | **P2 ×1**：全模块无生产调用方，且**写 SQLite / 读 Parquet** 存储不一致 ⇒ 恒空表；`save_dividends` 用 `asyncio.run`（B3b-B4，已知条目延伸） |
| `ingest/financials.py` | **P2 ×1**：`fetch/save/load_financials` 均无生产调用方，PIT 财务表永不落库（B3b-B3） |
| `ingest/announcements.py` | **P2 ×1**：写路径 `save_announcements` 无生产调用方，而生产读路径正是读它写的目录（B3b-B2） |
| `ingest/etf_instruments.py` | 未发现 P0–P2（目录→instrument 幂等 upsert 正确，空目录不写伪造行） |
| `ingest/__main__.py` | 未发现 P0–P2（仅第 94 行日志文案「三口径」与实际两口径不符，属文案） |
| `pipeline.py` | 未发现 P0–P2（`step_validate` 容差为纯比例、降级口径已如实留痕；完整性缺口见 B3b-A7） |
| `quality.py` | **P2 ×1**（经 `ops.quality_scan` 走 `validate_partition` 时：除权日误报 error、`factor_jump`/`calendar` 检查永不触发，B3b-A6）；`repair.is_quarantined` 死代码（B3b-B6） |
| `repair.py` | **P3 ×1**：`is_quarantined` 无调用方且语义与 docstring 相反（B3b-B6）；qfq 重建/qfq=hfq/F_last 正确 |
| `realtime.py` | **P3 ×1**（`_fetch_em_quote._v` 把合法的 0 当缺失 ⇒ EM 兜底源下平盘股 `pct=None`）；限速/降级链/字段映射经真实行情验证正确 |
| `quotes_hub.py` | **P1 ×1**：`quotes_snapshot` 静默截断 200 只、零披露，调用方（预警）会静默漏报（B3b-A2）；**P2 ×1**：推送节奏硬编码，`interval` 参数无效 + 首帧最长等 30s（B3b-A8） |
| `screening.py` | **P1 ×1**（空榜终态 `ok`，B3b-A1）、**P2 ×2**（null 排最前 + `enrich_items` 抛 TypeError，B3b-A4；`as_of` 丢日期，B3b-A3）、**P3 ×2**（选股列表部分见 §3） |
| `etf.py` | **P1 ×1**：`fetch_etf_flow_history` 字段口径错误，主力净流入被算成「主力净额 − 小单净额」（B3b-A9，已用真实行情证实） |
| `announcements.py` | 未发现 P0–P2（坏文件跳过并如实缩水，符合契约） |
| `text_ingest.py` | **P2 ×1**：`attach_text_features` 无调用方，`app/ml` 全目录不引用文本因子 ⇒ 文本链路产出永不入模（B3b-B7） |

---

## 1. 目标 A — Bug

> 条目按严重度降序排列，**编号不代表顺序**（A1/A2/A9 为 P1，A3–A8 为 P2）。ID 在 §0 表中被引用，请勿重排。

### B3b-A1【P1｜Bug/一致性】**空榜终态是 `ok` 空数组，违反「空榜必须 unavailable」契约**（确定）

- **位置**：`backend/app/api/v1/screener.py:305-309`（`_finalize_screener_payload`），触发点在 `backend/app/data/screening.py:71-132`（`filter_universe` → 池为空 → `enrich_items` 返回 `[]`）
- **现象**：`total == 0` 时把 `status` 置为 `"ok"`、`reason="no_matching_signals"`、`items=[]`。契约（§1.3）明确要求「空榜的终态必须是 unavailable（不能是 ok 空数组）」。**`board=bse` 必然落到这里**（§1.2.5 明确 bse 板块池为 0 是需求），因此 `GET /screener?board=bse` 恒返回 `{"status":"ok","count":0,"items":[]}`。
- **前端影响**：空数组 + `ok` 与「有榜但全被滤掉」不可区分，前端只能显示空表；`unavailable`（模型未产出/行情缺失）则会走「数据不可用」提示。三态语义在同一字段上出现两种「空」。
- **冲突披露（必须由父审核员裁决）**：`backend/tests/test_data_freshness_degradation.py:119-120` **断言** `status == "ok"`，`docs/audit-2026-09-14/data-freshness-degradation-fix-report.md:56` 亦把 `ok/no_matching_signals` 记为有意设计。本次派单说明又把「空榜终态必须 unavailable」列为硬契约。二者不可兼得，修复时必须同步改该用例（否则用例会把修复判红）。
- **最小验证**（已执行）：

```powershell
cd backend; $env:PYTHONPATH="$PWD"; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -c @"
import polars as pl
from pathlib import Path
from datetime import date
from app.core.config import get_settings
root = Path(r'...\backend\.tmp_b3b_probe2'); root.mkdir(exist_ok=True)
get_settings().DATA_ROOT = root
from app.data.parquet_store import write_partition
uni = pl.DataFrame({'date':[date(2026,9,11)]*2,'symbol':['600001.SH','600002.SH'],'name':['A','B'],
  'board':['main','main'],'is_st':[False,False],'is_halted':[False,False],'industry':['X','Y'],
  'close':[10.0,20.0],'limit_pct':[10.0,10.0]})
write_partition('universe_daily','__all__',date(2026,1,1),uni,dedup_keys=('date','symbol'))
(root/'predictions').mkdir(exist_ok=True)
pl.DataFrame({'date':[date(2026,9,11)]*2,'symbol':['600001.SH','600002.SH'],'pred_score':[0.5,0.4],
              'feature_version':['alpha_basic_v1']*2}).write_parquet(root/'predictions'/'date=20260911.parquet')
from app.api.v1 import screener as S
for b in ('all','bse'):
    d,_ = S._screen(None,'alpha_basic_v1',50,b)
    f = S._finalize_screener_payload(dict(d, items=list(d['items']), stats=dict(d['stats'])))
    print('[A] board=%-4s -> final status=%s reason=%s count=%s' % (b, f['status'], f['reason'], f['count']))
"@
```

真实输出：

```
[A] board=all  -> final status=degraded reason=data_stale count=2
[A] board=bse  -> final status=ok reason=no_matching_signals count=0
```

- **改法建议**：`total == 0` 时 `status="unavailable"`、`reason="no_matching_signals"`（保留机器码以便前端区分「无信号」与「数据缺失」），并把 `test_data_freshness_degradation.py::test_screener_distinguishes_no_signal_and_missing_market_data` 的断言改为 `unavailable`。

---

### B3b-A2【P1｜Bug】**`quotes_snapshot` 静默截断 200 只，预警规则会静默漏报**（确定）

- **位置**：`backend/app/data/quotes_hub.py:68-69`（`syms = syms[:QUOTES_MAX_SYMBOLS]`）；受害调用方 `backend/app/api/v1/alerts.py:511-522`（`evaluate_rules` 合并**全部**行情类规则标的，未分片）；`_rule_symbols`（`alerts.py:299-303`）对 `scope=="watchlist"` 返回**整张自选表、无上限**。
- **现象**：请求 250 只 → 只抓/只回前 200 只，**无日志、无 `truncated` 字段、无 `requested` 计数**。`quotes_by_sym` 缺标的时 `_evaluate_one` 直接跳过（`q` 为 None 不触发），于是**第 201 只起的自选股预警永不触发，且完全不可观测**。`screener.py:531-563` 之所以专门写了分片，正是因为这个截断；`alerts.py` 漏了这一步。
- **附加**：`syms` 顺序取自规则遍历顺序（非排序），截断集合随规则/自选表顺序而变；缓存键却是 `sorted(syms)` 的哈希 ⇒ 不同子集各占一份缓存，命中率进一步下降。
- **最小验证**（已执行，monkeypatch 外部抓取）：

```powershell
.venv\Scripts\python.exe -c @"
import asyncio
from app.data import quotes_hub, realtime
calls={}
def fake(syms):
    calls['n']=len(syms)
    return ([{'symbol':s,'price':1.0,'as_of':'2026-09-11 15:00:00'} for s in syms],'tencent')
realtime.fetch_quotes_batch=fake
syms=[f'{600000+i}.SH' for i in range(250)]
snap=asyncio.run(quotes_hub.quotes_snapshot(syms))
print('requested=250 fetched=',calls['n'],'returned=',len(snap['quotes']))
print('missing=',[s for s in syms if s not in {q['symbol'] for q in snap['quotes']}][:3])
"@
```

真实输出：

```
requested=250 fetched= 200 returned_quotes= 200
missing_example= ['600200.SH', '600201.SH', '600202.SH']
```

- **改法建议**：`quotes_snapshot` 超限时**显式返回** `truncated/requested/returned` 字段（并把 warning 限频），调用方 `alerts.evaluate_rules` 改为按 `QUOTES_MAX_SYMBOLS` 分片（复用 `screener._fetch_quotes_sharded` 的口径），或直接把上限校验上移成业务码错误。

---

### B3b-A9【P1｜Bug】**ETF 资金流历史字段口径错误：`net_inflow = 主力净额 − 小单净额`**（确定，已用真实行情证实）

> **✅ 已修（2026-09-21，第 11 轮，= 报告 P1-14）**：`net_inflow` 直接取 `f52`（主力净额，不做任何加减），
> 并新增分项净额 `super_large_net/large_net/medium_net/small_net` 供独立核对；缺失字段由 `or 0` 改 `None`
> （不再把"无数据"伪造成 0）；列数判据 `<3` 收紧为 `<6`。
> **本条的 29.5% 系笔误，正确值 +29.4392%**（红队手算，与本报告 P1-14 一致）——已被
> `tests/test_etf_flow_field_semantics.py` 用**本页真实观测样本**（f52=274,198,704 / 小单=−80,721,950 /
> 中单=−193,476,752 / 大单=65,674,576 / 超大单=208,524,128）复算固化：旧值 = 354,920,654。
> 偏差公式（本轮补充推导）：旧值 ≡ `2×主力 + 中单` ⇒ 偏差率 = `1 + 中单/主力`。
> 另加"与 `realtime._fetch_em_fflow` 逐字段同构"的守卫用例，防两套解读再次分叉。

- **位置**：`backend/app/data/etf.py:659-689`（`fetch_etf_flow_history`），错误在 677-688 行：注释自称 `f52=main_in, f53=main_out`，代码 `net_inflow = _num(parts[1]) - _num(parts[2])`。
- **事实**：东财 `fflow/kline` 的 fields2 是**净额**序列，不是 in/out 成对：`f51=日期, f52=主力净额, f53=小单净额, f54=中单净额, f55=大单净额, f56=超大单净额, …`。同仓 `realtime._fetch_em_fflow` 的解读是对的（那里 `cols[2]` 明确记为「小单净额」）——**同一 API 两套解读，etf.py 这套是错的**。
- **实测（恒等式验证）**：`secid=1.510300`，最近一根 kline：

```
2026-09-21,274198704.0,-80721950.0,-193476752.0,65674576.0,208524128.0
```

  - 大单 65,674,576 + 超大单 208,524,128 = **274,198,704 = f52** ⇒ f52 就是主力净额（不是 in）；
  - 小单 −80,721,950 + 中单 −193,476,752 = −274,198,704 = −f52 ⇒ 各列皆为净额。
  - 代码输出：`274,198,704 − (−80,721,950) = 354,920,654`，相对真值**高估 29.5%**；误差项 = −小单净额，量级随行情任意变化，**可翻转符号**（小单净流出时虚增，净流入时虚减）。

- **最小验证**：

```powershell
.venv\Scripts\python.exe -c @"
import httpx
p={'lmt':'3','klt':'101','secid':'1.510300','fields1':'f1,f2,f3,f7',
   'fields2':'f51,f52,f53,f54,f55,f56,f57'}
print(httpx.get('https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get',params=p,timeout=6).json()['data']['klines'])
"@
# 输出：['2026-09-21,274198704.0,-80721950.0,-193476752.0,65674576.0,208524128.0']
# 校验：大单(f55)+超大单(f56) == f52 ⇒ f52=主力净额；小单(f53)+中单(f54) == -f52
```

- **改法建议**：`net_inflow = _num(parts[1])`（直接用主力净额），并按需另取 `f53/f55/f56` 明细；同步修正 677-679 行注释。
- **影响面（如实说明）**：调用方 `api/v1/etf.py:662-674` 因 `len(items) < 5` 恒标 `degraded` 并给 note「仅获取到最近 1 个交易日的主力净流入」，所以受影响的只是**一个交易日、且已被标注降级**的数据点；但该数值本身错了约 30%（且符号可错），前端「今日主力净流入」若直接展示即为错值——修复成本 1 行。

---

### B3b-A3【P2｜一致性/披露】**实时快照的 `as_of` 被截成 `HH:MM:SS`：非交易时段返回上一交易日收盘值却无法辨别日期，且 `degraded=False`**（确定）

- **位置**：`backend/app/data/screening.py:755`（`_as_of_time`，`str(as_of)[11:19]`）；使用处 `screening.py` 的 `apply_quote_snapshot`（`source`/`as_of` 写入 `_realtime_basis_fields` 的文案「…实时快照，截至 …」）；出口 `backend/app/api/v1/screener.py:644`（`"as_of": data.get("as_of")`）。
- **现象**：`quotes_hub.quotes_snapshot` 的 `as_of` 原本是「2026-09-18 16:14:37」（腾讯 `f[30]`、新浪 `f[30]+f[31]` 都给到**完整日期**），经 `_as_of_time` 变成 `"16:14:37"`。周末/节假日/停牌时段，腾讯返回的是**上一交易日**的快照（价格、涨跌幅都是上一交易日的值），响应里却是 `source="tencent"`、`degraded=False`、`basis_desc="腾讯实时快照，截至 16:14:37（N/M 只命中）"`（`screening.py:837-845`）—— 客户端**没有任何字段能判断这是哪一天的数据**（`trade_date` 是本地截面日期，也不等于快照日期）。对比：score 榜有 `_freshness`（`lag_trading_days/is_stale`，`api/v1/screener.py:54-97`），`/screener/stocks` 完全没有等价物（响应里的 `stale` 字段只来自 SWR 过期命中，`api/v1/screener.py:655`，与行情新鲜度无关）。
- **最小验证**：

```powershell
.venv\Scripts\python.exe -c "from app.data.screening import _as_of_time; print(repr(_as_of_time('2026-09-18 16:14:37')))"
# 真实输出: '16:14:37'
```

- **改法建议**：`as_of` 保留完整日期时间（或用 `as_of_date` + `as_of_time` 两个字段）；对实时路径复用 `_freshness`（把快照日期与「最近已收盘交易日」比较）并对非交易时段标 `degraded` + `reason="off_hours_snapshot"`。

---

### B3b-A4【P2｜Bug】**`pred_score` 部分为 null 时：null 排到榜首且 `enrich_items` 抛 TypeError（未走降级）**（机制确定；触发条件疑似，需现场分区证据）

- **位置**：`backend/app/data/screening.py:132`（`df.sort("pred_score", descending=True)` 缺 `nulls_last=True`）、`screening.py:238`（`round(float(r["pred_score"]), 6)`）；守卫只覆盖整列 null：`screening.py:128-131`；同类写法还有 `backend/app/orchestrator.py:319-323`（raw predictions 分区上的同一单列 sort，早于 `write_screener_snapshot`）。
- **现象**：
  1. polars 单列 `sort(descending=True)` 默认 `nulls_last=False` ⇒ **null 排最前**（实测：`[None,None,0.9,0.7,0.5]`）。于是无分数的标的占据 rank 1…k，并被 `signal_strength_by_rank` 标成 **strong**，直接污染榜单顶部；
  2. `enrich_items` 对第一名取 `float(None)` → `TypeError`。`_screen` 只 `except AQPException`，`TypeError` 会穿透到全局异常处理器（信封 code 50000），**不是**契约要求的 `status="unavailable"` 降级。
- **可达性（疑似）**：`pred_score` 由 `LightGBM.predict` 直出（`ml/infer.py:100-102`、`orchestrator.py:248-250`），正常路径应为有限浮点；但 2026-09-18 刚为「整列 Null」修过同类 panic，**部分为 null**（例如未来改接 reindex/左连接、或特征表缺行）尚无守卫。判定：机制与后果确定，是否已发生需看 `data/predictions/date=*.parquet` 的 `pred_score` 空值计数。
- **最小验证**（已执行）：

```powershell
.venv\Scripts\python.exe -c @"
import polars as pl
from pathlib import Path
from app.core.config import get_settings
get_settings().DATA_ROOT = Path(r'...\backend\.tmp_b3b_probe')   # 空目录，跳过 universe join
from app.data.screening import filter_universe, enrich_items
pred = pl.DataFrame({'date':['2026-09-11']*5,
  'symbol':['600001.SH','600002.SH','600003.SH','600004.SH','600005.SH'],
  'pred_score':[None,0.9,0.5,None,0.7]})
df,pool = filter_universe(pred,'2026-09-11','all')
print(df.select(['symbol','pred_score']).to_dicts())
try: enrich_items(df,5)
except Exception as e: print(type(e).__name__, e)
"@
```

真实输出：

```
sort order: [{'symbol': '600001.SH', 'pred_score': None}, {'symbol': '600004.SH', 'pred_score': None},
             {'symbol': '600002.SH', 'pred_score': 0.9}, {'symbol': '600005.SH', 'pred_score': 0.7},
             {'symbol': '600003.SH', 'pred_score': 0.5}]
enrich_items -> TypeError float() argument must be a string or a real number, not 'NoneType'
```

- **改法建议**：`sort(["pred_score","symbol"], descending=[True,False], nulls_last=True)`；`enrich_items` 对 `pred_score is None` 的行使 `score=None, signal_strength=None`（或先过滤）；守卫从 `dtype == pl.Null` 扩展为 `null_count() == height`。`orchestrator.py:323` 同步加 `nulls_last=True`。

---

### B3b-A5【P2｜一致性】**主源结构性异常（列名漂移）被静默降级为新浪，且降级数据 `source` 仍写 `"akshare"`**（确定，已实测）

- **位置**：`backend/app/data/ingest/akshare_adapter.py:238-257`（`fetch_daily_bar` 的 `except Exception → _fetch_daily_bar_sina`），`_standardize_daily` 在缺 `date` 列时抛 `ValueError`（结构化故障，非网络故障）；`akshare_adapter.py:230`（新浪分支硬写 `source="akshare"`）。
- **现象**：docstring 声称「只有网络类异常或主源结构性异常才会抛异常，不会静默吞掉」，实现却把**结构性 `ValueError` 一起吞掉**，只打一条 WARNING（实测日志：`eastmoney daily fetch fail 600519: ValueError(...) -> fallback to sina`）并返回新浪数据，落盘 `source="akshare"`。后果三点：
  1. 主源接口变更**不会**触发任何失败/告警升级（sync 记为成功）；
  2. `source` 列失去可审计性（EM/Sina 不可区分，`quality.py:82` 明确把 `source` 定义为追踪用途）；
  3. 同一 symbol 序列里 EM 与 Sina 的 hfq 复权基准不同（两家各算），源切换处会出现一次性水平跳变，而**唯一能发现的 `check_factor_jump` 只在 CLI 路径**（见 B3b-A6）——**若两源基准不一致，该跳变会被 CLI 判成“数据损坏/error”，生产路径则完全无感**（此后果为疑似，需比对同一 symbol 的 EM/Sina hfq 序列确认）。
- **最小验证**（已执行，注入结构性异常）：

```powershell
.venv\Scripts\python.exe -c @"
import pandas as pd
from app.data.ingest import akshare_adapter as A
pdf = pd.DataFrame({'date':['2024-01-02'],'open':[10.0],'high':[10.5],'low':[9.9],
                    'close':[10.2],'volume':[100.0],'amount':[1e6]})
A._fetch_daily_bar_em = lambda *a,**k: (_ for _ in ()).throw(ValueError('东方财富日线响应缺 date 列——主源接口可能已变更'))
A._fetch_daily_bar_sina = lambda code,start,end,adjust: A._standardize_daily(pdf.copy(), code)
o = A.fetch_daily_bar('600519','2024-01-01','2024-01-05','')
print(o.height, o['source'].to_list())
"@
```

真实输出：

```
[E] 主源 ValueError 后返回 rows=1 source=['akshare']
WARNING | app.data.ingest.akshare_adapter:fetch_daily_bar:256 - eastmoney daily fetch fail 600519: ValueError(...) -> fallback to sina
```

- **改法建议**：区分「网络类」与「结构类」异常（后者用专用异常类型，**不参与降级**，直接上抛让 sync 记 FAILED）；无论如何 `_fetch_daily_bar_sina` 的 `source` 必须写 `"sina"`，并在年分区的 manifest/`source` 分布上做可观测统计。

---

### B3b-A6【P2｜一致性/误报】**用户可见的「数据质量体检」路径：合法除权日被判 error；且 `factor_jump`、`calendar` 两项检查永不触发**（确定，已实测）

- **位置**：`backend/app/api/v1/ops.py:34`（docstring 声明检查「复权突变/价格突变/**日历**」）与 `ops.py:48-64`（用 `QCThresholds()` + `validate_partition(df, dataset, sym, year, th)`）；`backend/app/data/quality.py:436-459`（`validate_partition` 的检查集：schema/null/dup/ohlc/daily_return/year_density/calendar，**没有 `check_factor_jump`**）。
- **现象 1（误报，用户可见）**：`validate_partition` 调 `check_daily_return(df, dataset, symbol, th, is_st, list_date)`，**既不传 `exempt_dates`**（除权/除息日豁免）**也不传 `list_date` 之外的新股口径** ⇒ raw `daily_bar` 上任何合法的 10 送 10（−50%）、大比例分红除息都会被判 `daily_return/error`。CLI 路径 `scan_dataset → _partition_issues(..., exempt_dates=ex_div)` 才有豁免。实测同一份 10 送 10 数据：API 路径 = `[('daily_return','error'), ('year_density','warn')]`，CLI 路径 = `[('year_density','warn')]`。
- **现象 2（永不触发）**：`check_factor_jump` 只在 `scan_dataset` 里调用，`ops.quality_scan` **永远不可能**返回 `kind="factor_jump"`；`check_calendar` 需要 `trade_days`，而该端点不传（`ops.py:60`）⇒ 默认 `None` ⇒ `quality.py:458` 直接跳过。也就是说前端「数据质量」页里声称的三类检查（复权突变/日历/除权豁免）在这条路径上全部失效。
- **最小验证**（已执行）：见 §5 附录 P4；命令与输出：

```powershell
.venv\Scripts\python.exe -c @"
import polars as pl
from datetime import date
from app.data.quality import QCThresholds, validate_partition, _partition_issues
exd = pl.DataFrame({'date':[date(2026,6,1),date(2026,6,2)],'open':[10.0,5.0],'high':[10.0,5.1],
  'low':[10.0,4.9],'close':[10.0,5.0],'volume':[1000.0,2000.0],'amount':[1e6,1e6],
  'turnover':[0.01,0.02],'code':['000028']*2,'symbol':['000028.SZ']*2,'source':['akshare']*2})
th=QCThresholds()
print('API:', [(i.kind,i.severity) for i in validate_partition(exd,'daily_bar','000028.SZ',2026,th)])
print('CLI:', [(i.kind,i.severity) for i in _partition_issues(exd,'daily_bar','000028.SZ',2026,th,None,{date(2026,6,2)})])
"@
# API: [('daily_return', 'error'), ('year_density', 'warn')]
# CLI: [('year_density', 'warn')]
```

- **改法建议**：`ops.quality_scan` 改为调用 `scan_dataset`/`_partition_issues`（带 `trade_days=load_trade_days(SQLITE_PATH)` 与自动推导的除权日），或至少在 `validate_partition` 内补 `exempt_dates`；端点 docstring 与实际检查项必须一致。

---

### B3b-A7【P2｜数据完整性】**写入门禁不含日期/行数完整性校验：残缺抓取会被当完整数据合并入年分区**（确定）

- **位置**：`backend/app/data/ingest/tasks.py:115-129`（`write_daily_bars` → `validate_write_gate`）、`tasks.py:182-194`（门禁只跑 schema/null/dup/ohlc）、`backend/app/data/quality.py:78-79`（`WRITE_GATE_THRESHOLDS` 把 `min_year_rows` 归零、`max_year_rows` 放宽**且该阈值永不被读取**，因为门禁根本不调 `check_year_density`）。
- **现象**：抓取侧一旦返回「短窗/截断」结果（EM 限流返回部分日期、新浪返回较短区间、降级源只回最近 N 天），`write_partition` 按 `date` 去重合并，**缺失日期静默留空**，没有任何断言比较「抓到的日期集合」与「`[start,end]` 内交易日历」。`step_validate` 只看 `trade_date` 当天一行（`pipeline.py:106-124`），跨天数缺口不会被它发现；`check_year_density`（min 180 行/年）只在 CLI `qc_scan` 里以 **warn** 级出现。这正是派单里「降级后是否把残缺数据当完整数据落盘」的落点。
- **触发条件**：初始 bootstrap（`fetch_and_write_daily_bars(code, start, end)` 传 120 天窗）、`scripts/update_daily.py`、任何 `--start/--end` 回补，且源返回少行。
- **最小验证（只读，建议执行）**：

```powershell
# 对任一 symbol 的任一年分区，比较实际日期数与该年交易日数
.venv\Scripts\python.exe -c @"
import polars as pl, sqlite3
from app.core.config import get_settings
s=get_settings(); f=next((s.DATA_ROOT/'daily_bar').glob('symbol=*/year=2026.snappy.parquet'))
d=pl.read_parquet(f, columns=['date'])
c=sqlite3.connect(s.SQLITE_PATH)
n=c.execute(\"select count(*) from trade_calendar where trade_date like '2026-%'\").fetchone()[0]
print(f.parent.name, 'rows=',d.height,'trading_days_2026=',n)
"@
```

- **可断言的最小单测思路**：注入一个只返回 `[start, start+3]` 的 fetcher，写入 120 天窗后断言「年分区行数 == 交易日历覆盖行数」，当前必然红。
- **改法建议**：`write_daily_bars` 增加 `expected_dates`（由 `get_calendar` + `[start,end]` 推导）参数，缺失率超阈值 → 记 `warn` 并写入 manifest 的 `coverage` 字段；或让 `validate_write_gate` 带上 `min_year_rows` 的「区间≥5 日时按比例」版本。

---

### B3b-A8【P2｜一致性】**quotes SSE 的 `interval` 参数不生效 + 首帧最长等 30s**（确定）

- **位置**：`backend/app/data/quotes_hub.py:180-181`（`await asyncio.sleep(clamp_quotes_ttl(QUOTES_TTL_DEFAULT))`，硬编码 30s）、`backend/app/api/v1/notify.py:88-90/124`（`interval` 参数文档写「quotes 推送间隔秒（服务端钳制 [15,120]）」，实际只用作 `asyncio.wait_for(out.get(), timeout=min(_HEARTBEAT_SECONDS, push_interval))` 的本地超时）。`quotes_snapshot` 的缓存 TTL 用的是 `get_settings().QUOTES_TTL`，与推送节奏是两套值。
- **现象**：客户端传 `interval=120` 仍每 30s 收到推送；传 `interval=15` 也收不到 15s 节奏（只能拿到心跳注释帧）。另外 `subscribe_quotes` 不立即推一份快照，**订阅者最多要等一个推送周期（30s）才收到第一批行情**（列表页切进来会长时间空白，只有 `: connected`）。
- **最小验证**：读 `quotes_hub.py:170-190` 与 `notify.py:124` 即可判定（无外部依赖）；运行时可断言「`interval=120` 时 60s 内推送条数 ≈ 2」。
- **改法建议**：`_quotes_loop` 用调用方注册的 interval（取所有订阅者的最小值或统一取 `QUOTES_TTL`），或在 `subscribe_quotes` 后立即 `q.put_nowait(await quotes_snapshot(...))`。

---

## 2. 目标 B — 死代码 / 未接线

> 简报要求「未接线」单列：以下 B1/B2/B3/B7 都是**写了、有测试、但没有任何生产入口能触达**的功能，比普通死代码危险（读起来像已交付的能力）。

### B3b-B1【P2｜未接线】`ingest/multi_source.py` 整个「三源冗余（P1-2）」未接线（确定）

- **证据**：`fetch_daily_bar_multi` 全部引用 = 定义处 + `backend/tests/test_multi_source.py`（5 处），**无生产调用方**。生产抓取链路是 `tasks.fetch_and_write_daily_bars(fetcher=akshare_adapter.fetch_daily_bar)`（`tasks.py:157-160`），只有 EM→Sina 两源。
- **附带语义冲突**：`multi_source._from_akshare:50-52` 在 `fetch_daily_bar` 返回空表时 `raise ConnectionError("akshare returned empty")` ⇒ 把「停牌/退市/无数据」当**抓取失败**并触发降级、最终 `raise ConnectionError("全部数据源失败")`（`multi_source.py:153`）。这与 `akshare_adapter.fetch_daily_bar` docstring 明确的「空数据 = 无数据，不是抓取故障，由下游处理」相反。若接线，停牌股会被计入 `failed`，与 `test_sync_integrity`/断点续传语义冲突。
- **最小验证**：`rg -n "fetch_daily_bar_multi" backend --glob '!**/.venv/**'` → 只有 tests 命中。
- **改法建议**：要么把 `multi_source` 接进 `tasks.fetch_and_write_daily_bars` 的默认 fetcher（并先修正「空 = 失败」），要么删除并在 docstring 里如实说明当前只有两源——**不要让「P1-2 三源冗余」停留在文档里**。

### B3b-B2【P2｜未接线（已知条目延伸）】公告写路径 `save_announcements` 无生产调用方，而生产读路径正依赖它

- **证据**：`ingest/announcements.py:71 save_announcements` / `:82 load_announcements_asof` 的引用只有 `tests/test_p1_data.py`；生产读路径 `data/announcements.py::read_announcement_frame`（被 `panels.build_events`、`api/v1/market.py::_latest_announcements` 使用）读的正是 `DATA_ROOT/announcements/symbol=__all__/year=*.parquet`——即 `save_announcements` 写的目录。`docs/audit-2026-09-15/REVIEW-ROUND2.md:80` 已把「由 save_announcements 写入」当成事实，但全仓没有调用点。
- **延伸价值**：不是「某函数没人用」，而是**读链路已接线、写链路断链** ⇒ 个股「近期事件」块只在手工种过 parquet 的机器上有数据；`fetch_announcements` 也不在任何 sync/pipeline 步骤里。
- **最小验证**：`rg -n "save_announcements" -g '!*.parquet' .` → 只有定义/文档/测试。

### B3b-B3【P2｜未接线】`ingest/financials.py` 三个函数全部无生产调用方

- **证据**：`fetch_financials`/`save_financials`/`load_financials_asof` 引用仅 `tests/test_p1_data.py`。个股财务块走的是 `realtime.fetch_financial_indicators`（东财实时快照，**无 PIT 保证**），因此 PIT 财务表 `financial_report` 在生产上恒为空。
- **最小验证**：`rg -n "load_financials_asof|save_financials" backend/app backend/scripts -g '*.py'` → 0 命中（除定义）。

### B3b-B4【P2｜死代码 + 存储不一致（已知条目延伸）】`ingest/dividends.py` 全模块未接线，且**写 SQLite / 读 Parquet**

- **已知**：`docs/audit/2026-09-05-代码审核报告.md` P2-5 已报「分红模块是死代码、`load_dividends_asof` 会静默返回空表」。
- **新增证据（本轮）**：
  1. `save_dividends`（`dividends.py:52-79`）写的是 **SQLite `dividend_split` 表**（`sqlite_insert(DividendSplit)`），而 `load_dividends_asof`（`dividends.py:82-94`）读的是 **`DATA_ROOT/dividend_split/symbol=__all__/year=*.parquet`**；全仓无任何代码写该 parquet（`rg dividend_split` → 仅 `models.py` 表名与本模块）。⇒ **即使把 `save_dividends` 接进 sync，读路径仍然恒空**，不是「没接数据」而是「读写不是同一个存储」。
  2. `save_dividends` 内部 `asyncio.run(_go())`（`dividends.py:78`）：一旦从任何 async 上下文（FastAPI 路由 / 流水线协程）调用即 `RuntimeError: asyncio.run() cannot be called from a running event loop`。`financials.save_financials:57` 同款。
- **最小验证**：`rg -n "dividend_split" -g '!*.parquet' .`；`rg -n "save_dividends|fetch_dividends|load_dividends_asof" backend/app backend/scripts -g '*.py'`。

### B3b-B5【P3｜死代码 + 潜在错误】`ingest/validate.check_pct_limit` 永不触发；单位混用

- **证据**：`ingest/validate.py:56-62` 读 `pct` 列；而 `pct` **从不落盘**——`quality.py:82` 明确「'pct' 可由 close/prev_close 推导，不入库」，`normalize_schema` 只保留 canonical 列。`validate_daily_bar` 的唯一生产调用方 `pipeline.step_validate:108-110` 读的是落盘分区 ⇒ 该检查恒返回 `(True, [])`。
- **潜在错误**：阈值 `max_abs=0.45` 按**小数**写，但 akshare 的 `涨跌幅` 是**百分数**（`akshare_adapter._RENAME_DAILY` 直接映射）。实测把 `pct=8.8`（正常 +8.8%）喂进去 → `(False, ['|pct| > 45% 共 1 行'])`，即**任何 |涨跌| > 0.45% 的正常波动都会被判错**。一旦有人为「修复死检查」把 pct 传进来，会立刻大面积误报。
- **最小验证**（已执行）：

```
[C] 落盘后是否保留 pct: False | check_pct_limit(落盘schema)= (True, []) | check_pct_limit(原始 pct=8.8)= (False, ['|pct| > 45% 共 1 行'])
```

- **改法建议**：删除该检查或改成「用 close/prev_close 现场推导 pct（百分数）并与阈值 45.0 比较」，顺手清理 `WRITE_GATE_THRESHOLDS` 里永不被读的 `min/max_year_rows` 覆盖。

### B3b-B6【P3｜死代码】`repair.is_quarantined` 无调用方且语义与 docstring 相反

- **证据**：`repair.py:135-141` 仅定义、无引用（同文件 `quarantined_symbols` 被 `scripts/repair_data.py:181` 使用）。docstring 写「整只标的中毒（**出现在 manifest 且数量覆盖多数据集**）」，实现只判断 `any(e.get("symbol") == symbol ...)` ⇒ 任一分区被隔离即返回 True。若将来接线做「跳过已隔离标的」，会把只因一个坏分区被隔离的正常标的整只跳过。
- **最小验证**：`rg -n "is_quarantined" backend -g '*.py'` → 仅定义处。

### B3b-B7【P2｜未接线】文本因子链路产出永不入模：`attach_text_features` 无调用方

- **证据**：`text_ingest.py:324 attach_text_features` 无任何调用方；`rg -n "text_features|sentiment|attach_text" backend/app/ml -g '*.py'` → **0 命中**（features/v2/train_lgbm 全都不碰文本因子）。而 `text_ingest.py:14` 声称「公告日 T 的信息在训练/推理侧经 attach_text_features 消费」。
- **实际接线状况**：`/datacenter/text/import`、`/datacenter/text/build`、`/datacenter/text/status` 已接（`api/v1/datacenter.py:1033-1058`），所以用户能导入文档、能生成 `text_features/version=sentiment_v1/year=*.parquet`，**但这些因子对选股/训练零影响**——属于典型「看起来已交付」的未接线。
- **改法建议**：要么在 `ml/features.py` 的特征装配处调用 `attach_text_features`（注意 `lag_days=1` 的 T+1 可见性），要么在 `/datacenter/text/status` 里如实标注 `wired_into_model=false`，避免误导。

### B3b-B8【P3｜死代码】`akshare_adapter.fetch_daily_bar_batch` / `fetch_daily_bar_batch_by_year` 无调用方

- **证据**：两个函数的引用只有自身（`akshare_adapter.py:359,416-429`）。生产全市场同步是 `services/sync_service.py` 的**单线程顺序循环**（`:232/288/338` 直接调 `tasks.fetch_and_write_daily_bars`，全仓唯一的并发原语是 `:662` 那一个 `threading.Thread` 跑 worker），完全不使用按年/按标的的批量并发抓取。
- **附带**：`fetch_daily_bar_batch_by_year`（`:416-429`）会调用 `fetch_daily_bar_batch(start=f"{year}-01-01", end=f"{year}-12-31")`，与 `tasks.fetch_and_write_daily_bars` docstring 明令禁止的「逐年抓取再拼接（复权基准漂移）」做法一致；若有人误用它，正是 CRIT-002 那类事故。

---

## 3. 目标 C — 策略合理性（`screening.py` 为主）

> 五要素格式：当前问题 / 具体改法 / 预期收益 / 引入风险 / 验证方式。

### C1【P3｜策略】`signal_strength` 分档在榜内退化为「rank < 40」，零信息量且跨板块不可比

- **当前问题**：`enrich_items:219-220` 的参考总体恒为 `df.head(SIGNAL_REFERENCE_DEPTH=200)`，`signal_strength_by_rank(n)` 按 `i/n` 分档 ⇒ 只要板块池 ≥ 200，榜内 rank 1–40 **恒为 `strong`**。默认 `top_k=50` 时页面 50 行里 40 行都挂「强信号」，第 41–50 行恒为 `neutral`，**从不出现 weak**；`top_k=10` 时 10 行全 strong。标签完全由位置决定，与 `pred_score` 的绝对水平、与分数分布形态都无关。同时跨板块不可比：60 只池里的第 30 名与 5000 只池里的第 30 名同样标 `strong`。
- **具体改法**：保留「相对分位」思路但换总体——用**当日全市场（board=all 池）分位**作为唯一参考总体，并把数值分位随行返回（`score_pct_rank = rank / pool_size`，或 `quantile` 档：top1% / top5% / top20%）；前端用数值分位显示强度条，`signal_strength` 三态改成按**全市场**分位切（如前 1% strong、前 5% neutral、其余 weak）。若必须保持「实时榜与快照标签一致」，只需保证两路径用**同一参考总体（board=all 池）**即可。
- **预期收益**：`strong` 从「页面 80% 行」降到「页面 0–1 行（前 1% 时）」，标签恢复区分度；跨板块可比；无额外 IO（`signal_strength_reference_map` 已额外算 all 榜）。
- **引入风险**：小池板块（bse=0、chinext_star 数百只）可能出现「全弱」，需在前端文案上说明「弱 ≠ 不建议，仅表示未进入全市场前列」；改口径属**展示口径变更**，需同步 `screener_snapshot.signal_strength` 的存量数据（`_translate_signal_strength` 只做旧枚举映射，不做分位重算，建议重跑快照）。
- **验证方式**：单测断言「板块池 5000、top_k=50 时 strong 数量 == 全市场分位定义的期望值（如 ≤1）」，并把 `signal_strength_by_rank(200)` 的 40/60/100 分布固化成回归。

### C2【P3｜策略】榜单缺流动性 / 涨跌停 / 次新股维度，且核心榜单无同分次级键

- **当前问题**：
  1. `filter_universe:107-111` 只 join `(symbol,name,industry,board,is_st,is_halted,close,limit_pct)`，把 universe 里**现成的** `list_date / days_since_list / limit_up / limit_down`（`universe.py:248-250` 输出）全部丢弃 ⇒ 上市不足 5 日（`is_new_issue`，`universe.py:220-223`）的次新股可以进榜前 40，它们的因子多为缺失、模型分数缺少可比性；涨停/跌停标的（当日不可成交）没有任何标记，经验上动量模型会把涨停股排到最前，用户看到的是「买不进的第一名」。
  2. `filter_universe:132` 是单列 `sort("pred_score")`，**无次级键**。同仓 `sort_stock_rows` 明确用 `[order_col, "symbol"]` 做稳定次级排序（注释：「否则同值行在并发/分页间顺序不定」），核心榜单却没有。polars 单列 sort 未承诺稳定，我实测 10/1000/5000 行同分场景恰好保序（**故此项为「疑似，需验证」**），但一旦 reorder，`screener_snapshot` 的 rank 与实时榜会漂移，破坏模块自称的「两路径数字必须一致」。
- **具体改法**：`filter_universe` 的 `join_cols` 增加 `list_date/days_since_list/limit_up/limit_down`；(a) 榜内增加 `is_new_issue` 与 `at_limit_up/at_limit_down` 布尔标记并在 `basis_fields` 披露；(b) 可选加 `days_since_list >= 60`（可配置）硬过滤，先在快照上做 A/B；排序统一改 `sort(["pred_score","symbol"], descending=[True,False], nulls_last=True)`。
- **预期收益**：剔除「不可买」与「无因子史」的头部噪声；排序可复现（快照 rank 与实时榜一致，前端分页/对齐不再跳行）。按 A 股惯例，新股/涨停剔除通常能显著降低榜单头部的空转率（量级取决于策略本身，**不要在没有样本外验证前把它当收益承诺**）。
- **引入风险**：硬过滤会缩小可选池（牛市次新动量强，可能砍掉真实 alpha）；`limit_up` 判据依赖 `prev_close` 与整数化规则，误判会把可交易标的挡掉。建议先把标记做出来（无风险），过滤阈值用回测样本外验证后再启用。
- **验证方式**：(a) 单测：构造含 `days_since_list=2`、`close==limit_up` 的 universe 行，断言标记列存在且取值正确；(b) 稳定性：对同一 pred 连续调用 100 次断言 rank 序列完全一致；(c) 过滤效果：用 `predictions/date=*` 历史分区离线统计「被过滤标的的 T+1 收益分布」，若显著低于保留组再启用。

### C3【P3｜策略】实时快照合并的「两路径一致」只在字段层面成立，口径层面有混源

- **当前问题**：`_fetch_quotes_sharded`（`api/v1/screener.py:527-563`）按 200 分片，每个分片独立走 `quotes_hub.quotes_snapshot`（腾讯→新浪）。来源只记录**第一个非 degraded 分片**（`:557-559`）——若分片 A 成功于腾讯、分片 B 落到新浪，响应仍整体标注「腾讯实时快照」。而新浪解析器 `_fetch_sina_quotes_batch`（`realtime.py:348-399`）对 `turnover/float_cap_yi/total_cap_yi/limit_up/limit_down` **恒返回 None** ⇒ 这些行在 `apply_quote_snapshot` 里 `turnover` 保持**本地日终值**（`screening.py:832-835` 只在非 None 时覆盖），市值则被写成 null。于是同一次响应内：一部分行的 `turnover` 是**日内实时换手**（腾讯），另一部分是**上一交易日日终换手**（本地），而 `_realtime_basis_fields`（`screening.py:617-627`）**根本没有 `turnover` 键**——该列的来源与时间口径在响应里完全无法辨识。这正是模块自定红线「绝不部分混用：一半行实时、一半行日终」的弱化版。**注意**：`quote_coverage {hit,total}` 与逐行 `quote_status` 已如实披露「命中数」，所以不是「静默」；缺的是**混源**这一维度。
- **具体改法**：`_fetch_quotes_sharded` 统计每个分片的实际来源，返回 `source_mix: {tencent: n1, sina: n2, local: n3}` 并写进 `basis_desc`/`basis_fields`，同时给 `_realtime_basis_fields` **补上 `turnover` 键**（说明「腾讯=日内实时 / 新浪=不推算、保留本地日终值」）；或按「最差来源」统一降级（有任一分片回落新浪即把整表标 `degraded`）。更彻底：给每行带 `quote_source` 字段。
- **预期收益**：消除「列 + 单一来源标签」与真实口径不符；用户/前端能判断换手率是否可比。
- **引入风险**：`source_mix` 是响应字段新增，前端需容忍未知字段（现有前端按需取值，向后兼容）；整表降级会让混合场景下页面显示「降级」，可能被误以为故障——建议只做披露（`source_mix` + `turnover` basis）不做整表降级。
- **验证方式**：monkeypatch 让第 2 个分片返回 `source="sina"`，断言响应含 `source_mix` 且 `turnover` 的来源口径可辨。

### C4【P3｜策略】`_stats` 的「平均分/最高分/上涨占比」在榜单被截断后不再是池统计量

- **当前问题**：`compute_stats(items, pool_size)`（`screening.py:260`）用**榜内 top_k 行**算 `avg_score/max_score/up_ratio`，而同一响应里的 `pool_size` 是**全池**规模（快照路径甚至来自 `screener_snapshot_stats.stats_json`）。前端把 `avg_score` 当作「全池平均分」展示时会被 top_k 偏高（`top_k=50` 时是池的前 1–5%），且 `top_k` 不同统计量口径不同（快照 top-200 vs 实时 top-50 的 `avg_score` 不可比）。字段本身没有 `basis` 说明它是「榜内」还是「池内」。
- **具体改法**：改名为 `top_avg_score/top_max_score/top_up_ratio` 或在 `stats` 增加 `scope: "topk"` + `k`；若要真正的池统计，在写快照时顺带落全池 `mean/std/up_ratio`（`write_screener_snapshot` 手上有完整排序后的 df，零额外 IO）。
- **预期收益**：口径可辨（契约 §1.5「派生指标必须披露口径」），且能让「今日榜平均分 vs 昨日」真正可比。
- **引入风险**：字段改名影响前端读取（可先**新增**字段、旧字段保留一个版本）。
- **验证方式**：单测断言 `top_k=50` 与 `top_k=200` 时新字段 `scope/k` 正确披露，且全池统计量与 `filter_universe` 的完整 df 一致。

### C5（因子选择经济逻辑的总体评价，不含改法承诺）

- 生产打分口径是 **LightGBM `pred_score`**：`objective="regression"`（L2 回归预测未来 N 日收益，`ml/train_lgbm.py:44`）+ `alpha_basic_v2g` 特征空间（`ml/features.py:33`），标签侧可选横截面去均值（`train_lgbm.py:233`，与 `screening.py:36-40` 观察到的「分数尺度极小、max≈0.0398」一致）。**`screening.py` 本身不引入任何因子权重**——所谓「alpha_basic_v1 策略」在数据层只是「按模型分降序 + ST/停牌过滤 + 板块切分」。因此不存在「因子权重过拟合」问题，但也意味着：**任何 alpha 层面的缺陷都落在 `ml/features.py` 与训练口径上，不在本批**；数据层能做且应该做的是把「不可交易/不可比」的头部噪声去掉（C2）与把展示口径说清楚（C3/C4）。本轮**未发现伪因子**（没有把衍生字段当原始行情冒充，`basis_fields` 会标注 `腾讯实时快照` 等来源）。
- 顺带一条与 C1 相关的经济含义：若标签做了横截面去均值，`pred_score` 表达的是「相对同日全市场的预期超额」，本身就是**相对量**；此时再按「板块池分位」二次相对化（C1）会让 `strong` 的含义变成「板块内相对强」而非「全市场相对强」，两个相对化叠加后与「哪些标的值得关注」之间没有稳定映射——这是 C1 建议改用全市场分位的另一条理由。
- 需要提醒的一点：`STRATEGY = "alpha_basic_v1"`（`screening.py:33`）与生产特征空间 `alpha_basic_v2g`（`orchestrator.py:293-296` 注释）**不同代**；`feature_runs` 已按预测分区真实列写 `feature_version`，但 `screener_snapshot.strategy` 恒为 `alpha_basic_v1`、接口默认 `strategy="alpha_basic_v1"` ⇒ 快照表的「策略名」无法区分 v1/v2g 产出，跨代对比时容易误读（属命名/口径问题，P3，未单列成条）。

---

## 4. 明确「未发现 P0–P2」的文件

- `ingest/etf_instruments.py`：`build_etf_instrument_frame` 空目录返回**同 schema 空表**、`upsert` 前判空不写伪造行（`etf_instruments.py:103-104,131-133`）；`cn_etf_symbol` 对 `1xxxxx` 深市 ETF 的处理有注释与测试依据，未发现 P0–P2。
- `ingest/__main__.py`：`--stage` 分支、`init_sqlite_file`（WAL）、ETF 目录失败只 warning 不阻断股票目录，均符合设计；唯一瑕疵是第 94 行日志文案「三口径」而实际只有两口径（P3 文案，不上表）。
- `pipeline.py`：`step_validate` 的「纯比例容差、无绝对下限」与「日历不可用降级为覆盖率」两处都按 FIX-SPEC 落地，返回值区分 `degraded=0/1`，未发现 P0–P2（完整性缺口见 B3b-A7）。
- `announcements.py`：坏文件跳过并「如实缩水」、`pub_date` cast 容错、`limit<=0` 按 1 处理，未发现 P0–P2。
- `realtime.py` 除下述一条 P3 外，**限速/重试/字段映射经真实行情验证正确**：`qt.gtimg.cn` 实拉 `sh600519` 得 `f[3]=1252.57, f[4]=1257.12(昨收), f[32]=-0.36, f[38]=0.20(换手), f[47]=1382.83=昨收×1.1, f[48]=1131.41=昨收×0.9`，与 `_parse_tencent_batch` 的索引一致（含涨跌停价整数化）；新浪源 `_fetch_sina_quotes_batch:348-399` 的 `f[2]=昨收/f[3]=现价/f[8]=成交量(股→手)/f[30]+f[31]=日期时间` 亦正确。
  - **P3（未单列）**：`_fetch_em_quote._v`（`realtime.py:243-247`）`if not isinstance(v,(int,float)) or v in (-1, 0): return None` 把**合法的 0** 与东财的 `-1` 哨兵一并当缺失 ⇒ 单股 EM 兜底路径下平盘股（`f170=0`）与换手率为 0 的标的被写成 `None`；腾讯批量路径正常返回 `0.0`（`_parse_tencent_batch` 用 `if not f[3]` 判空）。触发：`fetch_quote` 落到 EM 源且当日平盘。
- `quality.py` 自身的检查函数（`check_daily_return`/`check_factor_jump`/`check_adjusted_continuity`/`check_year_density`/`detect_ex_div_dates`）逐条读过，阈值取值有制度依据、`legit`/`broken` 二级判定与「先隔离后修复」纪律一致；问题出在**调用方传参缺失**（B3b-A6），不在函数本身。

---

## 5. 附录：全部实测命令与原始输出

> 所有 probe 均写入临时目录并在结束后删除；未修改任何源码、未写仓库 `data/`。环境：Python 3.11.15 / `backend/.venv`，`PYTHONIOENCODING=utf-8`。

**P1 — 空榜三态 / null 排序 / pct 检查 / 除权误报 / 主源静默降级（一次跑完）**

```
[A] board=all  -> final status=degraded reason=data_stale count=2
[A] board=bse  -> final status=ok reason=no_matching_signals count=0
[B] sort order: [{'symbol': '600001.SH', 'pred_score': None}, {'symbol': '600004.SH', 'pred_score': None},
                 {'symbol': '600002.SH', 'pred_score': 0.9}, {'symbol': '600005.SH', 'pred_score': 0.7},
                 {'symbol': '600003.SH', 'pred_score': 0.5}]
[B] enrich_items -> TypeError float() argument must be a string or a real number, not 'NoneType'
[C] 落盘后是否保留 pct: False | check_pct_limit(落盘schema)= (True, []) | check_pct_limit(akshare 原始 pct=8.8)= (False, ['|pct| > 45% 共 1 行'])
[D] API path: [('daily_return', 'error'), ('year_density', 'warn')]
[D] CLI path: [('year_density', 'warn')]
[E] 主源 ValueError 后返回 rows=1 source=['akshare']
WARNING | app.data.ingest.akshare_adapter:fetch_daily_bar:256 - eastmoney daily fetch fail 600519: ValueError(东方财富日线响应缺 date 列——主源接口可能已变更) -> fallback to sina
```

**P2 — polars 单列 sort 的 null 位置（B3b-A4 的前置事实）**

```
desc default:        [{'b', None}, {'d', None}, {'a', 0.9}, {'c', 0.5}]
desc nulls_last=True:[{'a', 0.9}, {'c', 0.5}, {'b', None}, {'d', None}]
empty df cols: []
```

**P3 — quotes 分片上限静默截断（B3b-A2）**

```
requested=250 fetched= 200 returned_quotes= 200 source= tencent
missing_example= ['600200.SH', '600201.SH', '600202.SH'] ...
```

**P4 — 质量检查路径差异（B3b-A6）**：见上文命令；输出 `API: [('daily_return','error'), ...]` / `CLI: [('year_density','warn')]`。

**P5 — 腾讯行情字段索引（`realtime._parse_tencent_batch` 正确性）**

```
now local: 2026-09-21 19:00:16
sh600519 len(f)= 88
  f[3]='1252.57' f[4]='1257.12' f[6]='25017' f[30]='20260921161437' f[31]='-4.55' f[32]='-0.36'
  f[33]='1259.95' f[34]='1250.80' f[37]='313591' f[38]='0.20' f[44]='15658.15' f[45]='15658.15'
  f[47]='1382.83' f[48]='1131.41'      # 1257.12×1.1 / ×0.9 ⇒ 涨跌停价索引正确
```

**P6 — 腾讯日线：美股只有 1 根（B3b-B8 附带）**

```
usSPY.OQ,day,,,320,qfq -> {'day': 1}          # 无论 limit/后缀/qfq 都只有 1 根
sh510300,day,,,320,qfq -> {'qfqday': 321}     # A 股正常 limit+1 根
```

**P7 — 东财 fflow 字段口径（B3b-A9）**

```
['2026-09-21,274198704.0,-80721950.0,-193476752.0,65674576.0,208524128.0']
# 大单 65,674,576 + 超大单 208,524,128 = 274,198,704 = f52 ⇒ f52 = 主力净额（不是 main_in）
```

**P8 — 未接线/死代码（静态）**

```powershell
rg -n "fetch_daily_bar_multi" backend --glob '!**/.venv/**'      # 仅 tests/test_multi_source.py
rg -n "save_announcements|save_dividends|fetch_dividends|load_dividends_asof|save_financials" backend/app backend/scripts
rg -n "text_features|sentiment|attach_text" backend/app/ml -g '*.py'   # 0 命中
rg -n "is_quarantined" backend -g '*.py'                          # 仅定义处
rg -n "fetch_daily_bar_batch" backend --glob '!**/.venv/**'       # 仅自身定义
```

**P9 — 质量路径 inspect（B3b-A6）**

```
validate_partition 命中检查项: ['check_calendar', 'check_daily_return', 'check_year_density', 'schema_drift']   # 无 check_factor_jump
scan_dataset 有 check_factor_jump: True
```

---

## 6. 需向审核方确认的信息

1. **空榜终态口径**（B3b-A1）：以 2026-09-18 派单的硬契约（空榜必须 `unavailable`）为准，还是以 2026-09-14 的裁决 + 既有用例（`ok/no_matching_signals`）为准？两者必须有一个被改掉。
2. **`data/predictions/date=*.parquet` 的 `pred_score` 空值计数**（B3b-A4 可达性）：`python -c` 读全部分区统计 `null_count()` 即可定性；若确为 0，本条降为「防御性加固」。
3. **同一 symbol 是否存在 EM 与 Sina 混合的 hfq 序列**（B3b-A5 后果 3）：需要 `source` 列目前无法区分（这正是缺陷本身），需按抓取时间/已知故障窗口人工核对。
4. **`quotes_hub` 的 200 上限是否可作为业务约束**（B3b-A2）：若产品上「自选股 ≤ 200 只」是硬约束，则应在上游（自选表写入）拒绝而非静默截断。
5. **`multi_source` / `dividends` / `financials` / `text_ingest.attach_text_features` 是「待接线」还是「应删除」**（B3b-B1/B3/B4/B7）：决定是补 sync 步骤还是清理文档与代码。