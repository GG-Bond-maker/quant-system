"""模型 promote CLI（第五阶段：显式提升，禁止训练后自动覆盖生产模型）。

流程： train -> evaluate -> candidate -> **promote（本脚本）** -> production

    python scripts/promote_model.py --list
    python scripts/promote_model.py --version 20260830_120000
    python scripts/promote_model.py --auto            # 自动挑选最优 candidate
    python scripts/promote_model.py --current         # 查看当前生产模型
    python scripts/promote_model.py --version 20260905_181337 --rollback \
        --reason "新版生产模型 IC 劣化/线上异常"      # 回滚通道（审计 P1-6）

promote 决策只基于 **validation** 指标（valid_rank_ic / valid_icir / valid_rmse）；
test 指标只写入注册表供审计，不参与决策（用 test 调参 = test contamination）。

--rollback 与普通 promote 的区别：回滚的目标必须是**曾被提升过**的版本，其相对门禁
（"必须不劣于当前生产"）按语义不适用，因此跳过并**披露**（checks.rollback）；从未
过门禁的候选不得借 --rollback 绕过质量门槛。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


from app.core.logging import setup_logging  # noqa: E402
from app.ml.registry import (  # noqa: E402
    DEFAULT_PROMOTE_POLICY, ModelRegistryError, count_production, get_production, list_models, promote_model, rollback_model,
)


def _print_table(rows: list[dict], current_version: str | None) -> None:
    hdr = f"{'version':<26}{'status':<12}{'vRankIC':>10}{'vICIR':>9}{'vRMSE':>9}{'tRankIC':>10}  prod"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        mark = "  <== PROD" if r["version"] == current_version else ""
        print(f"{r['version']:<26}{str(r.get('status') or ''):<12}"
              f"{_fmt(r.get('valid_rank_ic')):>10}{_fmt(r.get('valid_icir')):>9}"
              f"{_fmt(r.get('valid_rmse')):>9}{_fmt(r.get('test_rank_ic')):>10}{mark}")


def _fmt(v: object) -> str:
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return "-" if f != f else f"{f:.4f}"


def main() -> None:
    ap = argparse.ArgumentParser(prog="promote_model.py")
    ap.add_argument("--list", action="store_true", help="列出候选模型")
    ap.add_argument("--current", action="store_true", help="显示当前生产模型")
    ap.add_argument("--version", default=None, help="要提升的 version")
    ap.add_argument("--auto", action="store_true",
                    help="自动挑选 valid_rank_ic 最高的 candidate 并尝试提升")
    ap.add_argument("--by", default="cli")
    ap.add_argument("--reason", default="")
    ap.add_argument("--rollback", action="store_true",
                    help="回滚到曾提升过的版本（跳过相对门禁但强制要求 --reason）")
    ap.add_argument("--json", default=None, help="导出决策到 JSON")
    args = ap.parse_args()

    setup_logging()
    cur = get_production()
    cur_v = cur["version"] if cur else None

    if args.current:
        if cur is None:
            print("当前无生产模型（推理会明确报错，不会回退实验模型）")
        else:
            print(json.dumps({k: str(v) for k, v in cur.items()},
                             ensure_ascii=False, indent=2))
        return

    if args.list or not (args.version or args.auto):
        rows = list_models(limit=100)
        if not rows:
            print("model_registry 为空")
            return
        _print_table(rows, cur_v)
        print(f"\n生产模型数量 = {count_production()}（不变量：必须 <= 1）")
        print(f"promote 门槛：{DEFAULT_PROMOTE_POLICY}")
        return

    if args.rollback:
        try:
            res = rollback_model("lgbm_v1", args.version, by=args.by,
                                 reason=args.reason)
        except ModelRegistryError as e:
            print(f"回滚失败：{e}")
            sys.exit(1)
        print()
        print("=" * 66)
        print(f"回滚成功: {res['previous_production']} -> {res['version']}")
        print(f"原因：{res['reason']}")
        print(f"  [INFO] rollback: {res.get('checks', {}).get('rollback')}")
        if args.json:
            Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2,
                                                  default=str), encoding="utf-8")
            print(f"\n决策已导出：{args.json}")
        sys.exit(0)

    if args.auto:
        rows = [r for r in list_models(limit=200)
                if (r.get("status") or "candidate") == "candidate"
                and r.get("valid_rank_ic") is not None
                and r.get("valid_icir") is not None]
        if not rows:
            print("没有可评估的 candidate")
            return
        rows.sort(key=lambda r: (r["valid_rank_ic"] or -9, r["valid_icir"] or -9),
                  reverse=True)
        target = rows[0]
        print(f"自动挑选最优 candidate: {target['version']} "
              f"(valid_rank_ic={target['valid_rank_ic']:.4f})")
        args.version = target["version"]

    try:
        res = promote_model("lgbm_v1", args.version, by=args.by, reason=args.reason)
    except ModelRegistryError as e:
        print(f"promote 失败：{e}")
        sys.exit(1)

    print()
    print("=" * 66)
    print(f"promote {'成功' if res['promoted'] else '被拒绝'}: {args.version}")
    print(f"原因：{res['reason']}")
    for k, v in res.get("checks", {}).items():
        # pass=None 表示"仅披露/未参与判决"（如跨 regime 判据默认只披露），
        # 不能当成 FAIL 打印——否则运维会把披露项误读为拒绝原因。
        mark = {True: "PASS", False: "FAIL"}.get(v.get("pass"), "INFO")
        print(f"  [{mark:<4}] {k}: {v}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2,
                                              default=str), encoding="utf-8")
        print(f"\n决策已导出：{args.json}")
    sys.exit(0 if res["promoted"] else 2)


if __name__ == "__main__":
    main()
