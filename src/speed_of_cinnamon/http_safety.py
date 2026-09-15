from __future__ import annotations

import http.client
import ipaddress
import math
import socket
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from multiprocessing.connection import Connection

MAX_LOOPBACK_HOSTNAME_CHARS = 255

MAX_PINNED_CONNECTION_TIMEOUT_SECONDS = 180.0

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

def _connect_to_pinned_addresses(
    connection: http.client.HTTPConnection,
    addresses: tuple[str, ...],
    *,
    prepare_socket: Callable[[object], object] | None = None,
) -> None:
    last_error: OSError | None = None
    timeout = connection.timeout
    if timeout is None:
        raise OSError("pinned connection timeout is required")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise OSError("pinned connection timeout is invalid")
    try:
        finite_timeout = math.isfinite(timeout)
    except OverflowError:
        finite_timeout = False
    if not finite_timeout or timeout <= 0:
        raise OSError("pinned connection timeout is invalid")
    if timeout > MAX_PINNED_CONNECTION_TIMEOUT_SECONDS:
        raise OSError("pinned connection timeout exceeds safe limit")
    connection.sock = None
    deadline = time.monotonic() + float(timeout)
    for address in addresses:
        remaining_timeout = deadline - time.monotonic()
        if remaining_timeout <= 0:
            break
        connected_socket = None
        try:
            connected_socket = socket.create_connection(
                (address, connection.port),
                remaining_timeout,
                connection.source_address,
            )
            remaining_after_connect = deadline - time.monotonic()
            if remaining_after_connect <= 0:
                raise TimeoutError("pinned connection timeout expired")
            try:
                connected_socket.settimeout(remaining_after_connect)
            except (AttributeError, OSError) as exc:
                raise OSError("pinned socket timeout could not be set") from exc
            prepared_socket = connected_socket
            if prepare_socket is not None:
                prepared_socket = prepare_socket(connected_socket)
            connection.sock = prepared_socket
            return
        except OSError as exc:
            connection.sock = None
            if connected_socket is not None:
                try:
                    connected_socket.close()
                except OSError:
                    pass
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError("no pinned addresses available")

class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, pinned_addresses: tuple[str, ...], **kwargs: object) -> None:
        self._pinned_addresses = pinned_addresses
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        if not self._tunnel_host:
            _connect_to_pinned_addresses(self, self._pinned_addresses)
            return

        def prepare_socket(raw_socket: object) -> object:
            self.sock = raw_socket
            self._tunnel()
            return self.sock

        _connect_to_pinned_addresses(
            self,
            self._pinned_addresses,
            prepare_socket=prepare_socket,
        )

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, pinned_addresses: tuple[str, ...], **kwargs: object) -> None:
        self._pinned_addresses = pinned_addresses
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        def prepare_socket(raw_socket: object) -> object:
            self.sock = raw_socket
            if self._tunnel_host:
                self._tunnel()
            server_hostname = self._tunnel_host or self.host
            return self._context.wrap_socket(self.sock, server_hostname=server_hostname)

        _connect_to_pinned_addresses(
            self,
            self._pinned_addresses,
            prepare_socket=prepare_socket,
        )

DNS_RESOLUTION_TIMEOUT_SECONDS = 5.0
MAX_DNS_RESOLUTION_TIMEOUT_SECONDS = DNS_RESOLUTION_TIMEOUT_SECONDS
_DNS_RESOLUTION_MAX_IN_FLIGHT = 4
_DNS_RESOLUTION_SLOTS = threading.BoundedSemaphore(_DNS_RESOLUTION_MAX_IN_FLIGHT)
_DNS_RESOLUTION_CONTEXT = None


def _dns_resolution_context():
    global _DNS_RESOLUTION_CONTEXT
    if _DNS_RESOLUTION_CONTEXT is None:
        import multiprocessing

        try:
            _DNS_RESOLUTION_CONTEXT = multiprocessing.get_context("fork")
        except ValueError:
            _DNS_RESOLUTION_CONTEXT = multiprocessing.get_context()
    return _DNS_RESOLUTION_CONTEXT


class UnsafeUrlError(ValueError):
    pass


def _resolve_dns_in_process(hostname: str, port: int, result_connection: Connection) -> None:
    try:
        result = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except BaseException as exc:
        try:
            result_connection.send(("error", (isinstance(exc, OSError), str(exc))))
        except (BrokenPipeError, EOFError, OSError):
            pass
    else:
        try:
            result_connection.send(("result", result))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        result_connection.close()


def _getaddrinfo_with_timeout(hostname: str, port: int, *, timeout_seconds: float) -> list[tuple[object, ...]]:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TimeoutError("DNS resolution deadline expired")
    try:
        finite_timeout = math.isfinite(timeout_seconds)
    except OverflowError:
        finite_timeout = False
    if not finite_timeout or timeout_seconds <= 0:
        raise TimeoutError("DNS resolution deadline expired")
    if timeout_seconds > MAX_DNS_RESOLUTION_TIMEOUT_SECONDS:
        raise TimeoutError("DNS resolution timeout exceeds safe limit")
    if not _DNS_RESOLUTION_SLOTS.acquire(timeout=timeout_seconds):
        raise TimeoutError("DNS resolver is busy")
    context = _dns_resolution_context()
    result_connection, child_connection = context.Pipe(duplex=False)
    worker = context.Process(
        target=_resolve_dns_in_process,
        args=(hostname, port, child_connection),
        name="speed-of-cinnamon-dns",
    )
    worker.daemon = True
    try:
        worker.start()
        child_connection.close()
        worker.join(timeout_seconds)
        if worker.is_alive():
            worker.terminate()
            worker.join(0.5)
            if worker.is_alive():
                worker.kill()
                worker.join(0.5)
            if worker.is_alive():
                raise TimeoutError("DNS resolver process could not be stopped")
            raise TimeoutError("DNS resolution deadline expired")
        if not result_connection.poll(0.1):
            raise OSError("DNS resolver exited without result")
        kind, value = result_connection.recv()
        if kind == "error":
            _is_os_error, message = value
            if _is_os_error:
                raise OSError(message)
            raise RuntimeError(message)
        return value
    finally:
        child_connection.close()
        result_connection.close()
        _DNS_RESOLUTION_SLOTS.release()






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
    try:
        finite_timeout = math.isfinite(resolution_timeout)
    except OverflowError:
        finite_timeout = False
    if (
        isinstance(resolution_timeout, bool)
        or not isinstance(resolution_timeout, (int, float))
        or not finite_timeout
        or resolution_timeout <= 0
        or resolution_timeout > MAX_DNS_RESOLUTION_TIMEOUT_SECONDS
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
