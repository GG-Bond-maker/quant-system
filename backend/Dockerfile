# ============================================================
# AQP Backend Dockerfile（多阶段：TA-Lib 编译 + 运行）
# 构建上下文 = 仓库根：docker build -f backend/Dockerfile .
# ============================================================

FROM python:3.11-slim AS builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential wget libgomp1 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv $VIRTUAL_ENV \
 && pip install --no-cache-dir --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

# ================= runtime =================
FROM python:3.11-slim AS runtime

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_HOME=/app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libgomp1 tzdata \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR $APP_HOME
COPY backend/app        $APP_HOME/app
COPY backend/scripts    $APP_HOME/scripts
COPY backend/pytest.ini $APP_HOME/pytest.ini
COPY backend/tests      $APP_HOME/tests

RUN mkdir -p $APP_HOME/data/parquet $APP_HOME/data/models $APP_HOME/data/sqlite $APP_HOME/logs
RUN useradd --create-home --uid 10001 aqp \
 && chown -R aqp:aqp $APP_HOME

USER aqp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
