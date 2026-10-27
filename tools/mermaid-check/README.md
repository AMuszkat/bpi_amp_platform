# mermaid-check

Renders every ```mermaid block of a Markdown file with the pinned Mermaid build, so a
diagram that does not parse is caught before it is pushed (GitHub would show an error box
instead of the diagram). The design documents in `docs/` depend on it.

```bash
cd tools/mermaid-check && npm install && npx playwright install chromium   # once
node tools/mermaid-check/check.js docs/remote-plugin-host.md               # check
node tools/mermaid-check/check.js docs/remote-plugin-host.md out/          # check and keep SVG + PNG
```

With a system Chromium, skip the browser download and set
`PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium`.

The PostToolUse hook `.claude/hooks/post-edit.py` runs the check after every edit of a
Markdown file under `docs/` or `tests/` when `node_modules` is present, and says so when it
is not. A label that starts with `1. ` is the usual failure: Mermaid reads it as a Markdown
list.
