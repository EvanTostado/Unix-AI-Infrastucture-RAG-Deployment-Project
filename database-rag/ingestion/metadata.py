# src/processors/metadata.py
"""
Metadata extraction and enrichment for documents.
Extracts both explicit metadata and inferred properties.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime
import hashlib
import re
from pathlib import Path


@dataclass
class EnrichedMetadata:
    """
    Contains all metadata for a document.
    """
    # Source information
    source_path: str
    file_name: str
    file_extension: str
    file_size_bytes: int

    # Content hashes for deduplication
    content_hash: str

    # Timestamps
    created_at: Optional[datetime]
    modified_at: Optional[datetime]
    ingested_at: datetime

    # Document properties
    title: str
    author: str
    language: str
    word_count: int
    char_count: int

    # Extracted entities
    topics: List[str]
    entities: Dict[str, List[str]]

    # Custom metadata
    custom: Dict[str, Any]


class MetadataExtractor:
    """
    Extracts and enriches document metadata.
    Combines file-level, content-level, and inferred metadata.
    """

    def __init__(self, detect_language: bool = True, extract_entities: bool = False):
        """
        Initialize the metadata extractor.

        Args:
            detect_language: Whether to detect document language
            extract_entities: Whether to extract named entities (requires spaCy)
        """
        self.detect_language = detect_language
        self.extract_entities = extract_entities

        if detect_language:
            try:
                from langdetect import detect
                self.lang_detect = detect
            except ImportError:
                self.detect_language = False

        if extract_entities:
            try:
                import spacy
                self.nlp = spacy.load("en_core_web_sm")
            except (ImportError, OSError):
                self.extract_entities = False

    def extract(
        self,
        content: str,
        file_path: Path,
        document_metadata: Dict[str, Any] = None
    ) -> EnrichedMetadata:
        """
        Extract comprehensive metadata from a document.

        Args:
            content: The document text content
            file_path: Path to the source file
            document_metadata: Metadata extracted during content extraction

        Returns:
            EnrichedMetadata object
        """
        document_metadata = document_metadata or {}

        # File-level metadata
        stat = file_path.stat()

        # Content hash for deduplication
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Language detection
        language = "unknown"
        if self.detect_language and content:
            try:
                language = self.lang_detect(content[:1000])
            except Exception:
                pass

        # Entity extraction
        entities = {}
        if self.extract_entities and content:
            entities = self._extract_entities(content)

        # Topic extraction (simple keyword-based)
        topics = self._extract_topics(content)

        return EnrichedMetadata(
            source_path=str(file_path),
            file_name=file_path.name,
            file_extension=file_path.suffix.lower(),
            file_size_bytes=stat.st_size,
            content_hash=content_hash,
            created_at=datetime.fromtimestamp(stat.st_ctime),
            modified_at=datetime.fromtimestamp(stat.st_mtime),
            ingested_at=datetime.now(),
            title=document_metadata.get('title', file_path.stem),
            author=document_metadata.get('author', ''),
            language=language,
            word_count=len(content.split()),
            char_count=len(content),
            topics=topics,
            entities=entities,
            custom=document_metadata
        )

    def _extract_entities(self, content: str) -> Dict[str, List[str]]:
        """
        Extract named entities using spaCy.

        Args:
            content: Document text

        Returns:
            Dictionary mapping entity types to lists of entities
        """
        # Process only first 100K chars to avoid memory issues
        doc = self.nlp(content[:100000])

        entities = {}
        for ent in doc.ents:
            if ent.label_ not in entities:
                entities[ent.label_] = []
            if ent.text not in entities[ent.label_]:
                entities[ent.label_].append(ent.text)

        return entities

    def _extract_topics(self, content: str, max_topics: int = 10) -> List[str]:
        """
        Extract topic keywords from content.
        Uses simple TF-based extraction.

        Args:
            content: Document text
            max_topics: Maximum number of topics to return

        Returns:
            List of topic keywords
        """
        # Simple word frequency analysis
        words = re.findall(r'\b[a-zA-Z]{4,}\b', content.lower())

        # Filter common words
        stopwords = {
            'that', 'this', 'with', 'from', 'have', 'been', 'were', 'they',
            'their', 'which', 'would', 'could', 'should', 'about', 'there',
            'when', 'what', 'where', 'will', 'more', 'some', 'than', 'into',
            'only', 'other', 'also', 'just', 'your', 'like', 'make', 'made'
        }

        filtered_words = [w for w in words if w not in stopwords]

        # Count frequencies
        freq = {}
        for word in filtered_words:
            freq[word] = freq.get(word, 0) + 1

        # Sort by frequency and return top topics
        sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)

        return [word for word, count in sorted_words[:max_topics]]