"""把 AOF 恢复出来的 ETF 快照存档**合并 + 净化**后写回 Redis。

用法（在 ``backend/`` 下执行）：

    # 1) 先看会写成什么（不写 Redis），并把当前值备份下来
    .venv/Scripts/python.exe scripts/restore_etf_snap_history.py \
        --recovered ../docs/audit/2026-09-29/etf_snap_history_recovered_8.json \
        --backup    ../docs/audit/2026-09-29/etf_snap_history_before_restore.json

    # 2) 确认无误后真正写回
    ... 同上，再加 --apply

设计要点：

- **净化规则不在这里**：全部在 ``app/data/etf_snapshot_sanitize.py``（可被测试
  锁住、读路径也复用），本脚本只负责「读现有 → 备份 → 合并 → 写回 → 打印」；
- **同日以现有行为准**（``merge_etf_snapshots``）：现有行是误删后重写出来的、
  带新口径字段，比 AOF 挖出来的旧行更新；
- **默认 dry-run**：必须显式 ``--apply`` 才写 Redis，避免手滑覆盖；
- **写回前自检**：输出里若还残留 ``net_inflow_yi == 0`` 则直接拒绝写入
  （旧口径假零绝不允许落库）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import orjson  # noqa: E402

from app.cache.keys import k_etf_snap_history  # noqa: E402
from app.cache.redis_client import RedisClient  # noqa: E402
from app.data.etf_snapshot_sanitize import (  # noqa: E402
    FLOW_FIELD, SNAP_TTL_SECONDS, build_restored_archive,
)

SNAP_KEY = k_etf_snap_history()


def _row_line(snap: dict[str, Any]) -> str:
    """逐行摘要：date / net_inflow_yi / flow.status（+ 恢复标记）。"""
    flow = snap.get("flow") if isinstance(snap.get("flow"), dict) else {}
    status = flow.get("status") or ("—" if snap.get(FLOW_FIELD) is not None else "无 flow 子对象")
    caliber = snap.get("net_inflow_caliber") or "—"
    flag = "recovered" if snap.get("recovered") else ("existing" if snap.get("archived_post_close") else "—")
    return (f"  {str(snap.get('date')):<12} net_inflow_yi={str(snap.get(FLOW_FIELD)):<8}"
            f" flow.status={str(status):<14} caliber={str(caliber):<18} {flag}")


def _has_fake_zero(rows: list[dict]) -> list[str]:
    """检出仍为 0 的假零行（真写回前必须为空）。"""
    bad: list[str] = []
    for snap in rows:
        v = snap.get(FLOW_FIELD)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0:
            bad.append(str(snap.get("date")))
    return bad


async def _read_existing() -> tuple[bytes | None, list[dict]]:
    raw = await RedisClient.get(SNAP_KEY)
    if not raw:
        return None, []
    try:
        data = orjson.loads(raw)
    except Exception as exc:  # noqa: BLE001 存档损坏 ⇒ 由调用方决定是否继续
        raise SystemExit(f"现有存档解析失败，已中止（未写回）: {exc!r}") from exc
    return raw, [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复并净化 ETF 快照存档")
    parser.add_argument("--recovered", required=True, type=Path,
                        help="AOF 恢复出来的存档 JSON 路径")
    parser.add_argument("--backup", type=Path, default=None,
                        help="把写回前的当前值备份到该路径")
    parser.add_argument("--recovered-at", default=None,
                        help="恢复时间戳（默认取当前时间）")
    parser.add_argument("--apply", action="store_true",
                        help="真正写回 Redis；默认只 dry-run 打印")
    args = parser.parse_args(argv)

    recovered_path: Path = args.recovered
    if not recovered_path.exists():
        print(f"恢复源文件不存在: {recovered_path}")
        return 2
    recovered_raw = json.loads(recovered_path.read_text(encoding="utf-8"))
    recovered: list[dict] = [d for d in recovered_raw if isinstance(d, dict)]

    raw_before, existing = await _read_existing()
    print(f"目标键: {SNAP_KEY}（TTL {SNAP_TTL_SECONDS}s）")
    print(f"现有存档: {len(existing)} 条；恢复源: {len(recovered)} 条")

    if args.backup is not None:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        payload = raw_before if raw_before is not None else b"[]"
        args.backup.write_bytes(payload)
        print(f"已备份当前值 -> {args.backup}（{len(payload)} 字节）")

    merged = build_restored_archive(existing, recovered, recovered_at=args.recovered_at)
    print(f"合并+净化后: {len(merged)} 条")
    for snap in merged:
        print(_row_line(snap))

    bad = _has_fake_zero(merged)
    if bad:
        print(f"REFUSE: 输出仍含旧口径假零（{bad}），拒绝写回")
        return 3

    if not args.apply:
        print("DRY-RUN：未写回 Redis（加 --apply 才写）")
        return 0

    await RedisClient.set(SNAP_KEY, orjson.dumps(merged), ex=SNAP_TTL_SECONDS)
    check_raw = await RedisClient.get(SNAP_KEY)
    check = orjson.loads(check_raw) if check_raw else []
    same = check == merged
    fake = _has_fake_zero([d for d in check if isinstance(d, dict)])
    print(f"已写回：{len(check)} 条；读回一致={same}；残留假零={fake or '无'}")
    print(f"写入时间: {datetime.now().isoformat(timespec='seconds')}")
    return 0 if (same and not fake) else 4


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
