"""全市场扩容改造的回归测试（``scripts/expand_universe_2500.py``）。

覆盖本次新增的三处能力，并为「默认值 = 原行为，零行为变更」提供证据：

1. ``step_select(universe_name=...)`` —— 清单文件名可覆盖。全市场扩容写
   ``universe_full.json``，**不得覆盖** 2500 池的决策记录 ``universe_2500.json``。
2. 清单新增 ``unavailable`` 段 —— 北交所以「显式不可得 + 原因 + 原始代码」登记。
   🔴 反向断言：北交所代码**绝不能**出现在 ``symbols``（可交易清单）里。
   依据：2026-10-02 四源实测（新浪 JSONDecodeError / 腾讯 KeyError /
   东财 ProxyError / baostock 空返回），akshare 无北交所日线接口。
3. ``step_fetch(cover_min=..., state_name=...)`` —— 覆盖判定阈值必须**随起点联动**。
   起点改 2022-01-01 而 cover_min 仍是 2023-01-05 时，「只覆盖 2023+ 的旧分区」
   会被误判为已覆盖，新标的 2022 整年数据静默缺失。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

# 🔴 允许被证伪脚本重定向到「副本」：``dev_falsify_full_market.py`` 若直接对原文件
# 做注入-还原，则**宿主对同一命令的重复执行**会让两次运行交错 —— 第二次会把
# "已被注入的文件"当成 original 读入，还原时把缺陷写回原文件（2026-10-02 实测：
# ``ex.shutdown(wait=True)`` 残留在生产脚本里）。改为对副本注入后，原文件只读。
_SCRIPT = Path(
    os.environ.get("AQP_EXPAND_SCRIPT")
    or (PROJECT_ROOT / "scripts" / "expand_universe_2500.py")
)


def _load_script_module():
    """以文件路径加载扩容脚本为独立模块（与既有测试同法，避免命名空间污染）。"""
    name = "expand_universe_2500_full_market_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


mod = _load_script_module()

# 4 行快照：2 只可交易（沪/深）+ 2 只北交所（前缀 bj / 92 段）
_SNAPSHOT = [
    {"code": "sh600000", "name": "浦发银行", "amount": 1.0e9},
    {"code": "sz000001", "name": "平安银行", "amount": 9.0e8},
    {"code": "bj430047", "name": "诺思兰德", "amount": 1.0e6},
    {"code": "sh920819", "name": "北交所92段样例", "amount": 1.0e6},
]


@pytest.fixture
def select_env(tmp_path, monkeypatch):
    """把 step_select 的外部依赖全部替换为离线桩。"""
    monkeypatch.setattr(mod, "_fetch_spot_snapshot", lambda cache: list(_SNAPSHOT))
    # step_select 内部 ``from app.data.parquet_store import list_symbols_with_data``，
    # 故打桩模块属性即可（调用时解析）。
    monkeypatch.setattr("app.data.parquet_store.list_symbols_with_data",
                        lambda ds: ["600000.SH"])
    root = tmp_path / "parquet"
    root.mkdir()
    return root


def test_select_honours_custom_universe_name(select_env):
    """清单落到自定义文件名，且不触碰 2500 池的历史记录。"""
    root = select_env
    state: dict = {}
    report: dict = {}

    selected = mod.step_select(root, state, report, limit=100,
                               snapshot_json=None,
                               universe_name="universe_full.json")

    out = root.parent / "universe_full.json"
    assert out.exists(), "自定义清单名未被采用"
    assert not (root.parent / "universe_2500.json").exists(), \
        "全市场扩容不得覆盖 2500 池的决策记录"
    assert sorted(selected) == ["000001.SZ", "600000.SH"]


def test_select_registers_bse_as_explicitly_unavailable(select_env):
    """北交所必须进 ``unavailable``（含原因），且不得进可交易清单。"""
    root = select_env
    state: dict = {}
    report: dict = {}

    mod.step_select(root, state, report, limit=100, snapshot_json=None,
                    universe_name="universe_full.json")
    d = json.loads((root.parent / "universe_full.json").read_text(encoding="utf-8"))

    un = d["unavailable"]
    assert un["market"] == "BSE"
    assert un["count"] == 2
    assert len(un["symbols"]) == 2
    assert {x["code"] for x in un["symbols"]} == {"bj430047", "sh920819"}
    # 每一条都必须带原因，且原因是"北交所"而非空串/占位
    assert all(x["reason"] and "北交所" in x["reason"] for x in un["symbols"])
    # 反向断言：北交所绝不出现在可交易清单里（不以 0/占位冒充）
    listed = {x["symbol"] for x in d["symbols"]}
    assert listed == {"000001.SZ", "600000.SH"}
    assert d["excluded_bse"] == 2
    assert d["eligible_non_bj"] == 2


def test_fetch_forwards_cover_min_and_state_name(tmp_path, monkeypatch):
    """``cover_min`` 必须原样传到判定函数（否则起点与阈值脱钩 ⇒ 静默缺年）。"""
    captured: dict = {}

    def _fake_classify(root, selected, have, cover_min=mod.FETCH_COVER_MIN):
        captured["cover_min"] = cover_min
        return set(), [], []

    monkeypatch.setattr(mod, "_classify_fetch_coverage", _fake_classify)
    monkeypatch.setattr(mod, "ada_list_symbols", lambda: set())
    monkeypatch.setattr("app.data.parquet_store.manifest_invalidate", lambda: None)

    root = tmp_path / "parquet"
    root.mkdir()
    mod.step_fetch(root, {}, {}, selected=[], start=date(2022, 1, 1),
                   end=date(2026, 9, 30), cover_min=date(2022, 1, 5),
                   state_name=".expand_full_state.json")

    assert captured["cover_min"] == date(2022, 1, 5)


def test_fetch_default_cover_min_is_legacy_value():
    """默认阈值必须仍是 2023-01-05（2500 池的历史行为，零行为变更）。"""
    assert mod.FETCH_COVER_MIN == date(2023, 1, 5)


def test_cli_defaults_preserve_legacy_names():
    """CLI 默认值必须指向 2500 池的原文件名（否则旧调用会写错位置）。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert '"--universe-name", default="universe_2500.json"' in src
    assert '"--state-name", default=".expand_2500_state.json"' in src
    assert '"--report-name", default="expand_2500_report.json"' in src
    assert '"--cover-min", default=FETCH_COVER_MIN.isoformat()' in src


# --------------------------------------------------------------------------- #
# 静默失活防护（2026-10-02 事故）
#
# 事故现场：全市场抓取长跑在 09:41 后**进度行与状态文件双双冻结**在 898/2873，
# 磁盘却仍在写入，进程又活着，撑了 1h54m 才因外部终止而退出（EXIT=1）。
# 根因：宿主 safe-delete 守卫的 ``_check_bulk_delete_guard``（sitecustomize.py）
# 在 helper 超时/不可用/非零退出时 ``raise SystemExit(1)``；该异常发生在 worker
# 线程内，经 ``Future.result()`` 重抛到主线程，而主循环只写了
# ``except Exception`` —— ``SystemExit`` 属 ``BaseException``，漏网后触发
# ``ThreadPoolExecutor.__exit__`` 的 ``shutdown(wait=True)``，主线程遂静默挂起、
# 由 worker 把剩余队列 drain 完；``SystemExit`` 又不打印 traceback。
#
# 三条判据都必须成立：① 单标的不打死循环；② 循环体异常不 drain 队列；
# ③ 源码层留下显式护栏（防止后人"顺手"把 BaseException 改回 Exception）。
# --------------------------------------------------------------------------- #
def _patch_fetch_env(monkeypatch) -> None:
    """让 ``step_fetch`` 走到 todo 分支且不碰网络/清单。"""
    monkeypatch.setattr(mod, "_classify_fetch_coverage",
                        lambda *a, **k: (set(), [], []))
    monkeypatch.setattr(mod, "ada_list_symbols", lambda: set())
    monkeypatch.setattr("app.data.parquet_store.manifest_invalidate", lambda: None)


def test_fetch_survives_baseexception_from_worker(tmp_path, monkeypatch):
    """worker 抛 ``SystemExit`` 只应记为该标的失败，不得打死主循环。"""
    _patch_fetch_env(monkeypatch)
    root = tmp_path / "parquet"
    root.mkdir()

    calls: list[str] = []

    def _fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        calls.append(code)
        if code == "000002":
            raise SystemExit(1)          # ← 宿主 safe-delete 守卫的真实行为
        return 100, []

    # ``fetch_and_write_daily_bars`` 在 step_fetch 内部 import，故须打桩源模块
    monkeypatch.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars",
                        _fake_fetch)

    fs = mod.step_fetch(root, {}, {},
                        selected=["000001.SZ", "000002.SZ", "000003.SZ"],
                        start=date(2022, 1, 1), end=date(2026, 9, 30),
                        cover_min=date(2022, 1, 5),
                        state_name=".expand_full_state.json")

    assert sorted(calls) == ["000001", "000002", "000003"], \
        "SystemExit 打断了抓取循环（剩余标的被跳过）"
    assert fs["failed"]["000002.SZ"].startswith("SystemExit"), \
        "BaseException 未登记进 failed（静默吞掉）"
    assert sorted(fs["ok"]) == ["000001.SZ", "000003.SZ"]
    assert fs["done"] == 3


def test_fetch_aborts_without_draining_pool(tmp_path, monkeypatch):
    """循环体异常时必须取消未启动任务，而非 ``shutdown(wait=True)`` 干等。"""
    _patch_fetch_env(monkeypatch)
    root = tmp_path / "parquet"
    root.mkdir()

    def _slow_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        time.sleep(0.4)
        return 100, []

    monkeypatch.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars",
                        _slow_fetch)

    real_log = mod.log

    def _boom(msg: str) -> None:
        if msg.startswith("["):          # 只炸循环体内的逐标的进度行
            raise RuntimeError("模拟循环体异常")
        real_log(msg)

    monkeypatch.setattr(mod, "log", _boom)

    t = time.perf_counter()
    with pytest.raises(RuntimeError, match="模拟循环体异常"):
        mod.step_fetch(root, {}, {},
                       selected=[f"{i:06d}.SZ" for i in range(1, 31)],
                       start=date(2022, 1, 1), end=date(2026, 9, 30),
                       cover_min=date(2022, 1, 5),
                       state_name=".expand_full_state.json")
    elapsed = time.perf_counter() - t

    # 30 只 × 0.4s ÷ 3 线程 ≈ 4.0s（drain 完）；取消未启动任务则 ≈ 0.4~0.8s。
    assert elapsed < 2.0, \
        f"异常收尾仍把队列 drain 完了（{elapsed:.2f}s）—— cancel_futures 未生效"


def test_fetch_loop_guards_are_explicit():
    """源码层必须留下显式护栏：BaseException + 有界收尾。

    锚点用 16 空格缩进（逐标的 except），以区别于 8 空格的循环体级 except。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "                except BaseException as e:  # noqa: BLE001" in src, \
        "逐标的异常处理退回了 except Exception ⇒ SystemExit 会再次漏网"
    assert "ex.shutdown(wait=aborted is None" in src
    assert "cancel_futures=aborted is not None" in src


def test_flush_failure_does_not_abort_run(tmp_path, monkeypatch):
    """状态文件写入失败必须被忽略 —— 续跑权威是磁盘分区，不是进度快照。

    实测 2026-10-02：一次 ``OSError: [Errno 22] Invalid argument`` 把 3 小时的
    抓取中止在 1655/3175（异常收尾已取消未启动任务，损失有限，但为一个**非必需**
    的记账写入而中断长任务毫无必要）。

    构造手法：把 ``state_name`` 指向一个**已存在的同名目录** ⇒ ``write_text`` 必抛
    ``IsADirectoryError``（OSError 子类）。
    """
    _patch_fetch_env(monkeypatch)
    root = tmp_path / "parquet"
    root.mkdir()
    (root.parent / "state_is_dir.json").mkdir()

    monkeypatch.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars",
                        lambda code, start, end, adjusts=("", "hfq"), fetcher=None:
                        (100, []))

    fs = mod.step_fetch(root, {}, {},
                        selected=["000001.SZ", "000002.SZ"],
                        start=date(2022, 1, 1), end=date(2026, 9, 30),
                        cover_min=date(2022, 1, 5),
                        state_name="state_is_dir.json")

    assert fs["done"] == 2, "状态文件写入失败不应中断抓取"
    assert sorted(fs["ok"]) == ["000001.SZ", "000002.SZ"]


def test_flush_is_throttled_and_forced_at_end():
    """节流（否则每只写整份 ok 列表 = O(N²)）+ 收尾强制落盘（否则丢末次进度）。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "if not force and now - _flush_last[0] < 5.0:" in src
    assert "_flush(force=True)" in src


def test_symbol_min_date_first_partition_equals_bruteforce():
    """对照实验：`_symbol_min_date` 只读**最早分区** == 遍历**全部分区**取 min。

    优化依据（2026-10-02 性能修复）：分区按 `year` 落盘（写入时由 `_year_of(d)` 决定），
    故该标的**全区间的最早日期必然落在最早的年份分区内**，无需逐分区读。
    原实现对每个年份分区都读一遍 parquet ⇒ 5223 只 × 2 数据集 × ~5 年 ≈ 5.2 万次读取，
    实测让分类阶段耗时 4 分钟以上。

    🔴 这是**纯优化**，必须用对照实验证明"裁前/裁后结果一致"。
    若真实数据里存在「分区年份与行内日期不一致」的坏分区，本测试会 FAIL。
    """
    root = PROJECT_ROOT / "data" / "parquet"
    if not root.is_dir():
        pytest.skip("无真实数据目录（CI 环境），对照实验跳过")

    checked = 0
    for ds in ("daily_bar", "daily_bar_hfq"):
        d = root / ds
        if not d.is_dir():
            continue
        for sym_dir in sorted(d.glob("symbol=*"))[:60]:
            files = sorted(sym_dir.glob("year=*.parquet"))
            if len(files) < 2:      # 单分区时两种算法必然同值，对照无意义
                continue
            sym = sym_dir.name.split("symbol=")[1]
            fast = mod._symbol_min_date(root, ds, sym)
            brute = min(
                (m for m in (mod._file_min_date_simple(f) for f in files) if m is not None),
                default=None,
            )
            assert fast == brute, (
                f"{ds}/{sym}: 只读首分区={fast}，全遍历取 min={brute} —— 分区年份与行内日期不一致")
            checked += 1

    assert checked > 0, "未取到「多分区」样本，对照实验无效（断言必须失败而非静默通过）"


def test_symbol_max_date_last_partition_equals_bruteforce():
    """`_symbol_max_date` 只读**最晚分区** == 遍历全部分区取 max（同款对照实验）。"""
    root = PROJECT_ROOT / "data" / "parquet"
    if not root.is_dir():
        pytest.skip("无真实数据目录（CI 环境），对照实验跳过")

    checked = 0
    for sym_dir in sorted((root / "daily_bar").glob("symbol=*"))[:60]:
        files = sorted(sym_dir.glob("year=*.parquet"))
        if len(files) < 2:
            continue
        sym = sym_dir.name.split("symbol=")[1]
        fast = mod._symbol_max_date(root, "daily_bar", sym)
        brute = max(
            (m for m in (mod._file_min_date_simple(f) for f in files) if m is not None),
            default=None,
        )
        assert fast == brute, f"{sym}: 只读末分区={fast}，全遍历取 max={brute}"
        checked += 1
    assert checked > 0, "未取到「多分区」样本，对照实验无效"


def _tail_setup(monkeypatch, max_dates: dict):
    """把 3 只标的都判为「已覆盖」，并给它们各自的 max(date)。"""
    _patch_fetch_env(monkeypatch)
    syms = sorted(max_dates)
    monkeypatch.setattr(mod, "_classify_fetch_coverage",
                        lambda *a, **k: (set(syms), [], []))
    monkeypatch.setattr(mod, "ada_list_symbols", lambda: set(syms))
    monkeypatch.setattr(mod, "_symbol_max_date", lambda r, rel, s: max_dates[s])
    calls: list[str] = []
    monkeypatch.setattr(
        "app.data.ingest.tasks.fetch_and_write_daily_bars",
        lambda code, start, end, adjusts=("", "hfq"), fetcher=None:
        (calls.append(code), (100, []))[1])
    return calls


_TAIL_DATES = {"A.SZ": date(2026, 9, 30), "B.SZ": date(2026, 9, 17),
               "C.SZ": date(2026, 9, 30)}


def test_fetch_tail_tolerance_zero_is_noop(tmp_path, monkeypatch):
    """**默认 `tail_tolerance_days=0` 必须零行为变更**：已覆盖的标的一只都不抓。"""
    calls = _tail_setup(monkeypatch, dict(_TAIL_DATES))
    root = tmp_path / "parquet"
    root.mkdir()

    fs = mod.step_fetch(root, {}, {}, selected=sorted(_TAIL_DATES),
                        start=date(2022, 1, 1), end=date(2026, 9, 30),
                        cover_min=date(2022, 1, 5), tail_tolerance_days=0,
                        state_name=".expand_full_state.json")

    assert calls == [], "默认值下不应额外抓取（零行为变更被破坏）"
    assert fs["done"] == 0


def test_fetch_tail_tolerance_pulls_stale_into_todo(tmp_path, monkeypatch):
    """尾部陈旧补充：`max(date) < end - N 天` 的**已覆盖**标的必须并入 todo。

    🔴 不做这件事的后果（实测 2026-10-02）：`_classify_fetch_coverage` 只判起点
    ⇒ 起点达标的标的**每次都被跳过** ⇒ 243 只末日停在 2026-09-17、
    且 2 只标的 2022–2025 分区的 volume 单位仍是「手」（旧口径永不重写）。
    """
    calls = _tail_setup(monkeypatch, dict(_TAIL_DATES))
    root = tmp_path / "parquet"
    root.mkdir()

    fs = mod.step_fetch(root, {}, {}, selected=sorted(_TAIL_DATES),
                        start=date(2022, 1, 1), end=date(2026, 9, 30),
                        cover_min=date(2022, 1, 5), tail_tolerance_days=7,
                        state_name=".expand_full_state.json")

    assert calls == ["B"], f"只有尾部陈旧的 B 应被补抓，实际 {calls}"
    assert fs["done"] == 1


# --------------------------------------------------------------------------- #
# 步骤列表对齐护栏（2026-10-02 事故，本仓同型缺陷**第三次**出现）
#
# 事故现场：扩容重建跑完后 `cs/` 截面镜像**停更** —— cs/daily_bar 与
# cs/daily_bar_hfq 末日停在 2026-09-28、cs/daily_bar_qfq 停在 2026-09-17，
# 而 daily_bar 已到 2026-09-30。不报错，只是 `/screener/stocks` **静默回退旧截面**。
#
# 根因：`step_rebuild` **自己硬编码了一份步骤列表 `seq`**，漏了 `build_cs_mirror`。
# `orchestrator.FULL_STEPS` 才是单一事实源，而 `step_build_cs_mirror` 的 docstring
# 早就写明「缺陷 2 起并入默认 FULL_STEPS，漏跑会让 cs/ 停更」。
#
# 历史复现三次：
#   ① `orchestrator.STEPS` 曾缺 rebuild_qfq / build_universe / build_cs_mirror 三步；
#   ② `ops.dag_rerun` 曾落到一份精简默认集；
#   ③ 本次 `step_rebuild.seq` 漏 build_cs_mirror。
# ⇒ 每次都是「有人手写了一份步骤列表，与单一事实源漂移」。以下两条测试是**唯一**
#   能在源码层拦住第四次开洞的机器判据（子序列 + 尾部完整性，缺一不可：
#   **只判子序列抓不住"漏项"**，而本次事故恰恰是漏项）。
# --------------------------------------------------------------------------- #
def _extract_rebuild_seq_steps() -> list[str]:
    """从 ``step_rebuild`` 源码提取自建 ``seq`` 的步骤名（**AST，非正则**）。

    🔴 为什么必须 AST：本次修复附带的说明注释里也含 ``("build_cs_mirror", ...)``
    样的文字，正则会把注释里的示例一并抓进来 ⇒ 判据被自己的注释污染而**恒真**。
    """
    import ast

    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "step_rebuild"),
        None,
    )
    assert fn is not None, "未找到 step_rebuild（函数被改名？）"

    assign = next(
        (n for n in ast.walk(fn)
         if isinstance(n, ast.AnnAssign)
         and isinstance(n.target, ast.Name)
         and n.target.id == "seq"),
        None,
    )
    assert assign is not None, "step_rebuild 内未找到 seq 赋值"
    assert isinstance(assign.value, ast.List), "seq 不再是字面量列表（结构被改）"

    steps: list[str] = []
    for elt in assign.value.elts:
        assert isinstance(elt, ast.Tuple) and elt.elts, f"seq 元素结构异常：{ast.dump(elt)}"
        head = elt.elts[0]
        assert isinstance(head, ast.Constant) and isinstance(head.value, str), \
            f"seq 元素首项必须是字符串字面量：{ast.dump(elt)}"
        steps.append(head.value)
    return steps


def test_rebuild_seq_is_ordered_subsequence_of_full_steps():
    """🔴 自建 ``seq`` 必须是 ``FULL_STEPS`` 的**有序子序列**。

    ① 每步都在 FULL_STEPS 内（拼写错/改名/自造步骤 → 立即失败）；
    ② 相对顺序与 FULL_STEPS 一致（乱序会破坏「同日同序」口径）。
    """
    from app.orchestrator import FULL_STEPS

    seq = _extract_rebuild_seq_steps()
    assert seq, "seq 为空 ⇒ AST 提取失败或 seq 被改结构"

    unknown = [s for s in seq if s not in FULL_STEPS]
    assert not unknown, f"seq 含 FULL_STEPS 之外的步骤：{unknown}"

    idx = [FULL_STEPS.index(s) for s in seq]
    assert idx == sorted(idx), (
        f"seq 顺序与 FULL_STEPS 不一致：seq={seq} ⇒ FULL_STEPS 下标={idx}"
    )


def test_rebuild_seq_covers_full_steps_tail():
    """🔴 ``seq`` 必须覆盖 ``FULL_STEPS`` 中 ``rebuild_qfq`` 起的**全部**尾部步骤。

    「有序子序列」判据只保证「无外来项、顺序不乱」，**漏项仍会通过** ——
    而本次事故（漏 `build_cs_mirror`）恰恰是漏项。扩容重建的语义就是
    「重算全部派生数据集」，故 FULL_STEPS 里 rebuild_qfq 之后的每一步都必须在 seq 内。

    修复路径（本判据可被修复，不是永久阻塞器）：把漏掉的步骤补进 ``seq``；
    若某步**确实**不该在扩容重建里跑，须在此显式登记豁免并写明理由。
    """
    from app.orchestrator import FULL_STEPS

    seq = set(_extract_rebuild_seq_steps())
    tail = FULL_STEPS[FULL_STEPS.index("rebuild_qfq"):]
    missing = [s for s in tail if s not in seq]
    assert not missing, (
        f"seq 漏掉 FULL_STEPS 尾部步骤 {missing} ⇒ 对应数据集会**静默停更**"
        "（实测：漏 build_cs_mirror 使 cs/ 截面镜像停在旧日期、/screener/stocks 回退旧截面）"
    )


# --------------------------------------------------------------------------- #
# 退出码必须反映重建失败（2026-10-02 事故：`launcher exit rc=0` 上报假成功）
#
# 事故：`rebuild_qfq` 抛 `PermissionError(13)` ⇒ 步骤 FAILED + 其后 5 步全部未执行，
# 但 `main()` 返回 `None` ⇒ `sys.exit(None)` = 0 ⇒ **失败被上报为成功**。
# 接力链/门禁据此认为重建已完成 —— 「静默假成功」比崩溃更危险。
# --------------------------------------------------------------------------- #
def test_failed_rebuild_steps_detects_failure():
    """有 FAILED 步骤时必须被识别出来（否则退出码恒 0）。"""
    report = {"rebuild": {"steps": {
        "rebuild_qfq": {"status": "FAILED", "error": "PermissionError: 拒绝访问。"},
        "build_universe": {"status": "OK"},
    }}}
    assert mod.failed_rebuild_steps(report) == ["rebuild_qfq"]


def test_failed_rebuild_steps_empty_on_success_and_missing():
    """全 OK / 结构缺失 都必须返回空（不得误报非零退出码）。"""
    assert mod.failed_rebuild_steps(
        {"rebuild": {"steps": {"a": {"status": "OK"}, "b": {"status": "OK"}}}}) == []
    assert mod.failed_rebuild_steps({}) == []
    assert mod.failed_rebuild_steps({"rebuild": {}}) == []
    assert mod.failed_rebuild_steps({"rebuild": {"steps": None}}) == []


def test_main_propagates_rebuild_failure_to_exit_code():
    """源码护栏：``main`` 必须把重建失败转成非零退出码。

    没有它，上面两个 helper 测试全绿也挡不住"有人把这段检查删掉"。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "failed_rebuild_steps(report)" in src, \
        "main 未调用 failed_rebuild_steps ⇒ 重建失败仍会退出 0（假成功）"
    assert "raise SystemExit(1)" in src, \
        "缺少 SystemExit(1) ⇒ 失败无法传播到调用方"
