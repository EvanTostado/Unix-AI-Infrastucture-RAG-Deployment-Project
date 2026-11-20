# main.py
"""
Complete document ingestion pipeline example.
Demonstrates the full flow from raw documents to vector store.
"""

import asyncio
from pathlib import Path
import os

# Import pipeline components
from src.extractors.base import ExtractorRegistry, FileTypeDetector
from src.extractors.pdf_extractor import PDFExtractor
from src.extractors.docx_extractor import DocxExtractor
from src.extractors.html_extractor import HTMLExtractor, MarkdownExtractor
from src.processors.chunker import RecursiveCharacterChunker
from src.processors.metadata import MetadataExtractor
from src.pipeline.batch import BatchProcessor, BatchProgress
from src.storage.vector_store import ChromaDBStore


def create_embedding_function():
    """
    Create an embedding function using OpenAI or local model.
    Returns a function that converts text to embeddings.
    """
    # Option 1: OpenAI embeddings
    if os.getenv('OPENAI_API_KEY'):
        from openai import OpenAI
        client = OpenAI()

        def embed(text: str) -> list:
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            return response.data[0].embedding

        return embed

    # Option 2: Local embeddings with sentence-transformers
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('all-MiniLM-L6-v2')

        def embed(text: str) -> list:
            return model.encode(text).tolist()

        return embed


async def main():
    """
    Main entry point for document ingestion.
    """
    # Configuration
    DOCUMENTS_DIR = Path("./documents")
    VECTOR_DB_DIR = Path("./vector_db")
    CHECKPOINT_DIR = Path("./checkpoints")

    # Initialize extractor registry
    registry = ExtractorRegistry()
    registry.register(PDFExtractor(enable_ocr=True))
    registry.register(DocxExtractor())
    registry.register(HTMLExtractor())
    registry.register(MarkdownExtractor())

    # Initialize chunker with sensible defaults
    chunker = RecursiveCharacterChunker(
        chunk_size=1000,      # ~200-250 words per chunk
        chunk_overlap=200     # 20% overlap for context
    )

    # Initialize embedding function
    embed = create_embedding_function()

    # Initialize vector store
    vector_store = ChromaDBStore(
        collection_name="documents",
        persist_directory=str(VECTOR_DB_DIR)
    )

    # Initialize batch processor
    processor = BatchProcessor(
        extractor_registry=registry,
        chunker=chunker,
        embedding_function=embed,
        vector_store=vector_store,
        max_workers=4,
        batch_size=50,
        checkpoint_dir=CHECKPOINT_DIR
    )

    # Progress callback
    def on_progress(progress: BatchProgress):
        print(
            f"\rProgress: {progress.processed}/{progress.total_documents} "
            f"({progress.completion_percentage:.1f}%) | "
            f"Success: {progress.successful} | "
            f"Failed: {progress.failed} | "
            f"Speed: {progress.documents_per_second:.2f} docs/sec",
            end=""
        )

    # Run the pipeline
    print(f"Starting ingestion from {DOCUMENTS_DIR}")

    results = await processor.process_directory(
        directory=DOCUMENTS_DIR,
        recursive=True,
        file_patterns=['*.pdf', '*.docx', '*.md', '*.html', '*.txt'],
        progress_callback=on_progress
    )

    print("\n\nIngestion complete!")
    print(f"Total documents: {len(results)}")
    print(f"Successful: {sum(1 for r in results if r.status.value == 'completed')}")
    print(f"Failed: {sum(1 for r in results if r.status.value == 'failed')}")
    print(f"Skipped: {sum(1 for r in results if r.status.value == 'skipped')}")

    # Print any errors
    errors = [r for r in results if r.error_message]
    if errors:
        print("\nErrors:")
        for r in errors[:10]:  # Show first 10 errors
            print(f"  {r.file_path}: {r.error_message}")


if __name__ == "__main__":
    asyncio.run(main())