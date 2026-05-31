"""Optional merchant web research for DSPy category suggestions.

The project must work offline, so this module is deliberately opt-in. When
``ENABLE_WEB_RESEARCH=1`` and a supported search API key is present, it fetches
small search snippets for the transaction merchant and caches them locally.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import CACHE_DIR
from app.categorization.rule_store import normalize_keyword

CACHE_PATH = CACHE_DIR / "merchant_research.json"
MAX_RESULTS = 3
TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class MerchantResearch:
    query: str
    summary: str
    source: str


def is_web_research_enabled() -> bool:
    """Return True when merchant web research is enabled."""
    flag = os.getenv("ENABLE_WEB_RESEARCH", "0")
    return flag.strip().lower() in {"1", "true", "yes", "on"}


def research_merchant(*, description: str, merchant: str) -> MerchantResearch | None:
    """Search the merchant/company online and return compact evidence snippets."""
    if not is_web_research_enabled():
        return None

    query_subject = _query_subject(description=description, merchant=merchant)
    if not query_subject:
        return None

    query = f"{query_subject} empresa atividade categoria Portugal"
    provider = os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower()
    cache_key = _cache_key(provider, query)

    cached = _read_cache().get(cache_key)
    if isinstance(cached, dict):
        summary = str(cached.get("summary", "")).strip()
        if summary:
            return MerchantResearch(
                query=str(cached.get("query", query)),
                summary=summary,
                source=str(cached.get("source", f"{provider}_cache")),
            )

    try:
        if provider == "ollama":
            summary = _search_ollama(query, description=description, merchant=merchant)
        elif provider == "serpapi":
            summary = _search_serpapi(query)
        elif provider == "brave":
            summary = _search_brave(query)
        else:
            provider = "tavily"
            summary = _search_tavily(query)
    except Exception:
        return None

    summary = _compact(summary)
    if not summary:
        return None

    result = MerchantResearch(query=query, summary=summary, source=provider)
    _write_cache_entry(cache_key, result)
    return result


def _query_subject(*, description: str, merchant: str) -> str:
    merchant_norm = normalize_keyword(merchant)
    if merchant_norm and len(merchant_norm) >= 3:
        return merchant.strip()

    description_norm = normalize_keyword(description)
    if not description_norm:
        return ""

    tokens = [token for token in description_norm.split() if len(token) >= 3]
    return " ".join(tokens[:6])


def _cache_key(provider: str, query: str) -> str:
    digest = hashlib.sha256(f"{provider}:{query}".encode("utf-8")).hexdigest()
    return digest[:32]


def _read_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache_entry(cache_key: str, research: MerchantResearch) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = _read_cache()
    data[cache_key] = {
        "query": research.query,
        "summary": research.summary,
        "source": research.source,
    }
    CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _search_tavily(query: str) -> str:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return ""

    data = _post_json(
        "https://api.tavily.com/search",
        {
            "api_key": api_key,
            "query": query,
            "max_results": MAX_RESULTS,
            "search_depth": "basic",
            "include_answer": False,
        },
    )
    results = data.get("results", [])
    return _format_results(results, title_key="title", snippet_key="content", url_key="url")


def _search_serpapi(query: str) -> str:
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return ""

    params = urllib.parse.urlencode(
        {"engine": "google", "q": query, "api_key": api_key, "num": MAX_RESULTS}
    )
    data = _get_json(f"https://serpapi.com/search.json?{params}")
    results = data.get("organic_results", [])
    return _format_results(results, title_key="title", snippet_key="snippet", url_key="link")


def _search_brave(query: str) -> str:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return ""

    params = urllib.parse.urlencode({"q": query, "count": MAX_RESULTS})
    data = _get_json(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    results = data.get("web", {}).get("results", [])
    return _format_results(results, title_key="title", snippet_key="description", url_key="url")


def _search_ollama(query: str, *, description: str, merchant: str) -> str:
    """Use a local Ollama model as merchant context provider.

    This is not internet search. It asks the local model to infer likely merchant
    activity from its local knowledge and from the transaction text.
    """
    model = os.getenv("OLLAMA_RESEARCH_MODEL", "qwen2.5:14b").strip() or "qwen2.5:14b"
    timeout = int(os.getenv("OLLAMA_RESEARCH_TIMEOUT", "90"))
    prompt = f"""
Analise a transação bancária abaixo e ajude a identificar a atividade provável
do comerciante/empresa para fins de categorização financeira pessoal.

Não invente certeza. Se não souber, diga que a evidência é fraca.
Responda em português em no máximo 5 linhas, com:
- atividade provável
- categoria financeira mais provável
- nível de confiança: alto, médio ou baixo
- motivo curto

Consulta: {query}
Merchant: {merchant}
Descrição: {description}
""".strip()

    completed = subprocess.run(
        ["ollama", "run", model, prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _format_results(
    results: list[dict[str, Any]],
    *,
    title_key: str,
    snippet_key: str,
    url_key: str,
) -> str:
    lines: list[str] = []
    for result in results[:MAX_RESULTS]:
        title = str(result.get(title_key, "")).strip()
        snippet = str(result.get(snippet_key, "")).strip()
        url = str(result.get(url_key, "")).strip()
        if title or snippet:
            lines.append(_compact(f"{title}: {snippet} ({url})"))
    return "\n".join(lines)


def _compact(text: str, limit: int = 1200) -> str:
    compacted = " ".join(str(text).split())
    return compacted[:limit].strip()
