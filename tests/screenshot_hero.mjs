import { chromium } from 'playwright';

async function run() {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();

  await page.goto('http://localhost:8403/dashboard/', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  // Go to Playground and type a complex prompt
  await page.click('button:has-text("PLAYGROUND")');
  await page.waitForTimeout(500);
  await page.locator('textarea').fill('Design a distributed rate limiter with Redis and sliding window counters');
  await page.waitForTimeout(12000); // wait for embedding model + prediction

  // Screenshot just the main content area (no browser chrome)
  await page.screenshot({
    path: '/Users/anjieyang/Work/GradientNetwork/smart_router/UncommonRoute/docs/assets/hero-playground.png',
    clip: { x: 0, y: 0, width: 1440, height: 900 }
  });
  console.log('✓ Playground hero');

  // Also screenshot Home page
  await page.click('button:has-text("HOME")');
  await page.waitForTimeout(1500);
  await page.screenshot({
    path: '/Users/anjieyang/Work/GradientNetwork/smart_router/UncommonRoute/docs/assets/hero-home.png',
    clip: { x: 0, y: 0, width: 1440, height: 900 }
  });
  console.log('✓ Home hero');

  await browser.close();
}

run().catch(e => { console.error(e); process.exit(1); });
