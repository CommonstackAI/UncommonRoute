/**
 * E2E Dashboard Test — Playwright
 *
 * Tests:
 * 1. Every page loads without console errors
 * 2. Playground: type prompts → verify tier predictions
 * 3. Screenshots of every page for visual review
 */

import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE = 'http://localhost:8403/dashboard/';
const SCREENSHOT_DIR = '/Users/anjieyang/Work/GradientNetwork/smart_router/UncommonRoute/tests/screenshots';

mkdirSync(SCREENSHOT_DIR, { recursive: true });

const PAGES = ['HOME', 'PLAYGROUND', 'EXPLAIN', 'ROUTING', 'MODELS', 'ACTIVITY', 'CONNECTIONS', 'BUDGET', 'FEEDBACK'];

const PLAYGROUND_TESTS = [
  { prompt: 'prove me Kepler conjecture', expectTier: 'HIGH', desc: 'complex math' },
  { prompt: 'What is 2+2?', expectTier: 'LOW', desc: 'trivial' },
  { prompt: 'Hi there', expectTier: 'LOW', desc: 'greeting' },
  { prompt: 'Write a distributed consensus algorithm with Raft protocol, leader election, log replication, and comprehensive error handling', expectTier: 'HIGH', desc: 'complex engineering' },
  { prompt: 'Summarize REST vs GraphQL', expectTier: 'MID', desc: 'medium task' },
];

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  let passed = 0;
  let failed = 0;

  // ─── Test 1: Load dashboard ───
  console.log('\n=== Loading dashboard ===');
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  console.log('✓ Dashboard loaded');
  passed++;

  // ─── Test 2: Screenshot every page ───
  console.log('\n=== Screenshotting all pages ===');
  for (const pageName of PAGES) {
    try {
      // Click nav item
      await page.click(`button:has-text("${pageName}")`, { timeout: 5000 });
      await page.waitForTimeout(1500); // Wait for data to load
      await page.screenshot({ path: `${SCREENSHOT_DIR}/${pageName.toLowerCase()}.png`, fullPage: true });
      console.log(`✓ ${pageName} — screenshot saved`);
      passed++;
    } catch (e) {
      console.log(`✗ ${pageName} — ${e.message}`);
      failed++;
    }
  }

  // ─── Test 3: Playground tier predictions ───
  console.log('\n=== Playground prediction tests ===');

  // Navigate to Playground
  await page.click('button:has-text("PLAYGROUND")', { timeout: 5000 });
  await page.waitForTimeout(500);

  for (const test of PLAYGROUND_TESTS) {
    try {
      // Clear and type prompt
      const textarea = page.locator('textarea');
      await textarea.fill('');
      await textarea.fill(test.prompt);

      // Wait for debounced API call + embedding model load (first call is slow)
      await page.waitForTimeout(8000);

      // Read predicted tier
      const tierEl = page.locator('text=PREDICTED TIER').locator('..').locator('.font-display');
      const tierText = await tierEl.textContent({ timeout: 5000 });
      const tier = tierText?.trim().toUpperCase() || 'UNKNOWN';

      // Check signals are rendered
      const signals = await page.locator('text=SIGNAL READOUT').count();

      // Determine pass/fail
      const tierMatches =
        (test.expectTier === 'LOW' && tier === 'LOW') ||
        (test.expectTier === 'HIGH' && (tier === 'HIGH' || tier === 'MID_HIGH')) ||
        (test.expectTier === 'MID' && (tier === 'MID' || tier === 'MID_HIGH'));

      if (tierMatches && signals > 0) {
        console.log(`✓ "${test.desc}" → ${tier} (expected ${test.expectTier})`);
        passed++;
      } else {
        console.log(`✗ "${test.desc}" → ${tier} (expected ${test.expectTier}), signals=${signals}`);
        failed++;
      }

      // Screenshot this prediction
      await page.screenshot({ path: `${SCREENSHOT_DIR}/playground_${test.desc.replace(/\s+/g, '_')}.png` });

    } catch (e) {
      console.log(`✗ "${test.desc}" — error: ${e.message}`);
      failed++;
    }
  }

  // ─── Test 4: Console errors ───
  console.log('\n=== Console errors ===');
  const realErrors = consoleErrors.filter(e => !e.includes('favicon') && !e.includes('HMR'));
  if (realErrors.length === 0) {
    console.log('✓ No console errors');
    passed++;
  } else {
    console.log(`✗ ${realErrors.length} console errors:`);
    realErrors.forEach(e => console.log(`  - ${e}`));
    failed++;
  }

  // ─── Summary ───
  console.log(`\n${'═'.repeat(40)}`);
  console.log(`PASSED: ${passed}  FAILED: ${failed}  TOTAL: ${passed + failed}`);
  console.log(`Screenshots: ${SCREENSHOT_DIR}/`);
  console.log(`${'═'.repeat(40)}\n`);

  await browser.close();
  process.exit(failed > 0 ? 1 : 0);
}

run().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
