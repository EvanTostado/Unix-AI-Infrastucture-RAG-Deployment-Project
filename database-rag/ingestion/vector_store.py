import os
import json
import psycopg2

from typing import List, Dict, Any

from pgvector.psycopg2 import register_vector


UPSERT_SQL = """
INSERT INTO embeddings (
    id,
    text,
    metadata,
    embedding
)
VALUES (%s, %s, %s, %s)
ON CONFLICT (id)
DO UPDATE SET
    text = EXCLUDED.text,
    metadata = EXCLUDED.metadata,
    embedding = EXCLUDED.embedding
"""


class PgVectorStore:
    """
    PostgreSQL + pgvector implementation.
    """

    def __init__(self):

        self.conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "postgres"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "ragdb"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD")
        )

        register_vector(self.conn)

        self.cursor = self.conn.cursor()
        
    def close(self):

        if self.cursor:
            self.cursor.close()

        if self.conn:
            self.conn.close()
            

    def add(
        self,
        id: str,
        embedding: List[float],
        text: str,
        metadata: Dict[str, Any] = None
    ) -> None:

        self.cursor.execute(
            UPSERT_SQL,
            (
                id,
                text,
                json.dumps(metadata or {}),
                embedding
            )
        )

        self.conn.commit()

    def add_batch(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        texts: List[str],
        metadatas: List[Dict[str, Any]] = None
    ) -> None:

        metadatas = metadatas or [{} for _ in ids]

        rows = [
            (
                doc_id,
                text,
                json.dumps(metadata),
                embedding
            )
            for doc_id, embedding, text, metadata in zip(
                ids,
                embeddings,
                texts,
                metadatas
            )
        ]

        self.cursor.executemany(
            UPSERT_SQL,
            rows
        )

        self.conn.commit()