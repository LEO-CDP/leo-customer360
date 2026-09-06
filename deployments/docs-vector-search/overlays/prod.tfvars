# docs-vector-search (local-model RAG, FastAPI :8000) — PROD overlay.
# Deployed by deployments/docs-vector-search/deploy.sh onto its DEDICATED box
# (server key "docs" in ../server/overlays/prod.tfvars). Vectors: pgvector in the
# shared PROD vDB (schema "rag"); DB creds resolved from ../postgres.
docs_server_key = "docs"
docs_port       = 8000
docs_pg_schema  = "rag"

# Local models — fetched onto the box at deploy, cached in /opt/c360/docs-models.
# The prod box is 2 vCPU / 4 GB (gen-2 floor), so the reranker stays on.
docs_embed_model    = "intfloat/multilingual-e5-small" # VN+EN, 384-dim
docs_embed_dim      = 384
docs_rerank_enabled = true
docs_rerank_model   = "BAAI/bge-reranker-base"

# Image tag to deploy. A prod release deploys the vX.Y.Z tag; CD sets IMAGE_TAG.
# Uncomment to pin an immutable tag for a manual rollback.
# image_tag = "latest"
