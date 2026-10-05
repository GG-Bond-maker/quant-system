# -*- coding: utf-8 -*-
"""A 档：ETF 跟踪基准解析（F10 真实跟踪标的 + 中证指数名录）单元测试。

本文件覆盖的每一条判据都对应一个**实测踩到的坑**，不是构造出来的分支：

* 名称匹配必须**校验**：宽松子串会静默错配（实测「中证新能源指数」→「上证新能源」
  950220、「上证科创板200指数」→「中证200」000904、「中小企业100指数」→「上证100」
  000132）—— 页面会给出一个自信的**错**基准与错跟踪误差，比"无法识别"更糟。
* 兜底表只允许「中证名录覆盖不到」的键参与子串匹配（实测「中证中央企业红利指数」
  命中「红利指数」→ 000015、「沪深300价值指数」命中「沪深300」→ 000300）。
* F10 的「该基金无跟踪标的」是**事实**（货币/债券 ETF），不是解析失败。
* 中证源连打约 60 次即整域 403 ⇒ 必须熔断，不能把限流放大成整页降级。
* 降级态（取不到）不得进缓存，否则一次瞬时失败会被钉死整个 TTL。
"""
from __future__ import annotations

import pytest

from app.core.errors import DataSourceUnavailable
from app.data import etf as etf_mod


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    """每个用例都从干净状态开始：清缓存、清单飞锁、复位两个熔断。"""
    etf_mod._cache.clear()
    etf_mod._KEY_LOCKS.clear()
    monkeypatch.setattr(etf_mod, "_csindex_cooldown_until", 0.0)
    monkeypatch.setattr(etf_mod, "_legulegu_cooldown_until", 0.0)
    monkeypatch.setattr(etf_mod, "_legulegu_failures", 0)
    yield
    etf_mod._cache.clear()
    etf_mod._KEY_LOCKS.clear()


def _fake_request(routes: dict, calls: list | None = None):
    """按 URL 子串分发的 `_request` 替身；`routes` 的值可为常量或 callable。"""
    def _fake(method, url, *, params=None, json_body=None, encoding=None,
              retries=1, headers=None, cookies=None, timeout=None):
        if calls is not None:
            calls.append((method, url, params, json_body))
        for key, val in routes.items():
            if key in url:
                return val(method, url, params, json_body) if callable(val) else val
        raise AssertionError(f"未预期的请求: {method} {url}")
    return _fake


def _f10_html(target: str) -> str:
    return f"<html><table><tr><th>跟踪标的</th><td>{target}</td></tr></table></html>"


def _dict_rows(*pairs: tuple[str, str]) -> dict:
    return {"data": [{"indexCode": c, "indexName": n} for c, n in pairs]}


def _perf_json(name: str, n: int = 30) -> dict:
    rows = []
    for i in range(n):
        d = f"202601{i + 1:02d}"
        rows.append({"tradeDate": d, "close": 100.0 + i, "indexNameCnAll": name})
    return {"code": "200", "data": rows}


# ==================== 名称归一化 ====================

def test_norm_index_name_keeps_market_prefix():
    """归一化必须**保留** 中证/上证 前缀，否则两个不同指数会被抹成同一个串。"""
    assert etf_mod._norm_index_name("中证新能源指数") == "中证新能源"
    assert etf_mod._norm_index_name("上证新能源指数") == "上证新能源"
    assert etf_mod._norm_index_name("中证新能源指数") != etf_mod._norm_index_name("上证新能源指数")


def test_norm_index_name_maps_kechuangboard_to_kechuang():
    """「科创板」→「科创」：F10 写「上证科创板50成份指数」，名录短名写「科创50」。"""
    assert etf_mod._norm_index_name("上证科创板50成份指数") == "上证科创50"
    assert etf_mod._norm_index_name("科创50") in etf_mod._norm_index_name("上证科创板50成份指数")


def test_norm_index_name_flattens_year_maturity_suffix():
    """「10年期国债」与「10年国债」是**同一个**指数：名录短名省「期」、F10 写全称。

    实测 511260（十年国债ETF）：名录短名「10年国债」、F10 写「上证10年期国债指数」。
    不抹平这一个字，归一化后一个「10年国债」、一个「上证10年期国债」，子串匹配整体
    失配 ⇒ 候选退化成「上证指数 000001」「国债指数 000012」两个毫不相干的条目 ⇒
    官方全名校验全不过 ⇒ **明明算得出来的跟踪误差被报成"取不到"**。
    """
    assert etf_mod._norm_index_name("上证10年期国债指数") == "上证10年国债"
    assert etf_mod._norm_index_name("10年国债") in etf_mod._norm_index_name("上证10年期国债指数")
    assert etf_mod._norm_index_name("中证5-10年期国债活跃券指数") == "中证510年国债活跃券"


def test_norm_index_name_keeps_jiuqi_word():
    """只抹**紧跟在数字后面**的「期」：「久期」是真词，不能被削成「久」。

    名录里 14 个「N年久期…」指数（932538~932543 等）全部依赖这一点；把「期」整体
    丢进 `_INDEX_NAME_NOISE` 会把这些键打成「N年久…」，既难读又凭空扩大键碰撞面。
    """
    assert etf_mod._norm_index_name("10年久期国债及政金债") == "10年久期国债及政金债"
    assert etf_mod._norm_index_name("中证2年恒定久期政策性金融债指数") == "中证2年恒定久期政策性金融债"


def test_relax_index_name_maps_catalog_abbreviations():
    """名录爱用缩写、F10 爱写全称。三条别名**都拿中证官方全名逐条核过**。

    实测：名录「沪30年国债」950175 官方全名 = 「上证30年期国债指数」（511130 跟踪）；
    名录「5年地债」930865 官方全名 = 「中证5年期地方政府债指数」（159972 跟踪）。
    入参用**归一化后的键**形态（`idx` 的键本身就是 `_norm_index_name` 过的）。
    """
    assert etf_mod._relax_index_name("沪30年国债") == "上证30年国债"
    assert etf_mod._relax_index_name("5年地债") == "5年地方政府债"
    assert etf_mod._relax_index_name("沪01国债及政金债") == "上证01国债及政策性金融债"
    # ⚠️ 别名只解决「政金债→政策性金融债」；名录这里**还省了「年」**（官方全名是
    # 「上证0-1**年**国债及政策性金融债指数」），本层**不**处理 —— 见 `_INDEX_NAME_ALIASES`
    # 的「未覆盖」说明。这条断言把这个已知边界**显式钉住**，避免以后被误当成已解决。
    assert etf_mod._relax_index_name("沪01国债及政金债") != "上证01年国债及政策性金融债"


def test_relax_index_name_only_treats_leading_hu_as_shanghai():
    """「沪」只有在**开头、且不是「沪深」**时才是「上证」的缩写。

    名录里 280 个含「沪」的键中多数是**沪深**词（「沪深300沪市」→ 官方「沪深300沪市
    指数」、「中华沪深港300」…）。只判 `startswith("沪")` 会把这些键打成
    「上证深300沪市」这种乱码 —— 既挤掉真候选，又白白多打几次很紧的 oracle 配额。
    """
    assert etf_mod._relax_index_name("沪深300沪市") == "沪深300沪市"
    assert etf_mod._relax_index_name("中华沪深港300") == "中华沪深港300"
    assert etf_mod._relax_index_name("沪做市国债") == "上证做市国债"


def test_relax_index_name_never_leaks_into_strict_normalizer():
    """放宽**只能**影响"谁进候选"，绝不能改名录的键。

    名录的键同时喂 `_resolve_by_name` 里那条**不校验**的快速路径（`idx.get(nf)` 且
    唯一即采用）。放宽归一化 = 给这条免检通道放行未经核实的基准，正是本模块反复踩过
    的静默错配。
    """
    assert etf_mod._norm_index_name("沪30年国债") == "沪30年国债"
    assert etf_mod._norm_index_name("5年地债") == "5年地债"


# ==================== F10 跟踪标的 ====================

def test_f10_parses_index_target(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_request({"jbgk_": _f10_html("中证全指证券公司指数")}))
    got = etf_mod.fetch_etf_tracking_target("512880")
    assert got == {"kind": "index", "name": "中证全指证券公司指数"}


def test_f10_no_target_is_a_fact_not_a_failure(monkeypatch: pytest.MonkeyPatch):
    """货币 ETF 的 F10 原文就是「该基金无跟踪标的」——必须与"解析失败"区分开。"""
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_request({"jbgk_": _f10_html("该基金无跟踪标的")}))
    got = etf_mod.fetch_etf_tracking_target("511990")
    assert got["kind"] == "none"


def test_f10_unparsable_is_unknown_and_not_cached(monkeypatch: pytest.MonkeyPatch):
    """页面结构变化 ⇒ kind=unknown，且**不得**进缓存（下次要重试）。"""
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_request({"jbgk_": "<html>没有这个字段</html>"}, calls))
    assert etf_mod.fetch_etf_tracking_target("510300")["kind"] == "unknown"
    assert etf_mod.fetch_etf_tracking_target("510300")["kind"] == "unknown"
    assert len(calls) == 2, "unknown 结果不应被缓存"


def test_f10_non_cn_code_is_unsupported(monkeypatch: pytest.MonkeyPatch):
    """非 6 位中国场内基金代码，F10 无此页 ⇒ unsupported，且不打网络。"""
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request", _fake_request({}, calls))
    assert etf_mod.fetch_etf_tracking_target("SPY")["kind"] == "unsupported"
    assert calls == []


# ==================== 名称 -> 代码（含官方全名校验） ====================

def test_resolve_exact_dict_match_skips_oracle(monkeypatch: pytest.MonkeyPatch):
    """名录**全等**命中 ⇒ 直接采用，不再打 perf（省一次外部调用）。"""
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("沪深300指数"),
        "query-index-item": _dict_rows(("000300", "沪深300")),
        "index-perf": lambda *a, **k: pytest.fail("全等命中不应再打 perf"),
    }, calls))
    b = etf_mod.resolve_etf_benchmark("510300", "沪深300ETF华泰柏瑞", None)
    assert (b["status"], b["code"], b["name"], b["source"]) == (
        "ok", "000300", "沪深300", "csindex-name")


def test_resolve_rejects_wrong_substring_candidate(monkeypatch: pytest.MonkeyPatch):
    """「中证新能源指数」必须落到 000941，不能被「上证新能源」(950220) 抢走。"""
    def _perf(method, url, params, body):
        code = (params or {}).get("indexCode")
        if code == "000941":
            return _perf_json("中证新能源指数")
        if code == "950220":
            return _perf_json("上证新能源指数")
        raise AssertionError(f"未预期 indexCode={code}")

    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("中证新能源指数"),
        "query-index-item": _dict_rows(("000941", "新能源"), ("950220", "上证新能源")),
        "index-perf": _perf,
    }))
    b = etf_mod.resolve_etf_benchmark("159875", "新能源ETF嘉实", None)
    assert b["status"] == "ok"
    assert b["code"] == "000941", f"错配到 {b['code']} {b['name']}"


def test_resolve_rejects_all_candidates_when_oracle_disagrees(monkeypatch: pytest.MonkeyPatch):
    """官方全名与 F10 声明不一致 ⇒ 判「无法识别」，绝不硬选一个。"""
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("上证科创板200指数"),
        "query-index-item": _dict_rows(("000904", "中证200")),
        "index-perf": _perf_json("中证200指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("588820", "科创200ETF华夏", None)
    assert b["status"] == "unresolved", f"不应错配到 {b.get('code')}"


def test_resolve_accepts_unique_superset(monkeypatch: pytest.MonkeyPatch):
    """F10 给的是缩写名时，唯一一个"官方全名包含 F10 名"的候选可以采用。"""
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("有色金属"),
        "query-index-item": _dict_rows(("000819", "有色金属")),
        "index-perf": _perf_json("中证申万有色金属指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("512400", "有色金属ETF南方", None)
    assert (b["status"], b["code"]) == ("ok", "000819")


def test_resolve_matches_catalog_shanghai_prefix_alias(monkeypatch: pytest.MonkeyPatch):
    """名录短名「沪30年国债」 vs F10「上证30年期国债指数」⇒ 必须落到 950175。

    实测 511130（30年国债ETF博时）：名录用「沪」缩写、F10 写「上证」全称，逐字对不上
    ⇒ 修前报"取不到"；而 950175 的**官方全名与 F10 声明逐字相同**（判据 64：不猜，
    但要拿得到锚点）。注意名录里还有 931080「30年国债」，它的官方全名是**中证**30年
    期国债指数 —— 必须被校验拒掉，不能顺手选它。
    """
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("上证30年期国债指数"),
        "query-index-item": _dict_rows(("950175", "沪30年国债"), ("931080", "30年国债")),
        "index-perf": lambda m, u, params, body: _perf_json(
            "上证30年期国债指数" if (params or {}).get("indexCode") == "950175"
            else "中证30年期国债指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("511130")
    assert (b["status"], b["code"], b["name"]) == ("ok", "950175", "上证30年期国债指数")


def test_resolve_matches_catalog_dizhai_alias(monkeypatch: pytest.MonkeyPatch):
    """名录短名「5年地债」 vs F10「中证5年期地方政府债指数」⇒ 必须落到 930865。

    实测 159972（5年地方债ETF鹏华）。
    """
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("中证5年期地方政府债指数"),
        "query-index-item": _dict_rows(("930865", "5年地债")),
        "index-perf": _perf_json("中证5年期地方政府债指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("159972")
    assert (b["status"], b["code"], b["name"]) == (
        "ok", "930865", "中证5年期地方政府债指数")


def test_relaxed_candidates_still_verified_by_oracle(monkeypatch: pytest.MonkeyPatch):
    """**放宽候选 ≠ 放宽校验**：名录只有「上证1-5年地债」时，F10 的「上证5年期地方政府债」

    必须被拒 —— 「1-5年」与「5年」是**两个不同**的指数。修前修后都该是 unresolved；
    本用例守的是"别为了让 511060 显示出来而顺手把 950047 选上"。
    """
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("上证5年期地方政府债指数"),
        "query-index-item": _dict_rows(("950047", "上证1-5年地债")),
        "index-perf": _perf_json("上证1-5年期地方政府债指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("511060")
    assert b["status"] == "unresolved", f"不应错配到 {b.get('code')} {b.get('name')}"


def test_resolve_matches_catalog_infix_insertion(monkeypatch: pytest.MonkeyPatch):
    """名录短名往**中间插字**（系统性缩写习惯）⇒ 由"子序列"桶兜住。

    名录「沪做市国债」950243 的官方全名是「上证**基准**做市国债指数」——
    子串两个方向都对不上，但短名是 F10 名的**子序列**。实测 511100（国债ETF华夏）。
    """
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("上证基准做市国债指数"),
        "query-index-item": _dict_rows(("950243", "沪做市国债"), ("000012", "国债指数")),
        "index-perf": lambda m, u, params, body: _perf_json(
            "上证基准做市国债指数" if (params or {}).get("indexCode") == "950243"
            else "上证国债指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("511100")
    assert (b["status"], b["code"], b["name"]) == (
        "ok", "950243", "上证基准做市国债指数")


def test_subsequence_bucket_prefers_matching_market_prefix(monkeypatch: pytest.MonkeyPatch):
    """子序列桶里同时有「AAA科创债」(中证 932160) 与「沪AAA科创债」(上证 950167) 时，
    必须选**官方全名与 F10 声明一致**的那个。

    排序按"插字少"优先（950167 插 5 字 < 932160 插 7 字），但最终仍由官方全名校验
    定夺 —— 排序只决定"谁有机会被校验"，不决定结论。实测 511150。
    """
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("上证AAA科技创新公司债指数"),
        "query-index-item": _dict_rows(("932160", "AAA科创债"), ("950167", "沪AAA科创债")),
        "index-perf": lambda m, u, params, body: _perf_json(
            "上证AAA科技创新公司债指数" if (params or {}).get("indexCode") == "950167"
            else "中证AAA科技创新公司债指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("511150")
    assert (b["status"], b["code"], b["name"]) == (
        "ok", "950167", "上证AAA科技创新公司债指数")


def test_subsequence_bucket_does_not_force_oracle_on_exact_hit(monkeypatch: pytest.MonkeyPatch):
    """子序列桶是**最低优先级**：名录全等命中时走免检快速路径，不得因此多打 perf。

    子序列是三个桶里最松的一个，若它被并进"全等"判定，等于给免检通道放行未校验的
    基准，还会白烧一次中证配额（该源连打 ~60 次即整域 403）。
    """
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("沪深300指数"),
        "query-index-item": _dict_rows(("000300", "沪深300"), ("999999", "沪深")),
        "index-perf": lambda *a, **k: pytest.fail("全等命中不应再打 perf"),
    }))
    b = etf_mod.resolve_etf_benchmark("510300")
    assert (b["status"], b["code"], b["name"]) == ("ok", "000300", "沪深300")


def test_fast_path_uses_strict_key_not_relaxed(monkeypatch: pytest.MonkeyPatch):
    """`_resolve_by_name` 的**免检**快速路径必须用严格键，不能跟着放宽。

    放宽它 = 让「沪X」直接命中名录的「上证X」并**跳过**官方全名校验。构造：F10 声明
    「沪新能源指数」、名录里只有「上证新能源」(950220)，而 950220 的官方全名其实是
    **中证**新能源指数 ⇒ 正确行为是判 unresolved；放宽快速路径则会自信地返回 950220
    并标 source=csindex-name（页面会显示一个**错**基准与错跟踪误差）。

    注：本用例的 code 是 6 位，故不会走兜底表（`resolve_tracking_index` 只对非 6 位
    代码生效），结果只可能来自名录路径。名录里另放一条「新能源」(000941)，是为了让
    "有候选 ⇒ 必须打 perf 校验" 这个断言**不依赖别名层是否生效**（否则别名层一坏、
    候选为空，perf 自然不被调用，用例会因无关原因变红 —— 两个契约被绑在一起）。
    """
    calls: list = []
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("沪新能源指数"),
        "query-index-item": _dict_rows(("950220", "上证新能源"), ("000941", "新能源")),
        "index-perf": lambda m, u, params, body: _perf_json(
            "中证新能源指数" if (params or {}).get("indexCode") == "950220"
            else "国证新能源指数"),
    }, calls))
    b = etf_mod.resolve_etf_benchmark("510001")
    perf_codes = [p.get("indexCode") for (_m, u, p, _j) in calls if "index-perf" in u]
    assert perf_codes, "免检快速路径被放宽 ⇒ 跳过了官方全名校验"
    assert b["status"] == "unresolved", f"放行了未校验的基准: {b}"


def test_resolve_rejects_ambiguous_superset(monkeypatch: pytest.MonkeyPatch):
    """多个候选都满足"官方全名包含 F10 名" ⇒ 有歧义，**不猜**。

    构造：F10 写「有色金属」（缩写），名录里有「有色」「金属」两个都能对上，
    两个候选的官方全名又都包含「有色金属」⇒ 无法判定唯一，必须判 unresolved。
    """
    monkeypatch.setattr(etf_mod, "_request", _fake_request({
        "jbgk_": _f10_html("有色金属"),
        "query-index-item": _dict_rows(("000819", "有色"), ("000820", "金属")),
        "index-perf": lambda m, u, params, body: _perf_json(
            "中证有色金属指数" if (params or {}).get("indexCode") == "000819"
            else "国证有色金属指数"),
    }))
    b = etf_mod.resolve_etf_benchmark("512400", "有色金属ETF南方", None)
    assert b["status"] == "unresolved"


def test_resolve_no_target_for_money_market_etf(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_request({"jbgk_": _f10_html("该基金无跟踪标的")}))
    b = etf_mod.resolve_etf_benchmark("511990", "华宝添益ETF", None)
    assert b["status"] == "no_target"


def test_resolve_never_uses_loose_fund_name_for_cn(monkeypatch: pytest.MonkeyPatch):
    """F10 不可达时，中国 ETF **不得**退回"基金名称子串"兜底。

    实测风险：「银华沪深300价值ETF」名称里含「沪深300」，但它跟踪的是 000919
    沪深300价值指数 —— 旧写法会把它错配到 000300。宁缺勿错。
    """
    def _boom(*a, **k):
        raise DataSourceUnavailable("F10 挂了")

    monkeypatch.setattr(etf_mod, "_request", _boom)
    b = etf_mod.resolve_etf_benchmark("562320", "银华沪深300价值ETF", None)
    assert b["status"] == "unresolved"


def test_resolve_us_etf_uses_declared_tracking_index(monkeypatch: pytest.MonkeyPatch):
    """非中国场内基金：目录里 tracking_index 是静态声明的，走兜底表映射到代理。"""
    b = etf_mod.resolve_etf_benchmark("SPY", "标普500ETF-SPDR", "标普500")
    assert (b["status"], b["code"], b["market"]) == ("ok", "SPY", "us")


# ==================== 兜底表收紧 ====================

def test_legacy_substring_keys_exclude_dict_covered_names():
    """名录里有权威条目的键**不得**参与子串匹配（实测三个静默错配）。"""
    assert etf_mod.resolve_tracking_index("银华沪深300价值ETF", None) is None
    assert etf_mod.resolve_tracking_index("央企红利ETF华泰柏瑞", "中证中央企业红利指数") is None
    assert etf_mod.resolve_tracking_index("中小企业100ETF华夏", None) is None


def test_legacy_substring_keys_still_cover_shenzhen_guozheng():
    """深证/国证自有系列不在中证名录里，必须仍由兜底表覆盖。

    注意匹配的是 **F10 全名**：「易方达创业板ETF」这个基金名里只有「创业板」，
    没有「创业板指」；F10 写的才是「创业板指数(价格)」。
    """
    assert etf_mod.resolve_tracking_index(None, "创业板指数(价格)") == ("sz", "399006")
    assert etf_mod.resolve_tracking_index(None, "深证100指数(价格)") == ("sz", "399330")
    assert etf_mod.resolve_tracking_index("国证2000ETF", None) == ("sz", "399303")
    # 基金名本身不足以判定（宁缺勿错）
    assert etf_mod.resolve_tracking_index("易方达创业板ETF", None) is None


def test_legacy_map_has_no_wrong_zhongzheng2000_entry():
    """旧表把「中证2000」指向 sh000932，而 000932 实为**中证主要消费**（已删除）。"""
    assert "中证2000" not in etf_mod._TRACKING_INDEX_MAP
    assert etf_mod._TRACKING_INDEX_MAP["上证红利"] == ("sh", "000015")


# ==================== 基准行情（腾讯 / 中证 perf 分工） ====================

def test_benchmark_kline_prefers_tencent_for_000_codes(monkeypatch: pytest.MonkeyPatch):
    bars = [{"date": f"2026-01-{i + 1:02d}", "close": 1.0} for i in range(30)]
    seen: list = []
    monkeypatch.setattr(etf_mod, "fetch_index_kline",
                        lambda m, c, limit=252: (seen.append((m, c)), bars)[1])
    monkeypatch.setattr(etf_mod, "_request", _fake_request({}, []))
    out = etf_mod.fetch_benchmark_kline("sh", "000300", limit=252)
    assert len(out) == 30
    assert seen == [("sh", "000300")]


def test_benchmark_kline_uses_csindex_for_csi_namespace(monkeypatch: pytest.MonkeyPatch):
    """932000 这类中证自有命名空间腾讯没有 ⇒ 直接走中证 perf，不浪费一次腾讯请求。"""
    def _tx(m, c, limit=252):
        raise AssertionError("CSI 命名空间不应打腾讯")

    monkeypatch.setattr(etf_mod, "fetch_index_kline", _tx)
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_request({"index-perf": _perf_json("中证2000指数")}))
    out = etf_mod.fetch_benchmark_kline("sh", "932000", limit=252)
    assert len(out) == 30


def test_benchmark_kline_falls_back_to_perf_when_tencent_empty(monkeypatch: pytest.MonkeyPatch):
    """深证/国证系列腾讯有数据，但腾讯若返回空（限流/停更）仍要能兜到 perf。"""
    monkeypatch.setattr(etf_mod, "fetch_index_kline", lambda m, c, limit=252: [])
    monkeypatch.setattr(etf_mod, "_request",
                        _fake_request({"index-perf": _perf_json("创业板指数")}))
    out = etf_mod.fetch_benchmark_kline("sz", "399006", limit=252)
    assert len(out) == 30


# ==================== 限流熔断 ====================

def test_csindex_circuit_breaker_stops_hammering(monkeypatch: pytest.MonkeyPatch):
    """一次失败后进入冷却，后续调用**不再打上游**（否则限流会被自己放大）。"""
    calls: list = []

    def _boom(*a, **k):
        calls.append(a)
        raise DataSourceUnavailable("403")

    monkeypatch.setattr(etf_mod, "_request", _boom)
    with pytest.raises(DataSourceUnavailable):
        etf_mod.fetch_index_dict()
    n_after_first = len(calls)
    with pytest.raises(DataSourceUnavailable, match="冷却"):
        etf_mod.fetch_index_dict()
    assert len(calls) == n_after_first, "冷却期内不得再打上游"


# ==================== 缓存语义 ====================

def test_fetch_index_dict_is_single_flight(monkeypatch: pytest.MonkeyPatch):
    """名录必须**单飞**：详情页 header/tracking/valuation 三块会并发调用它。

    实测（2026-10-04）：无单飞时一次冷请求打了 **4 次** POST（2 页/构建 × 2 个并发
    构建），既浪费中证源很紧的配额（连打 ~60 次即整域 403），又让全局 0.25s 节流把
    冷路径顶出端点预算。本用例用**并发**调用复现该场景（判据 57：只断言"能取到"
    是不够的，必须断言"只打了几次上游"）。
    """
    import threading
    import time

    posts: list = []
    rows = [{"indexCode": f"{i:06d}", "indexName": f"指数{i}"} for i in range(2000)]

    def _req(method, url, *, params=None, json_body=None, **kw):
        assert method == "POST", method
        page = json_body["pager"]["pageNum"]
        posts.append(page)
        # 必须让构建**有耗时**：否则先跑的线程会在其它线程读缓存前就写完，
        # 于是"无单飞"也能全命中缓存 ⇒ 用例恒绿、根本不具备区分力（判据 57）。
        time.sleep(0.15)
        return {"data": rows if page == 1 else rows[:5]}

    monkeypatch.setattr(etf_mod, "_request", _req)

    results: list = []
    threads = [threading.Thread(target=lambda: results.append(etf_mod.fetch_index_dict()))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)

    # 2 页 × 1 次构建 = 2 次 POST（无单飞会是 8 次）
    assert sorted(posts) == [1, 2], f"名录未单飞，POST 页号={posts}"
    assert len(results) == 4
    assert all(r == results[0] for r in results)


def test_cached_ok_does_not_cache_none():
    """降级态不得进缓存：一次瞬时失败不能被钉死整个 TTL。"""
    calls: list = []

    def _builder():
        calls.append(1)
        return None

    assert etf_mod._cached_ok("k-none", 3600, _builder) is None
    assert etf_mod._cached_ok("k-none", 3600, _builder) is None
    assert len(calls) == 2


def test_cached_ok_single_flight():
    """同一 key 并发只跑一次 builder（详情页三个块会同时解析同一基准）。"""
    import threading
    calls: list = []
    gate = threading.Event()

    def _builder():
        calls.append(1)
        gate.wait(2.0)
        return {"v": 1}

    threads = [threading.Thread(target=lambda: etf_mod._cached_ok("k-sf", 3600, _builder))
               for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5.0)
    assert len(calls) == 1


# ==================== 估值（乐咕乐股） ====================

def test_legulegu_circuit_breaker_needs_consecutive_failures():
    """闸门粒度必须与不变量同宽（判据 19）：**单只指数未覆盖**不得让整页估值降级。

    实测乐咕乐股对**未覆盖的指数**也可能返回 HTTP 错误 ⇒ 若"一次失败就熔断"，
    "某一个指数没覆盖"会被放大成"整页估值全部不可用"。故必须**连续**失败 3 次才冷却，
    且一次成功即清零。
    """
    # 两次失败：不冷却，后续调用仍可打上游
    etf_mod._legulegu_fail()
    etf_mod._legulegu_fail()
    etf_mod._legulegu_guard()  # 不抛异常 = 尚未冷却
    assert etf_mod._legulegu_failures == 2

    # 一次成功即清零 ⇒ 之前的两次失败不得累积到下一次
    etf_mod._legulegu_ok()
    assert etf_mod._legulegu_failures == 0
    etf_mod._legulegu_fail()
    etf_mod._legulegu_fail()
    etf_mod._legulegu_guard()  # 仍不抛（被成功清零过）
    assert etf_mod._legulegu_failures == 2

    # 第三次**连续**失败 ⇒ 达到阈值，进入冷却
    etf_mod._legulegu_fail()
    assert etf_mod._legulegu_failures == 0
    with pytest.raises(DataSourceUnavailable, match="冷却"):
        etf_mod._legulegu_guard()


def test_legulegu_empty_result_is_not_a_failure(monkeypatch: pytest.MonkeyPatch):
    """200 + 空数组 = 该指数**未覆盖**，是事实不是失败 ⇒ 不得计入熔断计数。"""
    monkeypatch.setattr(etf_mod, "_legulegu_session",
                        lambda: {"token": "t", "cookies": {}, "headers": {}})
    monkeypatch.setattr(etf_mod, "_request", lambda *a, **k: {"data": []})
    assert etf_mod._legulegu_fetch("399975", "pe") == []
    assert etf_mod._legulegu_failures == 0


def test_legulegu_session_failure_trips_breaker_immediately(monkeypatch: pytest.MonkeyPatch):
    """会话（CSRF/token）取不到 ⇒ **立即熔断**，不等 3 次 —— 判据 19 的另一个方向。

    与会话不同，**逐指数**的失败仍要连续 3 次才熔断（见上一条用例）；但会话与指数
    无关，且不立刻熔断会出事：估值块被 ``wait_for`` 截断后线程仍在跑
    （``run_in_executor`` 不可取消），下一个请求会在**同一个会话单飞锁**上排队，
    实测把后续请求越拖越慢（5.7s → 6.1s），最终顶破端点预算。
    """
    calls: list = []
    monkeypatch.setattr(etf_mod, "_build_legulegu_session",
                        lambda: calls.append(1) or None)

    with pytest.raises(DataSourceUnavailable):
        etf_mod._legulegu_fetch("000300", "pe")
    assert calls == [1]
    assert etf_mod._legulegu_failures == 0

    # 已熔断：第二次**连会话都不再尝试**（冷却检查先于会话获取）
    with pytest.raises(DataSourceUnavailable, match="冷却"):
        etf_mod._legulegu_fetch("000300", "pe")
    assert calls == [1], "冷却期内不得再取会话（不得再打上游）"


def test_build_legulegu_session_parses_csrf_and_sets_timeout(monkeypatch: pytest.MonkeyPatch):
    """CSRF 自己取且**必须显式设超时** —— akshare 的 ``get_cookie_csrf`` 是
    ``requests.get(url)``（无 timeout），实测被限流时整域 504、调用挂 5s+。"""
    seen: dict = {}

    class _Resp:
        text = '<html><head><meta name="_csrf" content="abc123"></head></html>'
        cookies = {"sid": "s1"}

        def raise_for_status(self):
            return None

    class _Cli:
        def __init__(self, **kw):
            seen.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            seen["url"] = url
            return _Resp()

    monkeypatch.setattr(etf_mod.httpx, "Client", _Cli)
    monkeypatch.setattr(etf_mod, "_legulegu_token", lambda: "tok")
    got = etf_mod._build_legulegu_session()
    assert got is not None
    assert got["token"] == "tok"
    assert got["headers"]["X-CSRF-Token"] == "abc123"
    assert got["cookies"] == {"sid": "s1"}
    assert seen["timeout"] == etf_mod._LEGULEGU_CSRF_TIMEOUT, "必须显式设超时"
    assert etf_mod._LEGULEGU_CSRF_TIMEOUT <= 3.0, "超时必须显著小于端点预算，否则拖垮整页"


def test_build_legulegu_session_returns_none_on_network_error(monkeypatch: pytest.MonkeyPatch):
    """抓 CSRF 页超时/504 ⇒ 返回 None，**不向上抛**（由 `_legulegu_session` 计熔断）。"""
    class _Cli:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            raise etf_mod.httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(etf_mod.httpx, "Client", _Cli)
    monkeypatch.setattr(etf_mod, "_legulegu_token", lambda: "tok")
    assert etf_mod._build_legulegu_session() is None


def test_legulegu_index_code_suffix_rule():
    """后缀按命名空间取：000→.SH / 399→.SZ / 其余（93xxxx 等）→.CSI。"""
    assert etf_mod._legulegu_index_code("000300") == "000300.SH"
    assert etf_mod._legulegu_index_code("399006") == "399006.SZ"
    assert etf_mod._legulegu_index_code("932000") == "932000.CSI"


def test_valuation_proxy_covers_index_outside_old_whitelist(monkeypatch: pytest.MonkeyPatch):
    """科创50(000688) 不在旧的 12 代码白名单里，A 档后必须能取到估值。"""
    lg: list = []

    def _req(method, url, **kw):
        if "jbgk_" in url:
            return _f10_html("上证科创板50成份指数")
        if "query-index-item" in url:
            return _dict_rows(("000688", "科创50"))
        if "index-perf" in url:
            return _perf_json("上证科创板50成份指数")
        if "legulegu.com" in url:
            lg.append(kw.get("params", {}).get("indexCode"))
            return {"data": [{"date": "2026-01-01", "ttmPe": 80.2, "pb": 5.1}]}
        raise AssertionError(url)

    monkeypatch.setattr(etf_mod, "_request", _req)
    monkeypatch.setattr(etf_mod, "_legulegu_session",
                        lambda: {"token": "t", "cookies": {}, "headers": {}})
    info = {"code": "588000", "name": "科创50ETF华夏", "country": "cn",
            "tracking_index": None}
    got = etf_mod.fetch_etf_valuation_proxy("588000", info)
    assert got is not None and got["index_code"] == "000688"
    assert lg == ["000688.SH", "000688.SH"]


def test_valuation_proxy_returns_none_for_us_benchmark(monkeypatch: pytest.MonkeyPatch):
    """美股代理（SPY 等）没有 A 股估值分位 ⇒ 如实返回 None，不套用别处口径。"""
    info = {"code": "SPY", "name": "标普500ETF-SPDR", "country": "us",
            "tracking_index": "标普500"}
    assert etf_mod.fetch_etf_valuation_proxy("SPY", info) is None


def test_valuation_proxy_returns_none_when_index_uncovered(monkeypatch: pytest.MonkeyPatch):
    """乐咕乐股返回 200 + 空数组（该指数未覆盖，实测 399975 证券公司）⇒ 如实降级。"""
    def _req(method, url, **kw):
        if "jbgk_" in url:
            return _f10_html("中证全指证券公司指数")
        if "query-index-item" in url:
            return _dict_rows(("399975", "证券公司"))
        if "index-perf" in url:
            return _perf_json("中证全指证券公司指数")
        if "legulegu.com" in url:
            return {"data": []}
        raise AssertionError(url)

    monkeypatch.setattr(etf_mod, "_request", _req)
    monkeypatch.setattr(etf_mod, "_legulegu_session",
                        lambda: {"token": "t", "cookies": {}, "headers": {}})
    info = {"code": "512880", "name": "证券ETF国泰", "country": "cn",
            "tracking_index": None}
    assert etf_mod.fetch_etf_valuation_proxy("512880", info) is None
