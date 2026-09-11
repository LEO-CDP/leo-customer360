"""Document vector-search agent backed by pgvector on the VNGCloud vDB.
Chunk → embed → store (pgvector) → retrieve → rerank (bge) → provider generation."""
