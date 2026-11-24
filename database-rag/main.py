"""
Document Ingestion Pipeline

Flow:

PDF Documents
    ↓
PDF Extraction
    ↓
Metadata Enrichment
    ↓
Chunking
    ↓
Ollama Embeddings (nomic-embed-text)
    ↓
PostgreSQL + pgvector
"""

import asyncio
import logging
import os
import requests

from pathlib import Path

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

    def __init__(self):

        self.extractor = PDFExtractor(
            enable_ocr=False
        )

    def get_extractor(self, file_path):

        if Path(file_path).suffix.lower() == ".pdf":
            return self.extractor

        return None


def create_embedding_function():
    """
    Uses Ollama's nomic-embed-text model
    instead of sentence-transformers.
    """

    ollama_url = os.getenv(
        "OLLAMA_URL",
        "http://ai-lb:11434"
    )

    def embed(texts):

        embeddings = []

        for text in texts:

            response = requests.post(
                f"{ollama_url}/api/embeddings",
                json={
                    "model": "nomic-embed-text",
                    "prompt": text
                },
                timeout=120
            )

            response.raise_for_status()

            embeddings.append(
                response.json()["embedding"]
            )

        return embeddings

    return embed


def merge_startup_pdfs(documents_dir, extractor):
    """
    Optional PDF merge step.

    If files named merge_a.pdf and merge_b.pdf
    exist they are combined into merged.pdf.
    """

    pdf_a = documents_dir / "merge_a.pdf"
    pdf_b = documents_dir / "merge_b.pdf"

    merged_output = documents_dir / "merged.pdf"

    if pdf_a.exists() and pdf_b.exists():

        logger.info(
            f"Merging {pdf_a.name} and {pdf_b.name}"
        )

        extractor.merge_pdfs(
            str(pdf_a),
            str(pdf_b),
            str(merged_output)
        )

        logger.info(
            f"Created {merged_output.name}"
        )


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

    merge_startup_pdfs(
        documents_dir,
        registry.extractor
    )

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
        max_workers=1,
        batch_size=8,
        checkpoint_dir=checkpoint_dir
    )

    def on_progress(progress: BatchProgress):

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

    print(f"Processed: {summary.get('processed', 0)}")
    print(f"Successful: {summary.get('successful', 0)}")
    print(f"Failed: {summary.get('failed', 0)}")
    print(f"Skipped: {summary.get('skipped', 0)}")

    failures = [
        result
        for result in results
        if result.status.value == "failed"
    ]

    if failures:

        print("\nFailures:")

        for failure in failures:

            print(f"{failure.file_path}")
            print(f"  {failure.error_message}")

    vector_store.close()

    logger.info(
        "Shutdown complete."
    )


if __name__ == "__main__":
    asyncio.run(main())