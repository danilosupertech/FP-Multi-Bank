"""Persistent JSON rule store for learned merchant/category rules.

The database is useful for fast reads, but the JSON file is the durable
"learning memory" of the project. If the SQLite database is deleted, confirmed
rules can still be loaded again from this file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR

RULES_DIR = DATA_DIR / "rules"
MERCHANT_RULES_PATH = RULES_DIR / "merchant_rules.json"


@dataclass(frozen=True)
class JsonRuleMatch:
    """A matched rule from the JSON learning store."""

    keyword: str
    category: str
    confidence: float
    source: str


def normalize_keyword(text: str) -> str:
    """Normalize text for stable merchant/rule matching."""
    text = text.upper().strip()
    text = re.sub(r"[^A-ZÀ-Ú0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def _empty_store() -> dict:
    return {"version": 1, "rules": []}


def ensure_rule_store() -> None:
    """Create the JSON rule store if it does not exist."""
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    if not MERCHANT_RULES_PATH.exists():
        MERCHANT_RULES_PATH.write_text(
            json.dumps(_empty_store(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_json_rules() -> list[dict]:
    """Load merchant rules saved in JSON."""
    ensure_rule_store()
    try:
        data = json.loads(MERCHANT_RULES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = MERCHANT_RULES_PATH.with_suffix(".broken.json")
        MERCHANT_RULES_PATH.replace(backup)
        ensure_rule_store()
        data = _empty_store()

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        return []
    return rules


def save_json_rule(
    keyword: str,
    category: str,
    *,
    source: str = "user_confirmed",
    confidence: float = 1.0,
) -> None:
    """Insert or update a confirmed merchant/category rule in JSON."""
    normalized = normalize_keyword(keyword)
    category = category.strip()

    if not normalized or not category or category in {"Outros", "Crédito"}:
        return

    ensure_rule_store()
    data = json.loads(MERCHANT_RULES_PATH.read_text(encoding="utf-8"))
    rules = data.setdefault("rules", [])
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    for rule in rules:
        if normalize_keyword(str(rule.get("keyword", ""))) == normalized:
            rule["keyword"] = normalized
            rule["category"] = category
            rule["source"] = source
            rule["confidence"] = float(confidence)
            rule["times_confirmed"] = int(rule.get("times_confirmed", 0)) + 1
            rule["updated_at"] = now
            break
    else:
        rules.append(
            {
                "keyword": normalized,
                "category": category,
                "source": source,
                "confidence": float(confidence),
                "times_confirmed": 1,
                "created_at": now,
                "updated_at": now,
            }
        )

    rules.sort(key=lambda item: len(str(item.get("keyword", ""))), reverse=True)
    MERCHANT_RULES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def delete_json_rules_for_category(category: str, *, replacement: str = "Outros") -> None:
    """Remove or redirect rules when a category is deleted."""
    ensure_rule_store()
    data = json.loads(MERCHANT_RULES_PATH.read_text(encoding="utf-8"))
    rules = data.setdefault("rules", [])

    if replacement == "Outros":
        data["rules"] = [rule for rule in rules if rule.get("category") != category]
    else:
        for rule in rules:
            if rule.get("category") == category:
                rule["category"] = replacement
                rule["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    MERCHANT_RULES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def rename_json_category(old_name: str, new_name: str) -> None:
    """Rename category values inside the JSON learning store."""
    ensure_rule_store()
    data = json.loads(MERCHANT_RULES_PATH.read_text(encoding="utf-8"))
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for rule in data.setdefault("rules", []):
        if rule.get("category") == old_name:
            rule["category"] = new_name
            rule["updated_at"] = now
    MERCHANT_RULES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def match_json_rule(description: str, merchant: str) -> JsonRuleMatch | None:
    """Return the best JSON rule that matches description or merchant."""
    searchable = normalize_keyword(f"{description} {merchant}")
    if not searchable:
        return None

    for rule in load_json_rules():
        keyword = normalize_keyword(str(rule.get("keyword", "")))
        category = str(rule.get("category", "")).strip()
        if not keyword or not category:
            continue
        if keyword in searchable:
            return JsonRuleMatch(
                keyword=keyword,
                category=category,
                confidence=float(rule.get("confidence", 1.0)),
                source=str(rule.get("source", "json_rule")),
            )
    return None
