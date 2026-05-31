"""Parser registry.

This is the only place that needs to change when a new bank parser is added.
"""

from __future__ import annotations

from app.parsers.activobank import ActivoBankParser
from app.parsers.base import BankStatementParser
from app.parsers.wise import WiseParser

PARSERS: list[BankStatementParser] = [
    ActivoBankParser(),
    WiseParser(),
]


def registered_parsers() -> list[BankStatementParser]:
    """Return available parser instances in detection order."""
    return PARSERS
