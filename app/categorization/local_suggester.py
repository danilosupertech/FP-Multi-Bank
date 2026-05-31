"""Local category suggestion using token similarity.

This is a no-API fallback. It uses numpy to score tokens from the transaction
against fixed rules, learned JSON rules and known category names. The result is
not automatically applied; it is shown in the dashboard for user confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from app.categorization.rule_store import load_json_rules, normalize_keyword
from app.categorization.rules import FIXED_RULES


@dataclass(frozen=True)
class LocalSuggestion:
    category: str
    confidence: str
    score: float
    reason: str
    method: str = "local_similarity"


DOMAIN_HINTS: dict[str, str] = {
    "repsol": "Transportes",
    "petroazul": "Transportes",
    "posto": "Transportes",
    "est servico": "Transportes",
    "combustivel": "Transportes",
    "gasolina": "Transportes",
    "latam": "Transportes",
    "norauto": "Transportes",
    "tabacaria": "Serviços",
    "padaria": "Alimentação",
    "confeitaria": "Restaurantes e Cafés",
    "restaurante": "Restaurantes e Cafés",
    "cafe": "Restaurantes e Cafés",
    "arcadia": "Restaurantes e Cafés",
    "brendan snyder": "Educação",
    "welcome music": "Educação",
    "escola": "Educação",
    "ludimusic": "Educação",
    "irn": "Documentos PT",
    "sef": "Documentos PT",
    "pag estado": "Impostos e Taxas",
    "hipay": "Serviços",
    "ifthenpay": "Serviços",
    "eupago": "Serviços",
    "nuvei": "Serviços",
    "kiabi": "Vestuário",
    "decathlon": "Vestuário",
    "pepco": "Vestuário",
    "nyx": "Beleza",
    "wells": "Saúde",
    "farmacia": "Saúde",
    "lusiadas": "Saúde",
}


def _tokens(text: str) -> set[str]:
    normalized = normalize_keyword(text)
    return {token for token in normalized.split() if len(token) >= 3}


def _confidence(score: float) -> str:
    if score >= 0.82:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _jaccard_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    if union == 0:
        return 0.0
    return float(intersection / union)


def suggest_category_locally(
    *,
    description: str,
    merchant: str,
    amount: float,
    existing_categories: list[str],
) -> LocalSuggestion | None:
    """Suggest a category without calling an LLM."""
    searchable = normalize_keyword(f"{description} {merchant}")
    transaction_tokens = _tokens(searchable)
    if not transaction_tokens:
        return None

    candidates: list[tuple[str, str, str]] = []

    for hint, category in DOMAIN_HINTS.items():
        candidates.append((hint, category, "pista de domínio"))

    for keyword, category in FIXED_RULES.items():
        candidates.append((keyword, category, "regra fixa semelhante"))

    for rule in load_json_rules():
        keyword = str(rule.get("keyword", ""))
        category = str(rule.get("category", ""))
        if keyword and category:
            candidates.append((keyword, category, "regra JSON aprendida semelhante"))

    for category in existing_categories:
        candidates.append((category, category, "nome da categoria semelhante"))

    scores: list[float] = []
    scored: list[tuple[str, str, str, float]] = []

    for keyword, category, source in candidates:
        keyword_norm = normalize_keyword(keyword)
        keyword_tokens = _tokens(keyword_norm)
        if not keyword_tokens:
            continue

        exact_bonus = 0.35 if keyword_norm and keyword_norm in searchable else 0.0
        token_score = _jaccard_score(transaction_tokens, keyword_tokens)
        containment_bonus = 0.25 if keyword_tokens <= transaction_tokens else 0.0
        score = min(1.0, token_score + exact_bonus + containment_bonus)
        scores.append(score)
        scored.append((keyword, category, source, score))

    if not scored:
        return None

    # numpy is used deliberately here so the scoring can later evolve to vectors.
    score_array = np.array(scores, dtype=float)
    best_index = int(np.argmax(score_array))
    keyword, category, source, best_score = scored[best_index]

    if best_score < 0.30 or category == "Outros":
        return None

    reason = (
        f"Sugestão por similaridade com '{keyword}' ({source}). "
        f"Score local: {best_score:.2f}. Confirme antes de aprender a regra."
    )
    return LocalSuggestion(
        category=category,
        confidence=_confidence(best_score),
        score=float(best_score),
        reason=reason,
    )
