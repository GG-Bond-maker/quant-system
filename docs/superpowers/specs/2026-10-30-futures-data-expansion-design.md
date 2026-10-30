# 期货品种分层扩容与全历史数据治理设计

> 状态：待用户批准
>
> 日期：2026-10-30
>
> 选定方案：方案 C，核心池 + 可配置扩展池

## 1. 背景

当前项目只维护 14 个国内期货品种，历史缓存共 43,508 行。质量检查和清理脚本已经落地并执行，但品种目录、上市日期、数据源、缓存覆盖范围和质量状态仍分散在代码与缓存文件中。

只读探测确认 Sina 主力连续目录当前列出 82 个品种；项目现有 `T0` 不在该列表中，但主连续接口可正常返回，因此统一目录共 83 个品种。接口可用不等于适合进入默认研究和回测：

- 部分品种已经停止更新或成交极低。
- 不同品种的数据源实际首日差异很大。
- 金融期货的主连续数据只从 2017 年开始，无法代表实际上市历史。
- 原始数据存在非法 OHLC、零成交、零持仓和换月跳空，需要逐品种治理。

因此本次改造同时解决“扩容”和“数据治理”，而不是简单把 83 个代码加入 `FUTURES_SPECS`。

## 2. 历史数据口径

历史范围以数据源实际首日为起点，以最新有效交易日为终点。

- 不统一截断到 2008 年。
- 不要求数据源从交易所上市日开始。
- 不使用上市日期截断数据。
- `source_first_date` 取数据源按日期排序后的第一条原始记录。
- `cache_first_date` 取质量清理和可选截断后，本地缓存中的第一条记录。
- `effective_start` 等于 `cache_first_date`。
- `listing_date` 仅作为目录元数据和质量诊断信息保留。
- 如果 `source_first_date` 早于 `listing_date`，标记 `listing_date_mismatch` 并进入人工审计，不自动删除或截断。
- 不存在 `source_first_date` 的品种不得启用交易功能。

## 3. 目标

1. 默认研究池从 14 个扩展到 40 个核心品种。
2. 将剩余 43 个 Sina 可用品种登记为扩展池，默认不进入回测。
3. 建立唯一的期货品种目录，统一维护代码、名称、交易所、类别、上市日期、层级和交易规格。
4. 保留 Sina 能为每个品种提供的全部历史，从实际首日直到最新交易日。
5. 首次全量同步后使用本地缓存和 manifest，后续同步按品种更新并保留可追溯元数据。
6. 历史行情禁止使用随机合成数据兜底。
7. 未知品种不得回退到 `IF` 的合约规格或行情。
8. 前后端品种列表由统一目录生成，移除重复硬编码。
9. 扩容后执行质量清理、数据同步和回归测试，并产出可复现报告。
10. 所有行情和分析端点区分真实数据、派生数据与不可用状态，不返回冒充真实的虚构值。

## 4. 非目标

- 本次不接入付费数据源，也不补造 Sina 无法提供的早期历史。
- 本次不要求数据覆盖到交易所上市日；只记录并使用数据源实际首日。
- 本次不构造逐合约历史换月日历，也不把启发式连续合约升级为交易所级精确复权。
- 本次不默认开放扩展池中的全部品种用于回测。
- 本次不重写 CTA 回测模型、基差模型或套利模型。
- 本次不建立历史逐日保证金和手续费变更表；交易规格固定为实施日的交易所基准。

## 5. 反目标

- 用随机行情填充缺失历史，导致回测结果不可复现。
- 将低流动性、已退市或历史不足的品种静默加入默认池。
- 对未知代码继续使用 `IF` 的乘数、手续费和保证金。
- 将数据源缺失伪装成真实零成交、零持仓或上市前行情。
- 把启发式换月平滑结果宣称为精确连续合约。
- 为覆盖数量而牺牲数据可审计性。
- 在 COT、基差、期限结构或展期收益端点中用随机数、固定模板或隐藏的合成值填充新增品种。

## 6. 约束

- 当前免费历史数据主源为 Sina 主力连续接口：`futures_main_sina(symbol="CF0")`。
- `futures_main_sina` 虽接受 `start_date` 和 `end_date`，但实现上先下载全量再切片。
- 当前缓存目录为 `DATA_ROOT/futures_cache`。
- 当前质量工具已经支持结构校验、重复日期、低流动性极端行情隔离、备份和 quarantine。
- 当前项目工作区存在用户未提交修改，实施时不得回退或覆盖无关改动。
- 所有缓存写入必须使用原子写，清理前必须备份。

## 7. 当前实现中必须修正的风险

1. `backend/app/data/futures.py` 在无缓存且网络失败时生成 `synthetic=True` 历史行情。
2. `get_futures_kline`、回测和画像多处使用 `FUTURES_SPECS.get(prefix) or FUTURES_SPECS["IF"]`，未知品种会被当成 IF。
3. 实时快照的订阅列表和静态兜底列表与 `FUTURES_SPECS` 分别硬编码。
4. 前端品种下拉、顶栏搜索和中心页存在多份硬编码列表。
5. `calc_continuous_contract` 通过隔日开盘跳空识别换月，可能把真实涨跌停或极端行情误判为换月。
6. 当前缓存没有 manifest，无法直接回答某品种的数据源、首日、末日、质量和同步时间。

## 8. 方案选择

### 方案 A：只扩容核心 40 个

优点：范围可控、验证成本低。

缺点：未来继续增加品种仍需再次修改目录和前端。

### 方案 B：一次性启用全部 83 个

优点：覆盖最广。

缺点：低流动性、短历史和已停止更新的品种会污染默认研究和回测，规格维护成本最高。

### 方案 C：核心池 + 可配置扩展池

优点：默认研究池可控，同时保留未来扩展到 83 个的工程能力。

缺点：比方案 A 多一层目录、manifest 和质量状态设计。

### 决策

采用方案 C。

## 9. 品种分层

### 9.1 核心池：40 个

| 类别 | 代码 |
|---|---|
| 股指、国债 | `IF IC IM IH T TF TS` |
| 贵金属 | `AU AG` |
| 有色金属 | `CU AL ZN NI SN` |
| 黑色建材 | `RB I J JM HC` |
| 能源化工 | `SC TA MA FG SA PP L V RU BU` |
| 农产品 | `CF SR M Y P A C OI RM AP JD` |

核心池要求：

- 有完整交易规格。
- 默认出现在期货中心、分析页和搜索中。
- 允许进入历史查询和回测。
- 必须完成至少一次全量同步和质量审计。

### 9.2 扩展池：其余 43 个

扩展池包括：

`PB SS AO BC AD SF SM UR EG EB PG PF PX LU NR SP BR EC PR PL SH CY CJ LH CS PK B FU FB RR LG BZ RS WH JR RI LR LC SI PS PT PD OP`

扩展池要求：

- 在目录中可检索，可显示上市日期和可用状态。
- 默认不进入 `/contracts`、默认分析下拉和默认回测。
- 未配置完整交易规格前，不允许回测、套保或手续费计算。
- 只有通过质量门槛并显式启用后，才可进入交易相关功能。

## 10. 目标架构

### 10.1 领域目录

新增统一目录模块：

`backend/app/domain/futures_catalog.py`

目录分为两层：

```python
@dataclass(frozen=True)
class FuturesInstrument:
    code: str
    name: str
    exchange: str
    category: str
    sina_symbol: str
    listing_date: date | None
    tier: Literal["core", "extended"]
    status: Literal["active", "retired", "pending"]


@dataclass(frozen=True)
class FeeRule:
    fee_type: Literal["rate", "fixed"]
    fee_unit: float
    source_note: str


@dataclass(frozen=True)
class FuturesContractSpec:
    symbol_prefix: str
    name: str
    exchange: str
    category: str
    multiplier: float
    tick_size: float
    margin_rate: float
    fee_open: FeeRule
    fee_close: FeeRule
    fee_close_today: FeeRule
    spec_as_of: date
    underlying_code: str | None = None
```

为兼容旧代码，`FuturesContractSpec` 可提供只读属性，将 `fee_type`、`fee_unit` 映射到开仓费率，但交易成本计算必须能够区分开仓、平昨和平今。

交易规格口径：

- 合约乘数和最小变动价位以交易所合约文本为准。
- 保证金比例使用交易所实施日公布的最低交易保证金比例，并记录 `spec_as_of=2026-10-30`。
- 手续费使用交易所标准费率或项目已确认的保守基准，分别记录开仓、平昨和平今规则。
- 不把当前规格宣称为历史逐日规格；回测结果需注明使用固定的实施日基准。

统一导出：

- `FUTURES_CATALOG`：全部 83 个基础品种元数据。
- `FUTURES_SPECS`：当前已启用且有完整交易规格的品种，用于兼容现有调用方。
- `FUTURES_CORE`：40 个核心品种代码。
- `FUTURES_EXTENDED`：扩展池代码。

`get_futures_spec(code)` 只能返回已启用品种的规格。未启用品种返回 `None`，调用方必须显式处理，禁止回退到 IF。

### 10.2 数据缓存与 manifest

缓存继续使用：

`DATA_ROOT/futures_cache/{code}.parquet`

新增：

`DATA_ROOT/futures_cache/manifest.json`

manifest 每个品种至少记录：

```json
{
  "source": "sina_main",
  "source_symbol": "CF0",
  "fetched_at": "2026-10-30T18:00:00+08:00",
  "listing_date": "2004-06-01",
  "raw_source_first_date": "2005-01-04",
  "source_first_date": "2005-01-04",
  "source_last_date": "2026-10-30",
  "cache_first_date": "2005-01-04",
  "effective_start": "2005-01-04",
  "requested_start_date": null,
  "rows": 5308,
  "tier": "core",
  "quality_status": "ok",
  "issue_counts": {},
  "warning_counts": {},
  "checksum": "sha256:..."
}
```

`effective_start` 的定义：

```text
effective_start = cache_first_date
```

默认同步不设置人工下限，此时：

```text
cache_first_date = source_first_date
effective_start = source_first_date
```

使用 `--start-date` 进行人工截断时，`requested_start_date` 记录传入日期，`effective_start` 必须等于实际缓存首日，不能继续宣称完整源历史。如果数据源返回空数据或没有可验证日期，`effective_start` 为 `null`，品种状态为 `not_synced` 或 `failed`。

### 10.3 同步流程

新增同步脚本：

`backend/scripts/sync_futures_history.py`

支持：

- `--tier core|extended|all`
- `--symbols CF,SR,M`
- `--start-date YYYY-MM-DD`，可选的人工下限覆盖
- `--force`
- `--apply`
- `--max-workers 4`

默认不设置统一起点，直接获取数据源全部可用历史。只有显式传入 `--start-date` 时才做人工截断。

每个品种的同步顺序：

1. 读取现有缓存和 manifest。
2. 判断缓存是否在允许的新鲜度窗口内。
3. 从 Sina 拉取原始数据。
4. 标准化列名、日期、数值类型并排序。
5. 执行结构质量审计。
6. 将结构错误写入 quarantine。
7. 将清理后的数据写入临时 parquet。
8. 原子替换缓存。
9. 原子更新 manifest。

单个品种失败不得中断其他品种；失败状态写入 manifest，已有有效旧缓存保留。

### 10.4 读取流程

`get_futures_kline` 的行为调整为：

1. 只接受目录中存在的代码。
2. 优先读取有效缓存。
3. 缓存过期时尝试单品种刷新。
4. 刷新失败时允许返回旧缓存，但必须记录 `degraded` 状态。
5. 没有缓存且刷新失败时返回数据不可用错误，不生成随机行情。
6. 不指定日期时返回该品种从 `effective_start` 开始的全部可用历史。

新增可选参数：

- `start_date`
- `end_date`

保留 `days` 参数供轻量图表使用；`days` 和显式日期同时存在时，先按日期过滤，再取尾部窗口。现有前端图表必须显式传 `days`，避免把全历史误用于高频图表请求。

### 10.5 连续合约语义

本次将原始主力序列和连续调整序列明确区分：

- `main_raw`：Sina 原始主力连续数据。
- `continuous_ratio`：按当前算法调整后的连续序列。
- `roll_detection=heuristic`：没有逐合约换月日历时必须标注为启发式。

如果无法获得换月日历：

- 不修改原始缓存。
- 在 API 返回和前端展示中标注 `roll_quality=heuristic`。
- 不将启发式连续序列宣称为交易所级精确复权。

后续若接入逐合约数据，再新增 `roll_calendar` 和 `roll_detection=explicit`。

### 10.6 质量门槛

核心池必须满足：

- 清洗后无非法日期、重复日期、非正价格、非法 OHLC、负成交量或负持仓量。
- 至少 250 个交易日。
- 最新日期在 10 个自然日内，或在允许的周末、节假日窗口内。
- 最近三年零成交比例不超过 5%。
- 最近三年成交量中位数不低于 10,000 手。

启用状态规则：

- 核心品种在目录中固定为 40 个，不因单次同步失败而从目录消失。
- 核心品种同步或质量校验失败时，标记 `trade_enabled=false`、`data_status=failed`，API 和前端返回明确不可用状态。
- 本次发布验收要求 40 个核心品种全部通过质量门槛；任一核心品种失败时，数据落地任务以非零状态退出，但不能删除其目录信息。
- 本次发布不自动启用 43 个扩展品种。扩展品种保持 `trade_enabled=false`。
- 后续人工启用扩展品种时，必须补齐完整交易规格，并通过与核心品种相同的结构、行数、新鲜度和成交质量检查。

质量告警不直接删除数据：

- 零持仓
- 零成交
- 单日高波动
- 启发式换月
- `source_first_date < listing_date`

结构错误必须隔离；极端低流动性异常仅在同时满足“极端收益 + 流动性坍塌”时隔离。

### 10.7 API

数据真实性规则：

- 行情快照只返回真实上游数据或明确标记来源和时间的真实缓存，不使用硬编码价格冒充实时行情。
- COT、基差、期限结构、展期收益、跨期和跨品种分析只有在存在真实基础数据或可审计派生逻辑时才返回结果。
- 无法取得真实数据时返回 `available=false`、`reason` 和 `data_status`，不得生成随机值、固定名单或模板化持仓。
- 派生指标必须标记 `modeled=true`、输入数据首末日和计算方法；`modeled=true` 不等于真实观测数据。

新增：

`GET /api/v1/futures/instruments`

查询参数：

- `tier=core|extended|all`
- `category=index|bond|precious|metal|ferrous|energy|agri`
- `enabled=true|false`

返回每个品种的基础元数据、交易规格可用性、manifest 数据状态和质量状态。

调整：

- `/contracts` 默认只返回启用品种。
- 所有交易相关端点对未启用品种返回明确的参数错误。
- 未知品种不再使用 IF 规格。
- `/kline` 和回测端点支持 `start_date`、`end_date`。

### 10.8 前端

新增共享数据源：

- `frontend/src/api/futures.ts` 增加 `instruments()`。
- 新增 `useFuturesCatalog` hook 或等价缓存层。

替换以下硬编码列表：

- `frontend/src/pages/FuturesAnalysis/index.tsx`
- `frontend/src/pages/FuturesCenter/TermStructureCard.tsx`
- `frontend/src/pages/FuturesCenter/CotRankCard.tsx`
- `frontend/src/pages/FuturesCenter/CalendarSpreadCard.tsx`
- `frontend/src/components/Topbar.tsx`

默认只展示核心池。扩展池可通过筛选器查看，但在未启用时禁用回测、套保和交易按钮。

核心品种数据不可用时，分析页和中心页显示明确的数据状态，不回退到模型价格或随机指标。

## 11. 兼容与迁移

1. 保留 `FUTURES_SPECS` 导入路径，避免一次性修改所有调用方。
2. 将现有 14 个规格迁移到新目录，并补齐核心池新增 26 个规格。
3. 现有缓存文件继续有效；首次同步后补充 manifest。
4. 旧版缓存没有 manifest 时按 `quality_status=unknown` 处理，并触发一次审计。
5. 旧接口不返回合成数据后，前端必须展示明确的数据不可用状态。

## 12. 测试策略

后端测试必须覆盖：

- 目录包含 40 个核心品种和 43 个扩展品种。
- 代码唯一、层级合法、核心品种均有完整规格。
- 扩展品种未启用时不能回测或计算手续费。
- 未知代码不会回退到 IF。
- 无缓存且数据源失败时不生成 synthetic 数据。
- 默认同步时 manifest 的 `effective_start` 等于 `source_first_date`。
- 使用 `--start-date` 时 manifest 的 `effective_start` 等于实际缓存首日。
- 结构错误被隔离，真实高波动不被误删。
- 同步脚本单品种失败不中断其他品种。
- 核心品种质量失败时 `trade_enabled=false`，但目录仍保留。
- 分析端点无法取得真实输入时返回不可用，不返回模板化或随机值。

前端测试必须覆盖：

- 品种下拉来自 API，而不是本地硬编码。
- 扩展池未启用时交易按钮不可用。
- 未知或未同步品种显示明确错误状态。

回归验证：

- 现有期货测试。
- 质量工具测试。
- API 路由和权限测试。
- 前端期货中心与分析页测试。
- 后端完整测试集。

## 13. 实施阶段

1. 目录与规格：建立 83 个基础目录、40 个核心规格和兼容层。
2. 数据层：同步脚本、manifest、缓存读写和禁用 synthetic 兜底。
3. 质量层：扩展逐品种审计、质量状态和 quarantine。
4. API：品种目录、日期参数、启用状态和未知代码处理。
5. 前端：共享目录数据源，替换硬编码列表和禁用状态。
6. 数据落地：同步核心池，执行清理，生成 manifest 和质量报告。
7. 验证：运行回归测试、前后端构建和完整后端测试。

## 14. 验收标准

- 默认研究池为 40 个核心品种，扩展池为 43 个。
- 核心池至少有一次全量同步和一份可复现质量报告。
- 每个核心品种从 `source_first_date` 开始覆盖数据源全部可用历史。
- 所有品种返回真实首日、末日和 `effective_start`。
- 历史数据不存在 `synthetic=True`。
- 未知品种不会获得 IF 规格或行情。
- 核心品种全部通过质量门槛；失败品种保持目录信息但禁止交易。
- 行情与分析端点对不可用数据返回明确状态，不生成虚构值。
- 前端不依赖硬编码品种列表。
- 默认回测池不包含未启用扩展品种。
- 相关后端、前端和回归测试通过。
- README 和 CHANGELOG 中的期货品种数量与实现一致。

## 15. 明确留给实施者的选择

- 目录数据使用 Python 常量还是受版本控制的 JSON；必须保证类型校验和单一事实源。
- 同步并发数、重试次数和新鲜度窗口的具体默认值；必须可由 CLI 覆盖。
- manifest 使用单一文件还是每品种 sidecar；必须保证原子写入和可恢复性。
- 前端扩展池筛选器的具体交互形式；不得改变默认核心池行为。
