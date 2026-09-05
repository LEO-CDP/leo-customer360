# Documentation & Knowledge Plans

Implementation plans for turning the repo's `docs/` corpus into first-class documentation surfaces — one for humans, one for AI.

| Plan | What it delivers | Reference |
|---|---|---|
| [Quartz docs site](quartz-docs-site-plan.md) | Publishes **every `*.md` in the repo** (minus `.documentignore`, folder structure preserved, frontmatter + wikilinks added) as a searchable, cross-linked website via GitHub Actions → GitHub Pages | `C:\quartz` (Quartz v4) |
| [AI-agent vector search](ai-vector-search-plan.md) | An AI agent that answers questions over `docs/` with semantic retrieval + cited sources (FastAPI service) | `C:\vault-graph` (Graph-RAG) |

The Quartz site is the human-facing view (whole-repo markdown); the vector-search agent is the AI/programmatic view (currently `docs/`, and can reuse the same [`.documentignore`](../../.documentignore) to pick its corpus). The AI enrichment writes its metadata into HTML-comment blocks so it stays invisible in the rendered Quartz pages.

The exclusion list lives at the repo root: [`.documentignore`](../../.documentignore).

> These are planning documents. Each ends with a **Decisions to confirm** section flagging the choices that are yours to make before implementation begins.
