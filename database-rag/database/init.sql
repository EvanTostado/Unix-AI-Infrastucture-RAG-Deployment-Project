cat > sql/init.sql <<'EOF'
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    metadata JSONB,
    embedding VECTOR(384)
);

CREATE INDEX IF NOT EXISTS embeddings_hnsw_idx
ON embeddings
USING hnsw (embedding vector_cosine_ops);
EOF