# src/storage/vector_store.py
"""
Vector store abstraction for storing document embeddings.
Supports multiple backends including ChromaDB, Pinecone, and Weaviate.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import numpy as np


@dataclass
class SearchResult:
    """Result of a vector similarity search."""
    id: str
    text: str
    score: float
    metadata: Dict[str, Any]


class VectorStore(ABC):
    """
    Abstract base class for vector stores.
    Provides a unified interface for different vector databases.
    """

    @abstractmethod
    def add(
        self,
        id: str,
        embedding: List[float],
        text: str,
        metadata: Dict[str, Any] = None
    ) -> None:
        """Add a single document to the store."""
        pass

    @abstractmethod
    def add_batch(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        texts: List[str],
        metadatas: List[Dict[str, Any]] = None
    ) -> None:
        """Add multiple documents to the store."""
        pass

    @abstractmethod
    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter: Dict[str, Any] = None
    ) -> List[SearchResult]:
        """Search for similar documents."""
        pass

    @abstractmethod
    def delete(self, ids: List[str]) -> None:
        """Delete documents by ID."""
        pass


class ChromaDBStore(VectorStore):
    """
    ChromaDB vector store implementation.
    Good for local development and small to medium datasets.
    """

    def __init__(
        self,
        collection_name: str,
        persist_directory: str = None,
        embedding_function = None
    ):
        """
        Initialize ChromaDB store.

        Args:
            collection_name: Name of the collection
            persist_directory: Directory for persistent storage
            embedding_function: Optional embedding function for queries
        """
        import chromadb

        # Initialize client
        if persist_directory:
            self.client = chromadb.PersistentClient(path=persist_directory)
        else:
            self.client = chromadb.Client()

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        self.embedding_function = embedding_function

    def add(
        self,
        id: str,
        embedding: List[float],
        text: str,
        metadata: Dict[str, Any] = None
    ) -> None:
        """
        Add a single document to ChromaDB.

        Args:
            id: Unique document ID
            embedding: Document embedding vector
            text: Document text
            metadata: Additional metadata
        """
        self.collection.add(
            ids=[id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {}]
        )

    def add_batch(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        texts: List[str],
        metadatas: List[Dict[str, Any]] = None
    ) -> None:
        """
        Add multiple documents to ChromaDB.

        Args:
            ids: List of unique document IDs
            embeddings: List of embedding vectors
            texts: List of document texts
            metadatas: List of metadata dicts
        """
        # ChromaDB has a limit on batch size
        batch_size = 5000

        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            batch_embeddings = embeddings[i:i + batch_size]
            batch_texts = texts[i:i + batch_size]
            batch_metadatas = metadatas[i:i + batch_size] if metadatas else None

            self.collection.add(
                ids=batch_ids,
                embeddings=batch_embeddings,
                documents=batch_texts,
                metadatas=batch_metadatas or [{}] * len(batch_ids)
            )

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter: Dict[str, Any] = None
    ) -> List[SearchResult]:
        """
        Search for similar documents in ChromaDB.

        Args:
            query_embedding: Query vector
            top_k: Number of results to return
            filter: Metadata filter conditions

        Returns:
            List of SearchResult objects
        """
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=filter,
            include=["documents", "metadatas", "distances"]
        )

        search_results = []
        for i in range(len(results['ids'][0])):
            # Convert distance to similarity score. With hnsw:space="cosine"
            # ChromaDB returns cosine distance in [0, 2], where 0 means identical.
            distance = results['distances'][0][i]
            score = 1 - (distance / 2)  # Convert to similarity in [0, 1]

            search_results.append(SearchResult(
                id=results['ids'][0][i],
                text=results['documents'][0][i],
                score=score,
                metadata=results['metadatas'][0][i]
            ))

        return search_results

    def delete(self, ids: List[str]) -> None:
        """
        Delete documents by ID.

        Args:
            ids: List of document IDs to delete
        """
        self.collection.delete(ids=ids)


class PineconeStore(VectorStore):
    """
    Pinecone vector store implementation.
    Best for production deployments with large datasets.
    """

    def __init__(
        self,
        index_name: str,
        api_key: str,
        namespace: str = ""
    ):
        """
        Initialize Pinecone store.

        Args:
            index_name: Name of the Pinecone index
            api_key: Pinecone API key
            namespace: Optional namespace for multi-tenancy
        """
        from pinecone import Pinecone

        self.pc = Pinecone(api_key=api_key)
        self.index = self.pc.Index(index_name)
        self.namespace = namespace

    def add(
        self,
        id: str,
        embedding: List[float],
        text: str,
        metadata: Dict[str, Any] = None
    ) -> None:
        """Add a single document to Pinecone."""
        metadata = metadata or {}
        metadata['text'] = text  # Store text in metadata

        self.index.upsert(
            vectors=[(id, embedding, metadata)],
            namespace=self.namespace
        )

    def add_batch(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        texts: List[str],
        metadatas: List[Dict[str, Any]] = None
    ) -> None:
        """Add multiple documents to Pinecone."""
        vectors = []

        for i, (id, embedding, text) in enumerate(zip(ids, embeddings, texts)):
            metadata = metadatas[i] if metadatas else {}
            metadata['text'] = text
            vectors.append((id, embedding, metadata))

        # Pinecone recommends batches of 100
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i:i + batch_size]
            self.index.upsert(vectors=batch, namespace=self.namespace)

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter: Dict[str, Any] = None
    ) -> List[SearchResult]:
        """Search for similar documents in Pinecone."""
        results = self.index.query(
            vector=query_embedding,
            top_k=top_k,
            filter=filter,
            include_metadata=True,
            namespace=self.namespace
        )

        search_results = []
        for match in results.matches:
            text = match.metadata.pop('text', '')
            search_results.append(SearchResult(
                id=match.id,
                text=text,
                score=match.score,
                metadata=match.metadata
            ))

        return search_results

    def delete(self, ids: List[str]) -> None:
        """Delete documents by ID."""
        self.index.delete(ids=ids, namespace=self.namespace)