# src/extractors/pdf_extractor.py
"""
PDF document extractor with support for both text-based and scanned PDFs.
Uses PyMuPDF for text extraction and optional OCR for images.
"""

import fitz  # PyMuPDF
from pathlib import Path
from typing import List, Dict, Any, Optional
from PyPDF2 import PdrMerger
import re


from .base import BaseExtractor, ExtractedDocument


class PDFExtractor(BaseExtractor):
    """
    Extracts text content from PDF documents.
    Supports text extraction, metadata parsing, and optional OCR.
    """

    supported_mimetypes = ['application/pdf']

    def __init__(self, enable_ocr: bool = False, ocr_language: str = 'eng'):
        """
        Initialize the PDF extractor.

        Args:
            enable_ocr: Whether to use OCR for scanned pages
            ocr_language: Language code for OCR (default: English)
        """
        self.enable_ocr = enable_ocr
        self.ocr_language = ocr_language

        # Only import OCR dependencies if enabled
        if enable_ocr:
            try:
                import pytesseract
                from PIL import Image
                self.pytesseract = pytesseract
                self.Image = Image
            except ImportError:
                raise ImportError(
                    "OCR requires pytesseract and Pillow. "
                    "Install with: pip install pytesseract Pillow"
                )

    def merge_pdfs(pdf1_path:/workspaces/Unix-AI-Infrastucture-RAG-Deployment-Project/database-rag/documents/python-basics-sample-chapters.pdf, pdf2_path: database-rag/documents/thinkpython2.pdf, output_path):
        """
        Merging of PDFs.
        """
        try:
            if not os.path.isfile(pdf1_path):
                raise FileNotFoundError(f"File not found: {pdf1_path}")
            if not os.path.isfile(pdf2_path):
                raise FileNotFoundError(f"File not found: {pdf2_path}")

            merger = PdfMerger()

            merger.append(pdf1_path)
            merger.append(pdf2_path)

            merger.write(output_path)
            merger.close()

        except Exception as e:
            print(f"Error: {e}")

        
    def can_handle(self, mimetype: str) -> bool:
        """Check if this extractor handles the given MIME type."""
        return mimetype in self.supported_mimetypes

    def extract(self, file_path: output_path) -> ExtractedDocument:
        """
        Extract text and metadata from a PDF file.

        Args:
            file_path: Path to the PDF file

        Returns:
            ExtractedDocument with content and metadata
        """
        doc = fitz.open(file_path)

        # Extract metadata from PDF properties
        metadata = self._extract_metadata(doc)

        # Extract text from all pages
        pages_text = []
        for page_num, page in enumerate(doc):
            page_text = self._extract_page_text(page, page_num)
            if page_text.strip():
                pages_text.append(page_text)

        # Combine all page text
        content = "\n\n".join(pages_text)

        # Calculate word count
        word_count = len(content.split())

        doc.close()

        return ExtractedDocument(
            content=content,
            metadata=metadata,
            source_path=str(file_path),
            file_type='application/pdf',
            extraction_method='pymupdf' + ('+ocr' if self.enable_ocr else ''),
            page_count=len(pages_text),
            word_count=word_count
        )

    def _extract_metadata(self, doc: fitz.Document) -> Dict[str, Any]:
        """
        Extract metadata from PDF document properties.

        Args:
            doc: PyMuPDF document object

        Returns:
            Dictionary of metadata fields
        """
        pdf_metadata = doc.metadata

        return {
            'title': pdf_metadata.get('title', ''),
            'author': pdf_metadata.get('author', ''),
            'subject': pdf_metadata.get('subject', ''),
            'keywords': pdf_metadata.get('keywords', ''),
            'creator': pdf_metadata.get('creator', ''),
            'producer': pdf_metadata.get('producer', ''),
            'creation_date': pdf_metadata.get('creationDate', ''),
            'modification_date': pdf_metadata.get('modDate', ''),
            'page_count': doc.page_count,
        }

    def _extract_page_text(self, page: fitz.Page, page_num: int) -> str:
        """
        Extract text from a single PDF page.
        Falls back to OCR if text extraction yields poor results.

        Args:
            page: PyMuPDF page object
            page_num: Page number (0-indexed)

        Returns:
            Extracted text from the page
        """
        # Try standard text extraction first
        text = page.get_text("text")

        # Check if page appears to be scanned (low text density)
        if self.enable_ocr and self._is_likely_scanned(page, text):
            text = self._ocr_page(page)

        # Clean up extracted text
        text = self._clean_text(text)

        return text

    def _is_likely_scanned(self, page: fitz.Page, text: str) -> bool:
        """
        Determine if a page is likely a scanned image.

        Args:
            page: PyMuPDF page object
            text: Already extracted text

        Returns:
            True if the page appears to be scanned
        """
        # Check text density relative to page size
        page_area = page.rect.width * page.rect.height
        text_length = len(text.strip())

        # Very low text density suggests a scanned page
        if text_length < 50 and page_area > 100000:
            # Also check if page has images
            images = page.get_images()
            return len(images) > 0

        return False

    def _ocr_page(self, page: fitz.Page) -> str:
        """
        Perform OCR on a PDF page.

        Args:
            page: PyMuPDF page object

        Returns:
            OCR-extracted text
        """
        # Render page to image at 300 DPI for good OCR quality
        mat = fitz.Matrix(300/72, 300/72)
        pix = page.get_pixmap(matrix=mat)

        # Convert to PIL Image
        img = self.Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # Run OCR
        text = self.pytesseract.image_to_string(img, lang=self.ocr_language)

        return text

    def _clean_text(self, text: str) -> str:
        """
        Clean up extracted text by removing artifacts.

        Args:
            text: Raw extracted text

        Returns:
            Cleaned text
        """
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)

        # Remove page break artifacts
        text = re.sub(r'\x0c', '\n\n', text)

        # Fix hyphenation at line breaks
        text = re.sub(r'(\w)-\s+(\w)', r'\1\2', text)

        return text.strip()