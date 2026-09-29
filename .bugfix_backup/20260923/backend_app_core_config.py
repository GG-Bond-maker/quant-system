"""
全局配置加载（AQP）。

- 优先读取环境变量，其次读取项目根目录下的 .env 文件；
- 所有字段默认值保守、本地可跑（例如 SQLite 绝对路径指向 <root>/data/sqlite/aqp.db）；
- 通过 lru_cache 单例化，避免重复解析 .env。

层级关系（重构移动文件时务必同步修改 parents 索引）：
    parents[0] = backend/app/core/   <- 本文件所在目录
    parents[1] = backend/app/
    parents[2] = backend/
    parents[3] = <项目仓库根目录>     <- 仓库布局下的 PROJECT_ROOT

⚠️ 2026-09-21 审计 P0-1：**不能再按固定层数取 parents[3] 当作数据根**。
   容器内（`COPY backend/app /app/app`）本文件位于 `/app/app/core/config.py`，
   `parents[3]` 会退化成 **`/`**，于是 LOG_DIR=`/backend/logs`、DATA_ROOT=`/data/parquet`
   全部落到根目录；`read_only: true` + uid 10001 下 import 期 mkdir 直接 EROFS/PermissionError，
   **API 容器无法启动**（且 compose 的卷 `./data:/app/data`、`./backend/logs:/app/logs`
   才是预期位置）。现改为"按标记向上查找 + 支持 env 显式覆盖"。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_backend_root() -> Path:
    """定位**后端根**（即包含 ``app/`` 包的那一层）。

    仓库布局 → ``<repo>/backend``；容器布局 → ``/app``。
    判据是 ``<base>/app/main.py`` 存在，因此不依赖文件层级深度。
    """
    here = Path(__file__).resolve()
    for base in here.parents:
        if (base / "app" / "main.py").is_file():
            return base
    # 回退：<backend>/app/core/config.py → parents[2] = <backend>
    return here.parents[2]


def _resolve_project_root(backend_root: Path) -> Path:
    """定位**数据根**（即包含 ``data/`` 的那一层）。

    优先级：显式 env（``AQP_PROJECT_ROOT``，兼容 ``PROJECT_ROOT``）> 仓库标记 > 后端根。

    仓库布局 ``<repo>/backend/app/...`` 且 ``<repo>/backend/app`` 存在 ⇒ ``<repo>``；
    容器布局 ``/app/app/...``（无 ``/backend/app``）⇒ ``/app``，正好对上 compose 的
    ``./data:/app/data`` 与 ``./backend/logs:/app/logs`` 两个卷。
    """
    for key in ("AQP_PROJECT_ROOT", "PROJECT_ROOT"):
        raw = os.environ.get(key)
        if raw:
            return Path(raw).expanduser().resolve()
    if (backend_root.parent / "backend" / "app").is_dir():
        return backend_root.parent
    return backend_root


BACKEND_ROOT: Path = _resolve_backend_root()
PROJECT_ROOT: Path = _resolve_project_root(BACKEND_ROOT)


class Settings(BaseSettings):
    """应用配置。字段顺序 = 常用度排序。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 基础 ----
    ENV: Literal["dev", "test", "prod"] = Field(default="dev", description="运行环境")
    DEBUG: bool = Field(default=True, description="是否开启 debug 模式")
    APP_NAME: str = Field(default="AQP", description="应用名")
    ADMIN_TOKEN: str = Field(
        default="aqp-dev-token-change-me",
        description="后台管理接口的 Bearer Token；生产环境必须改为强随机串",
    )
    ALLOW_ADMIN_TOKEN_LOGIN: bool = Field(
        default=True,
        description="是否允许 Bearer ADMIN_TOKEN 直接登录；生产环境必须关闭",
    )

    # ---- 网络 ----
    API_HOST: str = Field(default="0.0.0.0", description="API 监听地址")
    API_PORT: int = Field(default=8000, description="API 监听端口")
    METRICS_REQUIRE_AUTH: bool | None = Field(
        default=None,
        description="Prometheus /metrics 是否要求 Bearer 认证；留空时 prod 要求认证、dev/test 保持开放",
    )
    # 注意：.env 中用逗号分隔（pydantic-settings 对 list 类型要求 JSON，逗号串更友好）
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="允许跨域的前端地址，逗号分隔",
    )

    # ---- SQLite ----
    SQLITE_URL: str = Field(
        default=f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'sqlite' / 'aqp.db'}",
        description="SQLAlchemy 异步 URL：3 斜杠 + 绝对路径",
    )

    # ---- Redis ----
    REDIS_HOST: str = Field(default="127.0.0.1")
    REDIS_PORT: int = Field(default=6379)
    REDIS_DB: int = Field(default=0)
    REDIS_PASSWORD: str | None = Field(default=None)
    REDIS_TIMEOUT: float = Field(default=1.0, description="Redis 超时秒；超时自动降级内存缓存")
    REDIS_ENABLED: bool = Field(default=True, description="Redis 总开关；False 直接走内存缓存")

    # ---- 数据 / 模型根目录 ----
    DATA_ROOT: Path = Field(default=PROJECT_ROOT / "data" / "parquet")
    MODEL_ROOT: Path = Field(default=PROJECT_ROOT / "data" / "models")
    FEATURE_VERSION: str = Field(
        default="",
        description=(
            "features 版本；留空时按 parquet mtime 隐式选择最新单一版本（禁止混读多版本）。"
            "⚠️ 留空时口径可能随新版本写入而静默切换：自动选择会记 WARNING（monitor 侧，"
            "提示当前生效版本），但生产建议显式固定 FEATURE_VERSION 以免口径漂移"
        ),
    )

    # ---- 汇率 ----
    USD_CNY_RATE: float = Field(
        default=7.2,
        description="美元兑人民币汇率（ETF 规模折算口径）；人工配置值，非实时汇率",
    )

    # ---- 日志 ----
    LOG_LEVEL: str = Field(default="INFO")
    LOG_DIR: Path = Field(default=BACKEND_ROOT / "logs")

    # ---- AKShare ----
    AKSHARE_RATE_LIMIT: float = Field(
        default=1.2,
        description="AKShare 两次请求最小间隔秒数，东方财富会封临时 IP",
    )
    AKSHARE_RETRY: int = Field(default=3, description="重试次数")
    WARM_OVERVIEW_ON_STARTUP: bool = Field(
        default=True,
        description="启动时预热 market/overview（无 Redis 时首建需 48s+ 外部聚合；测试环境应关闭）",
    )
    QUOTES_TTL: int = Field(
        default=15,
        description="批量实时行情 /market/quotes 进程内缓存秒数（§3.3：相同 symbols 集合共享）",
    )
    COMPUTE_CONCURRENCY: int = Field(
        default=2, ge=1, le=2,
        description="单进程重计算并发上限；跨 worker 部署需配合任务队列",
    )
    COMPUTE_ACQUIRE_TIMEOUT_SECONDS: float = Field(
        default=10.0, ge=0.0, le=60.0,
        description=(
            "重计算并发满额时的有界等待秒数：在此窗口内排队等待空闲 slot，"
            "超时才返回 ERR_RATE_LIMITED（而非一到上限就立刻拒绝）；"
            "前端 ComputeQueue 已串行化，后端配套排队避免『前端排好队、后端仍直接拒』"
        ),
    )

    # ---- ML 超参（默认保守） ----
    ML_LABEL_HORIZON: int = Field(default=5, description="Label 为未来 N 日收益率")
    ML_HOLDOUT_DAYS: int = Field(default=252, description="验证段保留交易日")
    ML_TEST_DAYS: int = Field(default=252, description="测试段保留交易日（仅最终评估）")

    # ---- JWT (P3-1) ----
    JWT_SECRET: str | None = Field(default=None, description="JWT 签名密钥；生产必须从 .env 设置")
    JWT_EXPIRE_SECONDS: int = Field(default=604800, description="JWT 有效期秒（默认 7 天）")

    # ---- RBAC 开关（2026-09-23 用户裁决：全面放开）----
    # 用户明确要求「只要登录即可使用全部功能（含下单 / 数据同步 / 模型训练等写操作），
    # 对所有现有和将来新增的用户都生效」⇒ 默认 False。
    # 关闭时 core/auth.ensure_role 不再比较角色等级，**仅保留登录（require_auth）这道门**；
    # 置 True 即回滚到 viewer < researcher < admin 的分级校验（无需改代码，改配置即可）。
    RBAC_ENFORCE: bool = Field(
        default=False,
        description=(
            "是否强制角色最低等级（RBAC）。False（默认）= 登录即可用全部功能；"
            "True = 回滚到 viewer/researcher/admin 分级拦截"
        ),
    )

    # ---- 自助注册（登录页「注册」入口） ----
    ALLOW_REGISTRATION: bool = Field(
        default=True,
        description="是否开放自助注册；公网部署必须关闭（AQP_ALLOW_REGISTRATION=false）",
    )
    REGISTER_DEFAULT_ROLE: str = Field(
        default="viewer",
        description="自助注册用户的默认角色；只允许 viewer/researcher，管理员须由脚本创建",
    )

    # ---- 流水线失败通知（P1-1：默认关闭，通知失败不影响流水线） ----
    NOTIFY_ENABLED: bool = Field(default=False, description="失败通知总开关")
    NOTIFY_WEBHOOK_URL: str | None = Field(default=None, description="Webhook URL")
    NOTIFY_EMAIL_TO: str | None = Field(default=None, description="收件邮箱（预留）")

    # ---- 数据源（P1-2 三源冗余） ----
    TUSHARE_TOKEN: str | None = Field(default=None, description="Tushare Pro token，来自 .env")

    # ---- 线程统一配置（P0 修复：Polars / LightGBM 共用，避免 18x18 争用） ----
    AQP_CPU_THREADS: int | None = Field(
        default=None,
        description="统一 CPU 线程数；未设置时默认 min(逻辑核-2, 12)",
    )

    # ---- 智能监控与 LLM（前沿演进 Phase 1/2）----
    EVENING_ROUTINE_ENABLED: bool = Field(
        default=True, description="晚间例行调度开关（build_features→infer→监控→日报）")
    EVENING_ROUTINE_TIME: str = Field(
        default="17:30", description="晚间例行触发时间 HH:MM（收盘后，autoSync 15:45 之后）")
    AUTO_RETRAIN_ON_DRIFT: bool = Field(
        default=True, description="PSI 漂移超标时自动触发重训（candidate 注册 + promote 门禁把关）")
    RETRAIN_MIN_INTERVAL_HOURS: int = Field(
        default=12, description="两次自动重训的最小间隔小时数（防漂移期反复重训）")
    LLM_PROVIDER: Literal["none", "ollama", "openai"] = Field(
        default="none", description="LLM 提供方；none 时 NL-to-Factor 入口返回未配置提示")
    LLM_BASE_URL: str = Field(
        default="http://127.0.0.1:11434", description="LLM 服务地址（ollama 默认 11434）")
    LLM_API_KEY: str = Field(default="", description="LLM API Key（openai 兼容服务）")
    LLM_MODEL: str = Field(default="qwen2.5:7b", description="LLM 模型名")
    LLM_TIMEOUT_SECONDS: int = Field(default=60, description="LLM 请求超时秒数")

    # ---- 派生属性 ----
    @property
    def cors_origins_list(self) -> list[str]:
        """把逗号分隔的 CORS_ORIGINS 解析为 list[str]。"""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def metrics_require_auth(self) -> bool:
        """解析 /metrics 鉴权策略：生产默认收紧，开发/测试默认便于采集。"""
        if self.METRICS_REQUIRE_AUTH is not None:
            return self.METRICS_REQUIRE_AUTH
        return self.ENV == "prod"

    @property
    def SQLITE_PATH(self) -> Path:
        """从 SQLITE_URL 解析出 SQLite 文件物理路径（供同步 sqlite3 fallback 使用）。"""
        prefix = "sqlite+aiosqlite:///"
        if self.SQLITE_URL.startswith(prefix):
            return Path(self.SQLITE_URL[len(prefix):])
        return PROJECT_ROOT / "data" / "sqlite" / "aqp.db"


DEFAULT_ADMIN_TOKEN = "aqp-dev-token-change-me"


def validate_runtime_safety(settings: Settings) -> None:
    """校验运行时安全配置，在生产环境 fail-fast。"""
    required_prod_safe: list[str] = []
    if settings.ADMIN_TOKEN == DEFAULT_ADMIN_TOKEN:
        required_prod_safe.append("ADMIN_TOKEN 仍为默认值")
    if settings.ALLOW_ADMIN_TOKEN_LOGIN:
        required_prod_safe.append("ALLOW_ADMIN_TOKEN_LOGIN 必须为 false")
    if settings.ALLOW_REGISTRATION:
        required_prod_safe.append("ALLOW_REGISTRATION 必须为 false")

    if settings.ENV == "prod":
        problems = list(required_prod_safe)
        if len(settings.ADMIN_TOKEN) < 32:
            problems.append("ADMIN_TOKEN 长度必须至少为 32")
        if not settings.JWT_SECRET:
            problems.append("JWT_SECRET 必须显式设置，禁止由 ADMIN_TOKEN 派生")
        elif len(settings.JWT_SECRET) < 32:
            problems.append("JWT_SECRET 长度必须至少为 32")
        dev_origins = {"http://localhost:5173", "http://127.0.0.1:5173"}
        if (not settings.cors_origins_list
                or any(origin in dev_origins for origin in settings.cors_origins_list)):
            problems.append("CORS_ORIGINS 不能包含开发环境地址")
        if problems:
            raise ValueError("生产安全配置不合格：" + "；".join(problems))
    elif settings.ENV == "dev":
        for problem in required_prod_safe:
            logger.warning(f"开发环境安全告警：{problem}")
        # 审计 P0-2：仅说"开发环境"不够——`API_HOST` 默认就是 0.0.0.0（全网卡监听），
        # 只要 ADMIN_TOKEN 还是默认值、且仍允许用它登录，就是一个**可被任意人接管**的
        # 管理入口。此处把"默认凭据 + 非回环绑定"这一真实暴露面单独、显式地喊出来，
        # 并给出两条可直接照做的处置方式（不阻断本地开发）。
        if settings.ADMIN_TOKEN == DEFAULT_ADMIN_TOKEN and settings.ALLOW_ADMIN_TOKEN_LOGIN:
            host = (settings.API_HOST or "").strip()
            loopback = host in ("127.0.0.1", "localhost", "::1")
            if not loopback:
                logger.warning(
                    f"⚠️ 高危暴露：ADMIN_TOKEN 为默认值、ALLOW_ADMIN_TOKEN_LOGIN=true，"
                    f"而 API_HOST={host!r} 监听非回环地址 ⇒ 同一网络内任何人都能用"
                    f"'aqp-dev-token-change-me' 直接取得管理员权限。"
                    f"请任选其一：① 在 .env 设置强随机 ADMIN_TOKEN（≥32 位）并设"
                    f"ALLOW_ADMIN_TOKEN_LOGIN=false；② 本机开发时把 API_HOST 改为 127.0.0.1。"
                    f"（容器部署请设 ENV=prod —— 该组合会被直接拒绝启动）")


def _ensure_dir(path: Path, *, what: str, setting: str) -> None:
    """创建目录；失败时抛出**可操作**的错误而不是裸 EROFS/PermissionError。

    审计 P0-1 的五个失败机制之一就是"import 期 mkdir 撞墙"，而原始报错既不说明
    是哪个目录、也不提示该改哪个配置，排查成本极高。
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"无法创建{what}目录：{path}（{type(e).__name__}: {e}）。"
            f"请设置环境变量 {setting} 指向可写位置；容器部署请确认 compose 的卷挂载"
            f"（期望 /app/data 与 /app/logs）以及 AQP_PROJECT_ROOT 的取值"
            f"（当前 PROJECT_ROOT={PROJECT_ROOT}，BACKEND_ROOT={BACKEND_ROOT}）。"
        ) from e


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """缓存配置对象，避免重复读文件；首次调用时确保关键目录存在。"""
    s = Settings()
    validate_runtime_safety(s)
    _ensure_dir(s.LOG_DIR, what="日志", setting="LOG_DIR")
    _ensure_dir(s.DATA_ROOT, what="行情数据", setting="DATA_ROOT")
    _ensure_dir(s.MODEL_ROOT, what="模型", setting="MODEL_ROOT")
    _ensure_dir(s.SQLITE_PATH.parent, what="SQLite", setting="SQLITE_URL")

    # MEDIUM 修复：默认密钥是公开已知字符串，而 JWT 直接由它派生
    # （secret = "aqp-derive:" + ADMIN_TOKEN），任何人都能伪造 admin token。
    # 本地研究场景不阻断启动，但必须显式告警。
    if s.ADMIN_TOKEN == DEFAULT_ADMIN_TOKEN:
        logger.warning(
            "安全告警：ADMIN_TOKEN 仍为默认值，JWT 签名密钥可被任何人推导；"
            "请在项目根目录创建 .env 并设置 ADMIN_TOKEN / JWT_SECRET。"
            f"当前 API_HOST={s.API_HOST}，请勿暴露到非授信网络。")
    if not s.JWT_SECRET:
        logger.warning("JWT_SECRET 未设置，将从 ADMIN_TOKEN 派生（生产必须单独设置）")
    return s


def effective_cpu_threads() -> int:
    """统一 CPU 线程配置：AQP_CPU_THREADS（env/settings）> 默认 min(逻辑核-2, 12)。

    供 POLARS_MAX_THREADS（app/__init__ 启动时注入）与
    LightGBM num_threads（train_lgbm）共同使用，避免多组件线程过载。
    """
    import os

    raw = os.environ.get("AQP_CPU_THREADS") or (
        get_settings().AQP_CPU_THREADS if get_settings().AQP_CPU_THREADS else None
    )
    if raw:
        return max(1, int(raw))
    cpu = os.cpu_count() or 4
    return max(1, min(cpu - 2, 12))
