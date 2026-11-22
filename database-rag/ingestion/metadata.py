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
            rows
        )

        self.conn.commit()

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter: Dict[str, Any] = None
    ) -> List"""
        Search using cosine similarity.
        """

        if filter:
            where_conditions = []
            values = [query_embedding]

            for key, value in filter.items():
                where_conditions.append(
                    f"metadata->>'{key}' = %s"
                )
                values.append(str(value))

            values.append(top_k)

            sql = f"""
                SELECT
                    id,
                    text,
                    metadata,
                    embedding <=> %s AS distance
                FROM embeddings
                WHERE {' AND '.join(where_conditions)}
                ORDER BY distance ASC
                LIMIT %s
            """

            self.cursor.execute(sql, values)

        else:
            self.cursor.execute(
                """
                SELECT
                    id,
                    text,
                    metadata,
                    embedding <=> %s AS distance
                FROM embeddings
                ORDER BY distance ASC
                LIMIT %s
                """,
                (
                    query_embedding,
                    top_k
                )
            )

        rows = self.cursor.fetchall()

        results = []

        for row in rows:

            similarity = max(
                0.0,
                1.0 - float(row[3])
            )

            results.append(
                SearchResult(
                    id=row[0],
                    text=row[1],
                    score=similarity,
                    metadata=row[2] or {}
                )
            )

        return results

    def delete(
        self,
        ids: List[str]
    ) -> None:
        """
        Delete vectors by ID.
        """

        self.cursor.execute(
            """
            DELETE FROM embeddings
            WHERE id = ANY(%s)
            """,
            (ids,)
        )

        self.conn.commit()

    def count(self) -> int:
        """
        Return document count.
        """

        self.cursor.execute(
            "SELECT COUNT(*) FROM embeddings"
        )

        return self.cursor.fetchone()[0]

    def clear(self) -> None:
        """
        Delete all vectors.
        """

        self.cursor.execute(
            "TRUNCATE TABLE embeddings"
        )

        self.conn.commit()

    def close(self) -> None:
        """
        Close database connection.
        """

        self.cursor.close()
        self.conn.close()