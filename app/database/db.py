"""SQLite database access layer with migrations for multi-bank imports."""

from __future__ import annotations

import sqlite3
import hashlib
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.config import DB_PATH
from app.categorization.rule_store import (
    delete_json_rules_for_category,
    rename_json_category,
    save_json_rule,
)

DEFAULT_CATEGORIES = [
    "Arrendamento",
    "Supermercado",
    "Alimentação",
    "Alimentação/Ovos",
    "Alimentação Escolar",
    "Restaurantes e Cafés",
    "Transportes",
    "Combustível",
    "Telecom",
    "Energia",
    "Água",
    "Saúde",
    "Beleza",
    "Vestuário",
    "Medicare",
    "Educação",
    "Compras Online",
    "Tecnologia",
    "Lazer",
    "Serviços",
    "Transferência",
    "Documentos PT",
    "Impostos e Taxas",
    "Seguros",
    "Criança/Família",
    "Casa",
    "Crédito",
    "Lavanderia",
    "Automóvel",
    "Turismo",
    "Outros",
]


TRANSACTION_COLUMNS: dict[str, str] = {
    "launch_date": "TEXT",
    "value_date": "TEXT",
    "description": "TEXT",
    "merchant": "TEXT",
    "amount": "REAL",
    "debit": "REAL",
    "credit": "REAL",
    "balance": "REAL",
    "category": "TEXT",
    "operation": "TEXT",
    "transaction_type": "TEXT",
    "bank": "TEXT DEFAULT 'ActivoBank'",
    "account_currency": "TEXT DEFAULT 'EUR'",
    "external_id": "TEXT",
    "source_file": "TEXT",
    "source_page": "INTEGER",
    "raw_line": "TEXT",
    "parse_method": "TEXT DEFAULT 'regex'",
    "parse_status": "TEXT DEFAULT 'ok'",
    "parse_note": "TEXT",
    "category_method": "TEXT",
    "suggested_category": "TEXT",
    "suggestion_confidence": "TEXT",
    "suggestion_reason": "TEXT",
    "suggestion_method": "TEXT",
    "imported_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    "audit_reviewed": "INTEGER DEFAULT 0",
    "audit_review_note": "TEXT",
}


def get_connection() -> sqlite3.Connection:
    """Create and return a SQLite connection with a safer timeout for Streamlit."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def init_db() -> None:
    """Create/migrate database tables and insert default categories."""
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS category_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
            """
        )

        existing_cols = _table_columns(conn, "transactions")
        for column, col_type in TRANSACTION_COLUMNS.items():
            if column not in existing_cols:
                cursor.execute(
                    f"ALTER TABLE transactions ADD COLUMN {column} {col_type}"
                )

        for category in DEFAULT_CATEGORIES:
            cursor.execute(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                (category,),
            )

        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_external
            ON transactions (bank, external_id)
            WHERE external_id IS NOT NULL AND external_id <> ''
            """
        )

        _backfill_generated_external_ids(conn)


def get_categories() -> list[str]:
    """Return category names sorted alphabetically."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM categories ORDER BY name"
        ).fetchall()
    return [str(row[0]) for row in rows]


def add_category(name: str) -> bool:
    """Add a category. Return True when inserted."""
    name = name.strip()
    if not name:
        return False
    init_db()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO categories (name) VALUES (?)",
            (name,),
        )
        return cur.rowcount > 0


def rename_category(
    old_name: str,
    new_name: str,
    *,
    update_transactions: bool = True,
) -> bool:
    """Rename a category and optionally migrate transactions/rules."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name or old_name == new_name:
        return False

    init_db()
    with get_connection() as conn:
        try:
            conn.execute(
                "UPDATE categories SET name = ? WHERE name = ?",
                (new_name, old_name),
            )
        except sqlite3.IntegrityError:
            return False

        if update_transactions:
            conn.execute(
                "UPDATE transactions SET category = ? WHERE category = ?",
                (new_name, old_name),
            )
            conn.execute(
                "UPDATE category_rules SET category = ? WHERE category = ?",
                (new_name, old_name),
            )

    if update_transactions:
        rename_json_category(old_name, new_name)

    return True


def delete_category(category: str, *, replacement: str = "Outros") -> bool:
    """Delete a category and move linked records to replacement."""
    category = category.strip()
    replacement = replacement.strip() or "Outros"
    if not category or category in {"Outros", "Crédito"}:
        return False

    init_db()
    with get_connection() as conn:
        conn.execute(
            "UPDATE transactions SET category = ? WHERE category = ?",
            (replacement, category),
        )
        conn.execute(
            "UPDATE category_rules SET category = ? WHERE category = ?",
            (replacement, category),
        )
        conn.execute("DELETE FROM categories WHERE name = ?", (category,))

    delete_json_rules_for_category(category, replacement=replacement)
    return True


def get_category_rules() -> dict[str, str]:
    """Load manually learned categorization rules from SQLite."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT keyword, category
            FROM category_rules
            ORDER BY LENGTH(keyword) DESC
            """
        ).fetchall()
    return {str(keyword).lower(): str(category) for keyword, category in rows}


def save_category_rule(keyword: str, category: str) -> None:
    """Save or update a learned categorization rule in SQLite and JSON."""
    keyword = " ".join(str(keyword).strip().lower().split())
    category = str(category).strip()

    if not keyword or not category or category in {"Outros", "Crédito"}:
        return

    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO category_rules (keyword, category)
            VALUES (?, ?)
            """,
            (keyword, category),
        )

    save_json_rule(
        keyword,
        category,
        source="dashboard_confirmed",
        confidence=1.0,
    )


def _transaction_insert_values(transaction: dict[str, Any]) -> dict[str, Any]:
    values = {column: transaction.get(column) for column in TRANSACTION_COLUMNS}
    if values["imported_at"] is None:
        values["imported_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if not str(values.get("external_id") or "").strip():
        values["external_id"] = _generated_external_id(values)
    return values


def _normalize_signature_text(value: Any) -> str:
    """Normalize text fields for stable duplicate detection."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_text.lower().split())


def _signature_number(value: Any) -> str:
    """Normalize numeric fields to cents for stable fingerprints."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:.2f}"


def _generated_external_id(values: dict[str, Any]) -> str:
    """Build a deterministic external id for statements without bank IDs."""
    bank = _normalize_signature_text(values.get("bank"))
    parts = [
        bank,
        _normalize_signature_text(values.get("launch_date")),
        _normalize_signature_text(values.get("value_date")),
        _normalize_signature_text(values.get("operation")),
        _normalize_signature_text(values.get("transaction_type")),
        _signature_number(values.get("amount")),
        _signature_number(values.get("debit")),
        _signature_number(values.get("credit")),
        _signature_number(values.get("balance")),
        _normalize_signature_text(values.get("merchant")),
        _normalize_signature_text(values.get("description")),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    bank_prefix = bank.replace(" ", "_") or "bank"
    return f"generated:{bank_prefix}:{digest}"


def _backfill_generated_external_ids(conn: sqlite3.Connection) -> None:
    """Populate generated external ids for old rows that lack one.

    If the database already contains duplicate old rows, only the first row gets
    the generated id. That is enough to make future imports of the same statement
    collide with the existing row without deleting historical data.
    """
    columns = _table_columns(conn, "transactions")
    if "external_id" not in columns:
        return

    selected_columns = [
        "id",
        *TRANSACTION_COLUMNS.keys(),
    ]
    existing_selected = [column for column in selected_columns if column in columns]
    rows = conn.execute(
        f"""
        SELECT {", ".join(existing_selected)}
        FROM transactions
        WHERE external_id IS NULL OR external_id = ''
        ORDER BY id
        """
    ).fetchall()
    if not rows:
        return

    used_external_ids = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT external_id
            FROM transactions
            WHERE external_id IS NOT NULL AND external_id <> ''
            """
        ).fetchall()
    }
    names = existing_selected

    for row in rows:
        row_data = dict(zip(names, row))
        transaction_id = int(row_data["id"])
        values = {column: row_data.get(column) for column in TRANSACTION_COLUMNS}
        generated_id = _generated_external_id(values)
        if generated_id in used_external_ids:
            continue
        conn.execute(
            "UPDATE transactions SET external_id = ? WHERE id = ?",
            (generated_id, transaction_id),
        )
        used_external_ids.add(generated_id)


def save_transactions(transactions: Iterable[dict[str, Any]]) -> int:
    """Save extracted transactions into SQLite and return inserted count.

    For Wise and other banks with external IDs, duplicates are ignored through
    the unique index on (bank, external_id). ActivoBank statements without
    external IDs are inserted normally.
    """
    init_db()
    count = 0
    cols = list(TRANSACTION_COLUMNS)
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)

    with get_connection() as conn:
        cursor = conn.cursor()

        for transaction in transactions:
            values = _transaction_insert_values(transaction)
            try:
                cursor.execute(
                    f"""
                    INSERT OR IGNORE INTO transactions ({col_sql})
                    VALUES ({placeholders})
                    """,
                    [values[column] for column in cols],
                )
                count += cursor.rowcount
            except sqlite3.IntegrityError:
                continue

    return count
