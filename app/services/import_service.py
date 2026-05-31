"""Service that imports supported PDF statements into SQLite.

The importer is intentionally bank-folder agnostic: every PDF is placed in
``data/raw``. The parser factory detects whether it is ActivoBank, Wise or a
future supported format.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import FAILED_DIR, PROCESSED_DIR, RAW_DIR, ensure_data_directories
from app.database.db import init_db, save_transactions
from app.parsers.factory import get_parser_for_pdf


def unique_destination(directory: Path, filename: str) -> Path:
    """Return a non-existing destination path inside a directory."""
    destination = directory / filename
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    counter = 1

    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def move_pdf(pdf_file: Path, directory: Path) -> Path:
    """Move a PDF to a target folder without overwriting."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(directory, pdf_file.name)
    shutil.move(str(pdf_file), str(destination))
    return destination


def list_raw_pdfs() -> list[Path]:
    """Return PDFs directly inside data/raw.

    Keep ``data/raw`` as the single inbox. Do not create one folder per bank.
    """
    return sorted(path for path in RAW_DIR.glob("*.pdf") if path.is_file())


def import_pdfs() -> None:
    """Import all supported PDF statements from data/raw."""
    init_db()
    ensure_data_directories()

    pdfs = list_raw_pdfs()
    if not pdfs:
        print("Nenhum PDF encontrado em data/raw.")
        print("Coloque os PDFs do ActivoBank, Wise ou outros bancos suportados em:")
        print("  data/raw/")
        return

    for pdf_file in pdfs:
        print(f"\nProcessando: {pdf_file.name}")

        try:
            parser = get_parser_for_pdf(pdf_file)
            print(f"Tipo detectado automaticamente: {parser.bank_name}")

            transactions = parser.extract_transactions(pdf_file)
            inserted_count = save_transactions(transactions)
            duplicate_count = max(len(transactions) - inserted_count, 0)

            suspicious_count = sum(
                1 for tx in transactions
                if str(tx.get("parse_status", "ok")).lower() != "ok"
            )
            unclassified_count = sum(
                1 for tx in transactions if tx.get("category") == "Outros"
            )
            suggested_count = sum(
                1 for tx in transactions if tx.get("suggested_category")
            )

            print(f"{inserted_count} transações importadas")
            print(f"{duplicate_count} transações ignoradas por duplicidade")
            print(f"{suspicious_count} transações suspeitas de importação")
            print(f"{unclassified_count} transações em categoria Outros")
            print(f"{suggested_count} sugestões de categoria geradas")

            destination = move_pdf(pdf_file, PROCESSED_DIR)
            print(f"Movido para processed: {destination.name}")

        except Exception as error:
            destination = move_pdf(pdf_file, FAILED_DIR)
            print(f"Erro: {error}")
            print(f"Movido para failed: {destination.name}")
