from __future__ import annotations

import http.client
import ipaddress
import math
import queue
import socket
import threading
import time
import urllib.parse
import urllib.request

MAX_LOOPBACK_HOSTNAME_CHARS = 255
DNS_RESOLUTION_TIMEOUT_SECONDS = 5.0
_DNS_RESOLUTION_MAX_IN_FLIGHT = 4
_DNS_RESOLUTION_SLOTS = threading.BoundedSemaphore(_DNS_RESOLUTION_MAX_IN_FLIGHT)


class UnsafeUrlError(ValueError):
    pass


def _getaddrinfo_with_timeout(hostname: str, port: int, *, timeout_seconds: float) -> list[tuple[object, ...]]:
    if timeout_seconds <= 0:
        raise TimeoutError("DNS resolution deadline expired")
    if not _DNS_RESOLUTION_SLOTS.acquire(timeout=timeout_seconds):
        raise TimeoutError("DNS resolver is busy")
    result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result_queue.put(("result", socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)))
        except Exception as exc:
            result_queue.put(("error", exc))
        finally:
            _DNS_RESOLUTION_SLOTS.release()

    worker = threading.Thread(target=resolve, name="speed-of-cinnamon-dns", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError("DNS resolution deadline expired")
    kind, value = result_queue.get_nowait()
    if kind == "error":
        raise value  # type: ignore[misc]
    return value  # type: ignore[return-value]


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
    bracketed = normalized.startswith("[") or normalized.endswith("]")
    if bracketed:
        if not (normalized.startswith("[") and normalized.endswith("]")):
            return False
        normalized = normalized[1:-1]
    elif normalized.endswith("."):
        normalized = normalized[:-1]
    if "%" in normalized:
        return False
    if normalized == "localhost":
        return not bracketed
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if bracketed and not isinstance(address, ipaddress.IPv6Address):
        return False
    return address.is_loopback


def resolve_url_host(
    url: str,
    *,
    field_name: str,
    allow_loopback_host: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[str, ...]:
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError(f"{field_name} hostname could not be resolved") from exc
    if not hostname:
        raise UnsafeUrlError(f"{field_name} hostname could not be resolved")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    resolution_timeout = DNS_RESOLUTION_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    if (
        isinstance(resolution_timeout, bool)
        or not isinstance(resolution_timeout, (int, float))
        or not math.isfinite(resolution_timeout)
        or resolution_timeout <= 0
    ):
        raise UnsafeUrlError(f"{field_name} hostname resolution timed out")
    try:
        resolved = _getaddrinfo_with_timeout(hostname, port, timeout_seconds=resolution_timeout)
    except TimeoutError as exc:
        raise UnsafeUrlError(f"{field_name} hostname resolution timed out") from exc
    except OSError as exc:
        raise UnsafeUrlError(f"{field_name} hostname could not be resolved") from exc
    addresses: list[str] = []
    loopback_hostname = allow_loopback_host and is_loopback_hostname(hostname)
    for result in resolved:
        sockaddr = result[4] if len(result) > 4 else ()
        address_text = sockaddr[0] if isinstance(sockaddr, tuple) and sockaddr else ""
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            continue
        if loopback_hostname:
            unsafe_address = not address.is_loopback
        else:
            unsafe_address = address.is_multicast or not address.is_global
        if unsafe_address:
            raise UnsafeUrlError(f"{field_name} resolves to a non-public address")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise UnsafeUrlError(f"{field_name} hostname could not be resolved")
    return tuple(addresses)


def _connect_to_pinned_addresses(connection: http.client.HTTPConnection, addresses: tuple[str, ...]) -> None:
    last_error: OSError | None = None
    timeout = connection.timeout
    deadline = None if timeout is None else time.monotonic() + timeout
    for address in addresses:
        if deadline is None:
            remaining_timeout = None
        else:
            remaining_timeout = deadline - time.monotonic()
            if remaining_timeout <= 0:
                break
        try:
            connection.sock = socket.create_connection(
                (address, connection.port),
                remaining_timeout,
                connection.source_address,
            )
            return
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError("no pinned addresses available")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, pinned_addresses: tuple[str, ...], **kwargs: object) -> None:
        self._pinned_addresses = pinned_addresses
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        _connect_to_pinned_addresses(self, self._pinned_addresses)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, pinned_addresses: tuple[str, ...], **kwargs: object) -> None:
        self._pinned_addresses = pinned_addresses
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        _connect_to_pinned_addresses(self, self._pinned_addresses)
        if self._tunnel_host:
            self._tunnel()
        server_hostname = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pinned_addresses: tuple[str, ...]) -> None:
        super().__init__()
        self._pinned_addresses = pinned_addresses

    def http_open(self, req):  # type: ignore[override]
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPConnection(
                host,
                pinned_addresses=self._pinned_addresses,
                **kwargs,
            ),
            req,
        )


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_addresses: tuple[str, ...]) -> None:
        super().__init__()
        self._pinned_addresses = pinned_addresses

    def https_open(self, req):  # type: ignore[override]
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(
                host,
                pinned_addresses=self._pinned_addresses,
                **kwargs,
            ),
            req,
        )
