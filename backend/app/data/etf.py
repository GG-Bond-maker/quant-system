"""
ETF 中心数据源（AQP）。

覆盖范围：中国 / 美国 / 日本 / 韩国。
数据可达性（2026-09-23 实测）：
    - 中国 ETF 全量：东财 push2delay clist（fs=b:MK0021,b:MK0022）为主源；
      ⚠️ 该域名及备用 push2 在本网络环境 **http=000 完全不可达**，故增加
      新浪目录（Market_Center.getHQNodeData，node=etf_hq_fund）+ 腾讯市值
      （qt.gtimg.cn）兜底，见 :func:`fetch_cn_etfs`。
    - 中国/美国 ETF K 线历史：腾讯 web.ifzq.gtimg.cn（320 根日线）
    - 资金净流入：东财 clist f62(今日)/f164(5日)/f174(10日)；⚠️ 与主源同域名，
      当前不可达，且暂无等价的 ETF 全市场替代源（新浪仅提供单只 ETF 资金流历史，
      无法在交互预算内构建全市场榜单）⇒ 该块如实降级为 unavailable，不合成替代指标。
    - 美国 ETF 实时：腾讯 qt.gtimg.cn（usSPY 等批量）
    - ⚠️ 日本/韩国【本土】ETF 行情在本网络环境不可达：
      Yahoo Finance 403（被墙）、腾讯/东财不覆盖、Naver 拒绝访问。
      因此日韩两国提供【标的目录】（代码/名称/跟踪指数/分类），
      行情字段为 null，前端显示"暂无数据"；中国境内跟踪日经 225 的
      QDII ETF（如 513520）仍可正常取数，归入 cn 目录并标注 jp 敞口。

模块内列表/行情均做进程级缓存（TTL 见 _TTL），避免每次请求都打外部源。
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from typing import Any

import httpx
import pandas as pd
from loguru import logger

from ..core.errors import DataSourceUnavailable
from ..domain.a_share_rules import symbol_to_code
from .realtime import _request  # 复用 realtime 的限速 + 重试 HTTP 封装

# ---------------- 缓存 ----------------
_TTL = {
    "cn_list": 300,      # 中国 ETF 全量快照
    "kline": 3600,       # K 线历史
    "flow": 300,
}
# ETF 页面是交互路径。全量目录用单页请求，单个外部请求不可占满 12s×3 次默认重试。
# API 聚合层还会施加端点预算，二者共同保证外部源异常时快速、如实地降级。
_ETF_HTTP_TIMEOUT = 4.0
_ETF_HTTP_RETRIES = 1
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = __import__("threading").Lock()

# 最近一次中国 ETF 全量目录的**实际数据源**（口径披露用，Task B）：
#   "eastmoney"    东财 clist（主源）
#   "sina+tencent" 新浪目录（+腾讯市值增强）兜底源
#   "unknown"      尚未构建过目录
# 仅在 `_build` 真正取数时更新；命中 `_cached` 时保持上次取值（与缓存内容一致）。
_LAST_CN_SOURCE: str = "unknown"


def _cached(key: str, ttl: int, builder: Any, force: bool = False) -> Any:
    """进程级缓存：外部列表类数据按 TTL 复用，避免高频打源。

    force=True 跳过缓存读（refresh=1 强制重拉），重拉结果照常回写。
    """
    now = time.time()
    if not force:
        with _CACHE_LOCK:
            hit = _cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
    val = builder()
    with _CACHE_LOCK:
        _cache[key] = (now, val)
    return val


# ---------------- 中国 ETF 全量 ----------------
# 主源：东财 clist。2026-09-23 实测 push2delay / push2 均 http=000；
# 2026-09-30 复核确认该服务组**整组被网络层阻断**（代理+直连均 RemoteProtocolError）。
# ⇒ 切至 ``push2test``（同接口同字段，实测可达）：ETF 目录 1363 只 / 0.23s、
#   A 股 5562 只 / 0.24s、ETF 资金流榜 total=1363。旧主机保留作降级。
_EM_CLIST = "https://push2test.eastmoney.com/api/qt/clist/get"
_EM_CLIST_FALLBACK = "https://push2delay.eastmoney.com/api/qt/clist/get"
_EM_FIELDS = "f12,f14,f2,f3,f6,f20,f21"

# 兜底源 1：新浪 ETF 全量目录。实测可达；单页上限 100 条（num>100 被静默截断），
# 故按页并行抓取：出现「部分页」(0<len<100) 即真实末页；空页需重试，不直接当末页。
# 字段：trade=现价 changepercent=涨跌幅% amount=成交额(元)
#       mktcap=总市值(万元) nmc=流通市值(万元)。
_SINA_ETF_LIST = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_SINA_ETF_NODE = "etf_hq_fund"
_SINA_PAGE_SIZE = 100          # 该接口单页上限（实测 num>100 仍只返回 100 条）
_SINA_MAX_PAGES = 40           # 安全上限：40×100=4000 只，防目录异常膨胀时打爆外部源
_SINA_WORKERS = 8              # 并行翻页数（过高易触发新浪限流）
# ---- 翻页终止 / 截断防护（2026-09-23 修复静默截断）----
# 新浪限流时**整页返回空数组**（实测目录会退回 ~200 条），而旧实现把「任一页不满
# 100 条（含空页）即当末页 break」，于是被限流时静默返回残缺目录（页面看不出差别）。
_SINA_EMPTY_RETRIES = 3        # 空页重试次数（不含首次）；限流常表现为整页为空
_SINA_EMPTY_BACKOFF = 0.4      # 秒；第 n 次重试退避 = n × backoff
_SINA_EMPTY_GIVEUP = 5         # 连续 N 个「重试后仍空」的页 ⇒ 判定翻页到头（5 页≈500 个
                               # 候选位，远超瞬时抖动，又远小于全量 ~17 页）
_SINA_MIN_EXPECTED = 500       # 合理下限：中国 ETF 全量实测约 1679；低于此视为被限流 /
                               # 截断，交由调用方如实降级，绝不静默返回残缺目录
_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 兜底源 2：腾讯批量行情（规模字段）。实测单请求 600 个代码仍 <0.5s；
# 字段位 44=流通市值(亿元) 45=总市值(亿元)，与 _tencent_us_batch 同构。
_TENCENT_QT = "https://qt.gtimg.cn/q="
_TENCENT_BATCH = 300


def _scaled(v: Any, factor: float) -> float | None:
    """取值后乘以单位换算系数（如 万元->元 = 1e4）；不可解析则 None。"""
    n = _num(v)
    return None if n is None else n * factor


def _cn_tencent_symbol(code: str) -> str:
    """6 位 ETF 代码 -> 腾讯行情代码（5/6/9 开头沪市=sh，其余深市=sz）。"""
    return f"{'sh' if code.startswith(('5', '6', '9')) else 'sz'}{code}"


def _fetch_em_cn_etfs() -> list[dict]:
    """中国境内 ETF 全量（东财 clist，主源）。

    返回字段：code / name / price / pct / amount(元) / total_mv(元) / float_mv(元)
    f20=总市值 f21=流通市值；ETF 的"规模"口径取流通市值。
    """
    out: list[dict] = []
    page = 1
    # 东财 clist 支持大页；历史上每次冷启动按 100 条串行翻页，约 14 次网络
    # 调用叠加重试会让 ETF 首页和详情进入分钟级等待。限制为最多两次 2000 条请求。
    while page <= 2:
        params = {
            "pn": page, "pz": 2000, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f6",
            "fs": "b:MK0021,b:MK0022",
            "fields": _EM_FIELDS,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        data = _request(
            "GET", _EM_CLIST, params=params, retries=_ETF_HTTP_RETRIES,
            timeout=_ETF_HTTP_TIMEOUT,
        )
        d = (data or {}).get("data") or {}
        diff = d.get("diff") or []
        if not diff:
            break
        for x in diff:
            out.append({
                "code": str(x.get("f12")),
                "name": x.get("f14"),
                "price": _num(x.get("f2")),
                "pct": _num(x.get("f3")),
                "amount": _num(x.get("f6")),
                "total_mv": _num(x.get("f20")),
                "float_mv": _num(x.get("f21")),
            })
        total = int(d.get("total") or 0)
        if len(out) >= total:
            break
        page += 1
    return out


def _enrich_size_from_tencent(rows: list[dict]) -> None:
    """用腾讯批量行情的市值字段覆盖规模（原地改写 rows）。

    腾讯为交易所行情口径的流通/总市值（亿元），比新浪快照更权威；逐批请求，
    整批失败或个别代码缺失都只跳过（规模缺失时保留新浪兜底值 / 如实为 None），
    绝不向上抛 —— 规模属增强信息，不该拖垮整个目录。
    """
    for i in range(0, len(rows), _TENCENT_BATCH):
        batch = rows[i:i + _TENCENT_BATCH]
        q = ",".join(_cn_tencent_symbol(r["code"]) for r in batch)
        try:
            text = _request(
                "GET", _TENCENT_QT + q, encoding="gbk",
                retries=_ETF_HTTP_RETRIES, timeout=_ETF_HTTP_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 外部源异常只降级规模
            logger.warning(
                f"[etf] 腾讯市值批量拉取失败，保留新浪流通市值兜底: {type(exc).__name__}"
            )
            return
        by_code: dict[str, dict] = {}
        for chunk in (text or "").split(";"):
            if '="' not in chunk:
                continue
            _, body = chunk.split('="', 1)
            f = body.rstrip('";\n').split("~")
            if len(f) < 46:
                continue
            fmv, tmv = _num(f[44]), _num(f[45])   # 44=流通市值 45=总市值（亿元）
            by_code[f[2].strip()] = {
                "float_mv": fmv * 1e8 if fmv is not None else None,   # 亿元 -> 元
                "total_mv": tmv * 1e8 if tmv is not None else None,
            }
        for r in batch:
            s = by_code.get(r["code"])
            if not s:
                continue
            if s.get("float_mv") is not None:
                r["float_mv"] = s["float_mv"]
            if s.get("total_mv") is not None:
                r["total_mv"] = s["total_mv"]


def _fetch_sina_cn_etfs() -> list[dict]:
    """中国境内 ETF 全量（新浪目录 + 腾讯市值，兜底源）。

    东财不可达时启用。字段映射对齐东财契约：
    code / name / price / pct / amount(元) / total_mv(元) / float_mv(元)。
    规模优先用腾讯市值覆盖（见 :func:`_enrich_size_from_tencent`）；腾讯不可用时
    保留新浪 流通市值(nmc) 兜底；二者皆无则如实为 None，绝不用成交额冒充规模。

    翻页终止语义（2026-09-23 修复静默截断）：
    - **部分页**（``0 < len < _SINA_PAGE_SIZE``）是权威末页 ⇒ 立即停止翻页；
    - **空页**（``len == 0``）**不等于**末页 —— 新浪限流时整页会返回 ``[]``
      （实测被限流时目录退回 ~200 条）。故空页先重试 ``_SINA_EMPTY_RETRIES`` 次
      （短退避），仅当**连续** ``_SINA_EMPTY_GIVEUP`` 个「重试后仍空」的页才判定翻页
      到头；中间出现任何非空页即重置该连续计数（瞬时抖动不会被误判为末页）；
    - 最终条数若低于 ``_SINA_MIN_EXPECTED``（500，远低于全量实测 ~1679）⇒ 视为被
      限流 / 截断，抛 :class:`DataSourceUnavailable`，由 API 层如实降级为 unavailable，
      **绝不静默返回残缺目录**（既不产出错误的全量统计，也不写入口径错误的存档）。

    两源皆失败时抛 :class:`DataSourceUnavailable`（由 :func:`fetch_cn_etfs` 向上传播）。
    """
    raw: list[dict] = []
    with httpx.Client(
        timeout=_ETF_HTTP_TIMEOUT, follow_redirects=True, headers=_SINA_HEADERS,
        transport=httpx.HTTPTransport(retries=_ETF_HTTP_RETRIES),
    ) as client:

        def _page(p: int) -> list[dict]:
            resp = client.get(_SINA_ETF_LIST, params={
                "page": p, "num": _SINA_PAGE_SIZE,
                "sort": "symbol", "asc": 1, "node": _SINA_ETF_NODE,
            })
            resp.raise_for_status()
            rows = resp.json()
            return rows if isinstance(rows, list) else []

        def _page_with_retry(p: int) -> list[dict]:
            """抓取单页；空数组视为可疑（限流可能整页返回空），重试 + 短退避。

            只有真正取到非空页才返回；重试耗尽仍空则返回 ``[]``，由调用方按
            「连续空页」计数决定是否已到末尾（空页本身**不能**当末页）。
            """
            for attempt in range(_SINA_EMPTY_RETRIES + 1):
                rows = _page(p)
                if rows:
                    return rows
                if attempt < _SINA_EMPTY_RETRIES:
                    time.sleep(_SINA_EMPTY_BACKOFF * (attempt + 1))
            return []

        empty_streak = 0
        with ThreadPoolExecutor(max_workers=_SINA_WORKERS) as pool:
            start = 1
            while start <= _SINA_MAX_PAGES:
                stop = min(start + _SINA_WORKERS, _SINA_MAX_PAGES + 1)
                pages = list(pool.map(_page_with_retry, range(start, stop)))
                for rows in pages:
                    raw.extend(rows)
                # 出现「部分页」⇒ 已到真实末页，停止翻页。
                if any(0 < len(rows) < _SINA_PAGE_SIZE for rows in pages):
                    break
                # 统计「连续（重试后）仍空」的页；出现任何非空页即重置。
                for rows in pages:
                    empty_streak = 0 if rows else empty_streak + 1
                if empty_streak >= _SINA_EMPTY_GIVEUP:
                    break
                start = stop

    seen: set[str] = set()
    out: list[dict] = []
    for x in raw:
        code = str(x.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code,
            "name": x.get("name"),
            "price": _num(x.get("trade")),
            "pct": _num(x.get("changepercent")),
            "amount": _num(x.get("amount")),                 # 元
            "total_mv": _scaled(x.get("mktcap"), 1e4),       # 万元 -> 元
            "float_mv": _scaled(x.get("nmc"), 1e4),          # 万元 -> 元
        })
    if not out:
        raise DataSourceUnavailable("新浪 ETF 目录返回空数据（兜底源亦不可用）")
    if len(out) < _SINA_MIN_EXPECTED:
        raise DataSourceUnavailable(
            f"新浪 ETF 目录疑似被限流/截断：仅取到 {len(out)} 只 < 下限 "
            f"{_SINA_MIN_EXPECTED}（中国 ETF 全量实测约 1679）"
        )
    _enrich_size_from_tencent(out)
    return out


def fetch_cn_etfs() -> list[dict]:
    """中国境内 ETF 全量（东财主源，新浪+腾讯兜底源）。

    返回字段：code / name / price / pct / amount(元) / total_mv(元) / float_mv(元)。
    东财 clist 不可达时改用新浪目录（字段同构）+ 腾讯市值；两源皆失败则向上抛，
    由 API 层如实降级为 unavailable —— 不返回空目录去冒充"市场无 ETF"。
    """
    def _build() -> list[dict]:
        global _LAST_CN_SOURCE
        try:
            rows = _fetch_em_cn_etfs()
            _LAST_CN_SOURCE = "eastmoney"
            return rows
        except Exception as exc:  # noqa: BLE001 主源不可达 ⇒ 切换兜底源
            logger.warning(
                f"[etf] 东财 clist 不可达（{type(exc).__name__}），切换新浪+腾讯兜底源"
            )
        rows = _fetch_sina_cn_etfs()
        _LAST_CN_SOURCE = "sina+tencent"
        return rows

    return _cached("cn_list", _TTL["cn_list"], _build)


def cn_etf_source() -> str:
    """最近一次中国 ETF 全量目录的**实际数据源标识**（口径披露用，Task B）。

    返回 ``eastmoney`` / ``sina+tencent`` / ``unknown``，只如实反映目录实际来源，
    **不做任何推断**。供概览快照记录 ``source`` 字段，跨存档对比时判断数量是否可比。
    """
    return _LAST_CN_SOURCE


def _num(v: Any) -> float | None:
    """东财字段：'-'（停牌/无数据）转 None，其余转 float。"""
    if v is None or v == "-":
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


# ---------------- 美国 ETF 实时（腾讯批量） ----------------
def _tencent_us_batch(symbols: list[str]) -> dict[str, dict]:
    """腾讯美股批量快照：usSPY,usQQQ -> {SPY: {...}}。

    字段索引与 A 股一致：3=现价 32=涨跌幅 44=流通市值 45=总市值（亿美元口径）。
    """
    if not symbols:
        return {}
    q = ",".join(f"us{s}" for s in symbols[:50])
    text = _request(
        "GET", f"https://qt.gtimg.cn/q={q}", encoding="gbk",
        retries=_ETF_HTTP_RETRIES, timeout=_ETF_HTTP_TIMEOUT,
    )
    out: dict[str, dict] = {}
    for chunk in text.strip().split(";"):
        if '="' not in chunk:
            continue
        head, body = chunk.split('="', 1)
        code = head.strip().replace("v_us", "").rstrip('"')
        f = body.rstrip('";\n').split("~")
        if len(f) < 46:
            continue

        def _g(i: int) -> float | None:
            try:
                v = f[i].strip()
                return float(v) if v else None
            except (ValueError, IndexError):
                return None

        out[code] = {
            "name": f[1].strip(),
            "price": _g(3),
            "pct": _g(32),
            "total_mv": _g(45),      # 亿美元
            "float_mv": _g(44),
        }
    return out


# ---------------- K 线历史（腾讯，中美通用） ----------------
#: 美股交易所后缀。腾讯把交易所编码在后缀里，**写错后缀只会返回 1 根快照**
#: （HTTP 200 + 字段结构完整 ⇒ 不抛异常、不告警、图上一个孤点），极具迷惑性。
#:   .AM = NYSE Arca（绝大多数 ETF 挂这里）  .OQ = Nasdaq  .N = NYSE
_US_SUFFIXES: tuple[str, ...] = ("AM", "OQ", "N")

#: 静态后缀表（16 只 US_CATALOG 实测归属，2026-09-30）。
#: 命中此表可**零额外请求**，且可被单测静态断言；表外标的走 `qt` 自动发现。
_US_SYMBOL_MAP: dict[str, str] = {
    # Nasdaq（.OQ）
    "QQQ": "QQQ.OQ", "TLT": "TLT.OQ", "IBIT": "IBIT.OQ",
    # NYSE Arca（.AM）
    "SPY": "SPY.AM", "IWM": "IWM.AM", "DIA": "DIA.AM", "VTI": "VTI.AM",
    "EEM": "EEM.AM", "GLD": "GLD.AM", "XLF": "XLF.AM", "XLK": "XLK.AM",
    "XLV": "XLV.AM", "XLE": "XLE.AM", "ARKK": "ARKK.AM", "SOXL": "SOXL.AM",
    "HYG": "HYG.AM",
}

#: 美股日线单次可取的**硬上限**。实测（2026-09-30, usSPY.AM）：
#:   limit=2000 → 2000 根（起始 2018-10-12）；
#:   limit=2001 → **整个 data 变空**（不是"少给几根"，是返回空！）；limit>=5000 同样空。
#: 故必须硬夹紧，否则用户传入大 limit 会让该序列静默变成"暂无数据"。
#: 对照 A 股：limit=800 → 801 根；limit=1000 → 反而只回 641 根（超限**反向缩水**）。
#: 两个市场"超限行为"完全不同 ⇒ 不可用同一个上限常量。
_US_KLINE_MAX = 2000
#: A 股日线上限（与 app/api/v1/etf.py::_KLINE_LIMIT 一致，见其注释）。
_CN_KLINE_MAX = 800

#: `us_sym:<code>` → 真实标识（如 SPY.AM）。交易所归属极少变动，长 TTL 即可。
_US_SYM_TTL = 24 * 3600


def _probe_us_node(cand: str) -> dict:
    """拉 2 根日线探测某个候选标识，返回响应里的 node（失败返回空 dict）。"""
    data = _request(
        "GET",
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={cand},day,,,2,qfq",
        retries=0, timeout=_ETF_HTTP_TIMEOUT,
    )
    return ((data or {}).get("data") or {}).get(cand) or {}


def us_real_symbol(symbol: str) -> str:
    """把裸代码（``SPY``）解析成腾讯真实标识（``SPY.AM``）。

    背景：腾讯对**错误后缀**只回 1 根快照，且**不报错**，导致 13/16 只美股 ETF
    长期"看起来有数据、实际只有 1 个点"。其响应里的 ``qt`` 字段第 ``[2]`` 位
    **会回传真实标识**（请求 ``usSPY.OQ``，它答 ``SPY.AM``）⇒ 可自动发现，
    无需维护一张会过期的交易所映射表。

    解析顺序：静态表 → 逐后缀探测（优先信 ``qt`` 回传）→ 兜底 ``.OQ``（改造前行为）。
    """
    key = f"us_sym:{symbol}"
    with _CACHE_LOCK:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _US_SYM_TTL:
            return hit[1]

    real = _US_SYMBOL_MAP.get(symbol)
    if real is None:
        for suf in _US_SUFFIXES:
            cand = f"{symbol}.{suf}"
            try:
                node = _probe_us_node(cand)
            except Exception:  # noqa: BLE001 探测失败不该影响主流程
                continue
            if not node:
                continue
            # 优先信 qt 回传的真实标识
            for v in (node.get("qt") or {}).values():
                if (isinstance(v, list) and len(v) > 2
                        and isinstance(v[2], str) and "." in v[2]):
                    real = v[2]
                    break
            if real:
                break
            # qt 没给、但已能取到多根日线 ⇒ 该后缀本身可用
            days = node.get("day") or node.get("qfqday") or []
            if len(days) > 1:
                real = cand
                break
        if real is None:
            real = f"{symbol}.OQ"     # 兜底：与改造前一致，保证不退步
            logger.warning("us_real_symbol: {} 后缀解析失败，兜底 .OQ", symbol)

    with _CACHE_LOCK:
        _cache[key] = (time.time(), real)
    return real


def fetch_kline(market: str, symbol: str, limit: int = 320,
                force: bool = False) -> list[dict]:
    """腾讯日线：market ∈ {sh, sz, us}；返回 [{date, close, volume}]（升序）。

    force=True 跳过进程级 TTL 缓存（市场页 /market/index/kline 的 refresh=1）。

    **limit 上限按市场取值**（见 :data:`_US_KLINE_MAX` / :data:`_CN_KLINE_MAX`）——
    两个市场超限行为完全不同，且美股超限会**返回空**而非"少给几根"，
    故此处统一硬夹紧，绝不让超限值打到上游。

    ``us`` 会先把裸代码解析成真实交易所标识（:func:`us_real_symbol`），
    否则 13/16 只标的只回 1 根（详见该函数 docstring）。
    """
    # 硬夹紧：美股 2000 / A 股 800。见 _US_KLINE_MAX 注释（超限 → 整个响应变空）。
    cap = _US_KLINE_MAX if market == "us" else _CN_KLINE_MAX
    limit = max(1, min(int(limit), cap))

    if market == "us":
        # ⚠️ 缓存键必须用**解析后的真实标识**：否则先按 `.OQ` 请求拿到的那 1 根
        #    会被缓存污染后续请求（改造中最容易踩的坑）。
        real = us_real_symbol(symbol)
        key = f"kline:us:{real}:{limit}"
    else:
        real = symbol
        key = f"kline:{market}:{symbol}:{limit}"

    def _build() -> list[dict]:
        node_key = f"us{real}" if market == "us" else f"{market}{symbol}"
        param = f"{node_key},day,,,{limit},qfq"
        data = _request(
            "GET",
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}",
            retries=_ETF_HTTP_RETRIES, timeout=_ETF_HTTP_TIMEOUT,
        )
        node = ((data or {}).get("data") or {}).get(node_key) or {}
        rows: list[list] = []
        for k, v in node.items():
            if k.startswith("day") or k == "qfqday":
                rows = v or []
                break
        out = []
        for r in rows:
            if len(r) < 5:
                continue
            out.append({
                "date": r[0],
                "open": _num(r[1]), "close": _num(r[2]),
                "high": _num(r[3]), "low": _num(r[4]),
                "volume": _num(r[5]),
            })
        # 数据质量自检：正常标的请求 limit>1 只回 1 根，物理上不可能（除非当日新上市）。
        # 这类"静默退化"（HTTP 200 + 结构完整 + 不抛异常）曾潜伏很久，故留可观测信号。
        if market == "us" and limit > 1 and len(out) == 1:
            logger.warning(
                "fetch_kline: us{} limit={} 仅返回 1 根 —— 疑似交易所后缀错误",
                real, limit,
            )
        return out

    return _cached(key, _TTL["kline"], _build, force=force)


def em_secid(code: str) -> str:
    """6 位代码 -> 东财 secid（5/6/9 开头沪市=1，其余深市=0）。"""
    return f"{'1' if code.startswith(('5', '6', '9')) else '0'}.{code}"


def fetch_flow(period: str = "1d", limit: int = 20) -> list[dict]:
    """ETF 资金净流入榜（东财主力口径）。

    period: 1d -> f62(今日) / 5d -> f164 / 10d -> f174
    返回按净流入降序的前 limit 条。
    """
    field = {"1d": "f62", "5d": "f164", "10d": "f174"}.get(period, "f62")

    def _build() -> list[dict]:
        params = {
            "pn": 1, "pz": max(20, limit), "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": field,
            "fs": "b:MK0021,b:MK0022",
            "fields": "f12,f14,f2,f3,f6,f62,f164,f174",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        data = _request(
            "GET", _EM_CLIST, params=params, retries=_ETF_HTTP_RETRIES,
            timeout=_ETF_HTTP_TIMEOUT,
        )
        diff = ((data or {}).get("data") or {}).get("diff") or []
        out = []
        for x in diff[:limit]:
            amount = _num(x.get("f6"))
            flow = _num(x.get(field))
            out.append({
                "code": str(x.get("f12")),
                "name": x.get("f14"),
                "pct": _num(x.get("f3")),
                "amount": amount,
                "net_inflow": flow,
                # 净流入率 = 净流入 / 成交额（衡量资金推动强度）
                "inflow_ratio": (round(flow / amount * 100, 2)
                                 if flow is not None and amount else None),
            })
        return out

    return _cached(f"flow:{period}", _TTL["flow"], _build)


# ---------------- ETF 分类（基于名称的规则分类，可解释） ----------------
#: 分类判定顺序：先特殊类型，再宽基 / SmartBeta / 行业 / 主题
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("货币型", ("货币", "现金", "添益", "日利", "保证金")),
    ("债券型", ("债", "国债", "信用", "可转债", "地方债")),
    ("商品型", ("黄金", "原油", "豆粕", "白银", "商品", "有色金属期货")),
    ("跨境型", ("纳指", "标普", "日经", "恒生", "中概", "港股", "海外", "美国",
                "韩国", "德国", "法国", "亚太", "全球", "道琼斯", "东南亚", "沙特")),
]
_BROAD = ("沪深300", "中证500", "中证800", "中证1000", "中证2000", "上证50", "创业板",
          "科创50", "双创", "A50", "A500", "中证全指", "基本面50", "深证100", "北证50",
          "上证180", "中证A50", "中证A500", "国证2000")
_SMART_BETA = ("红利", "低波", "价值", "成长", "质量", "动量", "基本面", "央企", "国企",
               "自由现金流", "股息")
_INDUSTRY = ("券商", "证券", "银行", "保险", "地产", "医药", "医疗", "生物", "疫苗",
             "消费", "白酒", "酒", "食品", "饮料", "半导体", "芯片", "电子", "计算机",
             "软件", "通信", "5G", "军工", "国防", "新能源", "光伏", "锂电", "电池",
             "汽车", "智能汽车", "煤炭", "钢铁", "有色", "化工", "传媒", "游戏", "农业",
             "畜牧", "电力", "基建", "稀土", "机器人", "港口", "航运", "航空", "旅游",
             "家电", "建材", "水泥", "石油", "机械", "医药卫生")
_THEME = ("科技", "创新", "互联网", "人工智能", "AI", "碳中和", "一带一路", "国企改革",
          "云计算", "大数据", "新能车", "车联网", "数字经济", "专精特新", "央企创新",
          "生物医药", "养老", "白酒主题")


def classify_etf(name: str | None) -> str:
    """按名称规则分类：投资类型（货币/债券/商品/跨境/股票）。"""
    n = name or ""
    for cat, kws in _RULES:
        if any(k in n for k in kws):
            return cat
    return "股票型"


def classify_board(name: str | None) -> str:
    """按名称规则归类榜单 Tab：宽基 / 行业 / 主题 / Smart Beta / 跨境 / 债券 / 商品 / 货币。"""
    n = name or ""
    if any(k in n for k in ("纳指", "标普", "日经", "恒生", "中概", "港股", "海外", "美国",
                            "韩国", "德国", "法国", "亚太", "全球", "道琼斯", "东南亚")):
        return "跨境ETF"
    for cat, kws in _RULES[:3]:
        if any(k in n for k in kws):
            return f"{cat[0]}{cat[1:]}ETF" if False else cat
    if any(k in n for k in _BROAD):
        return "宽基ETF"
    if any(k in n for k in _SMART_BETA):
        return "Smart Beta"
    if any(k in n for k in _INDUSTRY):
        return "行业ETF"
    if any(k in n for k in _THEME):
        return "主题ETF"
    return "行业ETF"


# ---------------- 日本 / 韩国 / 美国 标的目录 ----------------
#: 日韩本土 ETF 行情在本网络不可达，仅提供目录（代码/名称/跟踪指数/管理公司）。
#: 管理公司 / 成立日期为公开常识性信息；规模随市场变化，留空由前端显示 —。
JP_CATALOG: list[dict] = [
    {"code": "1329.T", "name": "日经225ETF", "tracking_index": "日经225",
     "manager": "野村アセットマネジメント", "inception": "2001-07-13"},
    {"code": "1343.T", "name": "NEXT FUNDS 日经225ETF", "tracking_index": "日经225",
     "manager": "野村アセットマネジメント", "inception": "2001-07-13"},
    {"code": "1326.T", "name": "SPDR 黄金ETF", "tracking_index": "伦敦金现",
     "manager": "State Street Global Advisors", "inception": "2007-06-14"},
    {"code": "1570.T", "name": "NEXT FUNDS 日经225杠杆ETF", "tracking_index": "日经225(2x)",
     "manager": "野村アセットマネジメント", "inception": "2013-10-24"},
    {"code": "1320.T", "name": "日经300ETF", "tracking_index": "日经300",
     "manager": "日兴アセットマネジメント", "inception": "2001-07-13"},
    {"code": "1306.T", "name": "TOPIX ETF", "tracking_index": "TOPIX",
     "manager": "野村アセットマネジメント", "inception": "2001-07-13"},
    {"code": "1305.T", "name": "TOPIX Core30 ETF", "tracking_index": "TOPIX Core30",
     "manager": "野村アセットマネジメント", "inception": "2001-07-13"},
    {"code": "1345.T", "name": "MAXIS 东证REIT ETF", "tracking_index": "东证REIT指数",
     "manager": "三菱UFJ信托银行", "inception": "2015-01-08"},
]

KR_CATALOG: list[dict] = [
    {"code": "069500.KS", "name": "KODEX 200 ETF", "tracking_index": "KOSPI 200",
     "manager": "삼성자산운용(三星资产运用)", "inception": "2002-10-14"},
    {"code": "102110.KS", "name": "KODEX KOSPI ETF", "tracking_index": "KOSPI",
     "manager": "삼성자산운용(三星资产运用)", "inception": "2002-10-14"},
    {"code": "114800.KS", "name": "KODEX 200 反向ETF", "tracking_index": "KOSPI 200(-1x)",
     "manager": "삼성자산운용(三星资产运用)", "inception": "2011-08-01"},
    {"code": "251340.KS", "name": "KODEX 200 期货2x ETF", "tracking_index": "KOSPI 200(2x)",
     "manager": "삼성자산운용(三星资产运用)", "inception": "2012-07-05"},
    {"code": "005680.KS", "name": "KOSEF 200 ETF", "tracking_index": "KOSPI 200",
     "manager": "NH-Amundi자산운용", "inception": "2002-10-15"},
    {"code": "229200.KS", "name": "KODEX 코스닥150 ETF", "tracking_index": "KOSDAQ 150",
     "manager": "삼성자산운용(三星资产运用)", "inception": "2015-06-22"},
]

#: 美国 ETF 目录：规模随行情走（腾讯 f45 亿美元口径），管理公司 / 成立日期为公开事实
US_CATALOG: list[dict] = [
    {"code": "SPY", "name": "标普500ETF-SPDR", "tracking_index": "标普500",
     "manager": "State Street", "inception": "1993-01-22"},
    {"code": "QQQ", "name": "纳斯达克100ETF-Invesco", "tracking_index": "纳斯达克100",
     "manager": "Invesco", "inception": "1999-03-10"},
    {"code": "IWM", "name": "罗素2000ETF-iShares", "tracking_index": "罗素2000",
     "manager": "BlackRock", "inception": "2000-05-22"},
    {"code": "DIA", "name": "道琼斯工业ETF-SPDR", "tracking_index": "道琼斯工业平均",
     "manager": "State Street", "inception": "1998-01-20"},
    {"code": "VTI", "name": "全市场ETF-Vanguard", "tracking_index": "CRSP US Total Market",
     "manager": "Vanguard", "inception": "2001-05-24"},
    {"code": "EEM", "name": "新兴市场ETF-iShares", "tracking_index": "MSCI Emerging Markets",
     "manager": "BlackRock", "inception": "2003-04-07"},
    {"code": "GLD", "name": "黄金ETF-SPDR", "tracking_index": "伦敦金现",
     "manager": "World Gold Trust", "inception": "2004-11-18"},
    {"code": "TLT", "name": "20年+美债ETF-iShares", "tracking_index": "20年+美债指数",
     "manager": "BlackRock", "inception": "2002-07-22"},
    {"code": "XLF", "name": "金融行业ETF-SPDR", "tracking_index": "标普金融",
     "manager": "State Street", "inception": "1998-12-16"},
    {"code": "XLK", "name": "科技行业ETF-SPDR", "tracking_index": "标普科技",
     "manager": "State Street", "inception": "1998-12-16"},
    {"code": "XLV", "name": "医疗行业ETF-SPDR", "tracking_index": "标普医疗",
     "manager": "State Street", "inception": "1998-12-16"},
    {"code": "XLE", "name": "能源行业ETF-SPDR", "tracking_index": "标普能源",
     "manager": "State Street", "inception": "1998-12-16"},
    {"code": "ARKK", "name": "ARK 创新ETF", "tracking_index": "主动管理(创新主题)",
     "manager": "ARK Invest", "inception": "2014-10-31"},
    {"code": "SOXL", "name": "半导体3x杠杆ETF", "tracking_index": "ICE半导体(3x)",
     "manager": "Direxion", "inception": "2010-03-11"},
    {"code": "IBIT", "name": "比特币现货ETF", "tracking_index": "比特币现货",
     "manager": "BlackRock", "inception": "2024-01-11"},
    {"code": "HYG", "name": "高收益债ETF-iShares", "tracking_index": "高收益公司债",
     "manager": "BlackRock", "inception": "2007-04-04"},
]

#: 中国境内跟踪日经 225 的 QDII ETF（日本敞口的可用数据源）
CN_JP_EXPOSURE = ("日经", "日本")


def build_catalog() -> list[dict]:
    """合并四国目录（中国为实时全量，其余为静态目录）。"""
    out: list[dict] = []
    for e in fetch_cn_etfs():
        out.append({
            "code": e["code"], "name": e["name"], "country": "cn",
            "exchange": "SH/SZ", "type": classify_etf(e["name"]),
            "board": classify_board(e["name"]),
            "tracking_index": tracking_index_name(e["name"], None),
            "manager": None, "inception": None,
            "price": e["price"], "pct": e["pct"], "amount": e["amount"],
            "size_yi": (round(e["float_mv"] / 1e8, 2) if e["float_mv"] else None),
            "quote_status": "ok",
            # 跨境敞口：中国上市但跟踪境外指数（日经/标普/纳指等）
            "overseas": ("jp" if any(k in (e["name"] or "") for k in CN_JP_EXPOSURE) else None),
        })

    # 境外行情属于附加信息：不可达时必须降级为空字典，绝不能让整个 ETF 目录
    # 构建失败（曾因 qt.gtimg.cn 超时把 /etf/overview 打成 500）。
    try:
        us_quotes = _tencent_us_batch([e["code"] for e in US_CATALOG])
    except Exception as exc:  # noqa: BLE001 外部源任何异常都降级，不向上抛
        logger.warning(
            f"[etf] 美股行情拉取失败（跨境 ETF 行情降级为 unavailable）: {type(exc).__name__}"
        )
        us_quotes = {}
    from ..core.config import get_settings
    fx = get_settings().USD_CNY_RATE   # P2-8：汇率走配置（.env USD_CNY_RATE），不再硬编码
    for e in US_CATALOG:
        q = us_quotes.get(e["code"], {})
        mv = q.get("total_mv")
        out.append({
            "code": e["code"], "name": e["name"], "country": "us",
            "exchange": "NYSE/NASDAQ/AMEX", "type": "跨境型" if False else "股票型",
            "board": classify_board(e["name"]),
            "tracking_index": e["tracking_index"], "manager": e["manager"],
            "inception": e["inception"],
            "price": q.get("price"), "pct": q.get("pct"),
            "amount": None,
            # 腾讯美股市值为亿美元，按配置汇率折算成人民币亿元口径
            "size_yi": (round(mv * fx, 2) if mv else None),
            "size_basis": f"亿美元 × USD_CNY_RATE={fx}",
            "quote_status": "ok" if q.get("price") else "unavailable",
            "overseas": None,
        })

    for country, catalog in (("jp", JP_CATALOG), ("kr", KR_CATALOG)):
        for e in catalog:
            out.append({
                **e, "country": country, "exchange": "东证" if country == "jp" else "KRX",
                "type": "股票型", "board": "跨境ETF",
                "price": None, "pct": None, "amount": None, "size_yi": None,
                "quote_status": "unavailable", "overseas": None,
            })
    return out


def symbol_of(code: str, country: str) -> str:
    """目录 code -> 全局唯一 symbol（避免中美代码撞号）。"""
    return code if country != "cn" else symbol_to_code(code)


# ---------------- ETF 详情页专用数据源 ----------------

#: 跟踪指数名称 -> (市场前缀, 指数代码) 的常识映射
#: 用于「超额收益/基准对比」与「跟踪误差」计算。
_TRACKING_INDEX_MAP: dict[str, tuple[str, str]] = {
    "沪深300": ("sh", "000300"),
    "中证500": ("sh", "000905"),
    "中证800": ("sh", "000906"),
    "中证1000": ("sh", "000852"),
    "中证2000": ("sh", "000932"),
    "上证50": ("sh", "000016"),
    "科创50": ("sh", "000688"),
    "科创100": ("sh", "000698"),
    "创业板指": ("sz", "399006"),
    "创业板50": ("sz", "399673"),
    "深证成指": ("sz", "399001"),
    "深证100": ("sz", "399330"),
    "上证指数": ("sh", "000001"),
    "北证50": ("bj", "899050"),
    "国证2000": ("sz", "399303"),
    "红利指数": ("sh", "000015"),
    "标普500": ("us", "SPY"),       # 用 SPY 作为美股标普500可交易代理
    "纳斯达克100": ("us", "QQQ"),     # 用 QQQ 作为纳指100可交易代理
    "纳斯达克": ("us", "QQQ"),
    "罗素2000": ("us", "IWM"),
    "道琼斯": ("us", "DIA"),
}


def resolve_tracking_index(name: str | None, tracking_index: str | None) -> tuple[str, str] | None:
    """根据 ETF 名称或跟踪指数字段，解析出可对标的行情代码。

    返回 (market, code) 或 None（无法对标时）。
    """
    n = (name or "") + (tracking_index or "")
    # 先按 tracking_index 精确匹配
    if tracking_index:
        for k, v in _TRACKING_INDEX_MAP.items():
            if k in tracking_index:
                return v
    # 再按名称关键词匹配
    for k, v in _TRACKING_INDEX_MAP.items():
        if k in n:
            return v
    return None


def tracking_index_name(name: str | None, tracking_index: str | None) -> str | None:
    """返回跟踪指数的展示名称（如「沪深300」）。"""
    n = (name or "") + (tracking_index or "")
    if tracking_index:
        for k in _TRACKING_INDEX_MAP:
            if k in tracking_index:
                return k
    for k in _TRACKING_INDEX_MAP:
        if k in n:
            return k
    return None


def fetch_etf_fee(code: str) -> dict:
    """从天天基金 F10 费率页提取管理费率 / 托管费率。

    返回 {"management": 0.15, "custody": 0.05, "unit": "%/年"}
    解析失败返回 {"management": None, "custody": None, "unit": "%/年"}。
    """
    url = f"https://fundf10.eastmoney.com/jjfl_{code}.html"
    text = _request(
        "GET", url, encoding="utf-8", retries=2,
        headers={"Referer": f"https://fundf10.eastmoney.com/jjfl_{code}.html"},
    )
    mgmt = re.search(r"管理费率[\s\S]*?<td[^>]*>([\d.]+)%", text)
    custody = re.search(r"托管费率[\s\S]*?<td[^>]*>([\d.]+)%", text)
    return {
        "management": float(mgmt.group(1)) if mgmt else None,
        "custody": float(custody.group(1)) if custody else None,
        "unit": "%/年",
    }


def fetch_etf_holdings(code: str, year: str | None = None) -> dict:
    """从天天基金 F10 提取前十大重仓股（最近季度）。

    返回 {"date": "2024-12-31", "items": [{"code","name","ratio","mv"}]}
    """
    year = year or str(time.localtime().tm_year)
    url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    params = {
        "type": "jjcc", "code": code, "topline": "10000",
        "year": year, "month": "", "rt": str(time.time()),
    }
    text = _request(
        "GET", url, params=params, encoding="utf-8", retries=2,
        headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"},
    )
    m = re.search(r'var apidata=\{.*?content:"(.*?)",.*?\};', text, re.S)
    if not m:
        raise RuntimeError("无法解析基金持仓数据")

    # 响应已按 utf-8 解码，捕获内容即为 HTML（含中文，无 unicode 转义）
    html = m.group(1)
    tables = pd.read_html(StringIO(html), converters={0: str})
    if not tables:
        raise RuntimeError("持仓表格为空")

    df = tables[0]
    # 列顺序固定：0 序号, 1 股票代码, 2 股票名称, 3 最新价, 4 涨跌幅,
    # 5 相关资讯, 6 占净值比例, 7 持股数, 8 持仓市值
    if len(df.columns) < 7:
        raise RuntimeError(f"持仓表格列数异常: {len(df.columns)}")

    # 报告日期：取 HTML 中第一个 YYYY-MM-DD（最新季度在前）
    dates = re.findall(r"(\d{4}-\d{2}-\d{2})", html)
    report_date = dates[0] if dates else f"{year}-12-31"

    items = []
    for _, row in df.head(10).iterrows():
        ratio_raw = str(row.iloc[6]) if pd.notna(row.iloc[6]) else ""
        ratio = float(ratio_raw.replace("%", "")) if "%" in ratio_raw else None
        mv = row.iloc[8] if len(row) > 8 and pd.notna(row.iloc[8]) else None
        code_val = str(row.iloc[1]) if pd.notna(row.iloc[1]) else ""
        name_val = str(row.iloc[2]) if pd.notna(row.iloc[2]) else ""
        items.append({
            "code": code_val.strip(),
            "name": name_val.strip(),
            "ratio": ratio,
            # 持仓市值原始单位是 万元
            "mv_yi": round(float(mv) / 1e4, 2) if mv is not None else None,
        })
    return {"date": report_date, "items": items}


def fetch_etf_industry(code: str, year: str | None = None) -> dict:
    """AKShare 基金行业配置（取最近季度）。

    返回 {"date": "2024-12-31", "items": [{"industry","ratio","mv_yi"}]}
    市值优先用「占净值比例 × 最新基金规模」估算，避免不同数据源单位不一致。
    """
    import akshare as ak

    year = year or str(time.localtime().tm_year)
    df = ak.fund_portfolio_industry_allocation_em(symbol=code, date=year)
    if df is None or df.empty:
        raise RuntimeError("行业配置为空")
    df = df.rename(columns={
        "行业类别": "industry", "占净值比例": "ratio", "市值": "mv", "截止时间": "date",
    })
    latest = df["date"].max()
    latest_df = df[df["date"] == latest].sort_values("ratio", ascending=False)

    # 取最新规模用于推导行业市值
    cat = {x["code"]: x for x in build_catalog()}
    fund_size_yi = cat.get(code, {}).get("size_yi")

    items = []
    for _, row in latest_df.iterrows():
        ratio = float(row.get("ratio")) if pd.notna(row.get("ratio")) else None
        if fund_size_yi is not None and ratio is not None:
            mv_yi = round(ratio / 100 * fund_size_yi, 2)
        else:
            raw_mv = row.get("mv")
            mv_yi = round(float(raw_mv) / 1e8, 2) if pd.notna(raw_mv) else None
        items.append({
            "industry": str(row.get("industry") or "").strip(),
            "ratio": ratio,
            "mv_yi": mv_yi,
        })
    return {"date": str(latest), "items": items}


def fetch_index_kline(market: str, code: str, limit: int = 320,
                      force: bool = False) -> list[dict]:
    """指数 K 线：中国指数用腾讯，美股 ETF/指数用腾讯，日韩指数不可达。

    返回与 fetch_kline 同结构的日线列表（升序）。
    """
    if market in ("sh", "sz", "bj"):
        return fetch_kline(market, code, limit=limit, force=force)
    if market == "us":
        # 对美国指数，复用 fetch_kline（把 code 当作 ETF 代码）
        return fetch_kline("us", code, limit=limit, force=force)
    return []


def aggregate_kline(bars: list[dict], period: str) -> list[dict]:
    """将日线聚合为周线 / 月线。

    period: "day" | "week" | "month"
    """
    if period == "day" or not bars:
        return bars
    import pandas as pd

    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    if period == "week":
        df["g"] = df["date"].dt.isocalendar().week.astype(str) + "-" + df["date"].dt.year.astype(str)
    elif period == "month":
        df["g"] = df["date"].dt.strftime("%Y-%m")
    else:
        return bars

    out = []
    for _, g in df.groupby("g"):
        out.append({
            "date": g["date"].max().strftime("%Y-%m-%d"),
            "open": g["open"].iloc[0],
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].iloc[-1],
            "volume": g["volume"].sum(),
        })
    return out


def fetch_etf_flow_history(code: str, days: int = 60) -> list[dict]:
    """单只 ETF 近 N 日**主力净流入**（东财 `fflow/kline`）。

    口径（P1-14 修复，2026-09-21）：东财该接口的 ``f52``~``f56`` 是**净额**
    （不是"流入/流出"配对），与 :func:`app.data.realtime._fetch_em_fflow` 的
    正确解读同构：

    ============  ==================
    ``f51``       日期
    ``f52``       **主力净额**（= 大单 + 超大单）
    ``f53``       小单净额
    ``f54``       中单净额
    ``f55``       大单净额
    ``f56``       超大单净额
    ``f57``~``f61``  主力/小单/中单/大单/超大单 净占比（%）
    ``f62``/``f63``  收盘价 / 涨跌幅
    ============  ==================

    ⚠️ 修复前的错误解读把 ``f52`` 当 ``main_in``、``f53`` 当 ``main_out``，
    于是 ``net_inflow = 主力净额 − 小单净额``（因四类净额之和为 0，等价于
    ``2×主力净额 + 中单净额``）：偏差率 = ``1 + 中单/主力``，既有样本上观测到
    **+29.4%**（对应 中单/主力 = −70.6%），且**符号可错**（主力净流出而中单大幅
    净流入时，旧写法会把"流出"显示成"流入"）。判据 ``f55+f56≡f52``
    在该接口上精确成立 ⇒ ``f52`` 就是主力净额（大单 + 超大单）。

    实测 push2delay 仅返回最近 1 个交易日，因此历史数据大概率不完整。
    返回可用条目，由 API 层标记 degraded。
    """
    from .realtime import em_secid as _em_secid

    secid = _em_secid(f"{code}.SH" if code.startswith(("5", "6", "9")) else f"{code}.SZ")
    url = "https://push2test.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "lmt": str(days), "klt": "101", "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65,f66,f67",
    }
    data = _request("GET", url, params=params, retries=2)
    klines = ((data or {}).get("data") or {}).get("klines") or []
    out = []
    for k in klines:
        parts = k.split(",")
        if len(parts) < 6:
            continue
        # f52~f56 均为**净额**（元）：主力 / 小单 / 中单 / 大单 / 超大单
        main_net = _num(parts[1])
        out.append({
            "date": parts[0],
            # 主力净额直接取 f52（不再做任何加减），保留两位小数与既有契约一致
            "net_inflow": None if main_net is None else round(main_net, 2),
            # 分项净额：让"主力=大单+超大单"这一口径可被消费者/测试独立验证
            "super_large_net": _num(parts[5]),
            "large_net": _num(parts[4]),
            "medium_net": _num(parts[3]),
            "small_net": _num(parts[2]),
        })
    return out


#: 指数代码 -> 乐咕乐股指数名称（用于 PE/PB 历史分位）
_LEGULEGU_NAME_MAP: dict[str, str] = {
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
    "000852": "中证1000",
    "000009": "上证380",
    "000010": "上证180",
    "000903": "中证100",
    "000906": "中证800",
    "000015": "上证红利",
    "399673": "创业板50",
    "399324": "深证红利",
    "399330": "深证100",
}


def _legulegu_token() -> str:
    """生成乐咕乐股 API token（依赖 akshare 内部 hash_code）。"""
    from datetime import datetime

    from py_mini_racer import MiniRacer

    from akshare.stock_feature.stock_a_pe_and_pb import hash_code

    js = MiniRacer()
    js.eval(hash_code)
    return js.call("hex", datetime.now().date().isoformat()).lower()


def _fetch_legulegu_series(index_name: str, metric: str) -> list[dict]:
    """获取指数估值历史序列。metric: "pe" | "pb"。"""
    from akshare.stock_feature.stock_a_pe_and_pb import get_cookie_csrf

    token = _legulegu_token()
    url = f"https://legulegu.com/api/stockdata/index-basic-{metric}"
    symbol_map = {
        "上证50": "000016.SH", "沪深300": "000300.SH", "上证380": "000009.SH",
        "创业板50": "399673.SZ", "中证500": "000905.SH", "上证180": "000010.SH",
        "深证红利": "399324.SZ", "深证100": "399330.SZ", "中证1000": "000852.SH",
        "上证红利": "000015.SH", "中证100": "000903.SH", "中证800": "000906.SH",
    }
    params = {"token": token, "indexCode": symbol_map[index_name]}
    # get_cookie_csrf 只需要任意 legulegu 估值页获取 CSRF，与具体指数无关
    kwargs = get_cookie_csrf(url="https://legulegu.com/stockdata/sz50-ttm-lyr")
    # httpx 用 dict cookies；RequestsCookieJar 可直接 dict()
    cookies = dict(kwargs.get("cookies", {}))
    headers = kwargs.get("headers")
    r = _request("GET", url, params=params, retries=2, headers=headers, cookies=cookies)
    data = r.get("data") or []
    out = []
    for item in data:
        if metric == "pe":
            out.append({
                "date": item.get("date"),
                "pe_ttm": _num(item.get("ttmPe")),
                "pe_lyr": _num(item.get("lyrPe")),
            })
        else:
            out.append({
                "date": item.get("date"),
                "pb": _num(item.get("pb")),
            })
    return out


def _percentile(current: float | None, series: list[float | None]) -> float | None:
    """计算当前值在序列中的历史百分位（0-100）。"""
    if current is None:
        return None
    vals = [v for v in series if v is not None]
    if not vals:
        return None
    vals.sort()
    # 小于 current 的占比
    below = sum(1 for v in vals if v < current)
    return round(below / len(vals) * 100, 2)


def fetch_etf_valuation_proxy(code: str) -> dict | None:
    """ETF 估值代理：对跟踪 A 股宽基指数的 ETF，返回当前 PE/PB 与历史分位。

    数据源为乐咕乐股（legulegu），仅覆盖常见 A 股宽基指数。
    """
    cat = {x["code"]: x for x in build_catalog()}
    info = cat.get(code)
    if not info:
        return None
    idx = resolve_tracking_index(info.get("name"), info.get("tracking_index"))
    if not idx:
        return None
    market, idx_code = idx
    if market == "us":
        return None
    lg_name = _LEGULEGU_NAME_MAP.get(idx_code)
    if not lg_name:
        return None

    try:
        pe_series = _fetch_legulegu_series(lg_name, "pe")
        pb_series = _fetch_legulegu_series(lg_name, "pb")
    except Exception:  # noqa: BLE001
        return None

    if not pe_series or not pb_series:
        return None

    current_pe = pe_series[-1].get("pe_ttm")
    current_pb = pb_series[-1].get("pb")
    return {
        "index_code": idx_code,
        "index_name": lg_name,
        "pe_ttm": current_pe,
        "pb": current_pb,
        "pe_percentile": _percentile(current_pe, [p.get("pe_ttm") for p in pe_series]),
        "pb_percentile": _percentile(current_pb, [p.get("pb") for p in pb_series]),
    }


# ---------------- 基金公告 / 新闻 ----------------
_JJGG_URL = "https://api.fund.eastmoney.com/f10/JJGG"
_JJGG_TYPES = {1: "基金公告", 2: "分红送配", 3: "定期报告"}

# 注意：不要加「开放」——公募基金全称里的「开放式」会被误判为正面词
_POSITIVE = ("增长", "上涨", "新高", "分红", "超额", "领先", "利好",
             "扩募", "获批", "创新", "突破", "提升", "优异")
_NEGATIVE = ("下跌", "回撤", "风险", "亏损", "减持", "清盘", "赎回",
             "暂停", "违规", "处罚", "警示", "终止", "下调", "低于")


def fetch_etf_news(code: str, limit: int = 12) -> list[dict]:
    """基金公告（天天基金 F10 JJGG 接口）。

    实测（2026-08-30）：必须带 Referer，否则返回空；type 1/2/3 分别对应
    基金公告 / 分红送配 / 定期报告。仅中国场内基金（6 位数字代码）可用。
    """
    if not re.fullmatch(r"\d{6}", code or ""):
        return []

    rows: list[dict] = []
    for t, label in _JJGG_TYPES.items():
        try:
            data = _request(
                "GET", _JJGG_URL,
                params={"fundcode": code, "pageIndex": 1, "pageSize": 6, "type": t},
                retries=2,
                headers={"Referer": f"http://fundf10.eastmoney.com/ccmx_{code}.html"},
            )
        except Exception:  # noqa: BLE001 单类失败不影响其它类
            continue
        for r in (data or {}).get("Data") or []:
            rows.append({
                "date": (r.get("PUBLISHDATEDesc") or "")[:10],
                "title": r.get("TITLE") or "",
                "category": label,
                "url": (f"http://data.eastmoney.com/notices/detail/{r['ID']}.html"
                        if r.get("ID") else None),
            })

    rows.sort(key=lambda x: x["date"], reverse=True)
    return rows[:limit]


def score_sentiment(titles: list[str]) -> dict:
    """基于公告标题的关键词情感打分（自建词典，非第三方情感接口）。

    score 为 0~100：>60 偏乐观，<40 偏谨慎，其余中性；无命中词时返回 50。
    """
    pos = neg = 0
    samples: list[dict] = []
    for t in titles:
        p = sum(1 for w in _POSITIVE if w in t)
        n = sum(1 for w in _NEGATIVE if w in t)
        pos += p
        neg += n
        if p or n:
            samples.append({"title": t, "positive": p, "negative": n})

    total = pos + neg
    score = 50.0 if total == 0 else round(pos / total * 100, 1)
    label = "偏乐观" if score >= 60 else ("偏谨慎" if score <= 40 else "中性")
    return {
        "score": score,
        "label": label,
        "positive": pos,
        "negative": neg,
        "samples": samples[:5],
        "basis": "基于基金公告标题的关键词统计（自建词典）",
    }


# ---------------- 产业链归集 ----------------
# 一级：证监会行业门类（基金季报披露的行业配置就是这一层，实测 510300 命中 14 个门类）。
# 必须放在细分关键词前面 —— 「制造业」含「制造」，若走细分规则会被错归到「高端制造」。
_CSRC_CHAIN: list[tuple[str, tuple[str, ...]]] = [
    ("制造业", ("制造业",)),
    ("金融", ("金融业",)),
    ("信息技术", ("信息传输", "信息技术")),
    ("资源能源", ("采矿业",)),
    ("公用事业", ("电力、热力", "水利、环境", "公共设施")),
    ("交通运输", ("交通运输", "仓储", "邮政")),
    ("地产基建", ("建筑业", "房地产")),
    ("科技服务", ("科学研究", "技术服务")),
    ("农林牧渔", ("农、林、牧、渔",)),
    ("商业服务", ("租赁和商务",)),
    ("消费零售", ("批发和零售", "住宿和餐饮")),
    ("医疗健康", ("卫生和社会",)),
    ("文娱传媒", ("文化、体育", "教育")),
]

# 二级：细分行业关键词兜底（数据源给出申万/东财细分行业时才会走到这里）
_SUB_CHAIN: list[tuple[str, tuple[str, ...]]] = [
    ("信息技术", ("半导体", "软件", "电子", "计算机", "通信", "人工智能", "芯片", "IT")),
    ("金融", ("银行", "证券", "保险", "券商", "多元金融", "金融")),
    ("医疗健康", ("医药", "医疗", "生物", "疫苗", "制药", "保健")),
    ("资源能源", ("有色", "钢铁", "化工", "煤炭", "石油", "材料")),
    ("消费零售", ("食品", "饮料", "白酒", "家电", "纺织", "零售", "汽车", "家居", "农业")),
    ("高端制造", ("机械", "设备", "军工", "航天", "电力设备", "新能源", "电池")),
    ("公用事业", ("电力", "公用", "燃气", "水务")),
]


def build_industry_chain(industry_items: list[dict]) -> list[dict]:
    """把持仓行业归集到产业链大类，并按合计权重降序返回。

    先匹配证监会门类，再退回细分行业关键词，都没有则落「其他」。
    """
    buckets: dict[str, float] = {}
    for it in industry_items or []:
        name = it.get("industry") or "其他"
        ratio = float(it.get("ratio") or 0)
        matched = "其他"
        for rules in (_CSRC_CHAIN, _SUB_CHAIN):
            hit = next((chain for chain, kws in rules if any(k in name for k in kws)), None)
            if hit:
                matched = hit
                break
        buckets[matched] = buckets.get(matched, 0.0) + ratio
    return [
        {"chain": k, "ratio": round(v, 2)}
        for k, v in sorted(buckets.items(), key=lambda x: -x[1])
    ]
