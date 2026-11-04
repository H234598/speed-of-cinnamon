from __future__ import annotations

import http.client
import multiprocessing
import socket
import ssl
import threading
import time
import unittest
import warnings
from unittest import mock

from speed_of_cinnamon import http_safety, transcriber
from speed_of_cinnamon.http_safety import UnsafeUrlError, resolve_url_host


def _blocked_dns_worker(hostname, port, connection, ready):
    def blocked_resolution(*_args, **_kwargs):
        ready.set()
        time.sleep(60)
        return []

    with mock.patch.object(http_safety.socket, "getaddrinfo", blocked_resolution):
        http_safety._resolve_dns_in_process(hostname, port, connection)


def _error_dns_worker(hostname, port, connection):
    with mock.patch.object(
        http_safety.socket, "getaddrinfo", side_effect=OSError("resolver failed")
    ):
        http_safety._resolve_dns_in_process(hostname, port, connection)


class HttpSafetyTest(unittest.TestCase):
    def dns_context_double(self):
        context = mock.Mock()
        worker = context.Process.return_value
        worker.pid = 123
        worker.is_alive.return_value = False
        result_connection = mock.Mock()
        child_connection = mock.Mock()
        context.Pipe.return_value = (result_connection, child_connection)
        result_connection.recv.return_value = ("result", [])
        slots = mock.Mock(wraps=threading.BoundedSemaphore(1))
        for name, value in (
            ("_DNS_RESOLUTION_CONTEXT", context),
            ("_DNS_RESOLUTION_SLOTS", slots),
        ):
            patcher = mock.patch.object(http_safety, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return context, worker, result_connection, child_connection, slots

    def test_dns_context_selects_only_local_spawn_and_caches_it(self) -> None:
        default_method = multiprocessing.get_start_method(allow_none=True)
        with (
            mock.patch.object(http_safety, "_DNS_RESOLUTION_CONTEXT", None),
            mock.patch.object(multiprocessing, "get_context") as get_context,
        ):
            self.assertIs(
                http_safety._dns_resolution_context(), get_context.return_value
            )
            self.assertIs(
                http_safety._dns_resolution_context(), get_context.return_value
            )
            get_context.assert_called_once_with("spawn")
        self.assertEqual(
            multiprocessing.get_start_method(allow_none=True), default_method
        )

    def test_dns_context_does_not_fall_back_when_spawn_is_unavailable(self) -> None:
        with (
            mock.patch.object(http_safety, "_DNS_RESOLUTION_CONTEXT", None),
            mock.patch.object(
                multiprocessing, "get_context", side_effect=ValueError("no spawn")
            ) as get_context,
            self.assertRaisesRegex(ValueError, "no spawn"),
        ):
            http_safety._dns_resolution_context()
        get_context.assert_called_once_with("spawn")

    def test_real_dns_spawn_from_multithreaded_caller_has_no_warnings(self) -> None:
        ready = threading.Event()
        stop = threading.Event()

        def wait_in_thread():
            ready.set()
            stop.wait()

        thread = threading.Thread(target=wait_in_thread)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            with (
                mock.patch.object(http_safety, "_DNS_RESOLUTION_CONTEXT", None),
                warnings.catch_warnings(record=True) as caught,
            ):
                warnings.simplefilter("always")
                resolved = http_safety._getaddrinfo_with_timeout(
                    "127.0.0.1", 443, timeout_seconds=5
                )
                self.assertEqual(
                    http_safety._dns_resolution_context().get_start_method(), "spawn"
                )
            self.assertEqual(caught, [])
            self.assertEqual(
                resolved,
                socket.getaddrinfo("127.0.0.1", 443, type=socket.SOCK_STREAM),
            )
            self.assertFalse(
                any(
                    p.name == "speed-of-cinnamon-dns"
                    for p in multiprocessing.active_children()
                )
            )
        finally:
            stop.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_open_http_request_caps_long_dns_budget(self) -> None:
        request = mock.Mock()
        request.get_full_url.return_value = "https://example.test"
        with (
            mock.patch.object(transcriber, "_validate_http_request"),
            mock.patch.object(
                transcriber, "resolve_url_host", return_value=("93.184.216.34",)
            ) as resolver,
            mock.patch.object(
                transcriber.time, "monotonic", side_effect=[100.0, 101.0, 102.0]
            ),
            mock.patch.object(
                transcriber.urllib.request, "build_opener"
            ) as build_opener,
        ):
            transcriber._open_http_request(
                request, timeout=900, field_name="remote endpoint"
            )

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
            mock.patch.object(
                transcriber, "resolve_url_host", return_value=("93.184.216.34",)
            ) as resolver,
            mock.patch.object(
                transcriber.time, "monotonic", side_effect=[100.0, 100.5, 100.75]
            ),
            mock.patch.object(
                transcriber.urllib.request, "build_opener"
            ) as build_opener,
        ):
            transcriber._open_http_request(
                request, timeout=1, field_name="remote endpoint"
            )

        self.assertEqual(resolver.call_args.kwargs["timeout_seconds"], 0.5)
        build_opener.return_value.open.assert_called_once_with(request, timeout=0.25)

    def test_open_http_request_rejects_nonfinite_dns_budget(self) -> None:
        request = mock.Mock()
        request.get_full_url.return_value = "https://example.test"
        with (
            mock.patch.object(transcriber, "_validate_http_request"),
            mock.patch.object(transcriber, "resolve_url_host") as resolver,
            mock.patch.object(
                transcriber.time, "monotonic", side_effect=[100.0, float("nan")]
            ),
        ):
            with self.assertRaisesRegex(
                transcriber.TranscriptionError, "request timed out"
            ):
                transcriber._open_http_request(
                    request, timeout=900, field_name="remote endpoint"
                )

        resolver.assert_not_called()

    def test_open_http_request_rejects_nonfinite_budget_after_dns(self) -> None:
        request = mock.Mock()
        request.get_full_url.return_value = "https://example.test"
        with (
            mock.patch.object(transcriber, "_validate_http_request"),
            mock.patch.object(
                transcriber, "resolve_url_host", return_value=("93.184.216.34",)
            ) as resolver,
            mock.patch.object(
                transcriber.time, "monotonic", side_effect=[100.0, 101.0, float("nan")]
            ),
            mock.patch.object(
                transcriber.urllib.request, "build_opener"
            ) as build_opener,
        ):
            with self.assertRaisesRegex(
                transcriber.TranscriptionError, "request timed out"
            ):
                transcriber._open_http_request(
                    request, timeout=900, field_name="remote endpoint"
                )

        resolver.assert_called_once()
        build_opener.assert_not_called()

    def test_dns_resolution_rejects_unrepresentable_timeout(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "deadline expired"):
            http_safety._getaddrinfo_with_timeout(
                "example.test", 443, timeout_seconds=10**1000
            )

    def test_dns_resolution_rejects_timeout_above_safe_limit(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "exceeds safe limit"):
            http_safety._getaddrinfo_with_timeout(
                "example.test",
                443,
                timeout_seconds=http_safety.MAX_DNS_RESOLUTION_TIMEOUT_SECONDS + 1,
            )

    def test_resolve_url_host_rejects_timeout_above_safe_limit(self) -> None:
        with mock.patch.object(http_safety, "_getaddrinfo_with_timeout") as resolver:
            with self.assertRaisesRegex(
                UnsafeUrlError, "hostname resolution timed out"
            ):
                resolve_url_host(
                    "https://example.test",
                    field_name="remote endpoint",
                    timeout_seconds=10**1000,
                )
        resolver.assert_not_called()

    def test_dns_resolution_fails_closed_when_worker_survives_kill(self) -> None:
        context, worker, result_connection, child_connection, slots = (
            self.dns_context_double()
        )
        worker.is_alive.return_value = True

        with mock.patch.object(http_safety, "_DNS_RESOLUTION_CONTEXT", context):
            with self.assertRaisesRegex(TimeoutError, "could not be stopped"):
                http_safety._getaddrinfo_with_timeout(
                    "example.test", 443, timeout_seconds=0.01
                )

        worker.terminate.assert_called_once_with()
        worker.kill.assert_called_once_with()
        self.assertEqual(worker.join.call_count, 3)
        result_connection.close.assert_called_once_with()
        child_connection.close.assert_called_once_with()
        worker.close.assert_not_called()
        slots.release.assert_not_called()
        with self.assertRaisesRegex(TimeoutError, "resolver is busy"):
            http_safety._getaddrinfo_with_timeout(
                "example.test", 443, timeout_seconds=0.01
            )
        self.assertEqual(context.Process.call_count, 1)

    def test_dns_resolution_timeout_releases_worker(self) -> None:
        spawn = multiprocessing.get_context("spawn")
        ready = spawn.Event()
        context = mock.Mock(wraps=spawn)
        workers = []

        def make_worker(**kwargs):
            kwargs["target"] = _blocked_dns_worker
            kwargs["args"] += (ready,)
            worker = spawn.Process(**kwargs)
            workers.append(worker)
            return worker

        join = spawn.Process.join

        def join_after_ready(worker, timeout):
            if timeout == 0.01:
                self.assertTrue(ready.wait(5), "spawned resolver did not start")
                ready.clear()
            join(worker, timeout)

        context.Process.side_effect = make_worker
        with (
            mock.patch.object(http_safety, "_DNS_RESOLUTION_CONTEXT", context),
            mock.patch.object(spawn.Process, "join", join_after_ready),
        ):
            for _ in range(http_safety._DNS_RESOLUTION_MAX_IN_FLIGHT):
                with self.assertRaisesRegex(TimeoutError, "deadline expired"):
                    http_safety._getaddrinfo_with_timeout(
                        "127.0.0.1", 443, timeout_seconds=0.01
                    )
                self.assertTrue(workers[-1]._closed)

        self.assertEqual(
            http_safety._getaddrinfo_with_timeout("127.0.0.1", 443, timeout_seconds=5),
            socket.getaddrinfo("127.0.0.1", 443, type=socket.SOCK_STREAM),
        )
        self.assertFalse(
            any(
                p.name == "speed-of-cinnamon-dns"
                for p in multiprocessing.active_children()
            )
        )

    def test_dns_resolution_propagates_resolver_error(self) -> None:
        spawn = multiprocessing.get_context("spawn")
        context = mock.Mock(wraps=spawn)

        def make_worker(**kwargs):
            kwargs["target"] = _error_dns_worker
            return spawn.Process(**kwargs)

        context.Process.side_effect = make_worker
        with mock.patch.object(http_safety, "_DNS_RESOLUTION_CONTEXT", context):
            with self.assertRaisesRegex(OSError, "resolver failed"):
                http_safety._getaddrinfo_with_timeout(
                    "127.0.0.1", 443, timeout_seconds=5
                )

    def test_dns_construction_errors_release_slot_and_close_created_pipe(self) -> None:
        context, worker, receiver, sender, slots = self.dns_context_double()
        for stage in ("context", "Pipe", "Process"):
            with self.subTest(stage=stage):
                context.reset_mock(side_effect=True)
                slots.reset_mock()
                receiver.reset_mock()
                sender.reset_mock()
                with mock.patch.object(
                    http_safety, "_dns_resolution_context"
                ) as get_context:
                    get_context.return_value = context
                    failing = (
                        get_context if stage == "context" else getattr(context, stage)
                    )
                    failing.side_effect = OSError("construction failed")
                    with self.assertRaisesRegex(OSError, "construction failed"):
                        http_safety._getaddrinfo_with_timeout(
                            "example.test", 443, timeout_seconds=0.01
                        )
                slots.release.assert_called_once_with()
                worker.start.assert_not_called()
                self.assertEqual(receiver.close.call_count, int(stage == "Process"))
                self.assertEqual(sender.close.call_count, int(stage == "Process"))

    def test_dns_start_failure_closes_unstarted_process_and_releases_slot(self) -> None:
        _context, worker, receiver, sender, slots = self.dns_context_double()
        worker.pid = None
        worker.start.side_effect = OSError("start failed")
        with self.assertRaisesRegex(OSError, "start failed"):
            http_safety._getaddrinfo_with_timeout(
                "example.test", 443, timeout_seconds=0.01
            )
        worker.join.assert_not_called()
        worker.terminate.assert_not_called()
        worker.close.assert_called_once_with()
        receiver.close.assert_called_once_with()
        sender.close.assert_called_once_with()
        slots.release.assert_called_once_with()

    def test_dns_abort_after_start_stops_worker_before_releasing_slot(self) -> None:
        for stage in ("start", "join", "pipe_close"):
            with self.subTest(stage=stage):
                _context, worker, receiver, sender, slots = self.dns_context_double()
                worker.is_alive.return_value = True
                failure = KeyboardInterrupt("interrupted")
                if stage == "pipe_close":
                    sender.close.side_effect = [failure, None]
                else:
                    getattr(worker, stage).side_effect = [failure, None]

                def confirm_exit(timeout):
                    self.assertEqual(timeout, 0.5)
                    slots.release.assert_not_called()
                    worker.is_alive.return_value = False

                if stage == "join":
                    worker.join.side_effect = [failure, None]
                    worker.terminate.side_effect = lambda: setattr(
                        worker.is_alive, "return_value", False
                    )
                else:
                    worker.join.side_effect = confirm_exit
                with self.assertRaisesRegex(KeyboardInterrupt, "interrupted"):
                    http_safety._getaddrinfo_with_timeout(
                        "example.test", 443, timeout_seconds=0.01
                    )
                worker.terminate.assert_called_once_with()
                worker.close.assert_called_once_with()
                receiver.close.assert_called_once_with()
                slots.release.assert_called_once_with()

    def test_dns_cleanup_escalates_even_when_terminate_or_join_raises(self) -> None:
        for stage in ("terminate", "join"):
            with self.subTest(stage=stage):
                _context, worker, receiver, _sender, slots = self.dns_context_double()
                worker.is_alive.return_value = True
                getattr(worker, stage).side_effect = OSError("cleanup failed")
                if stage == "join":
                    worker.join.side_effect = [None, OSError("cleanup failed"), None]
                worker.kill.side_effect = lambda: setattr(
                    worker.is_alive, "return_value", False
                )
                with self.assertRaisesRegex(OSError, "cleanup failed"):
                    http_safety._getaddrinfo_with_timeout(
                        "example.test", 443, timeout_seconds=0.01
                    )
                worker.terminate.assert_called_once_with()
                worker.kill.assert_called_once_with()
                self.assertEqual(worker.join.call_args_list[-2:], [mock.call(0.5)] * 2)
                worker.close.assert_called_once_with()
                receiver.close.assert_called_once_with()
                slots.release.assert_called_once_with()

    def test_dns_handle_or_pipe_close_errors_do_not_leak_slot(self) -> None:
        for stage in ("handle", "pipe"):
            with self.subTest(stage=stage):
                _context, worker, receiver, sender, slots = self.dns_context_double()
                failing = worker if stage == "handle" else receiver
                failing.close.side_effect = OSError("close failed")
                with self.assertRaisesRegex(OSError, "close failed"):
                    http_safety._getaddrinfo_with_timeout(
                        "example.test", 443, timeout_seconds=0.01
                    )
                worker.close.assert_called_once_with()
                sender.close.assert_called_once_with()
                receiver.close.assert_called_once_with()
                slots.release.assert_called_once_with()

    def test_dns_success_closes_process_handle_and_releases_slot(self) -> None:
        _context, worker, receiver, sender, slots = self.dns_context_double()
        self.assertEqual(
            http_safety._getaddrinfo_with_timeout(
                "example.test", 443, timeout_seconds=0.01
            ),
            [],
        )
        worker.close.assert_called_once_with()
        sender.close.assert_called_once_with()
        receiver.close.assert_called_once_with()
        slots.release.assert_called_once_with()

    def test_dns_resolution_fails_closed_when_worker_exits_without_result(self) -> None:
        _context, worker, receiver, _sender, slots = self.dns_context_double()
        receiver.poll.return_value = False
        with self.assertRaisesRegex(OSError, "exited without result"):
            http_safety._getaddrinfo_with_timeout(
                "example.test", 443, timeout_seconds=0.01
            )
        worker.close.assert_called_once_with()
        slots.release.assert_called_once_with()

    def test_pinned_connections_use_pinned_connector(self) -> None:
        addresses = ("93.184.216.34",)
        with mock.patch(
            "speed_of_cinnamon.http_safety._connect_to_pinned_addresses"
        ) as connect:
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
            with mock.patch.object(
                https_connection._context, "wrap_socket", return_value=object()
            ):
                https_connection.connect()
            self.assertEqual(connect.call_args.args[:2], (https_connection, addresses))
            self.assertIn("prepare_socket", connect.call_args.kwargs)

    def test_loopback_hostname_accepts_canonical_forms_and_rejects_zone_ids(
        self,
    ) -> None:
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
        self.assertEqual(
            create_connection.call_args_list[0].args[0], ("203.0.113.10", 443)
        )
        self.assertEqual(
            create_connection.call_args_list[1].args[0], ("203.0.113.11", 443)
        )

    def test_connect_to_pinned_addresses_clamps_socket_timeout_to_remaining_deadline(
        self,
    ) -> None:
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

    def test_connect_to_pinned_addresses_closes_socket_when_timeout_setup_fails(
        self,
    ) -> None:
        connection = mock.Mock(spec=http.client.HTTPConnection)
        connection.timeout = 2.0
        connection.port = 443
        connection.source_address = None
        pinned_socket = mock.Mock()
        pinned_socket.settimeout.side_effect = OSError("settimeout failed")
        with (
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection",
                return_value=pinned_socket,
            ),
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
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection"
            ) as create_connection,
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
            mock.patch(
                "speed_of_cinnamon.http_safety.socket.create_connection"
            ) as create_connection,
            self.assertRaisesRegex(
                OSError, "pinned connection timeout exceeds safe limit"
            ),
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
        self.assertEqual(
            create_connection.call_args_list[0].args[0], ("2001:db8::10", 443)
        )
        self.assertEqual(
            create_connection.call_args_list[1].args[0], ("203.0.113.10", 443)
        )
        first_socket.close.assert_called_once_with()
        second_socket.close.assert_not_called()
        self.assertEqual(
            wrap_socket.call_args_list[0].kwargs["server_hostname"], "api.openai.com"
        )
        self.assertEqual(
            wrap_socket.call_args_list[1].kwargs["server_hostname"], "api.openai.com"
        )

    def test_pinned_https_propagates_last_tls_error_and_closes_each_socket(
        self,
    ) -> None:
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
            mock.patch.object(
                connection, "_tunnel", side_effect=[tunnel_error, None]
            ) as tunnel,
        ):
            connection.connect()

        self.assertIs(connection.sock, second_socket)
        self.assertEqual(
            create_connection.call_args_list[0].args[0], ("2001:db8::10", 80)
        )
        self.assertEqual(
            create_connection.call_args_list[1].args[0], ("203.0.113.10", 80)
        )
        self.assertEqual(tunnel.call_count, 2)
        first_socket.close.assert_called_once_with()
        second_socket.close.assert_not_called()

    def test_pinned_http_tunnel_failures_clear_socket_and_propagate_last_error(
        self,
    ) -> None:
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
            mock.patch.object(
                connection,
                "_tunnel",
                side_effect=[first_tunnel_error, last_tunnel_error],
            ),
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

    def test_resolve_url_host_accepts_loopback_result_for_loopback_hostname(
        self,
    ) -> None:
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
