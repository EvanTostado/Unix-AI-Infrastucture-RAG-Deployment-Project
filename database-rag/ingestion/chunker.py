# src/processors/chunker.py
"""
Text chunking strategies for RAG document processing.
Different strategies optimize for different retrieval patterns.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Callable
import re


@dataclass
class Chunk:
    """
    Represents a single chunk of text with its metadata.
    """
    text: str                           # The chunk content
    chunk_index: int                    # Position in the document
    start_char: int                     # Character offset in original document
    end_char: int                       # End character offset
    metadata: dict                      # Inherited and chunk-specific metadata


class BaseChunker(ABC):
    """
    Abstract base class for text chunking strategies.
    """

    @abstractmethod
    def chunk(self, text: str, metadata: dict = None) -> List[Chunk]:
        """
        Split text into chunks.

        Args:
            text: The text to chunk
            metadata: Metadata to attach to each chunk

        Returns:
            List of Chunk objects
        """
        pass


class RecursiveCharacterChunker(BaseChunker):
    """
    Recursively splits text on natural boundaries.
    Tries larger separators first, then falls back to smaller ones.
    This is the most commonly used strategy for general documents.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: List[str] = None
    ):
        """
        Initialize the chunker.

        Args:
            chunk_size: Target size for each chunk in characters
            chunk_overlap: Number of characters to overlap between chunks
            separators: List of separators to try, in order of preference
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # Default separators from largest to smallest boundary
        self.separators = separators or [
            "\n\n",     # Paragraph breaks
            "\n",       # Line breaks
            ". ",       # Sentence endings
            "? ",       # Question marks
            "! ",       # Exclamation marks
            "; ",       # Semicolons
            ", ",       # Commas
            " ",        # Words
            ""          # Characters (last resort)
        ]

    def chunk(self, text: str, metadata: dict = None) -> List[Chunk]:
        """
        Split text into overlapping chunks using recursive character splitting.

        Args:
            text: The text to chunk
            metadata: Metadata to attach to each chunk

        Returns:
            List of Chunk objects
        """
        metadata = metadata or {}
        chunks = []

        # Recursively split the text
        splits = self._split_text(text, self.separators)

        # Merge splits into chunks of appropriate size
        current_chunk = []
        current_length = 0
        current_start = 0
        char_position = 0

        for split in splits:
            split_length = len(split)

            # If adding this split exceeds chunk size, finalize current chunk
            if current_length + split_length > self.chunk_size and current_chunk:
                chunk_text = "".join(current_chunk)
                chunks.append(Chunk(
                    text=chunk_text,
                    chunk_index=len(chunks),
                    start_char=current_start,
                    end_char=current_start + len(chunk_text),
                    metadata={**metadata, 'chunk_size': len(chunk_text)}
                ))

                # Calculate overlap
                overlap_text = self._get_overlap(current_chunk)
                current_chunk = [overlap_text] if overlap_text else []
                current_length = len(overlap_text) if overlap_text else 0
                current_start = char_position - current_length

            current_chunk.append(split)
            current_length += split_length
            char_position += split_length

        # Don't forget the last chunk
        if current_chunk:
            chunk_text = "".join(current_chunk)
            chunks.append(Chunk(
                text=chunk_text,
                chunk_index=len(chunks),
                start_char=current_start,
                end_char=current_start + len(chunk_text),
                metadata={**metadata, 'chunk_size': len(chunk_text)}
            ))

        return chunks

    def _split_text(self, text: str, separators: List[str]) -> List[str]:
        """
        Recursively split text using the given separators.

        Args:
            text: Text to split
            separators: List of separators to try

        Returns:
            List of text segments
        """
        if not separators:
            return [text]

        separator = separators[0]
        remaining_separators = separators[1:]

        if separator == "":
            # Character-level split as last resort
            return list(text)

        splits = text.split(separator)

        # Re-add the separator to maintain text integrity
        result = []
        for i, split in enumerate(splits):
            if i < len(splits) - 1:
                result.append(split + separator)
            else:
                result.append(split)

        # Recursively split any segments that are still too large
        final_splits = []
        for split in result:
            if len(split) > self.chunk_size and remaining_separators:
                final_splits.extend(self._split_text(split, remaining_separators))
            else:
                final_splits.append(split)

        return final_splits

    def _get_overlap(self, chunks: List[str]) -> str:
        """
        Get the overlap text from the end of current chunks.

        Args:
            chunks: List of text segments in current chunk

        Returns:
            Text to use as overlap for next chunk
        """
        full_text = "".join(chunks)
        if len(full_text) <= self.chunk_overlap:
            return full_text
        return full_text[-self.chunk_overlap:]


class SemanticChunker(BaseChunker):
    """
    Chunks text based on semantic similarity.
    Groups sentences that are semantically related together.
    Requires an embedding model.
    """

    def __init__(
        self,
        embedding_function: Callable[[str], List[float]],
        similarity_threshold: float = 0.5,
        min_chunk_size: int = 100,
        max_chunk_size: int = 2000
    ):
        """
        Initialize the semantic chunker.

        Args:
            embedding_function: Function that converts text to embeddings
            similarity_threshold: Minimum cosine similarity to group sentences
            min_chunk_size: Minimum chunk size in characters
            max_chunk_size: Maximum chunk size in characters
        """
        self.embed = embedding_function
        self.similarity_threshold = similarity_threshold
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size

    def chunk(self, text: str, metadata: dict = None) -> List[Chunk]:
        """
        Split text into semantically coherent chunks.

        Args:
            text: The text to chunk
            metadata: Metadata to attach to each chunk

        Returns:
            List of Chunk objects
        """
        import numpy as np

        metadata = metadata or {}

        # Split into sentences
        sentences = self._split_sentences(text)

        if not sentences:
            return []

        # Get embeddings for all sentences
        embeddings = [self.embed(s) for s in sentences]

        # Group sentences by semantic similarity
        chunks = []
        current_group = [0]  # Start with first sentence
        current_embedding = embeddings[0]
        current_start = 0

        for i in range(1, len(sentences)):
            # Calculate similarity with current group
            similarity = self._cosine_similarity(current_embedding, embeddings[i])

            current_text = " ".join([sentences[j] for j in current_group])

            # Check if we should add to current group or start new one
            if (similarity >= self.similarity_threshold and
                len(current_text) + len(sentences[i]) <= self.max_chunk_size):
                current_group.append(i)
                # Update group embedding (running average)
                current_embedding = np.mean(
                    [embeddings[j] for j in current_group],
                    axis=0
                ).tolist()
            else:
                # Finalize current chunk if it meets minimum size
                if len(current_text) >= self.min_chunk_size:
                    chunks.append(Chunk(
                        text=current_text,
                        chunk_index=len(chunks),
                        start_char=current_start,
                        end_char=current_start + len(current_text),
                        metadata={
                            **metadata,
                            'semantic_coherence': float(np.mean([
                                self._cosine_similarity(embeddings[current_group[0]], embeddings[j])
                                for j in current_group
                            ]))
                        }
                    ))

                # Start new group
                current_group = [i]
                current_embedding = embeddings[i]
                current_start = sum(len(sentences[j]) + 1 for j in range(i))

        # Handle last group
        if current_group:
            current_text = " ".join([sentences[j] for j in current_group])
            if len(current_text) >= self.min_chunk_size:
                chunks.append(Chunk(
                    text=current_text,
                    chunk_index=len(chunks),
                    start_char=current_start,
                    end_char=current_start + len(current_text),
                    metadata=metadata
                ))

        return chunks

    def _split_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences.

        Args:
            text: Text to split

        Returns:
            List of sentences
        """
        # Simple sentence splitting (for production, use spaCy or similar)
        sentence_pattern = r'(?<=[.!?])\s+'
        sentences = re.split(sentence_pattern, text)
        return [s.strip() for s in sentences if s.strip()]

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.

        Args:
            a: First vector
            b: Second vector

        Returns:
            Cosine similarity score
        """
        import numpy as np
        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


class StructureAwareChunker(BaseChunker):
    """
    Chunks based on document structure (headings, sections).
    Best for well-structured documents like documentation or reports.
    """

    def __init__(
        self,
        max_chunk_size: int = 2000,
        preserve_headings: bool = True
    ):
        """
        Initialize the structure-aware chunker.

        Args:
            max_chunk_size: Maximum characters per chunk
            preserve_headings: Whether to prepend section heading to each chunk
        """
        self.max_chunk_size = max_chunk_size
        self.preserve_headings = preserve_headings

    def chunk(self, text: str, metadata: dict = None) -> List[Chunk]:
        """
        Split text based on document structure.

        Args:
            text: The text to chunk (Markdown or plain text with headings)
            metadata: Metadata to attach to each chunk

        Returns:
            List of Chunk objects
        """
        metadata = metadata or {}
        chunks = []

        # Parse document structure
        sections = self._parse_sections(text)

        char_position = 0

        for section in sections:
            heading = section['heading']
            content = section['content']
            level = section['level']

            # If section is small enough, keep it as one chunk
            if len(content) <= self.max_chunk_size:
                chunk_text = content
                if self.preserve_headings and heading:
                    chunk_text = f"{'#' * level} {heading}\n\n{content}"

                chunks.append(Chunk(
                    text=chunk_text,
                    chunk_index=len(chunks),
                    start_char=char_position,
                    end_char=char_position + len(content),
                    metadata={
                        **metadata,
                        'section_heading': heading,
                        'section_level': level
                    }
                ))
            else:
                # Split large sections using recursive chunker
                sub_chunker = RecursiveCharacterChunker(
                    chunk_size=self.max_chunk_size,
                    chunk_overlap=100
                )
                sub_chunks = sub_chunker.chunk(content)

                for sub_chunk in sub_chunks:
                    chunk_text = sub_chunk.text
                    if self.preserve_headings and heading:
                        chunk_text = f"{'#' * level} {heading}\n\n{chunk_text}"

                    chunks.append(Chunk(
                        text=chunk_text,
                        chunk_index=len(chunks),
                        start_char=char_position + sub_chunk.start_char,
                        end_char=char_position + sub_chunk.end_char,
                        metadata={
                            **metadata,
                            'section_heading': heading,
                            'section_level': level,
                            'is_partial_section': True
                        }
                    ))

            char_position += len(section['raw'])

        return chunks

    def _parse_sections(self, text: str) -> List[dict]:
        """
        Parse document into sections based on headings.

        Args:
            text: Document text

        Returns:
            List of section dictionaries
        """
        sections = []

        # Match Markdown headings
        heading_pattern = r'^(#{1,6})\s+(.+)$'

        lines = text.split('\n')
        current_section = {
            'heading': '',
            'level': 0,
            'content': [],
            'raw': ''
        }

        for line in lines:
            match = re.match(heading_pattern, line)

            if match:
                # Save current section if it has content
                if current_section['content']:
                    current_section['content'] = '\n'.join(current_section['content']).strip()
                    current_section['raw'] = (
                        (f"{'#' * current_section['level']} {current_section['heading']}\n"
                         if current_section['heading'] else '') +
                        current_section['content']
                    )
                    sections.append(current_section)

                # Start new section
                current_section = {
                    'heading': match.group(2),
                    'level': len(match.group(1)),
                    'content': [],
                    'raw': ''
                }
            else:
                current_section['content'].append(line)

        # Don't forget last section
        if current_section['content']:
            current_section['content'] = '\n'.join(current_section['content']).strip()
            current_section['raw'] = (
                (f"{'#' * current_section['level']} {current_section['heading']}\n"
                 if current_section['heading'] else '') +
                current_section['content']
            )
            sections.append(current_section)

        return sections