from __future__ import annotations

import os

MAX_PERSONAL_CONTEXT_CHARS = 65_535
MAX_VOCABULARY_CHARS = 65_535
_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_BASE_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
}
_DANGEROUS_ENV_PREFIXES = ("LD_", "PYTHON", "BASH_", "__")
_DANGEROUS_ENV_KEYS = {
    "ENV",
    "PWD",
    "OLDPWD",
    "CDPATH",
    "PS4",
    "BASH_XTRACEFD",
    "SHELLOPTS",
    "PROMPT_COMMAND",
    "IFS",
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "BASH_ENV",
}


def _is_unsafe_env_var(name: str) -> bool:
    return name in _DANGEROUS_ENV_KEYS or name.startswith(_DANGEROUS_ENV_PREFIXES)


def _filtered_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            if not isinstance(key, str) or isinstance(key, bool):
                raise ValueError("environment key must be text")
            if not isinstance(value, str) or isinstance(value, bool):
                raise ValueError("environment value must be text")
            env[key] = value
    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


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

    env = _filtered_environment()
    env["SPEED_OF_CINNAMON_CONTEXT"] = context
    env["SPEED_OF_CINNAMON_VOCABULARY"] = normalized_vocabulary
    env["SPEED_OF_CINNAMON_PROMPT"] = prompt
    return env


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered
