import os
import re
import fitz

from dataclasses import dataclass
from typing import Dict, Any

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


class PDFExtractor:

    def __init__(self, enable_ocr=False, ocr_language="eng"):
        self.enable_ocr = enable_ocr
        self.ocr_language = ocr_language

        if enable_ocr:
            import pytesseract
            from PIL import Image

            self.pytesseract = pytesseract
            self.Image = Image

    def merge_pdfs(self, pdf1, pdf2, output):
        merger = PdfMerger()
        merger.append(pdf1)
        merger.append(pdf2)
        merger.write(output)
        merger.close()
        return output

    def extract(self, file_path):

        doc = fitz.open(file_path)

        pages = []

        for page in doc:
            text = page.get_text("text")

            if self.enable_ocr and self._is_scanned(page, text):
                text = self._ocr_page(page)

            pages.append(self._clean_text(text))

        content = "\n\n".join(filter(None, pages))

        result = ExtractedDocument(
            content=content,
            metadata={
                "title": doc.metadata.get("title", ""),
                "author": doc.metadata.get("author", ""),
                "page_count": doc.page_count
            },
            source_path=str(file_path),
            file_type="application/pdf",
            extraction_method=(
                "pymupdf+ocr"
                if self.enable_ocr
                else "pymupdf"
            ),
            page_count=doc.page_count,
            word_count=len(content.split())
        )

        doc.close()
        return result

    def _is_scanned(self, page, text):
        return (
            len(text.strip()) < 50
            and len(page.get_images()) > 0
        )

    def _ocr_page(self, page):

        pix = page.get_pixmap(
            matrix=fitz.Matrix(300 / 72, 300 / 72)
        )

        image = self.Image.frombytes(
            "RGB",
            (pix.width, pix.height),
            pix.samples
        )

        return self.pytesseract.image_to_string(
            image,
            lang=self.ocr_language
        )

    def _clean_text(self, text):

        text = re.sub(r"\s+", " ", text)

        text = re.sub(
            r"(\w)-\s+(\w)",
            r"\1\2",
            text
        )

        text = text.replace("\x0c", "\n\n")

        return text.strip()