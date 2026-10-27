# 交易修复复核与完善

先读本文件、progress.md、原审查报告。你拥有backend/app/trading/*.py（paper/conditional/gateway/comparison）及交易相关tests：test_closed_loop_trading.py、test_paper_position_guard.py、test_production.py、test_trading_gateway.py、test_stage1_stage2_stage3.py；勿改api/、db/、ml/、frontend/（如需改发主代理消息）。

现状：paper大幅重构新增SQLite BEGIN IMMEDIATE写锁、reservations、account_id、limit、来源、幂等、账本replay；9新测试通过。broker.py卖出重复impact已修。db模型已加PaperAccount和订单/成交account与来源字段，默认paper_default。conditional已拒单REJECTED、收益按fill推导，接口由主代理处理。

任务：独立检查并修复资金/持仓/T+1/时点/费用/幂等/限价/部分成交/重启/账户隔离真实正确性，先用失败测试证明发现再修。重点查不同日期回放、在途卖单、费用/冲击守恒、限价/市价股数、未来行情不得用于决策/估值/成交、断档与行情缺失不应显示完整权益、Kill Switch对多账户已有单的语义。不能只是为了让旧测试绿而重新允许未来数据。

旧交易定向回归日志docs/fix-2026-10-27/trading-initial.log：37passed/8failed；多个夹具用未来bars并在今天下单却期望立即有D+1成交。修正夹具为真实过去成交窗口，或明确验证待成交；条件/网格原测试无行情也假定触发列表非空，需要seed真实隔离行情并验证真实订单/成交/收益。新语义SUBMITTED->PART_FILLED/FILLED/REJECTED/CANCELLED，不是虚构TRIGGERED成功。

对照comparison要求相同日期窗口净值收益，基准同窗真实价格缺失为空。网关不应默认price=10或假支持期货卖空。

不改生产数据，不提交git；只用隔离测试，报告trading-result.md含修改、红绿测试证据、剩余具体风险。类型/ruff顺手修你拥有文件，主代理做最后全量。若发现API/模型必需契约变动，先发简短消息再继续独立部分。
