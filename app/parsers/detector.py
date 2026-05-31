"""Automatic bank statement detection.

Add a new bank by creating a parser class and registering it in ``registry.py``.
The user should not need to create bank-specific folders; all PDFs go into
``data/raw`` and this detector chooses the correct parser.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber


def extract_preview_text(pdf_path: Path, max_pages: int = 2) -> str:
    """Extract a small text preview to identify the statement format."""
    chunks: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:max_pages]:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)
