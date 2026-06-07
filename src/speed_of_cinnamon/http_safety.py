from __future__ import annotations

import ipaddress


def is_loopback_hostname(hostname: str | None) -> bool:
    if hostname is None:
        return False
    normalized = hostname.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
