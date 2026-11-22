"""
PDF document extractor with support for both text-based and scanned PDFs.
Uses PyMuPDF for text extraction and optional OCR for images.
"""

import os
import re

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any

import fitz
from PyPDF2 import PdfMerger


@dataclass
class ExtractedDocument:
    content: str
    metadata: Dict[str, Any]
    source_path: str
    file_type: str
    extraction_method: str
    page_count: int
    word_count: int


class BaseExtractor(ABC):

    @abstractmethod
    def can_handle(self, mimetype: str) -> bool:
        pass

    @abstractmethod
    def extract(self, file_path):
        pass


class PDFExtractor(BaseExtractor):
    """
    PDF extraction using PyMuPDF.
    """

    supported_mimetypes = [
        "application/pdf"
    ]

    def __init__(
        self,
        enable_ocr: bool = False,
        ocr_language: str = "eng"
    ):

        self.enable_ocr = enable_ocr
        self.ocr_language = ocr_language

        if enable_ocr:
            try:
                import pytesseract
                from PIL import Image

                self.pytesseract = pytesseract
                self.Image = Image

            except ImportError:
                raise ImportError(
                    "OCR requires pytesseract and Pillow"
                )

    def merge_pdfs(
        self,
        pdf1_path: str,
        pdf2_path: str,
        output_path: str
    ):

        if not os.path.isfile(pdf1_path):
            raise FileNotFoundError(pdf1_path)

        if not os.path.isfile(pdf2_path):
            raise FileNotFoundError(pdf2_path)

        merger = PdfMerger()

        merger.append(pdf1_path)
        merger.append(pdf2_path)

        merger.write(output_path)
        merger.close()

        return output_path

    def can_handle(
        self,
        mimetype: str
    ) -> bool:

        return (
            mimetype in self.supported_mimetypes
        )

    def extract(
        self,
        file_path
    ) -> ExtractedDocument:

        doc = fitz.open(file_path)

        metadata = self._extract_metadata(
            doc
        )

        pages_text = []

        for page_num, page in enumerate(doc):

            page_text = self._extract_page_text(
                page,
                page_num
            )

            if page_text.strip():
                pages_text.append(
                    page_text
                )

        content = "\n\n".join(
            pages_text
        )

        word_count = len(
            content.split()
        )

        doc.close()

        return ExtractedDocument(
            content=content,
            metadata=metadata,
            source_path=str(file_path),
            file_type="application/pdf",
            extraction_method=(
                "pymupdf"
                + (
                    "+ocr"
                    if self.enable_ocr
                    else ""
                )
            ),
            page_count=len(pages_text),
            word_count=word_count
        )

    def _extract_metadata(
        self,
        doc: fitz.Document
    ) -> Dict[str, Any]:

        pdf_metadata = doc.metadata

        return {
            "title":
                pdf_metadata.get(
                    "title",
                    ""
                ),
            "author":
                pdf_metadata.get(
                    "author",
                    ""
                ),
            "subject":
                pdf_metadata.get(
                    "subject",
                    ""
                ),
            "keywords":
                pdf_metadata.get(
                    "keywords",
                    ""
                ),
            "creator":
                pdf_metadata.get(
                    "creator",
                    ""
                ),
            "producer":
                pdf_metadata.get(
                    "producer",
                    ""
                ),
            "creation_date":
                pdf_metadata.get(
                    "creationDate",
                    ""
                ),
            "modification_date":
                pdf_metadata.get(
                    "modDate",
                    ""
                ),
            "page_count":
                doc.page_count
        }

    def _extract_page_text(
        self,
        page: fitz.Page,
        page_num: int
    ) -> str:

        text = page.get_text(
            "text"
        )

        if (
            self.enable_ocr
            and self._is_likely_scanned(
                page,
                text
            )
        ):
            text = self._ocr_page(
                page
            )

        text = self._clean_text(
            text
        )

        return text

    def _is_likely_scanned(
        self,
        page: fitz.Page,
        text: str
    ) -> bool:

        page_area = (
            page.rect.width
            * page.rect.height
        )

        text_length = len(
            text.strip()
        )

        if (
            text_length < 50
            and page_area > 100000
        ):
            return (
                len(
                    page.get_images()
                ) > 0
            )

        return False

    def _ocr_page(
        self,
        page: fitz.Page
    ) -> str:

        mat = fitz.Matrix(
            300 / 72,
            300 / 72
        )

        pix = page.get_pixmap(
            matrix=mat
        )

        image = self.Image.frombytes(
            "RGB",
            [pix.width, pix.height],
            pix.samples
        )

        return self.pytesseract.image_to_string(
            image,
            lang=self.ocr_language
        )

    def _clean_text(
        self,
        text: str
    ) -> str:

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        text = re.sub(
            r"\x0c",
            "\n\n",
            text
        )

        text = re.sub(
            r"(\w)-\s+(\w)",
            r"\1\2",
            text
        )

        return text.strip()