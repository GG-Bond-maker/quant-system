"""审计 P0-2 防回归：prod 安全闸门必须 **fail-fast**，且 dev 下必须点明真实暴露面。

缺陷（2026-09-21 全栈审计，P0）：
    代码侧 `validate_runtime_safety()` 在 ``ENV == "prod"`` 时**确实**会抛错，但
    ① `docker-compose.yml` **从不设置 `ENV`** ⇒ 部署恒为默认 `dev` ⇒ prod 闸门**永不执行**；
    ② dev 分支只打一句"开发环境安全告警"，而 `API_HOST` 默认 `0.0.0.0`
       ⇒ 「默认 ADMIN_TOKEN + 允许 token 登录 + 全网卡监听」被淹没在普通告警里。
    修法：compose 显式 `ENV=prod` 并用 ``${ADMIN_TOKEN:?}`` / ``${JWT_SECRET:?}`` 必填；
          dev 分支额外把"非回环监听 + 默认凭据"作为高危暴露单独喊出。

本文件只断言**代码可验证**的部分；compose 侧的 fail-closed 由
`docker-compose --env-file <空文件> config` 报错来验证（见 FIXES-APPLIED.md）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import DEFAULT_ADMIN_TOKEN, Settings, validate_runtime_safety  # noqa: E402

STRONG = "x" * 40


def _prod(**over: object) -> Settings:
    """一份**合格**的 prod 配置，用例再逐项破坏。"""
    base: dict[str, object] = {
        "ENV": "prod",
        "ADMIN_TOKEN": STRONG,
        "JWT_SECRET": STRONG,
        "ALLOW_ADMIN_TOKEN_LOGIN": False,
        "ALLOW_REGISTRATION": False,
        "CORS_ORIGINS": "https://aqp.example.com",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_prod_baseline_passes() -> None:
    """合格配置必须能通过（否则闸门就是"永远拒绝"，等于没部署）。"""
    validate_runtime_safety(_prod())


@pytest.mark.parametrize(("over", "needle"), [
    ({"ADMIN_TOKEN": DEFAULT_ADMIN_TOKEN}, "ADMIN_TOKEN 仍为默认值"),
    ({"ALLOW_ADMIN_TOKEN_LOGIN": True}, "ALLOW_ADMIN_TOKEN_LOGIN"),
    ({"ALLOW_REGISTRATION": True}, "ALLOW_REGISTRATION"),
    ({"ADMIN_TOKEN": "short"}, "长度必须至少为 32"),
    ({"JWT_SECRET": ""}, "JWT_SECRET 必须显式设置"),
    ({"JWT_SECRET": "short"}, "JWT_SECRET 长度必须至少为 32"),
    ({"CORS_ORIGINS": "http://localhost:5173"}, "CORS_ORIGINS 不能包含开发环境地址"),
    ({"CORS_ORIGINS": ""}, "CORS_ORIGINS 不能包含开发环境地址"),
])
def test_prod_rejects_unsafe(over: dict, needle: str) -> None:
    """每一条不安全配置都必须在 prod 下**拒绝启动**（fail-fast 而非告警）。"""
    with pytest.raises(ValueError) as ei:
        validate_runtime_safety(_prod(**over))
    assert needle in str(ei.value), f"期望错误含 {needle!r}，实际：{ei.value}"


def test_dev_warns_but_does_not_raise() -> None:
    """dev 下只告警不阻断（本地研究与测试必须还能跑起来）。"""
    validate_runtime_safety(Settings(
        ENV="dev", ADMIN_TOKEN=DEFAULT_ADMIN_TOKEN,
        ALLOW_ADMIN_TOKEN_LOGIN=True, CORS_ORIGINS="http://localhost:5173"))


def test_dev_flags_non_loopback_default_credential(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """dev + 默认凭据 + 非回环监听 ⇒ 必须出现"高危暴露"级别的显式告警。"""
    from loguru import logger as _logger

    captured: list[str] = []

    def _sink(message: object) -> None:
        captured.append(str(message))

    sink_id = _logger.add(_sink, level="WARNING", format="{message}")
    try:
        validate_runtime_safety(Settings(
            ENV="dev", ADMIN_TOKEN=DEFAULT_ADMIN_TOKEN, ALLOW_ADMIN_TOKEN_LOGIN=True,
            API_HOST="0.0.0.0"))
    finally:
        _logger.remove(sink_id)

    joined = "\n".join(captured)
    assert "高危暴露" in joined, f"未出现高危暴露告警，实际日志：{joined!r}"
    assert "0.0.0.0" in joined
    assert "ALLOW_ADMIN_TOKEN_LOGIN" in joined


def test_dev_loopback_does_not_raise_high_risk_alert() -> None:
    """回环监听时不应误报高危（否则告警会被忽略）。"""
    from loguru import logger as _logger

    captured: list[str] = []
    sink_id = _logger.add(lambda m: captured.append(str(m)), level="WARNING",
                          format="{message}")
    try:
        validate_runtime_safety(Settings(
            ENV="dev", ADMIN_TOKEN=DEFAULT_ADMIN_TOKEN, ALLOW_ADMIN_TOKEN_LOGIN=True,
            API_HOST="127.0.0.1"))
    finally:
        _logger.remove(sink_id)
    assert "高危暴露" not in "\n".join(captured)


def test_compose_sets_env_prod_and_requires_secrets() -> None:
    """静态断言 compose 的契约：必须有 ENV=prod 与两个必填变量。

    这是"防回退"的关键一条 —— 只要有人删掉 `ENV=prod`，prod 闸门就再次失效。
    """
    compose = (BACKEND_ROOT.parent / "docker-compose.yml").read_text(encoding="utf-8")
    api_block = compose.split("aqp-api:", 1)[1].split("aqp-web:", 1)[0]
    assert "ENV=prod" in api_block, "compose 未设置 ENV=prod ⇒ prod 安全闸门永不执行（P0-2）"
    assert "${ADMIN_TOKEN:?" in api_block, "ADMIN_TOKEN 缺少 :? 必填校验"
    assert "${JWT_SECRET:?" in api_block, "JWT_SECRET 缺少 :? 必填校验"
    assert "ALLOW_ADMIN_TOKEN_LOGIN=false" in api_block
    assert "ALLOW_REGISTRATION=false" in api_block
    # 默认 CORS 是 5173 开发地址，prod 校验会拒绝启动 ⇒ 必须显式覆盖
    assert "CORS_ORIGINS=" in api_block, "prod 下必须显式设置 CORS_ORIGINS"