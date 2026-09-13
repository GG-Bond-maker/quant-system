"""业务服务层（Task 16 整改 A-P1-7）。

API 路由层只保留"参数校验 + 组装响应"；有状态的业务编排（同步状态机、
外部数据源聚合）下沉到本包，解除 api 层内嵌业务逻辑的耦合。

依赖方向（自上而下）：api → services → {data, ml, db, domain} → core。
services 禁止反向 import app.api（与 core 同等纪律）。
"""
