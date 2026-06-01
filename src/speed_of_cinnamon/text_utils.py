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
    parts: list[str] = []
    for char in text:
        if char in SAFE_CHAR_MAP:
            parts.append(SAFE_CHAR_MAP[char])
            continue
        normalized = unicodedata.normalize("NFKD", char)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        parts.append(ascii_text if ascii_text else char)
    return "".join(parts)
