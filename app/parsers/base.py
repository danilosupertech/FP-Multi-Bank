"""Base parser contracts for bank statement importers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BankStatementParser(ABC):
    """Base interface for bank-specific PDF parsers."""

    bank_name: str

    @abstractmethod
    def can_parse_text(self, text: str) -> bool:
        """Return True when this parser can handle the PDF text."""

    @abstractmethod
    def extract_transactions(self, pdf_path: Path) -> list[dict]:
        """Extract transactions from a PDF statement."""
