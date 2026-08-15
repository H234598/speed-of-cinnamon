from __future__ import annotations

import unittest
import http.client
import time
from unittest import mock

from speed_of_cinnamon import http_safety
from speed_of_cinnamon.http_safety import UnsafeUrlError, resolve_url_host


class HttpSafetyTest(unittest.TestCase):
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
            connect.assert_called_with(https_connection, addresses)
            wrap_socket.assert_called_once()

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
        pinned_socket = object()
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
