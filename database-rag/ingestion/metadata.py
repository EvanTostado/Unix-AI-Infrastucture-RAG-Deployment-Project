"""
Metadata extraction and enrichment.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import hashlib
import re


@dataclass
class EnrichedMetadata:

    source_path: str
    file_name: str
    file_extension: str
    file_size_bytes: int

    content_hash: str

    created_at: Optional[datetime]
    modified_at: Optional[datetime]
    ingested_at: datetime

    title: str
    author: str
    language: str

    word_count: int
    char_count: int

    topics: List[str]
    entities: Dict[str, List[str]]

    custom: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:

        return {
            "source_path": self.source_path,
            "file_name": self.file_name,
            "file_extension": self.file_extension,
            "file_size_bytes": self.file_size_bytes,
            "content_hash": self.content_hash,
            "created_at": self.created_at.isoformat()
            if self.created_at else None,
            "modified_at": self.modified_at.isoformat()
            if self.modified_at else None,
            "ingested_at": self.ingested_at.isoformat(),
            "title": self.title,
            "author": self.author,
            "language": self.language,
            "word_count": self.word_count,
            "char_count": self.char_count,
            "topics": self.topics,
            "entities": self.entities,
            "custom": self.custom
        }


class MetadataExtractor:

    def __init__(
        self,
        detect_language: bool = True,
        extract_entities: bool = False
    ):

        self.detect_language = detect_language
        self.extract_entities = extract_entities

        self.lang_detect = None
        self.nlp = None

        if detect_language:
            try:
                from langdetect import detect
                self.lang_detect = detect
            except ImportError:
                self.detect_language = False

        if extract_entities:
            try:
                import spacy
                self.nlp = spacy.load(
                    "en_core_web_sm"
                )
            except Exception:
                self.extract_entities = False

    def extract(
        self,
        content: str,
        file_path: Path,
        document_metadata: Dict[str, Any] | None = None
    ) -> EnrichedMetadata:

        document_metadata = document_metadata or {}

        stat = file_path.stat()

        content_hash = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()

        language = self._detect_language(
            content
        )

        entities = (
            self._extract_entities(content)
            if self.extract_entities
            else {}
        )

        topics = self._extract_topics(
            content
        )

        return EnrichedMetadata(
            source_path=str(file_path),
            file_name=file_path.name,
            file_extension=file_path.suffix.lower(),
            file_size_bytes=stat.st_size,
            content_hash=content_hash,
            created_at=datetime.fromtimestamp(
                stat.st_ctime
            ),
            modified_at=datetime.fromtimestamp(
                stat.st_mtime
            ),
            ingested_at=datetime.now(),
            title=document_metadata.get(
                "title",
                file_path.stem
            ),
            author=document_metadata.get(
                "author",
                ""
            ),
            language=language,
            word_count=len(content.split()),
            char_count=len(content),
            topics=topics,
            entities=entities,
            custom=document_metadata
        )

    def _detect_language(
        self,
        content: str
    ) -> str:

        if (
            not self.detect_language
            or not self.lang_detect
            or not content.strip()
        ):
            return "unknown"

        try:
            return self.lang_detect(
                content[:1000]
            )
        except Exception:
            return "unknown"

    def _extract_entities(
        self,
        content: str
    ) -> Dict[str, List[str]]:

        if not self.nlp:
            return {}

        doc = self.nlp(
            content[:100000]
        )

        entities: Dict[
            str,
            List[str]
        ] = {}

        for ent in doc.ents:

            entities.setdefault(
                ent.label_,
                []
            )

            if ent.text not in entities[ent.label_]:
                entities[ent.label_].append(
                    ent.text
                )

        return entities

    def _extract_topics(
        self,
        content: str,
        max_topics: int = 10
    ) -> List[str]:

        words = re.findall(
            r"\b[a-zA-Z]{4,}\b",
            content.lower()
        )

        stopwords = {
            "that", "this", "with",
            "from", "have", "been",
            "were", "they", "their",
            "which", "would", "could",
            "should", "about", "there",
            "when", "what", "where",
            "will", "more", "some",
            "into", "only",
            "other", "also", "just",
            "your", "like", "make",
            "made"
        }

        frequencies: Dict[str, int] = {}

        for word in words:

            if word in stopwords:
                continue

            frequencies[word] = (
                frequencies.get(word, 0) + 1
            )

        return [
            word
            for word, _
            in sorted(
                frequencies.items(),
                key=lambda x: x[1],
                reverse=True
            )[:max_topics]
        ]