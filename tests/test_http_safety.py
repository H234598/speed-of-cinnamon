from __future__ import annotations

import unittest
import http.client
import ssl
import time
from unittest import mock

from speed_of_cinnamon import http_safety, transcriber
from speed_of_cinnamon.http_safety import UnsafeUrlError, resolve_url_host


class HttpSafetyTest(unittest.TestCase):
    def test_open_http_request_caps_long_dns_budget(self) -> None:
        request = mock.Mock()
        request.get_full_url.return_value = "https://example.test"
        with (
            mock.patch.object(transcriber, "_validate_http_request"),
            mock.patch.object(transcriber, "resolve_url_host", return_value=("93.184.216.34",)) as resolver,
            mock.patch.object(transcriber.time, "monotonic", side_effect=[100.0, 101.0, 102.0]),
            mock.patch.object(transcriber.urllib.request, "build_opener") as build_opener,
        ):
            transcriber._open_http_request(request, timeout=900, field_name="remote endpoint")

        resolver.assert_called_once_with(
            "https://example.test",
            field_name="remote endpoint",
            allow_loopback_host=True,
            timeout_seconds=5.0,
        )
        build_opener.return_value.open.assert_called_once_with(request, timeout=180.0)

    def test_open_http_request_preserves_dns_budget_below_cap(self) -> None:
        request = mock.Mock()
        request.get_full_url.return_value = "https://example.test"
        with (
            mock.patch.object(transcriber, "_validate_http_request"),
            mock.patch.object(transcriber, "resolve_url_host", return_value=("93.184.216.34",)) as resolver,
            mock.patch.object(transcriber.time, "monotonic", side_effect=[100.0, 100.5, 100.75]),
            mock.patch.object(transcriber.urllib.request, "build_opener") as build_opener,
        ):
            transcriber._open_http_request(request, timeout=1, field_name="remote endpoint")

        self.assertEqual(resolver.call_args.kwargs["timeout_seconds"], 0.5)
        build_opener.return_value.open.assert_called_once_with(request, timeout=0.25)

    def test_open_http_request_rejects_nonfinite_dns_budget(self) -> None:
        request = mock.Mock()
        request.get_full_url.return_value = "https://example.test"
        with (
            mock.patch.object(transcriber, "_validate_http_request"),
            mock.patch.object(transcriber, "resolve_url_host") as resolver,
            mock.patch.object(transcriber.time, "monotonic", side_effect=[100.0, float("nan")]),
        ):
            with self.assertRaisesRegex(transcriber.TranscriptionError, "request timed out"):
                transcriber._open_http_request(request, timeout=900, field_name="remote endpoint")

        resolver.assert_not_called()

    def test_open_http_request_rejects_nonfinite_budget_after_dns(self) -> None:
        request = mock.Mock()
        request.get_full_url.return_value = "https://example.test"
        with (
            mock.patch.object(transcriber, "_validate_http_request"),
            mock.patch.object(transcriber, "resolve_url_host", return_value=("93.184.216.34",)) as resolver,
            mock.patch.object(transcriber.time, "monotonic", side_effect=[100.0, 101.0, float("nan")]),
            mock.patch.object(transcriber.urllib.request, "build_opener") as build_opener,
        ):
            with self.assertRaisesRegex(transcriber.TranscriptionError, "request timed out"):
                transcriber._open_http_request(request, timeout=900, field_name="remote endpoint")

        resolver.assert_called_once()
        build_opener.assert_not_called()

    def test_dns_resolution_rejects_unrepresentable_timeout(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "deadline expired"):
            http_safety._getaddrinfo_with_timeout("example.test", 443, timeout_seconds=10**1000)

    def test_dns_resolution_rejects_timeout_above_safe_limit(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "exceeds safe limit"):
            http_safety._getaddrinfo_with_timeout(
                "example.test",
                443,
                timeout_seconds=http_safety.MAX_DNS_RESOLUTION_TIMEOUT_SECONDS + 1,
            )

    def test_resolve_url_host_rejects_timeout_above_safe_limit(self) -> None:
        with mock.patch.object(http_safety, "_getaddrinfo_with_timeout") as resolver:
            with self.assertRaisesRegex(UnsafeUrlError, "hostname resolution timed out"):
                resolve_url_host(
                    "https://example.test",
                    field_name="remote endpoint",
                    timeout_seconds=10**1000,
                )
        resolver.assert_not_called()

    def test_dns_resolution_fails_closed_when_worker_survives_kill(self) -> None:
        worker = mock.Mock()
        worker.is_alive.return_value = True
        context = mock.Mock()
        result_connection = mock.Mock()
        child_connection = mock.Mock()
        context.Pipe.return_value = (result_connection, child_connection)
        context.Process.return_value = worker

        with mock.patch.object(http_safety, "_DNS_RESOLUTION_CONTEXT", context):
            with self.assertRaisesRegex(TimeoutError, "could not be stopped"):
                http_safety._getaddrinfo_with_timeout("example.test", 443, timeout_seconds=0.01)

        worker.terminate.assert_called_once_with()
        worker.kill.assert_called_once_with()
        self.assertEqual(worker.join.call_count, 3)
        result_connection.close.assert_called_once_with()
        self.assertEqual(child_connection.close.call_count, 2)

    def test_dns_resolution_timeout_releases_worker(self) -> None:
        def blocked_resolution(*_: object, **__: object) -> list[tuple[object, ...]]:
            time.sleep(1.0)
            return []

        with mock.patch(
            "speed_of_cinnamon.http_safety.socket.getaddrinfo",
            side_effect=blocked_resolution,
        ):
            for _ in range(http_safety._DNS_RESOLUTION_MAX_IN_FLIGHT):
                with self.assertRaisesRegex(TimeoutError, "deadline expired"):
                    http_safety._getaddrinfo_with_timeout("example.test", 443, timeout_seconds=0.01)

        resolved = [(0, 0, 0, "", ("93.184.216.34", 443))]
        with mock.patch("speed_of_cinnamon.http_safety.socket.getaddrinfo", return_value=resolved):
            self.assertEqual(
                http_safety._getaddrinfo_with_timeout("example.test", 443, timeout_seconds=1.0),
                resolved,
            )

    def test_dns_resolution_propagates_resolver_error(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.http_safety.socket.getaddrinfo",
            side_effect=OSError("resolver failed"),
        ):
            with self.assertRaisesRegex(OSError, "resolver failed"):
                http_safety._getaddrinfo_with_timeout("example.test", 443, timeout_seconds=1.0)

    def test_pinned_connections_use_pinned_connector(self) -> None:
        addresses = ("93.184.216.34",)
        with mock.patch("speed_of_cinnamon.http_safety._connect_to_pinned_addresses") as connect:
            http_connection = http_safety._PinnedHTTPConnection(
                "example.test",
                pinned_addresses=addresses,
                timeout=1,
            )
            http_connection.connect()
            connect.assert_called_once_with(http_connection, addresses)

            https_connection = http_safety._PinnedHTTPSConnection(
                "example.test",
                pinned_addresses=addresses,
                timeout=1,
            )
            with mock.patch.object(https_connection._context, "wrap_socket", return_value=object()) as wrap_socket:
                https_connection.connect()
            self.assertEqual(connect.call_args.args[:2], (https_connection, addresses))
            self.assertIn("prepare_socket", connect.call_args.kwargs)

    def test_loopback_hostname_accepts_canonical_forms_and_rejects_zone_ids(self) -> None:
        self.assertTrue(http_safety.is_loopback_hostname("localhost."))
        self.assertTrue(http_safety.is_loopback_hostname("[::1]"))
        self.assertFalse(http_safety.is_loopback_hostname("[127.0.0.1]"))
        self.assertFalse(http_safety.is_loopback_hostname("fe80::1%lo"))

    def test_resolve_url_host_deduplicates_addresses(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.http_safety._getaddrinfo_with_timeout",
            return_value=[
                (0, 0, 0, "", ("93.184.216.34", 443)),
                (0, 0, 0, "", ("93.184.216.34", 443)),
                (0, 0, 0, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
            ],
        ):
            self.assertEqual(
                resolve_url_host("https://example.test", field_name="remote endpoint"),
                ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
            )

    def test_connect_to_pinned_addresses_retries_and_sets_socket(self) -> None:
        connection = mock.Mock(spec=http.client.HTTPConnection)
        connection.timeout = 2.0
        connection.port = 443
        connection.source_address = None
        pinned_socket = mock.Mock()
        with mock.patch(
            "speed_of_cinnamon.http_safety.socket.create_connection",
            side_effect=[OSError("first address unavailable"), pinned_socket],
        ) as create_connection:
            http_safety._connect_to_pinned_addresses(
                connection,
                ("203.0.113.10", "203.0.113.11"),
            )

        self.assertIs(connection.sock, pinned_socket)
        self.assertEqual(create_connection.call_args_list[0].args[0], ("203.0.113.10", 443))
        self.assertEqual(create_connection.call_args_list[1].args[0], ("203.0.113.11", 443))

    def test_connect_to_pinned_addresses_clamps_socket_timeout_to_remaining_deadline(self) -> None:
        connection = mock.Mock(spec=http.client.HTTPConnection)
        connection.timeout = 2.0
        connection.port = 443
        connection.source_address = None
        pinned_socket = mock.Mock()
        with (
            mock.patch(
                "speed_of_cinnamon.http_safety.time.monotonic",
                side_effect=[100.0, 100.25, 100.75],
            ),
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection",
                return_value=pinned_socket,
            ),
        ):
            http_safety._connect_to_pinned_addresses(connection, ("203.0.113.10",))

        pinned_socket.settimeout.assert_called_once_with(1.25)
        self.assertIs(connection.sock, pinned_socket)

    def test_connect_to_pinned_addresses_closes_socket_when_timeout_setup_fails(self) -> None:
        connection = mock.Mock(spec=http.client.HTTPConnection)
        connection.timeout = 2.0
        connection.port = 443
        connection.source_address = None
        pinned_socket = mock.Mock()
        pinned_socket.settimeout.side_effect = OSError("settimeout failed")
        with (
            mock.patch("speed_of_cinnamon.http_safety.socket.create_connection", return_value=pinned_socket),
            self.assertRaisesRegex(OSError, "pinned socket timeout could not be set"),
        ):
            http_safety._connect_to_pinned_addresses(connection, ("203.0.113.10",))

        pinned_socket.close.assert_called_once_with()

    def test_connect_to_pinned_addresses_requires_timeout(self) -> None:
        connection = mock.Mock(spec=http.client.HTTPConnection)
        connection.timeout = None
        connection.port = 443
        connection.source_address = None
        with (
            mock.patch("speed_of_cinnamon.http_safety.socket.create_connection") as create_connection,
            self.assertRaisesRegex(OSError, "pinned connection timeout is required"),
        ):
            http_safety._connect_to_pinned_addresses(connection, ("203.0.113.10",))

        create_connection.assert_not_called()

    def test_connect_to_pinned_addresses_rejects_oversized_timeout(self) -> None:
        connection = mock.Mock(spec=http.client.HTTPConnection)
        connection.timeout = http_safety.MAX_PINNED_CONNECTION_TIMEOUT_SECONDS + 1
        connection.port = 443
        connection.source_address = None
        with (
            mock.patch("speed_of_cinnamon.http_safety.socket.create_connection") as create_connection,
            self.assertRaisesRegex(OSError, "pinned connection timeout exceeds safe limit"),
        ):
            http_safety._connect_to_pinned_addresses(connection, ("203.0.113.10",))

        create_connection.assert_not_called()

    def test_connect_to_pinned_addresses_reports_last_error(self) -> None:
        connection = mock.Mock(spec=http.client.HTTPConnection)
        connection.timeout = 1.0
        connection.port = 443
        connection.source_address = None
        with mock.patch(
            "speed_of_cinnamon.http_safety.socket.create_connection",
            side_effect=OSError("address unavailable"),
        ):
            with self.assertRaisesRegex(OSError, "address unavailable"):
                http_safety._connect_to_pinned_addresses(connection, ("203.0.113.10",))

    def test_pinned_https_retries_tls_from_ipv6_to_ipv4_and_preserves_sni(self) -> None:
        first_socket = mock.Mock()
        second_socket = mock.Mock()
        tls_socket = mock.Mock()
        connection = http_safety._PinnedHTTPSConnection(
            "api.openai.com",
            pinned_addresses=("2001:db8::10", "203.0.113.10"),
            timeout=5.0,
        )
        tls_error = ssl.SSLError("handshake failed")
        with (
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection",
                side_effect=[first_socket, second_socket],
            ) as create_connection,
            mock.patch.object(
                connection._context,
                "wrap_socket",
                side_effect=[tls_error, tls_socket],
            ) as wrap_socket,
        ):
            connection.connect()

        self.assertIs(connection.sock, tls_socket)
        self.assertEqual(create_connection.call_args_list[0].args[0], ("2001:db8::10", 443))
        self.assertEqual(create_connection.call_args_list[1].args[0], ("203.0.113.10", 443))
        first_socket.close.assert_called_once_with()
        second_socket.close.assert_not_called()
        self.assertEqual(wrap_socket.call_args_list[0].kwargs["server_hostname"], "api.openai.com")
        self.assertEqual(wrap_socket.call_args_list[1].kwargs["server_hostname"], "api.openai.com")

    def test_pinned_https_propagates_last_tls_error_and_closes_each_socket(self) -> None:
        first_socket = mock.Mock()
        second_socket = mock.Mock()
        first_tls_error = ssl.SSLError("first handshake failed")
        last_tls_error = ssl.SSLError("last handshake failed")
        connection = http_safety._PinnedHTTPSConnection(
            "api.openai.com",
            pinned_addresses=("2001:db8::10", "203.0.113.10"),
            timeout=5.0,
        )
        with (
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection",
                side_effect=[first_socket, second_socket],
            ),
            mock.patch.object(
                connection._context,
                "wrap_socket",
                side_effect=[first_tls_error, last_tls_error],
            ),
            self.assertRaisesRegex(ssl.SSLError, "last handshake failed"),
        ):
            connection.connect()

        first_socket.close.assert_called_once_with()
        second_socket.close.assert_called_once_with()

    def test_pinned_https_retry_deadline_is_shared_between_addresses(self) -> None:
        first_socket = mock.Mock()
        second_socket = mock.Mock()
        tls_socket = mock.Mock()
        connection = http_safety._PinnedHTTPSConnection(
            "api.openai.com",
            pinned_addresses=("2001:db8::10", "203.0.113.10"),
            timeout=5.0,
        )
        with (
            mock.patch(
                "speed_of_cinnamon.http_safety.time.monotonic",
                side_effect=[100.0, 100.1, 100.2, 100.3, 100.4],
            ),
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection",
                side_effect=[first_socket, second_socket],
            ) as create_connection,
            mock.patch.object(
                connection._context,
                "wrap_socket",
                side_effect=[ssl.SSLError("first handshake failed"), tls_socket],
            ),
        ):
            connection.connect()

        first_timeout = create_connection.call_args_list[0].args[1]
        second_timeout = create_connection.call_args_list[1].args[1]
        self.assertLess(second_timeout, first_timeout)
        first_socket.settimeout.assert_called_once()
        second_socket.settimeout.assert_called_once()
        self.assertAlmostEqual(first_socket.settimeout.call_args.args[0], 4.8)
        self.assertAlmostEqual(second_socket.settimeout.call_args.args[0], 4.6)

    def test_pinned_http_retries_tunnel_from_first_address_to_second(self) -> None:
        first_socket = mock.Mock()
        second_socket = mock.Mock()
        connection = http_safety._PinnedHTTPConnection(
            "api.openai.com",
            pinned_addresses=("2001:db8::10", "203.0.113.10"),
            timeout=5.0,
        )
        connection.set_tunnel("proxy.example", 443)
        tunnel_error = OSError("tunnel failed")
        with (
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection",
                side_effect=[first_socket, second_socket],
            ) as create_connection,
            mock.patch.object(connection, "_tunnel", side_effect=[tunnel_error, None]) as tunnel,
        ):
            connection.connect()

        self.assertIs(connection.sock, second_socket)
        self.assertEqual(create_connection.call_args_list[0].args[0], ("2001:db8::10", 80))
        self.assertEqual(create_connection.call_args_list[1].args[0], ("203.0.113.10", 80))
        self.assertEqual(tunnel.call_count, 2)
        first_socket.close.assert_called_once_with()
        second_socket.close.assert_not_called()

    def test_pinned_http_tunnel_failures_clear_socket_and_propagate_last_error(self) -> None:
        first_socket = mock.Mock()
        second_socket = mock.Mock()
        first_tunnel_error = OSError("first tunnel failed")
        last_tunnel_error = OSError("last tunnel failed")
        connection = http_safety._PinnedHTTPConnection(
            "api.openai.com",
            pinned_addresses=("2001:db8::10", "203.0.113.10"),
            timeout=5.0,
        )
        connection.set_tunnel("proxy.example", 443)
        with (
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection",
                side_effect=[first_socket, second_socket],
            ),
            mock.patch.object(connection, "_tunnel", side_effect=[first_tunnel_error, last_tunnel_error]),
            self.assertRaisesRegex(OSError, "last tunnel failed"),
        ):
            connection.connect()

        self.assertIsNone(connection.sock)
        first_socket.close.assert_called_once_with()
        second_socket.close.assert_called_once_with()

    def test_resolve_url_host_rejects_public_result_for_loopback_hostname(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.http_safety._getaddrinfo_with_timeout",
            return_value=[(0, 0, 0, "", ("93.184.216.34", 80))],
        ):
            with self.assertRaisesRegex(UnsafeUrlError, "non-public address"):
                resolve_url_host(
                    "http://localhost",
                    field_name="local endpoint",
                    allow_loopback_host=True,
                )

    def test_resolve_url_host_accepts_loopback_result_for_loopback_hostname(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.http_safety._getaddrinfo_with_timeout",
            return_value=[(0, 0, 0, "", ("127.0.0.1", 80))],
        ):
            self.assertEqual(
                resolve_url_host(
                    "http://localhost",
                    field_name="local endpoint",
                    allow_loopback_host=True,
                ),
                ("127.0.0.1",),
            )

    def test_resolve_url_host_rejects_ipv4_multicast(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.http_safety._getaddrinfo_with_timeout",
            return_value=[(0, 0, 0, "", ("224.0.0.1", 443))],
        ):
            with self.assertRaisesRegex(UnsafeUrlError, "non-public address"):
                resolve_url_host("https://example.test", field_name="remote endpoint")

    def test_resolve_url_host_rejects_ipv6_multicast(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.http_safety._getaddrinfo_with_timeout",
            return_value=[(0, 0, 0, "", ("ff02::1", 443, 0, 0))],
        ):
            with self.assertRaisesRegex(UnsafeUrlError, "non-public address"):
                resolve_url_host("https://example.test", field_name="remote endpoint")


if __name__ == "__main__":
    unittest.main()
