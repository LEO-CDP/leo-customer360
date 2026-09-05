# docs-vector-search

Graph-RAG over the LEO Customer 360 documentation corpus — semantic search + a
question-answering agent that cites its sources. Modeled on `C:\vault-graph`.
**Runs entirely on OpenAI** (chat + embeddings).

Full design + rationale: [`deployments/document/ai-vector-search-plan.md`](../../deployments/document/ai-vector-search-plan.md).

## How it works

```
docs/ *.md
  │  enrich.py   embed each doc + extract entities/relations (OpenAI), cache → data/embeddings.json
  ▼
agent.py        embed question → cosine top-k → expand along links + shared entities → answer with OpenAI, cite sources
  ▼
server.py       FastAPI: POST /ask, POST /search, GET /health   (index loaded once at startup)
```

`src/providers.py` is the single integration seam — `chat()` and `embed()`, both OpenAI:
- **`chat()`** → `chat.completions` (`CHAT_MODEL`, default `gpt-4.1`); structured extraction via `response_format` json_schema.
- **`embed()`** → `embeddings` (`EMBED_MODEL`, default `text-embedding-3-small`).

## Credentials

The OpenAI SDK only auto-reads `OPENAI_API_KEY`. This project also accepts the CI
names, which take precedence:

| Env var | Kind | Used for |
|---------|------|----------|
| `LEO_OPENAI_API_KEY` | GitHub Actions **secret** | OpenAI API key (falls back to `OPENAI_API_KEY`) |
| `LEO_OPENAI_MODEL_NAME` | GitHub Actions **variable** | chat model (falls back to `CHAT_MODEL`, then `gpt-4.1`) |

The embeddings model is set separately via `EMBED_MODEL`.

## Quick start

```bash
cd tools/docs-vector-search
python -m venv .venv && . .venv/Scripts/activate     # (Linux/mac: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env                                 # then set the OpenAI key/model
export LEO_OPENAI_API_KEY=sk-...                     # or OPENAI_API_KEY

python -m src.enrich                                 # build index (embeddings + graph)
python -m src.agent "How does identity resolution merge two profiles?"
uvicorn src.server:app --port 8000

curl -s localhost:8000/health
curl -s localhost:8000/search -H 'content-type: application/json' -d '{"query":"tenant isolation"}'
curl -s localhost:8000/ask    -H 'content-type: application/json' -d '{"question":"What is CIR?"}'
```

> An OpenAI key is required for everything except `/health` — `enrich`, `/search`
> (it embeds the query), and `/ask` all call OpenAI.

## Commands

| Command | Does |
|---------|------|
| `python -m src.enrich` | embed changed docs + extract graph → `data/embeddings.json` (idempotent) |
| `python -m src.enrich --no-extract` | embeddings only (skip the LLM extraction pass) |
| `python -m src.enrich --dry-run` | show what would change, write nothing |
| `python -m src.agent "…"` | one-shot question; `--top-k`, `--hops`, `--show-context` |
| `uvicorn src.server:app` | serve `/ask`, `/search`, `/health` |

## Endpoints

| Endpoint | Body | Returns |
|----------|------|---------|
| `POST /ask` | `{question, top_k?, hops?}` | `{answer, sources[], used_docs[]}` (chat + embeddings) |
| `POST /search` | `{query, top_k?}` | `{hits[]}` — ranked docs, embeddings only (no chat) |
| `GET /health` | — | readiness, loaded doc count, models, index build time |

## Deploy

Docker (mirrors `vault-graph` — the corpus is bind-mounted):

```bash
docker build -t docs-vector-search tools/docs-vector-search
docker run -p 8000:8000 \
  -e LEO_OPENAI_API_KEY=sk-... -e LEO_OPENAI_MODEL_NAME=gpt-4.1 \
  -v "$PWD/docs:/app/corpus:ro" \
  docs-vector-search
```

In **GitHub Actions**, pass the secret + variable as env:

```yaml
    env:
      LEO_OPENAI_API_KEY:    ${{ secrets.LEO_OPENAI_API_KEY }}
      LEO_OPENAI_MODEL_NAME: ${{ vars.LEO_OPENAI_MODEL_NAME }}
```

Refresh the index when `docs/**` changes (a CI job or scheduled run). On GKE/Cloud
Run use mounted secrets rather than `-e` flags.

## Notes

- **Idempotent enrich:** a content hash over the human text skips unchanged docs. Changing `EMBED_MODEL` invalidates the whole cache (documents and queries must share one embedder).
- **Graph metadata lives in the cache**, not in the source `.md` files. Entities/relations + link edges drive `expand()`.
- **`/ask` returns JSON** (non-streaming) in this version; streaming is a documented enhancement.
- **Multi-tenancy:** this indexes *public product docs*. If it ever indexes tenant/customer data, add a `tenant_id` + RLS and a tenant-keyed cache first — see the plan's §5.
