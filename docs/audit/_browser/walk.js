// AQP 前端页面级验证：登录后逐页截图 + 抓 console error / 失败请求
// 只读验证，不修改任何业务代码。
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');

const BASE = 'http://127.0.0.1:5173';
const USER = process.env.AQP_USER || 'qa_runtime_0928';
const PASS = process.env.AQP_PASS || 'QaRuntime#2026';
const OUT = path.resolve(__dirname, 'shots');
const REPORT = path.resolve(__dirname, '..', '_browser_result.json');

const ROUTES = [
  ['/', 'market'],
  ['/market', 'market2'],
  ['/screener', 'screener'],
  ['/etf', 'etf'],
  ['/etf/510300', 'etf_detail'],
  ['/stock/600519.SH', 'stock_detail'],
  ['/portfolio', 'portfolio'],
  ['/watchlist', 'watchlist'],
  ['/data', 'datacenter'],
  ['/report', 'report'],
  ['/settings', 'settings'],
  ['/backtest', 'backtest'],
  ['/research', 'research'],
  ['/studio', 'studio'],
  ['/alerts', 'alerts'],
  ['/desk', 'desk'],
  ['/pipeline', 'pipeline'],
  ['/capacity', 'capacity'],
  ['/dataquality', 'dataquality'],
];

const EXEC = process.env.PLAYWRIGHT_CHROMIUM_EXEC ||
  'C:\\Users\\HY\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe';

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EXEC, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const failedReq = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300));
  });
  page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 300)));
  page.on('requestfailed', (r) =>
    failedReq.push(`${r.method()} ${r.url().slice(0, 160)} :: ${r.failure()?.errorText}`)
  );
  page.on('response', (r) => {
    if (r.status() >= 400) failedReq.push(`HTTP${r.status()} ${r.url().slice(0, 160)}`);
  });

  const out = { login: null, pages: [], consoleErrors, pageErrors, failedReq };

  // --- 登录 ---
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(2500);
  const inputs = await page.locator('input').count();
  await page.locator('input').nth(0).fill(USER);
  await page.locator('input').nth(1).fill(PASS);
  await page.screenshot({ path: path.join(OUT, '00_login.png') });
  // 提交
  const btn = page.locator('button[type=submit]').first();
  if (await btn.count()) await btn.click();
  else await page.keyboard.press('Enter');
  await page.waitForTimeout(4000);
  out.login = { url: page.url(), inputs, ok: !page.url().includes('/login') };
  await page.screenshot({ path: path.join(OUT, '01_after_login.png') });

  // --- 逐页 ---
  for (const [route, name] of ROUTES) {
    const rec = { route, name, http: null, ms: 0, err: '', blank: false, textLen: 0 };
    const t0 = Date.now();
    consoleErrors.length = 0;
    failedReq.length = 0;
    try {
      const resp = await page.goto(BASE + route, {
        waitUntil: 'domcontentloaded',
        timeout: 45000,
      });
      rec.http = resp ? resp.status() : null;
      await page.waitForTimeout(3500);
      rec.ms = Date.now() - t0;
      const body = await page.evaluate(() => {
        const root = document.getElementById('root');
        return {
          textLen: (document.body.innerText || '').trim().length,
          rootChildren: root ? root.children.length : -1,
        };
      });
      rec.textLen = body.textLen;
      rec.rootChildren = body.rootChildren;
      rec.blank = body.textLen < 40;
      rec.consoleErrors = [...consoleErrors];
      rec.failedReq = [...new Set(failedReq)];
      rec.url = page.url();
    } catch (e) {
      rec.ms = Date.now() - t0;
      rec.err = String(e).slice(0, 200);
      rec.consoleErrors = [...consoleErrors];
      rec.failedReq = [...new Set(failedReq)];
    }
    const shot = path.join(OUT, `pg_${name}.png`);
    try {
      await page.screenshot({ path: shot, fullPage: false });
      rec.shot = shot;
    } catch (e) {
      rec.shotErr = String(e).slice(0, 120);
    }
    console.log(
      `${route.padEnd(20)} http=${rec.http} ${String(rec.ms).padStart(6)}ms ` +
        `text=${String(rec.textLen).padStart(5)} blank=${rec.blank} ` +
        `cerr=${rec.consoleErrors?.length ?? 0} fail=${rec.failedReq?.length ?? 0} ${rec.err.slice(0, 60)}`
    );
    out.pages.push(rec);
  }

  await browser.close();
  fs.writeFileSync(REPORT, JSON.stringify(out, null, 1), 'utf8');
  console.log('\nWROTE', REPORT);
})().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
