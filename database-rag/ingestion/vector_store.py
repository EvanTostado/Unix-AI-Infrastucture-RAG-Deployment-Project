import os
import json
import psycopg2

from pgvector.psycopg2 import register_vector


class PgVectorStore(VectorStore):
    """
    PostgreSQL + pgvector implementation.

    Good for self-hosted RAG systems and Docker deployments.
    """

    def __init__(self):
        """
        Initialize PostgreSQL connection.
        """

        self.conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "db"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "ragdb"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD")
        )

        register_vector(self.conn)

        self.cursor = self.conn.cursor()

    def add(
        self,
        id: str,
        embedding: List[float],
        text: str,
        metadata: Dict[str, Any] = None
    ) -> None:
        """
        Add a single document embedding.
        """

        metadata = metadata or {}

        self.cursor.execute(
            """
            INSERT INTO embeddings
            (
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
            """,
            (
                id,
                text,
                json.dumps(metadata),
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
        """
        Batch insert documents.
        """

        metadatas = metadatas or [{} for _ in ids]

        rows = []

        for doc_id, embedding, text, metadata in zip(
            ids,
            embeddings,
            texts,
            metadatas
        ):
            rows.append(
                (
                    doc_id,
                    text,
                    json.dumps(metadata),
                    embedding
                )
            )

        self.cursor.executemany(
      