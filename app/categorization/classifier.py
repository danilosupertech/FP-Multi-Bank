"""Transaction categorization classifier.

Strategy:
1. Credit transactions are categorized as "Crédito".
2. Confirmed JSON rules have priority and survive database resets.
3. Learned database rules are used next.
4. Fixed rules are used as fallback.
5. Unknown transactions stay as "Outros" but can receive a suggestion.
6. Suggestions can come from DSPy or from a local numpy token-similarity model.
"""

from __future__ import annotations

from app.categorization.dspy_category import (
    CategorySuggestion,
    audit_category_with_dspy,
    suggest_category_with_dspy,
)
from app.categorization.local_suggester import suggest_category_locally
from app.categorization.rule_store import match_json_rule
from app.categorization.rules import FIXED_RULES
from app.database.db import get_categories, get_category_rules


def categorize_transaction(description: str, merchant: str, operation: str) -> str:
    """Categorize a transaction using deterministic rules."""
    result = categorize_transaction_with_details(description, merchant, operation)
    return result["category"]


def categorize_transaction_with_details(
    description: str,
    merchant: str,
    operation: str,
    amount: float = 0,
) -> dict[str, str]:
    """Categorize a transaction and return audit-friendly details."""
    if operation == "credit":
        return {
            "category": "Crédito",
            "category_method": "system_credit",
            "suggested_category": "",
            "suggestion_confidence": "",
            "suggestion_reason": "",
            "suggestion_method": "",
        }

    json_match = match_json_rule(description, merchant)
    if json_match is not None:
        return _with_dspy_audit(
            {
                "category": json_match.category,
                "category_method": "json_learned_rule",
                "suggested_category": "",
                "suggestion_confidence": "",
                "suggestion_reason": f"Regra JSON aprendida pelo termo: {json_match.keyword}",
                "suggestion_method": json_match.source,
            },
            description=description,
            merchant=merchant,
            amount=amount,
            operation=operation,
        )

    searchable_text = f"{description} {merchant}".lower()

    learned_rules = get_category_rules()
    for keyword, category in learned_rules.items():
        if keyword in searchable_text:
            return _with_dspy_audit(
                {
                    "category": category,
                    "category_method": "db_learned_rule",
                    "suggested_category": "",
                    "suggestion_confidence": "",
                    "suggestion_reason": f"Regra aprendida no banco pelo termo: {keyword}",
                    "suggestion_method": "db_learned_rule",
                },
                description=description,
                merchant=merchant,
                amount=amount,
                operation=operation,
            )

    for keyword, category in FIXED_RULES.items():
        if keyword in searchable_text:
            return _with_dspy_audit(
                {
                    "category": category,
                    "category_method": "fixed_rule",
                    "suggested_category": "",
                    "suggestion_confidence": "",
                    "suggestion_reason": f"Regra fixa pelo termo: {keyword}",
                    "suggestion_method": "fixed_rule",
                },
                description=description,
                merchant=merchant,
                amount=amount,
                operation=operation,
            )

    suggestion = _suggest_for_unknown(
        description=description,
        merchant=merchant,
        amount=amount,
        operation=operation,
    )

    return {
        "category": "Outros",
        "category_method": "unclassified",
        "suggested_category": suggestion.category if suggestion else "",
        "suggestion_confidence": suggestion.confidence if suggestion else "",
        "suggestion_reason": suggestion.reason if suggestion else "Sem regra determinística encontrada.",
        "suggestion_method": suggestion.method if suggestion else "none",
    }


def _with_dspy_audit(
    result: dict[str, str],
    *,
    description: str,
    merchant: str,
    amount: float,
    operation: str,
) -> dict[str, str]:
    """Attach a DSPy audit suggestion without changing the chosen category."""
    current_category = result.get("category", "")
    if not current_category or current_category in {"Outros", "Crédito"}:
        return result

    existing_categories = get_categories()
    audit_suggestion = audit_category_with_dspy(
        description=description,
        merchant=merchant,
        amount=amount,
        operation=operation,
        current_category=current_category,
        category_method=result.get("category_method", ""),
        existing_categories=existing_categories,
    )
    if audit_suggestion is None:
        return result

    updated = dict(result)
    updated["suggested_category"] = audit_suggestion.category
    updated["suggestion_confidence"] = audit_suggestion.confidence
    updated["suggestion_reason"] = (
        f"Autoauditoria DSPy: categoria atual '{current_category}'. "
        f"{audit_suggestion.reason}"
    )
    updated["suggestion_method"] = audit_suggestion.method
    return updated


def _suggest_for_unknown(
    *,
    description: str,
    merchant: str,
    amount: float,
    operation: str,
) -> CategorySuggestion | None:
    """Suggest a category when deterministic classification fails.

    DSPy is attempted first when enabled. If DSPy is not configured or fails,
    the local numpy token-similarity suggester still tries to produce a
    reviewable suggestion. Suggestions are never auto-applied.
    """
    existing_categories = get_categories()

    dspy_suggestion = suggest_category_with_dspy(
        description=description,
        merchant=merchant,
        amount=amount,
        operation=operation,
        existing_categories=existing_categories,
    )
    if dspy_suggestion is not None:
        return dspy_suggestion

    local_suggestion = suggest_category_locally(
        description=description,
        merchant=merchant,
        amount=amount,
        existing_categories=existing_categories,
    )
    if local_suggestion is None:
        return None

    return CategorySuggestion(
        category=local_suggestion.category,
        confidence=local_suggestion.confidence,
        reason=local_suggestion.reason,
        method=local_suggestion.method,
    )
