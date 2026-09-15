from __future__ import annotations


INSERT_METHOD_ALIASES = {
    "clipboard-paste.submit": "clipboard-paste-submit",
}


def normalize_insert_method(value: str) -> str:
    normalized = value.strip().lower()
    return INSERT_METHOD_ALIASES.get(normalized, normalized)
