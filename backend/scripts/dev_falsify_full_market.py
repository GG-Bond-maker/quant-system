"""证伪脚本：确认新增测试真的能捕获它们声称要防的缺陷。

对 ``scripts/expand_universe_2500.py`` 依次注入 8 个缺陷，每个都应让**指定**
测试 FAIL；任何一组"注入后仍 PASS"都说明该测试是空转的。

🔴 **注入目标是副本，不是原文件**（2026-10-02 修正）：
早期版本直接对原文件做「读→注入→跑测试→还原」，但**宿主会对同一命令重复执行**，
两次运行交错时第二次会把"已被注入的文件"当成 ``original`` 读入，于是还原动作
把缺陷**写回生产脚本**（实测残留 ``ex.shutdown(wait=True)``）。
现在：原文件**只读**，副本路径含 PID（两次并发运行互不干扰），收尾校验原文件 SHA。

用法（cd backend）：
    ./.venv/Scripts/python.exe scripts/dev_falsify_full_market.py
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
TARGET = PROJECT / "scripts" / "expand_universe_2500.py"
WORKDIR = BACKEND / ".tmp_falsify"
TEST = "tests/test_expand_universe_full_market.py"

CASES: list[tuple[str, str, str, str]] = [
    (
        "缺陷1：忽略 universe_name，硬编码回 universe_2500.json",
        "    uni_path = root.parent / universe_name",
        '    uni_path = root.parent / "universe_2500.json"',
        "test_select_honours_custom_universe_name",
    ),
    (
        "缺陷2：不登记北交所 unavailable 段",
        '        "unavailable": {',
        '        "_unavailable_disabled": {',
        "test_select_registers_bse_as_explicitly_unavailable",
    ),
    (
        "缺陷3：step_fetch 丢弃 cover_min（起点与阈值脱钩）",
        "    covered, uncovered_raw, uncovered_hfq = _classify_fetch_coverage(\n"
        "        root, selected, have, cover_min)",
        "    covered, uncovered_raw, uncovered_hfq = _classify_fetch_coverage(\n"
        "        root, selected, have)",
        "test_fetch_forwards_cover_min_and_state_name",
    ),
    (
        "缺陷4：逐标的异常处理退回 except Exception（SystemExit 漏网 ⇒ 静默失活）",
        "                except BaseException as e:  # noqa: BLE001",
        "                except Exception as e:  # noqa: BLE001",
        "test_fetch_survives_baseexception_from_worker",
    ),
    (
        "缺陷5：异常收尾改回 shutdown(wait=True)（队列被 drain 数小时）",
        "            ex.shutdown(wait=aborted is None, cancel_futures=aborted is not None)",
        "            ex.shutdown(wait=True)",
        "test_fetch_aborts_without_draining_pool",
    ),
    (
        "缺陷6：_flush 不再容忍 OSError（一次记账写失败即中止 3 小时长任务）",
        "        except OSError as e:\n"
        "            log(f\"⚠️ 状态文件写入失败（已忽略；续跑以磁盘分区为准）：\"",
        "        except ZeroDivisionError as e:\n"
        "            log(f\"⚠️ 状态文件写入失败（已忽略；续跑以磁盘分区为准）：\"",
        "test_flush_failure_does_not_abort_run",
    ),
    (
        "缺陷7：去掉收尾强制落盘（末次进度丢失）",
        "    _flush(force=True)\n    manifest_invalidate()",
        "    manifest_invalidate()",
        "test_flush_is_throttled_and_forced_at_end",
    ),
    (
        "缺陷8：尾部陈旧判据取反（陈旧标的不会被并入 todo ⇒ 旧分区永不重写）",
        "            if (lambda d: d is not None and d < _floor)(",
        "            if (lambda d: d is not None and d > _floor)(",
        "test_fetch_tail_tolerance_pulls_stale_into_todo",
    ),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    sha0 = _sha(TARGET)

    WORKDIR.mkdir(exist_ok=True)
    # PID 入名：宿主重复执行时两次运行的副本互不覆盖
    copy = WORKDIR / f"expand_universe_2500_{os.getpid()}.py"
    env = dict(os.environ, AQP_EXPAND_SCRIPT=str(copy))

    results: list[tuple[str, str, bool]] = []
    try:
        for label, old, new, test_name in CASES:
            n = original.count(old)
            if n != 1:
                print(f"❌ 锚点不唯一（count={n}）：{old!r}", flush=True)
                results.append((label, test_name, False))
                continue
            copy.write_text(original.replace(old, new), encoding="utf-8")
            cp = subprocess.run(
                [sys.executable, "-m", "pytest", TEST, "-q", "--tb=no",
                 "-p", "no:randomly", "-k", test_name],
                cwd=BACKEND, capture_output=True, text=True, env=env)
            failed = cp.returncode != 0
            results.append((label, test_name, failed))
            print(f"{'✅ 捕获' if failed else '❌ 未捕获'} | {label} -> {test_name}"
                  f"（pytest rc={cp.returncode}）", flush=True)
    finally:
        try:
            copy.unlink(missing_ok=True)
        except OSError:
            pass

    # 🔴 原文件必须逐字节未变 —— 注入目标已改为副本，这里一旦不一致就是真缺陷
    sha1 = _sha(TARGET)
    if sha0 != sha1:
        print("\n❌ 原文件被改动（证伪机制本身有 bug，禁止提交）")
        return 2
    print("\n原文件校验：OK 未改动（注入只作用于副本）")

    bad = [r for r in results if not r[2]]
    print(f"证伪结果：{len(results) - len(bad)}/{len(results)} 组被捕获")
    if bad:
        for label, _, _ in bad:
            print(f"  ⚠️ 空转测试：{label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
