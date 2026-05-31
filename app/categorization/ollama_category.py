"""Local Ollama-assisted category suggestions.

This module lets the reallocation agent run without an external LLM API. It is
used when ``ENABLE_OLLAMA_CATEGORY=1`` and calls ``ollama run`` directly.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from app.categorization.category_knowledge import (
    find_category_intelligence_match,
    format_category_intelligence_for_prompt,
)
from app.categorization.dspy_category import CategorySuggestion
from app.categorization.web_research import research_merchant


def is_ollama_category_enabled() -> bool:
    flag = os.getenv("ENABLE_OLLAMA_CATEGORY", "0")
    return flag.strip().lower() in {"1", "true", "yes", "on"}


def suggest_category_with_ollama(
    *,
    description: str,
    merchant: str,
    amount: float,
    operation: str,
    existing_categories: list[str],
) -> CategorySuggestion | None:
    """Suggest a category using a local Ollama model."""
    if not is_ollama_category_enabled():
        return None

    learned_match = find_category_intelligence_match(
        description=description,
        merchant=merchant,
        existing_categories=existing_categories,
    )
    if learned_match is not None:
        return CategorySuggestion(
            category=learned_match["category"],
            confidence=_normalize_confidence(learned_match["confidence"]),
            reason=learned_match["reason"],
            method=learned_match["method"],
        )

    research = research_merchant(description=description, merchant=merchant)
    prompt = _build_prompt(
        task="suggest",
        description=description,
        merchant=merchant,
        amount=amount,
        operation=operation,
        current_category="Outros",
        category_method="unclassified",
        existing_categories=existing_categories,
        web_evidence=research.summary if research else "",
    )
    data = _run_ollama_json(prompt)
    if not data:
        return None

    category = str(data.get("suggested_category", "")).strip()
    confidence = _normalize_confidence(data.get("confidence", "low"))
    reason = str(data.get("reason", "")).strip()
    if not category:
        category = "Outros"

    method = "ollama_web_category" if research else "ollama_category"
    return CategorySuggestion(
        category=category,
        confidence=confidence,
        reason=reason or "Sugestão local via Ollama.",
        method=method,
    )


def audit_category_with_ollama(
    *,
    description: str,
    merchant: str,
    amount: float,
    operation: str,
    current_category: str,
    category_method: str,
    existing_categories: list[str],
) -> CategorySuggestion | None:
    """Audit a current category using a local Ollama model."""
    if not is_ollama_category_enabled():
        return None

    learned_match = find_category_intelligence_match(
        description=description,
        merchant=merchant,
        existing_categories=existing_categories,
    )
    if learned_match is not None:
        category = learned_match["category"]
        if category == current_category:
            return CategorySuggestion(
                category=current_category,
                confidence=_normalize_confidence(learned_match["confidence"]),
                reason=learned_match["reason"],
                method="category_intelligence_keep",
            )
        return CategorySuggestion(
            category=category,
            confidence=_normalize_confidence(learned_match["confidence"]),
            reason=learned_match["reason"],
            method=learned_match["method"],
        )

    research = research_merchant(description=description, merchant=merchant)
    prompt = _build_prompt(
        task="audit",
        description=description,
        merchant=merchant,
        amount=amount,
        operation=operation,
        current_category=current_category,
        category_method=category_method,
        existing_categories=existing_categories,
        web_evidence=research.summary if research else "",
    )
    data = _run_ollama_json(prompt)
    if not data:
        return None

    action = str(data.get("action", "")).strip().lower()
    category = str(data.get("suggested_category", "")).strip()
    confidence = _normalize_confidence(data.get("confidence", "low"))
    reason = str(data.get("reason", "")).strip()
    if action == "keep" or not category:
        category = current_category
    if category == current_category:
        method = "ollama_web_keep" if research else "ollama_keep"
        return CategorySuggestion(
            category=current_category,
            confidence=confidence,
            reason=reason or "Categoria atual considerada adequada pelo Ollama.",
            method=method,
        )
    if confidence == "low" and action != "change":
        return None

    method = "ollama_web_audit" if research else "ollama_audit"
    return CategorySuggestion(
        category=category,
        confidence=confidence,
        reason=reason or "Auditoria local via Ollama.",
        method=method,
    )


def _build_prompt(
    *,
    task: str,
    description: str,
    merchant: str,
    amount: float,
    operation: str,
    current_category: str,
    category_method: str,
    existing_categories: list[str],
    web_evidence: str,
) -> str:
    return f"""
Voce e um classificador financeiro pessoal. Analise a transacao e responda
somente com JSON valido, sem markdown.

Tarefa: {task}
Descricao: {description}
Merchant: {merchant}
Valor: {amount:.2f}
Operacao: {operation}
Categoria atual: {current_category}
Metodo atual: {category_method}
Categorias existentes: {", ".join(existing_categories)}
Contexto auxiliar: {web_evidence}
Licoes aprendidas:
{format_category_intelligence_for_prompt()}

Regras:
- A categoria sugerida deve ser exatamente uma das categorias existentes sempre que possivel.
- Copie o nome da categoria exatamente como aparece em "Categorias existentes".
- Nao use nomes genericos como "Restaurante" se existir "Restaurantes e Cafés".
- Nao use "Mercado" se existir "Supermercado".
- Nao altere para uma categoria nova se uma existente for suficiente.
- Se a categoria atual estiver adequada, use action "keep".
- Use confidence "high", "medium" ou "low".
- Para creditos, a categoria deve ser "Credito".
- Responda em JSON com as chaves:
  action, suggested_category, confidence, reason
""".strip()


def _run_ollama_json(prompt: str) -> dict[str, Any] | None:
    model = os.getenv("OLLAMA_RESEARCH_MODEL", "qwen2.5:14b").strip() or "qwen2.5:14b"
    timeout = int(os.getenv("OLLAMA_CATEGORY_TIMEOUT", "120"))
    try:
        completed = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return None

    if completed.returncode != 0:
        return None

    text = completed.stdout.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_confidence(value: Any) -> str:
    confidence = str(value).strip().lower()
    if confidence in {"alta", "alto", "high"}:
        return "high"
    if confidence in {"media", "média", "medio", "médio", "medium"}:
        return "medium"
    return "low"
