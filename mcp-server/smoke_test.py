"""AQP MCP Server 冒烟测试脚本。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"


def main() -> None:
    print("[1/3] 启动 AQP MCP Server 子进程...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "aqp_mcp_server.server"],
        cwd=str(SRC_DIR),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    try:
        # 1. 发送 initialize 请求
        init_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        })
        proc.stdin.write(init_req + "\n")
        proc.stdin.flush()

        init_resp_line = proc.stdout.readline()
        init_resp = json.loads(init_resp_line)
        assert init_resp.get("id") == 1
        assert "serverInfo" in init_resp.get("result", {})
        print("  [OK] initialize passed:", init_resp["result"]["serverInfo"]["name"])

        # 2. 发送 tools/list 请求
        list_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        proc.stdin.write(list_req + "\n")
        proc.stdin.flush()

        list_resp_line = proc.stdout.readline()
        list_resp = json.loads(list_resp_line)
        tools = list_resp.get("result", {}).get("tools", [])
        assert len(tools) >= 5
        print(f"  [OK] tools/list passed: {len(tools)} tools registered")
        for t in tools:
            print(f"    - {t['name']}")

        print("[3/3] MCP Server smoke test passed!")
    finally:
        proc.terminate()
        proc.wait(timeout=3)


if __name__ == "__main__":
    main()
