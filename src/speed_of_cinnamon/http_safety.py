from __future__ import annotations

import ipaddress

MAX_LOOPBACK_HOSTNAME_CHARS = 255


def has_unsafe_url_characters(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        return True
    return any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F
        for char in value
    )


def is_loopback_hostname(hostname: str | None) -> bool:
    if hostname is None or isinstance(hostname, bool) or not isinstance(hostname, str):
        return False
    if len(hostname) > MAX_LOOPBACK_HOSTNAME_CHARS or has_unsafe_url_characters(hostname):
        return False
    normalized = hostname.lower()
    if normalized.endswith("."):
        normalized = normalized[:-1]
    bracketed = normalized.startswith("[") or normalized.endswith("]")
    if bracketed:
        if not (normalized.startswith("[") and normalized.endswith("]")):
            return False
        normalized = normalized[1:-1]
    if "%" in normalized:
        return False
    if normalized == "localhost":
        return not bracketed
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return False
    if bracketed and not isinstance(address, ipaddress.IPv6Address):
        return False
    return address.is_loopback
