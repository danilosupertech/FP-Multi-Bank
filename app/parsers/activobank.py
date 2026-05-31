"""ActivoBank PDF statement parser."""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from app.categorization.classifier import categorize_transaction_with_details
from app.categorization.merchant import extract_merchant
from app.parsers.base import BankStatementParser
from app.parsers.utils import compact_line, normalize_activo_date, normalize_money

STATEMENT_PERIOD_PATTERN = re.compile(
    r"EXTRATO DE (\d{4})/\d{2}/\d{2} A \d{4}/\d{2}/\d{2}"
)
INITIAL_BALANCE_PATTERN = re.compile(r"SALDO INICIAL\s+([\d\s]+[.,]\d{2})")
MONEY_PATTERN = r"(?:\d{1,3}(?:\s\d{3})+|\d+)[.,]\d{2}"
TRANSACTION_PATTERN = re.compile(
    r"(\d{1,2}\.\d{2})\s+"
    r"(\d{1,2}\.\d{2})\s+"
    r"(.+?)\s+"
    rf"({MONEY_PATTERN})\s+"
    rf"({MONEY_PATTERN})$"
)


class ActivoBankParser(BankStatementParser):
    """Parse ActivoBank PDF statements."""

    bank_name = "ActivoBank"

    def can_parse_text(self, text: str) -> bool:
        return "ACTIVOBANK" in text.upper() or "BANCO ACTIVOBANK" in text.upper()

    def extract_statement_year(self, text: str) -> str | None:
        match = STATEMENT_PERIOD_PATTERN.search(text)
        return match.group(1) if match else None

    def extract_initial_balance(self, text: str) -> float | None:
        match = INITIAL_BALANCE_PATTERN.search(text)
        return normalize_money(match.group(1)) if match else None

    def identify_operation(self, previous_balance: float | None, current_balance: float) -> str:
        if previous_balance is None:
            return "debit"
        if current_balance > previous_balance:
            return "credit"
        return "debit"

    def identify_transaction_type(self, description: str, operation: str) -> str:
        desc = description.lower()
        if operation == "credit":
            return "credit"
        if desc.startswith("compra"):
            return "purchase"
        if desc.startswith("dd "):
            return "direct_debit"
        if desc.startswith("trf"):
            return "transfer"
        if desc.startswith(("pag serv", "pagserv", "pag ")):
            return "service_payment"
        return "other"

    def extract_transactions(self, pdf_path: Path) -> list[dict]:
        transactions: list[dict] = []
        statement_year: str | None = None
        previous_balance: float | None = None

        with pdfplumber.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""

                if not text:
                    continue

                if statement_year is None:
                    statement_year = self.extract_statement_year(text)

                if previous_balance is None:
                    initial_balance = self.extract_initial_balance(text)
                    if initial_balance is not None:
                        previous_balance = initial_balance

                for raw_line in text.splitlines():
                    line = compact_line(raw_line)
                    match = TRANSACTION_PATTERN.search(line)

                    if not match:
                        continue

                    if statement_year is None:
                        raise ValueError("Ano do extrato ActivoBank não encontrado.")

                    launch_date = normalize_activo_date(match.group(1), statement_year)
                    value_date = normalize_activo_date(match.group(2), statement_year)
                    description = compact_line(match.group(3))
                    amount = normalize_money(match.group(4))
                    current_balance = normalize_money(match.group(5))

                    operation = self.identify_operation(previous_balance, current_balance)
                    debit = amount if operation == "debit" else 0.0
                    credit = amount if operation == "credit" else 0.0
                    merchant = extract_merchant(description)
                    transaction_type = self.identify_transaction_type(description, operation)

                    category_info = categorize_transaction_with_details(
                        description,
                        merchant,
                        operation,
                        amount=amount,
                    )

                    transactions.append(
                        {
                            "bank": self.bank_name,
                            "account_currency": "EUR",
                            "external_id": "",
                            "launch_date": launch_date,
                            "value_date": value_date,
                            "description": description,
                            "merchant": merchant,
                            "amount": amount,
                            "debit": debit,
                            "credit": credit,
                            "balance": current_balance,
                            "category": category_info["category"],
                            "operation": operation,
                            "transaction_type": transaction_type,
                            "source_file": pdf_path.name,
                            "source_page": page_index,
                            "raw_line": line,
                            "parse_method": "regex",
                            "parse_status": "ok",
                            "parse_note": "",
                            **category_info,
                        }
                    )

                    previous_balance = current_balance

        return transactions
