# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Product ontology for Gemini UCP agents.

Provides synonym normalisation and category inference so that natural-language
queries (e.g. "ultrabook", "cell phone") resolve to the canonical terms and
category filters that the UCP search endpoint understands.

Three integration points:

1. ``get_agent_hints()`` — inject into the Gemini system prompt at startup.
2. ``expand_query(fn_name, args)`` — enrich UCP search args before each HTTP call.
3. ``agent_hints`` fragment in ``build_ucp_manifest()`` — surface hints to any
   UCP-compliant agent that reads the manifest.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Synonym → canonical product term
# ---------------------------------------------------------------------------

_CANONICAL: dict[str, str] = {
    "notebook": "laptop",
    "ultrabook": "laptop",
    "portable computer": "laptop",
    "laptop computer": "laptop",
    "chromebook": "laptop",
    "smartphone": "phone",
    "mobile phone": "phone",
    "cell phone": "phone",
    "cellphone": "phone",
    "handset": "phone",
    "slate": "tablet",
    "ipad": "tablet",
    "display": "monitor",
    "screen": "monitor",
    "earphones": "headphones",
    "earbuds": "headphones",
    "headset": "headphones",
    "in-ear": "headphones",
    "wireless router": "router",
    "wifi router": "router",
    "wi-fi router": "router",
    "network router": "router",
    "hard drive": "storage",
    "hard disk": "storage",
    "hdd": "storage",
    "ssd": "storage",
    "flash drive": "storage",
    "external drive": "storage",
    "usb drive": "storage",
    "webcam": "camera",
    "dslr": "camera",
    "mirrorless": "camera",
    "digital camera": "camera",
    "inkjet": "printer",
    "laser printer": "printer",
    "ink printer": "printer",
}

# ---------------------------------------------------------------------------
# Canonical product term → UCP category name
# ---------------------------------------------------------------------------

_TERM_CATEGORY: dict[str, str] = {
    "laptop": "Computers",
    "computer": "Computers",
    "desktop": "Computers",
    "workstation": "Computers",
    "tablet": "Tablets",
    "phone": "Mobile Phones",
    "monitor": "Monitors & Displays",
    "keyboard": "Peripherals",
    "mouse": "Peripherals",
    "headphones": "Audio",
    "speaker": "Audio",
    "camera": "Cameras",
    "printer": "Printers",
    "router": "Networking",
    "switch": "Networking",
    "storage": "Storage",
    "memory": "Memory & RAM",
    "ram": "Memory & RAM",
    "gpu": "Graphics Cards",
    "graphics card": "Graphics Cards",
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def expand_query(fn_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Enrich UCP search args using the ontology before the HTTP call.

    For ``search_products`` calls:
    - Normalises alternate product terms to their canonical form in the ``q``
      field (e.g. "ultrabook" → "laptop").
    - Auto-sets ``category`` when unspecified and the query maps to a known
      category, giving the search endpoint an extra filter at no cost.

    All other UCP functions are returned unchanged.
    """
    if fn_name != "search_products":
        return args

    result = dict(args)
    q = (args.get("q") or "").strip()
    if not q:
        return result

    q_lower = q.lower()

    # Multi-word synonyms must be checked before single-word ones.
    canonical: str | None = None
    for alt in sorted(_CANONICAL, key=len, reverse=True):
        if alt in q_lower:
            canonical = _CANONICAL[alt]
            # Replace the alternate term with the canonical one in the query.
            result["q"] = q_lower.replace(alt, canonical).strip()
            break

    # If no synonym matched, check whether a canonical term is already present.
    if canonical is None:
        for term in _TERM_CATEGORY:
            if term in q_lower:
                canonical = term
                break

    # Inject category when the query maps to a known one and none was set.
    if canonical and not result.get("category"):
        cat = _TERM_CATEGORY.get(canonical)
        if cat:
            result["category"] = cat

    return result


def get_agent_hints() -> str:
    """Return a concise ontology-awareness hint for the Gemini system prompt."""
    sample_synonyms = [
        f'"{alt}" → "{canon}"'
        for alt, canon in list(_CANONICAL.items())[:6]
    ]
    sample_categories = list(_TERM_CATEGORY.values())[:6]
    return (
        "Ontology layer is active: common synonym mappings are applied automatically "
        "before each product search — for example "
        + ", ".join(sample_synonyms)
        + ". "
        "Recognised product categories include: "
        + ", ".join(f'"{c}"' for c in dict.fromkeys(sample_categories))
        + ", and more. "
        "When a user's intent clearly targets a product type, prefer precise category "
        "names in the search call rather than relying on keyword matching alone."
    )
