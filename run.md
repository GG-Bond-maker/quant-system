# AQP 启动指南

> 零基础可跟做，全程复制粘贴，约 20 分钟。

## 先看这张表：一共 7 步

| 步骤 | 在哪做 | 一句话 |
|---|---|---|
| 1 | — | 装 **Python 3.11** 和 **Node 20+** |
| 2 | — | 把项目下载到电脑上 |
| 3 | **终端 A** | 装后端依赖 |
| 4 | **终端 A** | 初始化数据 |
| 5 | **终端 B** | 装前端依赖 |
| 6 | A + B | 启动后端、启动前端 |
| 7 | 浏览器 | 打开网页、创建账号 |

**两个关键概念，记住就行：**

- **终端** = 那个黑底白字、能敲命令的窗口。Windows 按 `Win` 键 → 输入 `powershell` → 回车。
- **要开两个终端窗口**：**终端 A** 跑后端（幕后算数据），**终端 B** 跑前端（你看到的网页）。**两个都不能关。**

---

## 第 1 步：装两个软件

| 软件 | 版本 | 下载地址 |
|---|---|---|
| **Python** | **3.11** | https://www.python.org/downloads/ |
| **Node.js** | **20 或更高** | https://nodejs.org/ |

> ⚠️ 装 Python 时**务必勾选 "Add Python to PATH"**（安装界面第一个复选框），否则后面会提示"不是内部或外部命令"。
> ⚠️ Python 请装 **3.11**，装 3.12 / 3.13 可能有个别依赖装不上。

装完打开终端，输入这两条验证：

```powershell
python --version
node -v
```

能正常显示版本号（`Python 3.11.x` / `v20.x` 以上）就可以继续。

---

## 第 2 步：把项目放到电脑上

```powershell
cd D:\Python_Project
git clone <你的仓库地址> "Alpha Quant Platform"
cd "Alpha Quant Platform"
```

> **没有 Git？** 直接把项目文件夹整个复制过来也行。
> 💡 路径里有空格（`Alpha Quant Platform`），所以**路径要加英文双引号**，不加会报错。

---

## 第 3 步：装后端依赖（终端 A）

```powershell
cd "D:\Python_Project\Alpha Quant Platform\backend"

python -m venv .venv

.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

看到最后一行 `Successfully installed ...` 就成功了（约 3~5 分钟）。

> 💡 **为什么不"激活虚拟环境"？** 因为 PowerShell 默认禁止运行激活脚本（报"禁止运行脚本"）。
> 直接写 `.venv\Scripts\python.exe` 全路径效果完全一样，但不会卡住 —— 后面所有后端命令都这么写。

**顺手创建配置文件**（还在 `backend` 目录下）：

```powershell
Copy-Item ..\.env.example ..\.env
```

> CMD 用户用 `copy ..\.env.example ..\.env`；或直接在文件管理器里把 `.env.example` 复制一份改名成 `.env`。
> 不复制也能启动（用默认值，会打印安全告警）。**本地自己玩默认值就够**；要放到公网给别人用，
> 必须改掉 `.env` 里的 `ADMIN_TOKEN`、`JWT_SECRET`，并把 `ALLOW_ADMIN_TOKEN_LOGIN`、`ALLOW_REGISTRATION` 改成 `false`。

---

## 第 4 步：初始化数据（终端 A，约 3 分钟）

```powershell
cd "D:\Python_Project\Alpha Quant Platform\backend"

.venv\Scripts\python.exe scripts\bootstrap.py
.venv\Scripts\python.exe -m app.data.ingest
```

**看到 `数据初始化完成 ✅` 就成功了。**

> 数据来自东方财富 / 新浪的免费公开接口，需要联网。默认拉 3 只示例股票 120 天行情。
> **想要更多股票**（推荐 100 只以上才有研究价值）：把第二条命令换成
> `.venv\Scripts\python.exe -m app.data.ingest --stage daily --codes 600519,000001,300750,688981,000858 --start 2022-01-01`
> （3 只约 3 分钟，100 只约 15 分钟）

---

## 第 5 步：装前端依赖（**新开一个终端 B**，别关终端 A）

```powershell
cd "D:\Python_Project\Alpha Quant Platform\frontend"
npm install
```

看到 `added xxx packages` 就成功了（约 2~3 分钟）。下载慢就先执行
`npm config set registry https://registry.npmmirror.com`，再重新 `npm install`。

---

## 第 6 步：启动（两个终端各一条命令）

**终端 A —— 启动后端（先启动它）**

```powershell
cd "D:\Python_Project\Alpha Quant Platform\backend"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 就成功了。**窗口不要关。**

**终端 B —— 启动前端**

```powershell
cd "D:\Python_Project\Alpha Quant Platform\frontend"
npm run dev
```

看到 `Local: http://127.0.0.1:5173/` 就成功了。**窗口不要关。**

> **停止**：到对应窗口按 `Ctrl + C`，或直接关掉窗口。

---

## 第 7 步：打开浏览器 + 创建账号

用 Chrome 或 Edge 打开 **http://127.0.0.1:5173**

| 地址 | 内容 |
|---|---|
| http://127.0.0.1:5173 | **市场概览页**（无需登录） |
| http://127.0.0.1:5173/login | **登录页** |
| http://127.0.0.1:8000/health | 后端健康检查，看到 `"code":0` 即正常 |
| http://127.0.0.1:8000/docs | API 接口文档（开发者用） |

市场概览页可以随便看，**其它页面需要登录**。首次使用先创建管理员账号 —— 在**终端 A** 执行
（把 `admin` 和 `你的密码` 换成你想用的，密码至少 6 位）：

```powershell
cd "D:\Python_Project\Alpha Quant Platform\backend"
.venv\Scripts\python.exe scripts\create_admin.py admin 你的密码
```

然后到 http://127.0.0.1:5173/login 用**这个用户名和密码**登录。

> ⚠️ 登录页填的是**用户名和密码**，不是 `.env` 里的 `ADMIN_TOKEN` —— 这是两个完全不同的东西。

**🎉 到这里就全部跑起来了。**

---

## （可选）Redis 缓存 —— 不装完全没问题

Redis 只是个缓存：装了，重启后端后数据不用重算；不装，后端自动降级成内存缓存，
**功能一模一样**，只是重启后首次请求慢一点。

想装就用 Docker：

```powershell
# 先启动 Docker Desktop，等右下角鲸鱼图标静止
docker-compose up -d redis
```

> 健康检查里显示 `"redis":"degraded"` = 当前在用内存缓存，**这是正常的，不是错误**。

---

## 常见问题

**Q：`python` / `node` 提示"不是内部或外部命令"？**
软件没装好，或装 Python 时没勾 "Add Python to PATH"。重装并勾选。

**Q：pip 装依赖超时？**
换阿里云镜像再试：`.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple`

**Q：提示"禁止运行脚本"？**
你在用"激活虚拟环境"的老办法。本指南不用激活，改用 `.venv\Scripts\python.exe xxx.py` 即可。

**Q：前端页面白屏？**
① 确认后端活着：打开 http://127.0.0.1:8000/health；② 确认前端活着：打开 http://127.0.0.1:5173；
③ 按 `F12` 看控制台红色报错；④ 最常见原因是**数据还没初始化**，回到第 4 步。

**Q：端口被占用？**
`netstat -ano | findstr :8000` 查到最后一列的 PID，再 `taskkill /F /PID 12345`（换成实际 PID）。

**Q：推荐榜是空的？**
推荐榜需要 AI 模型先跑出预测结果。先拉够历史数据（至少 400 天）再训练：

```powershell
cd "D:\Python_Project\Alpha Quant Platform\backend"
.venv\Scripts\python.exe -m app.data.ingest --stage daily --codes 600519,000001,300750 --start 2022-01-01
.venv\Scripts\python.exe scripts\build_features.py
.venv\Scripts\python.exe scripts\train.py --horizon 5 --holdout 60 --test-days 40
```

**Q：需要装 MySQL 之类的数据库吗？**
不需要。项目用 SQLite（单文件、自动创建）+ Parquet 文件，零配置。

---

## 常用命令速查（都在 `backend` 目录下）

> 下表 `python` 均指 `.venv\Scripts\python.exe`（这样写是为了好读）。

| 我想要… | 命令 |
|---|---|
| 更新到最新交易日数据 | `python -m app.orchestrator` |
| 拉某只股票行情 | `python -m app.data.ingest --stage daily --codes 600519 --start 2022-01-01` |
| 重新训练模型 | `python scripts\build_features.py` → `python scripts\train.py --horizon 5 --holdout 60 --test-days 40` |
| 跑后端测试 | `python -m pytest -q -m "not network"` |
| 只启动后端 | `python -m uvicorn app.main:app --port 8000` |

---

## 项目结构（看一眼就好）

```
Alpha Quant Platform/
├── backend/        # Python 后端（FastAPI）：app/ 源码、scripts/ 脚本、tests/ 测试
├── frontend/       # React 前端（网页界面）
├── data/           # 运行时自动生成，不用管
├── .env            # 你的本地配置（从 .env.example 复制而来）
└── run.md          # 就是本文档
```

---

## 免责声明

本项目仅用于学习研究，**不构成任何投资建议**。市场有风险，投资需谨慎。
