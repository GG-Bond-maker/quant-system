"""A 股交易费用口径的**唯一事实来源**（审计 R2 / §6.4 / §8.2 第 18 项）。

本模块只回答一个问题：**数字是多少**。此前同一批费率被硬编码在 8 处
（``backtest/broker.py``、``backtest/ma_cross.py``、``backtest/engine.py``、
``backtest/strategy_base.py``、``trading/paper.py``、``domain/portfolio.py``、
``api/v1/backtest.py``、``db/models.py``），改一处漏一处；审计 B5-10 记录的
「ETF 豁免 4 处免 / 1 处收」正是这种分叉的结果。

**分档口径（谁收、按成交日分段）不在本模块重复实现**，而由
:mod:`app.domain.a_share_rules` 的既有函数提供并被复用：
``stamp_duty_rate`` / ``transfer_fee_rate`` / ``effective_stamp_duty`` /
``effective_transfer_fee``（它们从本模块取数字）。即：

- 本模块 = 费率的**数字**单一来源；
- ``a_share_rules`` = **品种判定与历史分段**单一来源。

本模块为纯常量、无 IO、无外部依赖（可被 ``app.db`` / ``app.api`` / ``app.backtest``
任一层安全导入）。
"""
from __future__ import annotations

from datetime import date

# 佣金：万 3（0.0003），买卖双边收取；单笔最低 5 元。
COMMISSION_RATE_DEFAULT = 0.0003
COMMISSION_MIN = 5.0

# 印花税：股票**卖出**单边征收；2023-08-28 起由 1‰ 减半至 0.5‰。
# 场内基金（ETF/LOF/封闭式）免征 —— 免征与分段判定见
# ``a_share_rules.effective_stamp_duty``（本模块只提供数字）。
STAMP_DUTY_STOCK_RATE = 0.0005
STAMP_DUTY_STOCK_RATE_LEGACY = 0.001
STAMP_DUTY_CUT_DATE = date(2023, 8, 28)

# 过户费：2022-04-29 起沪深统一 0.01‰（0.00001），股票双边收取；场内基金不收。
TRANSFER_FEE_RATE = 0.00001