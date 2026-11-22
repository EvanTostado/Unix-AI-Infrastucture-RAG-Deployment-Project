"""
Batch document ingestion pipeline.

Features:
- Parallel processing
- Batch embedding generation
- Checkpointing
- Progress tracking
- Retry support
- PostgreSQL/pgvector compatible
"""

import json
import logging
import time

from pathlib import Path
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

from typing import (
    List,
    Dict,
    Any,
    Optional,
    Callable
)


class DocumentStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ProcessingResult:

    file_path: str
    status: DocumentStatus

    chunks_created: int = 0

    error_message: Optional[str] = None

    processing_time_ms: float = 0

    metadata: Dict[str, Any] = field(
        default_factory=dict
    )


@dataclass
class BatchProgress:

    total_documents: int

    processed: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0

    start_time: datetime = field(
        default_factory=datetime.now
    )

    @property
    def completion_percentage(self) -> float:

        if self.total_documents == 0:
            return 100.0

        return (
            self.processed
            / self.total_documents
        ) * 100

    @property
    def elapsed_seconds(self) -> float:

        return (
            datetime.now()
            - self.start_time
        ).total_seconds()

    @property
    def documents_per_second(self) -> float:

        if self.elapsed_seconds == 0:
            return 0.0

        return (
            self.processed
            / self.elapsed_seconds
        )


class BatchProcessor:

    def __init__(
        self,
        extractor_registry,
        chunker,
        embedding_function,
        vector_store,
        metadata_extractor=None,
        max_workers: int = 4,
        batch_size: int = 100,
        checkpoint_dir: Optional[Path] = None
    ):

        self.registry = extractor_registry
        self.chunker = chunker
        self.embed = embedding_function

        self.vector_store = vector_store
        self.metadata_extractor = metadata_extractor

        self.max_workers = max_workers
        self.batch_size = batch_size

        self.checkpoint_dir = checkpoint_dir

        self.results = []
        self.progress = None

        self.logger = logging.getLogger(__name__)

    async def process_directory(
        self,
        directory: Path,
        recursive: bool = True,
        file_patterns: Optional[List[str]] = None,
        progress_callback: Optional[
            Callable[[BatchProgress], None]
        ] = None
    ) -> List[ProcessingResult]:

        documents = self._discover_documents(
            directory,
            recursive,
            file_patterns
        )

        processed_files = self._load_checkpoint()

        documents = [
            doc
            for doc in documents
            if str(doc) not in processed_files
        ]

        self.logger.info(
            f"Processing {len(documents)} files"
        )

        self.progress = BatchProgress(
            total_documents=len(documents)
        )

        self.results = []

        for batch_start in range(
            0,
            len(documents),
            self.batch_size
        ):

            batch = documents[
                batch_start:
                batch_start + self.batch_size
            ]

            batch_results = await self._process_batch(
                batch
            )

            self.results.extend(batch_results)

            for result in batch_results:

                self.progress.processed += 1

                if (
                    result.status
                    == DocumentStatus.COMPLETED
                ):
                    self.progress.successful += 1

                elif (
                    result.status
                    == DocumentStatus.FAILED
                ):
                    self.progress.failed += 1

                else:
                    self.progress.skipped += 1

            self._save_checkpoint()

            if progress_callback:
                progress_callback(
                    self.progress
                )

            self.logger.info(
                f"{self.progress.processed}/"
                f"{self.progress.total_documents} "
                f"({self.progress.completion_percentage:.2f}%)"
            )

        return self.results

    async def _process_batch(
        self,
        documents: List[Path]
    ) -> List[ProcessingResult]:

        results = []

        with ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:

            futures = {
                executor.submit(
                    self._process_single_document,
                    document
                ): document
                for document in documents
            }

            for future in as_completed(futures):

                document = futures[future]

                try:

                    result = future.result()

                    results.append(result)

                except Exception as exc:

                    self.logger.exception(
                        f"Failed processing "
                        f"{document}"
                    )

                    results.append(
                        ProcessingResult(
                            file_path=str(document),
                            status=DocumentStatus.FAILED,
                            error_message=str(exc)
                        )
                    )

        return results

    def _retry(
        self,
        function,
        retries: int = 3,
        delay: int = 2
    ):

        last_error = None

        for attempt in range(retries):

            try:
                return function()

            except Exception as exc:

                last_error = exc

    ry {attempt + 1}/{retries}: "
                    f"{exc}"
                )

                time.sleep(delay)

        raise last_error

    def _process_single_document(
        self,
        file_path: Path
    ) -> ProcessingResult:

        start_time = time.time()

        try:

            extractor = self.registry.get_extractor(
                file_path
            )

            if not extractor:

                return ProcessingResult(
                    file_path=str(file_path),
                    status=DocumentStatus.SKIPPED,
                    error_message="No extractor found"
                )

            document = self._retry(
                lambda: extractor.extract(
                    file_path
                )
            )

            if not document.content.strip():

                return ProcessingResult(
                    file_path=str(file_path),
                    status=DocumentStatus.SKIPPED,
                    error_message="Empty content"
                )

            metadata = {
                "source": str(file_path),
                "title": document.metadata.get(
                    "title",
                    file_path.stem
                ),
                "file_type": document.file_type
            }

            if self.metadata_extractor:

                enriched = (
                    self.metadata_extractor.extract(
                        content=document.content,
                        file_path=file_path,
                        document_metadata=document.metadata
                    )
                )

                metadata.update(
                    enriched.to_dict()
                )

            chunks = self.chunker.chunk(
                document.content,
                metadata=metadata
            )

            if not chunks:

                return ProcessingResult(
                    file_path=str(file_path),
                    status=DocumentStatus.SKIPPED,
                    error_message="No chunks created"
                )

            texts = [
                chunk.text
                for chunk in chunks
            ]

            embeddings = self.embed(
                texts
            )

            ids = [
                chunk.chunk_id
                for chunk in chunks
            ]

            metadatas = [
                chunk.metadata
                for chunk in chunks
            ]

            self.vector_store.add_batch(
                ids=ids,
                embeddings=embeddings,
                texts=texts,
                metadatas=metadatas
            )

            elapsed_ms = (
                time.time() - start_time
            ) * 1000

            return ProcessingResult(
                file_path=str(file_path),
                status=DocumentStatus.COMPLETED,
                chunks_created=len(chunks),
                processing_time_ms=elapsed_ms,
                metadata={
                    **document.metadata,
                    "embeddings_created": len(
                        embeddings
                    )
                }
            )

        except Exception as exc:

            self.logger.exception(
                f"Error processing "
                f"{file_path}"
            )

            return ProcessingResult(
                file_path=str(file_path),
                status=DocumentStatus.FAILED,
                error_message=str(exc),
                processing_time_ms=(
                    time.time()
                    - start_time
                ) * 1000
            )

    def _discover_documents(
        self,
        directory: Path,
        recursive: bool,
        file_patterns: Optional[List[str]]
    ) -> Listpatterns = file_patterns or [
            "*.pdf",
            "*.docx",
            "*.doc",
            "*.txt",
            "*.md",
            "*.html"
        ]

        documents = []

        for pattern in patterns:

            if recursive:
                documents.extend(
                    directory.rglob(pattern)
                )
            else:
                documents.extend(
                    directory.glob(pattern)
                )

        return sorted(
            set(documents)
        )

    def _load_checkpoint(self) -> set:

        if not self.checkpoint_dir:
            return set()

        checkpoint_file = (
            self.checkpoint_dir
            / "processed_files.json"
        )

        if not checkpoint_file.exists():
            return set()

        try:

            with open(
                checkpoint_file,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

                return set(
                    data.get(
                        "completed",
                        []
                    )
                )

        except Exception:

            return set()

    def _save_checkpoint(self) -> None:

        if not self.checkpoint_dir:
            return

        self.checkpoint_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        checkpoint_file = (
            self.checkpoint_dir
            / "processed_files.json"
        )

        completed = [
            result.file_path
            for result in self.results
            if result.status
            == DocumentStatus.COMPLETED
        ]

        failed = [
            result.file_path
            for result in self.results
            if result.status
            == DocumentStatus.FAILED
        ]

        payload = {
            "completed": completed,
            "failed": failed,
            "last_updated": (
                datetime.utcnow()
                .isoformat()
            )
        }

        with open(
            checkpoint_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                payload,
                file,
                indent=2
            )

    def summary(self) -> Dict[str, Any]:

        if not self.progress:

            return {}

        return {
            "total": self.progress.total_documents,
            "processed": self.progress.processed,
            "successful": self.progress.successful,
            "failed": self.progress.failed,
            "skipped": self.progress.skipped,
            "elapsed_seconds": round(
                self.progress.elapsed_seconds,
                2
            ),
            "documents_per_second": round(
                self.progress.documents_per_second,
                2
            )
        }