# AQP 快速启动指南

> 跟着下面的步骤做，就能把项目跑起来。不需要任何量化或金融背景。

---

## 你需要准备什么

| 软件 | 版本 | 去哪下载 | 检查是否装好 |
|---|---|---|---|
| Python | 3.11.x（不要 3.12+） | https://www.python.org/downloads/ | 终端输入 `python --version` |
| Node.js | 20 LTS+ | https://nodejs.org/ | 终端输入 `node -v` |
| Redis | 7.x（可选，没有也能跑） | https://github.com/tporadowski/redis/releases 或 Memurai | 终端输入 `redis-cli ping` |

> **Redis 没装也完全没问题**——项目会自动降级为内存缓存，功能不受影响。
> 如果你想装：Windows 推荐下载 [Memurai](https://www.memurai.com/get-memurai/)（Redis 兼容版，一键安装）。

---

## 第一步：下载项目

```bash
cd D:\Python_Project
git clone <你的仓库地址> Alpha Quant Platform
cd "Alpha Quant Platform"
```

> 如果没有 Git，直接把项目文件夹复制过来也行。

---

## 第二步：安装后端依赖（约 5 分钟）

打开终端（Windows 用 PowerShell 或 CMD），执行：

```bash
cd backend

# 创建 Python 虚拟环境（相当于一个隔离的 Python 沙盒）
python -m venv .venv

# 激活虚拟环境
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Windows CMD:
.venv\Scripts\activate.bat
# Mac/Linux:
source .venv/bin/activate

# 安装所有依赖（用国内镜像加速）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 看到 `Successfully installed ...` 就说明装好了。
> 如果 TA-Lib 安装报错，去 https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib 下载对应版本的 whl 文件手动安装。

---

## 第三步：安装前端依赖（约 3 分钟）

**新开一个终端窗口**，执行：

```bash
cd frontend
npm install
```

> 看到 `added xxx packages` 就说明装好了。
> 如果下载慢，先执行 `npm config set registry https://registry.npmmirror.com` 再重新 `npm install`。

---

## 第四步：初始化数据（约 5~10 分钟）

回到 backend 目录的终端：

```bash
# 确保 venv 已激活（提示符前有 (.venv)）
cd backend

# 初始化数据库 + 目录
python scripts/bootstrap.py

# 拉取数据：交易日历 + 全部股票列表 + 3 只示例股票的行情
python -m app.data.ingest
```

> 这一步会从东方财富/新浪免费接口拉取真实 A 股数据。
> 完成后你会看到 `数据初始化完成 ✅`。
>
> 如果想拉更多股票的数据（推荐 100 只以上才有研究价值）：
> ```bash
> python -m app.data.ingest --codes 600519,000001,300750,688981,000858 --start 2022-01-01
> ```
> 拉取全部历史（2022 年至今 × 3 只 ≈ 3 分钟；100 只 ≈ 15 分钟）

---

## 可选准备：启动 Redis 缓存（强烈建议，设为部署默认步骤）

Redis 用来做**行情/接口缓存**：常驻后 overview 等聚合接口跨重启命中（不再出现重启后首个请求扛 48s 全量重建）。
不装/不启动时后端自动降级为内存缓存，**功能完全一样**，只是重启后缓存清空。

> **部署默认步骤**（Sprint1 L1-1 起）：每次开机/部署先执行下面的启动命令，再启动后端。
> 后端启动时会自动预热缓存；Redis 掉线时设置页会显示「⚠ 缓存降级」徽标提醒。

如果你的 Redis 装在 Docker 里（本机已是这种情况），按下面三步走：

```bash
# 1. 先启动 Docker Desktop（开始菜单打开，等右下角鲸鱼图标变绿/静止）

# 2. 启动已有的 Redis 容器（本机容器名：aqp-redis，由 docker-compose.yml 创建管理）
docker start aqp-redis

# 3. 验证：应输出 PONG
#    容器已设密码（= 根 .env 的 REDIS_PASSWORD，本机为 123456），必须带 -a 认证，
#    否则只会得到 NOAUTH Authentication required.
docker exec aqp-redis redis-cli -a 123456 ping
```

如果还没有 Redis 容器，两种方式任选其一（**推荐方式一**，与 docker-compose.yml 声明完全一致）：

```bash
# 方式一（推荐）：由 compose 创建 —— 密码/内存上限/健康检查全部按声明来，
#   compose 会自动读取根 .env 的 REDIS_PASSWORD，缺省会直接报错而不是悄悄起无密码实例
docker-compose up -d redis

# 方式二：一条手工命令（--restart unless-stopped 让它随 Docker 自启）。
#   ⚠️ 密码必须与 .env 的 REDIS_PASSWORD 保持一致（本机为 123456）：
#      不带 --requirepass 时后端 AUTH 会被拒，设置页会一直显示「⚠ 缓存降级」；
#      端口只绑 127.0.0.1，不暴露到局域网。
docker run -d --name aqp-redis --restart unless-stopped -p 127.0.0.1:6379:6379 \
  redis:7-alpine redis-server --requirepass 123456 \
  --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
```

> ⚠️ 两种方式创建的容器都叫 `aqp-redis`，**同一时间只能存在一个**：
> 手工容器存在时 `docker-compose up -d redis` 会报容器名冲突，先 `docker rm -f aqp-redis` 再 up 即可。

> 启动顺序无所谓。**Redis 中途掉线后无需手动重启后端**：缓存层有熔断器，
> 连续失败 5 次后熔断 60 秒，窗口一过自动重新探测并恢复（`/health` 的
> `redis` 字段会从 `degraded` 变回 `ok`）。不想折腾 Redis 就直接跳过这一节。

---

## 第五步：启动项目（两个终端窗口）

> 项目由两部分组成：**后端**（提供数据接口）和**前端**（你看到的网页）。
> 需要开 **两个终端窗口**，分别各跑一条命令。没有一键脚本，每一步都看得见、可控。

### 终端 1 —— 启动后端（先启动它）

```bash
cd "D:\Python_Project\Alpha Quant Platform\backend"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> 看到类似 `Uvicorn running on http://127.0.0.1:8000` 就成功了。
> **这个窗口不要关**，关了后端就停了。

### 终端 2 —— 启动前端

```bash
cd "D:\Python_Project\Alpha Quant Platform\frontend"
npm run dev
```

> 看到类似 `Local: http://127.0.0.1:5173/` 就成功了。
> **这个窗口也不要关**。

### 怎么确认真的启动成功了？

浏览器打开 `http://127.0.0.1:8000/health`，看到 `"code":0` 就是后端正常；
再打开 `http://127.0.0.1:5173`，能看到页面就是全部就绪。

### 怎么停止？

到对应终端窗口按 `Ctrl+C`（或直接关掉那个终端窗口）。

### 💡 首次打开页面需要登录

打开前端后会跳转到登录页。登录 Token 在项目根目录的 `.env` 文件里（`ADMIN_TOKEN=` 后面那一串），
复制粘贴进去即可。没有 `.env` 文件的话，先在项目根目录创建一个并写入：

```
ADMIN_TOKEN=你自己随便编的一串长随机字符
JWT_SECRET=另一串不同的长随机字符
```

然后**重启后端**（终端 1 里 Ctrl+C 再重新执行上面的命令）生效。

---

## 第六步：打开浏览器

打开 Chrome / Edge，访问：

| 地址 | 你会看到什么 |
|---|---|
| http://127.0.0.1:5173 | **市场概览页**：大盘指数、涨跌分布、推荐榜 |
| http://127.0.0.1:5173/stock/600519.SH | **个股详情页**：贵州茅台 K 线图 + AI 预测面板 |
| http://127.0.0.1:8000/docs | **API 文档**：Swagger 界面，可以在线测试所有接口 |

---

## 常见问题

### Q: `python --version` 显示的不是 3.11？

安装 Python 3.11 到一个独立目录，然后用完整路径创建 venv：
```bash
C:\Python311\python.exe -m venv .venv
```

### Q: `pip install` 报错 TA-Lib 安装失败？

TA-Lib 需要 C 语言库。最简单的解决方法：
1. 去 https://github.com/cgohlke/win-whl/releases 下载 `TA_Lib-0.4.28-cp311-cp311-win_amd64.whl`
2. `pip install TA_Lib-0.4.28-cp311-cp311-win_amd64.whl`
3. 再执行 `pip install -r requirements.txt`

或者：直接从 requirements.txt 中删掉 `TA-Lib==0.4.28` 这一行——项目不依赖 TA-Lib 也能跑（因子计算是纯 pandas 实现）。

### Q: 需要安装 MySQL / 其他数据库吗？

**不需要。** 项目数据库是 **SQLite**（单文件，位于 `data/sqlite/aqp.db`），随项目自动创建，零配置。
行情数据存 Parquet 文件（`data/parquet/`），同样无需安装任何数据库软件。
根目录的 `docker-compose.yml` 是**生产部署**用的整体容器化方案，本地开发用不到它。

### Q: Redis 没装，会有问题吗？

不会。项目自动检测 Redis 是否可用：
- Redis 在线 → 使用 Redis 缓存
- Redis 不在线 → 自动降级为进程内存缓存，**功能完全一样**，只是重启后缓存清空

你可以在健康检查里确认：`http://127.0.0.1:8000/health` 返回 `"redis":"degraded"` 就是降级模式，一切正常。

### Q: 前端页面是白屏？

1. 确认后端已启动（http://127.0.0.1:8000/health 能打开）
2. 确认前端已启动（http://127.0.0.1:5173 能打开）
3. 按 F12 打开浏览器控制台，看是否有红色报错
4. 最常见的原因：数据还没初始化（回到第四步）

### Q: 推荐榜是空的？

推荐榜需要 ML 模型产生预测结果。首次使用需要：
```bash
# 1. 拉取足够历史数据（至少 400 天）
python -m app.data.ingest --codes 600519,000001,300750 --start 2022-01-01

# 2. 训练模型（约 2 分钟）
python scripts/build_features.py
python scripts/train.py --horizon 5 --holdout 60 --test-days 40

# 3. 刷新前端页面即可看到推荐榜
```

### Q: 端口被占用？

```bash
# Windows: 找到占用端口的进程
netstat -ano | findstr :8000
# 杀掉它（把 12345 换成上面查到的 PID）
taskkill /F /PID 12345
```

---

## 项目结构（看一眼就好）

```
Alpha Quant Platform/
├── backend/           # Python 后端（FastAPI）
│   ├── app/           # 源代码
│   ├── scripts/       # 启动/数据/训练脚本
│   └── tests/         # 自动化测试
├── frontend/          # React 前端
│   └── src/pages/     # 4 个页面
├── data/              # 运行时生成的数据（不用管）
│   ├── sqlite/        # 数据库
│   ├── parquet/       # 行情/因子/预测
│   └── models/        # 训练好的模型
├── scripts/           # 数据维护脚本（初始化/流水线等）
└── README.md          # 项目说明
```

---

## 日常使用

| 我想要... | 怎么做 |
|---|---|
| 更新到最新交易日的数据 | `python -m app.data.pipeline` |
| 拉某只股票的数据 | `python -m app.data.ingest --stage daily --codes 600519 --start 2022-01-01` |
| 重新训练模型 | `python scripts/build_features.py && python scripts/train.py --horizon 5 --holdout 60 --test-days 40` |
| 运行全部测试 | `python -m pytest -q` |
| 只启动后端不启动前端 | `python -m uvicorn app.main:app --port 8000` |

---

## 免责声明

本项目仅用于学习研究，不构成任何投资建议。市场有风险，投资需谨慎。
