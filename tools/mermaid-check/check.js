// Checks that every ```mermaid block of a Markdown file parses, by rendering each one with the
// pinned mermaid build in a headless Chromium; optionally keeps the SVG and PNG of each.
//
//   node check.js <file.md> [outdir]
//
// Exit 1 when a diagram fails, with the parse error. Needs `npm install` in this folder
// (mermaid and playwright, which downloads its own Chromium) or a system Chromium in
// PLAYWRIGHT_CHROMIUM_EXECUTABLE. The PostToolUse hook .claude/hooks/post-edit.py runs it
// after a doc edit when it is installed.
const fs = require('fs'); const path = require('path'); const os = require('os');
const { chromium } = require('playwright');
const [,, mdPath, outArg] = process.argv;
if (!mdPath) { console.error('usage: node check.js <file.md> [outdir]'); process.exit(2); }
const outDir = outArg || fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-check-'));
const md = fs.readFileSync(mdPath, 'utf8');
const blocks = [...md.matchAll(/```mermaid\n([\s\S]*?)```/g)].map(m => m[1]);
// optional names: an HTML comment right before the block:  <!-- diagram: name -->
const names = [...md.matchAll(/<!--\s*diagram:\s*([\w-]+)\s*-->\s*\n```mermaid/g)].map(m => m[1]);
fs.mkdirSync(outDir, { recursive: true });
const mermaidJs = fs.readFileSync(path.join(__dirname, 'node_modules/mermaid/dist/mermaid.min.js'), 'utf8');
(async () => {
  const exe = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  const browser = await chromium.launch(exe ? { executablePath: exe } : {});
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 2 });
  await page.setContent('<!doctype html><html><body style="margin:0;background:#fff"><div id="d"></div></body></html>');
  await page.addScriptTag({ content: mermaidJs });
  await page.evaluate(() => mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose',
      flowchart: { htmlLabels: true, curve: 'basis' }, sequence: { mirrorActors: false } }));
  let failed = 0;
  for (let i = 0; i < blocks.length; i++) {
    const name = names[i] || `diagram-${i + 1}`;
    try {
      const svg = await page.evaluate(async (src) => { const { svg } = await mermaid.render('m' + Math.random().toString(36).slice(2), src); return svg; }, blocks[i]);
      fs.writeFileSync(path.join(outDir, `${name}.svg`), svg);
      const mw = (svg.match(/max-width:\s*([\d.]+)px/) || [])[1];
      const w = mw ? Math.ceil(parseFloat(mw)) + 16 : 1200;
      await page.setContent(`<!doctype html><html><body style="margin:0;background:#fff;padding:8px;width:${w}px">${svg}</body></html>`);
      const el = await page.$('svg');
      await el.screenshot({ path: path.join(outDir, `${name}.png`) });
      const box = await el.boundingBox();
      console.log(`ok   ${name}  ${Math.round(box.width)}x${Math.round(box.height)}`);
    } catch (e) { failed++; console.log(`FAIL ${name}: ${String(e.message || e).split('\n')[0]}`); }
  }
  await browser.close();
  console.log(`${blocks.length - failed}/${blocks.length} rendered`);
  process.exit(failed ? 1 : 0);
})();
