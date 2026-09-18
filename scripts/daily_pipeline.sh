#!/usr/bin/env bash
# AQP 每日盘后流水线（P1-1）：走 app.orchestrator CLI（FULL_STEPS 全量：
# update_daily → validate → rebuild_qfq → build_universe → build_features → infer
# → screener_dump → build_cs_mirror）。⚠️ 入口在 app.orchestrator，不在 app.data.pipeline。
# 用法: bash scripts/daily_pipeline.sh [--date YYYY-MM-DD] [--codes 600519,000001] [--dry-run]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/backend/.venv/Scripts/python.exe"
[ -f "$PY" ] || PY="$ROOT/backend/.venv/bin/python"
[ -f "$PY" ] || { echo "❌ 未找到后端 venv"; exit 1; }
cd "$ROOT/backend" && exec "$PY" -m app.orchestrator "$@"
