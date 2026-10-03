"""契约测试证伪：确认 ``/etf/*`` 与 ``/desk/*`` 的字段集合断言**不是恒真**。

纪律：「测试通过」不是证据，「注入 bug 后测试失败」才是。
本脚本对生产路由文件依次注入 4 个"字段形状漂移"缺陷，每个都应让**指定**契约测试 FAIL。

## 🔴 为什么这里必须**就地**注入（而不是像扩容脚本那样注入副本）

`dev_falsify_full_market.py` 打的是「按**路径**加载的独立脚本」（可用 `AQP_EXPAND_SCRIPT`
把测试重定向到副本）。而本脚本打的是 `app.api.v1.desk` / `app.api.v1.etf` —— 测试按
**模块名**导入，副本无法顶替。因此只能就地注入，并用三重防护兜住风险：

1. **`mkdir` 原子互斥锁** —— 宿主会对同一条命令**重复执行**，两次注入-还原交错会把缺陷
   写回生产文件（2026-10-02 实测在 `scripts/expand_universe_2500.py` 上真的发生过一次）。
   拿不到锁**立即退出**，绝不并发改写。
2. **还原只用内存里读到的原文**，不依赖任何可能被污染的外部备份。
3. **收尾逐个校验 SHA**，不一致即 `return 2`（禁止提交）。

用法（cd backend）：`./.venv/Scripts/python.exe scripts/dev_falsify_contracts.py`
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LOCK_DIR = BACKEND / ".falsify_contracts.lock"

CASES: list[tuple[str, str, str, str, str]] = [
    (
        "缺陷1：POST /desk/kill-switch 返回读形状（pending_orders 而非 cancelled_orders）",
        "app/api/v1/desk.py",
        '        return {"kill_switch": req.active, "cancelled_orders": n_cancelled}',
        '        return {"kill_switch": req.active, "pending_orders": n_cancelled}',
        "tests/test_desk_payload_contract.py::test_kill_switch_post_shape_differs_from_get",
    ),
    (
        "缺陷2：/etf/scale 丢掉作用域回显（打破「前端不得写死」的 E-3 契约）",
        "app/api/v1/etf.py",
        '        return {"period": period, "sample_size": len(sample), "points": scale,\n'
        '                "note": "估算口径：最新份额 × 历史收盘价，非基金公司披露规模",\n'
        '                "scope": _SCALE_SCOPE, "scope_note": _SCALE_SCOPE_NOTE}',
        '        return {"period": period, "sample_size": len(sample), "points": scale,\n'
        '                "note": "估算口径：最新份额 × 历史收盘价，非基金公司披露规模",\n'
        '                "scope": _SCALE_SCOPE}',
        "tests/test_etf_payload_contract.py::test_scale_ok_field_set_and_scope_echo",
    ),
    (
        "缺陷3：分区裁剪丢掉前瞻窗口（年末样本会被 drop_nulls 静默丢弃）",
        "app/api/v1/research.py",
        "    return pred_min.year, (pred_max + timedelta(days=fwd_days)).year",
        "    return pred_min.year, pred_max.year",
        "tests/test_partition_pruning_contract.py"
        "::TestBarYearWindow::test_年末边界必须外扩到次年",
    ),
    (
        "缺陷4：裁剪条件失效（等价于无条件 glob 全部年份 = 10 倍成本）",
        "app/api/v1/research.py",
        "    return [f for f in files\n"
        "            if y_from <= int(f.name.split(\"year=\")[1][:4]) <= y_to]",
        "    return [f for f in files\n"
        "            if True]",
        "tests/test_partition_pruning_contract.py"
        "::TestPruneBarFiles::test_对照_裁剪确实减少了分区数",
    ),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    # ① 互斥锁：原子 mkdir
    try:
        LOCK_DIR.mkdir()
    except FileExistsError:
        print(f"❌ 已存在 {LOCK_DIR.name}（另一次证伪运行中，或上次异常退出）。\n"
              "   本脚本会就地改写生产源文件，故拒绝并发；确认无运行后手工删除该目录再重试。")
        return 3

    originals: dict[str, str] = {}
    before: dict[str, str] = {}
    results: list[tuple[str, bool]] = []
    try:
        for _, rel, _, _, _ in CASES:
            p = BACKEND / rel
            originals[rel] = p.read_text(encoding="utf-8")
            before[rel] = _sha(p)

        for label, rel, old, new, node in CASES:
            orig = originals[rel]
            n = orig.count(old)
            if n != 1:
                print(f"❌ 锚点不唯一（count={n}）：{rel} :: {old[:60]!r}")
                results.append((label, False))
                continue

            (BACKEND / rel).write_text(orig.replace(old, new), encoding="utf-8")
            cp = subprocess.run(
                [sys.executable, "-m", "pytest", node, "-q", "--tb=no", "-p", "no:randomly"],
                cwd=BACKEND, capture_output=True, text=True)
            failed = cp.returncode != 0
            results.append((label, failed))
            print(f"{'✅ 捕获' if failed else '❌ 未捕获'} | {label}"
                  f"（pytest rc={cp.returncode}）", flush=True)

            (BACKEND / rel).write_text(orig, encoding="utf-8")   # 立即还原
    finally:
        # ② 双保险：无论怎么退出，都把内存原文写回
        for rel, orig in originals.items():
            try:
                (BACKEND / rel).write_text(orig, encoding="utf-8")
            except OSError:
                pass
        try:
            LOCK_DIR.rmdir()
        except OSError:
            pass

    # ③ 原文件必须逐字节还原
    drifted = [rel for rel, s in before.items() if _sha(BACKEND / rel) != s]
    if drifted:
        print(f"\n❌ 源文件未还原（证伪机制本身有 bug，禁止提交）：{', '.join(drifted)}")
        return 2
    print("\n源文件校验：OK 全部逐字节还原")

    bad = [r for r in results if not r[1]]
    print(f"证伪结果：{len(results) - len(bad)}/{len(results)} 组被捕获")
    for label, _ in bad:
        print(f"  ⚠️ 空转测试：{label}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
