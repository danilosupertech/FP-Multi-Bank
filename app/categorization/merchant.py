"""Merchant extraction utilities.

The goal is not to create a perfect merchant identifier, but to produce a
stable text that can be used by learned rules and category suggestions.
"""

from __future__ import annotations

import re

REMOVE_WORDS = [
    "CONTACTLESS",
    "PORTO",
    "LISBOA",
    "PORT",
    "POR",
    "MATOSINHOS",
    "GONDOMAR",
    "RIO TINTO",
    "BRAGA",
    "DUBLIN",
    "PARIS",
    "MADRID",
    "LUXEMBOURG",
]

PREFIX_PATTERN = re.compile(
    r"^(COMPRA|PAGSERV|PAG SERV|PAG BXVAL-|PAG|DD|TRF P/|TRF MB WAY P/|MBW|CRED\.)\s+",
    re.IGNORECASE,
)

POSTAL_CODE_PATTERN = re.compile(r"\b\d{4}-\d{3}\b")
CARD_CODE_PATTERN = re.compile(r"\b\d{4}\b")
REFERENCE_PATTERN = re.compile(r"\b\d{5,}/\d+\b")
LONG_NUMBER_PATTERN = re.compile(r"\b\d{6,}\b")


def extract_merchant(description: str) -> str:
    """Extract a simplified merchant name from a bank description."""
    merchant = description.upper().strip()
    merchant = PREFIX_PATTERN.sub("", merchant)

    merchant = POSTAL_CODE_PATTERN.sub("", merchant)
    merchant = REFERENCE_PATTERN.sub("", merchant)
    merchant = LONG_NUMBER_PATTERN.sub("", merchant)
    merchant = CARD_CODE_PATTERN.sub("", merchant)

    for word in REMOVE_WORDS:
        merchant = re.sub(rf"\b{re.escape(word)}\b", "", merchant)

    merchant = re.sub(r"\s*-\s*\d+\s*$", "", merchant)
    merchant = re.sub(r"[^A-ZÀ-Ú0-9.&/ -]+", " ", merchant)
    merchant = re.sub(r"\s+", " ", merchant).strip(" -")

    # Keep enough information for matching, but avoid very noisy long strings.
    words = merchant.split()
    if len(words) > 7:
        merchant = " ".join(words[:7])

    return merchant
