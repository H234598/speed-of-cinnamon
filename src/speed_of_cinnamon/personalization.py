from __future__ import annotations

import os

MAX_PERSONAL_CONTEXT_CHARS = 65_535
MAX_VOCABULARY_CHARS = 65_535


def normalize_context(value: str = "") -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("personal context must be text")
    raw = value or ""
    if _contains_escaped_null(raw):
        raise ValueError("personal context contains invalid null byte")
    normalized = "\n".join(line.rstrip() for line in raw.strip().splitlines()).strip()
    if len(normalized) > MAX_PERSONAL_CONTEXT_CHARS:
        raise ValueError(f"personal context is too large (max {MAX_PERSONAL_CONTEXT_CHARS} characters)")
    if len(normalized.encode("utf-8")) > MAX_PERSONAL_CONTEXT_CHARS:
        raise ValueError(f"personal context is too large (max {MAX_PERSONAL_CONTEXT_CHARS} bytes)")
    return normalized


def vocabulary_terms(value: str = "") -> list[str]:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("vocabulary must be text")
    raw = value or ""
    if _contains_escaped_null(raw):
        raise ValueError("vocabulary contains invalid null byte")
    if len(raw) > MAX_VOCABULARY_CHARS:
        raise ValueError(f"vocabulary is too large (max {MAX_VOCABULARY_CHARS} characters)")
    if len(raw.encode("utf-8")) > MAX_VOCABULARY_CHARS:
        raise ValueError(f"vocabulary is too large (max {MAX_VOCABULARY_CHARS} bytes)")
    terms: list[str] = []
    for line in raw.splitlines():
        term = line.strip()
        if term.startswith("- "):
            term = term[2:].strip()
        if term:
            terms.append(term)
    return terms


def normalize_vocabulary(value: str = "") -> str:
    return "\n".join(vocabulary_terms(value))


def build_personalization_prompt(personal_context: str = "", vocabulary: str = "") -> str:
    if len(personal_context) > MAX_PERSONAL_CONTEXT_CHARS:
        raise ValueError(f"personal context is too large (max {MAX_PERSONAL_CONTEXT_CHARS} characters)")
    if len(personal_context.encode("utf-8")) > MAX_PERSONAL_CONTEXT_CHARS:
        raise ValueError(f"personal context is too large (max {MAX_PERSONAL_CONTEXT_CHARS} bytes)")
    if len(vocabulary.encode("utf-8")) > MAX_VOCABULARY_CHARS:
        raise ValueError(f"vocabulary is too large (max {MAX_VOCABULARY_CHARS} bytes)")
    context = normalize_context(personal_context)
    terms = vocabulary_terms(vocabulary)
    sections: list[str] = []
    if context:
        sections.append("Context:\n" + context)
    if terms:
        sections.append("Vocabulary:\n" + "\n".join(f"- {term}" for term in terms))
    return "\n\n".join(sections)


def command_environment(personal_context: str = "", vocabulary: str = "") -> dict[str, str]:
    context = normalize_context(personal_context)
    normalized_vocabulary = normalize_vocabulary(vocabulary)
    prompt = build_personalization_prompt(personal_context, vocabulary)

    env = os.environ.copy()
    env["SPEED_OF_CINNAMON_CONTEXT"] = context
    env["SPEED_OF_CINNAMON_VOCABULARY"] = normalized_vocabulary
    env["SPEED_OF_CINNAMON_PROMPT"] = prompt
    return env


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered
