from __future__ import annotations

import unicodedata

SAFE_CHAR_MAP = {
    "ß": "ss",
    "ẞ": "SS",
    "æ": "ae",
    "Æ": "AE",
    "œ": "oe",
    "Œ": "OE",
    "ø": "o",
    "Ø": "O",
    "đ": "d",
    "Đ": "D",
    "ł": "l",
    "Ł": "L",
    "þ": "th",
    "Þ": "Th",
    "ð": "d",
    "Ð": "D",
    "ñ": "n",
    "Ñ": "N",
    "ç": "c",
    "Ç": "C",
    "¿": "",
    "¡": "",
}


def sanitize_special_chars(text: str) -> str:
    if not isinstance(text, str) or isinstance(text, bool):
        raise ValueError("text must be text")
    if text.isascii() and all((char >= " " and char != "\x7f") or char in "\n\t" for char in text):
        return text
    parts: list[str] = []
    for char in text:
        if char not in "\n\t" and (char < " " or unicodedata.category(char).startswith("C")):
            continue
        if unicodedata.category(char).startswith("M"):
            continue
        if char in SAFE_CHAR_MAP:
            parts.append(SAFE_CHAR_MAP[char])
            continue
        normalized = unicodedata.normalize("NFKD", char)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        parts.append(ascii_text if ascii_text else char)
    return "".join(parts)
