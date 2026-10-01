# ETF「热门 TOP 10」榜单联动修复方案

> ✅ **本文档的方案已于 2026-09-30 实施完毕**（提交 `a17abd9`）。
> 下文保留为**诊断与设计记录**，已实施部分以实际代码为准。实施摘要：
> - 后端 `/etf/hot` 新增 `country` / `board` / `etype`（缺省 `all`）+ 回显 `filters_applied`；
> - 前端 `etfApi.hot()` 增 `filters` 可选参，`loadBase` 传入当前筛选项并补依赖数组；
> - **折线图样本同步放开**：`perfCodes` 去掉 A 股过滤（原前提"美股拉不到 K 线"已证伪）；
> - 新增 6 条后端回归测试；`test_etf.py` **41 passed**，ruff/mypy/tsc/vite build 全通过。

**日期**：2026-09-30
**场景**：功能缺陷诊断 + 改造方案
**问题**：ETF 中心的「热门 ETF TOP 10」榜单写死全市场，切换国家/板块/Tab 时**不联动**
**涉及**：`frontend/src/pages/Etf/index.tsx` · `frontend/src/api/etf.ts` · `backend/app/api/v1/etf.py`

---

## 📌 TL;DR

- **结论：可以改，且改动很小。** 后端 `/etf/hot` **已经具备**全部过滤能力（复用 `_filter_catalog`），
  只是**忘了把参数暴露出来**；前端也**忘了传**。两侧各补几行即可。
- **根因（两处，缺一不可）**：
  1. **后端** `etf_hot()` 只声明了 `limit` / `sort` 两个参数，**没有** `country` / `board` / `etype`；
  2. **前端** `etfApi.hot(10, hotSort)` 只传 `limit`/`sort`，**国家与板块 state 根本没进请求**。
- **实测铁证**：`/etf/hot?country=us` 返回 10 条**全部 `country='cn'`**；
  `/etf/hot?board=宽基ETF` 返回的 board 分布是 `['债券型','行业ETF','货币型','跨境ETF']`
  ⇒ 参数被**静默忽略**（后端没有该形参，FastAPI 直接丢弃）。
- **对照**：同一份 `_filter_catalog` 驱动的 `/etf/list?country=us` 正常返回 16 只美 ETF；
  `/etf/list?board=宽基ETF&country=cn` 正常返回 211 只 ⇒ **过滤内核是好的，只是没接到 hot 上**。
- **工作量**：后端约 10 行，前端约 15 行，测试约 40 行。**无数据层改动、无 schema 改动。**

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 能否实现 | ✅ **可以**，且属"接线"而非"造轮子" |
| 严重度 | 🟡 功能缺陷（非阻塞，但违背用户直觉） |
| 改动面 | 后端 1 个端点 + 前端 1 个 API 方法 + 1 处调用 |
| 风险 | 🟢 低（默认值保持 `all` ⇒ 行为向后兼容） |
| 前置依赖 | **无**（`_filter_catalog` 已支持 `country`/`board`/`etype`） |

---

## 1. 现状诊断（实测证据）

### 1.1 根因定位

**后端** `backend/app/api/v1/etf.py:632-656`：

```python
@router.get("/hot", response_model=APIResponse[dict])
async def etf_hot(
    limit: int = Query(5, ge=1, le=50),
    sort: str = Query(_ETF_DEFAULT_HOT_SORT, pattern=r"^(amount|pct)$", ...),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    items = await asyncio.get_running_loop().run_in_executor(
        get_compute_pool(), _filter_catalog)          # ← 这里不传任何过滤条件！
    items, sort_applied, _dir_applied = _sort_catalog_items(items, sort, "desc")
    rows = items[:limit]
```

注意 `_filter_catalog` **本身接受 `**kw`**，且已完整实现 `country`/`board`/`etype` 过滤
（同文件 `:298-334`）。**hot 端点只是没把参数传进去**。

**前端** `frontend/src/api/etf.ts:43-45`：

```ts
hot: (limit = 5, sort: 'amount' | 'pct' = 'amount') =>
  get<{ items: EtfItem[]; sort_applied?: string }>(
    '/api/v1/etf/hot', { limit, sort }, ETF_TIMEOUT),
```

签名里**没有** country/board 的位置。

**前端调用** `frontend/src/pages/Etf/index.tsx:260`：

```ts
etfApi.overview(), etfApi.hot(10, hotSort),
```

`country` 与 `board` **两个 state 完全没进这个请求**。这也是为什么
`loadBase` 的依赖数组只有 `[hotSort]`（`:284`）—— 国家/板块变化时**根本不会重新拉榜单**。

### 1.2 实测证据

| 请求 | 返回结果 | 判定 |
|---|---|---|
| `/etf/hot?limit=10&sort=amount` | 10 条，全部 `country='cn'`（短融ETF/银华日利/科创债…） | 全市场 |
| `/etf/hot?limit=10&sort=amount&country=us` | **10 条，仍全部 `country='cn'`** | ❌ **参数被忽略** |
| `/etf/hot?limit=10&sort=amount&board=宽基ETF` | 10 条，board 分布 `['债券型','行业ETF','货币型','跨境ETF']` | ❌ **参数被忽略** |
| `/etf/list?country=us&sort=amount` | `total=16`，SPY/QQQ/IWM/DIA/VTI（全部美国） | ✅ 过滤正常 |
| `/etf/list?country=cn&board=宽基ETF` | `total=211`，科创50/创业板/A500 | ✅ 过滤正常 |

> 截图佐证：页面当前选中「**宽基ETF**」板块，但榜单第一名是 `511360 短融ETF`
> （**债券型**）—— 榜单与板块 Tab 无联动，肉眼可见。

### 1.3 下游影响（改之前必须知道）

`hot` 在本页有**两处下游消费者**（`index.tsx:224-225` 注释已声明）：

1. **「ETF表现」折线图的取样标的** —— `perfCodes` 从 `hot` 里筛 `country==='cn'` 取前 5
   （`:383-390`）。**若把国家切到「美国」，`hot` 会全变成美股 ⇒ `perfCodes` 为空 ⇒
   折线图走空态**（这是**正确**行为：后端 `/etf/performance` 拉不到美股 K 线，
   硬画会得到空图，见 `:358-364` 的既有裁决）。
2. **「我的自选ETF」的行情兜底匹配池** —— `watchRows` 用 `[...hot, ...listRes.items]`
   按代码查行情（`:439-448`）。榜单样本变小会让匹配率略降，但列表数据仍在池里，影响有限。

⇒ **改造时必须同步处理折线图样本口径**，否则切到美股会出现"图空但无解释"的困惑。

---

## 2. 推荐方案（最小改动，向后兼容）

### 2.1 后端：`/etf/hot` 增加三个可选过滤参数

```python
@router.get("/hot", response_model=APIResponse[dict])
async def etf_hot(
    limit: int = Query(5, ge=1, le=50),
    sort: str = Query(_ETF_DEFAULT_HOT_SORT, pattern=r"^(amount|pct)$", ...),
    # ── 新增：与 /list 同口径（默认 all/空 ⇒ 行为与改造前完全一致）──
    country: str = Query("all", description="all|cn|us|jp|kr"),
    board: str = Query("all", description="板块名，all=不过滤"),
    etype: str = Query("all", description="类型，all=不过滤"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """热门 ETF TOP N（可按国家/板块/类型过滤）。

    ``country``/``board``/``etype`` 复用 :func:`_filter_catalog` 的同名条件，
    与 ``/list`` 完全同语义；三者默认 ``all`` ⇒ 不传时行为与改造前逐字节一致。
    """
    kw = {"country": country, "board": board, "etype": etype}
    items = await asyncio.get_running_loop().run_in_executor(
        get_compute_pool(), lambda: _filter_catalog(**kw))
    items, sort_applied, _dir_applied = _sort_catalog_items(items, sort, "desc")
    total = len(items)
    rows = items[:limit]
    return ok({"items": rows, "total": total, "returned": len(rows),
               "limit": limit, "truncated": len(rows) < total,
               "sort_applied": sort_applied,
               # 回显过滤口径，便于前端披露与排查
               "filters_applied": kw})
```

> ⚠️ 闭包跨参数必须用 `lambda: _filter_catalog(**kw)`，**不要**写多行 lambda 直接展开
> `country=country, board=board, ...` —— 本仓 `etf.py` 已踩过这个括号错位的坑
> （见 `:599-609` 的 `_filter_kwargs` 用法）。

### 2.2 前端 API：`hot()` 增加过滤参数

```ts
hot: (limit = 5, sort: 'amount' | 'pct' = 'amount',
      filters?: { country?: string; board?: string; etype?: string }) =>
  get<{ items: EtfItem[]; sort_applied?: string; filters_applied?: Record<string, string> }>(
    '/api/v1/etf/hot',
    { limit, sort, ...(filters ?? {}) },      // 未传的键不进 query，保持原请求形状
    ETF_TIMEOUT),
```

### 2.3 前端页面：把 `country`/`board` 接进榜单

**改动点 A** —— `loadBase` 带上过滤条件，并把它们纳入依赖：

```ts
const loadBase = useCallback(async () => {
  setHotInFlight(true);
  // 「市场总览」是**聚合口径**，不该被板块过滤；只有具体板块才过滤。
  const hotFilters = {
    country,
    ...(board !== '市场总览' ? { board } : {}),
  };
  const [o, h] = await Promise.allSettled([
    etfApi.overview(), etfApi.hot(10, hotSort, hotFilters),
  ]);
  ...
}, [hotSort, country, board]);   // ← 关键：补上 country / board
```

> **为什么「市场总览」不过滤板块**：它是聚合视图，用户此时看的是"整个市场最活跃的 ETF"，
> 强行按某个板块过滤反而违背语义。`board === '市场总览'` 时不传 `board` 参数即可。
> 国家维度则**任何时候都该生效**（选"美国"就该看美国榜）。

**改动点 B** —— 榜单卡片标题随口径变化，让用户看得见联动：

```tsx
<Card title={`热门 ETF TOP 10${
  hotScopeLabel ? ` · ${hotScopeLabel}` : ''}`}
```

其中：

```ts
const hotScopeLabel = useMemo(() => {
  const c = COUNTRIES.find((x) => x.key === country)?.label;
  const parts = [
    board !== '市场总览' ? board : null,
    country !== 'all' ? c : null,
  ].filter(Boolean);
  return parts.join(' · ');   // 例：「宽基ETF · 中国」
}, [country, board]);
```

**改动点 C（重要）** —— 折线图样本口径文案要跟随，避免"图空无解释"：

现有三态文案（`:552-565`）已覆盖 loading/unavailable/ok。需在 `perfSampleNote`
里补「因国家过滤导致无 A 股样本」的分支：

```ts
const perfSampleNote = useMemo(() => {
  if (!perfCodes.length) {
    // 分清两种"没有 A 股样本"：① 榜单头部全是境外标的；② 用户主动切到了境外国家
    if (country !== 'all' && country !== 'cn') {
      return `当前为「${COUNTRIES.find((x) => x.key === country)?.label}」市场，`
           + `折线图仅展示中国 ETF 样本，故不绘制`;
    }
    return `热门榜当前排序下没有 A 股标的（本次 ${hot.length} 只均为境外标的），折线图不展示样本`;
  }
  return `样本：热门榜前 ${perfCodes.length} 只 A 股（按${hotSortApplied === 'amount' ? '成交额' : '涨跌幅'}排序）`;
}, [perfCodes, hot.length, hotSortApplied, country]);
```

---

## 3. 备选方案对比

| 方案 | 做法 | 优点 | 缺点 | 建议 |
|---|---|---|---|---|
| **A. 后端加参数**（推荐） | `/etf/hot` 暴露 `country`/`board`/`etype` | 语义清晰、可复用、可测；一次请求拿到正确数据 | 需改后端 | ✅ **首选** |
| B. 前端复用 `/list` | 榜单改调 `/etf/list?page_size=10&sort=...` | **零后端改动** | ① `/list` 返回分页信封形状不同，需适配；② 语义混淆（"榜单"和"列表"共用一个端点，将来任一侧改口径会互相牵动）；③ `/list` 超时 30s 且带全量目录，比 hot 重 | ⚠️ 仅当**无法改后端**时用 |
| C. 前端本地过滤 | 拉全量目录，前端筛完再取前 10 | 零后端改动 | ❌ **不可行**：hot 只返回 10 条，本地过滤后可能不足 10 条；要拿全量得调 `/list`（等于方案 B）| ❌ 否决 |
| D. 写死各板块榜单 | 后端预置几份榜单 | 快 | ❌ 违背本仓"不写死样本"红线；无法覆盖 9 板块 × 5 国家组合 | ❌ 否决 |

---

## 4. 测试建议

### 后端（`backend/tests/`）

```python
@pytest.mark.parametrize(("params", "expect"), [
    ({},                                                    {"country": {"cn", "us", "jp", "kr"}}),
    ({"country": "us"},                                     {"country": {"us"}}),
    ({"country": "cn", "board": "宽基ETF"},                   {"country": {"cn"}, "board": {"宽基ETF"}}),
    ({"country": "jp"},                                     {"country": {"jp"}}),
])
def test_hot_respects_filters(client, auth_headers, params, expect):
    """hot 必须真正按 country/board 过滤（改造前 country=us 返回的仍全是 cn）。"""
    r = client.get("/api/v1/etf/hot", params={"limit": 10, "sort": "amount", **params},
                   headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()["data"]["items"]
    for key, allowed in expect.items():
        assert {x[key] for x in rows} <= allowed, f"{key} 过滤失效"
    assert len(rows) <= 10


def test_hot_default_is_backward_compatible(client, auth_headers):
    """不传过滤参数时，结果必须与改造前一致（全市场口径）。"""
    rows = client.get("/api/v1/etf/hot", params={"limit": 10},
                      headers=auth_headers).json()["data"]["items"]
    assert len(rows) == 10            # 全市场样本充足
    assert rows[0]["code"] == "511360"  # 改造前实测的第 1 名（回归锚点）


def test_hot_returns_empty_for_small_universe(client, auth_headers):
    """小样本市场（如日本 8 只）按 limit=10 请求时，truncated 必须为 False 且不补齐。"""
    d = client.get("/api/v1/etf/hot", params={"limit": 10, "country": "jp"},
                   headers=auth_headers).json()["data"]
    assert d["truncated"] is False
    assert len(d["items"]) <= 10
```

### 前端（人工/截图）

| 场景 | 期望 |
|---|---|
| 选「美国」 | 榜单出现 SPY/QQQ/IWM；折线图空态 + 文案解释"仅展示中国样本" |
| 选「中国」+ 板块「宽基ETF」 | 榜单同为宽基（科创50/创业板/A500），标题显示「宽基ETF · 中国」 |
| 选「市场总览」 | 榜单恢复全市场（与改造前一致） |
| 点「涨跌幅」Tab | 在**当前过滤口径内**按涨跌幅排序（不是全市场） |

---

## ⚠️ 待裁决 / 需要你确认

1. **「市场总览」板块是否需要过滤？**
   本方案默认**不过滤**（聚合语义）。若你希望它也有明确口径，可改为"中国市场全部"。
2. **切到境外市场时折线图空置，是否可接受？**
   技术上是**正确的**（后端拿不到美股 K 线，硬画是假数据）。若希望有图，
   需要额外开发"境外 K 线数据源"，**属独立课题，不在本次范围**。
3. **资金流向 / 规模变化两张卡是否也要联动？**
   本次未包含。它们当前同样是全市场口径，若要一致体验需另开一轮。

---

## 附：一句话总结

> **不是"要造过滤能力"，而是"过滤能力早就有了、只是没接到榜单上"。**
> 后端 `/etf/hot` 少声明 3 个参数、前端少传 2 个 state —— 这就是全部问题。
