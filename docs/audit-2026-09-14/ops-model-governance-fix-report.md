# 可观测性与模型治理 P1 修复报告

## 修复范围

### 1. Prometheus 指标基数与访问策略

- HTTP 请求指标的 `endpoint` 标签不再使用原始 URL；改为 Starlette 已匹配路由的模板路径，例如 `/api/v1/stock/{symbol}/profile`。
- 未匹配路由或框架异常统一记录为 `/unmatched`，避免恶意或错误路径制造无限时序。
- 新增 `METRICS_REQUIRE_AUTH` 配置：未设置时生产环境默认要求现有 Bearer 认证，开发与测试保持开放，避免破坏本地调试和默认 Prometheus 采集；部署方可显式覆盖。

### 2. 假 API Key 功能

- 设置页移除 API Key 列表、生成按钮、明文展示弹窗及前端调用。
- 后端保留 `/api/v1/settings/apikeys/rotate` 兼容路由，但固定返回“API Key 功能未启用”，不再生成或持久化任何不可认证的伪凭证。
- 真正启用前必须实现哈希化安全存储、撤销机制、身份验证依赖和审计日志。

### 3. 研究页模型治理

- `/research/feature-importance` 从 `model_registry.is_production=1` 的 `model_path` 读取模型，不再拼接 `models/exp/` 路径。
- 缺少生产登记、模型文件缺失、或生产模型不是 LightGBM 时返回明确业务错误；不会回退实验副本。

## 验证

- 编译检查：修改模块与新增测试均通过 `compileall`。
- 回归测试：`tests/test_ops_model_governance.py`、`tests/test_p3_security.py`、`tests/test_research.py`。
- 新测试覆盖路由模板低基数、生产 metrics 鉴权、禁用 API Key、registry 模型路径解析和缺失产物拒绝。

## 遗留事项

生产环境如需 Prometheus 无交互抓取，应创建受限服务账号/JWT 或由反向代理注入 Bearer 凭据，并显式配置 `METRICS_REQUIRE_AUTH`；不得恢复匿名公网暴露。