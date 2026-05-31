"""Map model-proposed category names to the project's category taxonomy."""

from __future__ import annotations

import unicodedata


ALIASES: dict[str, str] = {
    "aluguel": "Arrendamento",
    "renda": "Arrendamento",
    "arrendamento": "Arrendamento",
    "mercado": "Supermercado",
    "supermercados": "Supermercado",
    "supermercado": "Supermercado",
    "mercearia": "Supermercado",
    "alimentacao": "Alimentação",
    "comida": "Alimentação",
    "restaurante": "Restaurantes e Cafés",
    "restaurantes": "Restaurantes e Cafés",
    "cafe": "Restaurantes e Cafés",
    "cafes": "Restaurantes e Cafés",
    "fast food": "Restaurantes e Cafés",
    "transporte": "Transportes",
    "transportes": "Transportes",
    "mobilidade": "Transportes",
    "uber": "Transportes",
    "bolt": "Transportes",
    "combustivel": "Combustível",
    "gasolina": "Combustível",
    "posto combustivel": "Combustível",
    "telecomunicacoes": "Telecom",
    "telecom": "Telecom",
    "internet": "Telecom",
    "energia": "Energia",
    "eletricidade": "Energia",
    "agua": "Água",
    "saude": "Saúde",
    "farmacia": "Saúde",
    "medico": "Saúde",
    "hospital": "Saúde",
    "beleza": "Beleza",
    "cabeleireiro": "Beleza",
    "vestuario": "Vestuário",
    "roupa": "Vestuário",
    "educacao": "Educação",
    "escola": "Educação",
    "curso": "Educação",
    "compras online": "Compras Online",
    "ecommerce": "Compras Online",
    "tecnologia": "Tecnologia",
    "software": "Tecnologia",
    "assinatura digital": "Tecnologia",
    "lazer": "Lazer",
    "entretenimento": "Lazer",
    "servicos": "Serviços",
    "servico": "Serviços",
    "transferencia": "Transferência",
    "documentos": "Documentos PT",
    "documentos pt": "Documentos PT",
    "impostos": "Impostos e Taxas",
    "taxas": "Impostos e Taxas",
    "seguros": "Seguros",
    "seguro": "Seguros",
    "casa": "Casa",
    "lar": "Casa",
    "automovel": "Automóvel",
    "carro": "Automóvel",
    "turismo": "Turismo",
    "viagem": "Turismo",
    "google": "Google/IA/Youtube",
    "youtube": "Google/IA/Youtube",
    "ia": "Google/IA/Youtube",
}


def normalize_category_text(value: str) -> str:
    """Normalize category text for matching."""
    text = unicodedata.normalize("NFKD", str(value).strip().lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace("/", " ").replace("-", " ").split())


def resolve_category_name(
    proposed: str,
    existing_categories: list[str],
) -> tuple[str, str]:
    """Resolve a model-proposed category to an existing project category.

    Returns ``(resolved_category, note)``. ``note`` is empty when no mapping was
    needed or possible.
    """
    proposed = str(proposed).strip()
    if not proposed:
        return "", ""

    exact = {category: category for category in existing_categories}
    if proposed in exact:
        return proposed, ""

    normalized_existing = {
        normalize_category_text(category): category for category in existing_categories
    }
    normalized = normalize_category_text(proposed)
    if normalized in normalized_existing:
        return normalized_existing[normalized], f"Categoria normalizada de '{proposed}'."

    alias = ALIASES.get(normalized)
    if alias and alias in existing_categories:
        return alias, f"Categoria mapeada de '{proposed}' para '{alias}'."

    for key, alias_category in ALIASES.items():
        if key in normalized and alias_category in existing_categories:
            return alias_category, f"Categoria mapeada de '{proposed}' para '{alias_category}'."

    return proposed, ""
