/**
 * 第2轮独立回归验证 · 前端预警规则编辑/启停 · Chromium 真实浏览器探针 (v2)
 * 覆盖：编辑预填 / PUT 字段完整性 / 启停成功 / 启停失败回滚+提示 /
 *       加载失败 ErrorState+重试 / 新建流程回归 / 必填校验
 * 运行：node docs/audit-2026-09-27/_r2_browser_probe.mjs
 */
import { chromium } from 'file:///C:/Users/HY/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs';

const EXE = 'C:/Users/HY/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe';
const BASE = 'http://localhost:5173';
const API = 'http://127.0.0.1:8000';
const TOKEN = 'cTwPZSdPU6WT_4DEcc02Z4KlCo4IgiN5HFoQQRMso2I';
const AUTH = JSON.stringify({
  state: { token: TOKEN, user: { username: 'admin', role: 'admin' }, expiresAt: '2030-01-01T00:00:00.000Z' },
  version: 0,
});

const out = [];
const log = (s) => { out.push(s); console.log(s); };
const pass = (s) => log('  PASS  ' + s);
const fail = (s) => log('  FAIL  ' + s);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const apiRules = async () => {
  const r = await fetch(`${API}/api/v1/alerts/rules`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  return (await r.json()).data ?? [];
};

async function newPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await ctx.addInitScript(([k, v]) => { window.localStorage.setItem(k, v); }, ['aqp-auth', AUTH]);
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  return { ctx, page, consoleErrors };
}

async function gotoAlerts(page) {
  await page.goto(`${BASE}/alerts`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('table', { timeout: 15000 }).catch(() => {});
  await sleep(900);
}

const rowByName = (page, name) => page.locator('table tbody tr', { hasText: name });

const main = async () => {
  const browser = await chromium.launch({ headless: true, executablePath: EXE });
  let ok = true;

  // 预取规则，动态定位
  const rulesBefore = await apiRules();
  const ruleA = rulesBefore.find((r) => r.rule_type === 'price_pct');   // symbol 规则
  const ruleB = rulesBefore.find((r) => r.rule_type === 'data_health'); // 全局规则
  log(`规则快照：A=#${ruleA?.id} ${ruleA?.name} / B=#${ruleB?.id} ${ruleB?.name} enabled=${ruleB?.enabled}`);
  if (!ruleA || !ruleB) { log('前置规则缺失，终止'); await browser.close(); process.exit(1); }

  try {
    // ============ 场景 0：基线 ============
    {
      const { ctx, page } = await newPage(browser);
      await gotoAlerts(page);
      const rows = await page.locator('table tbody tr').count();
      const editBtns = await page.getByRole('button', { name: '编辑' }).count();
      const switches = await page.locator('button[role="switch"]').count();
      log(`\n[0] 基线：行=${rows} 编辑按钮=${editBtns} 开关=${switches}`);
      if (rows >= 2 && editBtns >= 2 && switches >= 2) pass('列表/编辑/开关渲染正常');
      else { fail('基线渲染异常'); ok = false; }
      await ctx.close();
    }

    // ============ 场景 1：编辑预填 + PUT 完整性 + 列表刷新（ruleA） ============
    {
      const { ctx, page } = await newPage(browser);
      let putBody = null;
      page.on('request', (req) => {
        if (req.method() === 'PUT' && req.url().endsWith(`/api/v1/alerts/rules/${ruleA.id}`)) {
          try { putBody = JSON.parse(req.postData() || '{}'); } catch { putBody = {}; }
        }
      });
      await gotoAlerts(page);
      await rowByName(page, ruleA.name).getByRole('button', { name: '编辑' }).click();
      await sleep(500);
      const title = await page.locator('h3', { hasText: '编辑预警规则' }).first().textContent().catch(() => null);
      const nameVal = await page.locator('input[placeholder="如：茅台大涨提醒"]').inputValue();
      const symbolVal = await page.locator('input[placeholder="600519.SH"]').inputValue().catch(() => null);
      const thresholdVal = await page.locator('input[type="number"][step="0.1"][max="30"]').inputValue().catch(() => null);
      log(`\n[1] 编辑标题=${JSON.stringify(title)}`);
      log(`      预填 name=${JSON.stringify(nameVal)} symbol=${JSON.stringify(symbolVal)} threshold=${JSON.stringify(thresholdVal)}（期望 ${ruleA.symbol} / ${ruleA.params?.threshold}）`);
      if (nameVal === ruleA.name && symbolVal === ruleA.symbol && thresholdVal === String(ruleA.params?.threshold)) pass('编辑预填全部正确');
      else { fail('编辑预填不正确'); ok = false; }

      await page.locator('input[placeholder="如：茅台大涨提醒"]').fill('R2验证-已编辑');
      await page.locator('input[type="number"][step="0.1"][max="30"]').fill('5');
      await page.getByRole('button', { name: '保存修改' }).click();
      await sleep(1400);
      log(`      PUT body=${JSON.stringify(putBody)}`);
      const need = putBody && putBody.name === 'R2验证-已编辑' && putBody.rule_type === 'price_pct'
        && putBody.scope === 'symbol' && putBody.symbol === ruleA.symbol
        && putBody.params && putBody.params.threshold === 5
        && Array.isArray(putBody.channels) && putBody.channels.includes('sse')
        && putBody.cooldown_minutes === ruleA.cooldown_minutes && putBody.enabled === ruleA.enabled;
      if (need) pass('PUT 字段完整回传（enabled/channels/cooldown 未丢）'); else { fail('PUT 字段不完整'); ok = false; }
      const listHasNew = await page.locator('table tbody').getByText('R2验证-已编辑').count();
      if (listHasNew >= 1) pass('保存后列表已刷新'); else { fail('列表未刷新'); ok = false; }
      await ctx.close();
    }

    // ============ 场景 2：启停成功（ruleB 动态） ============
    {
      const { ctx, page } = await newPage(browser);
      let putBody = null;
      page.on('request', (req) => {
        if (req.method() === 'PUT' && req.url().endsWith(`/api/v1/alerts/rules/${ruleB.id}`)) {
          try { putBody = JSON.parse(req.postData() || '{}'); } catch { putBody = {}; }
        }
      });
      await gotoAlerts(page);
      const sw = rowByName(page, ruleB.name).locator('button[role="switch"]');
      const before = await sw.getAttribute('aria-checked');
      await sw.click();
      await sleep(1400);
      const after = await sw.getAttribute('aria-checked');
      log(`\n[2] 启停成功：before=${before} after=${after} PUT.enabled=${putBody?.enabled}`);
      if (before !== after && String(putBody?.enabled) === after) pass('启停翻转生效且回传 enabled 一致');
      else { fail('启停未按预期生效'); ok = false; }
      await ctx.close();
    }

    // ============ 场景 3：启停失败 → 回滚 + 可见提示 ============
    {
      const { ctx, page } = await newPage(browser);
      await page.route(`**/api/v1/alerts/rules/${ruleB.id}`, async (route) => {
        if (route.request().method() === 'PUT') {
          await route.fulfill({ status: 200, contentType: 'application/json',
            body: JSON.stringify({ code: 50000, message: '模拟写入失败', data: null, trace_id: 'x', ts: Date.now() }) });
        } else { await route.continue(); }
      });
      await gotoAlerts(page);
      const sw = rowByName(page, ruleB.name).locator('button[role="switch"]');
      const before = await sw.getAttribute('aria-checked');
      await sw.click();
      await sleep(1600);
      const after = await sw.getAttribute('aria-checked');
      const banner = await page.getByText('模拟写入失败').count();
      log(`\n[3] 启停失败：before=${before} after=${after} 可见提示=${banner}`);
      if (after === before) pass('失败后 UI 已回滚'); else { fail('失败后未回滚'); ok = false; }
      if (banner >= 1) pass('失败有可见提示（非静默）'); else { fail('失败静默无提示'); ok = false; }
      await ctx.close();
    }

    // ============ 场景 4：加载失败 → ErrorState + 重试 ============
    {
      const { ctx, page } = await newPage(browser);
      await page.route('**/api/v1/alerts/rules', async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json',
          body: JSON.stringify({ code: 50000, message: '加载失败(模拟)', data: null, trace_id: 'y', ts: Date.now() }) });
      });
      await gotoAlerts(page);
      await sleep(1800);
      const errText = await page.getByText('数据暂时不可用').count();
      const retryBtn = await page.getByRole('button', { name: '重试' }).count();
      const spinners = await page.locator('.animate-spin').count();
      log(`\n[4] 加载失败：ErrorState=${errText} 重试按钮=${retryBtn} 旋转指示器=${spinners}`);
      if (errText >= 1 && retryBtn >= 1) pass('加载失败显示 ErrorState + 重试');
      else { fail('加载失败未正确降级'); ok = false; }
      if (spinners === 0) pass('无残留永久转圈'); else log(`  WARN  仍有 ${spinners} 处 LoadingState（事件卡片无 error 分支，见报告）`);
      await ctx.close();
    }

    // ============ 场景 5：新建流程回归 ============
    {
      const { ctx, page } = await newPage(browser);
      let postBody = null;
      page.on('request', (req) => {
        if (req.method() === 'POST' && req.url().endsWith('/api/v1/alerts/rules')) {
          try { postBody = JSON.parse(req.postData() || '{}'); } catch { postBody = {}; }
        }
      });
      await gotoAlerts(page);
      await page.getByRole('button', { name: '新建规则' }).click();
      await sleep(400);
      const cardVisible = await page.locator('h3', { hasText: '新建预警规则' }).count();
      if (cardVisible >= 1) pass('新建表单可展开'); else { fail('新建表单未展开'); ok = false; }
      await page.locator('input[placeholder="如：茅台大涨提醒"]').fill('R2验证-新建回归');
      await page.locator('input[placeholder="600519.SH"]').fill('000001.sz');
      await page.getByRole('button', { name: '创建规则' }).click();
      await sleep(1600);
      log(`\n[5] 新建 POST body=${JSON.stringify(postBody)}`);
      const good = postBody && postBody.name === 'R2验证-新建回归' && postBody.rule_type === 'price_pct'
        && postBody.scope === 'symbol' && postBody.symbol === '000001.SZ'
        && postBody.params && postBody.params.threshold === 3
        && Array.isArray(postBody.channels) && postBody.channels.includes('sse') && postBody.enabled === true;
      if (good) pass('新建流程正常（symbol 大写归一、默认参数/渠道正确）'); else { fail('新建流程异常'); ok = false; }
      const listed = await page.locator('table tbody').getByText('R2验证-新建回归').count();
      if (listed >= 1) pass('新建后列表刷新'); else { fail('新建后列表未刷新'); ok = false; }
      await ctx.close();
    }

    // ============ 场景 6：必填校验 ============
    {
      const { ctx, page } = await newPage(browser);
      await gotoAlerts(page);
      await page.getByRole('button', { name: '新建规则' }).click();
      await sleep(400);
      await page.getByRole('button', { name: '创建规则' }).click();
      await sleep(500);
      const nameErr = await page.getByText('规则名称必填').count();
      log(`\n[6] 必填校验：名称错提示=${nameErr}`);
      if (nameErr >= 1) pass('必填校验有本地可见提示'); else { fail('必填校验静默'); ok = false; }
      await ctx.close();
    }
  } catch (e) {
    ok = false;
    log('\nEXCEPTION: ' + (e?.stack || e));
  } finally {
    await browser.close();
  }

  log(`\n========== 浏览器验证结论：${ok ? 'ALL PASS' : 'HAS FAILURES'} ==========`);
  process.exit(ok ? 0 : 1);
};

main();
