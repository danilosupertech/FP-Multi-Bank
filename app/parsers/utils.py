"""Shared parser helper functions."""

from __future__ import annotations

import re
from datetime import datetime

MONTHS_PT = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


def normalize_money(value: str) -> float:
    """Convert common PT/EU bank money formats to float."""
    value = str(value).strip()
    if not value:
        return 0.0
    value = value.replace("EUR", "").replace("€", "").strip()
    value = value.replace(" ", "")
    # If both separators exist, assume comma is decimal and dot is thousands.
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    else:
        value = value.replace(",", ".")
    return float(value)


def normalize_activo_date(date_value: str, year: str) -> str:
    """Return DD.MM.YYYY from ActivoBank M.DD/DD.MM-like values.

    ActivoBank statement lines in this project are in month.day format
    (e.g. 3.04 = 04/03), so the stored text is normalized as DD.MM.YYYY.
    """
    month, day = date_value.split(".")
    return f"{int(day):02d}.{int(month):02d}.{year}"


def normalize_date_display(date_text: str) -> str:
    """Display DD.MM.YYYY as DD/MM/YYYY, preserving invalid values."""
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").strftime("%d/%m/%Y")
    except ValueError:
        return date_text


def parse_wise_pt_date(text: str) -> str | None:
    """Parse Wise date like '31 de dezembro de 2025' into DD.MM.YYYY."""
    match = re.search(
        r"(\d{1,2})\s+de\s+([A-Za-zçÇãÃéÉ]+)\s+de\s+(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2).lower().replace("ç", "c")
    year = int(match.group(3))
    month = MONTHS_PT.get(month_name)
    if month is None:
        return None
    return f"{day:02d}.{month:02d}.{year}"


def compact_line(text: str) -> str:
    """Normalize whitespace in a raw PDF line/chunk."""
    return re.sub(r"\s+", " ", text).strip()
