"""Backward-compatible wrapper.

New code should import from app.parsers.factory or bank-specific parsers.
"""

from pathlib import Path

from app.parsers.factory import extract_transactions
from app.parsers.utils import normalize_money

__all__ = ["extract_transactions", "normalize_money"]
