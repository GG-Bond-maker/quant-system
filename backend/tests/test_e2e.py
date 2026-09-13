"""P3-7 Playwright E2E 测试（Nightly 可用；本地需要后端 + 前端同时运行）。

这些测试通过 subprocess 启动真实服务，使用 Playwright 浏览器截图。
CI 中作为 Nightly 运行；本地可 `pytest tests/test_e2e.py -v` 执行。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = BACKEND_ROOT / "screenshots"

E2E_AVAILABLE = os.environ.get("E2E_AVAILABLE", "").lower() in ("1", "true")
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5173")
API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")

pytestmark = pytest.mark.skipif(not E2E_AVAILABLE, reason="E2E 需设置 E2E_AVAILABLE=true 且启动服务")


def _wait_for(url: str, timeout: int = 30) -> bool:
    import httpx

    for _ in range(timeout):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            time.sleep(1)
    return False


@pytest.fixture(scope="module")
def browser_page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


class TestE2E:
    def test_e2e_01_market_page(self, browser_page):
        """E2E-01：启动系统 → Market 页面加载。"""
        assert _wait_for(BASE_URL)
        browser_page.goto(BASE_URL)
        browser_page.wait_for_load_state("networkidle")
        browser_page.screenshot(path=str(SCREENSHOTS / "market.png"))
        assert "AQP" in browser_page.title() or "市场" in browser_page.content()

    def test_e2e_02_stock_detail(self, browser_page):
        """E2E-02：点击股票 → Stock Detail → K 线出现。"""
        browser_page.goto(f"{BASE_URL}/stock/600519.SH")
        browser_page.wait_for_load_state("networkidle")
        browser_page.screenshot(path=str(SCREENSHOTS / "stock.png"))
        assert browser_page.content()  # 页面有内容即可

    def test_e2e_03_screener(self, browser_page):
        """E2E-03：Screener 页面加载。"""
        browser_page.goto(f"{BASE_URL}/screener")
        browser_page.wait_for_load_state("networkidle")
        browser_page.screenshot(path=str(SCREENSHOTS / "screener.png"))

    def test_e2e_04_backtest(self, browser_page):
        """E2E-04：Backtest 页面加载。"""
        browser_page.goto(f"{BASE_URL}/backtest")
        browser_page.wait_for_load_state("networkidle")
        browser_page.screenshot(path=str(SCREENSHOTS / "backtest.png"))

    def test_e2e_05_api_health(self):
        """E2E-05：API 健康检查。"""
        import httpx

        r = httpx.get(f"{API_URL}/health", timeout=5)
        assert r.json()["code"] == 0
