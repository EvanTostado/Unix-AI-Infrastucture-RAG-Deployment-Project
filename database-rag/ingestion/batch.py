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
    def completion_percentage(self):

        if not self.total_documents:
            return 100.0

        return (
            self.processed /
            self.total_documents
        ) * 100

    @property
    def elapsed_seconds(self):

        return (
            datetime.now() -
            self.start_time
        ).total_seconds()

    @property
    def documents_per_second(self):

        if self.elapsed_seconds == 0:
            return 0

        return (
            self.processed /
            self.elapsed_seconds
        )


class BatchProcessor:

    def __init__(
        self,
        extractor_registry,
        chunker,
        embedding_function,
        vector_store,
        metadata_extractor=None,
        max_workers=4,
        batch_size=100,
        checkpoint_dir=None
    ):

        self.registry = extractor_registry
        self.chunker = chunker
        self.embed = embedding_function
        self.vector_store = vector_store
        self.metadata_extractor = metadata_extractor

        self.max_workers = max_workers
        self.batch_size = batch_size
        self.checkpoint_dir = checkpoint_dir

        self.logger = logging.getLogger(__name__)

        self.results = []
        self.progress = None

    async def process_directory(
        self,
        directory,
        recursive=True,
        file_patterns=None,
        progress_callback=None
    ):

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

                if result.status == DocumentStatus.COMPLETED:
                    self.progress.successful += 1

                elif result.status == DocumentStatus.FAILED:
                    self.progress.failed += 1

                else:
                    self.progress.skipped += 1

            self._save_checkpoint()

            if progress_callback:
                progress_callback(
                    self.progress
                )

        return self.results

    async def _process_batch(
        self,
        documents
    ):

        results = []

        with ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:

            futures = {
                executor.submit(
                    self._process_single_document,
                    doc
                ): doc
                for doc in documents
            }

            for future in as_completed(
                futures
            ):
                results.append(
                    future.result()
                )

        return results

    def _process_single_document(
        self,
        file_path
    ):

        start = time.time()

        try:

            extractor = (
                self.registry
                .get_extractor(file_path)
            )

            if not extractor:

                return ProcessingResult(
                    file_path=str(file_path),
                    status=DocumentStatus.SKIPPED,
                    error_message="No extractor"
                )

            document = extractor.extract(
                file_path
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

            texts = [
                chunk.text
                for chunk in chunks
            ]

            embeddings = self.embed(
                texts
            )

            self.vector_store.add_batch(
                ids=[
                    chunk.chunk_id
                    for chunk in chunks
                ],
                embeddings=embeddings,
                texts=texts,
                metadatas=[
                    chunk.metadata
                    for chunk in chunks
                ]
            )

            return ProcessingResult(
                file_path=str(file_path),
                status=DocumentStatus.COMPLETED,
                chunks_created=len(chunks),
                processing_time_ms=(
                    time.time() - start
                ) * 1000
            )

        except Exception as exc:

            self.logger.exception(
                f"Error processing {file_path}"
            )

            return ProcessingResult(
                file_path=str(file_path),
                status=DocumentStatus.FAILED,
                error_message=str(exc)
            )

    def _discover_documents(
        self,
        directory,
        recursive,
        file_patterns
    ):

        patterns = file_patterns or [
            "*.pdf"
        ]

        docs = []

        for pattern in patterns:

            if recursive:
                docs.extend(
                    directory.rglob(pattern)
                )
            else:
                docs.extend(
                    directory.glob(pattern)
                )

        return sorted(set(docs))

    def _load_checkpoint(self):

        if not self.checkpoint_dir:
            return set()

        checkpoint = (
            self.checkpoint_dir /
            "processed_files.json"
        )

        if not checkpoint.exists():
            return set()

        try:

            with open(
                checkpoint,
                "r"
            ) as file:

                return set(
                    json.load(file)
                )

        except Exception:
            return set()

    def _save_checkpoint(self):

        if not self.checkpoint_dir:
            return

        self.checkpoint_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        checkpoint = (
            self.checkpoint_dir /
            "processed_files.json"
        )

        processed = [
            result.file_path
            for result in self.results
            if result.status ==
            DocumentStatus.COMPLETED
        ]

        with open(
            checkpoint,
            "w"
        ) as file:

            json.dump(
                processed,
                file,
                indent=2
            )

    def summary(self):

        if not self.progress:
            return {}

        return {
            "processed":
                self.progress.processed,
            "successful":
                self.progress.successful,
            "failed":
                self.progress.failed,
            "skipped":
                self.progress.skipped
        }