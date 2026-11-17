# src/pipeline/batch.py
"""
Batch processing pipeline for document ingestion.
Handles parallel processing, progress tracking, and error recovery.
"""

import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
import json
import logging
from enum import Enum


class DocumentStatus(Enum):
    """Status of a document in the processing pipeline."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ProcessingResult:
    """Result of processing a single document."""
    file_path: str
    status: DocumentStatus
    chunks_created: int = 0
    error_message: Optional[str] = None
    processing_time_ms: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchProgress:
    """Progress tracking for batch processing."""
    total_documents: int
    processed: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    start_time: datetime = field(default_factory=datetime.now)

    @property
    def completion_percentage(self) -> float:
        """Calculate completion percentage."""
        if self.total_documents == 0:
            return 100.0
        return (self.processed / self.total_documents) * 100

    @property
    def elapsed_seconds(self) -> float:
        """Calculate elapsed time in seconds."""
        return (datetime.now() - self.start_time).total_seconds()

    @property
    def documents_per_second(self) -> float:
        """Calculate processing speed."""
        if self.elapsed_seconds == 0:
            return 0
        return self.processed / self.elapsed_seconds


class BatchProcessor:
    """
    Processes documents in batches with parallel execution.
    Handles retries, progress tracking, and checkpointing.
    """

    def __init__(
        self,
        extractor_registry,
        chunker,
        embedding_function: Callable[[str], List[float]],
        vector_store,
        max_workers: int = 4,
        batch_size: int = 100,
        checkpoint_dir: Optional[Path] = None
    ):
        """
        Initialize the batch processor.

        Args:
            extractor_registry: Registry of document extractors
            chunker: Text chunker instance
            embedding_function: Function to generate embeddings
            vector_store: Vector database client
            max_workers: Maximum parallel workers
            batch_size: Documents to process before checkpointing
            checkpoint_dir: Directory for checkpoint files
        """
        self.registry = extractor_registry
        self.chunker = chunker
        self.embed = embedding_function
        self.vector_store = vector_store
        self.max_workers = max_workers
        self.batch_size = batch_size
        self.checkpoint_dir = checkpoint_dir

        self.logger = logging.getLogger(__name__)
        self.progress = None
        self.results: List[ProcessingResult] = []

    async def process_directory(
        self,
        directory: Path,
        recursive: bool = True,
        file_patterns: List[str] = None,
        progress_callback: Callable[[BatchProgress], None] = None
    ) -> List[ProcessingResult]:
        """
        Process all documents in a directory.

        Args:
            directory: Directory containing documents
            recursive: Whether to process subdirectories
            file_patterns: Glob patterns for file selection
            progress_callback: Function called with progress updates

        Returns:
            List of processing results
        """
        # Discover documents
        documents = self._discover_documents(directory, recursive, file_patterns)

        self.logger.info(f"Discovered {len(documents)} documents to process")

        # Load checkpoint if exists
        processed_files = self._load_checkpoint()
        documents = [d for d in documents if str(d) not in processed_files]

        self.logger.info(f"Processing {len(documents)} documents (skipping {len(processed_files)} already processed)")

        # Initialize progress tracking
        self.progress = BatchProgress(total_documents=len(documents))
        self.results = []

        # Process in batches
        for batch_start in range(0, len(documents), self.batch_size):
            batch = documents[batch_start:batch_start + self.batch_size]

            # Process batch in parallel
            batch_results = await self._process_batch(batch)
            self.results.extend(batch_results)

            # Update progress
            for result in batch_results:
                self.progress.processed += 1
                if result.status == DocumentStatus.COMPLETED:
                    self.progress.successful += 1
                elif result.status == DocumentStatus.FAILED:
                    self.progress.failed += 1
                else:
                    self.progress.skipped += 1

            # Save checkpoint
            self._save_checkpoint()

            # Report progress
            if progress_callback:
                progress_callback(self.progress)

            self.logger.info(
                f"Progress: {self.progress.processed}/{self.progress.total_documents} "
                f"({self.progress.completion_percentage:.1f}%) - "
                f"{self.progress.documents_per_second:.2f} docs/sec"
            )

        return self.results

    async def _process_batch(self, documents: List[Path]) -> List[ProcessingResult]:
        """
        Process a batch of documents in parallel.

        Args:
            documents: List of document paths

        Returns:
            List of processing results
        """
        results = []

        # Use ProcessPoolExecutor for CPU-bound extraction
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all documents for processing
            future_to_doc = {
                executor.submit(self._process_single_document, doc): doc
                for doc in documents
            }

            # Collect results as they complete
            for future in as_completed(future_to_doc):
                doc = future_to_doc[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    self.logger.error(f"Failed to process {doc}: {e}")
                    results.append(ProcessingResult(
                        file_path=str(doc),
                        status=DocumentStatus.FAILED,
                        error_message=str(e)
                    ))

        return results

    def _process_single_document(self, file_path: Path) -> ProcessingResult:
        """
        Process a single document through the full pipeline.

        Args:
            file_path: Path to the document

        Returns:
            ProcessingResult with status and metrics
        """
        import time
        start_time = time.time()

        try:
            # Get appropriate extractor
            extractor = self.registry.get_extractor(file_path)

            if not extractor:
                return ProcessingResult(
                    file_path=str(file_path),
                    status=DocumentStatus.SKIPPED,
                    error_message="No extractor available for this file type"
                )

            # Extract content
            document = extractor.extract(file_path)

            if not document.content.strip():
                return ProcessingResult(
                    file_path=str(file_path),
                    status=DocumentStatus.SKIPPED,
                    error_message="Document has no extractable content"
                )

            # Chunk the content
            chunks = self.chunker.chunk(
                document.content,
                metadata={
                    'source': str(file_path),
                    'title': document.metadata.get('title', ''),
                    'file_type': document.file_type
                }
            )

            # Generate embeddings and store
            for chunk in chunks:
                embedding = self.embed(chunk.text)

                self.vector_store.add(
                    id=f"{file_path}_{chunk.chunk_index}",
                    embedding=embedding,
                    text=chunk.text,
                    metadata=chunk.metadata
                )

            processing_time = (time.time() - start_time) * 1000

            return ProcessingResult(
                file_path=str(file_path),
                status=DocumentStatus.COMPLETED,
                chunks_created=len(chunks),
                processing_time_ms=processing_time,
                metadata=document.metadata
            )

        except Exception as e:
            self.logger.exception(f"Error processing {file_path}")
            return ProcessingResult(
                file_path=str(file_path),
                status=DocumentStatus.FAILED,
                error_message=str(e),
                processing_time_ms=(time.time() - start_time) * 1000
            )

    def _discover_documents(
        self,
        directory: Path,
        recursive: bool,
        file_patterns: List[str]
    ) -> List[Path]:
        """
        Discover all documents in a directory.

        Args:
            directory: Root directory
            recursive: Search subdirectories
            file_patterns: Glob patterns to match

        Returns:
            List of document paths
        """
        patterns = file_patterns or ['*.pdf', '*.docx', '*.doc', '*.txt', '*.md', '*.html']
        documents = []

        for pattern in patterns:
            if recursive:
                documents.extend(directory.rglob(pattern))
            else:
                documents.extend(directory.glob(pattern))

        return sorted(set(documents))

    def _load_checkpoint(self) -> set:
        """
        Load checkpoint of already processed files.

        Returns:
            Set of processed file paths
        """
        if not self.checkpoint_dir:
            return set()

        checkpoint_file = self.checkpoint_dir / "processed_files.json"

        if checkpoint_file.exists():
            with open(checkpoint_file, 'r') as f:
                return set(json.load(f))

        return set()

    def _save_checkpoint(self) -> None:
        """Save checkpoint of processed files."""
        if not self.checkpoint_dir:
            return

        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_file = self.checkpoint_dir / "processed_files.json"

        processed = [r.file_path for r in self.results if r.status == DocumentStatus.COMPLETED]

        with open(checkpoint_file, 'w') as f:
            json.dump(processed, f)