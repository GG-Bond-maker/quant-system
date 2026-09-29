// 前端运行时回归验证（yan-regression）：Topbar 未登录不发请求 / 已登录搜索正常 / DataQuality 血缘渲染
const { chromium } = require('playwright-core');
const fs = require('fs');

const CHROME = 'C:/Users/HY/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe';
const BASE = 'http://localhost:5199';
const ENV = 'D:/Python_Project/Alpha Quant Platform/.env';

let TOKEN = '';
for (const line of fs.readFileSync(ENV, 'utf-8').split(/\r?\n/)) {
  if (line.startsWith('ADMIN_TOKEN=')) { TOKEN = line.split('=')[1].trim(); break; }
}

const results = [];
function log(name, pass, detail) {
  results.push({ name, pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'} | ${name} | ${detail}`);
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });

  // ---------- 1. 未登录：搜索不发请求 + 登录引导 ----------
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const apiHits = [];
    page.on('request', (r) => {
      const u = r.url();
      if (u.includes('/api/v1/stock/search') || u.includes('/api/v1/etf/list')) apiHits.push(u);
    });
    await page.goto(BASE + '/market', { waitUntil: 'domcontentloaded' });
    const input = page.locator('input[role="combobox"]');
    await input.waitFor({ timeout: 15000 });
    await input.click();
    await input.fill('茅台');
    await page.waitForTimeout(1200);
    const hint = await page.locator('text=登录后可搜索').count();
    log('未登录-搜索不发请求', apiHits.length === 0, `stock/search+etf/list 命中 ${apiHits.length} 次`);
    log('未登录-显示登录引导', hint > 0, `"登录后可搜索" 出现 ${hint} 处`);
    await ctx.close();
  }

  // ---------- 2. 已登录：搜索正常出结果 ----------
  {
    const ctx = await browser.newContext();
    await ctx.addInitScript(([tok]) => {
      localStorage.setItem('aqp-auth', JSON.stringify({
        state: { token: tok, user: { username: 'admin', role: 'admin' }, expiresAt: null },
        version: 0,
      }));
    }, [TOKEN]);
    const page = await ctx.newPage();
    const apiHits = [];
    page.on('request', (r) => {
      const u = r.url();
      if (u.includes('/api/v1/stock/search') || u.includes('/api/v1/etf/list')) apiHits.push(u);
    });
    await page.goto(BASE + '/market', { waitUntil: 'domcontentloaded' });
    const input = page.locator('input[role="combobox"]');
    await input.waitFor({ timeout: 15000 });
    await input.click();
    await input.fill('茅台');
    await page.waitForTimeout(2500);
    const listItems = await page.locator('#topbar-search-list li').count();
    const loginHint = await page.locator('text=登录后可搜索').count();
    log('已登录-搜索发出请求', apiHits.length > 0, `命中 ${apiHits.length} 次`);
    log('已登录-搜索出结果', listItems > 0, `下拉项 ${listItems} 条`);
    log('已登录-不误显示登录引导', loginHint === 0, `登录引导出现 ${loginHint} 处`);
    await ctx.close();
  }

  // ---------- 3. 已登录：DataQuality 血缘渲染（非永久"加载中"） ----------
  {
    const ctx = await browser.newContext();
    await ctx.addInitScript(([tok]) => {
      localStorage.setItem('aqp-auth', JSON.stringify({
        state: { token: tok, user: { username: 'admin', role: 'admin' }, expiresAt: null },
        version: 0,
      }));
    }, [TOKEN]);
    const page = await ctx.newPage();
    await page.goto(BASE + '/dataquality', { waitUntil: 'domcontentloaded' });
    // 等血缘块离开"加载血缘…"（成功渲染或错误态）
    let state = 'timeout';
    for (let i = 0; i < 40; i++) {
      const loading = await page.locator('text=加载血缘…').count();
      const errState = await page.locator('text=血缘图谱加载失败').count();
      if (loading === 0) { state = errState > 0 ? 'error' : 'rendered'; break; }
      await page.waitForTimeout(1000);
    }
    await page.waitForTimeout(1500);
    const bodyText = await page.locator('body').innerText();
    const hasGraphHint = /数据血缘图谱/.test(bodyText);
    const hasNodeState = /节点状态为实时扫描|扫描于/.test(bodyText);
    log('DataQuality-血缘非永久加载中', state !== 'timeout', `最终状态=${state}`);
    log('DataQuality-成功渲染图谱', state === 'rendered' && hasGraphHint && hasNodeState,
      `状态=${state}, 标题=${hasGraphHint}, 实扫状态=${hasNodeState}`);
    await ctx.close();
  }

  await browser.close();
  console.log('\n=== 汇总 ===');
  console.log(`PASS ${results.filter((r) => r.pass).length} / ${results.length}`);
})().catch((e) => { console.error('SCRIPT ERROR', e); process.exit(1); });
