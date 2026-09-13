"""任务调度层（Task 14 整改 A-P1-6a：自 core 迁出）。

core 层自声明不依赖 api/ml/data；晚间例行调度需要编排 data+ml+db，
故下沉到本包，解 core→api/ml/data 的反向依赖环。
"""
