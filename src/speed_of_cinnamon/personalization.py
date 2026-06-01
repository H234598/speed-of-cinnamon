from __future__ import annotations

import os


def normalize_context(value: str = "") -> str:
    lines = [line.rstrip() for line in str(value or "").strip().splitlines()]
    return "\n".join(lines).strip()


def vocabulary_terms(value: str = "") -> list[str]:
    terms: list[str] = []
    for line in str(value or "").splitlines():
        term = line.strip()
        if term.startswith("- "):
            term = term[2:].strip()
        if term:
            terms.append(term)
    return terms


def normalize_vocabulary(value: str = "") -> str:
    return "\n".join(vocabulary_terms(value))


def build_personalization_prompt(personal_context: str = "", vocabulary: str = "") -> str:
    context = normalize_context(personal_context)
    terms = vocabulary_terms(vocabulary)
    sections: list[str] = []
    if context:
        sections.append("Context:\n" + context)
    if terms:
        sections.append("Vocabulary:\n" + "\n".join(f"- {term}" for term in terms))
    return "\n\n".join(sections)


def command_environment(personal_context: str = "", vocabulary: str = "") -> dict[str, str]:
    env = os.environ.copy()
    env["SPEED_OF_CINNAMON_CONTEXT"] = normalize_context(personal_context)
    env["SPEED_OF_CINNAMON_VOCABULARY"] = normalize_vocabulary(vocabulary)
    env["SPEED_OF_CINNAMON_PROMPT"] = build_personalization_prompt(personal_context, vocabulary)
    return env
