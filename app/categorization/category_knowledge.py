"""Load reusable category intelligence for LLM/Ollama agents."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from app.config import RULES_DIR

INTELLIGENCE_PATH = RULES_DIR / "category_intelligence.json"
NEW_INTELLIGENCE_PATH = RULES_DIR / "category_intelligence_novo.json"


def load_category_intelligence() -> dict[str, Any]:
    """Load category intelligence JSON, returning an empty structure on failure."""
    data = _load_json_file(INTELLIGENCE_PATH)
    new_data = _load_json_file(NEW_INTELLIGENCE_PATH)
    if new_data:
        data = _merge_intelligence(data, new_data)
    return data


def _load_json_file(path) -> dict[str, Any]:
    """Load one intelligence JSON file defensively."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_intelligence(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge the base memory with an optional user-provided richer file."""
    if not base:
        return dict(incoming)

    merged = dict(base)
    for key, value in incoming.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **value}
        elif isinstance(current, list) and isinstance(value, list):
            merged[key] = list(dict.fromkeys([*current, *value]))
        else:
            merged[key] = value
    return merged


def _format_category_notes(notes: Any) -> str:
    """Format category notes supporting both old and rich JSON schemas."""
    if isinstance(notes, list):
        return " ".join(str(note) for note in notes[:2])
    if not isinstance(notes, dict):
        return str(notes)

    parts: list[str] = []
    description = notes.get("description")
    if description:
        parts.append(str(description))

    examples = notes.get("merchant_examples", [])
    if isinstance(examples, list) and examples:
        parts.append("Exemplos: " + ", ".join(str(item) for item in examples[:8]))

    keywords = notes.get("keywords", [])
    if isinstance(keywords, list) and keywords:
        parts.append("Termos: " + ", ".join(str(item) for item in keywords[:12]))

    negative_keywords = notes.get("negative_keywords", [])
    if isinstance(negative_keywords, list) and negative_keywords:
        parts.append("Evitar quando houver: " + ", ".join(str(item) for item in negative_keywords[:8]))

    return " ".join(parts)


def format_category_intelligence_for_prompt(limit: int = 8000) -> str:
    """Format the intelligence file as compact prompt context."""
    data = load_category_intelligence()
    if not data:
        return ""

    lines: list[str] = []
    principles = data.get("principles", [])
    if isinstance(principles, list) and principles:
        lines.append("Principios aprendidos:")
        lines.extend(f"- {item}" for item in principles[:8])

    category_guidance = data.get("category_guidance", {})
    if isinstance(category_guidance, dict) and category_guidance:
        lines.append("Guia por categoria:")
        for category, notes in category_guidance.items():
            note_text = _format_category_notes(notes)
            lines.append(f"- {category}: {note_text}")

    merchant_examples = data.get("merchant_examples", {})
    if isinstance(merchant_examples, dict) and merchant_examples:
        examples = [
            f"{merchant} => {category}"
            for merchant, category in list(merchant_examples.items())[:60]
        ]
        lines.append("Exemplos merchant => categoria:")
        lines.append("; ".join(examples))

    merchant_overrides = data.get("merchant_overrides", {})
    if isinstance(merchant_overrides, dict) and merchant_overrides:
        examples = []
        for merchant, rule in list(merchant_overrides.items())[:120]:
            if isinstance(rule, dict):
                category = rule.get("category", "")
                confidence = rule.get("confidence", "")
                examples.append(f"{merchant} => {category} ({confidence})")
            else:
                examples.append(f"{merchant} => {rule}")
        lines.append("Regras aprendidas por merchant:")
        lines.append("; ".join(examples))

    decision_rules = data.get("decision_rules", [])
    if isinstance(decision_rules, list) and decision_rules:
        lines.append("Regras por termos:")
        for rule in decision_rules[:40]:
            if not isinstance(rule, dict):
                continue
            terms = rule.get("if_contains", [])
            category = rule.get("category", "")
            confidence = rule.get("confidence", "")
            if isinstance(terms, list) and terms and category:
                lines.append(
                    f"- {', '.join(str(term) for term in terms[:10])} => {category} ({confidence})"
                )

    subcategories = data.get("subcategories", {})
    if isinstance(subcategories, dict) and subcategories:
        lines.append("Subcategorias uteis:")
        for category, values in subcategories.items():
            if isinstance(values, list):
                lines.append(f"- {category}: {', '.join(str(value) for value in values[:8])}")

    text = "\n".join(lines)
    return text[:limit].strip()


def find_category_intelligence_match(
    *,
    description: str,
    merchant: str,
    existing_categories: list[str],
) -> dict[str, str] | None:
    """Return a deterministic category suggestion from the intelligence files."""
    data = load_category_intelligence()
    if not data:
        return None

    categories = {str(category) for category in existing_categories}
    haystack = _normalize_text(f"{merchant} {description}")
    merchant_text = _normalize_text(merchant)

    merchant_overrides = data.get("merchant_overrides", {})
    if isinstance(merchant_overrides, dict):
        matches: list[tuple[int, str, dict[str, Any]]] = []
        for pattern, rule in merchant_overrides.items():
            if not isinstance(rule, dict):
                continue
            normalized_pattern = _normalize_text(str(pattern))
            if not normalized_pattern:
                continue
            if normalized_pattern == merchant_text or normalized_pattern in haystack:
                matches.append((len(normalized_pattern), str(pattern), rule))
        if matches:
            _, pattern, rule = sorted(matches, reverse=True)[0]
            category = str(rule.get("category", "")).strip()
            if category in categories:
                return {
                    "category": category,
                    "confidence": str(rule.get("confidence", "high")).strip() or "high",
                    "reason": str(rule.get("reason", "")).strip()
                    or f"Regra aprendida para o merchant '{pattern}'.",
                    "method": "category_intelligence_override",
                }

    decision_rules = data.get("decision_rules", [])
    if isinstance(decision_rules, list):
        for rule in decision_rules:
            if not isinstance(rule, dict):
                continue
            terms = rule.get("if_contains", [])
            category = str(rule.get("category", "")).strip()
            if category not in categories or not isinstance(terms, list):
                continue
            matched_terms = [
                str(term)
                for term in terms
                if _normalize_text(str(term)) and _normalize_text(str(term)) in haystack
            ]
            if matched_terms:
                return {
                    "category": category,
                    "confidence": str(rule.get("confidence", "high")).strip() or "high",
                    "reason": "Regra por termo aprendido: " + ", ".join(matched_terms[:5]),
                    "method": "category_intelligence_rule",
                }

    category_guidance = data.get("category_guidance", {})
    if isinstance(category_guidance, dict):
        for category, notes in category_guidance.items():
            category_name = str(category).strip()
            if category_name not in categories or not isinstance(notes, dict):
                continue
            keywords = notes.get("keywords", [])
            negative_keywords = notes.get("negative_keywords", [])
            if not isinstance(keywords, list):
                continue
            if isinstance(negative_keywords, list) and any(
                _normalize_text(str(term)) in haystack
                for term in negative_keywords
                if _normalize_text(str(term))
            ):
                continue
            matched_keywords = [
                str(term)
                for term in keywords
                if _normalize_text(str(term)) and _normalize_text(str(term)) in haystack
            ]
            if matched_keywords:
                return {
                    "category": category_name,
                    "confidence": "medium",
                    "reason": "Categoria indicada por palavra-chave: " + ", ".join(matched_keywords[:5]),
                    "method": "category_intelligence_keyword",
                }

    return None


def _normalize_text(value: str) -> str:
    """Normalize text for accent/case-insensitive rule matching."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_text.lower().split())
