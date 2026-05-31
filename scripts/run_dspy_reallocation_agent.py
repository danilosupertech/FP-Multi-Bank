"""Run the DSPy reallocation agent from the command line.

Use this from a PowerShell session with either OpenAI/DSPy variables configured
or ``ENABLE_OLLAMA_CATEGORY=1`` for local Ollama decisions.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dashboard.streamlit_app import (  # noqa: E402
    load_transactions,
    prepare_data,
    run_dspy_reallocation_agent,
    save_agent_run_log,
)


PLACEHOLDER_PREFIXES = ("sua_chave", "your_", "sk-xxxx")


def _looks_configured(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return bool(normalized) and not normalized.startswith(PLACEHOLDER_PREFIXES)


def _validate_environment() -> list[str]:
    errors: list[str] = []
    dspy_category = os.getenv("ENABLE_DSPY_CATEGORY", "").strip().lower() in {"1", "true", "yes", "on"}
    dspy_audit = os.getenv("ENABLE_DSPY_AUDIT", "").strip().lower() in {"1", "true", "yes", "on"}
    ollama_category = os.getenv("ENABLE_OLLAMA_CATEGORY", "").strip().lower() in {"1", "true", "yes", "on"}

    if not ollama_category:
        if not dspy_category:
            errors.append("ENABLE_DSPY_CATEGORY precisa estar ativo.")
        if not dspy_audit:
            errors.append("ENABLE_DSPY_AUDIT precisa estar ativo.")
        if not _looks_configured(os.getenv("OPENAI_API_KEY")):
            errors.append("OPENAI_API_KEY ausente ou ainda com valor de exemplo.")

    web_enabled = os.getenv("ENABLE_WEB_RESEARCH", "").strip().lower() in {"1", "true", "yes", "on"}
    if web_enabled:
        provider = os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower()
        provider_keys = {
            "tavily": "TAVILY_API_KEY",
            "serpapi": "SERPAPI_API_KEY",
            "brave": "BRAVE_SEARCH_API_KEY",
            "ollama": "",
        }
        key_name = provider_keys.get(provider)
        if key_name is None:
            errors.append("WEB_SEARCH_PROVIDER deve ser tavily, serpapi, brave ou ollama.")
        elif key_name and not _looks_configured(os.getenv(key_name)):
            errors.append(f"{key_name} ausente ou ainda com valor de exemplo.")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa o agente DSPy para realocar categorias no SQLite."
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--confidence", choices=["high", "medium", "low"], default="high")
    parser.add_argument(
        "--scope",
        choices=["outros", "all"],
        default="outros",
        help="outros = apenas categoria Outros; all = todas as categorias de débito",
    )
    parser.add_argument("--create-missing-categories", action="store_true")
    parser.add_argument("--learn-rules", action="store_true")
    args = parser.parse_args()

    errors = _validate_environment()
    if errors:
        print("Configuração incompleta:")
        for error in errors:
            print(f"- {error}")
        return 2

    df = prepare_data(load_transactions())
    if df.empty:
        print("Nenhuma transação válida encontrada.")
        return 1

    scope = "Apenas categoria Outros" if args.scope == "outros" else "Todas as categorias de débito"
    print(
        "Executando agente DSPy: "
        f"limit={args.limit}, confidence={args.confidence}, scope={scope}"
    )

    def show_progress(
        processed_now: int,
        total_now: int,
        detail: dict[str, object],
    ) -> None:
        percent = processed_now / total_now if total_now else 1.0
        merchant = str(detail.get("merchant", "")) or "Sem merchant"
        status = str(detail.get("status", ""))
        current = str(detail.get("categoria_atual", ""))
        suggested = str(detail.get("categoria_sugerida", "")) or "-"
        confidence = str(detail.get("confianca", "")) or "-"
        method = str(detail.get("metodo", "")) or "-"
        print(
            f"[{processed_now}/{total_now} {percent:.0%}] "
            f"{merchant} | {status} | {current} -> {suggested} | "
            f"confiança={confidence} | método={method}",
            flush=True,
        )

    processed, changed, skipped, details = run_dspy_reallocation_agent(
        df,
        limit=args.limit,
        minimum_confidence=args.confidence,
        scope=scope,
        create_missing_categories=args.create_missing_categories,
        learn_rules=args.learn_rules,
        progress_callback=show_progress,
    )

    print(f"Analisadas: {processed}")
    print(f"Realocadas: {changed}")
    print(f"Ignoradas: {skipped}")
    log_path = save_agent_run_log(details)
    if log_path is not None:
        print(f"Log: {log_path}")
    if not details.empty:
        preview_cols = [
            "id",
            "status",
            "categoria_atual",
            "categoria_sugerida",
            "confianca",
            "metodo",
            "motivo",
        ]
        print(details[[col for col in preview_cols if col in details.columns]].head(20).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
