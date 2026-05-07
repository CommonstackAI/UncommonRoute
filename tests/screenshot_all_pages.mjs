import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE = 'http://localhost:8403/dashboard/';
const DIR = '/Users/anjieyang/Work/GradientNetwork/smart_router/UncommonRoute/tests/screenshots';
mkdirSync(DIR, { recursive: true });

const PAGES = ['HOME','PLAYGROUND','EXPLAIN','ROUTING','MODELS','ACTIVITY','CONNECTIONS','BUDGET','FEEDBACK'];

async function run() {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  for (const name of PAGES) {
    await page.click(`button:has-text("${name}")`, { timeout: 5000 });
    await page.waitForTimeout(1500);
    // For playground, type a complex prompt so we see results
    if (name === 'PLAYGROUND') {
      await page.locator('textarea').fill('Implement a B-tree with concurrent access, crash recovery, and MVCC support');
      await page.waitForTimeout(10000); // wait for embedding model + prediction
    }
    await page.screenshot({ path: `${DIR}/${name.toLowerCase()}.png`, fullPage: true });
    console.log(`✓ ${name}`);
  }
  await browser.close();
}
run().catch(e => { console.error(e); process.exit(1); });
