"""
Main entry point for document ingestion.

Pipeline:

PDF Documents
    ↓
PDF Extraction
    ↓
Metadata Enrichment
    ↓
Chunking
    ↓
Embeddings
    ↓
PostgreSQL + pgvector
"""

import asyncio
import logging
import os

from pathlib import Path

from sentence_transformers import SentenceTransformer

from ingestion.extractor import PDFExtractor
from ingestion.chunker import RecursiveCharacterChunker
from ingestion.metadata import MetadataExtractor
from ingestion.batch import BatchProcessor, BatchProgress
from ingestion.vector_store import PgVectorStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class SimpleRegistry:
    """
    Minimal extractor registry since the original
    ExtractorRegistry class does not exist.
    """

    def __init__(self):
        self.extractor = PDFExtractor(
            enable_ocr=False
        )

    def get_extractor(self, file_path):

        suffix = Path(file_path).suffix.lower()

        if suffix == ".pdf":
            return self.extractor

        return None


def create_embedding_function():
    """
    Create batched embedding function.
    """

    model = SentenceTransformer(
        "sentence-transformers/all-MiniLM-L6-v2"
    )

    def embed(texts):

        embeddings = model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True
        )

        return embeddings.tolist()

    return embed


async def main():

    documents_dir = Path(
        os.getenv(
            "DOCUMENTS_DIR",
            "./documents"
        )
    )

    checkpoint_dir = Path(
        os.getenv(
            "CHECKPOINT_DIR",
            "./checkpoints"
        )
    )

    logger.info(
        f"Document directory: {documents_dir}"
    )

    logger.info(
        "Initializing extractor..."
    )

    registry = SimpleRegistry()

    logger.info(
        "Initializing metadata extractor..."
    )

    metadata_extractor = MetadataExtractor(
        detect_language=True,
        extract_entities=False
    )

    logger.info(
        "Initializing chunker..."
    )

    chunker = RecursiveCharacterChunker(
        chunk_size=750,
        chunk_overlap=200
    )

    logger.info(
        "Initializing embeddings..."
    )

    embed_function = create_embedding_function()

    logger.info(
        "Connecting to PostgreSQL..."
    )

    vector_store = PgVectorStore()

    processor = BatchProcessor(
        extractor_registry=registry,
        chunker=chunker,
        embedding_function=embed_function,
        vector_store=vector_store,
        metadata_extractor=metadata_extractor,
        max_workers=2,
        batch_size=8,
        checkpoint_dir=checkpoint_dir
    )

    def on_progress(
        progress: BatchProgress
    ):

        print(
            f"\r"
            f"{progress.processed}/"
            f"{progress.total_documents} "
            f"({progress.completion_percentage:.1f}%) "
            f"| Success: {progress.successful} "
            f"| Failed: {progress.failed} "
            f"| Speed: "
            f"{progress.documents_per_second:.2f}/s",
            end=""
        )

    logger.info(
        "Starting ingestion..."
    )

    results = await processor.process_directory(
        directory=documents_dir,
        recursive=True,
        file_patterns=["*.pdf"],
        progress_callback=on_progress
    )

    print("\n")
    print("=" * 60)
    print("INGESTION COMPLETE")
    print("=" * 60)

    summary = processor.summary()

    print(
        f"Processed: {summary['processed']}"
    )

    print(
        f"Successful: {summary['successful']}"
    )

    print(
        f"Failed: {summary['failed']}"
    )

    print(
        f"Skipped: {summary['skipped']}"
    )

    failures = [
        r
        for r in results
        if r.status.value == "failed"
    ]

    if failures:

        print("\nFailures:")

        for failure in failures:

            print(
                f"{failure.file_path}"
            )

            print(
                f"  {failure.error_message}"
            )

    vector_store.close()

    logger.info(
        "Shutdown complete."
    )


if __name__ == "__main__":
    asyncio.run(main())