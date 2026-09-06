# docs-site

Builds the LEO Customer 360 documentation website from **every `*.md` in the repo**
using [Quartz v4](https://quartz.jzhao.xyz), deployed to GitHub Pages by
`.github/workflows/deploy-docs.yml`.

Full design + rationale: [`deployments/document/quartz-docs-site-plan.md`](../deployments/docs/quartz-docs-site-plan.md).

## What's here

| File | Purpose |
|------|---------|
| `collect.mjs` | Scans the repo for `*.md` (via `git ls-files`), filters by the root [`.documentignore`](../.documentignore), mirrors the tree into `content/` (structure preserved), **also copies the media the docs embed — images, video, audio, PDF — alongside them** so `![](path)` and `![[embed]]` render, and injects frontmatter where missing. |
| `quartz.config.ts` | Site config — title, `baseUrl`, theme, plugins (wikilinks, GFM, TOC, KaTeX, RSS/sitemap). |
| `quartz.layout.ts` | Page layout — explorer, search, graph, backlinks. |
| `package.json` | The collector's one dependency (`ignore`). |

> The **Quartz engine itself is not vendored here** — CI clones it at the pinned
> tag (`QUARTZ_REF` in the workflow) and injects these two `.ts` files. This keeps
> the product repo free of the framework's source tree.

## Local preview

Run from the repo root:

```bash
# 1. Fetch the Quartz engine (same tag CI uses)
git clone --depth 1 -b v4.5.2 https://github.com/jackyzha0/quartz.git .quartz-engine

# 2. Inject config + layout + the Docs Assistant (chatbot) component
cp docs-site/quartz.config.ts docs-site/quartz.layout.ts .quartz-engine/
mkdir -p .quartz-engine/quartz/components/scripts .quartz-engine/quartz/components/styles
cp docs-site/quartz-components/DocsChatbot.tsx                .quartz-engine/quartz/components/
cp docs-site/quartz-components/scripts/docs-chatbot.inline.ts .quartz-engine/quartz/components/scripts/
cp docs-site/quartz-components/styles/docs-chatbot.scss       .quartz-engine/quartz/components/styles/

# 3. Collect the markdown into the engine's content/
( cd docs-site && npm install && node collect.mjs --repo .. --out ../.quartz-engine/content --inject-frontmatter )

# 4. Build + serve. The chatbot calls a PUBLIC docs-vector-search API (CORS); point it at one
#    with DOCS_AI_PUBLIC_URL (defaults to the UAT docs API). DOCS_SITE_BASE sets citation links.
( cd .quartz-engine && npm install && \
  DOCS_AI_PUBLIC_URL="https://beta.leocdp.com/docs-ai" \
  DOCS_SITE_BASE="http://localhost:8080" \
  npx quartz build --serve )   # → http://localhost:8080
```

`.quartz-engine/` is disposable — delete it anytime; it should be git-ignored.

## Choosing what publishes

- **Exclude a file/folder:** add a gitignore-style pattern to the root `.documentignore`.
- **Exclude one file inline:** add `draft: true` to its frontmatter (Quartz's `RemoveDrafts()` drops it).
- **Re-include something excluded:** a leading `!` line in `.documentignore`.
