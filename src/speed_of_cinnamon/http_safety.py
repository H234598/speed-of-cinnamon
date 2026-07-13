from __future__ import annotations

import ipaddress


def is_loopback_hostname(hostname: str | None) -> bool:
    if hostname is None or isinstance(hostname, bool) or not isinstance(hostname, str):
        return False
    normalized = hostname.lower()
    if normalized.startswith("[") or normalized.endswith("]"):
        if not (normalized.startswith("[") and normalized.endswith("]")):
            return False
        normalized = normalized[1:-1]
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
