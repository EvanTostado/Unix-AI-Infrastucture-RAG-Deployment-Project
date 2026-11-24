from dataclasses import dataclass
from typing import List, Dict, Any
import hashlib


@dataclass
class Chunk:
    text: str
    chunk_index: int
    start_char: int
    end_char: int
    metadata: Dict[str, Any]
    chunk_id: str = ""


class RecursiveCharacterChunker:

    def __init__(
        self,
        chunk_size=1000,
        chunk_overlap=200,
        separators=None
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        self.separators = separators or [
            "\n\n", "\n", ". ", "? ",
            "! ", "; ", ", ", " ", ""
        ]

    def chunk(self, text, metadata=None):

        if not text.strip():
            return []

        metadata = metadata or {}

        splits = self._split_text(
            text,
            self.separators
        )

        chunks = []
        current = []
        length = 0
        start = 0
        pos = 0

        for part in splits:

            if (
                length + len(part) > self.chunk_size
                and current
            ):
                chunk_text = "".join(current)

                chunks.append(
                    self._build_chunk(
                        chunk_text,
                        len(chunks),
                        start,
                        metadata
                    )
                )

                overlap = self._get_overlap(current)

                current = [overlap] if overlap else []

                length = len(overlap)

                start = pos - length

            current.append(part)

            length += len(part)

            pos += len(part)

        if current:
            chunks.append(
                self._build_chunk(
                    "".join(current),
                    len(chunks),
                    start,
                    metadata
                )
            )

        return chunks

    def _build_chunk(
        self,
        text,
        index,
        start,
        metadata
    ):

        chunk_id = hashlib.sha256(
            (
                metadata.get("source", "")
                + str(index)
                + text
            ).encode()
        ).hexdigest()

        return Chunk(
            text=text,
            chunk_index=index,
            start_char=start,
            end_char=start + len(text),
            metadata=metadata,
            chunk_id=chunk_id
        )

    def _split_text(self, text, separators):

        if not separators:
            return [text]

        sep = separators[0]

        if sep == "":
            return list(text)

        parts = text.split(sep)

        result = [
            p + sep if i < len(parts) - 1 else p
            for i, p in enumerate(parts)
        ]

        final = []

        for part in result:
            if (
                len(part) > self.chunk_size
                and len(separators) > 1
            ):
                final.extend(
                    self._split_text(
                        part,
                        separators[1:]
                    )
                )
            else:
                final.append(part)

        return final

    def _get_overlap(self, chunks):

        text = "".join(chunks)

        return (
            text
            if len(text) <= self.chunk_overlap
            else text[-self.chunk_overlap:]
        )