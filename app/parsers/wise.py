"""Wise PDF statement parser.

Wise statements have a different layout from ActivoBank. In pdfplumber output,
the signed amount and balance often appear at the end of the description line,
while the date and transaction id appear on the following line. Some merchant
names wrap to an intermediate continuation line.

Example:
Transação por cartão de 0,40 EUR emitida por A Super 2000 SAO COSME -0,40 318,96
BRA
27 de maio de 2026 Cartão terminado em 1876 ... Transação: CARD-3845732683
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from app.categorization.classifier import categorize_transaction_with_details
from app.categorization.merchant import extract_merchant
from app.parsers.base import BankStatementParser
from app.parsers.utils import compact_line, normalize_money, parse_wise_pt_date

AMOUNT_AT_END_PATTERN = re.compile(
    r"^(?P<description>.+?)\s+"
    r"(?P<amount>[+-]?\d{1,3}(?:[.\s]\d{3})*,\d{2}|[+-]?\d+,\d{2})\s+"
    r"(?P<balance>\d{1,3}(?:[.\s]\d{3})*,\d{2}|\d+,\d{2})$"
)

TRANSACTION_ID_PATTERN = re.compile(r"Transação:\s*([A-Z]+-\d+)", re.IGNORECASE)
DATE_ID_PATTERN = re.compile(
    r"\d{1,2}\s+de\s+[A-Za-zçÇãÃéÉ]+\s+de\s+\d{4}.*Transação:\s*[A-Z]+-\d+",
    re.IGNORECASE,
)
CARD_MERCHANT_PATTERN = re.compile(
    r"Transação por cartão de\s+[-+]?\d+[.,]\d{2}\s+EUR\s+emitida por\s+(.+)",
    re.IGNORECASE,
)
RECEIVED_PATTERN = re.compile(
    r"Recebeu dinheiro de\s+(.+?)\s+com a referência",
    re.IGNORECASE,
)


class WiseParser(BankStatementParser):
    """Parse Wise PDF statements."""

    bank_name = "Wise"

    def can_parse_text(self, text: str) -> bool:
        upper = text.upper()
        return "WISE PAYMENTS LTD" in upper or "EXTRATO EM EUR" in upper

    def _extract_description_and_merchant(self, description: str) -> tuple[str, str, str]:
        """Return description, merchant, transaction_type."""
        description = compact_line(description)

        card_match = CARD_MERCHANT_PATTERN.search(description)
        if card_match:
            merchant = compact_line(card_match.group(1))
            return description, extract_merchant(merchant), "card"

        received_match = RECEIVED_PATTERN.search(description)
        if received_match:
            merchant = compact_line(received_match.group(1))
            return description, extract_merchant(merchant), "transfer"

        if "Dinheiro adicionado à conta" in description:
            return description, "Dinheiro adicionado", "transfer"

        return description, extract_merchant(description), "other"

    def _parse_record(
        self,
        *,
        buffered_lines: list[str],
        date_id_line: str,
        pdf_path: Path,
        page_index: int,
    ) -> dict | None:
        """Parse a Wise transaction once the date/id line is encountered."""
        if not buffered_lines:
            return None

        amount_match = None
        amount_line_index = -1

        # The amount/balance usually live at the end of the first description
        # line, but scanning backwards makes this tolerant to wrapping.
        for index, line in enumerate(buffered_lines):
            match = AMOUNT_AT_END_PATTERN.match(line)
            if match:
                amount_match = match
                amount_line_index = index
                break

        if amount_match is None:
            return None

        description_parts: list[str] = []
        # Description before amount.
        description_parts.append(compact_line(amount_match.group("description")))
        # Continuation lines around the amount line usually belong to merchant text.
        for index, line in enumerate(buffered_lines):
            if index == amount_line_index:
                continue
            if not line.startswith(("ref:", "Descrição Entrada Saída Valor")):
                description_parts.append(line)

        description = compact_line(" ".join(description_parts))
        date_value = parse_wise_pt_date(date_id_line)
        tx_match = TRANSACTION_ID_PATTERN.search(date_id_line)
        external_id = tx_match.group(1).upper() if tx_match else ""

        if not date_value:
            return None

        signed_amount = normalize_money(amount_match.group("amount"))
        balance = normalize_money(amount_match.group("balance"))
        operation = "credit" if signed_amount > 0 else "debit"
        amount = abs(signed_amount)
        debit = amount if operation == "debit" else 0.0
        credit = amount if operation == "credit" else 0.0

        description, merchant, transaction_type = self._extract_description_and_merchant(description)
        category_info = categorize_transaction_with_details(
            description,
            merchant,
            operation,
            amount=amount,
        )

        return {
            "bank": self.bank_name,
            "account_currency": "EUR",
            "external_id": external_id,
            "launch_date": date_value,
            "value_date": date_value,
            "description": description,
            "merchant": merchant,
            "amount": amount,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "category": category_info["category"],
            "operation": operation,
            "transaction_type": transaction_type,
            "source_file": pdf_path.name,
            "source_page": page_index,
            "raw_line": compact_line(" | ".join(buffered_lines + [date_id_line])),
            "parse_method": "wise_regex",
            "parse_status": "ok",
            "parse_note": "",
            **category_info,
        }

    def extract_transactions(self, pdf_path: Path) -> list[dict]:
        transactions: list[dict] = []

        with pdfplumber.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if not text:
                    continue

                buffer: list[str] = []

                for raw_line in text.splitlines():
                    line = compact_line(raw_line)
                    if not line:
                        continue

                    # Skip headers/metadata.
                    if line.startswith("ref:") or line in {
                        "Descrição Entrada Saída Valor",
                        "Wise Payments Ltd.",
                    }:
                        continue
                    if line.startswith(
                        (
                            "1st Floor",
                            "London",
                            "EC2A",
                            "United Kingdom",
                            "Titular da Conta",
                            "IBAN",
                            "Swift/BIC",
                            "Gerado em:",
                            "Extrato em EUR",
                        )
                    ):
                        continue
                    if "BE67" in line and "TRWIBEB1" in line:
                        continue
                    if line in {"Brazil", "MG"} or re.fullmatch(r"\d{5}-\d{3}", line):
                        continue
                    if line.startswith("EUR em "):
                        continue

                    if DATE_ID_PATTERN.search(line):
                        parsed = self._parse_record(
                            buffered_lines=buffer,
                            date_id_line=line,
                            pdf_path=pdf_path,
                            page_index=page_index,
                        )
                        if parsed is not None:
                            transactions.append(parsed)
                        buffer = []
                    else:
                        # New transaction descriptions usually start with these
                        # prefixes. If an old buffer has no date line yet, keep it
                        # only if it may be a continuation; otherwise reset to avoid
                        # header noise leaking into descriptions.
                        if line.startswith(("Transação por cartão", "Recebeu dinheiro", "Dinheiro adicionado")):
                            buffer = [line]
                        else:
                            if buffer:
                                buffer.append(line)

        return transactions
