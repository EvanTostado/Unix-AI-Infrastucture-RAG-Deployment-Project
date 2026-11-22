"""
Text chunking strategies for RAG document processing.
Different strategies optimize for different retrieval patterns.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any
import hashlib
import re


@dataclass
class Chunk:
    """
    Represents a single chunk of text with metadata.
    """

    text: str
    chunk_index: int
    start_char: int
    end_char: int
    metadata: Dict[str, Any]

    # Deterministic ID for idempotent ingestion
    chunk_id: str = ""

    @property
    def length(self) -> int:
        return len(self.text)


class BaseChunker(ABC):
    """
    Abstract base chunker.
    """

    @abstractmethod
    def chunk(
        self,
        text: str,
        metadata: Dict[str, Any] = None
    ) -> List[Chunk]:
        pass


class RecursiveCharacterChunker(BaseChunker):
    """
    Recursive character-based chunking.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: List[str] = None
    ):

        if chunk_size <= 0:
            raise ValueError(
                "chunk_size must be greater than 0"
            )

        if chunk_overlap < 0:
            raise ValueError(
                "chunk_overlap cannot be negative"
            )

        if chunk_overlap >= chunk_size:
            raise ValueError(
                "chunk_overlap must be less than chunk_size"
            )

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        self.separators = separators or [
            "\n\n",
            "\n",
            ". ",
            "? ",
            "! ",
            "; ",
            ", ",
            " ",
            ""
        ]

    def chunk(
        self,
        text: str,
        metadata: Dict[str, Any] = None
    ) -> List[Chunk]:

        if not text or not text.strip():
            return []

        metadata = metadata or {}

        chunks = []

        splits = self._split_text(
            text,
            self.separators
        )

        current_chunk = []
        current_length = 0

        current_start = 0
        char_position = 0

        for split in splits:

            split_length = len(split)

            if (
                current_length + split_length > self.chunk_size
                and current_chunk
            ):

                chunk_text = "".join(current_chunk)

                chunks.append(
                    self._build_chunk(
                        chunk_text,
                        len(chunks),
                        current_start,
                        metadata,
                        "recursive"
                    )
                )

                overlap_text = self._get_overlap(
                    current_chunk
                )

                current_chunk = (
                    [overlap_text]
                    if overlap_text
                    else []
                )

                current_length = len(overlap_text)

                current_start = (
                    char_position - current_length
                )

            current_chunk.append(split)
            current_length += split_length
            char_position += split_length

        if current_chunk:

            chunk_text = "".join(current_chunk)

            chunks.append(
                self._build_chunk(
                    chunk_text,
                    len(chunks),
                    current_start,
                    metadata,
                    "recursive"
                )
            )

        return chunks

    def _build_chunk(
        self,
        chunk_text: str,
        index: int,
        start_char: int,
        metadata: Dict[str, Any],
        chunk_type: str
    ) -> Chunk:

        chunk_id = hashlib.sha256(
            (
                metadata.get("source", "")
                + str(index)
                + chunk_text
            ).encode("utf-8")
        ).hexdigest()

        return Chunk(
            text=chunk_text,
            chunk_index=index,
            start_char=start_char,
            end_char=start_char + len(chunk_text),
            metadata={
                **metadata,
                "chunk_hash": chunk_id,
                "chunk_size": len(chunk_text),
                "chunk_type": chunk_type,
                "chunk_index": index
            },
            chunk_id=chunk_id
        )

    def _split_text(
        self,
        text: str,
        separators: List[str]
    ) -> List[str]:

        if not separators:
            return [text]

        separator = separators[0]
        remaining = separators[1:]

        if separator == "":
            return list(text)

        splits = text.split(separator)

        result = []

        for i, split in enumerate(splits):

            if i < len(splits) - 1:
                result.append(split + separator)
            else:
                result.append(split)

        final_splits = []

        for split in result:

            if (
                len(split) > self.chunk_size
                and remaining
            ):
                final_splits.extend(
                    self._split_text(
                        split,
                        remaining
                    )
                )
            else:
                final_splits.append(split)

        return final_splits

    def _get_overlap(
        self,
        chunks: List[str]
    ) -> str:

        full_text = "".join(chunks)

        if len(full_text) <= self.chunk_overlap:
            return full_text

        return full_text[-self.chunk_overlap:]