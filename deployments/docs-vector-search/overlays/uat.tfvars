# docs-vector-search (local-model RAG, FastAPI :8000) — UAT overlay.
# Deployed by deployments/docs-vector-search/deploy.sh onto its DEDICATED box
# (server key "docs" in ../server/overlays/uat.tfvars). Vectors: pgvector in the
# shared vDB (schema "rag"); DB creds resolved from ../postgres.
docs_server_key = "docs"
docs_port       = 8000
docs_pg_schema  = "rag"

# Local models — weights are fetched onto the box at deploy (Qwen GGUF via curl;
# fastembed ONNX self-downloads) and cached in /opt/c360/docs-models across deploys.
docs_embed_model    = "intfloat/multilingual-e5-small" # VN+EN, 384-dim
docs_embed_dim      = 384
docs_rerank_enabled = true                             # set false to shed ~300 MB if 2 GB is tight
docs_rerank_model   = "BAAI/bge-reranker-base"

# Image tag to deploy. CD sets IMAGE_TAG (sha-<commit>); a docs-only refresh uses
# latest. Uncomment to pin an immutable tag for a manual rollback.
# image_tag = "latest"
