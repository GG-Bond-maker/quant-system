# ============================================================
# AQP Frontend Dockerfile（两阶段：node 构建 + nginx 托管）
# 构建上下文 = 仓库根：docker build -f frontend/Dockerfile .
# ============================================================

FROM node:20-slim AS builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm config set registry https://registry.npmmirror.com && npm ci || npm install

COPY frontend/tsconfig.json frontend/tsconfig.node.json ./
COPY frontend/vite.config.ts frontend/tailwind.config.js frontend/postcss.config.js ./
COPY frontend/index.html ./
COPY frontend/src ./src

RUN npm run build

# ================= runtime =================
FROM nginx:1.27-alpine AS runtime

ENV TZ=Asia/Shanghai
RUN apk add --no-cache tzdata curl \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone

COPY --from=builder /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
