// Optional browser verification: npm install playwright, or supply ALOGS_PLAYWRIGHT_MODULE.
import assert from 'node:assert/strict';
const {chromium} = await import(process.env.ALOGS_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.ALOGS_TEST_URL || 'http://localhost:4480/';
const browser = await chromium.launch({headless:true, channel:process.env.ALOGS_BROWSER_CHANNEL || 'chrome'});
const context = await browser.newContext({viewport:{width:1440,height:1000},
  permissions:['clipboard-read','clipboard-write']});
const page = await context.newPage();
const errors = [], requests = [];
page.on('pageerror', e=>errors.push(e.message));
page.on('request', r=>requests.push({url:r.url(),method:r.method()}));
try {
  await page.goto(base, {waitUntil:'networkidle'});
  assert.ok(await page.locator('.news-row').count()>0);
  await page.locator('#refresh').click();
  await page.getByText('已刷新', {exact:true}).waitFor();
  const date = await page.locator('#global-date').inputValue();
  const save = page.locator('.news-row .save-button').first();
  await save.click();
  assert.equal(await save.getAttribute('aria-pressed'),'true');
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('.news-row .save-button').first().getAttribute('aria-pressed'),'true');
  await page.locator('[data-module="builders"]').click();
  assert.equal(await page.locator('#global-date').inputValue(),date);
  assert.ok(await page.locator('.opinion-row').count()>0);
  await page.locator('.opinion-row summary').first().click();
  assert.ok(await page.locator('.opinion-row .original-posts').first().isVisible());
  await page.locator('#global-date').selectOption('all');
  await page.locator('[data-view="podcasts"]').click();
  assert.ok(await page.locator('.podcast').count()>0);
  const transcript = page.locator('.podcast .transcript').first();
  if(await transcript.count()) {
    await page.locator('.podcast summary').first().click();
    assert.ok((await transcript.textContent()).trim().length>100);
  }
  await page.locator('[data-module="github"]').click();
  await page.locator('#global-date').selectOption(date);
  const projects = page.locator('.resources-grid').first().locator('.resource');
  assert.ok(await projects.count()>0 && await projects.count()<=5);
  await page.locator('[data-view="skills"]').click();
  const skills = page.locator('.resources-grid').first().locator('.resource');
  assert.ok(await skills.count()>0 && await skills.count()<=5);
  const firstSkill = await skills.first().getAttribute('data-record');
  await skills.first().locator('[data-copy]').click();
  await page.getByText('安装话术已复制', {exact:true}).waitFor();
  const clipboard = await page.evaluate(()=>navigator.clipboard.readText());
  assert.match(clipboard,/https:\/\/github.com\//);
  await skills.first().locator('[data-save]').click();
  await page.locator('[data-view="saved"]').click();
  assert.ok(await page.locator(`.resource[data-record="${firstSkill}"]`).count());
  await page.locator('[data-module="news"]').click();
  await page.locator('#side-nav summary').click();
  await page.locator('[data-view="digest"]').click();
  assert.ok(await page.locator('.daily-report').count()>0);
  await page.locator('[data-view="hot"]').click();
  assert.ok(await page.locator('.hot-row').count()>0);
  await page.locator('#theme').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.setViewportSize({width:320,height:780});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await context.setOffline(true);
  await page.locator('#refresh').click();
  await page.getByText('网络读取失败，已保留当前内容', {exact:true}).waitFor();
  assert.ok(await page.locator('.hot-row').count()>0);
  assert.deepEqual(errors,[]);
  assert.ok(requests.every(r=>r.method==='GET'));
  assert.ok(requests.every(r=>!new URL(r.url).pathname.includes('/api/')));
  if(base.startsWith('https:'))assert.ok(requests.every(r=>!r.url.includes('localhost')));
  console.log(JSON.stringify({base,modules:3,dates:true,save:true,copy:true,
    daily:true,podcasts:true,hot:true,responsive:[1440,390,320],offline:true,
    publicDataOnly:true,errors}));
} finally {
  await browser.close();
}
