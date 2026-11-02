#!/usr/bin/env node
// Renders docs/diagrams/<name>.html (written by diagrams.py and hero.py) to docs/images/<name>.png at
// twice the pixel size, with the Chromium that tools/mermaid-check installs:
//
//     python3 docs/diagrams/diagrams.py && node docs/diagrams/render.js [name ...]
//
// With a system Chromium, set PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium.
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..", "..");
const { chromium } = require(path.join(ROOT, "tools", "mermaid-check", "node_modules", "playwright"));

async function main() {
  const names = process.argv.slice(2).length ? process.argv.slice(2) : ["overview", "speaker", "hero"];
  const launch = {};
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) launch.executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  const browser = await chromium.launch(launch);
  let failed = 0;
  try {
    for (const name of names) {
      const file = path.join(__dirname, `${name}.html`);
      const out = path.join(ROOT, "docs", "images", `${name}.png`);
      const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width: 1600, height: 1200 } });
      try {
        await page.goto("file://" + file, { waitUntil: "networkidle", timeout: 30000 });
      } catch (e) {
        // Without a network the fonts fall back to the system's; the diagram still renders.
        await page.goto("file://" + file, { waitUntil: "load" });
      }
      await page.evaluate(() => document.fonts.ready);
      const el = await page.$("body > div");
      if (!el) {
        console.log(`fail ${name}: no root <div>`);
        failed++;
        continue;
      }
      await el.screenshot({ path: out });
      const box = await el.boundingBox();
      console.log(`ok   ${name}  ${Math.round(box.width)}x${Math.round(box.height)} -> ${path.relative(ROOT, out)}`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
