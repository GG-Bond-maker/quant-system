# 前端闭环联动任务

先读本文件与progress.md。用户要求修复审查报告全部缺口，不含自然语言问答。资金/时点后端已初步实现，17个核心测试通过；你负责frontend/全部文件，不修改backend/。

任务：
1. OrderDesk增加独立策略账户选择/创建/共同日期收益比较，沿用现有样式tokens；使用backend/app/api/v1/desk.py已有GET/POST /accounts、GET /accounts/compare?accounts=a,b&benchmark=000300.SH、GET /account?account_id=...、GET /orders?account_id=...、POST /orders body account_id。所有交易/取消/条件单/网格/TCA操作必须跟随选中的账户，禁止串账户。核对后端尚缺的参数，发消息给主代理补齐。
2. 现有限价priceMode/limitPrice已加入单笔请求，补真实对手价缺失时不造默认价格，切标的/方向清理过期限价；重复网络请求使用稳定client_order_id，成功后新意图换ID。批量草稿重试使用每条稳定ID避免部分成功重复报单。
3. 来源贯通：PaperOrder支持strategy_version/model_version/signal_id/backtest_id/run_id；OrderDesk批量草稿和URL带入来源，持久下单并在订单/对照中显示可追踪信息。新backtest成功响应将有backtest_id/run_id/strategy_version/signal_sources[{symbol,date,model_version,signal_id,run_id,feature_version,label_horizon}]；请在ML回测结果中提供进入交易台的草稿入口，初始只做可审核草稿，不自动下单。Report SignalPreviewCard现有草稿也保留真实来源，无法获得的不编造。
4. API/types新增校准confidence是P(model_target>0)，预测区间、coverage_level与信号ID等由predict API返回；现有页面明确展示预测期限、校准缺失/区间，不误称绝对上涨概率。
5. 修复前端真实行为问题并新增 meaningful tests（先红后绿），运行相关vitest、npm run build、前端四门禁。不要以源码字面量测试替代交互验证。

约束：不重做UI系统、不引入库、不弱化测试；不清理别人变更，不提交git、不部署。仅修改frontend/与本任务报告 frontend-result.md。后端账户初始现金不可重置，account_id字符串regex[A-Za-z0-9_-]。老mock未提供新方法时可以适配测试或component隔离，但生产不可静默吞掉真实接口错误。
