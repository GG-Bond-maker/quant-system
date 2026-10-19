"""Alpha Quant Platform (AQP) MCP Server.

遵循 Model Context Protocol (MCP) JSON-RPC 2.0 Stdio 规范的轻量开放工具网关。
100% 本地化、零付费外部 API 依赖，无需安装任何收费依赖包。

让任何支持 MCP 的 AI 客户端（如 Cursor、Claude Desktop、Antigravity、本地 Ollama 客户端）
能够直接与本地 AQP 量化预测与投研引擎进行安全交互。

支持工具列表：
1. aqp_market_overview: 查询 A 股全市场总览、涨跌分布、两市成交额与主要指数行情
2. aqp_market_abnormal: 监控全时段异动（竞价高低开/爆量、盘中快速拉升/炸板、交易所偏离监管风险）
3. aqp_ml_prediction: 获取针对单只标的的本地 LightGBM 机器学习收益预测与 TreeSHAP 因子归因
4. aqp_stock_kline: 获取标的日K线与基础技术指标（MA/MACD/RSI）
5. aqp_capability_matrix: 查看全平台 5 类核心数据源（日K/实时/分时/财务/期货）路由状态
6. aqp_search_instrument: 模糊搜索股票、ETF、股指期货品种
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

AQP_BASE = os.environ.get("AQP_BASE", "http://127.0.0.1:8000").rstrip("/")
AQP_TOKEN = os.environ.get("AQP_TOKEN", "")

_client = httpx.Client(
    base_url=AQP_BASE,
    headers={"Authorization": f"Bearer {AQP_TOKEN}"} if AQP_TOKEN else {},
    timeout=30.0,
)


def _call_api(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """请求本地 AQP REST 接口并解包返回。"""
    try:
        resp = _client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"无法连接 AQP 本地后端 ({AQP_BASE}): {e!r}"}


# ===================== MCP 注册工具清单 =====================
TOOLS_SCHEMA = [
    {
        "name": "aqp_market_overview",
        "description": "查询 A 股全市场概览：上证/深成/创业板等核心指数行情、涨跌家数分布、两市总成交额及情绪得分。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aqp_market_abnormal",
        "description": "查询全时段量化异动监控数据。支持查看盘前集合竞价异动、盘中分时脉冲/炸板信号、或交易所严重偏离值监管风险。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeline": {
                    "type": "string",
                    "enum": ["all", "auction", "intraday", "deviation"],
                    "description": "时间线类型：all (全部) / auction (竞价) / intraday (盘中脉冲) / deviation (交易所偏离值)",
                    "default": "all",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数上限",
                    "default": 15,
                },
            },
        },
    },
    {
        "name": "aqp_ml_prediction",
        "description": "获取指定股票的本地 LightGBM 机器学习模型未来收益率预测、置信度以及 Top 5 TreeSHAP 核心因子贡献解释。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "标的代码（如 600519.SH 或 000001.SZ）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "aqp_stock_kline",
        "description": "获取标的历史日K线行情序列及均线/技术指标（默认前复权）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "标的代码（如 600519.SH）",
                },
                "limit": {
                    "type": "integer",
                    "description": "获取的历史交易日数量",
                    "default": 30,
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "aqp_capability_matrix",
        "description": "获取本地 AQP 数据源能力路由矩阵：检查日K、五档实时、分时、财务报表、期货主力 5 类核心数据路由与延迟。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aqp_search_instrument",
        "description": "模糊检索股票、ETF 与股指期货合约代码与名称。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（如 '茅台'、'600519' 或 'IF'）",
                },
            },
            "required": ["query"],
        },
    },
]


def handle_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """分发并执行工具调用。"""
    if name == "aqp_market_overview":
        res = _call_api("/api/v1/market/overview/rt")
        return json.dumps(res, ensure_ascii=False, indent=2)

    elif name == "aqp_market_abnormal":
        timeline = arguments.get("timeline", "all")
        limit = arguments.get("limit", 15)
        res = _call_api("/api/v1/market/abnormal", params={"timeline": timeline, "limit": limit})
        return json.dumps(res, ensure_ascii=False, indent=2)

    elif name == "aqp_ml_prediction":
        symbol = arguments.get("symbol", "").strip()
        if not symbol:
            return json.dumps({"error": "缺少必需参数 symbol"}, ensure_ascii=False)
        res = _call_api(f"/api/v1/stock/{symbol}/predict")
        return json.dumps(res, ensure_ascii=False, indent=2)

    elif name == "aqp_stock_kline":
        symbol = arguments.get("symbol", "").strip()
        limit = arguments.get("limit", 30)
        res = _call_api(f"/api/v1/stock/{symbol}/kline", params={"limit": limit, "adjust": "qfq"})
        return json.dumps(res, ensure_ascii=False, indent=2)

    elif name == "aqp_capability_matrix":
        res = _call_api("/api/v1/datacenter/capabilities")
        return json.dumps(res, ensure_ascii=False, indent=2)

    elif name == "aqp_search_instrument":
        q = arguments.get("query", "").strip()
        res = _call_api("/api/v1/stock/search", params={"q": q})
        return json.dumps(res, ensure_ascii=False, indent=2)

    return json.dumps({"error": f"未知的工具名称: {name}"}, ensure_ascii=False)


def run_stdio_server() -> None:
    """启动基于标准输入输出 (Stdio) 的 JSON-RPC 2.0 MCP 事件循环。"""
    # 强制 UTF-8 编码传输
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # 1. 握手协议 (initialize)
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "aqp-mcp-server",
                        "version": "1.0.0",
                    },
                    "capabilities": {
                        "tools": {},
                    },
                },
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        # 2. 握手就绪确认
        elif method == "notifications/initialized":
            continue

        # 3. 列出工具清单 (tools/list)
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS_SCHEMA,
                },
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        # 4. 执行工具调用 (tools/call)
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            call_result = handle_tool_call(tool_name, tool_args)
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": call_result,
                        }
                    ],
                },
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        # 5. 心跳探测 (ping)
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
