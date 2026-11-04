import http.client
import http.server
import socket
import threading
import unittest
import urllib.request

from speed_of_cinnamon import transcriber


class TranscriberRedirectSecurityTest(unittest.TestCase):
    def setUp(self):
        self.authorization = "Bearer SYNTHETIC-REDIRECT-TEST-NO-KEY"
        self.body = b"SYNTHETIC-POST-BODY-NO-AUDIO"
        self.events = {"initial": [], "follow": [], "sink": []}
        self.received = {"origin": 0, "sink": 0}
        self.servers = []
        self.threads = []
        self.addCleanup(self.close_servers)
        case = self

        class Server(http.server.HTTPServer):
            def __init__(self, address, receiver):
                self.receiver = receiver
                self.barrier_address = None
                super().__init__(address, Handler)

            def get_request(self):
                request = super().get_request()
                if request[1] != self.barrier_address:
                    case.received[self.receiver] += 1
                return request

        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(2)
                super().setup()

            def log_message(self, *_args):
                # Suppress default HTTPServer stderr logging during tests.
                return

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                if (
                    self.server is case.sink
                    and self.client_address == self.server.barrier_address
                ):
                    status = (
                        204
                        if self.command == "GET" and self.path == "/__sink_barrier__"
                        else 400
                    )
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    return
                role = self.path.split("/", 2)[1]
                if role not in case.events:
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 256:
                    self.send_error(413)
                    return
                body = self.rfile.read(length) if length else b""
                if len(case.events[role]) >= 8:
                    self.send_error(429)
                    return
                case.events[role].append(
                    {
                        "receiver": self.server.receiver,
                        "method": self.command,
                        "host": self.headers.get("Host"),
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                self.send_response(case.redirect_status if role == "initial" else 200)
                if role == "initial":
                    self.send_header("Location", case.location)
                response_body = b"" if role == "initial" else b"OK"
                self.send_header("Content-Length", str(len(response_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if response_body:
                    self.wfile.write(response_body)
                self.close_connection = True

        self.sink = Server(("127.0.0.2", 0), "sink")
        self.servers.append(self.sink)
        self.origin = Server(("127.0.0.1", 0), "origin")
        self.servers.append(self.origin)
        for server in self.servers:
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.02},
            )
            self.threads.append(thread)
            thread.start()

    def close_servers(self):
        try:
            for server, thread in zip(self.servers, self.threads):
                if thread.is_alive():
                    server.shutdown()
        finally:
            for server in self.servers:
                server.server_close()
            for thread in self.threads:
                if thread.ident is not None:
                    thread.join(timeout=2)
        for thread in self.threads:
            self.assertFalse(thread.is_alive(), "HTTP server thread survived cleanup")
        for server in self.servers:
            self.assertEqual(server.fileno(), -1, "HTTP server socket survived cleanup")

    def synchronize_sink(self):
        # A reserved source address identifies only this probe, even at accept().
        # The single server thread finishes earlier connections before replying.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            probe.bind(("127.0.0.1", 0))
            self.sink.barrier_address = probe.getsockname()
            try:
                probe.connect(self.sink.server_address)
                probe.sendall(b"GET /__sink_barrier__ HTTP/1.0\r\n\r\n")
                with http.client.HTTPResponse(probe) as response:
                    response.begin()
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.read(1), b"")
            finally:
                self.sink.barrier_address = None

    def request(self, method, status, cross_origin):
        self.synchronize_sink()
        self.events = {"initial": [], "follow": [], "sink": []}
        self.received = {"origin": 0, "sink": 0}
        self.redirect_status = status
        self.location = (
            f"http://127.0.0.2:{self.sink.server_port}/sink/{method}-{status}"
            if cross_origin
            else f"/follow/{method}-{status}"
        )
        return urllib.request.Request(
            f"http://127.0.0.1:{self.origin.server_port}/initial/{method}-{status}",
            data=self.body if method == "POST" else None,
            headers={"Authorization": self.authorization},
            method=method,
        )

    def assert_initial_request(self, method):
        self.assertEqual(len(self.events["initial"]), 1)
        initial = self.events["initial"][0]
        self.assertEqual(initial["receiver"], "origin")
        self.assertEqual(initial["method"], method)
        self.assertEqual(initial["host"], f"127.0.0.1:{self.origin.server_port}")
        self.assertEqual(initial["authorization"], self.authorization)
        self.assertEqual(initial["body"], self.body if method == "POST" else b"")

    def test_cross_origin_redirects_block_authorization_and_body(self):
        for method in ("POST", "GET"):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(method=method, status=status):
                    request = self.request(method, status, cross_origin=True)
                    with self.assertRaisesRegex(
                        transcriber.TranscriptionError,
                        "redirect target changes origin",
                    ):
                        response = transcriber._open_http_request(
                            request, timeout=2, field_name="synthetic endpoint"
                        )
                        try:
                            response.read(65)
                        finally:
                            response.close()
                    self.synchronize_sink()
                    self.assert_initial_request(method)
                    self.assertEqual(self.received, {"origin": 1, "sink": 0})
                    self.assertEqual(self.events["follow"], [])
                    self.assertEqual(self.events["sink"], [])

    def test_same_origin_get_redirects_keep_authorization(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                request = self.request("GET", status, cross_origin=False)
                response = transcriber._open_http_request(
                    request, timeout=2, field_name="synthetic endpoint"
                )
                try:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(65), b"OK")
                finally:
                    response.close()
                self.synchronize_sink()
                self.assert_initial_request("GET")
                self.assertEqual(self.received, {"origin": 2, "sink": 0})
                self.assertEqual(len(self.events["follow"]), 1)
                self.assertEqual(
                    self.events["follow"][0],
                    {
                        "receiver": "origin",
                        "method": "GET",
                        "host": f"127.0.0.1:{self.origin.server_port}",
                        "authorization": self.authorization,
                        "body": b"",
                    },
                )
                self.assertEqual(self.events["sink"], [])


if __name__ == "__main__":
    unittest.main()
