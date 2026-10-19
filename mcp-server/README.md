# AQP MCP Server · 本地量化预测与投研工具箱

**Model Context Protocol (MCP)** 官方标准服务实现。
将 AQP 的本地机器学习预测、全时段异动监控、行情指标与数据湖仓开放给任意 AI 客户端（如 Claude Desktop、Cursor、Antigravity、本地 Ollama 客户端）。

- **100% 本地与免费**：不依赖任何第三方收费大模型 API，纯本地数据与计算。
- **直连 AQP 预测核心**：支持直接查询本地训练的 LightGBM 未来收益率预测与 TreeSHAP 因子归因。

---

## 快速配置

### 1. Cursor 配置 (`.cursor/mcp.json` 或设置界面)

```json
{
  "mcpServers": {
    "aqp": {
      "command": "python",
      "args": ["-m", "aqp_mcp_server.server"],
      "cwd": "D:\\Python_Project\\Alpha Quant Platform\\mcp-server\\src",
      "env": {
        "AQP_BASE": "http://127.0.0.1:8000"
      }
    }
  }
}
```

### 2. Claude Desktop 配置 (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "aqp-quant": {
      "command": "python",
      "args": ["-m", "aqp_mcp_server.server"],
      "cwd": "D:\\Python_Project\\Alpha Quant Platform\\mcp-server\\src",
      "env": {
        "AQP_BASE": "http://127.0.0.1:8000"
      }
    }
  }
}
```

---

## 工具清单

| 工具名称 | 说明 | 适用场景 |
| :--- | :--- | :--- |
| `aqp_market_overview` | 查询 A 股全市场总览、涨跌分布、总成交额与情绪指标 | 询问“今日 A 股行情如何”、“市场赚钱效应如何” |
| `aqp_market_abnormal` | 监控全时段异动（竞价高低开/爆量、分时脉冲/炸板、交易所偏离值监管） | 询问“今天集合竞价有哪些异动标的”、“有没有触及严重偏离监管红线的股票” |
| `aqp_ml_prediction` | 查询单只标的的本地 LightGBM 预测收益率、置信度与 TreeSHAP 因子贡献 | 询问“分析一下贵州茅台的机器学习预测和主要驱动因子” |
| `aqp_stock_kline` | 获取前复权历史日 K 线数据及 MA/MACD/RSI 指标 | 询问“查询 600519 最近 30 天的 K 线与技术指标” |
| `aqp_capability_matrix` | 检查 5 类数据源能力（日K/五档/分时/财务/期货）路由状态与延迟 | 检查系统运行状态与数据链路健康度 |
| `aqp_search_instrument` | 模糊搜索股票代码、ETF 或期货品种代码 | 模糊查找代码（如搜索“宁德”或“IF”） |

---

## 冒烟测试

```bash
cd mcp-server
python smoke_test.py
```
