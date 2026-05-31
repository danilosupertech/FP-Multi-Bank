"""Optional DSPy-assisted transaction category suggestions.

This module is intentionally optional. The importer can run without DSPy or an
LLM API key. When ``ENABLE_DSPY_CATEGORY=1`` is set, unknown transactions are
sent to DSPy to suggest an existing category or a new one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.categorization.web_research import research_merchant


@dataclass(frozen=True)
class CategorySuggestion:
    """Category suggestion produced by DSPy or a local heuristic."""

    category: str
    confidence: str
    reason: str
    method: str


def is_dspy_category_enabled() -> bool:
    """Return True when category suggestion with DSPy is enabled."""
    flag = os.getenv("ENABLE_DSPY_CATEGORY", os.getenv("USE_DSPY", "0"))
    return flag.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_dspy_audit_enabled() -> bool:
    """Return True when DSPy should audit deterministic classifications."""
    flag = os.getenv("ENABLE_DSPY_AUDIT", "0")
    return flag.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _configure_dspy() -> Any:
    """Import and configure DSPy only when required."""
    try:
        import dspy  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "DSPy não está instalado. Execute: pip install -r requirements.txt"
        ) from exc

    model = os.getenv("DSPY_MODEL", "openai/gpt-4o-mini")
    api_key = os.getenv("OPENAI_API_KEY")

    if api_key:
        lm = dspy.LM(model, api_key=api_key, temperature=0)
    else:
        lm = dspy.LM(model, temperature=0)

    dspy.configure(lm=lm)
    return dspy


def suggest_category_with_dspy(
    *,
    description: str,
    merchant: str,
    amount: float,
    operation: str,
    existing_categories: list[str],
) -> CategorySuggestion | None:
    """Suggest a category for transactions not classified by fixed rules.

    Returns None when DSPy is disabled, unavailable, or gives an unsafe answer.
    """
    if not is_dspy_category_enabled():
        return None

    try:
        dspy = _configure_dspy()
    except Exception:
        return None
    research = research_merchant(description=description, merchant=merchant)

    class SuggestTransactionCategory(dspy.Signature):  # type: ignore[misc]
        """Classify a bank transaction into the best category.

        Rules:
        - Prefer one of the existing categories when possible.
        - If none fits, propose a short new category name.
        - Do not invent facts beyond the transaction description.
        - Use web evidence only when provided; ignore it if it is weak or unrelated.
        - Use confidence: high, medium, or low.
        - Return a brief reason in Portuguese.
        """

        description: str = dspy.InputField()
        merchant: str = dspy.InputField()
        amount: str = dspy.InputField()
        operation: str = dspy.InputField()
        existing_categories: str = dspy.InputField()
        web_evidence: str = dspy.InputField()
        suggested_category: str = dspy.OutputField()
        confidence: str = dspy.OutputField(desc="high, medium, or low")
        reason: str = dspy.OutputField(desc="short reason in Portuguese")

    predictor = dspy.Predict(SuggestTransactionCategory)

    try:
        result = predictor(
            description=description,
            merchant=merchant,
            amount=f"{amount:.2f}",
            operation=operation,
            existing_categories=", ".join(existing_categories),
            web_evidence=research.summary if research else "",
        )
        category = str(result.suggested_category).strip()
        confidence = str(result.confidence).strip().lower()
        reason = str(result.reason).strip()
    except Exception:
        return None

    if not category:
        return None

    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    method = "dspy_web_category" if research else "dspy_category"
    if research:
        reason = f"{reason} Pesquisa: {research.summary[:220]}"

    return CategorySuggestion(
        category=category,
        confidence=confidence,
        reason=reason,
        method=method,
    )


def audit_category_with_dspy(
    *,
    description: str,
    merchant: str,
    amount: float,
    operation: str,
    current_category: str,
    category_method: str,
    existing_categories: list[str],
) -> CategorySuggestion | None:
    """Use DSPy to self-audit a deterministic classification.

    The audit never changes the applied category. It only returns a reviewable
    suggestion when DSPy finds a better category with enough confidence.
    """
    if not is_dspy_audit_enabled():
        return None

    try:
        dspy = _configure_dspy()
    except Exception:
        return None
    research = research_merchant(description=description, merchant=merchant)

    class AuditTransactionCategory(dspy.Signature):  # type: ignore[misc]
        """Audit a bank transaction category.

        Rules:
        - Decide if current_category is adequate.
        - Prefer existing categories; suggest a new category only if clearly needed.
        - Do not change credits, transfers, or taxes without strong evidence.
        - Use web evidence only when provided and clearly about the merchant.
        - Return action as keep, change, or review.
        - Use confidence: high, medium, or low.
        - Return a brief reason in Portuguese.
        """

        description: str = dspy.InputField()
        merchant: str = dspy.InputField()
        amount: str = dspy.InputField()
        operation: str = dspy.InputField()
        current_category: str = dspy.InputField()
        category_method: str = dspy.InputField()
        existing_categories: str = dspy.InputField()
        web_evidence: str = dspy.InputField()
        action: str = dspy.OutputField(desc="keep, change, or review")
        suggested_category: str = dspy.OutputField()
        confidence: str = dspy.OutputField(desc="high, medium, or low")
        reason: str = dspy.OutputField(desc="short reason in Portuguese")

    predictor = dspy.Predict(AuditTransactionCategory)

    try:
        result = predictor(
            description=description,
            merchant=merchant,
            amount=f"{amount:.2f}",
            operation=operation,
            current_category=current_category,
            category_method=category_method,
            existing_categories=", ".join(existing_categories),
            web_evidence=research.summary if research else "",
        )
        action = str(result.action).strip().lower()
        category = str(result.suggested_category).strip()
        confidence = str(result.confidence).strip().lower()
        reason = str(result.reason).strip()
    except Exception:
        return None

    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    if action == "keep" or not category or category == current_category:
        return None

    if confidence == "low" and action != "change":
        return None

    method = "dspy_web_audit" if research else "dspy_audit"
    if research:
        reason = f"{reason} Pesquisa: {research.summary[:220]}"

    return CategorySuggestion(
        category=category,
        confidence=confidence,
        reason=reason,
        method=method,
    )
