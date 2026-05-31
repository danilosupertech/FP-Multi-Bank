"""Parser factory for multiple bank statement formats."""

from __future__ import annotations

from pathlib import Path

from app.parsers.base import BankStatementParser
from app.parsers.detector import extract_preview_text
from app.parsers.registry import registered_parsers


def get_parser_for_pdf(pdf_path: Path) -> BankStatementParser:
    """Return the first registered parser that recognizes the PDF."""
    preview = extract_preview_text(pdf_path)

    for parser in registered_parsers():
        if parser.can_parse_text(preview):
            return parser

    available = ", ".join(parser.bank_name for parser in registered_parsers())
    raise ValueError(
        "Formato de extrato não reconhecido. "
        f"Parsers disponíveis: {available}. "
        "Para adicionar outro banco, crie um parser em app/parsers/ "
        "e registre em app/parsers/registry.py."
    )


def extract_transactions(pdf_path: Path) -> list[dict]:
    """Extract transactions using the matching bank parser."""
    parser = get_parser_for_pdf(pdf_path)
    return parser.extract_transactions(pdf_path)
