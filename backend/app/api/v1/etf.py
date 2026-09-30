"""
ETF 中心接口（AQP）。

- GET /api/v1/etf/overview      市场概览 5 卡（含前一交易日对比）
- GET /api/v1/etf/list          ETF 列表：国家/板块/类型/指数/管理公司/规模/成立日期/关键词
- GET /api/v1/etf/performance   多 ETF 累计涨跌序列（ETF表现 折线图）
- GET /api/v1/etf/scale         规模变化（柱 = 估算规模，线 = 样本数）
- GET /api/v1/etf/flow          资金净流入榜（近1日/近5日/近10日）
- GET /api/v1/etf/hot           热门 ETF TOP N（默认按成交额降序，sort=pct 按涨跌幅）

自选（我的自选ETF）由前端 localStorage 管理，与股票自选一致，无后端依赖。
"""
from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Awaitable, Callable

import orjson
from fastapi import APIRouter, Depends, Query
from loguru import logger

from ...cache.keys import (
    k_etf_detail, k_etf_overview, k_etf_overview_series, k_etf_performance,
    k_etf_scale, k_etf_snap_history,
)
from ...cache import swr
from ...cache.redis_client import RedisClient
from ...cache.swr import _spawn_rebuild, cached_or_build
from ...core.auth import require_role
from ...core.compute_pool import get_compute_pool
from ...core.errors import APIResponse, ok
from ...data import etf as E
from ...data.etf_snapshot_sanitize import SNAP_TTL_SECONDS

router = APIRouter()

# ⚠️ 持久状态键（**不是缓存**）：ETF 概览快照存档的唯一副本，删掉不可再生。
# 键名在 cache/keys.py 里单一定义（k_etf_snap_history），此处不得再硬编码；
# 缓存清理（app_settings.clear_cache）已按 PERSISTENT_KEYS 排除它。
_SNAP_KEY = k_etf_snap_history()
_SNAP_MAX = 30

# 资金流源不可用时的**统一**说明文案。
# ⚠️ 提到模块级常量是为了让 /overview（flow 子对象）与 /flow 两处**同一口径**：
# 同一数据源、同一次失败在两处给出不同答案（一处 null、一处 0.00）属于
# 诚实性口径自相矛盾，2026-09-28 已按此修复。
_FLOW_UNAVAILABLE_REASON = (
    "ETF 资金流数据源（东方财富主力净流入口径）暂不可达；"
    "暂无等价的 ETF 全市场替代源（新浪仅单只 ETF 资金流历史、"
    "腾讯不提供全市场 ETF 资金流榜），故如实置为不可用，不合成替代指标"
)

# 概览默认展示的代表 ETF（ETF表现 折线图默认序列）
DEFAULT_PERF = "510300,510500,159915,513500,512100"

# 列表排序白名单：sort 参数 -> 实际取值字段（"" = 默认 size）
_ETF_SORT_FIELDS: dict[str, str] = {
    "": "size_yi",
    "size": "size_yi",
    "amount": "amount",
    "pct": "pct",
    "code": "code",
}
_ETF_DEFAULT_SORT = "size"
_ETF_DEFAULT_DIR = "desc"
_ETF_DIRS = ("asc", "desc")
# /hot 的默认排序基准：**必须保持 amount**（成交额），改默认值会把「热门」语义
# 悄悄换成别的榜 —— 前端的间接依赖（ETF表现默认标的、自选对比池）会跟着漂移。
_ETF_DEFAULT_HOT_SORT = "amount"

# 聚合缓存以数据日隔离；SWR 影子键可在 Redis 不可用时回退进程 LRU，
# 防止瞬时外部源故障导致每个用户都触发一次冷重建。
_ETF_CACHE_TTL = 300
_ETF_STALE_WINDOW = 1800
_ETF_ENDPOINT_BUDGET_SECONDS = 4.5
_ETF_DETAIL_BLOCK_BUDGET_SECONDS = 4.0
# 后台重建锁秒数：需覆盖最坏重建耗时（冷路径实测 ~6.3s），并去重并发重建。
_ETF_REBUILD_LOCK_TTL = 30

# 启动预热器的**提前续期**阈值（秒）：剩余 TTL 低于此值才重建（对齐 market 的
# ``OVERVIEW_WARM_RENEW_THRESHOLD_SECONDS``）。推导：main._etf_overview_warmer 每 240s
# 检查一次，主键 TTL = ``_ETF_CACHE_TTL``(300s)。稳态下每轮检查时剩余 TTL ≈ 300-240
# = 60s < 120 ⇒ 每轮都会续期，且续期后仍有约 60s 余量，保证「某轮预热失败（warmer
# 只 warning）」时缓存不会进入真空期把用户打回冷路径。
_ETF_WARM_RENEW_THRESHOLD_SECONDS = 120


def _etf_data_date() -> str:
    """返回缓存维度使用的数据日，避免实时行情跨日串用。"""
    return date.today().isoformat()


def _freshness(status: str, reason: str | None = None) -> dict[str, str]:
    """统一可观察的数据新鲜度，绝不以虚构行情填补不可用数据。"""
    payload = {"status": status, "as_of": _etf_data_date()}
    if reason:
        payload["reason"] = reason
    return payload


def _mark_payload_degraded(
    payload: dict[str, Any], *, degraded_path: bool = False,
) -> None:
    """确保降级载荷带**顶层** ``status``，交由 swr 收敛为短 TTL / 不落地。

    背景（2026-09-23 修复「概览必然超时 + 降级自锁」）：``_build`` 原先在预算内
    超时/异常时**原样返回**无顶层 ``status`` 的 fallback，被 ``cached_or_build``
    当成正常结果按 300s TTL 落地 ⇒ 一次外部源抖动（冷路径实测 6.22s > 4.5s 预算，
    100% 触发）就让概览降级**自锁整整 5 分钟**。

    这里就地补齐顶层 ``status``：
      - 已显式标注 ``unavailable`` 的保持原样（swr → **不写缓存**，快速自愈）；
      - 其余标 ``degraded``（swr → **≤15s 短 TTL + 无影子键**）。
    两种情形都保证降级载荷**绝不占据 300s 主缓存**；诚实降级语义不变
    （仍是结构化空态 + reason，绝不虚构行情）。

    ⚠️ **仅可用于降级路径**（2026-09-23 QA 复核加固）：本函数是「误用即中毒」的
    助手——对**正常**（fresh）载荷调用会给它补上顶层 ``status="degraded"``，令 swr
    误判为降级并收敛成 ≤15s 短 TTL + 无影子键，**正常数据被短命化/自毁**。故正常载荷
    **绝不能**调用本函数：调用方必须显式传 ``degraded_path=True`` 声明「此刻确在降级
    分支」，否则本函数直接 ``raise`` 拒绝误用（宁可在测试期炸掉，也不让坏标注静默
    落地）。唯一合法调用点是 :func:`_cached_etf_payload` 内 ``degraded["hit"]`` 为真的
    分支。

    Args:
        payload: 待标注的载荷（应为 dict；非 dict 时安静跳过，保持历史容错行为）。
        degraded_path: 必须为 True 才执行标注；默认 False（防误用，误调即抛）。

    Raises:
        AssertionError: 未显式声明身处降级路径（``degraded_path`` 为 False）时。
    """
    if not degraded_path:
        raise AssertionError(
            "_mark_payload_degraded 仅可用于降级路径：正常载荷绝不能调用"
            "（会误标 status=degraded 并把正常数据短命化）；确在降级分支请显式传 "
            "degraded_path=True")
    if isinstance(payload, dict):
        payload.setdefault("status", "degraded")


async def _run_unbudgeted(
    build: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """无预算构建包装（即 ``_build_unbudgeted`` 那条路）：``await build()`` 不加预算。

    请求路径的 ``_cached_etf_payload._build`` 有严格交互预算
    （``_ETF_ENDPOINT_BUDGET_SECONDS`` = 4.5s，冷路径实测 6.22s **必然**超时），但
    **后台**没有这个约束：冷路径慢构建只有经本函数才能完整跑完并把真实数据灌回缓存
    （对齐 market 的 ``_build_unbudgeted`` 先例）。请求路径的 SWR 后台重建
    （``background_build``）、降级后**立即**触发的一次重建、以及启动预热
    （:func:`warm_etf_overview_cache`）**共用**此实现，避免多份逻辑分叉。

    异常**不在此吞**：交给 ``_spawn_rebuild`` / 预热器既有的 except 分支记日志，
    旧值继续服务，绝不把降级载荷写坏。
    """
    data = await build()
    data.setdefault("data_freshness", _freshness("fresh"))
    return data


def _spawn_etf_rebuild(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    ttl: int,
) -> None:
    """冷路径降级后立刻触发一次**无预算**后台重建（``rebuild:{key}`` 锁去重）。

    请求路径可以有严格预算（宁可降级也不让用户等），但后台重建没有——它是唯一能
    完成冷路径慢构建（实测 6.22s > 4.5s 预算）并把好数据灌回缓存的路径
    （对齐 datacenter/market 的 ``background_build`` 先例）。仅在本次请求**确实
    走了冷路径并降级**时触发；命中缓存 / 正常构建时不触发。
    """
    try:
        _spawn_rebuild(key, build, ttl, _ETF_STALE_WINDOW,
                       _ETF_REBUILD_LOCK_TTL, None)
    except RuntimeError:  # pragma: no cover - 无运行中事件循环（理论不可达）
        logger.debug("[etf] 无事件循环，跳过 ETF 后台重建")


async def _cached_etf_payload(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    fallback: Callable[[str], dict[str, Any]],
    *,
    ttl: int = _ETF_CACHE_TTL,
) -> dict[str, Any]:
    """用 SWR 缓存 ETF 聚合，并把冷路径限制在交互预算内。

    超时/异常返回调用方定义的结构化空态，响应仍是 HTTP 200 + 业务 code=0；
    这与已有 ETF 块的 ``status: unavailable`` 语义一致。

    降级安全（2026-09-23）：
      1. **降级不占主缓存** —— 冷路径降级载荷经 :func:`_mark_payload_degraded`
         补齐顶层 ``status``，由 swr 的 ``default_cacheable`` / ``_effective_ttl``
         收敛为短 TTL（≤15s）+ 无影子键，或直接不落地；
      2. **无预算后台重建** —— 同时提供 ``background_build=_build_unbudgeted``
         （影子键 stale 命中时的 SWR 重建不再继承请求预算）并在冷路径一旦降级时
         **立刻**再触发一次无预算重建，使真实数据在数秒内回填，而非等一段
         TTL 后再次冷启动。
    """
    degraded = {"hit": False}

    async def _build() -> dict[str, Any]:
        try:
            data = await asyncio.wait_for(build(), timeout=_ETF_ENDPOINT_BUDGET_SECONDS)
            data.setdefault("data_freshness", _freshness("fresh"))
            return data
        except TimeoutError:
            logger.warning(
                f"[etf] {key} 请求路径超时：预算 {_ETF_ENDPOINT_BUDGET_SECONDS:.1f}s "
                f"内未完成 → 结构化降级载荷（短 TTL，不自锁主缓存）")
            degraded["hit"] = True
            return fallback("数据源响应超时，已快速降级")
        except Exception as exc:  # noqa: BLE001
            degraded["hit"] = True
            return fallback(f"数据源暂不可用（{type(exc).__name__}）")

    async def _build_effective() -> dict[str, Any]:
        data = await _build()
        if degraded["hit"]:
            _mark_payload_degraded(data, degraded_path=True)
        return data

    async def _build_unbudgeted() -> dict[str, Any]:
        """后台重建专用 builder：**不加 wait_for 预算**（见 :func:`_run_unbudgeted`）。

        异常**不在此吞**：交给 ``_spawn_rebuild`` 既有的 except 分支记日志，
        旧值 / 空态继续服务，绝不把降级载荷写坏。
        """
        return await _run_unbudgeted(build)

    data = await cached_or_build(
        key, _build_effective, ttl=ttl, stale_window=_ETF_STALE_WINDOW,
        rebuild_lock_ttl=_ETF_REBUILD_LOCK_TTL,
        background_build=_build_unbudgeted,
    )
    if degraded["hit"]:
        # 冷路径已降级：主缓存不会被降级载荷占据（见 _mark_payload_degraded），
        # 这里立刻触发无预算后台重建把好数据灌回缓存。
        _spawn_etf_rebuild(key, _build_unbudgeted, ttl)
    return data


async def _run_detail_block(
    build: Callable[..., dict[str, Any]], *args: Any,
) -> dict[str, Any]:
    """在每个详情块上施加预算，单一外部源不可拖住整页。"""
    try:
        # 专用计算池（core/compute_pool.py）：预算 4.5s 而冷路径实测 6.22s
        # ⇒ 属"必然泄漏"组合；下沉后泄漏被隔离，不再挤占 /health/ready 的槽位。
        return await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(get_compute_pool(), build, *args),
            timeout=_ETF_DETAIL_BLOCK_BUDGET_SECONDS,
        )
    except TimeoutError:
        return _block_unavailable("数据源响应超时")
    except Exception:  # noqa: BLE001
        return _block_unavailable("数据源暂时不可用")


def _sort_catalog_items(items: list[dict], sort: str,
                        dir_: str) -> tuple[list[dict], str, str]:
    """ETF 列表排序：收敛白名单 + **null 恒排末尾**（asc / desc 都一样）。

    历史坑：旧实现用 ``x.get(k) or 0`` 给缺失值兜底，于是 -1（东财的"无数据"
    哨兵）与 0 会混进真实值里，升序时把 -1 排在最前、看起来像"跌幅榜第一"；
    同时 ``0`` 兜底让"无规模"的 ETF 排在"规模 0.01 亿"之前。现在一律
    ``null 恒末尾``——缺失就是缺失，不参与比较。

    Args:
        items: 目录行（原地排序）。
        sort: 排序键，白名单 ``""|size|amount|pct|code``；非法 → 默认 size。
        dir_: ``asc|desc``；非法 → ``desc``。

    Returns:
        ``(items, sort_applied, dir_applied)``：后两者是**实际生效**的值，
        响应里回显。非法 sort 连同 dir 一起回落默认 ``{size, desc}``
        （旧行为恒降序，保持向后兼容）。
    """
    applied_sort = sort if sort in _ETF_SORT_FIELDS else None
    applied_dir = dir_ if dir_ in _ETF_DIRS else _ETF_DEFAULT_DIR
    if applied_sort is None:
        applied_sort, applied_dir = _ETF_DEFAULT_SORT, _ETF_DEFAULT_DIR
    field = _ETF_SORT_FIELDS[applied_sort]

    # 第一遍：值升序 + null 恒末尾（元组首位 1 > 0）
    items.sort(key=lambda x: (x.get(field) is None, x.get(field)))
    if applied_dir == "desc":
        # 只翻转非 null 段，null 段仍留在末尾（reverse=True 会把 null 顶到最前）
        non_null = sum(1 for x in items if x.get(field) is not None)
        items[:non_null] = items[:non_null][::-1]
    return items, applied_sort, applied_dir


def _filter_catalog(**kw: Any) -> list[dict]:
    """按筛选条件过滤目录。所有条件可缺省，条件之间为 AND。"""
    items = E.build_catalog()
    country = kw.get("country")
    if country and country != "all":
        items = [x for x in items if x["country"] == country]
    board = kw.get("board")
    if board and board != "all":
        items = [x for x in items if x["board"] == board]
    etype = kw.get("etype")
    if etype and etype != "all":
        items = [x for x in items if x["type"] == etype]
    if kw.get("index"):
        idx = str(kw["index"]).lower()
        items = [x for x in items if idx in (x.get("tracking_index") or "").lower()]
    if kw.get("manager"):
        mg = str(kw["manager"]).lower()
        items = [x for x in items if mg in (x.get("manager") or "").lower()]
    if kw.get("q"):
        q = str(kw["q"]).lower()
        items = [x for x in items
                 if q in (x.get("code") or "").lower() or q in (x.get("name") or "").lower()]
    lo, hi = kw.get("min_size"), kw.get("max_size")
    if lo is not None:
        items = [x for x in items if x.get("size_yi") is not None and x["size_yi"] >= lo]
    if hi is not None:
        items = [x for x in items if x.get("size_yi") is not None and x["size_yi"] <= hi]
    d_from, d_to = kw.get("inception_from"), kw.get("inception_to")
    if d_from:
        items = [x for x in items if x.get("inception") and x["inception"] >= str(d_from)]
    if d_to:
        items = [x for x in items if x.get("inception") and x["inception"] <= str(d_to)]
    return items


def _overview_snapshot() -> dict:
    """聚合当日概览：数量 / 总市值 / 平均涨跌幅 / 资金净流入 / 成交额。

    P2-8 口径拆分：核心统计（数量/总规模/成交额/净流入）只统计
    境内有真实行情的中国 ETF；美/日/韩条目为目录补充（日韩无行情、
    美股规模为配置汇率折算值），单独在 overseas 里披露，不再混入合计。
    """
    cat = E.build_catalog()
    cn = [x for x in cat if x["country"] == "cn"]
    overseas = [x for x in cat if x["country"] != "cn"]
    pcts = [x["pct"] for x in cn if x.get("pct") is not None]
    # ⚠️ 资金流源不可达时**绝不能兜成 0**：下游 KPI 卡会把它渲染成红色
    # 「+0.00 亿 / 较昨日持平」——即把「取不到」伪装成「零净流入且与昨日持平」，
    # 是**带方向的断言**，属于用兜底值冒充真实指标（项目红线）。
    # 本端点与 /etf/flow 必须同一降级口径：/etf/flow 返回 status="unavailable" +
    # reason，此处同理经 flow 子对象如实表达。全市场 ETF 主力净流入**暂无等价
    # 替代源**（见 /etf/flow 的 docstring），故不得用成交额等指标顶替。
    try:
        flow = E.fetch_flow("1d", limit=100)
        flow_status: str = "ok"
        flow_reason: str | None = None
    except Exception as e:  # noqa: BLE001 资金流不可用时不阻塞概览
        flow = []
        flow_status = "unavailable"
        flow_reason = _FLOW_UNAVAILABLE_REASON
        logger.warning(f"[etf.overview] 资金流源不可用，net_inflow 置为 null 而非 0: {e!r}")
    net_inflow: float | None = (
        sum(f["net_inflow"] or 0 for f in flow if f.get("net_inflow"))
        if flow_status == "ok" else None
    )
    us_quoted = [x for x in overseas if x["country"] == "us" and x.get("size_yi")]
    net_inflow_yi = None if net_inflow is None else round(net_inflow / 1e8, 2)
    return {
        # 口径披露（Task B）：记录本次中国 ETF 全量目录的**实际数据源**
        # （eastmoney / sina+tencent / unknown），随快照一并存档，供跨存档对比时
        # 判断「数量差值是否可比」——数据源切换会让数量口径整体改变。
        "source": E.cn_etf_source(),
        "etf_count": len(cn),
        "total_size_yi": round(sum(x.get("size_yi") or 0 for x in cn), 2),
        "avg_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        # 顶层 net_inflow_yi 与下方 flow.net_inflow_yi **同源赋值**（同一个局部变量，
        # 不重算），仅为兼容既有消费方保留；契约以 flow 子对象为准。
        "net_inflow_yi": net_inflow_yi,
        # 与 /etf/flow 同构的资金流块：取不到时为 None + status=unavailable，
        # 前端据此显示「—」而非 0。
        "flow": {
            "status": flow_status,
            "net_inflow_yi": net_inflow_yi,
            "reason": flow_reason,
        },
        "amount_yi": round(sum(x.get("amount") or 0 for x in cn) / 1e8, 2),
        "overseas": {
            "us_count": sum(1 for x in overseas if x["country"] == "us"),
            "jp_count": sum(1 for x in overseas if x["country"] == "jp"),
            "kr_count": sum(1 for x in overseas if x["country"] == "kr"),
            # 美股规模为汇率折算值（非人民币实盘口径），日韩仅目录无行情
            "us_size_yi": round(sum(x.get("size_yi") or 0 for x in us_quoted), 2),
            "note": "美股规模为美元按配置汇率折算；日/韩为标的目录（行情不可达，未计入统计）",
        },
    }


async def append_etf_snapshot(snap: dict, *, trade_date: str | None = None) -> bool:
    """把一份概览快照追进 Redis 存档。返回是否**实际写入**（含覆盖），跳过为 ``False``。

    幂等规则（**同日已有「盘后」行才跳过**）：写入时先按
    ``should_archive_etf_snapshot()[0]`` 记录本次口径是否为盘后，并把结果落进
    该行的 ``archived_post_close`` 字段。若同日已有行且其
    ``archived_post_close is True`` ⇒ 保留首个盘后观测、跳过（返回 ``False``）；
    否则**先移除同日旧行再写入**（覆盖，返回 ``True``）。

    ⚠️ 旧存档没有 ``archived_post_close`` 字段 ⇒ 一律视为「非盘后、可覆盖」。
    这正好用于修复：盘前/周末误写到某日期的占位行（无该字段）会在当晚盘后
    被**真收盘值覆盖**，无需人工清理 Redis 存量数据。

    ⚠️ 调用方**仍应先过交易日 + 盘后双判据**（``should_archive_etf_snapshot``），
    本函数只负责幂等/覆盖。按自然日无条件归档会把周末/节假日/盘前的**重复值**
    灌进序列 —— 实测 2026-09-26（六）/09-27（日）/09-28（盘前）三天数值与
    09-25 周五收盘完全相同。
    """
    d = trade_date or date.today().isoformat()
    # 惰性 import：与 jobs/evening_routine.py 同一套判据，避免模块级循环导入。
    from ...data.kpi_series import should_archive_etf_snapshot

    archived_post_close = should_archive_etf_snapshot()[0]
    hist_raw = await RedisClient.get(_SNAP_KEY)
    history: list[dict] = orjson.loads(hist_raw) if hist_raw else []
    existing = next((h for h in history if h.get("date") == d), None)
    if existing is not None and existing.get("archived_post_close") is True:
        # 同日已有盘后口径的行 ⇒ 不覆盖（保留首个盘后观测）。
        return False
    # 覆盖路径：同日的旧行可能是盘前占位或旧格式（无 archived_post_close 字段），
    # 一律移除后写入本次观测，保证同日只有一条、且内容为本次口径。
    history = [h for h in history if h.get("date") != d]
    history.append({"date": d, **snap, "archived_post_close": archived_post_close})
    history = sorted(history, key=lambda x: x.get("date") or "")[-_SNAP_MAX:]
    # TTL 与恢复脚本共用同一常量（见 data/etf_snapshot_sanitize.py）：两处分写
    # 会让「恢复」悄悄改掉存档的存活时间。
    await RedisClient.set(_SNAP_KEY, orjson.dumps(history), ex=SNAP_TTL_SECONDS)
    return True


async def _overview_with_prev() -> dict:
    """今日概览 + 前一存档快照对比。

    对比数据来自 Redis 存档（每天首次访问时写入一份），
    因此首日运行的「较昨日」为 null（不编造）。
    """
    # 2026-09-30 全检 P1-b：统一走专用计算池（原 asyncio.to_thread 落 22 槽默认池，
    # 与 /health/ready 共用；ETF 概览需聚合全市场快照，属重计算）。
    snap = await asyncio.get_running_loop().run_in_executor(
        get_compute_pool(), _overview_snapshot)
    today = date.today().isoformat()

    hist_raw = await RedisClient.get(_SNAP_KEY)
    history: list[dict] = orjson.loads(hist_raw) if hist_raw else []
    # history 按日期升序（见下方 sorted），因此「前一存档」必须取日期最大的一条，
    # 而不是 next() 拿到的第一条 —— 后者是最早的存档，会让「较上一期」对比跨越
    # 任意多天（实测曾取到 9 天前的 09-11 而非最近的 09-20）。
    prev = max(
        (h for h in history if h.get("date") and h["date"] < today),
        key=lambda x: x["date"],
        default=None,
    )

    # 读路径的被动归档：必须与定时任务**同一套判据**（交易日 + 盘后）才写。
    #
    # 旧注释声称"读路径若也加判据，用户周末访问时 prev 会缺失、卡片'较昨日'
    # 空掉，反而更差" —— **该理由不成立**：prev 取自 max(h['date'] < today)，
    # 本次访问写入的 "今天" 行本来就不参与本次 prev 计算（周六写的那行只对周日
    # 有用），故加判据**不会**让当次"较上一期"变差。
    #
    # 反过来，不加判据的代价是**真实的数据丢失**：盘前/周末访问会把上一交易日的
    # 值写成 today 行；随后当晚盘后定时任务（jobs/evening_routine.py）发现该日期
    # 已存在而跳过 ⇒ 当日真收盘数据**永久丢失**（实测 2026-09-28 02:30 盘前访问
    # 导致 09-28 真收盘快照丢失）。故此处复用与定时任务**完全相同**的
    # should_archive_etf_snapshot（惰性 import，与 jobs/evening_routine.py 一致，
    # 避免循环导入）。
    from ...data.kpi_series import should_archive_etf_snapshot

    should_write, gate_reason = should_archive_etf_snapshot()
    if not should_write:
        logger.debug(f"[etf] 读路径跳过快照归档：{gate_reason}")
    elif not any(h.get("date") == today for h in history):
        await append_etf_snapshot(snap, trade_date=today)
        history = orjson.loads(await RedisClient.get(_SNAP_KEY) or b"[]")

    # 口径披露（Task B）：老快照可能没有 source 字段（历史存档 3 条）→ 如实标 unknown，
    # **不做任何推断**。当「前一口径 ≠ 今日口径」或任一侧未知时，ETF 数量差值来自
    # 统计口径变更（如 东财 1337 只 → 新浪+腾讯 1679 只）而非市场变化，不可直接比较；
    # 此时置 count_comparable=false 并给 comparison_note，但**保留 delta 数值**（不删数据）。
    today_source = str(snap.get("source") or "unknown")
    prev_view: dict | None = None
    count_comparable = True
    comparison_note: str | None = None
    if prev:
        prev_source = str(prev.get("source") or "unknown")
        prev_view = {**prev, "date": prev["date"], "source": prev_source}
        count_comparable = (
            prev_source != "unknown"
            and today_source != "unknown"
            and prev_source == today_source
        )
        if not count_comparable:
            comparison_note = (
                f"数据源口径不同（{prev_source} → {today_source}），"
                f"ETF 数量不可直接比较"
            )

    return {
        "today": {**snap, "date": today},
        "prev": prev_view,
        "count_comparable": count_comparable,
        "comparison_note": comparison_note,
    }


@router.get("/overview", response_model=APIResponse[dict])
async def etf_overview(
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """市场概览：按数据日缓存，冷路径至多等待五秒后返回结构化降级。"""
    def _fallback(reason: str) -> dict[str, Any]:
        unavailable = {
            "etf_count": None, "total_size_yi": None, "avg_pct": None,
            "net_inflow_yi": None, "amount_yi": None,
            "overseas": None, "date": _etf_data_date(),
            "status": "unavailable", "reason": reason,
        }
        return {"today": unavailable, "prev": None,
                "data_freshness": _freshness("degraded", reason)}

    data = await _cached_etf_payload(
        k_etf_overview(_etf_data_date()), _overview_with_prev, _fallback,
    )
    return ok(data)


async def warm_etf_overview_cache() -> bool:
    """启动预热：提前构建 ETF 概览并写入**请求路径同一** SWR 缓存键。

    背景（2026-09-23）：ETF 概览冷路径实测 6.22s > 请求预算 4.5s，**无预热**时首个
    用户请求必然在预算内超时 ⇒ 结构化降级（前端黄条）。market/overview 已有同构的
    启动预热（:func:`app.api.v1.market.warm_overview_cache`）但**未覆盖** ETF，故此处
    补齐对称的一路，由 ``WARM_OVERVIEW_ON_STARTUP`` 统一控制、后台运行、失败绝不影响
    启动。

    **命中请求路径缓存**：预热键必须是 ``k_etf_overview(_etf_data_date())``，与
    ``etf_overview`` 端点**完全同键**——否则只热了进程内 ``E.build_catalog`` 的缓存，
    请求路径仍会走冷 SWR、仍会黄条。

    **无预算**：走 :func:`_run_unbudgeted`（即 ``_build_unbudgeted`` 那条路），**不施加**
    ``_ETF_ENDPOINT_BUDGET_SECONDS``，让慢构建完整跑完再回填。

    **续期判据**（对齐 market）：键不存在（-2）或剩余 TTL < 阈值时重建，否则跳过。主 TTL
    ``_ETF_CACHE_TTL=300``、影子窗口 ``_ETF_STALE_WINDOW=1800``、预热间隔 240s ⇒ 稳态
    下每轮剩余 ≈60s < ``_ETF_WARM_RENEW_THRESHOLD_SECONDS``(120s)，每轮续期且留约 60s
    余量，缓存不进入真空期。

    Returns:
        本轮是否真正重建并回写（跳过 / 失败均返回 ``False``）。
    """
    try:
        key = k_etf_overview(_etf_data_date())
        # -2 = 不存在（含 Redis/LRU 双降级）-> 必须重建；
        # -1 = 存在但无过期（本键由 write_cache 带 TTL 写入，理论不出现）-> 视为无需续期；
        # >=0 = 剩余秒数，仅当剩余 < 阈值时提前续期。
        remaining = await RedisClient.ttl(key)
        if remaining == -1 or remaining >= _ETF_WARM_RENEW_THRESHOLD_SECONDS:
            logger.debug(
                f"[etf] warm skip {key}: 剩余 TTL={remaining}s "
                f">= 阈值 {_ETF_WARM_RENEW_THRESHOLD_SECONDS}s")
            return False
        t0 = asyncio.get_event_loop().time()
        data = await _run_unbudgeted(_overview_with_prev)
        # 主键 + 影子键双写：重启后即便主键过期，首个请求也可 stale 回旧值。
        await swr.write_cache(key, data, _ETF_CACHE_TTL, _ETF_STALE_WINDOW)
        logger.info(f"[etf] warm overview cache done in "
                    f"{asyncio.get_event_loop().time() - t0:.1f}s")
        return True
    except Exception as e:  # noqa: BLE001 预热失败不影响启动
        logger.warning(f"[etf] warm overview fail: {e!r}")
        return False


@router.get("/list", response_model=APIResponse[dict])
async def etf_list(
    country: str = Query("all", description="all/cn/us/jp/kr"),
    board: str = Query("all", description="宽基ETF/行业ETF/主题ETF/Smart Beta/跨境ETF/..."),
    etype: str = Query("all", description="股票型/债券型/商品型/货币型/跨境型"),
    index: str | None = Query(None, description="跟踪指数关键词"),
    manager: str | None = Query(None, description="管理公司关键词"),
    q: str | None = Query(None, max_length=32, description="代码/名称关键词"),
    min_size: float | None = Query(None, ge=0, description="规模下限（亿元）"),
    max_size: float | None = Query(None, ge=0, description="规模上限（亿元）"),
    inception_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    inception_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    sort: str = Query(_ETF_DEFAULT_SORT, description="''|size|amount|pct/code"),
    sort_dir: str = Query(_ETF_DEFAULT_DIR, alias="dir", description="asc|desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 列表（筛选器 + 搜索共用），返回分页结果与可选值集合。

    排序：白名单外的 ``sort`` 静默回落 ``{size, desc}``（不报错），响应回显
    ``sort_applied`` / ``dir_applied``；缺失值（规模/涨跌幅/成交额为 null）
    在升、降序下都排**末尾**，不做 0 兜底。
    """
    _filter_kwargs = dict(
        country=country, board=board, etype=etype, index=index,
        manager=manager, q=q, min_size=min_size, max_size=max_size,
        inception_from=inception_from, inception_to=inception_to,
    )
    # 2026-09-30 全检 P1-b：统一走专用计算池（原 asyncio.to_thread 落默认池）
    items = await asyncio.get_running_loop().run_in_executor(
        get_compute_pool(), lambda: _filter_catalog(**_filter_kwargs))
    items, sort_applied, dir_applied = _sort_catalog_items(items, sort, sort_dir)

    total = len(items)
    start = (page - 1) * page_size
    rows = items[start:start + page_size]

    # 2026-09-30 全检 P1-b：统一走专用计算池
    catalog_all = await asyncio.get_running_loop().run_in_executor(
        get_compute_pool(), E.build_catalog)
    options = {
        "boards": sorted({x["board"] for x in catalog_all}),
        "types": sorted({x["type"] for x in catalog_all}),
        "indexes": sorted({x["tracking_index"] for x in catalog_all if x.get("tracking_index")}),
        "managers": sorted({x["manager"] for x in catalog_all if x.get("manager")}),
    }
    return ok({
        "total": total, "page": page, "page_size": page_size, "items": rows,
        "options": options, "sort_applied": sort_applied,
        "dir_applied": dir_applied,
    })


@router.get("/hot", response_model=APIResponse[dict])
async def etf_hot(
    limit: int = Query(5, ge=1, le=50),
    sort: str = Query(_ETF_DEFAULT_HOT_SORT, pattern=r"^(amount|pct)$",
                      description="amount|pct，缺省为 amount（历史行为）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """热门 ETF TOP N：默认按成交额降序，``sort=pct`` 时按涨跌幅降序。

    排序复用 :func:`_sort_catalog_items`（与 ``/list`` 同语义）：
      - 白名单内键才生效，``amount`` / ``pct`` 缺失（日/韩无行情）的条目在
        **升、降序下都排末尾**，绝不用 ``or 0`` 兜底（会让 -1 哨兵冒充跌幅榜首）；
      - 响应回显 ``sort_applied``，与 ``/list`` 一致。
    """
    # 2026-09-30 全检 P1-b：统一走专用计算池
    items = await asyncio.get_running_loop().run_in_executor(
        get_compute_pool(), _filter_catalog)
    items, sort_applied, _dir_applied = _sort_catalog_items(items, sort, "desc")
    total = len(items)
    rows = items[:limit]
    # 审计 R10 / P5-S12：任何 `[:N]` 必带截断三件套（此前 items[:limit] 静默截断）。
    # 前端 `api/etf.ts` 消费 `{items}`，新增键不改变既有形状。
    return ok({"items": rows, "total": total, "returned": len(rows),
               "limit": limit, "truncated": len(rows) < total,
               "sort_applied": sort_applied})


# period -> 「近 N 个交易日」。**只放能用一个整数窗口表达的 period**，
# `ytd`（日历年初至今）不在此表中，由 etf_performance 单独按日期过滤处理。
# 注意 `1d`：**前端「ETF表现」已不提供该选项**——窗口只有 1 根 K 线，
# 必然触发下方 `len(bars) < 2` ⇒ 每条序列都 unavailable ⇒ 永远空图。
# 此处仅为兼容其它既有调用方保留，勿在产品化路径上再暴露它。
_PERIOD_DAYS = {"1d": 1, "5d": 5, "1m": 22, "3m": 66, "6m": 132,
                "1y": 252, "3y": 756}
# `fetch_kline` 的日线请求根数。**实测 800 就是腾讯数据源的上限**，再调大无效：
# 2026-09-24 实测 sh510050 → limit=800 返回 800 根（2023-06-12 起）；
# limit=810/1000/2000 反而只返回 **640 根**；limit>=2400 直接
# {"msg":"param error"}。即 800 是可用**最大值**，勿改成更大值（详见
# app/data/etf.py::fetch_kline 的 docstring）。
_KLINE_LIMIT = 800
_FLOW_FIELD = {"1d": "1d", "5d": "5d", "10d": "10d"}


@router.get("/performance", response_model=APIResponse[dict])
async def etf_performance(
    symbols: str = Query(DEFAULT_PERF, max_length=400,
                         description="逗号分隔 ETF 代码，中国用 6 位、美国用字母代码"),
    metric: str = Query("pct", pattern=r"^(pct|price)$"),
    period: str = Query("1y", pattern=r"^(1d|5d|1m|3m|6m|1y|3y|ytd)$",
                        description="1d|5d|1m|3m|6m|1y|3y（近 N 交易日）"
                                    "|ytd（当年1月1日起）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """多 ETF 累计涨跌（或净值）序列，用于「ETF表现」折线图。

    period 口径：
      - ``1d/5d/1m/3m/6m/1y/3y``：取**最近 N 个交易日**窗口（``bars[-days:]``）；
      - ``ytd``：**日历口径**，只保留当年 1 月 1 日及之后的 bar ``date >= YYYY-01-01``。
        历史上这里被写成 ``_PERIOD_DAYS["ytd"] = 0``，``days=0`` 是 falsy ⇒ 走
        ``bars[-days:] if days else bars`` 的 else 分支 ⇒ **完全不截断**，
        实际返回数据源能给的 800 根（实测 ~3.3 年），标签却写「今年来」——口径谎报，已修。

    本端点**不提供「成立以来 / 全历史」口径**：腾讯数据源单次最多返回约 800 根日线
    （≈3.3 年，见 :data:`_KLINE_LIMIT`），拿不到标的历史全貌（最早的上证50ETF 上市于
    2005-02-23），提供它会变成标签与数据不符的口径谎报。

    日本 / 韩国本土 ETF 行情不可达，代码不在数据源内时该序列为空，
    前端据此显示「暂无数据」。
    """
    codes = [s.strip() for s in symbols.split(",") if s.strip()][:8]
    # `ytd` 不走「近 N 根」窗口：它按日历年初过滤（详见下方 ytd_start 注释）。
    days = None if period == "ytd" else _PERIOD_DAYS.get(period, 252)
    # 当年 1 月 1 日。年份取自 `_etf_data_date()`（而非响应里最后一根 bar 的日期）：
    # ① 该日期已用于本端点的缓存键 `k_etf_performance(_etf_data_date(), ...)`，
    #    两者同源 ⇒ 不会出现「截断点按数据算、缓存键按今天算」的跨日漂移（本项目踩过这个坑）；
    # ② 同一请求里各标的最后一根 bar 可能不同停牌日期，若按 bar 取年份会出现
    #    同一张图不同序列的截断年份不一致。
    ytd_start = f"{_etf_data_date()[:4]}-01-01" if period == "ytd" else None

    def _build() -> dict:
        catalog = {x["code"]: x for x in E.build_catalog()}
        requests: list[tuple[str, dict[str, Any], str]] = []
        for code in codes:
            info = catalog.get(code) or {}
            country = info.get("country") or ("cn" if code.isdigit() else "us")
            market = "us" if country == "us" else (
                "sh" if code.startswith(("5", "6", "9")) else "sz")
            requests.append((code, info, market))

        def _load(request: tuple[str, dict[str, Any], str]) -> list[dict]:
            code, _, market = request
            try:
                return E.fetch_kline(market, code, limit=_KLINE_LIMIT)
            except Exception:  # noqa: BLE001 单序列失败不影响其他
                return []

        # 独立 K 线请求并行，但上限为四，避免用户可控 symbols 放大外部压力。
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(requests)))) as pool:
            loaded = list(pool.map(_load, requests))

        series: list[dict] = []
        for (code, info, _), bars in zip(requests, loaded):
            if ytd_start is not None:
                # ytd：**日历口径**，只保留当年 1/1 及之后的 bar。`bars` 的 date 是
                # YYYY-MM-DD 字符串，字典序 == 时间序，可直接比较。
                # 不能用切片：`bars[-N:]` 是「最近 N 个交易日」，与「年初至今」的
                # 日历语义不等价（交易日数随日历天数变化，旧实现就是这里谎报的）。
                bars = [b for b in bars if (b.get("date") or "") >= ytd_start]
            elif days:
                bars = bars[-days:]
            if len(bars) < 2:
                series.append({"code": code, "name": info.get("name") or code,
                               "points": [], "status": "unavailable"})
                continue
            base = bars[0]["close"] or 1.0
            points = [{"date": b["date"],
                       "value": (round(((b["close"] or base) / base - 1) * 100, 2)
                                 if metric == "pct" else b["close"])}
                      for b in bars]
            series.append({"code": code, "name": info.get("name") or code,
                           "points": points, "status": "ok"})
        return {"metric": metric, "period": period, "series": series}

    codes_key = ",".join(code.upper() for code in codes)
    symbols_key = hashlib.sha256(codes_key.encode("utf-8")).hexdigest()[:16]

    async def _build_async() -> dict[str, Any]:
        # 2026-09-30 全检 P1-b：统一走专用计算池（原 asyncio.to_thread 落默认池）
        return await asyncio.get_running_loop().run_in_executor(get_compute_pool(), _build)

    def _fallback(reason: str) -> dict[str, Any]:
        return {
            "metric": metric, "period": period,
            "series": [{"code": code, "name": code, "points": [],
                        "status": "unavailable"} for code in codes],
            "data_freshness": _freshness("degraded", reason),
        }

    data = await _cached_etf_payload(
        k_etf_performance(_etf_data_date(), symbols_key, metric, period),
        _build_async, _fallback,
    )
    return ok(data)


@router.get("/scale", response_model=APIResponse[dict])
async def etf_scale(
    period: str = Query("1m", pattern=r"^(1m|3m|1y)$"),
    top_n: int = Query(10, ge=3, le=30, description="纳入规模估算的头部 ETF 数量"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 规模变化：头部 N 只按「最新份额 × 历史收盘」估算日度总规模。

    份额 = 最新流通市值 / 最新收盘价；估算口径，非基金公司披露的申赎后规模。
    数量(只) 线为当日有成交的样本数。
    """
    def _build() -> dict:
        cat = [x for x in E.build_catalog()
               if x["country"] == "cn" and x.get("size_yi") is not None]
        cat.sort(key=lambda x: x["size_yi"], reverse=True)
        sample = cat[:top_n]
        days = _PERIOD_DAYS.get(period, 22)
        def _load(e: dict[str, Any]) -> tuple[str, list[dict]]:
            market = "sh" if e["code"].startswith(("5", "6", "9")) else "sz"
            try:
                return e["code"], E.fetch_kline(market, e["code"], limit=days + 5)[-days:]
            except Exception:  # noqa: BLE001
                return e["code"], []

        # Top-N 最多 30；按四个 worker 分批，杜绝原有 N 次串行网络等待。
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(sample)))) as pool:
            series_by_code = dict(pool.map(_load, sample))

        by_date: dict[str, dict[str, float]] = {}
        for e in sample:
            bars = series_by_code.get(e["code"]) or []
            if not bars:
                continue
            # 份额(股) = 规模(元) / 最新收盘价；目录里是 size_yi(亿元) 与 price
            shares = (((e["size_yi"] * 1e8) / e["price"])
                      if (e.get("size_yi") and e.get("price")) else None)
            if not shares:
                continue
            for b in bars:
                if b["close"] is None:
                    continue
                by_date.setdefault(b["date"], {})[e["code"]] = b["close"] * shares

        dates = sorted(by_date)
        scale = [{"date": d, "value": round(sum(by_date[d].values()) / 1e8, 2),
                  "count": len(by_date[d])} for d in dates]
        return {"period": period, "sample_size": len(sample), "points": scale,
                "note": "估算口径：最新份额 × 历史收盘价，非基金公司披露规模"}

    async def _build_async() -> dict[str, Any]:
        # 2026-09-30 全检 P1-b：统一走专用计算池（原 asyncio.to_thread 落默认池）
        return await asyncio.get_running_loop().run_in_executor(get_compute_pool(), _build)

    def _fallback(reason: str) -> dict[str, Any]:
        return {
            "period": period, "sample_size": 0, "points": [],
            "note": "估算口径数据暂不可用，未返回虚构规模",
            "status": "unavailable", "reason": reason,
            "data_freshness": _freshness("degraded", reason),
        }

    data = await _cached_etf_payload(
        k_etf_scale(_etf_data_date(), period, top_n), _build_async, _fallback,
    )
    return ok(data)


@router.get("/flow", response_model=APIResponse[dict])
async def etf_flow(
    period: str = Query("1d", pattern=r"^(1d|5d|10d)$"),
    limit: int = Query(10, ge=1, le=50),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 资金净流入榜：东财主力净流入口径，按净流入降序。

    数据源不可用（如东财 push2delay / push2 域在本网络不可达）时**不抛 51000**，
    而是返回 HTTP 200 + ``status: unavailable`` + 可读 ``reason``。全市场 ETF 主力
    净流入**暂无等价替代源**（新浪仅提供单只 ETF 资金流历史、腾讯不提供全市场 ETF
    资金流榜），故如实披露原因，**绝不用成交额等指标冒充净流入**。
    """
    try:
        # 2026-09-30 全检 P1-b：统一走专用计算池（外部数据源，网络阻塞）
        data = await asyncio.get_running_loop().run_in_executor(
            get_compute_pool(), E.fetch_flow, _FLOW_FIELD.get(period, "1d"), limit)
    except Exception as exc:  # noqa: BLE001 数据源不可用 ⇒ 结构化降级，而非裸 51000
        # 与 /overview 的 flow.reason 共用同一常量，防止两处文案漂移
        reason = _FLOW_UNAVAILABLE_REASON
        logger.warning(
            f"[etf] /flow 数据源不可用（{type(exc).__name__}）→ 结构化降级（HTTP 200 信封）")
        return ok({
            "period": period, "items": [],
            "status": "unavailable", "reason": reason,
            "returned": 0, "limit": limit,
            "total": None, "truncated": False,
            "data_freshness": _freshness(
                "degraded", f"资金流数据源不可用（{type(exc).__name__}）"),
        })
    # 审计 R10 / P5-S12：截断必须披露。数据源按 `pz=max(20,limit)` 请求并在
    # `data/etf.py:232` 处 `diff[:limit]` 截断，**不回传总量** ⇒ `total` 如实置
    # None、`truncated` 取保守判据（达到上限即视为"可能仍有更多"），
    # 绝不声称"这就是全部"。前端消费 `{period, items}`，新增键不改形状。
    returned = len(data)
    return ok({"period": period, "items": data,
               "status": "ok",
               "returned": returned, "limit": limit,
               "total": None, "truncated": returned >= limit,
               "truncation_basis": "数据源按 limit 请求且不回传总量；"
                                    "returned==limit 时视为可能被截断"})


# ---------------- ETF 中心 · KPI 历史序列 ----------------
@router.get("/overview/series", response_model=APIResponse[dict])
async def etf_overview_series_endpoint(
    days: int = Query(30, ge=10, le=60, description="回溯快照天数（10~60）"),
    refresh: int = Query(0, description="1=跳过缓存重算"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 中心 KPI 卡片的历史序列。

    ⚠️ **当前各指标 ``enough`` 均为 false（前端不画）**，这是**如实**的：
    ETF 概览统计 100% 实时来自外部源、本地无落库；唯一历史是 Redis 每日快照
    存档，且是**被动累积**的（历史上还有口径断裂：东财源不可达后切到新浪+腾讯）。
    本端点按 ``source`` 过滤到与最新一条同口径的记录，如实返回真实条数。

    每日归档任务（``jobs/evening_routine.py``）带「交易日 + 盘后」双判据，
    累积到 ≥ MIN_POINTS(6) 条后 ``enough`` 自动转 true，前端零改动即可显示趋势
    —— **绝不用任何方式补齐或合成曲线**。
    """
    days = max(10, min(int(days or 30), 60))
    key = k_etf_overview_series(days)

    async def _build() -> dict:
        from ...data.kpi_series import MIN_POINTS, etf_overview_series as _series

        # ``etf_overview_series`` 内部走同步 Redis 读（``_redis_get_sync``），
        # 且 ``cached_or_build`` 只接受协程——传同步函数会在 ``await build()``
        # 处抛 ``TypeError: object dict can't be used in 'await' expression``。
        # 2026-09-30 全检 P1-b：统一走专用计算池（_series 内部同步 Redis + 重算）
        metrics = await asyncio.get_running_loop().run_in_executor(
            get_compute_pool(), lambda: _series(days=days))
        payload = {k: v.to_dict() for k, v in metrics.items()}
        usable = [k for k, m in metrics.items() if m.enough and m.comparable]
        return {
            "as_of": max((p.date for m in metrics.values() for p in m.points),
                         default=None),
            "window_days": days,
            "min_points": MIN_POINTS,
            "drawable": usable,
            "metrics": payload,
            "status": "ok" if usable else "unavailable",
            "reason": None if usable else (
                "ETF 概览指标无本地历史落库，靠每日盘后归档累积；"
                f"当前同口径存档不足 {MIN_POINTS} 天，故如实不绘制趋势"),
        }

    data = await cached_or_build(key, _build, ttl=300, refresh=bool(refresh))
    return ok(data)


# ---------------- ETF 详情页（ETF分析） ----------------

_KLINE_PERIODS = {"day": "day", "week": "week", "month": "month"}


def _market_for_code(code: str, country: str) -> str:
    """code + country -> Tencent 市场前缀。"""
    if country == "us":
        return "us"
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _block_ok(payload: dict) -> dict:
    return {"status": "ok", **payload}


def _block_degraded(payload: dict, reason: str) -> dict:
    return {"status": "degraded", "reason": reason, **payload}


def _block_unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def _build_header_block(code: str, info: dict | None) -> dict:
    """顶部信息栏：最新价 / 涨跌幅 / 成交额 / 管理费率 / 规模 / 跟踪指数。"""
    if not info:
        return _block_unavailable("未找到该 ETF")
    fee: dict = {"management": None, "custody": None, "unit": "%/年"}
    if info.get("country") == "cn":
        try:
            fee = E.fetch_etf_fee(code)
        except Exception:  # noqa: BLE001
            pass
    return _block_ok({
        "name": info.get("name"),
        "code": code,
        "country": info.get("country"),
        "price": info.get("price"),
        "pct": info.get("pct"),
        "amount": info.get("amount"),
        "size_yi": info.get("size_yi"),
        "management_fee": fee.get("management"),
        "custody_fee": fee.get("custody"),
        "tracking_index": info.get("tracking_index"),
        "manager": info.get("manager"),
        "inception": info.get("inception"),
    })


def _build_kline_block(code: str, country: str, period: str) -> dict:
    """K 线：日线 / 周线 / 月线。"""
    market = _market_for_code(code, country)
    try:
        bars = E.fetch_kline(market, code, limit=320)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"K线数据暂时不可用: {type(e).__name__}")
    if len(bars) < 2:
        return _block_unavailable("K线数据不足")
    bars = E.aggregate_kline(bars, period)
    return _block_ok({"period": period, "bars": bars})


def _build_holdings_block(code: str, country: str) -> dict:
    """前十大重仓股 + 行业配置。"""
    if country != "cn":
        return _block_unavailable("非中国境内 ETF 暂无持仓明细")
    holdings: dict | None = None
    industry: dict | None = None
    try:
        holdings = E.fetch_etf_holdings(code)
    except Exception as e:  # noqa: BLE001
        holdings = {"error": str(type(e).__name__)}
    try:
        industry = E.fetch_etf_industry(code)
    except Exception as e:  # noqa: BLE001
        industry = {"error": str(type(e).__name__)}

    if not holdings or "error" in holdings:
        return _block_degraded(
            {"holdings": holdings, "industry": industry},
            "重仓股解析失败" if (holdings and "error" in holdings) else "重仓股数据缺失",
        )
    return _block_ok({
        "holdings": holdings,
        "industry": industry,
    })


def _build_tracking_block(code: str, country: str, info: dict | None) -> dict:
    """超额收益 / 基准对比 + 跟踪误差。"""
    idx = E.resolve_tracking_index(info.get("name") if info else None,
                                   info.get("tracking_index") if info else None)
    if not idx:
        return _block_unavailable("无法识别跟踪指数")
    market, idx_code = idx
    try:
        etf_bars = E.fetch_kline(_market_for_code(code, country), code, limit=252)
        idx_bars = E.fetch_index_kline(market, idx_code, limit=252)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"基准数据暂时不可用: {type(e).__name__}")
    if len(etf_bars) < 20 or len(idx_bars) < 20:
        return _block_unavailable("基准序列不足")

    etf_map = {b["date"]: b for b in etf_bars}
    idx_map = {b["date"]: b for b in idx_bars}
    dates = sorted(set(etf_map) & set(idx_map))
    if len(dates) < 20:
        return _block_unavailable("ETF 与基准日期交集不足")

    # 累计收益率序列（从共同起始日开始）
    base_etf = etf_map[dates[0]]["close"] or 1.0
    base_idx = idx_map[dates[0]]["close"] or 1.0
    points = []
    daily_diffs: list[float] = []
    prev_e: float | None = None
    prev_i: float | None = None
    for d in dates:
        e_close = etf_map[d]["close"] or base_etf
        i_close = idx_map[d]["close"] or base_idx
        e_ret = (e_close / base_etf - 1) * 100
        i_ret = (i_close / base_idx - 1) * 100
        points.append({"date": d, "etf": round(e_ret, 2), "index": round(i_ret, 2)})
        if prev_e is not None and prev_i is not None and prev_e and prev_i:
            daily_diffs.append((e_close / prev_e - 1) - (i_close / prev_i - 1))
        prev_e, prev_i = e_close, i_close

    # 跟踪误差：日收益差的标准差年化（常用定义）
    if len(daily_diffs) >= 2:
        mean = sum(daily_diffs) / len(daily_diffs)
        variance = sum((x - mean) ** 2 for x in daily_diffs) / (len(daily_diffs) - 1)
        te = (variance ** 0.5) * (252 ** 0.5) * 100  # 转为百分比
    else:
        te = None

    # 指数名称美化
    idx_name_map: dict[str, str] = {
        "000300": "沪深300", "000905": "中证500", "000016": "上证50",
        "000852": "中证1000", "399006": "创业板指", "000688": "科创50",
    }
    benchmark_name = (info.get("tracking_index") if info else None) or idx_name_map.get(idx_code, idx_code)

    return _block_ok({
        "benchmark_code": idx_code,
        "benchmark_name": benchmark_name,
        "points": points,
        "tracking_error": round(te, 4) if te is not None else None,
    })


def _build_valuation_block(code: str, info: dict | None) -> dict:
    """估值百分位：当前 PE/PB + 历史分位（乐咕乐股）。"""
    proxy = E.fetch_etf_valuation_proxy(code)
    if not proxy:
        return _block_unavailable("无法获取估值数据")
    pe_pct = proxy.get("pe_percentile")
    pb_pct = proxy.get("pb_percentile")
    if pe_pct is None or pb_pct is None:
        return _block_degraded(
            {
                "pe_ttm": proxy.get("pe_ttm"),
                "pb": proxy.get("pb"),
                "index_code": proxy.get("index_code"),
                "index_name": proxy.get("index_name"),
                "pe_percentile": pe_pct,
                "pb_percentile": pb_pct,
            },
            "仅获取到当前 PE/PB，历史分位计算失败",
        )
    return _block_ok({
        "pe_ttm": proxy.get("pe_ttm"),
        "pb": proxy.get("pb"),
        "index_code": proxy.get("index_code"),
        "index_name": proxy.get("index_name"),
        "pe_percentile": pe_pct,
        "pb_percentile": pb_pct,
    })


def _build_flow_block(code: str, country: str) -> dict:
    """单只 ETF 资金流向。"""
    if country != "cn":
        return _block_unavailable("非中国境内 ETF 暂无资金流数据")
    try:
        items = E.fetch_etf_flow_history(code, days=60)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"资金流数据暂时不可用: {type(e).__name__}")
    if not items:
        return _block_unavailable("暂无资金流数据")
    status = "ok" if len(items) >= 5 else "degraded"
    note = None if len(items) >= 5 else "仅获取到最近 1 个交易日的主力净流入"
    return {"status": status, "items": items, "note": note}


def _build_news_block(code: str, country: str) -> dict:
    """基金公告 / 新闻动态（天天基金 F10）。"""
    if country != "cn":
        return _block_unavailable("非中国境内 ETF 暂无公告数据")
    try:
        items = E.fetch_etf_news(code, limit=12)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"公告数据暂时不可用: {type(e).__name__}")
    if not items:
        return _block_unavailable("暂无公告数据")
    return {"status": "ok", "items": items, "note": "数据源：天天基金基金公告"}


def _build_sentiment_block(news_block: dict) -> dict:
    """基于公告标题的关键词情感打分（纯函数，无 IO）。"""
    titles = [it.get("title", "") for it in ((news_block or {}).get("items") or [])]
    if not titles:
        return _block_unavailable("暂无可用于情感统计的公告")
    return {"status": "ok", **E.score_sentiment(titles)}


def _build_chain_block(holdings_block: dict) -> dict:
    """产业链归集：由持仓细分行业按关键词映射到产业链大类（纯函数，无 IO）。"""
    hb = holdings_block or {}
    if hb.get("status") != "ok":
        return _block_unavailable("暂无持仓行业数据")
    items = ((hb.get("industry") or {}).get("items")) or []
    chain = E.build_industry_chain(items)
    if not chain:
        return _block_unavailable("暂无产业链数据")
    return {"status": "ok", "items": chain, "note": "由持仓细分行业归集到产业链大类"}


@router.get("/detail/{code}", response_model=APIResponse[dict])
async def etf_detail(
    code: str,
    kline_period: str = Query("day", pattern=r"^(day|week|month)$"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 分析页统一详情接口，块级并发且每块最多等待四秒。"""
    normalized_code = code.strip().upper()

    async def _build() -> dict[str, Any]:
        try:
            catalog = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(get_compute_pool(), E.build_catalog),
                timeout=_ETF_DETAIL_BLOCK_BUDGET_SECONDS,
            )
        except TimeoutError:
            catalog = []
        except Exception:  # noqa: BLE001
            catalog = []
        info = next((x for x in catalog if x["code"] == normalized_code), None)
        country = info["country"] if info else (
            "us" if not normalized_code.isdigit() else "cn")

        blocks = await asyncio.gather(
            _run_detail_block(_build_header_block, normalized_code, info),
            _run_detail_block(_build_kline_block, normalized_code, country, kline_period),
            _run_detail_block(_build_holdings_block, normalized_code, country),
            _run_detail_block(_build_tracking_block, normalized_code, country, info),
            _run_detail_block(_build_valuation_block, normalized_code, info),
            _run_detail_block(_build_flow_block, normalized_code, country),
            _run_detail_block(_build_news_block, normalized_code, country),
        )

        keys = ["header", "kline", "holdings", "tracking", "valuation", "flow", "news"]
        result: dict[str, Any] = {}
        overall = "ok"
        for key, block in zip(keys, blocks):
            result[key] = block
            if (block.get("status") or "") in ("degraded", "unavailable"):
                overall = "degraded"

        # 派生块无 IO；上游不可用时返回明确空态而非伪造结果。
        result["sentiment"] = _build_sentiment_block(result.get("news") or {})
        result["chain"] = _build_chain_block(result.get("holdings") or {})
        return {
            "code": normalized_code,
            "name": info.get("name") if info else None,
            "country": country,
            "status": overall,
            "blocks": result,
        }

    def _fallback(reason: str) -> dict[str, Any]:
        unavailable = _block_unavailable(reason)
        blocks = {key: dict(unavailable) for key in (
            "header", "kline", "holdings", "tracking", "valuation", "flow", "news",
            "sentiment", "chain",
        )}
        return {
            "code": normalized_code, "name": None,
            "country": "us" if not normalized_code.isdigit() else "cn",
            "status": "degraded", "blocks": blocks,
            "data_freshness": _freshness("degraded", reason),
        }

    data = await _cached_etf_payload(
        k_etf_detail(_etf_data_date(), normalized_code, kline_period), _build, _fallback,
    )
    return ok(data)
