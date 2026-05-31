"""Financial dashboard application.

This dashboard is intentionally defensive because the project evolved across
several database versions. It supports:
- single raw folder with auto-detected banks;
- ActivoBank and Wise imports;
- audit review;
- category suggestions;
- category management;
- learned merchant rules;
- charts and CSV exports.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from app.categorization.category_mapping import resolve_category_name
from app.config import DB_PATH, LOGS_DIR
from app.database.db import get_connection, save_category_rule

try:
    from app.categorization.dspy_category import (
        audit_category_with_dspy,
        is_dspy_audit_enabled,
        is_dspy_category_enabled,
        suggest_category_with_dspy,
    )
except ImportError:
    audit_category_with_dspy = None
    is_dspy_audit_enabled = None
    is_dspy_category_enabled = None
    suggest_category_with_dspy = None

try:
    from app.categorization.ollama_category import (
        audit_category_with_ollama,
        is_ollama_category_enabled,
        suggest_category_with_ollama,
    )
except ImportError:
    audit_category_with_ollama = None
    is_ollama_category_enabled = None
    suggest_category_with_ollama = None

try:
    from app.database.db import add_category, delete_category, rename_category
except ImportError:
    add_category = None
    delete_category = None
    rename_category = None

try:
    from app.categorization.rule_store import MERCHANT_RULES_PATH, load_json_rules
except ImportError:
    MERCHANT_RULES_PATH = Path("data/rules/merchant_rules.json")

    def load_json_rules() -> list[dict]:
        return []


MONTH_NAMES = [
    "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
    "Jul", "Ago", "Set", "Out", "Nov", "Dez",
]

MONTH_NAME_TO_NUMBER = {name: index + 1 for index, name in enumerate(MONTH_NAMES)}

BASE_TRANSACTION_COLUMNS = [
    "id",
    "launch_date",
    "value_date",
    "description",
    "merchant",
    "amount",
    "debit",
    "credit",
    "balance",
    "category",
    "operation",
    "transaction_type",
    "bank",
    "source_file",
    "source_page",
    "parse_method",
    "parse_status",
    "parse_note",
    "category_method",
    "suggested_category",
    "suggestion_confidence",
    "suggestion_reason",
    "suggestion_method",
    "raw_line",
    "imported_at",
    "audit_reviewed",
    "audit_review_note",
    "bank_name",
    "source_bank",
    "external_transaction_id",
    "parser_name",
]

VISIBLE_TRANSACTION_COLUMNS = [
    "id",
    "date_display",
    "bank",
    "description",
    "merchant",
    "debit",
    "credit",
    "balance",
    "category",
    "operation",
    "transaction_type",
    "source_file",
    "external_transaction_id",
]

AUDIT_COLUMNS = [
    "id",
    "date_display",
    "bank",
    "description",
    "merchant",
    "debit",
    "credit",
    "balance",
    "category",
    "audit_reasons",
    "parse_status",
    "parse_method",
    "category_method",
    "suggested_category",
    "suggestion_confidence",
    "suggestion_method",
    "suggestion_reason",
    "source_file",
    "source_page",
    "external_transaction_id",
    "raw_line",
]


def _table_columns(table: str) -> set[str]:
    """Return column names available in a SQLite table."""
    if not DB_PATH.exists():
        return set()

    with get_connection() as conn:
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except Exception:
            return set()

    return {str(row[1]) for row in rows}


def _ensure_dashboard_columns() -> None:
    """Add dashboard/audit columns when an older database does not have them."""
    if not DB_PATH.exists():
        return

    existing = _table_columns("transactions")
    if not existing:
        return

    columns_to_add = {
        "source_file": "TEXT",
        "source_page": "INTEGER",
        "parse_method": "TEXT DEFAULT 'regex'",
        "parse_status": "TEXT DEFAULT 'ok'",
        "parse_note": "TEXT DEFAULT ''",
        "category_method": "TEXT DEFAULT ''",
        "suggested_category": "TEXT DEFAULT ''",
        "suggestion_confidence": "TEXT DEFAULT ''",
        "suggestion_reason": "TEXT DEFAULT ''",
        "suggestion_method": "TEXT DEFAULT ''",
        "raw_line": "TEXT DEFAULT ''",
        "imported_at": "TEXT DEFAULT ''",
        "audit_reviewed": "INTEGER DEFAULT 0",
        "audit_review_note": "TEXT DEFAULT ''",
        "bank_name": "TEXT DEFAULT ''",
        "source_bank": "TEXT DEFAULT ''",
        "external_transaction_id": "TEXT DEFAULT ''",
        "parser_name": "TEXT DEFAULT ''",
    }

    with get_connection() as conn:
        for column, column_type in columns_to_add.items():
            if column not in existing:
                try:
                    conn.execute(
                        f"ALTER TABLE transactions ADD COLUMN {column} {column_type}"
                    )
                except Exception:
                    pass


def _select_existing_columns(table: str, wanted: Iterable[str]) -> list[str]:
    existing = _table_columns(table)
    return [column for column in wanted if column in existing]


def load_transactions() -> pd.DataFrame:
    """Load transactions from SQLite database."""
    _ensure_dashboard_columns()

    if not DB_PATH.exists():
        return pd.DataFrame()

    existing_columns = _select_existing_columns("transactions", BASE_TRANSACTION_COLUMNS)
    if not existing_columns:
        return pd.DataFrame()

    query = f"""
        SELECT {", ".join(existing_columns)}
        FROM transactions
        ORDER BY id
    """

    with get_connection() as conn:
        return pd.read_sql_query(query, conn)


def load_categories() -> list[str]:
    """Load available categories."""
    if not DB_PATH.exists() or "categories" not in _sqlite_tables():
        return ["Outros", "Crédito"]

    with get_connection() as conn:
        try:
            df = pd.read_sql_query(
                "SELECT name FROM categories ORDER BY name",
                conn,
            )
        except Exception:
            return ["Outros", "Crédito"]

    categories = [str(value) for value in df["name"].tolist() if str(value).strip()]
    for required in ("Outros", "Crédito"):
        if required not in categories:
            categories.append(required)
    return sorted(categories)


def _sqlite_tables() -> set[str]:
    if not DB_PATH.exists():
        return set()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _parse_bank_date_series(series: pd.Series) -> pd.Series:
    """Parse generic dates.

    This helper is used as a fallback. For ActivoBank legacy imports, prefer
    _parse_dates_by_source(), because old ActivoBank rows were persisted as
    MM.DD.YYYY while Wise rows are usually DD.MM.YYYY or ISO-like.
    """
    text = series.fillna("").astype(str).str.strip()

    parsed = pd.to_datetime(text, format="%d.%m.%Y", errors="coerce")

    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            text.loc[missing],
            format="%d/%m/%Y",
            errors="coerce",
        )

    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            text.loc[missing],
            errors="coerce",
            dayfirst=True,
        )

    return parsed


def _looks_like_activobank_row(row: pd.Series) -> bool:
    """Return True for old ActivoBank/Extrato Combinado imports."""
    source_file = str(row.get("source_file", "") or "").lower()
    bank_name = str(row.get("bank_name", "") or "").lower()
    source_bank = str(row.get("source_bank", "") or "").lower()
    parser_name = str(row.get("parser_name", "") or "").lower()

    markers = " ".join([source_file, bank_name, source_bank, parser_name])
    return (
        "extrato combinado" in markers
        or "activobank" in markers
        or "activo" in markers
    )


def _parse_single_date_by_source(row: pd.Series, column: str) -> pd.Timestamp:
    """Parse one row's date using source-aware logic.

    Important:
    - Old ActivoBank parser stored dates like MM.DD.YYYY.
      Example: 11.01.2025 means 01/11/2025, not 11/01/2025.
    - Wise statements are Portuguese natural dates and newer parser rows
      should be DD.MM.YYYY or ISO-like.
    """
    raw = str(row.get(column, "") or "").strip()
    if not raw:
        return pd.NaT

    if _looks_like_activobank_row(row):
        for fmt in ("%m.%d.%Y", "%m/%d/%Y", "%Y-%m-%d"):
            parsed = pd.to_datetime(raw, format=fmt, errors="coerce")
            if not pd.isna(parsed):
                return parsed

        # Fallback for rare already-correct Activo rows.
        parsed = pd.to_datetime(raw, format="%d.%m.%Y", errors="coerce")
        if not pd.isna(parsed):
            return parsed

    else:
        for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
            parsed = pd.to_datetime(raw, format=fmt, errors="coerce")
            if not pd.isna(parsed):
                return parsed

    return pd.to_datetime(raw, errors="coerce", dayfirst=not _looks_like_activobank_row(row))


def _parse_dates_by_source(df: pd.DataFrame, column: str) -> pd.Series:
    """Parse dates row by row, respecting the bank/source parser."""
    if column not in df.columns:
        return pd.Series(pd.NaT, index=df.index)

    return df.apply(lambda row: _parse_single_date_by_source(row, column), axis=1)


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare dataframe for dashboard analysis."""
    if df.empty:
        return df

    df = df.copy()

    for column in BASE_TRANSACTION_COLUMNS:
        if column not in df.columns:
            df[column] = None

    for column in ["amount", "debit", "credit", "balance"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)

    df["date"] = _parse_dates_by_source(df, "value_date")

    fallback_missing = df["date"].isna() & df["launch_date"].notna()
    if fallback_missing.any():
        df.loc[fallback_missing, "date"] = _parse_dates_by_source(
            df.loc[fallback_missing],
            "launch_date",
        )

    df = df.dropna(subset=["date"]).copy()
    df["year"] = df["date"].dt.year.astype(int)
    df["month"] = df["date"].dt.month.astype(int)
    # Keep a real datetime column for correct Streamlit sorting.
    # It is displayed as DD/MM/YYYY through column_config.
    df["date_display"] = df["date"].dt.normalize()

    text_columns = [
        "description",
        "merchant",
        "category",
        "operation",
        "transaction_type",
        "bank",
        "source_file",
        "parse_method",
        "parse_status",
        "parse_note",
        "category_method",
        "suggested_category",
        "suggestion_confidence",
        "suggestion_reason",
        "suggestion_method",
        "raw_line",
        "imported_at",
        "audit_review_note",
        "bank_name",
        "source_bank",
        "external_transaction_id",
        "parser_name",
    ]
    for column in text_columns:
        df[column] = df[column].fillna("").astype(str)

    df["category"] = df["category"].replace("", "Outros")
    df["parse_method"] = df["parse_method"].replace("", "regex")
    df["parse_status"] = df["parse_status"].replace("", "ok")
    df["audit_reviewed"] = pd.to_numeric(
        df["audit_reviewed"],
        errors="coerce",
    ).fillna(0).astype(int)

    df["bank"] = df["bank"].fillna("").astype(str)
    df.loc[df["bank"].eq(""), "bank"] = df.loc[df["bank"].eq(""), "bank_name"]
    df.loc[df["bank"].eq(""), "bank"] = df.loc[df["bank"].eq(""), "source_bank"]
    df.loc[df["bank"].eq(""), "bank"] = df.loc[df["bank"].eq(""), "parser_name"]
    df["bank"] = df["bank"].replace("", "Desconhecido")

    # Normalize operation when old imports do not have it correctly.
    df.loc[(df["credit"] > 0) & df["operation"].eq(""), "operation"] = "credit"
    df.loc[(df["debit"] > 0) & df["operation"].eq(""), "operation"] = "debit"

    return df


def euro(value: float) -> str:
    """Format euro values."""
    return f"{float(value):,.2f} €"


def euro_pt(value: float, *, decimals: int = 2) -> str:
    """Format euro values with Portuguese separators."""
    formatted = f"{float(value):,.{decimals}f}"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{formatted} €"


def chart_euro(value: float) -> str:
    """Full euro label for chart values."""
    return euro_pt(value, decimals=2)


def chart_number(value: float) -> str:
    """Full numeric label for count charts."""
    return f"{float(value):,.0f}".replace(",", ".")


def variation_label(previous: float, current: float) -> str:
    """Human-friendly variation label."""
    previous = float(previous)
    current = float(current)

    if previous == 0 and current == 0:
        return "—"
    if previous == 0 and current > 0:
        return "Sem histórico"
    if previous > 0 and current == 0:
        return "-100.0%"

    change = ((current - previous) / previous) * 100
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.1f}%"


def style_variation(value: str) -> str:
    """CSS style for variation column."""
    if value == "—":
        return "color: #777;"
    if value == "Sem histórico":
        return "color: #666; font-weight: 600;"
    if value.startswith("+"):
        return "color: #0a7a35; font-weight: 600;"
    if value.startswith("-"):
        return "color: #b00020; font-weight: 600;"
    return ""



def estimate_period_balances(df: pd.DataFrame) -> dict[str, float]:
    """Estimate opening and closing balances using transaction balances.

    The transaction balance is usually the balance after the transaction.
    Opening balance is estimated as:
        balance_before_first = first_balance - first_credit + first_debit

    The calculation is done per bank/source and then summed, so Wise and
    ActivoBank are not mixed as a single account before the aggregation.
    """
    if df.empty or "balance" not in df.columns:
        return {
            "opening_balance": 0.0,
            "closing_balance": 0.0,
            "balance_change": 0.0,
        }

    working = df.copy()
    working = working.sort_values(["bank", "date", "id"])

    opening_total = 0.0
    closing_total = 0.0

    for _, group in working.groupby("bank", dropna=False):
        group = group.sort_values(["date", "id"])
        if group.empty:
            continue

        first = group.iloc[0]
        last = group.iloc[-1]

        first_balance = float(first.get("balance", 0) or 0)
        first_debit = float(first.get("debit", 0) or 0)
        first_credit = float(first.get("credit", 0) or 0)
        last_balance = float(last.get("balance", 0) or 0)

        opening_total += first_balance - first_credit + first_debit
        closing_total += last_balance

    return {
        "opening_balance": opening_total,
        "closing_balance": closing_total,
        "balance_change": closing_total - opening_total,
    }


def update_transaction_fields(
    updated_df: pd.DataFrame,
    *,
    apply_to_similar: bool = False,
    match_field: str = "merchant",
    only_suspect: bool = False,
    learn_rules: bool = True,
) -> int:
    """Update description, merchant and category for manually edited rows."""
    if updated_df.empty:
        return 0

    allowed_match_fields = {"merchant", "description"}
    if match_field not in allowed_match_fields:
        match_field = "merchant"

    updated_count = 0
    learned_rules: list[tuple[str, str]] = []

    editable_columns = [
        col for col in ["description", "merchant", "category"]
        if col in updated_df.columns
    ]

    with get_connection() as conn:
        cursor = conn.cursor()

        for _, row in updated_df.iterrows():
            transaction_id = int(row["id"])
            description = str(row.get("description") or "").strip()
            merchant = str(row.get("merchant") or "").strip()
            category = str(row.get("category") or "").strip() or "Outros"

            set_parts = []
            params = []

            if "description" in editable_columns:
                set_parts.append("description = ?")
                params.append(description)
            if "merchant" in editable_columns:
                set_parts.append("merchant = ?")
                params.append(merchant)
            if "category" in editable_columns:
                set_parts.append("category = ?")
                params.append(category)
                set_parts.append("category_method = 'user_manual_review'")
                set_parts.append("audit_reviewed = 1")
                set_parts.append("audit_review_note = 'Revisado manualmente no dashboard'")

            if not set_parts:
                continue

            params.append(transaction_id)
            cursor.execute(
                f"""
                UPDATE transactions
                SET {", ".join(set_parts)}
                WHERE id = ?
                """,
                params,
            )
            updated_count += int(cursor.rowcount or 0)

            if learn_rules and category not in {"Outros", "Crédito"}:
                if merchant:
                    learned_rules.append((merchant, category))
                if description:
                    learned_rules.append((description, category))

            if apply_to_similar and category:
                match_value = merchant if match_field == "merchant" else description
                if match_value:
                    updated_count += _update_many_by_similarity(
                        cursor,
                        category=category,
                        transaction_id=transaction_id,
                        match_field=match_field,
                        match_value=match_value,
                        only_suspect=only_suspect,
                    )
                    if learn_rules and category not in {"Outros", "Crédito"}:
                        learned_rules.append((match_value, category))

    for keyword, category in learned_rules:
        try:
            save_category_rule(keyword, category)
        except Exception:
            pass

    return updated_count


def create_manual_transaction(
    *,
    transaction_date,
    operation: str,
    description: str,
    merchant: str,
    amount: float,
    category: str,
    account_currency: str = "EUR",
    note: str = "",
) -> int:
    """Insert a manual debit or credit transaction into SQLite."""
    amount = abs(float(amount or 0))
    if amount <= 0:
        return 0

    operation = "credit" if operation == "Crédito" else "debit"
    category = "Crédito" if operation == "credit" else (category.strip() or "Outros")
    date_text = pd.to_datetime(transaction_date).strftime("%Y-%m-%d")
    description = description.strip() or ("Crédito manual" if operation == "credit" else "Despesa manual")
    merchant = merchant.strip() or "Lançamento manual"
    external_id = f"manual-{uuid4().hex}"

    values = {
        "launch_date": date_text,
        "value_date": date_text,
        "description": description,
        "merchant": merchant,
        "amount": amount,
        "debit": amount if operation == "debit" else 0.0,
        "credit": amount if operation == "credit" else 0.0,
        "balance": 0.0,
        "category": category,
        "operation": operation,
        "transaction_type": "manual_entry",
        "bank": "Manual",
        "account_currency": account_currency,
        "external_id": external_id,
        "source_file": "manual_entry",
        "source_page": None,
        "raw_line": note.strip(),
        "parse_method": "manual_entry",
        "parse_status": "ok",
        "parse_note": note.strip(),
        "category_method": "manual_entry",
        "suggested_category": "",
        "suggestion_confidence": "",
        "suggestion_reason": "",
        "suggestion_method": "",
        "audit_reviewed": 1,
        "audit_review_note": "Lançamento criado manualmente no dashboard",
    }

    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    col_sql = ", ".join(columns)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO transactions ({col_sql})
            VALUES ({placeholders})
            """,
            [values[column] for column in columns],
        )
        return int(cursor.rowcount or 0)


def show_manual_entry_form(*, compact: bool = False) -> None:
    """Render form to add manual expenses or credits."""
    title = "Lançar despesa ou crédito"
    if compact:
        st.markdown(f'<div class="bi-section">{title}</div>', unsafe_allow_html=True)
    else:
        st.header(title)

    categories = load_categories()
    debit_categories = [cat for cat in categories if cat != "Crédito"]
    if "Outros" not in debit_categories:
        debit_categories.append("Outros")

    with st.form("manual_transaction_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 1])
        transaction_date = c1.date_input("Data")
        operation = c2.radio(
            "Tipo",
            ["Despesa", "Crédito"],
            horizontal=True,
            key="manual_entry_operation",
        )
        amount = c3.number_input(
            "Valor",
            min_value=0.01,
            step=1.0,
            format="%.2f",
            key="manual_entry_amount",
        )

        c4, c5 = st.columns([1.2, 1])
        description = c4.text_input("Descrição", key="manual_entry_description")
        merchant = c5.text_input("Comerciante / origem", key="manual_entry_merchant")

        category = st.selectbox(
            "Categoria",
            debit_categories,
            index=debit_categories.index("Outros") if "Outros" in debit_categories else 0,
            disabled=operation == "Crédito",
            key="manual_entry_category",
        )
        note = st.text_area("Nota opcional", height=80, key="manual_entry_note")

        submitted = st.form_submit_button("Salvar lançamento")

    if submitted:
        saved = create_manual_transaction(
            transaction_date=transaction_date,
            operation=operation,
            description=description,
            merchant=merchant,
            amount=amount,
            category=category,
            note=note,
        )
        if saved:
            st.success("Lançamento salvo.")
            st.rerun()
        else:
            st.warning("Não foi possível salvar. Verifique o valor informado.")


def download_csv_button(df: pd.DataFrame, filename: str, label: str) -> None:
    """Render a CSV download button."""
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(label, csv, file_name=filename, mime="text/csv")


def save_agent_run_log(details: pd.DataFrame, *, prefix: str = "agent_reallocation") -> Path | None:
    """Persist an agent run details dataframe and return its path."""
    if details.empty:
        return None
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"{prefix}_{timestamp}.csv"
    details.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def show_agent_run_result(details: pd.DataFrame, *, log_path: Path | None = None) -> None:
    """Show detailed agent execution result in the dashboard."""
    if details.empty:
        st.info("O agente não analisou nenhuma transação.")
        return

    analyzed = len(details)
    changed = int(details["status"].eq("realocada").sum()) if "status" in details else 0
    suggested = int(details["status"].eq("sugerida").sum()) if "status" in details else 0
    skipped = analyzed - changed - suggested

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Analisadas", analyzed)
    c2.metric("Realocadas", changed)
    c3.metric("Sugestões", suggested)
    c4.metric("Ignoradas", skipped)

    if log_path is not None:
        st.caption(f"Log salvo em: {log_path}")

    display_cols = [
        "id",
        "status",
        "categoria_atual",
        "categoria_modelo",
        "categoria_sugerida",
        "confianca",
        "metodo",
        "motivo",
        "merchant",
        "descricao",
    ]
    display_cols = [col for col in display_cols if col in details.columns]
    show_dataframe(details[display_cols], height=320)
    download_csv_button(details, "resultado_agente_categorias.csv", "Baixar resultado do agente CSV")



def date_column_config() -> dict:
    """Reusable Streamlit date column config."""
    return {
        "date_display": st.column_config.DateColumn(
            "Data",
            format="DD/MM/YYYY",
        )
    }


def show_dataframe(df: pd.DataFrame, **kwargs) -> None:
    """Display dataframe with consistent date formatting and correct date sorting."""
    column_config = kwargs.pop("column_config", {})
    column_config = {**date_column_config(), **column_config}
    st.dataframe(df, width="stretch", column_config=column_config, **kwargs)


def show_data_editor(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Display data editor with consistent date formatting and correct date sorting."""
    column_config = kwargs.pop("column_config", {})
    column_config = {**date_column_config(), **column_config}
    return st.data_editor(df, width="stretch", column_config=column_config, **kwargs)


def free_text_filter(df: pd.DataFrame, key: str, label: str = "Pesquisa livre") -> pd.DataFrame:
    """Filter a dataframe by free text across relevant text columns."""
    query = st.text_input(label, "", key=key).strip()
    if not query:
        return df

    searchable_columns = [
        column
        for column in [
            "description",
            "merchant",
            "category",
            "bank",
            "source_file",
            "external_transaction_id",
            "raw_line",
        ]
        if column in df.columns
    ]

    if not searchable_columns:
        return df

    mask = pd.Series(False, index=df.index)
    for column in searchable_columns:
        mask = mask | df[column].astype(str).str.contains(query, case=False, na=False)

    return df[mask].copy()


def apply_powerbi_style() -> None:
    """Apply a dense executive dashboard style inspired by BI tools."""
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.25rem;
            padding-bottom: 2rem;
        }
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 6px;
            padding: 0.75rem 0.85rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
        }
        div[data-testid="stMetricLabel"],
        div[data-testid="stMetricLabel"] * {
            color: #42526b !important;
            font-size: 0.8rem;
        }
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] * {
            color: #102033 !important;
            font-size: 1.45rem;
            font-weight: 700;
        }
        .bi-kpi-card {
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 6px;
            padding: 0.72rem 0.85rem 0.82rem 0.85rem;
            min-height: 105px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
        }
        .bi-kpi-title {
            color: #344156;
            font-size: 0.82rem;
            font-weight: 700;
            margin: 0 0 0.12rem 0;
        }
        .bi-kpi-note {
            color: #66758d;
            font-size: 0.72rem;
            line-height: 1.15;
            min-height: 1.55rem;
            margin: 0 0 0.35rem 0;
        }
        .bi-kpi-value {
            color: #061a33;
            font-size: 1.45rem;
            line-height: 1.15;
            font-weight: 800;
            margin: 0;
        }
        .bi-band {
            background: #f5f7fb;
            border: 1px solid #d9dee7;
            border-radius: 6px;
            padding: 0.75rem 0.9rem;
            margin: 0.4rem 0 0.8rem 0;
        }
        .bi-title {
            color: #102033;
            font-size: 1.35rem;
            font-weight: 700;
            margin: 0;
        }
        .bi-subtitle {
            color: #5f6f89;
            font-size: 0.85rem;
            margin-top: 0.15rem;
        }
        .bi-section {
            color: #102033;
            font-size: 1rem;
            font-weight: 700;
            margin: 0.35rem 0 0.2rem 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_bi_kpi(container, title: str, value: object, note: str) -> None:
    """Render a BI-style KPI card with explicit context."""
    container.markdown(
        f"""
        <div class="bi-kpi-card">
            <p class="bi-kpi-title">{title}</p>
            <p class="bi-kpi-note">{note}</p>
            <p class="bi-kpi-value">{value}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def create_audit_data(df: pd.DataFrame) -> pd.DataFrame:
    """Create a dataframe with transactions that deserve manual review."""
    audit_df = df.copy()
    audit_df["audit_reasons"] = ""

    def add_reason(mask: pd.Series, reason: str) -> None:
        mask = mask.reindex(audit_df.index, fill_value=False)
        if not mask.any():
            return

        current = audit_df.loc[mask, "audit_reasons"].astype(str)
        audit_df.loc[mask, "audit_reasons"] = current.apply(
            lambda text: f"{text}; {reason}".strip("; ") if text else reason
        )

    add_reason(
        audit_df["parse_status"].str.lower().ne("ok"),
        "Possível falha na importação",
    )
    add_reason(
        audit_df["parse_method"].str.lower().eq("dspy"),
        "Linha recuperada por DSPy",
    )
    add_reason(
        audit_df["category"].eq("Outros"),
        "Categoria não identificada",
    )
    add_reason(
        audit_df["suggested_category"].str.len() > 0,
        "Há sugestão de categoria para confirmar",
    )
    add_reason(
        audit_df["category_method"].str.lower().eq("dspy_agent_reallocation"),
        "Categoria realocada automaticamente por agente DSPy",
    )
    add_reason(
        audit_df["amount"].abs().gt(1000),
        "Valor alto: revisar",
    )
    add_reason(
        audit_df["raw_line"].eq(""),
        "Linha original não foi guardada",
    )

    # Critical fix:
    # Use map() so the monthly median aligns with audit_df's index.
    debit_rows = audit_df["debit"].gt(0)
    monthly_median_by_month = (
        audit_df.loc[debit_rows]
        .groupby(["year", "month"])["debit"]
        .median()
    )

    month_keys = list(zip(audit_df["year"], audit_df["month"]))
    month_reference = pd.Series(
        [monthly_median_by_month.get(key, 0.0) for key in month_keys],
        index=audit_df.index,
        dtype=float,
    )

    add_reason(
        audit_df["debit"].gt(month_reference.fillna(0) * 8)
        & month_reference.fillna(0).gt(0),
        "Valor muito acima do padrão do mês",
    )

    audit_df = audit_df[audit_df["audit_reasons"].str.len() > 0].copy()
    audit_df = audit_df[
        (audit_df["audit_reviewed"].eq(0))
        | (audit_df["parse_status"].str.lower().ne("ok"))
    ].copy()

    return audit_df.sort_values("date", ascending=False)


def _load_current_categories(transaction_ids: list[int]) -> dict[int, str]:
    if not transaction_ids:
        return {}

    placeholders = ",".join("?" for _ in transaction_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, category FROM transactions WHERE id IN ({placeholders})",
            transaction_ids,
        ).fetchall()

    return {int(row[0]): str(row[1] or "") for row in rows}


def _update_many_by_similarity(
    cursor,
    *,
    category: str,
    transaction_id: int,
    match_field: str,
    match_value: str,
    only_suspect: bool,
) -> int:
    """Update similar rows with safe LIKE matching."""
    if not match_value.strip():
        return 0

    match_field = match_field if match_field in {"merchant", "description"} else "merchant"
    pattern = f"%{match_value.lower()}%"

    if only_suspect:
        cursor.execute(
            f"""
            UPDATE transactions
            SET category = ?,
                category_method = 'user_audit_similar',
                audit_reviewed = 1,
                audit_review_note = 'Revisado por regra semelhante no dashboard'
            WHERE id <> ?
              AND (
                LOWER(COALESCE({match_field}, '')) = LOWER(?)
                OR LOWER(COALESCE({match_field}, '')) LIKE ?
              )
              AND (
                LOWER(COALESCE(category, '')) = 'outros'
                OR COALESCE(audit_reviewed, 0) = 0
                OR LOWER(COALESCE(parse_status, 'ok')) <> 'ok'
              )
            """,
            (category, transaction_id, match_value, pattern),
        )
    else:
        cursor.execute(
            f"""
            UPDATE transactions
            SET category = ?,
                category_method = 'user_manual_similar',
                audit_reviewed = 1,
                audit_review_note = 'Revisado por regra semelhante no dashboard'
            WHERE id <> ?
              AND (
                LOWER(COALESCE({match_field}, '')) = LOWER(?)
                OR LOWER(COALESCE({match_field}, '')) LIKE ?
              )
            """,
            (category, transaction_id, match_value, pattern),
        )

    return int(cursor.rowcount or 0)


def update_transaction_categories(
    updated_df: pd.DataFrame,
    *,
    apply_to_similar: bool = False,
    match_field: str = "merchant",
    only_suspect: bool = False,
    mark_reviewed: bool = False,
) -> int:
    """Update categories and learn rules without locking SQLite."""
    if updated_df.empty:
        return 0

    updated_count = 0
    learned_rules: list[tuple[str, str]] = []

    transaction_ids = [int(value) for value in updated_df["id"].tolist()]
    current_categories = _load_current_categories(transaction_ids)

    with get_connection() as conn:
        cursor = conn.cursor()

        for _, row in updated_df.iterrows():
            transaction_id = int(row["id"])
            category = str(row.get("category", "")).strip()

            if not category:
                continue

            previous_category = current_categories.get(transaction_id, "")

            if mark_reviewed:
                cursor.execute(
                    """
                    UPDATE transactions
                    SET category = ?,
                        category_method = 'user_audit',
                        audit_reviewed = 1,
                        audit_review_note = 'Revisado no dashboard'
                    WHERE id = ?
                    """,
                    (category, transaction_id),
                )
                updated_count += int(cursor.rowcount or 0)
            elif previous_category != category:
                cursor.execute(
                    """
                    UPDATE transactions
                    SET category = ?,
                        category_method = 'user_manual'
                    WHERE id = ?
                    """,
                    (category, transaction_id),
                )
                updated_count += int(cursor.rowcount or 0)

            merchant = str(row.get("merchant") or "").strip()
            description = str(row.get("description") or "").strip()

            if category not in {"Outros", "Crédito"}:
                if merchant:
                    learned_rules.append((merchant, category))
                if description and match_field == "description":
                    learned_rules.append((description, category))

            if apply_to_similar:
                match_value = merchant if match_field == "merchant" else description
                updated_count += _update_many_by_similarity(
                    cursor,
                    category=category,
                    transaction_id=transaction_id,
                    match_field=match_field,
                    match_value=match_value,
                    only_suspect=only_suspect,
                )
                if match_value and category not in {"Outros", "Crédito"}:
                    learned_rules.append((match_value, category))

    # Save learned rules after the main connection closes to avoid database locks.
    for keyword, category in learned_rules:
        try:
            save_category_rule(keyword, category)
        except Exception:
            pass

    return updated_count


def show_executive_summary(df: pd.DataFrame) -> None:
    """Show executive summary focused on financial decision making."""
    st.header("Resumo executivo")

    years = sorted(df["year"].unique().tolist())
    selected_year = st.selectbox(
        "Ano",
        years,
        index=len(years) - 1,
        key="summary_year",
    )

    year_df = df[df["year"] == selected_year].copy()
    debit_df = year_df[year_df["debit"] > 0]
    credit_df = year_df[year_df["credit"] > 0]

    months_with_expenses = debit_df["month"].nunique()
    monthly_living_cost = (
        debit_df.groupby("month")["debit"].sum().mean()
        if months_with_expenses > 0
        else 0.0
    )

    balances = estimate_period_balances(year_df)
    cashflow = float(credit_df["credit"].sum() - debit_df["debit"].sum())

    st.caption(
        "Créditos e débitos representam o fluxo do período. "
        "O saldo inicial/final estimado considera o saldo carregado de períodos anteriores, "
        "calculado a partir do saldo da primeira e da última transação importada."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo inicial estimado", euro(balances["opening_balance"]))
    c2.metric("Saldo final estimado", euro(balances["closing_balance"]))
    c3.metric("Variação estimada do saldo", euro(balances["balance_change"]))
    c4.metric("Fluxo líquido do ano", euro(cashflow))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Débitos no ano", euro(debit_df["debit"].sum()))
    c2.metric("Créditos no ano", euro(credit_df["credit"].sum()))
    c3.metric("Média mensal do custo de vida", euro(monthly_living_cost))
    c4.metric("Categoria Outros", len(year_df[year_df["category"] == "Outros"]))

    st.caption(
        "A média mensal considera apenas os meses com débitos importados no ano escolhido."
    )

    st.subheader("Top categorias de custo")
    top_categories = (
        debit_df.groupby("category")["debit"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
        .rename(columns={"debit": "total"})
    )
    top_categories["percentual"] = (
        top_categories["total"] / top_categories["total"].sum() * 100
        if not top_categories.empty and top_categories["total"].sum() > 0
        else 0
    )
    top_categories_display = top_categories.copy()
    top_categories_display["total"] = top_categories_display["total"].map(euro)
    top_categories_display["percentual"] = top_categories_display["percentual"].map(
        lambda value: f"{value:.1f}%"
    )
    show_dataframe(top_categories_display)
    download_csv_button(top_categories, "resumo_top_categorias.csv", "Baixar top categorias CSV")

    st.subheader("Evolução mensal do custo de vida")
    monthly = (
        debit_df.groupby("month")["debit"]
        .sum()
        .reindex(range(1, 13), fill_value=0)
        .reset_index()
        .rename(columns={"index": "month", "debit": "total"})
    )
    monthly["mês"] = monthly["month"].apply(lambda value: MONTH_NAMES[int(value) - 1])
    monthly_display = monthly[["mês", "total"]].copy()
    monthly_display["total"] = monthly_display["total"].map(euro)
    show_dataframe(monthly_display)
    download_csv_button(monthly, "resumo_custo_mensal.csv", "Baixar custo mensal CSV")


def monthly_debit_summary(df: pd.DataFrame, selected_year: int) -> pd.DataFrame:
    """Create month-by-month comparison for selected and previous years."""
    previous_year = selected_year - 1
    debit_df = df[df["debit"] > 0].copy()

    grouped = (
        debit_df[debit_df["year"].isin([previous_year, selected_year])]
        .groupby(["month", "year"])["debit"]
        .sum()
        .reset_index()
    )

    if grouped.empty:
        return pd.DataFrame(columns=["Mês", previous_year, selected_year, "Variação %"])

    months = sorted(grouped["month"].unique().tolist())

    pivot = grouped.pivot(index="month", columns="year", values="debit")
    pivot = pivot.reindex(months).fillna(0)

    for year in [previous_year, selected_year]:
        if year not in pivot.columns:
            pivot[year] = 0.0

    pivot = pivot[[previous_year, selected_year]]
    pivot["Mês"] = [MONTH_NAMES[int(month) - 1] for month in pivot.index]
    pivot["Variação %"] = [
        variation_label(row[previous_year], row[selected_year])
        for _, row in pivot.iterrows()
    ]

    return pivot.reset_index(drop=True)[["Mês", previous_year, selected_year, "Variação %"]]


def category_month_summary(
    df: pd.DataFrame,
    *,
    selected_year: int,
    selected_month: int,
) -> pd.DataFrame:
    """Create category comparison for a specific month."""
    previous_year = selected_year - 1
    debit_df = df[
        (df["debit"] > 0)
        & (df["month"] == selected_month)
        & (df["year"].isin([previous_year, selected_year]))
    ].copy()

    if debit_df.empty:
        return pd.DataFrame(
            columns=["Categoria", previous_year, selected_year, "Variação %"]
        )

    grouped = (
        debit_df.groupby(["category", "year"])["debit"]
        .sum()
        .reset_index()
    )
    pivot = grouped.pivot(index="category", columns="year", values="debit").fillna(0)

    for year in [previous_year, selected_year]:
        if year not in pivot.columns:
            pivot[year] = 0.0

    pivot = pivot[[previous_year, selected_year]]
    pivot["total_sort"] = pivot[[previous_year, selected_year]].max(axis=1)
    pivot = pivot.sort_values("total_sort", ascending=False).drop(columns="total_sort")
    pivot["Variação %"] = [
        variation_label(row[previous_year], row[selected_year])
        for _, row in pivot.iterrows()
    ]

    return pivot.reset_index().rename(columns={"category": "Categoria"})


def _plot_grouped_bar(
    data: pd.DataFrame,
    *,
    label_column: str,
    value_columns: list[int],
    title: str,
    ylabel: str = "Valor (€)",
) -> None:
    """Plot grouped bar chart with values above bars."""
    if data.empty:
        st.info("Sem dados para apresentar.")
        return

    fig, ax = plt.subplots(figsize=(16, 7))

    plot_df = data[[label_column] + value_columns].copy()
    plot_df = plot_df.set_index(label_column)

    plot_df.plot(kind="bar", ax=ax, width=0.78)

    max_value = plot_df.max().max()
    if max_value > 0:
        ax.set_ylim(0, max_value * 1.25)

    for container in ax.containers:
        labels = [
            f"{value:.2f} €" if value > 0 else "0,00 €"
            for value in container.datavalues
        ]
        ax.bar_label(container, labels=labels, fontsize=8, padding=3, rotation=90)

    ax.set_title(title, fontsize=16)
    ax.set_xlabel("")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    st.pyplot(fig)


def _plot_powerbi_bar(
    data: pd.DataFrame,
    *,
    label_column: str,
    value_column: str,
    title: str,
    horizontal: bool = False,
    value_kind: str = "currency",
) -> None:
    """Render a compact bar chart for the BI-style dashboard."""
    if data.empty:
        st.info("Sem dados para apresentar.")
        return

    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    plot_df = data.copy()

    if horizontal:
        plot_df = plot_df.sort_values(value_column, ascending=True)
        bars = ax.barh(plot_df[label_column], plot_df[value_column], color="#1f77b4")
        max_value = float(plot_df[value_column].max() or 0)
        if max_value > 0:
            ax.set_xlim(0, max_value * 1.18)
        for bar in bars:
            width = float(bar.get_width())
            label = chart_number(width) if value_kind == "count" else chart_euro(width)
            y = bar.get_y() + bar.get_height() / 2
            if max_value > 0 and width >= max_value * 0.18:
                ax.text(
                    width - max_value * 0.015,
                    y,
                    label,
                    va="center",
                    ha="right",
                    fontsize=8,
                    color="white",
                    fontweight="bold",
                )
            else:
                ax.text(
                    width + max_value * 0.015,
                    y,
                    label,
                    va="center",
                    ha="left",
                    fontsize=8,
                    color="#102033",
                    fontweight="bold",
                )
        ax.set_xlabel("Valor (€)")
        ax.set_ylabel("")
    else:
        bars = ax.bar(plot_df[label_column], plot_df[value_column], color="#1f77b4")
        max_value = float(plot_df[value_column].max() or 0)
        if max_value > 0:
            ax.set_ylim(0, max_value * 1.18)
        for bar in bars:
            height = float(bar.get_height())
            label = chart_number(height) if value_kind == "count" else chart_euro(height)
            x = bar.get_x() + bar.get_width() / 2
            if max_value > 0 and height >= max_value * 0.20:
                ax.text(
                    x,
                    height - max_value * 0.035,
                    label,
                    va="top",
                    ha="center",
                    fontsize=8,
                    color="white",
                    fontweight="bold",
                    rotation=90,
                )
            else:
                ax.text(
                    x,
                    height + max_value * 0.025,
                    label,
                    va="bottom",
                    ha="center",
                    fontsize=8,
                    color="#102033",
                    fontweight="bold",
                    rotation=90,
                )
        ax.set_ylabel("Valor (€)")
        ax.set_xlabel("")
        plt.xticks(rotation=35, ha="right")

    ax.set_title(title, fontsize=12, fontweight="bold", color="#102033")
    ax.grid(axis="x" if horizontal else "y", linestyle="--", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    st.pyplot(fig)


def _plot_powerbi_monthly(data: pd.DataFrame) -> None:
    """Render monthly debit/credit bars for the BI-style dashboard."""
    if data.empty:
        st.info("Sem dados mensais para apresentar.")
        return

    fig, ax = plt.subplots(figsize=(11, 3.8))
    x = list(range(len(data)))
    width = 0.38
    debit_bars = ax.bar(
        [value - width / 2 for value in x],
        data["debitos"],
        width=width,
        label="Débitos",
        color="#1f77b4",
    )
    credit_bars = ax.bar(
        [value + width / 2 for value in x],
        data["creditos"],
        width=width,
        label="Créditos",
        color="#2ca02c",
    )
    max_value = float(max(data["debitos"].max(), data["creditos"].max()) or 0)
    if max_value > 0:
        ax.set_ylim(0, max_value * 1.25)
    for bars in (debit_bars, credit_bars):
        for bar in bars:
            height = float(bar.get_height())
            if height <= 0:
                continue
            label = chart_euro(height)
            x_pos = bar.get_x() + bar.get_width() / 2
            if max_value > 0 and height >= max_value * 0.18:
                ax.text(
                    x_pos,
                    height - max_value * 0.035,
                    label,
                    va="top",
                    ha="center",
                    fontsize=8,
                    color="white",
                    fontweight="bold",
                    rotation=90,
                )
            else:
                ax.text(
                    x_pos,
                    height + max_value * 0.025,
                    label,
                    va="bottom",
                    ha="center",
                    fontsize=8,
                    color="#102033",
                    fontweight="bold",
                    rotation=90,
                )
    ax.set_xticks(list(x))
    ax.set_xticklabels(data["mês"])
    ax.set_title("Fluxo mensal", fontsize=12, fontweight="bold", color="#102033")
    ax.set_ylabel("Valor (€)")
    ax.legend(frameon=False, ncols=2)
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    st.pyplot(fig)


def _plot_category_pie(data: pd.DataFrame) -> None:
    """Render debit share by category as a pie chart."""
    if data.empty or float(data["total"].sum() or 0) <= 0:
        st.info("Sem débitos por categoria para apresentar.")
        return

    plot_df = data.copy()
    if len(plot_df) > 7:
        top = plot_df.head(7).copy()
        other_total = float(plot_df.iloc[7:]["total"].sum())
        if other_total > 0:
            top = pd.concat(
                [
                    top,
                    pd.DataFrame([{"category": "Outras", "total": other_total}]),
                ],
                ignore_index=True,
            )
        plot_df = top

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    colors = [
        "#1f77b4",
        "#2ca02c",
        "#ff7f0e",
        "#9467bd",
        "#17becf",
        "#d62728",
        "#8c564b",
        "#7f7f7f",
    ]
    wedges, _, autotexts = ax.pie(
        plot_df["total"],
        labels=None,
        autopct=lambda pct: f"{pct:.1f}%".replace(".", ",") if pct >= 3 else "",
        startangle=90,
        counterclock=False,
        colors=colors[: len(plot_df)],
        pctdistance=0.72,
        textprops={"fontsize": 8, "color": "white", "fontweight": "bold"},
        wedgeprops={"linewidth": 1, "edgecolor": "white"},
    )
    for autotext in autotexts:
        autotext.set_bbox(
            {"boxstyle": "round,pad=0.2", "facecolor": "#102033", "edgecolor": "none", "alpha": 0.85}
        )
    ax.legend(
        wedges,
        plot_df["category"],
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        fontsize=8,
    )
    ax.set_title("% por categoria", fontsize=12, fontweight="bold", color="#102033")
    ax.axis("equal")
    plt.tight_layout()
    st.pyplot(fig)


def _plot_credit_debit_comparison(total_credit: float, total_debit: float) -> None:
    """Render period credits vs debits as a compact comparison chart."""
    data = pd.DataFrame(
        {
            "tipo": ["Créditos", "Débitos"],
            "total": [float(total_credit), float(total_debit)],
        }
    )
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    bars = ax.bar(data["tipo"], data["total"], color=["#2ca02c", "#1f77b4"], width=0.55)
    max_value = float(data["total"].max() or 0)
    if max_value > 0:
        ax.set_ylim(0, max_value * 1.18)
    for bar in bars:
        height = float(bar.get_height())
        label = chart_euro(height)
        x_pos = bar.get_x() + bar.get_width() / 2
        if max_value > 0 and height >= max_value * 0.18:
            ax.text(
                x_pos,
                height - max_value * 0.04,
                label,
                va="top",
                ha="center",
                fontsize=10,
                color="white",
                fontweight="bold",
            )
        else:
            ax.text(
                x_pos,
                height + max_value * 0.025,
                label,
                va="bottom",
                ha="center",
                fontsize=10,
                color="#102033",
                fontweight="bold",
            )
    ax.set_title("Créditos x débitos do período", fontsize=12, fontweight="bold", color="#102033")
    ax.set_ylabel("Valor (€)")
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    st.pyplot(fig)


def show_powerbi_dashboard(df: pd.DataFrame) -> None:
    """Alternative dashboard with a BI-style executive layout."""
    apply_powerbi_style()

    st.markdown(
        """
        <div class="bi-band">
            <p class="bi-title">Painel financeiro executivo</p>
            <div class="bi-subtitle">Visão consolidada de fluxo, categorias, merchants e qualidade de categorização.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    years = sorted(df["year"].unique().tolist())
    months = ["Todos"] + MONTH_NAMES
    bank_values = sorted(
        value
        for value in df["bank"].dropna().astype(str).unique().tolist()
        if value and value != "Desconhecido"
    )
    if not bank_values and df["bank"].eq("Desconhecido").any():
        bank_values = ["Desconhecido"]
    banks = ["Todos"] + bank_values
    categories = ["Todas"] + sorted(df["category"].dropna().unique().tolist())

    f1, f2, f3, f4 = st.columns([1, 1, 1.2, 1.4])
    selected_years = f1.multiselect(
        "Ano",
        years,
        default=[years[-1]] if years else [],
        key="bi_years",
    )
    selected_month = f2.selectbox("Mês", months, key="bi_month")
    selected_bank = f3.selectbox("Banco/Formato", banks, key="bi_bank")
    selected_category = f4.selectbox("Categoria", categories, key="bi_category")

    filtered = df.copy()
    if selected_years:
        filtered = filtered[filtered["year"].isin(selected_years)]
    if selected_month != "Todos":
        filtered = filtered[filtered["month"].eq(MONTH_NAME_TO_NUMBER[selected_month])]
    if selected_bank != "Todos":
        filtered = filtered[filtered["bank"].eq(selected_bank)]
    if selected_category != "Todas":
        filtered = filtered[filtered["category"].eq(selected_category)]

    filtered = free_text_filter(
        filtered,
        "bi_search",
        "Pesquisar descrição, merchant, categoria ou banco",
    )

    if filtered.empty:
        st.info("Sem dados para os filtros escolhidos.")
        return

    debit_df = filtered[filtered["debit"] > 0].copy()
    credit_df = filtered[filtered["credit"] > 0].copy()
    total_debit = float(debit_df["debit"].sum())
    total_credit = float(credit_df["credit"].sum())
    cashflow = total_credit - total_debit
    months_with_debits = debit_df["date"].dt.to_period("M").nunique()
    monthly_avg = (
        debit_df.groupby(debit_df["date"].dt.to_period("M"))["debit"].sum().mean()
        if months_with_debits > 0
        else 0.0
    )
    others_count = int(filtered["category"].eq("Outros").sum())
    dspy_agent_count = int(
        filtered["category_method"].fillna("").astype(str).eq("dspy_agent_reallocation").sum()
    )

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    render_bi_kpi(k1, "Débitos", euro(total_debit), "Total de despesas no filtro")
    render_bi_kpi(k2, "Créditos", euro(total_credit), "Entradas recebidas no filtro")
    render_bi_kpi(k3, "Fluxo líquido", euro(cashflow), "Créditos menos débitos")
    render_bi_kpi(k4, "Média mensal", euro(monthly_avg), "Média de débitos por mês")
    render_bi_kpi(k5, "Categoria Outros", others_count, "Transações sem categoria final")
    render_bi_kpi(k6, "Realocadas IA", dspy_agent_count, "Categorias alteradas pelo agente")

    with st.expander("Novo lançamento manual", expanded=False):
        show_manual_entry_form(compact=True)

    active_years = ", ".join(str(year) for year in selected_years) if selected_years else "Todos"
    st.caption(
        f"Filtros aplicados aos gráficos: anos {active_years}; mês {selected_month}; "
        f"banco {selected_bank}; categoria {selected_category}; transações {len(filtered)}."
    )

    st.markdown('<div class="bi-section">Fluxo e composição</div>', unsafe_allow_html=True)
    chart_left, chart_right = st.columns([1.35, 1])

    monthly = (
        filtered.groupby(["year", "month"], dropna=False)
        .agg(creditos=("credit", "sum"), debitos=("debit", "sum"))
        .reset_index()
        .sort_values(["year", "month"])
    )
    monthly["mês"] = monthly.apply(
        lambda row: f"{MONTH_NAMES[int(row['month']) - 1]}/{int(row['year'])}",
        axis=1,
    )

    with chart_left:
        _plot_powerbi_monthly(monthly)

    category_totals = (
        debit_df.groupby("category", dropna=False)["debit"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
        .rename(columns={"debit": "total"})
    )
    top_categories = category_totals.head(10).copy()
    with chart_right:
        _plot_powerbi_bar(
            top_categories,
            label_column="category",
            value_column="total",
            title="Top categorias de custo",
            horizontal=True,
        )

    composition_left, composition_right = st.columns(2)
    with composition_left:
        _plot_category_pie(category_totals)
    with composition_right:
        _plot_credit_debit_comparison(total_credit, total_debit)

    st.markdown('<div class="bi-section">Merchants e categorização</div>', unsafe_allow_html=True)
    m1, m2 = st.columns(2)

    top_merchants = (
        debit_df.groupby("merchant", dropna=False)["debit"]
        .sum()
        .sort_values(ascending=False)
        .head(12)
        .reset_index()
        .rename(columns={"debit": "total"})
    )
    top_merchants["merchant_label"] = top_merchants["merchant"].replace("", "Sem merchant")

    with m1:
        _plot_powerbi_bar(
            top_merchants,
            label_column="merchant_label",
            value_column="total",
            title="Maiores despesas por comerciante",
            horizontal=True,
        )

    category_totals_detail = (
        debit_df.groupby("category", dropna=False)["debit"]
        .sum()
        .sort_values(ascending=False)
        .head(14)
        .reset_index()
        .rename(columns={"debit": "total"})
    )
    with m2:
        _plot_powerbi_bar(
            category_totals_detail,
            label_column="category",
            value_column="total",
            title="Total gasto por categoria",
            horizontal=True,
        )

    st.markdown('<div class="bi-section">Detalhe financeiro</div>', unsafe_allow_html=True)
    detail_cols = [
        "id",
        "date_display",
        "bank",
        "description",
        "merchant",
        "debit",
        "credit",
        "category",
        "category_method",
        "suggested_category",
        "suggestion_confidence",
    ]
    detail_cols = [col for col in detail_cols if col in filtered.columns]
    detail = filtered.sort_values(["date", "id"], ascending=False)[detail_cols].head(300)
    show_dataframe(detail, height=420)
    download_csv_button(filtered, "painel_executivo_filtrado.csv", "Baixar dados do painel CSV")


def show_graphs(df: pd.DataFrame) -> None:
    """Show analytics charts and exportable source tables."""
    st.header("Gráficos")

    years = sorted(df["year"].unique().tolist())
    selected_year = st.selectbox(
        "Ano para comparação",
        years,
        index=len(years) - 1,
        key="graphs_year",
    )
    previous_year = selected_year - 1

    st.subheader(f"Débitos mensais — {previous_year} vs {selected_year}")
    monthly = monthly_debit_summary(df, selected_year)

    _plot_grouped_bar(
        monthly,
        label_column="Mês",
        value_columns=[previous_year, selected_year],
        title=f"Débitos mensais — {previous_year} vs {selected_year}",
    )

    display_monthly = monthly.copy()
    for year in [previous_year, selected_year]:
        if year in display_monthly.columns:
            display_monthly[year] = display_monthly[year].map(euro)

    show_dataframe(
        display_monthly.style.map(style_variation, subset=["Variação %"]),
    )
    download_csv_button(monthly, "debitos_mensais.csv", "Baixar débitos mensais CSV")

    available_months = sorted(df[df["year"] == selected_year]["month"].unique().tolist())
    if not available_months:
        available_months = sorted(df["month"].unique().tolist())

    month_labels = [MONTH_NAMES[int(month) - 1] for month in available_months]
    selected_month_label = st.selectbox(
        "Mês para comparar categorias",
        month_labels,
        key="graphs_month",
    )
    selected_month = MONTH_NAME_TO_NUMBER[selected_month_label]

    st.subheader(
        f"Categorias — {selected_month_label}/{previous_year} vs {selected_month_label}/{selected_year}"
    )
    categories = category_month_summary(
        df,
        selected_year=selected_year,
        selected_month=selected_month,
    )

    _plot_grouped_bar(
        categories,
        label_column="Categoria",
        value_columns=[previous_year, selected_year],
        title=f"Categorias — {selected_month_label}: {previous_year} vs {selected_year}",
    )

    display_categories = categories.copy()
    for year in [previous_year, selected_year]:
        if year in display_categories.columns:
            display_categories[year] = display_categories[year].map(euro)

    show_dataframe(
        display_categories.style.map(style_variation, subset=["Variação %"]),
    )
    download_csv_button(categories, "categorias_mes.csv", "Baixar categorias CSV")

    st.subheader("Transações do período selecionado")
    period_df = df[
        df["year"].isin([previous_year, selected_year])
        & df["month"].eq(selected_month)
    ].copy()
    period_df = free_text_filter(period_df, "period_search")

    visible_cols = [col for col in VISIBLE_TRANSACTION_COLUMNS if col in period_df.columns]
    period_display_df = period_df.sort_values("date", ascending=False)
    show_dataframe(
        period_display_df[visible_cols],
    )
    download_csv_button(period_df, "transacoes_periodo.csv", "Baixar transações do período CSV")

    st.subheader("Top merchants do período")
    merchant_df = (
        period_df[period_df["debit"] > 0]
        .groupby(["merchant", "category"])["debit"]
        .sum()
        .sort_values(ascending=False)
        .head(20)
        .reset_index()
        .rename(columns={"debit": "total"})
    )

    if not merchant_df.empty:
        fig, ax = plt.subplots(figsize=(16, 7))
        labels = merchant_df["merchant"].replace("", "Sem merchant")
        bars = ax.bar(labels, merchant_df["total"])
        max_value = merchant_df["total"].max()
        if max_value > 0:
            ax.set_ylim(0, max_value * 1.25)
        for bar in bars:
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height,
                f"{height:.2f} €",
                ha="center",
                va="bottom",
                rotation=90,
                fontsize=8,
            )
        ax.set_title("Top merchants do período", fontsize=16)
        ax.set_ylabel("Valor (€)")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        st.pyplot(fig)
    else:
        st.info("Sem débitos para top merchants no período.")


def mark_transaction_reviewed(transaction_id: int, review_note: str = "") -> int:
    """Mark a transaction as reviewed so it leaves the audit queue."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE transactions
            SET audit_reviewed = 1,
                audit_review_note = ?
            WHERE id = ?
            """,
            (review_note, transaction_id),
        )
        return int(cursor.rowcount or 0)


def save_dspy_suggestion(
    transaction_id: int,
    *,
    category: str,
    confidence: str,
    reason: str,
    method: str,
) -> int:
    """Persist a DSPy suggestion without applying it as the transaction category."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE transactions
            SET suggested_category = ?,
                suggestion_confidence = ?,
                suggestion_reason = ?,
                suggestion_method = ?,
                audit_reviewed = 0
            WHERE id = ?
            """,
            (category, confidence, reason, method, transaction_id),
        )
        return int(cursor.rowcount or 0)


def run_dspy_audit_batch(
    df: pd.DataFrame,
    *,
    limit: int,
    progress_callback: Callable[[int, int, dict[str, object]], None] | None = None,
) -> tuple[int, int, pd.DataFrame]:
    """Run DSPy audit/suggestion for existing database rows."""
    if df.empty:
        return 0, 0, pd.DataFrame()

    category_options = load_categories()
    candidates = df[
        df["operation"].fillna("").astype(str).str.lower().eq("debit")
        & df["category"].fillna("").astype(str).ne("Crédito")
    ].copy()

    if candidates.empty:
        return 0, 0, pd.DataFrame()

    candidates = candidates.sort_values("date", ascending=False).head(limit)
    processed = 0
    saved = 0
    details: list[dict[str, object]] = []
    total_candidates = len(candidates)

    def record(detail: dict[str, object]) -> None:
        details.append(detail)
        if progress_callback is not None:
            progress_callback(processed, total_candidates, detail)

    for _, row in candidates.iterrows():
        processed += 1
        transaction_id = int(row["id"])
        category = str(row.get("category", "") or "").strip() or "Outros"
        description = str(row.get("description", "") or "")
        merchant = str(row.get("merchant", "") or "")
        amount = float(row.get("amount", 0) or 0)
        operation = str(row.get("operation", "") or "debit")
        base_detail = {
            "id": transaction_id,
            "descricao": description,
            "merchant": merchant,
            "valor": amount,
            "operacao": operation,
            "categoria_atual": category,
        }

        suggestion = None
        if category == "Outros":
            if suggest_category_with_dspy is not None:
                suggestion = suggest_category_with_dspy(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    existing_categories=category_options,
                )
            if suggestion is None and suggest_category_with_ollama is not None:
                suggestion = suggest_category_with_ollama(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    existing_categories=category_options,
                )
        else:
            if audit_category_with_dspy is not None:
                suggestion = audit_category_with_dspy(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    current_category=category,
                    category_method=str(row.get("category_method", "") or ""),
                    existing_categories=category_options,
                )
            if suggestion is None and audit_category_with_ollama is not None:
                suggestion = audit_category_with_ollama(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    current_category=category,
                    category_method=str(row.get("category_method", "") or ""),
                    existing_categories=category_options,
                )

        if suggestion is None:
            record(
                {
                    **base_detail,
                    "status": "ignorada",
                    "categoria_modelo": "",
                    "categoria_sugerida": "",
                    "confianca": "",
                    "metodo": "",
                    "motivo": "Nenhuma sugestão retornada pelo modelo.",
                }
            )
            continue

        model_category = str(suggestion.category).strip()
        suggested_category, mapping_note = resolve_category_name(
            model_category,
            category_options,
        )
        if not suggested_category:
            record(
                {
                    **base_detail,
                    "status": "ignorada",
                    "categoria_modelo": model_category,
                    "categoria_sugerida": "",
                    "confianca": suggestion.confidence,
                    "metodo": suggestion.method,
                    "motivo": "Modelo retornou categoria vazia.",
                }
            )
            continue

        if suggested_category == category:
            record(
                {
                    **base_detail,
                    "status": "mantida",
                    "categoria_modelo": model_category,
                    "categoria_sugerida": suggested_category,
                    "confianca": suggestion.confidence,
                    "metodo": suggestion.method,
                    "motivo": f"{mapping_note} {suggestion.reason or 'Categoria atual mantida pelo modelo.'}".strip(),
                }
            )
            continue

        affected = save_dspy_suggestion(
            transaction_id,
            category=suggested_category,
            confidence=suggestion.confidence,
            reason=f"{mapping_note} {suggestion.reason}".strip(),
            method=suggestion.method,
        )
        saved += affected
        record(
            {
                **base_detail,
                "status": "sugerida" if affected else "falha_update",
                "categoria_modelo": model_category,
                "categoria_sugerida": suggested_category,
                "confianca": suggestion.confidence,
                "metodo": suggestion.method,
                "motivo": f"{mapping_note} {suggestion.reason}".strip(),
            }
        )

    return processed, saved, pd.DataFrame(details)


def _confidence_passes(confidence: str, minimum: str) -> bool:
    levels = {"low": 1, "medium": 2, "high": 3}
    confidence_level = levels.get(str(confidence).strip().lower(), 0)
    minimum_level = levels.get(str(minimum).strip().lower(), 3)
    return confidence_level >= minimum_level


def _ensure_agent_category(category: str, *, create_missing: bool) -> bool:
    category = str(category).strip()
    if not category:
        return False

    categories = set(load_categories())
    if category in categories:
        return True

    if not create_missing or add_category is None:
        return False

    try:
        return bool(add_category(category))
    except Exception:
        return False


def apply_dspy_reallocation(
    transaction_id: int,
    *,
    category: str,
    confidence: str,
    reason: str,
    method: str,
) -> int:
    """Apply a DSPy agent category directly to a transaction."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE transactions
            SET category = ?,
                category_method = 'dspy_agent_reallocation',
                suggested_category = ?,
                suggestion_confidence = ?,
                suggestion_reason = ?,
                suggestion_method = ?,
                audit_reviewed = 0,
                audit_review_note = 'Categoria realocada automaticamente pelo agente DSPy'
            WHERE id = ?
            """,
            (category, category, confidence, reason, method, transaction_id),
        )
        return int(cursor.rowcount or 0)


def run_dspy_reallocation_agent(
    df: pd.DataFrame,
    *,
    limit: int,
    minimum_confidence: str,
    scope: str,
    create_missing_categories: bool,
    learn_rules: bool,
    progress_callback: Callable[[int, int, dict[str, object]], None] | None = None,
) -> tuple[int, int, int, pd.DataFrame]:
    """Run the DSPy agent and directly reallocate transaction categories."""
    if df.empty:
        return 0, 0, 0, pd.DataFrame()

    category_options = load_categories()
    candidates = df[
        df["operation"].fillna("").astype(str).str.lower().eq("debit")
        & df["category"].fillna("").astype(str).ne("Crédito")
    ].copy()

    if scope == "Apenas categoria Outros":
        candidates = candidates[candidates["category"].fillna("").astype(str).eq("Outros")]

    if candidates.empty:
        return 0, 0, 0, pd.DataFrame()

    candidates = candidates.sort_values("date", ascending=False).head(limit)
    processed = 0
    changed = 0
    skipped = 0
    details: list[dict[str, object]] = []
    total_candidates = len(candidates)

    def record(detail: dict[str, object]) -> None:
        details.append(detail)
        if progress_callback is not None:
            progress_callback(processed, total_candidates, detail)

    for _, row in candidates.iterrows():
        processed += 1
        transaction_id = int(row["id"])
        current_category = str(row.get("category", "") or "").strip() or "Outros"
        description = str(row.get("description", "") or "")
        merchant = str(row.get("merchant", "") or "")
        amount = float(row.get("amount", 0) or 0)
        operation = str(row.get("operation", "") or "debit")
        base_detail = {
            "id": transaction_id,
            "descricao": description,
            "merchant": merchant,
            "valor": amount,
            "operacao": operation,
            "categoria_atual": current_category,
        }

        suggestion = None
        if current_category == "Outros":
            if suggest_category_with_dspy is not None:
                suggestion = suggest_category_with_dspy(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    existing_categories=category_options,
                )
            if suggestion is None and suggest_category_with_ollama is not None:
                suggestion = suggest_category_with_ollama(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    existing_categories=category_options,
                )
        else:
            if audit_category_with_dspy is not None:
                suggestion = audit_category_with_dspy(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    current_category=current_category,
                    category_method=str(row.get("category_method", "") or ""),
                    existing_categories=category_options,
                )
            if suggestion is None and audit_category_with_ollama is not None:
                suggestion = audit_category_with_ollama(
                    description=description,
                    merchant=merchant,
                    amount=amount,
                    operation=operation,
                    current_category=current_category,
                    category_method=str(row.get("category_method", "") or ""),
                    existing_categories=category_options,
                )

        if suggestion is None:
            skipped += 1
            record(
                {
                    **base_detail,
                    "status": "ignorada",
                    "categoria_sugerida": "",
                    "confianca": "",
                    "metodo": "",
                    "motivo": "Nenhuma sugestão retornada pelo modelo.",
                }
            )
            continue

        model_category = str(suggestion.category).strip()
        suggested_category, mapping_note = resolve_category_name(
            model_category,
            category_options,
        )
        if not suggested_category:
            skipped += 1
            record(
                {
                    **base_detail,
                    "status": "ignorada",
                    "categoria_modelo": model_category,
                    "categoria_sugerida": "",
                    "confianca": suggestion.confidence,
                    "metodo": suggestion.method,
                    "motivo": "Modelo retornou categoria vazia.",
                }
            )
            continue

        if suggested_category == current_category:
            skipped += 1
            record(
                {
                    **base_detail,
                    "status": "mantida",
                    "categoria_modelo": model_category,
                    "categoria_sugerida": suggested_category,
                    "confianca": suggestion.confidence,
                    "metodo": suggestion.method,
                    "motivo": f"{mapping_note} {suggestion.reason or 'Categoria atual mantida pelo modelo.'}".strip(),
                }
            )
            continue

        if not _confidence_passes(suggestion.confidence, minimum_confidence):
            skipped += 1
            record(
                {
                    **base_detail,
                    "status": "baixa_confianca",
                    "categoria_modelo": model_category,
                    "categoria_sugerida": suggested_category,
                    "confianca": suggestion.confidence,
                    "metodo": suggestion.method,
                    "motivo": f"{mapping_note} {suggestion.reason or 'Sugestão abaixo da confiança mínima.'}".strip(),
                }
            )
            continue

        if not _ensure_agent_category(
            suggested_category,
            create_missing=create_missing_categories,
        ):
            skipped += 1
            record(
                {
                    **base_detail,
                    "status": "categoria_inexistente",
                    "categoria_modelo": model_category,
                    "categoria_sugerida": suggested_category,
                    "confianca": suggestion.confidence,
                    "metodo": suggestion.method,
                    "motivo": f"{mapping_note} Categoria sugerida não existe e criação automática está desligada.".strip(),
                }
            )
            continue

        affected = apply_dspy_reallocation(
            transaction_id,
            category=suggested_category,
            confidence=suggestion.confidence,
            reason=suggestion.reason,
            method=suggestion.method,
        )
        changed += affected
        record(
            {
                **base_detail,
                "status": "realocada" if affected else "falha_update",
                "categoria_modelo": model_category,
                "categoria_sugerida": suggested_category,
                "confianca": suggestion.confidence,
                "metodo": suggestion.method,
                "motivo": f"{mapping_note} {suggestion.reason}".strip(),
            }
        )

        if learn_rules and suggested_category not in {"Outros", "Crédito"}:
            for keyword in (merchant, description):
                keyword = str(keyword).strip()
                if keyword:
                    try:
                        save_category_rule(keyword, suggested_category)
                    except Exception:
                        pass

    return processed, changed, skipped, pd.DataFrame(details)


def show_audit(df: pd.DataFrame) -> None:
    """Render audit panel with manual confirmation workflow."""
    st.header("Auditoria")

    st.caption(
        "A auditoria não significa que a transação está errada. "
        "Ela apenas indica registos que merecem revisão. "
        "Marque 'confirmar_ok' para confirmar que a linha está correta, "
        "ou altere a categoria e salve."
    )

    audit_df = create_audit_data(df)

    # create_audit_data() already returns only records with audit_reasons and
    # excludes reviewed rows, except parser failures. Do not depend on columns
    # such as is_suspect, because older DB/dashboard versions do not have them.
    pending_df = audit_df.copy()

    suggestions_count = int(
        pending_df["suggested_category"].fillna("").astype(str).str.len().gt(0).sum()
        if "suggested_category" in pending_df.columns
        else 0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Suspeitas pendentes", len(pending_df))
    c2.metric("Categoria Outros", int(df["category"].eq("Outros").sum()))
    c3.metric("Falhas de parse", int(df["parse_status"].str.lower().ne("ok").sum()))
    c4.metric("Sugestões", suggestions_count)

    def configure_ollama_runtime(enabled: bool, model: str) -> bool:
        if not enabled:
            return (
                bool(is_ollama_category_enabled and is_ollama_category_enabled())
                if is_ollama_category_enabled is not None
                else False
            )
        os.environ["ENABLE_OLLAMA_CATEGORY"] = "1"
        os.environ["ENABLE_WEB_RESEARCH"] = "1"
        os.environ["WEB_SEARCH_PROVIDER"] = "ollama"
        os.environ["OLLAMA_RESEARCH_MODEL"] = model.strip() or "qwen2.5:14b"
        return True

    with st.expander("Autoauditoria DSPy e pesquisa de empresas", expanded=False):
        st.caption(
            "Executa DSPy sobre transações já importadas. Para linhas em 'Outros', "
            "pede uma categoria. Para linhas já categorizadas, faz uma auditoria e "
            "só grava sugestão quando discordar. A categoria original nunca é aplicada automaticamente."
        )

        dspy_category_on = (
            bool(is_dspy_category_enabled and is_dspy_category_enabled())
            if is_dspy_category_enabled is not None
            else False
        )
        dspy_audit_on = (
            bool(is_dspy_audit_enabled and is_dspy_audit_enabled())
            if is_dspy_audit_enabled is not None
            else False
        )
        runtime_ollama = st.checkbox(
            "Usar Ollama local nesta execução",
            value=not (dspy_category_on or dspy_audit_on),
            key="audit_runtime_ollama",
        )
        runtime_ollama_model = st.text_input(
            "Modelo Ollama",
            value=os.getenv("OLLAMA_RESEARCH_MODEL", "qwen2.5:14b"),
            key="audit_runtime_ollama_model",
            disabled=not runtime_ollama,
        )
        ollama_on = configure_ollama_runtime(runtime_ollama, runtime_ollama_model) or (
            bool(is_ollama_category_enabled and is_ollama_category_enabled())
            if is_ollama_category_enabled is not None
            else False
        )

        status = []
        status.append(f"DSPy sugestões: {'ativo' if dspy_category_on else 'inativo'}")
        status.append(f"DSPy auditoria: {'ativo' if dspy_audit_on else 'inativo'}")
        status.append(f"Ollama local: {'ativo' if ollama_on else 'inativo'}")
        st.caption(" | ".join(status))

        audit_limit = st.number_input(
            "Máximo de transações a auditar agora",
            min_value=1,
            max_value=500,
            value=50,
            step=10,
            key="dspy_audit_limit",
        )

        if st.button("Executar autoauditoria DSPy agora", key="run_dspy_audit_now"):
            if not dspy_category_on and not dspy_audit_on and not ollama_on:
                st.warning(
                    "Ative ENABLE_DSPY_CATEGORY=1/ENABLE_DSPY_AUDIT=1 com OPENAI_API_KEY "
                    "ou ENABLE_OLLAMA_CATEGORY=1 com Ollama antes de executar."
                )
            else:
                audit_progress_bar = st.progress(0, text="Preparando autoauditoria...")
                audit_progress_status = st.empty()
                audit_progress_table = st.empty()
                recent_audit_details: list[dict[str, object]] = []

                def update_audit_progress(
                    processed_now: int,
                    total_now: int,
                    detail: dict[str, object],
                ) -> None:
                    progress_value = processed_now / total_now if total_now else 1.0
                    status_now = str(detail.get("status", ""))
                    merchant_now = str(detail.get("merchant", "")) or "Sem merchant"
                    current_now = str(detail.get("categoria_atual", ""))
                    suggested_now = str(detail.get("categoria_sugerida", ""))
                    audit_progress_bar.progress(
                        min(progress_value, 1.0),
                        text=f"Auditando {processed_now}/{total_now} ({progress_value:.0%})",
                    )
                    audit_progress_status.info(
                        f"Atual: {merchant_now} | status: {status_now} | "
                        f"{current_now} -> {suggested_now or '-'}"
                    )
                    recent_audit_details.append(detail)
                    recent_df = pd.DataFrame(recent_audit_details[-8:])
                    cols = [
                        "id",
                        "status",
                        "categoria_atual",
                        "categoria_modelo",
                        "categoria_sugerida",
                        "confianca",
                        "metodo",
                        "motivo",
                    ]
                    cols = [col for col in cols if col in recent_df.columns]
                    if cols:
                        audit_progress_table.dataframe(
                            recent_df[cols],
                            width="stretch",
                            height=260,
                        )

                with st.spinner("Executando DSPy nas transações selecionadas..."):
                    processed, saved, details = run_dspy_audit_batch(
                        df,
                        limit=int(audit_limit),
                        progress_callback=update_audit_progress,
                    )
                    log_path = save_agent_run_log(details, prefix="agent_audit")
                audit_progress_bar.progress(1.0, text="Autoauditoria finalizada")
                st.success(
                    f"Transações analisadas: {processed}. Sugestões gravadas: {saved}."
                )
                show_agent_run_result(details, log_path=log_path)

    with st.expander("Agente DSPy de realocação automática", expanded=False):
        st.caption(
            "Este agente altera a categoria no banco. Use primeiro com confiança alta "
            "e limite pequeno para validar o comportamento nos seus dados."
        )

        dspy_category_on = (
            bool(is_dspy_category_enabled and is_dspy_category_enabled())
            if is_dspy_category_enabled is not None
            else False
        )
        dspy_audit_on = (
            bool(is_dspy_audit_enabled and is_dspy_audit_enabled())
            if is_dspy_audit_enabled is not None
            else False
        )
        runtime_ollama = st.checkbox(
            "Usar Ollama local nesta execução",
            value=not (dspy_category_on or dspy_audit_on),
            key="agent_runtime_ollama",
        )
        runtime_ollama_model = st.text_input(
            "Modelo Ollama",
            value=os.getenv("OLLAMA_RESEARCH_MODEL", "qwen2.5:14b"),
            key="agent_runtime_ollama_model",
            disabled=not runtime_ollama,
        )
        ollama_on = configure_ollama_runtime(runtime_ollama, runtime_ollama_model) or (
            bool(is_ollama_category_enabled and is_ollama_category_enabled())
            if is_ollama_category_enabled is not None
            else False
        )

        c_agent1, c_agent2, c_agent3 = st.columns(3)
        agent_limit = c_agent1.number_input(
            "Máximo de transações",
            min_value=1,
            max_value=5000,
            value=100,
            step=50,
            key="dspy_agent_limit",
        )
        agent_confidence = c_agent2.selectbox(
            "Confiança mínima",
            ["high", "medium", "low"],
            index=0,
            key="dspy_agent_confidence",
        )
        agent_scope = c_agent3.selectbox(
            "Escopo",
            ["Todas as categorias de débito", "Apenas categoria Outros"],
            index=0,
            key="dspy_agent_scope",
        )

        c_agent4, c_agent5 = st.columns(2)
        create_missing = c_agent4.checkbox(
            "Criar categorias novas sugeridas",
            value=False,
            key="dspy_agent_create_missing",
        )
        learn_agent_rules = c_agent5.checkbox(
            "Aprender regras das realocações",
            value=False,
            key="dspy_agent_learn_rules",
        )

        confirm_agent = st.checkbox(
            "Confirmo que quero permitir realocação automática de categorias",
            value=False,
            key="dspy_agent_confirm",
        )

        if st.button("Executar agente e realocar categorias", key="run_dspy_reallocation_agent"):
            if not confirm_agent:
                st.warning("Marque a confirmação antes de executar a realocação automática.")
            elif not dspy_category_on and not dspy_audit_on and not ollama_on:
                st.warning(
                    "Ative ENABLE_DSPY_CATEGORY=1/ENABLE_DSPY_AUDIT=1 com OPENAI_API_KEY "
                    "ou ENABLE_OLLAMA_CATEGORY=1 com Ollama antes de executar."
                )
            else:
                progress_bar = st.progress(0, text="Preparando agente...")
                progress_status = st.empty()
                progress_table = st.empty()
                recent_details: list[dict[str, object]] = []

                def update_agent_progress(
                    processed_now: int,
                    total_now: int,
                    detail: dict[str, object],
                ) -> None:
                    progress_value = processed_now / total_now if total_now else 1.0
                    status = str(detail.get("status", ""))
                    merchant = str(detail.get("merchant", "")) or "Sem merchant"
                    current = str(detail.get("categoria_atual", ""))
                    suggested = str(detail.get("categoria_sugerida", ""))
                    progress_bar.progress(
                        min(progress_value, 1.0),
                        text=f"Analisando {processed_now}/{total_now} ({progress_value:.0%})",
                    )
                    progress_status.info(
                        f"Atual: {merchant} | status: {status} | "
                        f"{current} -> {suggested or '-'}"
                    )
                    recent_details.append(detail)
                    recent_df = pd.DataFrame(recent_details[-8:])
                    cols = [
                        "id",
                        "status",
                        "categoria_atual",
                        "categoria_modelo",
                        "categoria_sugerida",
                        "confianca",
                        "metodo",
                        "motivo",
                    ]
                    cols = [col for col in cols if col in recent_df.columns]
                    if cols:
                        progress_table.dataframe(recent_df[cols], width="stretch", height=260)

                with st.spinner("Agente DSPy analisando e realocando categorias..."):
                    processed, changed, skipped, details = run_dspy_reallocation_agent(
                        df,
                        limit=int(agent_limit),
                        minimum_confidence=str(agent_confidence),
                        scope=str(agent_scope),
                        create_missing_categories=bool(create_missing),
                        learn_rules=bool(learn_agent_rules),
                        progress_callback=update_agent_progress,
                    )
                    log_path = save_agent_run_log(details)
                progress_bar.progress(1.0, text="Agente finalizado")
                st.success(
                    "Agente finalizado. "
                    f"Analisadas: {processed}. Realocadas: {changed}. Ignoradas: {skipped}."
                )
                show_agent_run_result(details, log_path=log_path)

    pending_df = free_text_filter(
        pending_df,
        "audit_search",
        "Pesquisar na auditoria",
    )

    if pending_df.empty:
        st.success("Nenhuma suspeita pendente.")
        return

    category_options = load_categories()
    if "Outros" not in category_options:
        category_options = ["Outros"] + category_options

    editable_columns = [
        "id",
        "confirmar_ok",
        "date_display",
        "bank",
        "description",
        "merchant",
        "debit",
        "credit",
        "balance",
        "category",
        "audit_reasons",
        "parse_status",
        "suggested_category",
        "source_file",
        "external_transaction_id",
    ]

    # confirmar_ok does not exist in the DB; create it only for the editor.
    real_columns = [col for col in editable_columns if col != "confirmar_ok" and col in pending_df.columns]
    working_df = pending_df[real_columns].copy()
    working_df.insert(1, "confirmar_ok", False)

    edited_df = show_data_editor(
        working_df,
        key="audit_editor",
        hide_index=True,
        disabled=[
            col for col in working_df.columns
            if col not in {"confirmar_ok", "category"}
        ],
        column_config={
            "confirmar_ok": st.column_config.CheckboxColumn(
                "confirmar_ok",
                help="Marque para confirmar que esta transação está correta.",
                default=False,
            ),
            "category": st.column_config.SelectboxColumn(
                "Categoria",
                options=category_options,
                required=True,
            ),
        },
    )

    c1, c2, c3 = st.columns(3)

    apply_to_similar = c1.checkbox(
        "Aplicar correção a semelhantes",
        value=False,
        key="audit_apply_similar",
    )

    match_field = c2.radio(
        "Critério",
        ["merchant", "description"],
        horizontal=True,
        disabled=not apply_to_similar,
        key="audit_match_field",
    )

    only_suspect = c3.checkbox(
        "Somente suspeitas/Outros",
        value=True,
        key="audit_only_suspect",
    )

    original_categories = (
        working_df.set_index("id")["category"]
        .astype(str)
        .to_dict()
    )

    edited_df["category_changed"] = edited_df.apply(
        lambda row: str(row["category"]) != original_categories.get(row["id"], ""),
        axis=1,
    )

    edited_df["confirmar_ok"] = edited_df["confirmar_ok"].fillna(False).astype(bool)

    rows_to_save = edited_df[
        edited_df["confirmar_ok"] | edited_df["category_changed"]
    ].copy()

    st.caption(
        f"Linhas exibidas: {len(edited_df)} | "
        f"Linhas marcadas/alteradas para salvar: {len(rows_to_save)}"
    )

    if st.button("Salvar auditoria", key="save_audit"):
        if rows_to_save.empty:
            st.warning("Nenhuma linha foi marcada ou alterada.")
        else:
            affected_rows = 0

            for _, row in rows_to_save.iterrows():
                transaction_id = int(row["id"])
                category = str(row["category"])

                if bool(row.get("category_changed", False)):
                    update_rows = pd.DataFrame([{
                        "id": transaction_id,
                        "category": category,
                        "merchant": row.get("merchant", ""),
                        "description": row.get("description", ""),
                    }])

                    affected_rows += update_transaction_fields(
                        update_rows,
                        apply_to_similar=apply_to_similar,
                        match_field=match_field,
                        only_suspect=only_suspect,
                        learn_rules=True,
                    )

                    affected_rows += mark_transaction_reviewed(
                        transaction_id,
                        review_note="Categoria corrigida manualmente na auditoria",
                    )

                elif bool(row.get("confirmar_ok", False)):
                    affected_rows += mark_transaction_reviewed(
                        transaction_id,
                        review_note="Confirmado manualmente na auditoria",
                    )

            st.success(f"Registos atualizados: {affected_rows}")
            st.rerun()

    download_csv_button(pending_df, "auditoria.csv", "Baixar auditoria CSV")



def show_review_center(df: pd.DataFrame) -> None:
    """Allow category/merchant/description review for any transaction."""
    st.header("Revisão livre de transações")
    st.caption(
        "Use esta aba para corrigir manualmente descrição, merchant e categoria "
        "de qualquer transação. O sistema pode aprender regras a partir da categoria confirmada."
    )

    category_options = load_categories()
    if "Outros" not in category_options:
        category_options = ["Outros"] + category_options

    years = ["Todos"] + sorted(df["year"].unique().tolist())
    selected_year = st.selectbox("Ano", years, key="review_year")

    month_options = ["Todos"] + MONTH_NAMES
    selected_month = st.selectbox("Mês", month_options, key="review_month")

    category_filter_options = ["Todas"] + sorted(df["category"].dropna().unique().tolist())
    selected_category = st.selectbox("Categoria atual", category_filter_options, key="review_category")

    review_df = df.copy()
    if selected_year != "Todos":
        review_df = review_df[review_df["year"].eq(int(selected_year))]
    if selected_month != "Todos":
        review_df = review_df[review_df["month"].eq(MONTH_NAME_TO_NUMBER[selected_month])]
    if selected_category != "Todas":
        review_df = review_df[review_df["category"].eq(selected_category)]

    review_df = free_text_filter(review_df, "review_search", "Pesquisar por descrição, merchant ou categoria")

    visible_cols = [
        "id",
        "date_display",
        "bank",
        "description",
        "merchant",
        "debit",
        "credit",
        "category",
        "operation",
        "source_file",
        "external_transaction_id",
    ]
    visible_cols = [col for col in visible_cols if col in review_df.columns]

    editable = review_df[visible_cols].sort_values("id", ascending=False).copy()
    edited = show_data_editor(
        editable,
        key="review_center_editor",
        hide_index=True,
        disabled=[
            col for col in visible_cols
            if col not in {"description", "merchant", "category"}
        ],
        column_config={
            "description": st.column_config.TextColumn("Descrição"),
            "merchant": st.column_config.TextColumn("Merchant"),
            "category": st.column_config.SelectboxColumn(
                "Categoria",
                options=category_options,
                required=True,
                help="Pode escolher 'Outros' ou qualquer categoria existente.",
            ),
        },
    )

    original = editable.set_index("id")[["description", "merchant", "category"]].astype(str).to_dict("index")

    edited_for_save = edited.copy()
    edited_for_save["changed"] = edited_for_save.apply(
        lambda row: (
            str(row.get("description", "")) != original.get(row["id"], {}).get("description", "")
            or str(row.get("merchant", "")) != original.get(row["id"], {}).get("merchant", "")
            or str(row.get("category", "")) != original.get(row["id"], {}).get("category", "")
        ),
        axis=1,
    )

    rows_to_save = edited_for_save[edited_for_save["changed"]].copy()

    c1, c2, c3 = st.columns(3)
    apply_to_similar = c1.checkbox(
        "Aplicar categoria a semelhantes",
        value=False,
        key="review_apply_similar",
    )
    match_field = c2.radio(
        "Critério de semelhança",
        ["merchant", "description"],
        horizontal=True,
        disabled=not apply_to_similar,
        key="review_match_field",
    )
    only_suspect = c3.checkbox(
        "Somente suspeitas/Outros",
        value=False,
        disabled=not apply_to_similar,
        key="review_only_suspect",
    )

    learn_rules = st.checkbox(
        "Aprender regra a partir da categoria confirmada",
        value=True,
        key="review_learn_rules",
    )

    st.caption(
        f"Linhas exibidas: {len(edited)} | Linhas alteradas para salvar: {len(rows_to_save)}"
    )

    if st.button("Salvar alterações manuais", key="review_save"):
        if rows_to_save.empty:
            st.warning("Nenhuma informação foi alterada.")
        else:
            rows_to_update = rows_to_save.drop(columns=["changed"], errors="ignore")
            updated = update_transaction_fields(
                rows_to_update,
                apply_to_similar=apply_to_similar,
                match_field=match_field,
                only_suspect=only_suspect,
                learn_rules=learn_rules,
            )
            st.success(f"Registos afetados: {updated}")
            st.rerun()

    download_csv_button(review_df, "revisao_livre_transacoes.csv", "Baixar revisão filtrada CSV")



def save_suggested_categories(
    suggestions_df: pd.DataFrame,
    *,
    apply_to_similar: bool,
    match_field: str,
) -> int:
    """Apply category suggestions confirmed by the user."""
    if suggestions_df.empty:
        return 0

    updated = suggestions_df.copy()
    if "chosen_category" in updated.columns:
        updated["category"] = updated["chosen_category"]

    return update_transaction_fields(
        updated,
        apply_to_similar=apply_to_similar,
        match_field=match_field,
        only_suspect=True,
        learn_rules=True,
    )


def show_suggestions(df: pd.DataFrame) -> None:
    """Show pending category suggestions and allow manual confirmation."""
    st.header("Sugestões")
    st.caption(
        "Aqui aparecem transações em 'Outros' que receberam uma sugestão de categoria. "
        "Você pode confirmar, alterar ou deixar como 'Outros'."
    )

    category_options = load_categories()
    if "Outros" not in category_options:
        category_options = ["Outros"] + category_options

    suggestions_df = df[
        (df["category"].eq("Outros"))
        & (df["suggested_category"].fillna("").astype(str).str.len() > 0)
    ].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("Categoria Outros", int(df["category"].eq("Outros").sum()))
    c2.metric("Sugestões pendentes", len(suggestions_df))
    c3.metric(
        "Sugestões DSPy",
        int(
            suggestions_df["suggestion_method"].fillna("").astype(str).str.startswith("dspy").sum()
            if "suggestion_method" in suggestions_df.columns
            else 0
        ),
    )

    if suggestions_df.empty:
        st.info("Nenhuma sugestão pendente.")
        return

    suggestions_df = free_text_filter(
        suggestions_df,
        "suggestions_search",
        "Pesquisar sugestões",
    )

    suggestions_df["chosen_category"] = suggestions_df["suggested_category"].where(
        suggestions_df["suggested_category"].isin(category_options),
        "Outros",
    )

    display_cols = [
        "id",
        "date_display",
        "bank",
        "description",
        "merchant",
        "debit",
        "credit",
        "category",
        "suggested_category",
        "chosen_category",
        "suggestion_confidence",
        "suggestion_method",
        "suggestion_reason",
        "source_file",
        "external_transaction_id",
    ]
    display_cols = [col for col in display_cols if col in suggestions_df.columns]

    edited_df = show_data_editor(
        suggestions_df[display_cols].sort_values("id", ascending=False),
        key="suggestions_editor",
        hide_index=True,
        disabled=[col for col in display_cols if col != "chosen_category"],
        column_config={
            "chosen_category": st.column_config.SelectboxColumn(
                "Categoria a aplicar",
                options=category_options,
                required=True,
            )
        },
    )

    c1, c2 = st.columns(2)
    apply_to_similar = c1.checkbox(
        "Aplicar a transações semelhantes em Outros",
        value=True,
        key="suggestions_apply_similar",
    )
    match_field = c2.radio(
        "Critério de semelhança",
        ["merchant", "description"],
        horizontal=True,
        key="suggestions_match_field",
    )

    if st.button("Salvar sugestões confirmadas", key="save_suggestions"):
        updated = save_suggested_categories(
            edited_df,
            apply_to_similar=apply_to_similar,
            match_field=match_field,
        )
        st.success(f"Registos afetados: {updated}")
        st.rerun()

    download_csv_button(suggestions_df, "sugestoes.csv", "Baixar sugestões CSV")


def show_category_editor(df: pd.DataFrame) -> None:
    """General transaction category editor."""
    st.header("Editar categorias")

    category_options = load_categories()
    filtered = free_text_filter(df, "edit_search", "Pesquisar por descrição/merchant/categoria")

    visible_cols = [
        "id",
        "date_display",
        "bank",
        "description",
        "merchant",
        "debit",
        "credit",
        "category",
        "operation",
        "transaction_type",
        "source_file",
    ]
    visible_cols = [col for col in visible_cols if col in filtered.columns]

    edited = show_data_editor(
        filtered.sort_values("id", ascending=False)[visible_cols],
        key="general_editor",
        hide_index=True,
        disabled=[col for col in visible_cols if col != "category"],
        column_config={
            "category": st.column_config.SelectboxColumn(
                "Categoria",
                options=category_options,
                required=True,
            )
        },
    )

    if st.button("Salvar categorias"):
        updated = update_transaction_categories(edited)
        st.success(f"Registos afetados: {updated}")
        st.rerun()

    download_csv_button(filtered, "transacoes_filtradas.csv", "Baixar CSV filtrado")


def show_category_manager() -> None:
    """Manage categories."""
    st.header("Gerir categorias")

    categories = load_categories()

    if add_category is None or rename_category is None or delete_category is None:
        st.warning(
            "Funções de gestão de categorias não disponíveis nesta versão do db.py."
        )
        show_dataframe(pd.DataFrame({"category": categories}))
        return

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("Adicionar")
        new_category = st.text_input("Nova categoria")
        if st.button("Adicionar categoria"):
            if add_category(new_category):
                st.success("Categoria adicionada.")
                st.rerun()
            st.warning("Categoria vazia ou já existente.")

    with col2:
        st.subheader("Renomear")
        old_name = st.selectbox("Categoria atual", categories)
        new_name = st.text_input("Novo nome", value=old_name)
        update_existing = st.checkbox("Atualizar transações e regras existentes", value=True)
        if st.button("Renomear categoria"):
            if rename_category(old_name, new_name, update_transactions=update_existing):
                st.success("Categoria renomeada.")
                st.rerun()
            st.warning("Não foi possível renomear.")

    with col3:
        st.subheader("Excluir")
        deletable = [cat for cat in categories if cat not in {"Outros", "Crédito"}]
        if deletable:
            category_to_delete = st.selectbox("Categoria para excluir", deletable)
            replacement_options = [cat for cat in categories if cat != category_to_delete]
            replacement = st.selectbox(
                "Mover transações para",
                replacement_options,
                index=replacement_options.index("Outros") if "Outros" in replacement_options else 0,
            )
            confirm = st.checkbox("Confirmo a exclusão")
            if st.button("Excluir categoria"):
                if not confirm:
                    st.warning("Confirme antes de excluir.")
                elif delete_category(category_to_delete, replacement=replacement):
                    st.success("Categoria excluída.")
                    st.rerun()
                else:
                    st.warning("Não foi possível excluir.")

    st.subheader("Categorias atuais")
    show_dataframe(pd.DataFrame({"category": categories}))


def show_merchants(df: pd.DataFrame) -> None:
    """Show imported merchants and learned rules."""
    st.header("Merchants")

    st.subheader("Merchants importados")
    merchants = (
        df.groupby(["merchant", "category", "bank"])["debit"]
        .agg(total="sum", quantidade="count")
        .reset_index()
        .sort_values("total", ascending=False)
    )
    merchants = free_text_filter(merchants, "merchant_search", "Pesquisar merchants")
    merchants_display = merchants.copy()
    merchants_display["total"] = merchants_display["total"].map(euro)
    show_dataframe(merchants_display)
    download_csv_button(merchants, "merchants.csv", "Baixar merchants CSV")

    st.subheader("Regras aprendidas em JSON")
    st.caption(str(MERCHANT_RULES_PATH))
    rules = load_json_rules()
    if rules:
        rules_df = pd.DataFrame(rules)
        rules_df = free_text_filter(rules_df, "rules_search", "Pesquisar regras")
        show_dataframe(rules_df)
        download_csv_button(rules_df, "merchant_rules.csv", "Baixar regras CSV")
    else:
        st.info("Nenhuma regra JSON encontrada.")


def show_balancete(df: pd.DataFrame) -> None:
    """Show a financial trial-balance style view for the whole period or filters."""
    st.header("Balancete")
    st.caption(
        "Visão consolidada de créditos, débitos, saldo carregado, saldo final, "
        "custo médio mensal e maiores saídas financeiras."
    )

    years = sorted(df["year"].unique().tolist())
    year_options = ["Todos"] + years
    selected_year = st.selectbox("Ano", year_options, key="balancete_year")

    month_options = ["Todos"] + MONTH_NAMES
    selected_month = st.selectbox("Mês", month_options, key="balancete_month")

    category_options = ["Todas"] + sorted(df["category"].dropna().unique().tolist())
    selected_category = st.selectbox("Categoria", category_options, key="balancete_category")

    bank_options = ["Todos"] + sorted(df["bank"].dropna().unique().tolist())
    selected_bank = st.selectbox("Banco/Formato", bank_options, key="balancete_bank")

    bal_df = df.copy()

    if selected_year != "Todos":
        bal_df = bal_df[bal_df["year"].eq(int(selected_year))]

    if selected_month != "Todos":
        bal_df = bal_df[bal_df["month"].eq(MONTH_NAME_TO_NUMBER[selected_month])]

    if selected_category != "Todas":
        bal_df = bal_df[bal_df["category"].eq(selected_category)]

    if selected_bank != "Todos":
        bal_df = bal_df[bal_df["bank"].eq(selected_bank)]

    bal_df = free_text_filter(bal_df, "balancete_search", "Pesquisa livre no balancete")

    total_debit = float(bal_df["debit"].sum())
    total_credit = float(bal_df["credit"].sum())
    cashflow = total_credit - total_debit
    balances = estimate_period_balances(bal_df)

    months_with_debits = bal_df[bal_df["debit"] > 0]["date"].dt.to_period("M").nunique()
    monthly_living_cost = (
        bal_df[bal_df["debit"] > 0]
        .groupby(bal_df[bal_df["debit"] > 0]["date"].dt.to_period("M"))["debit"]
        .sum()
        .mean()
        if months_with_debits > 0
        else 0.0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo inicial estimado", euro(balances["opening_balance"]))
    c2.metric("Saldo final estimado", euro(balances["closing_balance"]))
    c3.metric("Variação estimada do saldo", euro(balances["balance_change"]))
    c4.metric("Fluxo líquido", euro(cashflow))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total de créditos", euro(total_credit))
    c2.metric("Total de débitos", euro(total_debit))
    c3.metric("Média mensal do custo de vida", euro(monthly_living_cost))
    c4.metric("Transações", len(bal_df))

    if bal_df.empty:
        st.info("Sem dados para os filtros escolhidos.")
        return

    st.subheader("Balancete mensal")
    monthly = (
        bal_df.groupby(["year", "month"], dropna=False)
        .agg(
            creditos=("credit", "sum"),
            debitos=("debit", "sum"),
            fluxo_liquido=("amount", "sum"),
            transacoes=("id", "count"),
        )
        .reset_index()
        .sort_values(["year", "month"])
    )
    monthly["mês"] = monthly["month"].apply(lambda value: MONTH_NAMES[int(value) - 1])
    monthly_display = monthly[["year", "mês", "creditos", "debitos", "fluxo_liquido", "transacoes"]].copy()
    for col in ["creditos", "debitos", "fluxo_liquido"]:
        monthly_display[col] = monthly_display[col].map(euro)
    show_dataframe(monthly_display)
    download_csv_button(monthly, "balancete_mensal.csv", "Baixar balancete mensal CSV")

    st.subheader("Maiores custos por categoria")
    top_categories = (
        bal_df[bal_df["debit"] > 0]
        .groupby("category", dropna=False)
        .agg(total=("debit", "sum"), transacoes=("id", "count"))
        .reset_index()
        .sort_values("total", ascending=False)
    )
    top_categories["percentual"] = (
        top_categories["total"] / top_categories["total"].sum() * 100
        if not top_categories.empty and top_categories["total"].sum() > 0
        else 0
    )
    top_categories_display = top_categories.copy()
    top_categories_display["total"] = top_categories_display["total"].map(euro)
    top_categories_display["percentual"] = top_categories_display["percentual"].map(lambda value: f"{value:.1f}%")
    show_dataframe(top_categories_display)
    download_csv_button(top_categories, "balancete_categorias.csv", "Baixar custos por categoria CSV")

    st.subheader("Maiores transações de débito")
    largest_debits = (
        bal_df[bal_df["debit"] > 0]
        .sort_values("debit", ascending=False)
        .head(50)
    )
    visible_cols = [col for col in VISIBLE_TRANSACTION_COLUMNS if col in largest_debits.columns]
    show_dataframe(largest_debits[visible_cols])
    download_csv_button(largest_debits, "balancete_maiores_debitos.csv", "Baixar maiores débitos CSV")

    st.subheader("Créditos recebidos")
    credits = (
        bal_df[bal_df["credit"] > 0]
        .sort_values("credit", ascending=False)
    )
    if credits.empty:
        st.info("Nenhum crédito encontrado para os filtros escolhidos.")
    else:
        credit_cols = [col for col in VISIBLE_TRANSACTION_COLUMNS if col in credits.columns]
        show_dataframe(credits[credit_cols])
        download_csv_button(credits, "balancete_creditos.csv", "Baixar créditos CSV")

    st.subheader("Todas as transações do balancete")
    all_cols = [col for col in VISIBLE_TRANSACTION_COLUMNS if col in bal_df.columns]
    show_dataframe(bal_df.sort_values("date", ascending=False)[all_cols])
    download_csv_button(bal_df, "balancete_transacoes.csv", "Baixar transações do balancete CSV")



def show_reports(df: pd.DataFrame) -> None:
    """Generate exportable reports by year, month, category and bank."""
    st.header("Relatórios")
    st.caption(
        "Gere relatórios filtrados por ano, mês, categoria, banco e texto livre. "
        "As tabelas podem ser exportadas em CSV."
    )

    years = sorted(df["year"].unique().tolist())
    selected_years = st.multiselect(
        "Ano",
        years,
        default=[years[-1]] if years else [],
        key="report_years",
    )

    month_options = ["Todos"] + MONTH_NAMES
    selected_month = st.selectbox("Mês", month_options, key="report_month")

    categories = ["Todas"] + sorted(df["category"].dropna().unique().tolist())
    selected_category = st.selectbox("Categoria", categories, key="report_category")

    banks = ["Todos"] + sorted(df["bank"].dropna().unique().tolist())
    selected_bank = st.selectbox("Banco/Formato", banks, key="report_bank")

    report_df = df.copy()

    if selected_years:
        report_df = report_df[report_df["year"].isin(selected_years)]

    if selected_month != "Todos":
        report_df = report_df[report_df["month"].eq(MONTH_NAME_TO_NUMBER[selected_month])]

    if selected_category != "Todas":
        report_df = report_df[report_df["category"].eq(selected_category)]

    if selected_bank != "Todos":
        report_df = report_df[report_df["bank"].eq(selected_bank)]

    report_df = free_text_filter(report_df, "report_search", "Pesquisa livre no relatório")

    st.subheader("Resumo do relatório")
    debit_total = report_df["debit"].sum()
    credit_total = report_df["credit"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Débitos", euro(debit_total))
    c2.metric("Créditos", euro(credit_total))
    c3.metric("Saldo líquido", euro(credit_total - debit_total))
    c4.metric("Transações", len(report_df))

    st.subheader("Resumo por mês")
    by_month = (
        report_df.groupby(["year", "month"], dropna=False)
        .agg(
            debitos=("debit", "sum"),
            creditos=("credit", "sum"),
            transacoes=("id", "count"),
        )
        .reset_index()
        .sort_values(["year", "month"])
    )
    if not by_month.empty:
        by_month["mês"] = by_month["month"].apply(lambda value: MONTH_NAMES[int(value) - 1])
        by_month_display = by_month[["year", "mês", "debitos", "creditos", "transacoes"]].copy()
        by_month_display["debitos"] = by_month_display["debitos"].map(euro)
        by_month_display["creditos"] = by_month_display["creditos"].map(euro)
        show_dataframe(by_month_display)
        download_csv_button(by_month, "relatorio_por_mes.csv", "Baixar resumo por mês CSV")
    else:
        st.info("Sem dados para o resumo por mês.")

    st.subheader("Resumo por categoria")
    by_category = (
        report_df.groupby("category", dropna=False)
        .agg(
            debitos=("debit", "sum"),
            creditos=("credit", "sum"),
            transacoes=("id", "count"),
        )
        .reset_index()
        .sort_values("debitos", ascending=False)
    )
    if not by_category.empty:
        by_category_display = by_category.copy()
        by_category_display["debitos"] = by_category_display["debitos"].map(euro)
        by_category_display["creditos"] = by_category_display["creditos"].map(euro)
        show_dataframe(by_category_display)
        download_csv_button(by_category, "relatorio_por_categoria.csv", "Baixar resumo por categoria CSV")
    else:
        st.info("Sem dados para o resumo por categoria.")

    st.subheader("Resumo por merchant")
    by_merchant = (
        report_df.groupby(["merchant", "category", "bank"], dropna=False)
        .agg(
            debitos=("debit", "sum"),
            creditos=("credit", "sum"),
            transacoes=("id", "count"),
        )
        .reset_index()
        .sort_values("debitos", ascending=False)
    )
    if not by_merchant.empty:
        by_merchant_display = by_merchant.copy()
        by_merchant_display["debitos"] = by_merchant_display["debitos"].map(euro)
        by_merchant_display["creditos"] = by_merchant_display["creditos"].map(euro)
        show_dataframe(by_merchant_display)
        download_csv_button(by_merchant, "relatorio_por_merchant.csv", "Baixar resumo por merchant CSV")
    else:
        st.info("Sem dados para o resumo por merchant.")

    st.subheader("Transações do relatório")
    visible_cols = [col for col in VISIBLE_TRANSACTION_COLUMNS if col in report_df.columns]
    report_display = report_df.sort_values("date", ascending=False)
    show_dataframe(report_display[visible_cols])
    download_csv_button(report_df, "relatorio_transacoes.csv", "Baixar transações CSV")


def show_raw_data(df: pd.DataFrame) -> None:
    """Show raw data with export."""
    st.header("Dados brutos")
    filtered = free_text_filter(df, "raw_search")
    show_dataframe(filtered.sort_values("date", ascending=False))
    download_csv_button(filtered, "dados_brutos_filtrados.csv", "Baixar dados brutos CSV")


def main() -> None:
    """Application entry point."""
    st.set_page_config(page_title="Financial Dashboard", layout="wide")
    st.title("Financial Dashboard")

    if not DB_PATH.exists():
        st.error("Banco de dados não encontrado. Rode primeiro: python main.py")
        return

    df = load_transactions()
    if df.empty:
        st.warning("Nenhuma transação encontrada.")
        return

    df = prepare_data(df)
    if df.empty:
        st.warning("Nenhum dado válido encontrado após preparar datas.")
        return

    dashboard_mode = st.sidebar.radio(
        "Modo de visualização",
        ["Painel executivo", "Dashboard atual"],
        index=0,
        key="dashboard_mode",
    )

    if dashboard_mode == "Painel executivo":
        show_powerbi_dashboard(df)
        return

    tabs = st.tabs(
        [
            "Resumo executivo",
            "Balancete",
            "Gráficos",
            "Auditoria",
            "Revisão livre",
            "Sugestões",
            "Lançamentos",
            "Editar categorias",
            "Gerir categorias",
            "Merchants",
            "Relatórios",
            "Dados brutos",
        ]
    )

    with tabs[0]:
        show_executive_summary(df)

    with tabs[1]:
        show_balancete(df)

    with tabs[2]:
        show_graphs(df)

    with tabs[3]:
        show_audit(df)

    with tabs[4]:
        show_review_center(df)

    with tabs[5]:
        show_suggestions(df)

    with tabs[6]:
        show_manual_entry_form()

    with tabs[7]:
        show_category_editor(df)

    with tabs[8]:
        show_category_manager()

    with tabs[9]:
        show_merchants(df)

    with tabs[10]:
        show_reports(df)

    with tabs[11]:
        show_raw_data(df)


if __name__ == "__main__":
    main()
