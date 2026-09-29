"""db：SQLAlchemy 会话 + ORM 模型 + 初始化。

SQLite busy-timeout 策略（2026-09-29 P1）：所有 ``sqlite3.connect`` 一律显式
``timeout=30``（busy_timeout）；唯一例外是 ``app/main.py`` 的就绪探针 ``timeout=2``
（快速失败）。原因：``PRAGMA busy_timeout`` 是**连接级**设置，不会由 ``init_db.py``
的 DB 级 PRAGMA 继承 —— 默认 5s 在长事务/长 parquet 写入下会让状态写入**静默失败**，
是"非终态卡死"（KV 卡 ``running``、``data_jobs`` 卡 ``RUNNING``）的成因之一。
"""
