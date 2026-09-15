from __future__ import annotations

import ast
from contextlib import contextmanager
import ctypes
import errno
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import venv
from unittest import mock

from speed_of_cinnamon import remote_http
from speed_of_cinnamon.remote_http import (
    LISTING_DEADLINE_NS,
    MAX_ERROR_FRAME_BYTES,
    MAX_MODEL_LIST_ENTRIES,
    MAX_REQUEST_FRAME_BYTES,
    MAX_RESPONSE_FRAME_BYTES,
    MAX_URL_BYTES,
    MAX_URL_CHARS,
    LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
    POSTPROCESS_DEADLINE_NS,
    POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
    POSTPROCESS_OLLAMA_OPERATION,
    RemoteProtocolError,
    SUPERVISOR_ERROR_CODES,
    WORKER_ERROR_CODES,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)


NOW = 10_000_000_000
NONCE = "0123456789abcdef0123456789abcdef"


def _request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "deadline_monotonic_ns": NOW + LISTING_DEADLINE_NS,
        "nonce": NONCE,
        "operation": "list-ollama-models",
        "payload": {"url": "http://127.0.0.1:11434"},
        "schema_version": 1,
    }
    value.update(overrides)
    return value


def _openai_request(
    *,
    api_key: str = "",
    **overrides: object,
) -> dict[str, object]:
    value = _request(
        operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
        payload={"url": "http://127.0.0.1:11434", "api_key": api_key},
    )
    value.update(overrides)
    return value


def _postprocess_request(
    *,
    text: str = "Transcript: hello world",
    language: str = "en",
    model: str = "llama3.2:3b",
    personal_context: str = "",
    prompt: str = "",
    url: str = "http://127.0.0.1:11434",
    vocabulary: str = "",
    **overrides: object,
) -> dict[str, object]:
    value = _request(
        deadline_monotonic_ns=NOW + POSTPROCESS_DEADLINE_NS,
        operation=POSTPROCESS_OLLAMA_OPERATION,
        payload={
            "language": language,
            "model": model,
            "personal_context": personal_context,
            "prompt": prompt,
            "text": text,
            "url": url,
            "vocabulary": vocabulary,
        },
    )
    value.update(overrides)
    return value


def _openai_postprocess_request(
    *,
    api_key: str = "secret-token",
    flex_processing: bool = True,
    service_tier_fallback: bool = False,
    **overrides: object,
) -> dict[str, object]:
    value = _request(
        deadline_monotonic_ns=NOW + POSTPROCESS_DEADLINE_NS,
        operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        payload={
            "api_key": api_key,
            "flex_processing": flex_processing,
            "language": "en",
            "model": "gpt-5.6-luna",
            "personal_context": "",
            "prompt": "",
            "service_tier_fallback": service_tier_fallback,
            "text": "Transcript: hello world",
            "url": "https://api.openai.com/v1",
            "vocabulary": "",
        },
    )
    value.update(overrides)
    return value


def _model(name: str = "llama3.2:3b", **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": name,
        "model": name,
        "modified_at": "2026-06-01T09:00:00Z",
        "size": 2_016_000_000,
        "size_label": "1.9 GiB",
        "digest": "abc",
        "family": "llama",
        "parameter_size": "3.2B",
        "quantization": "Q4_K_M",
        "description": "llama 3.2B Q4_K_M",
    }
    value.update(overrides)
    return value


def _success(models: list[dict[str, object]], state: str = "listed") -> dict[str, object]:
    return {
        "nonce": NONCE,
        "result": {"listing_state": state, "models": models},
        "schema_version": 1,
        "status": "ok",
    }


def _openai_success(
    models: list[dict[str, object]],
    state: str = "listed",
) -> dict[str, object]:
    return {
        "nonce": NONCE,
        "result": {"listing_state": state, "models": models},
        "schema_version": 1,
        "status": "ok",
    }


def _postprocess_success(text: str) -> dict[str, object]:
    return {
        "nonce": NONCE,
        "result": {"text": text},
        "schema_version": 1,
        "status": "ok",
    }


def _openai_model(name: str) -> dict[str, object]:
    return {"name": name, "model": name}


def _raw_frame(body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + body


def _raw_request_json(**replacements: str) -> bytes:
    fields = {
        "deadline": str(NOW + LISTING_DEADLINE_NS),
        "nonce": json.dumps(NONCE),
        "operation": json.dumps("list-ollama-models"),
        "payload": '{"url":"http://127.0.0.1:11434"}',
        "schema": "1",
    }
    fields.update(replacements)
    return (
        "{"
        f'"deadline_monotonic_ns":{fields["deadline"]},'
        f'"nonce":{fields["nonce"]},'
        f'"operation":{fields["operation"]},'
        f'"payload":{fields["payload"]},'
        f'"schema_version":{fields["schema"]}'
        "}"
    ).encode("ascii")


@contextmanager
def _isolated_worker_python():
    source_package = Path(__file__).resolve().parents[1] / "src" / "speed_of_cinnamon"
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        venv_root = temporary_root / "venv"
        venv.EnvBuilder(with_pip=False, clear=True).create(venv_root)
        site_packages = next((venv_root / "lib").glob("python*/site-packages"))
        shutil.copytree(source_package, site_packages / "speed_of_cinnamon")
        yield venv_root / "bin" / "python"


def _start_test_child(
    source: str,
    *,
    pass_fds: tuple[int, ...] = (),
) -> remote_http._WorkerHandle:
    control_parent, control_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", source],
            stdin=control_child.fileno(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except BaseException:
        control_parent.close()
        control_child.close()
        raise
    control_child.close()
    process.stdin = control_parent
    try:
        pidfd = os.pidfd_open(process.pid, 0)
        start_time = remote_http._process_start_time(process.pid)
        if not start_time:
            raise OSError
        os.set_inheritable(pidfd, False)
    except (AttributeError, OSError, TypeError, ValueError):
        process.kill()
        process.wait()
        control_parent.close()
        raise unittest.SkipTest("pidfd or /proc process identity unavailable")
    return remote_http._WorkerHandle(process, process.pid, start_time, pidfd)


def _pidfd_getfd(pidfd: int, target_fd: int) -> int:
    if os.uname().machine not in {"x86_64", "amd64"}:
        raise OSError(errno.ENOSYS, "pidfd_getfd test is x86_64-only")
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(438, pidfd, target_fd, 0)
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return int(result)


def _dispose_test_child(handle: remote_http._WorkerHandle) -> None:
    remote_http._cleanup_worker(
        handle,
        time.monotonic_ns() + 2_000_000_000,
        time.monotonic_ns,
    )
    if handle.process.poll() is None:
        handle.process.kill()
        handle.process.wait()


def _output_script(*, chunks: tuple[bytes, ...], pauses: tuple[float, ...] = (), stderr: bytes = b"", hold: float = 0.0) -> str:
    return (
        "import os, time\n"
        f"chunks={chunks!r}\n"
        f"pauses={pauses!r}\n"
        f"stderr={stderr!r}\n"
        f"hold={hold!r}\n"
        "if stderr: os.write(2, stderr)\n"
        "for index, chunk in enumerate(chunks):\n"
        "    os.write(1, chunk)\n"
        "    if index < len(pauses): time.sleep(pauses[index])\n"
        "if hold: time.sleep(hold)\n"
    )


class _BlockingStream:
    def __init__(self) -> None:
        self.read_calls = 0

    def read(self, _size: int = -1) -> bytes:
        self.read_calls += 1
        raise AssertionError("stream must not be read by bytes-only codec")


class RemoteHttpCodecTest(unittest.TestCase):
    def assertProtocolCode(self, callable_object: object, code: str) -> None:
        with self.assertRaises(RemoteProtocolError) as context:
            callable_object()  # type: ignore[operator]
        self.assertEqual(context.exception.code, code)
        self.assertEqual(context.exception.__cause__, None)
        self.assertEqual(context.exception.__context__, None)
        self.assertNotIn("sentinel", repr(context.exception))

    def test_valid_request_roundtrips_canonically(self) -> None:
        requests = [
            _request(),
            _openai_request(api_key="  secret-token  "),
        ]
        for request in requests:
            for deadline in (NOW + 1, NOW + LISTING_DEADLINE_NS):
                request = dict(request)
                request["deadline_monotonic_ns"] = deadline
                with self.subTest(operation=request["operation"], deadline=deadline):
                    frame = encode_request(request, monotonic_ns=lambda: NOW)
                    body = frame[4:]
                    expected_request = dict(request)
                    if expected_request["operation"] == LIST_OPENAI_COMPATIBLE_MODELS_OPERATION:
                        expected_request["payload"] = {
                            "url": "http://127.0.0.1:11434",
                            "api_key": "secret-token",
                        }
                    self.assertEqual(frame[:4], struct.pack(">I", len(body)))
                    self.assertEqual(
                        body,
                        json.dumps(
                            expected_request,
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("ascii"),
                    )
                    self.assertEqual(
                        decode_request(frame, monotonic_ns=lambda: NOW),
                        expected_request,
                    )

    def test_postprocess_request_and_response_codec_bind_schema_and_deadline(self) -> None:
        request = _postprocess_request()
        frame = encode_request(request, monotonic_ns=lambda: NOW)
        self.assertEqual(decode_request(frame, monotonic_ns=lambda: NOW), request)
        response = _postprocess_success("hello world")
        response_frame = encode_response(response, operation=POSTPROCESS_OLLAMA_OPERATION)
        self.assertEqual(
            decode_response(
                response_frame,
                expected_nonce=NONCE,
                operation=POSTPROCESS_OLLAMA_OPERATION,
            ),
            response,
        )
        self.assertProtocolCode(
            lambda: encode_request(
                _postprocess_request(
                    deadline_monotonic_ns=NOW + POSTPROCESS_DEADLINE_NS + 1,
                ),
                monotonic_ns=lambda: NOW,
            ),
            "remote-request-invalid",
        )
        self.assertProtocolCode(
            lambda: encode_request(
                _request(deadline_monotonic_ns=NOW + LISTING_DEADLINE_NS + 1),
                monotonic_ns=lambda: NOW,
            ),
            "remote-request-invalid",
        )
        for wrong_operation in ("list-ollama-models", LIST_OPENAI_COMPATIBLE_MODELS_OPERATION):
            with self.subTest(wrong_operation=wrong_operation):
                wrong_response = encode_response(
                    response,
                    operation=POSTPROCESS_OLLAMA_OPERATION,
                )
                self.assertProtocolCode(
                    lambda wrong_operation=wrong_operation: decode_response(
                        wrong_response,
                        expected_nonce=NONCE,
                        operation=wrong_operation,
                    ),
                    "remote-worker-protocol-invalid",
                )

    def test_openai_postprocess_codec_binds_payload_response_and_deadline(self) -> None:
        request = _openai_postprocess_request(api_key="  secret-token  ")
        frame = encode_request(request, monotonic_ns=lambda: NOW)
        expected_request = dict(request)
        expected_payload = dict(expected_request["payload"])  # type: ignore[arg-type]
        expected_payload["api_key"] = "secret-token"
        expected_request["payload"] = expected_payload
        self.assertEqual(
            decode_request(frame, monotonic_ns=lambda: NOW),
            expected_request,
        )
        self.assertEqual(
            decode_request(
                encode_request(
                    _openai_postprocess_request(
                        deadline_monotonic_ns=NOW + LISTING_DEADLINE_NS + 1,
                    ),
                    monotonic_ns=lambda: NOW,
                ),
                monotonic_ns=lambda: NOW,
            )["operation"],
            POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        )
        response = _postprocess_success("processed")
        response_frame = encode_response(
            response,
            operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        )
        self.assertEqual(
            decode_response(
                response_frame,
                expected_nonce=NONCE,
                operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
            ),
            response,
        )
        self.assertProtocolCode(
            lambda: encode_request(
                _openai_postprocess_request(
                    deadline_monotonic_ns=NOW + POSTPROCESS_DEADLINE_NS + 1,
                ),
                monotonic_ns=lambda: NOW,
            ),
            "remote-request-invalid",
        )
        for wrong_operation in (POSTPROCESS_OLLAMA_OPERATION, LIST_OPENAI_COMPATIBLE_MODELS_OPERATION):
            with self.subTest(wrong_operation=wrong_operation):
                self.assertProtocolCode(
                    lambda wrong_operation=wrong_operation: decode_response(
                        response_frame,
                        expected_nonce=NONCE,
                        operation=wrong_operation,
                    ),
                    "remote-worker-protocol-invalid",
                )

    def test_openai_postprocess_payload_exact_fields_types_and_limits_fail_closed(self) -> None:
        valid_payload = _openai_postprocess_request()["payload"]
        assert type(valid_payload) is dict
        missing = dict(valid_payload)
        missing.pop("service_tier_fallback")
        extra = dict(valid_payload, extra="x")
        hostile = [
            {"payload": missing},
            {"payload": extra},
            {"payload": dict(valid_payload, api_key=True)},
            {"payload": dict(valid_payload, api_key="k" * 4_097)},
            {"payload": dict(valid_payload, flex_processing=1)},
            {"payload": dict(valid_payload, service_tier_fallback=1)},
            {"payload": dict(valid_payload, model=True)},
            {"payload": dict(valid_payload, model="text-embedding-3-small")},
            {"payload": dict(valid_payload, model="gpt-3.5-turbo-instruct")},
            {"payload": dict(valid_payload, prompt="p" * 4_097)},
            {"payload": dict(valid_payload, text="t" * 1_000_001)},
            {"payload": dict(valid_payload, url="https://api.openai.com/v1?unsafe=1")},
            {"payload": dict(valid_payload, url="https://api.openai.com/" + "u" * 2_040)},
            {"payload": dict(valid_payload, language=True)},
            {"payload": dict(valid_payload, personal_context="c" * 65_536)},
            {"payload": dict(valid_payload, vocabulary="v" * 65_536)},
            {"payload": dict(valid_payload, vocabulary="\n".join("v" for _ in range(4_097)))},
        ]
        for replacement in hostile:
            with self.subTest(replacement=replacement):
                request = _openai_postprocess_request(payload=replacement["payload"])
                self.assertProtocolCode(
                    lambda request=request: encode_request(request, monotonic_ns=lambda: NOW),
                    "remote-request-invalid",
                )

    def test_openai_postprocess_api_key_is_trimmed_and_empty_allowed(self) -> None:
        for api_key, expected in (("  secret  ", "secret"), ("", ""), ("   ", "")):
            with self.subTest(api_key=repr(api_key)):
                decoded = decode_request(
                    encode_request(
                        _openai_postprocess_request(api_key=api_key),
                        monotonic_ns=lambda: NOW,
                    ),
                    monotonic_ns=lambda: NOW,
                )
                self.assertEqual(decoded["payload"]["api_key"], expected)  # type: ignore[index]

    def test_openai_postprocess_response_rejects_api_key_in_public_text(self) -> None:
        frame = encode_response(
            _postprocess_success("contains secret-token"),
            operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        )
        self.assertProtocolCode(
            lambda: decode_response(
                frame,
                expected_nonce=NONCE,
                operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
                secret="secret-token",
            ),
            "remote-worker-protocol-invalid",
        )

    def test_postprocess_payload_exact_fields_types_and_limits_fail_closed(self) -> None:
        valid_payload = _postprocess_request()["payload"]
        assert type(valid_payload) is dict
        missing = dict(valid_payload)
        missing.pop("vocabulary")
        extra = dict(valid_payload, extra="x")
        hostile = [
            {"payload": missing},
            {"payload": extra},
            {"payload": dict(valid_payload, text=True)},
            {"payload": dict(valid_payload, model=" " * 240)},
            {"payload": dict(valid_payload, model="m" * 241)},
            {"payload": dict(valid_payload, prompt="p" * 4_097)},
            {"payload": dict(valid_payload, text="t" * 1_000_001)},
            {"payload": dict(valid_payload, url="http://127.0.0.1/" + "u" * 2_040)},
            {"payload": dict(valid_payload, language="de\n")},
            {"payload": dict(valid_payload, personal_context="c" * 65_536)},
            {"payload": dict(valid_payload, vocabulary="v" * 65_536)},
            {"payload": dict(valid_payload, vocabulary="\n".join("v" for _ in range(4_097)))},
        ]
        for replacement in hostile:
            with self.subTest(replacement=replacement):
                request = _postprocess_request(payload=replacement["payload"])
                self.assertProtocolCode(
                    lambda request=request: encode_request(request, monotonic_ns=lambda: NOW),
                    "remote-request-invalid",
                )
        for result in (
            {"text": ""},
            {"text": "x" * 1_000_001},
            {"text": True},
            {"text": "ok", "extra": "x"},
        ):
            with self.subTest(result=result):
                self.assertProtocolCode(
                    lambda result=result: encode_response(
                        {
                            "nonce": NONCE,
                            "result": result,
                            "schema_version": 1,
                            "status": "ok",
                        },
                        operation=POSTPROCESS_OLLAMA_OPERATION,
                    ),
                    "remote-worker-protocol-invalid",
                )

    def test_openai_api_key_is_trimmed_and_empty_is_allowed(self) -> None:
        for api_key, expected in (("  secret  ", "secret"), ("", ""), ("   ", "")):
            with self.subTest(api_key=repr(api_key)):
                decoded = decode_request(
                    encode_request(_openai_request(api_key=api_key), monotonic_ns=lambda: NOW),
                    monotonic_ns=lambda: NOW,
                )
                self.assertEqual(decoded["payload"]["api_key"], expected)  # type: ignore[index]

    def test_valid_response_roundtrips_listing_states_and_worker_errors(self) -> None:
        responses: list[dict[str, object]] = [
            _success([]),
            _success([], state="missing-model-list"),
        ] + [
            {"error_code": code, "nonce": NONCE, "schema_version": 1, "status": "error"}
            for code in sorted(WORKER_ERROR_CODES)
        ]
        for response in responses:
            with self.subTest(response=response):
                frame = encode_response(response)
                self.assertEqual(decode_response(frame, expected_nonce=NONCE), response)
        openai_responses = [
            _openai_success([]),
            _openai_success([], state="missing-model-list"),
        ]
        for response in openai_responses:
            with self.subTest(response=response):
                frame = encode_response(
                    response,
                    operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
                )
                self.assertEqual(
                    decode_response(
                        frame,
                        expected_nonce=NONCE,
                        operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
                    ),
                    response,
                )

    def test_response_wire_operation_binds_empty_states_and_errors(self) -> None:
        operations = (
            ("list-ollama-models", _success([])),
            ("list-ollama-models", _success([], state="missing-model-list")),
            (LIST_OPENAI_COMPATIBLE_MODELS_OPERATION, _openai_success([])),
            (
                LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
                _openai_success([], state="missing-model-list"),
            ),
        )
        error_responses = [
            {
                "error_code": "remote-operation-failed",
                "nonce": NONCE,
                "schema_version": 1,
                "status": "error",
            },
        ]
        for operation, response in operations + tuple(
            (operation, response)
            for operation in ("list-ollama-models", LIST_OPENAI_COMPATIBLE_MODELS_OPERATION)
            for response in error_responses
        ):
            with self.subTest(operation=operation, response=response):
                frame = encode_response(response, operation=operation)
                wire = remote_http._decode_frame(
                    frame,
                    max_frame_bytes=MAX_RESPONSE_FRAME_BYTES,
                )
                self.assertEqual(wire["operation"], operation)  # type: ignore[index]
                self.assertEqual(
                    decode_response(
                        frame,
                        expected_nonce=NONCE,
                        operation=operation,
                    ),
                    response,
                )
                wrong_operation = (
                    LIST_OPENAI_COMPATIBLE_MODELS_OPERATION
                    if operation == "list-ollama-models"
                    else "list-ollama-models"
                )
                self.assertProtocolCode(
                    lambda wrong_operation=wrong_operation, frame=frame: decode_response(
                        frame,
                        expected_nonce=NONCE,
                        operation=wrong_operation,
                    ),
                    "remote-worker-protocol-invalid",
                )
                self.assertNotIn("operation", decode_response(frame, expected_nonce=NONCE, operation=operation))

    def test_truncated_prefix_and_body_are_fixed_failures(self) -> None:
        frame = encode_request(_request(), monotonic_ns=lambda: NOW)
        for hostile in (b"", frame[:1], frame[:3], frame[:-1]):
            with self.subTest(hostile=hostile):
                self.assertProtocolCode(
                    lambda hostile=hostile: decode_request(hostile, monotonic_ns=lambda: NOW),
                    "remote-request-invalid",
                )

    def test_slow_stream_cases_have_no_codec_entrypoint(self) -> None:
        self.assertFalse(hasattr(remote_http, "read_request"))
        self.assertFalse(hasattr(remote_http, "read_response"))
        self.assertFalse(hasattr(remote_http, "_read_exact"))
        self.assertFalse(hasattr(remote_http, "_read_frame"))
        cases = (
            ("slow prefix", decode_request, {"monotonic_ns": lambda: NOW}),
            ("slow body", decode_request, {"monotonic_ns": lambda: NOW}),
            ("complete frame without EOF", decode_response, {"expected_nonce": NONCE}),
        )
        for scenario, decoder, kwargs in cases:
            with self.subTest(scenario=scenario):
                stream = _BlockingStream()
                self.assertProtocolCode(
                    lambda stream=stream, decoder=decoder, kwargs=kwargs: decoder(stream, **kwargs),
                    "remote-request-invalid" if decoder is decode_request else "remote-worker-protocol-invalid",
                )
                self.assertEqual(stream.read_calls, 0)

    def test_deadline_abort_remains_bytes_based(self) -> None:
        frame = encode_request(
            _request(deadline_monotonic_ns=NOW + 1),
            monotonic_ns=lambda: NOW,
        )
        self.assertProtocolCode(
            lambda: decode_request(frame, monotonic_ns=lambda: NOW + 1),
            "remote-request-invalid",
        )

    def test_trailing_leading_and_second_frames_fail_closed(self) -> None:
        frame = encode_request(_request(), monotonic_ns=lambda: NOW)
        second = encode_request(_request(nonce="fedcba9876543210fedcba9876543210"), monotonic_ns=lambda: NOW)
        for hostile in (b"x" + frame, frame + b"x", frame + second):
            with self.subTest(hostile=hostile):
                self.assertProtocolCode(
                    lambda hostile=hostile: decode_request(hostile, monotonic_ns=lambda: NOW),
                    "remote-request-invalid",
                )

    def test_duplicate_top_level_and_nested_keys_fail_closed(self) -> None:
        duplicate_request = _raw_request_json(nonce=json.dumps(NONCE) + "," + json.dumps(NONCE))
        self.assertProtocolCode(
            lambda: decode_request(_raw_frame(duplicate_request), monotonic_ns=lambda: NOW),
            "remote-request-invalid",
        )
        model_json = (
            '{"name":"m","name":"m","model":"m","modified_at":"",'
            '"size":0,"size_label":"","digest":"","family":"",'
            '"parameter_size":"","quantization":"","description":""}'
        )
        response_json = (
            '{"nonce":"'
            + NONCE
            + '","result":{"listing_state":"listed","models":['
            + model_json
            + ']},"schema_version":1,"status":"ok"}'
        ).encode("ascii")
        self.assertProtocolCode(
            lambda: decode_response(_raw_frame(response_json), expected_nonce=NONCE),
            "remote-worker-protocol-invalid",
        )

    def test_nonfinite_numbers_are_rejected(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity", "1e400"):
            with self.subTest(token=token):
                body = _raw_request_json(deadline=token)
                self.assertProtocolCode(
                    lambda body=body: decode_request(_raw_frame(body), monotonic_ns=lambda: NOW),
                    "remote-request-invalid",
                )

    def test_surrogates_and_invalid_utf8_are_rejected(self) -> None:
        surrogate = _raw_request_json(payload='{"url":"\\ud800"}')
        self.assertProtocolCode(
            lambda: decode_request(_raw_frame(surrogate), monotonic_ns=lambda: NOW),
            "remote-request-invalid",
        )
        invalid_utf8 = b'{"deadline_monotonic_ns":10000000001,"nonce":"' + NONCE.encode("ascii") + b'\xff"}'
        self.assertProtocolCode(
            lambda: decode_request(_raw_frame(invalid_utf8), monotonic_ns=lambda: NOW),
            "remote-request-invalid",
        )

    def test_nonce_must_be_exact_lowercase_hex_and_canonical(self) -> None:
        for nonce in ("a" * 31, "A" * 32, "g" * 32):
            with self.subTest(nonce=nonce):
                self.assertProtocolCode(
                    lambda nonce=nonce: encode_request(_request(nonce=nonce), monotonic_ns=lambda: NOW),
                    "remote-request-invalid",
                )
        noncanonical = _raw_request_json(nonce='"\\u0030' + NONCE[1:] + '"')
        self.assertProtocolCode(
            lambda: decode_request(_raw_frame(noncanonical), monotonic_ns=lambda: NOW),
            "remote-request-invalid",
        )

    def test_deadline_must_be_positive_future_int_within_listing_cap(self) -> None:
        for deadline in (NOW, NOW - 1, NOW + LISTING_DEADLINE_NS + 1, True, False):
            with self.subTest(deadline=deadline):
                self.assertProtocolCode(
                    lambda deadline=deadline: encode_request(
                        _request(deadline_monotonic_ns=deadline), monotonic_ns=lambda: NOW
                    ),
                    "remote-request-invalid",
                )

    def test_unknown_missing_and_cross_operation_fields_fail_closed(self) -> None:
        cases = [
            _request(extra="sentinel"),
            _request(payload={}),
            _request(payload={"url": "http://127.0.0.1:11434", "api_key": "secret"}),
            _openai_request(payload={"url": "http://127.0.0.1:11434"}),
            _openai_request(
                payload={
                    "url": "http://127.0.0.1:11434",
                    "api_key": "secret",
                    "extra": "sentinel",
                }
            ),
            _request(payload={"url": "http://127.0.0.1:11434", "__proto__": {}}),
        ]
        for request in cases:
            with self.subTest(request=request):
                self.assertProtocolCode(
                    lambda request=request: encode_request(request, monotonic_ns=lambda: NOW),
                    "remote-request-invalid",
                )

    def test_openai_request_key_limits_and_cross_operation_response_schema(self) -> None:
        for api_key in (
            "x" * (remote_http.MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1),
            "secret\nvalue",
            True,
        ):
            with self.subTest(api_key=repr(api_key)):
                self.assertProtocolCode(
                    lambda api_key=api_key: encode_request(
                        _openai_request(api_key=api_key),  # type: ignore[arg-type]
                        monotonic_ns=lambda: NOW,
                    ),
                    "remote-request-invalid",
                )
        self.assertProtocolCode(
            lambda: encode_response(
                _openai_success([_openai_model("m")]),
            ),
            "remote-worker-protocol-invalid",
        )
        self.assertProtocolCode(
            lambda: encode_response(
                _success([_model("m")]),
                operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
            ),
            "remote-worker-protocol-invalid",
        )
        secret_frame = encode_response(
            _openai_success([_openai_model("secret-token-model")]),
            operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
        )
        self.assertProtocolCode(
            lambda: decode_response(
                secret_frame,
                expected_nonce=NONCE,
                operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
                secret="secret-token",
            ),
            "remote-worker-protocol-invalid",
        )

    def test_success_error_mixing_and_supervisor_codes_are_rejected(self) -> None:
        mixed_success = _success([])
        mixed_success["error_code"] = "remote-operation-failed"
        mixed_error = {
            "error_code": "remote-operation-failed",
            "nonce": NONCE,
            "result": {"listing_state": "listed", "models": []},
            "schema_version": 1,
            "status": "error",
        }
        for response in [mixed_success, mixed_error]:
            self.assertProtocolCode(
                lambda response=response: encode_response(response),
                "remote-worker-protocol-invalid",
            )
        self.assertEqual(WORKER_ERROR_CODES.isdisjoint(SUPERVISOR_ERROR_CODES), True)
        for code in sorted(SUPERVISOR_ERROR_CODES | {"unknown-error"}):
            response = {"error_code": code, "nonce": NONCE, "schema_version": 1, "status": "error"}
            self.assertProtocolCode(lambda response=response: encode_response(response), "remote-worker-protocol-invalid")

    def test_listing_model_schema_count_duplicates_and_limits(self) -> None:
        empty_fields = {
            "modified_at": "",
            "size": 0,
            "size_label": "",
            "digest": "",
            "family": "",
            "parameter_size": "",
            "quantization": "",
            "description": "",
        }
        hundred = [_model(f"model-{index}", **empty_fields) for index in range(MAX_MODEL_LIST_ENTRIES)]
        self.assertEqual(
            decode_response(encode_response(_success(hundred)), expected_nonce=NONCE)["result"]["models"],
            hundred,
        )
        too_many = hundred + [_model("model-over-limit", **empty_fields)]
        self.assertProtocolCode(
            lambda: encode_response(_success(too_many)),
            "remote-worker-protocol-invalid",
        )
        duplicate = [_model("same", **empty_fields), _model("same", **empty_fields)]
        self.assertProtocolCode(
            lambda: encode_response(_success(duplicate)),
            "remote-worker-protocol-invalid",
        )
        invalid_size = _model(**empty_fields)
        invalid_size["size"] = True
        for invalid_model in (
            {**_model(**empty_fields), "extra": "sentinel"},
            {key: value for key, value in _model(**empty_fields).items() if key != "digest"},
            _model(**empty_fields, model="other"),
            invalid_size,
            _model(**empty_fields, name="x" * (remote_http.MAX_MODEL_CHARS + 1)),
        ):
            self.assertProtocolCode(
                lambda invalid_model=invalid_model: encode_response(_success([invalid_model])),
                "remote-worker-protocol-invalid",
            )
        self.assertProtocolCode(
            lambda: encode_response(_success([_model(**empty_fields)], state="missing-model-list")),
            "remote-worker-protocol-invalid",
        )

    def test_url_character_and_byte_limits_are_preserved(self) -> None:
        self.assertProtocolCode(
            lambda: encode_request(
                _request(payload={"url": "x" * (MAX_URL_CHARS + 1)}), monotonic_ns=lambda: NOW
            ),
            "remote-request-invalid",
        )
        self.assertProtocolCode(
            lambda: encode_request(
                _request(payload={"url": "é" * (MAX_URL_BYTES // 2 + 1)}), monotonic_ns=lambda: NOW
            ),
            "remote-request-invalid",
        )
        self.assertLessEqual(len("http://127.0.0.1:11434".encode("utf-8")), MAX_URL_BYTES)
        self.assertLess(MAX_ERROR_FRAME_BYTES, MAX_REQUEST_FRAME_BYTES)


class RemoteHttpLifecycleTest(unittest.TestCase):
    def _valid_frame(self) -> bytes:
        return encode_response(_success([]))

    def _assert_pump_code(
        self,
        source: str,
        expected_code: str,
        *,
        deadline_ns: int | None = None,
        request_frame: bytes = b"request",
        cancel: object = None,
    ) -> None:
        handle = _start_test_child(source)
        try:
            with self.assertRaises(remote_http._SupervisorFailure) as context:
                remote_http._pump_worker(
                    handle,
                    request_frame,
                    deadline_ns or time.monotonic_ns() + 1_000_000_000,
                    time.monotonic_ns,
                    cancel,  # type: ignore[arg-type]
                )
            self.assertEqual(context.exception.code, expected_code)
        finally:
            _dispose_test_child(handle)

    def test_pump_has_absolute_deadline_for_slow_prefix_body_and_open_eof(self) -> None:
        frame = self._valid_frame()
        cases = (
            ("slow prefix", (frame[:1], frame[1:]), (0.3,)),
            ("slow body", (frame[:5], frame[5:]), (0.3,)),
            ("complete frame without EOF", (frame,), ()),
        )
        for name, chunks, pauses in cases:
            with self.subTest(name=name):
                source = _output_script(
                    chunks=chunks,
                    pauses=pauses,
                    hold=0.3 if name == "complete frame without EOF" else 0.0,
                )
                self._assert_pump_code(
                    source,
                    "remote-operation-timeout",
                    deadline_ns=time.monotonic_ns() + 50_000_000,
                )

    def test_pump_retries_write_after_backpressure(self) -> None:
        handle = _start_test_child(
            _output_script(chunks=(self._valid_frame(),), hold=0.1)
        )
        try:
            real_send = remote_http._send_control_chunk
            state = {"blocked": False}

            def flaky_send(
                stream: socket.socket,
                data: bytes,
                credentials: bytes,
                *_deadline_args: object,
            ) -> int:
                if not state["blocked"]:
                    state["blocked"] = True
                    raise BlockingIOError
                return real_send(stream, data, credentials)

            with mock.patch.object(
                remote_http,
                "_send_control_chunk",
                side_effect=flaky_send,
            ):
                received = remote_http._pump_worker(
                    handle,
                    b"request",
                    time.monotonic_ns() + 1_000_000_000,
                    time.monotonic_ns,
                    None,
                )
            self.assertEqual(received, self._valid_frame())
            self.assertTrue(state["blocked"])
        finally:
            _dispose_test_child(handle)

    def test_supervisor_request_send_skips_ready_io_at_deadline(self) -> None:
        control_parent, control_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        stdout_parent, stdout_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        stderr_parent, stderr_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            process = mock.Mock()
            process.stdin = control_parent
            process.stdout = stdout_parent
            process.stderr = stderr_parent
            process.poll.return_value = None
            handle = remote_http._WorkerHandle(
                process=process,
                pid=1,
                start_time="start",
                pidfd=99,
                request_nonce=NONCE,
            )
            deadline_ns = 100
            selector = mock.Mock()
            selector.select.return_value = [
                mock.Mock(fd=control_parent.fileno(), data="stdin")
            ]
            clock = mock.Mock(side_effect=(99, 99, 99, deadline_ns))
            with (
                mock.patch.object(remote_http.selectors, "DefaultSelector", return_value=selector),
                mock.patch.object(remote_http.os, "set_blocking"),
                mock.patch.object(remote_http, "_send_control_chunk") as send_chunk,
                mock.patch.object(remote_http, "_observe_descendants", return_value=(True, False)),
            ):
                with self.assertRaises(remote_http._SupervisorFailure) as context:
                    remote_http._pump_worker(
                        handle,
                        b"request",
                        deadline_ns,
                        clock,
                        None,
                    )
            self.assertEqual(context.exception.code, "remote-operation-timeout")
            send_chunk.assert_not_called()
        finally:
            for endpoint in (
                control_parent,
                control_child,
                stdout_parent,
                stdout_child,
                stderr_parent,
                stderr_child,
            ):
                endpoint.close()

    def test_supervisor_release_send_skips_ready_io_at_deadline(self) -> None:
        control_parent, control_child = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        try:
            process = mock.Mock()
            process.stdin = control_parent
            process.poll.return_value = None
            handle = remote_http._WorkerHandle(process, 1, "start", 99)
            deadline_ns = 100
            selector = mock.Mock()
            selector.select.return_value = [mock.Mock(fd=control_parent.fileno())]
            clock = mock.Mock(side_effect=(99, 99, 99, deadline_ns))
            with (
                mock.patch.object(remote_http.selectors, "DefaultSelector", return_value=selector),
                mock.patch.object(remote_http, "_send_control_chunk") as send_chunk,
            ):
                self.assertFalse(
                    remote_http._send_control_frame(
                        handle,
                        b"release",
                        deadline_ns,
                        clock,
                    )
                )
            send_chunk.assert_not_called()
        finally:
            control_parent.close()
            control_child.close()

    def test_supervisor_output_read_skips_io_at_deadline(self) -> None:
        process = mock.Mock()
        handle = remote_http._WorkerHandle(process, 1, "start", -1)
        handle.output_credentials_required = False
        deadline_ns = 100
        with mock.patch.object(remote_http.os, "read") as read:
            with self.assertRaises(remote_http._CleanupDeadline):
                remote_http._read_worker_output(
                    handle,
                    object(),
                    123,
                    deadline_ns,
                    lambda: deadline_ns,
                )
        read.assert_not_called()

    def test_worker_request_release_and_output_skip_ready_io_at_deadline(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        deadline_ns = 100
        selector = mock.Mock()
        selector.select.return_value = [mock.Mock(fd=0, data="control")]
        control = mock.Mock()
        with (
            mock.patch.object(remote_http_worker.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(remote_http_worker, "_recv_control_chunk") as recv_chunk,
            mock.patch.object(
                remote_http_worker.time,
                "monotonic_ns",
                side_effect=(99, deadline_ns),
            ),
        ):
            with self.assertRaises(remote_http_worker._WorkerDeadline):
                remote_http_worker._read_request_frame(
                    control,
                    99,
                    deadline_ns,
                    (1, 2, 3),
                )
        recv_chunk.assert_not_called()

        selector = mock.Mock()
        selector.select.return_value = [mock.Mock(fd=0, data="control")]
        with (
            mock.patch.object(remote_http_worker.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(remote_http_worker, "_recv_control_chunk") as recv_chunk,
            mock.patch.object(
                remote_http_worker.time,
                "monotonic_ns",
                side_effect=(99, deadline_ns),
            ),
        ):
            with self.assertRaises(remote_http_worker._WorkerDeadline):
                remote_http_worker._wait_for_release(
                    control,
                    99,
                    (1, 2, 3),
                    NONCE,
                    b"response",
                    deadline_ns,
                )
        recv_chunk.assert_not_called()

        selector = mock.Mock()
        selector.select.return_value = [mock.Mock(fd=1, data="stdout")]
        with (
            mock.patch.object(remote_http_worker.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(remote_http_worker.os, "set_blocking"),
            mock.patch.object(remote_http_worker.os, "write") as write,
            mock.patch.object(remote_http_worker.os, "close"),
            mock.patch.object(
                remote_http_worker.time,
                "monotonic_ns",
                side_effect=(99, deadline_ns),
            ),
        ):
            with self.assertRaises(remote_http_worker._WorkerDeadline):
                remote_http_worker._write_response_frame(99, b"response", deadline_ns)
        write.assert_not_called()

    def test_worker_pre_envelope_budget_covers_postprocess_frame(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        request_deadline = NOW + POSTPROCESS_DEADLINE_NS
        request_frame = encode_request(
            _postprocess_request(deadline_monotonic_ns=request_deadline),
            monotonic_ns=lambda: NOW,
        )
        control = mock.Mock()
        credentials = (123, 1000, 1000)
        with (
            mock.patch.object(
                remote_http_worker,
                "_read_request_frame",
                return_value=request_frame,
            ) as read_request_frame,
            mock.patch.object(
                remote_http_worker.time,
                "monotonic_ns",
                side_effect=(NOW, NOW + LISTING_DEADLINE_NS + 1),
            ),
            mock.patch.object(remote_http_worker, "_install_deadline"),
            mock.patch.object(remote_http_worker, "_clear_deadline"),
            mock.patch.object(
                remote_http_worker,
                "_read_ollama_postprocess",
                return_value={"text": "processed"},
            ),
            mock.patch.object(
                remote_http_worker,
                "_finish_response",
                return_value=True,
            ) as finish_response,
        ):
            exit_code = remote_http_worker._run_with_control(-1, control, credentials)

        self.assertEqual(exit_code, 0)
        read_request_frame.assert_called_once_with(
            control,
            -1,
            NOW + POSTPROCESS_DEADLINE_NS,
            credentials,
        )
        finish_response.assert_called_once()

    def test_close_failure_is_terminal_even_with_real_open_fd(self) -> None:
        class CloseFailingStream:
            def __init__(self, endpoint: socket.socket) -> None:
                self.endpoint = endpoint

            def close(self) -> None:
                raise OSError("close failed")

            def fileno(self) -> int:
                return self.endpoint.fileno()

        endpoint, peer = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        try:
            process = mock.Mock()
            process.stdin = CloseFailingStream(endpoint)
            process.stdout = None
            process.stderr = None
            process.poll.return_value = 0
            handle = remote_http._WorkerHandle(process, 1, "start", -1)
            request = _request(deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS)
            with (
                mock.patch.object(remote_http, "_spawn_worker", return_value=handle),
                mock.patch.object(
                    remote_http,
                    "_pump_worker",
                    return_value=encode_response(_success([])),
                ),
                mock.patch.object(remote_http, "_cleanup_worker_body", return_value=True),
            ):
                result = remote_http.run_list_ollama_models(request)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_code"], "remote-worker-cleanup-unconfirmed")
            self.assertGreaterEqual(endpoint.fileno(), 0)
        finally:
            endpoint.close()
            peer.close()

    def test_cleanup_closes_real_socket_streams_for_both_lifecycle_states(self) -> None:
        for release_pending in (False, True):
            with self.subTest(release_pending=release_pending):
                pairs = [
                    socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
                    for _ in range(3)
                ]
                streams = [pair[0] for pair in pairs]
                peers = [pair[1] for pair in pairs]
                try:
                    process = mock.Mock()
                    process.stdin, process.stdout, process.stderr = streams
                    process.poll.return_value = 0
                    handle = remote_http._WorkerHandle(process, 1, "start", -1)
                    handle.release_pending = release_pending
                    with (
                        mock.patch.object(
                            remote_http,
                            "_cleanup_worker_body",
                            return_value=True,
                        ),
                        mock.patch.object(
                            remote_http,
                            "_cleanup_released_worker_body",
                            return_value=True,
                        ),
                    ):
                        self.assertTrue(
                            remote_http._cleanup_worker(
                                handle,
                                1_000_000_000,
                                lambda: 0,
                            )
                        )
                    self.assertTrue(all(stream.fileno() == -1 for stream in streams))
                finally:
                    for stream in streams + peers:
                        if stream.fileno() >= 0:
                            stream.close()

    def test_pidfd_close_failure_is_public_cleanup_failure_for_both_states(self) -> None:
        real_close = os.close
        for release_pending in (False, True):
            with self.subTest(release_pending=release_pending):
                handle = _start_test_child("import time\ntime.sleep(10)\n")
                pidfd = handle.pidfd
                close_attempts: list[int] = []

                def fail_pidfd_close(fd: int) -> None:
                    if fd == pidfd:
                        close_attempts.append(fd)
                        raise OSError("injected pidfd close failure")
                    real_close(fd)

                handle.release_pending = release_pending
                request = _request(
                    deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS,
                )
                try:
                    with (
                        mock.patch.object(remote_http.os, "close", side_effect=fail_pidfd_close),
                        mock.patch.object(remote_http, "_spawn_worker", return_value=handle),
                        mock.patch.object(
                            remote_http,
                            "_pump_worker",
                            return_value=encode_response(_success([])),
                        ),
                        mock.patch.object(remote_http, "_cleanup_worker_body", return_value=True),
                        mock.patch.object(
                            remote_http,
                            "_cleanup_released_worker_body",
                            return_value=True,
                        ),
                    ):
                        result = remote_http.run_list_ollama_models(request)
                    self.assertEqual(result["status"], "error")
                    self.assertEqual(
                        result["error_code"],
                        "remote-worker-cleanup-unconfirmed",
                    )
                    self.assertEqual(close_attempts, [pidfd])
                    os.fstat(pidfd)
                finally:
                    try:
                        os.fstat(pidfd)
                    except OSError:
                        pass
                    else:
                        real_close(pidfd)
                    if handle.process.poll() is None:
                        handle.process.kill()
                    handle.process.wait()

    def test_descendant_pidfd_close_failure_is_not_success(self) -> None:
        selector = mock.Mock()
        selector.select.return_value = [mock.Mock(data=123)]
        with (
            mock.patch.object(remote_http.selectors, "DefaultSelector", return_value=selector),
            mock.patch.object(remote_http.os, "pidfd_open", return_value=123),
            mock.patch.object(remote_http.os, "set_inheritable"),
            mock.patch.object(remote_http, "_identity_present", side_effect=(True, True)),
            mock.patch.object(remote_http, "_close_fd", return_value=False) as close_fd,
        ):
            self.assertFalse(
                remote_http._wait_for_descendants_exit(
                    {123: "start"},
                    time.monotonic_ns() + 1_000_000_000,
                    time.monotonic_ns,
                )
            )
        close_fd.assert_called_once_with(123)

    def test_abandon_pidfd_close_failure_is_not_success(self) -> None:
        handle = _start_test_child("import time\ntime.sleep(10)\n")
        pidfd = handle.pidfd
        real_close = os.close
        try:
            with mock.patch.object(remote_http, "_close_fd", return_value=False) as close_fd:
                self.assertFalse(
                    remote_http._abandon_spawned_worker(handle.process, pidfd)
                )
            close_fd.assert_called_once_with(pidfd)
            self.assertIsNotNone(handle.process.poll())
        finally:
            try:
                os.fstat(pidfd)
            except OSError:
                pass
            else:
                real_close(pidfd)
            if handle.process.poll() is None:
                handle.process.kill()
            handle.process.wait()

    def test_release_abandon_consumes_handle_pidfd_once_on_close_error(self) -> None:
        handle = _start_test_child("import time\ntime.sleep(10)\n")
        handle.release_pending = True
        pidfd = handle.pidfd
        real_close = os.close
        close_attempts: list[int] = []

        def fail_pidfd_close(fd: int) -> None:
            if fd == pidfd:
                close_attempts.append(fd)
                raise OSError("injected first pidfd close failure")
            real_close(fd)

        try:
            with (
                mock.patch.object(remote_http, "_observe_descendants", return_value=(True, False)),
                mock.patch.object(remote_http, "_drain_worker_output", return_value=True),
                mock.patch.object(remote_http, "_send_worker_tree_signal", return_value=False),
                mock.patch.object(remote_http.os, "close", side_effect=fail_pidfd_close),
            ):
                self.assertFalse(
                    remote_http._cleanup_worker(
                        handle,
                        time.monotonic_ns() + 2_000_000_000,
                        time.monotonic_ns,
                    )
                )
            self.assertEqual(handle.pidfd, -1)
            self.assertEqual(close_attempts, [pidfd])
            os.fstat(pidfd)
        finally:
            try:
                os.fstat(pidfd)
            except OSError:
                pass
            else:
                real_close(pidfd)
            if handle.process.poll() is None:
                handle.process.kill()
            handle.process.wait()

    def test_release_ignoring_workers_are_killed_within_tight_budget(self) -> None:
        frame = self._valid_frame()
        source = (
            "import os, time\n"
            f"os.write(1, {frame!r})\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "time.sleep(10)\n"
        )
        for attempt in range(20):
            with self.subTest(attempt=attempt):
                handle = _start_test_child(source)
                try:
                    self.assertEqual(
                        remote_http._pump_worker(
                            handle,
                            b"request",
                            time.monotonic_ns() + 1_000_000_000,
                            time.monotonic_ns,
                            None,
                        ),
                        frame,
                    )
                    self.assertTrue(handle.release_pending)
                    self.assertFalse(
                        remote_http._cleanup_worker(
                            handle,
                            time.monotonic_ns() + 250_000_000,
                            time.monotonic_ns,
                        )
                    )
                    self.assertIsNotNone(handle.process.poll())
                finally:
                    if handle.process.poll() is None:
                        handle.process.kill()
                    handle.process.wait()

    def test_release_ignoring_worker_is_terminal_with_production_budget(self) -> None:
        frame = self._valid_frame()
        source = (
            "import os, time\n"
            f"os.write(1, {frame!r})\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "time.sleep(10)\n"
        )
        handle = _start_test_child(source)
        try:
            request = _request(
                deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS,
            )
            with mock.patch.object(remote_http, "_spawn_worker", return_value=handle):
                result = remote_http.run_list_ollama_models(request)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_code"], "remote-worker-cleanup-unconfirmed")
            self.assertIsNotNone(handle.process.poll())
        finally:
            if handle.process.poll() is None:
                handle.process.kill()
            handle.process.wait()

    def test_late_stdout_or_stderr_after_release_is_cleanup_unconfirmed(self) -> None:
        frame = self._valid_frame()
        for phase, attempts in (("running", range(2)), ("exited", range(20))):
            for attempt in attempts:
                for late_stream in ("stdout", "stderr"):
                    with self.subTest(
                        phase=phase,
                        attempt=attempt,
                        late_stream=late_stream,
                    ), tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        stdout_path = root / "stdout.fifo"
                        stderr_path = root / "stderr.fifo"
                        os.mkfifo(stdout_path)
                        os.mkfifo(stderr_path)
                        stdout_fd = os.open(stdout_path, os.O_RDONLY | os.O_NONBLOCK)
                        stderr_fd = os.open(stderr_path, os.O_RDONLY | os.O_NONBLOCK)
                        stdout_file = os.fdopen(stdout_fd, "rb", buffering=0)
                        stderr_file = os.fdopen(stderr_fd, "rb", buffering=0)
                        stdout_guard = os.open(stdout_path, os.O_WRONLY | os.O_NONBLOCK)
                        stderr_guard = os.open(stderr_path, os.O_WRONLY | os.O_NONBLOCK)
                        control_read, control_write = os.pipe()
                        ready_read, ready_write = os.pipe()
                        done_read, done_write = os.pipe()
                        os.set_inheritable(control_read, True)
                        os.set_inheritable(ready_write, True)
                        os.set_inheritable(done_write, True)
                        source = (
                    "import os\n"
                    f"frame={frame!r}\n"
                    f"stdout_path={str(stdout_path)!r}\n"
                    f"stderr_path={str(stderr_path)!r}\n"
                            f"control_fd={control_read}\n"
                            f"ready_fd={ready_write}\n"
                            f"done_fd={done_write}\n"
                            f"late_stream={late_stream!r}\n"
                            "def send(path, data):\n"
                            "    fd = os.open(path, os.O_WRONLY)\n"
                            "    if data: os.write(fd, data)\n"
                            "    os.close(fd)\n"
                            "send(stdout_path, frame)\n"
                            "send(stderr_path, b'')\n"
                            "os.write(ready_fd, b'r')\n"
                            "os.read(0, 65536)\n"
                            "os.read(control_fd, 1)\n"
                            "send(stdout_path if late_stream == 'stdout' else stderr_path, b'late')\n"
                            "os.write(done_fd, b'd')\n"
                            "os._exit(0)\n"
                        )
                        control_parent, control_child = socket.socketpair(
                            socket.AF_UNIX,
                            socket.SOCK_STREAM,
                        )
                        process = subprocess.Popen(
                            [sys.executable, "-B", "-c", source],
                            stdin=control_child.fileno(),
                            stdout=stdout_file,
                            stderr=stderr_file,
                            close_fds=True,
                            pass_fds=(control_read, ready_write, done_write),
                            start_new_session=True,
                        )
                        control_child.close()
                        process.stdin = control_parent
                        process.stdout = stdout_file
                        process.stderr = stderr_file
                        os.close(ready_write)
                        os.close(done_write)
                        os.read(ready_read, 1)
                        os.close(stdout_guard)
                        os.close(stderr_guard)
                        pidfd = os.pidfd_open(process.pid, 0)
                        start_time = remote_http._process_start_time(process.pid)
                        self.assertIsNotNone(start_time)
                        handle = remote_http._WorkerHandle(
                            process,
                            process.pid,
                            start_time or "",
                            pidfd,
                        )
                        real_drain = remote_http._drain_worker_output
                        real_cleanup = remote_http._cleanup_worker

                        def drain_and_release(
                            worker_handle: remote_http._WorkerHandle,
                            deadline_ns: int,
                            clock: object,
                            **kwargs: object,
                        ) -> bool:
                            os.write(control_write, b"s")
                            drained = real_drain(
                                worker_handle,
                                deadline_ns,
                                clock,  # type: ignore[arg-type]
                                **kwargs,
                            )
                            process.wait(timeout=1.0)
                            return drained

                        def cleanup_after_root_exit(
                            worker_handle: remote_http._WorkerHandle,
                            deadline_ns: int,
                            clock: object,
                        ) -> bool:
                            os.write(control_write, b"s")
                            self.assertEqual(os.read(done_read, 1), b"d")
                            self.assertEqual(process.wait(timeout=1.0), 0)
                            return real_cleanup(worker_handle, deadline_ns, clock)  # type: ignore[arg-type]

                        try:
                            request = _request(
                                deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS,
                            )
                            with mock.patch.object(
                                remote_http,
                                "_spawn_worker",
                                return_value=handle,
                            ):
                                if phase == "running":
                                    with mock.patch.object(
                                        remote_http,
                                        "_drain_worker_output",
                                        side_effect=drain_and_release,
                                    ):
                                        result = remote_http.run_list_ollama_models(request)
                                else:
                                    with mock.patch.object(
                                        remote_http,
                                        "_cleanup_worker",
                                        side_effect=cleanup_after_root_exit,
                                    ):
                                        result = remote_http.run_list_ollama_models(request)
                            self.assertTrue(handle.release_pending)
                            self.assertEqual(result["status"], "error")
                            self.assertEqual(
                                result["error_code"],
                                "remote-worker-cleanup-unconfirmed",
                            )
                            self.assertEqual(process.poll(), 0)
                        finally:
                            for fd in (
                                control_read,
                                control_write,
                                ready_read,
                                done_read,
                                stdout_guard,
                                stderr_guard,
                                control_parent.fileno(),
                            ):
                                try:
                                    os.close(fd)
                                except OSError:
                                    pass
                            if process.poll() is None:
                                process.kill()
                            process.wait()
                            stdout_file.close()
                            stderr_file.close()

    def test_pump_rejects_partial_oversize_trailing_second_and_stderr(self) -> None:
        frame = self._valid_frame()
        cases = (
            ("partial", _output_script(chunks=(frame[:3],), hold=0.05)),
            (
                "oversize length",
                _output_script(chunks=(struct.pack(">I", MAX_RESPONSE_FRAME_BYTES),), hold=0.05),
            ),
            ("trailing", _output_script(chunks=(frame + b"x",), hold=0.05)),
            ("second frame", _output_script(chunks=(frame + frame,), hold=0.05)),
            ("stderr", _output_script(chunks=(frame,), stderr=b"worker detail", hold=0.05)),
        )
        for name, source in cases:
            with self.subTest(name=name):
                self._assert_pump_code(source, "remote-worker-protocol-invalid")

    def test_wrong_nonce_and_cancel_are_terminal(self) -> None:
        wrong_nonce = "fedcba9876543210fedcba9876543210"
        response = _success([])
        response["nonce"] = wrong_nonce
        handle = _start_test_child(
            _output_script(chunks=(encode_response(response),), hold=0.05)
        )
        try:
            received = remote_http._pump_worker(
                handle,
                b"request",
                time.monotonic_ns() + 1_000_000_000,
                time.monotonic_ns,
                None,
            )
            with self.assertRaises(RemoteProtocolError) as context:
                decode_response(received, expected_nonce=NONCE)
            self.assertEqual(context.exception.code, "remote-worker-protocol-invalid")
        finally:
            _dispose_test_child(handle)

        self._assert_pump_code(
            _output_script(chunks=(self._valid_frame(),), hold=1.0),
            "remote-worker-cancelled",
            cancel=lambda: True,
        )

    def test_cleanup_escalates_term_to_kill_and_confirms_reap(self) -> None:
        ready_read, ready_write = os.pipe()
        handle: remote_http._WorkerHandle | None = None
        try:
            handle = _start_test_child(
                "import os, signal, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"os.write({ready_write}, b'R')\n"
                f"os.close({ready_write})\n"
                "time.sleep(10)\n",
                pass_fds=(ready_write,),
            )
            os.close(ready_write)
            ready_write = -1
            self.assertEqual(os.read(ready_read, 1), b"R")
            self.assertTrue(
                remote_http._cleanup_worker(
                    handle,
                    time.monotonic_ns() + 3_000_000_000,
                    time.monotonic_ns,
                )
            )
            self.assertIsNotNone(handle.process.returncode)
            self.assertEqual(handle.process.returncode, -signal.SIGKILL)
        finally:
            if ready_read >= 0:
                os.close(ready_read)
            if ready_write >= 0:
                os.close(ready_write)
            if handle is not None:
                if handle.process.poll() is None:
                    handle.process.kill()
                handle.process.wait()

    def test_cleanup_reports_scan_identity_and_reap_failures(self) -> None:
        handle = _start_test_child("import time\ntime.sleep(1)\n")
        try:
            with mock.patch.object(remote_http, "_descendant_snapshot", return_value=None) as scan:
                self.assertFalse(
                    remote_http._cleanup_worker(
                        handle,
                        time.monotonic_ns() + 100_000_000,
                        time.monotonic_ns,
                    )
                )
            scan.assert_called_once()
        finally:
            if handle.process.poll() is None:
                handle.process.kill()
                handle.process.wait()

        handle = _start_test_child("import time\ntime.sleep(1)\n")
        try:
            with mock.patch.object(remote_http, "_identity_present", return_value=None):
                self.assertFalse(
                    remote_http._send_worker_tree_signal(handle, {}, signal.SIGTERM)
                )
        finally:
            _dispose_test_child(handle)

        handle = _start_test_child("import time\ntime.sleep(0.05)\n")
        try:
            time.sleep(0.1)
            with mock.patch.object(handle.process, "poll", return_value=None):
                self.assertFalse(
                    remote_http._cleanup_worker(
                        handle,
                        time.monotonic_ns() + 200_000_000,
                        time.monotonic_ns,
                    )
                )
        finally:
            if handle.process.poll() is None:
                handle.process.kill()
                handle.process.wait()

    def test_descendant_exit_requires_post_pidfd_identity_absence(self) -> None:
        cases = (
            ("eventual absence", (True, True, True, True, True, False), True, 2_000_000_000),
            ("deadline", None, False, 300_000_000),
            ("unknown", (True, True, None), False, 2_000_000_000),
            ("read error", (True, True, RuntimeError("proc read failed")), False, 2_000_000_000),
        )
        for name, identity_sequence, expected, budget_ns in cases:
            with self.subTest(name=name):
                handle = _start_test_child("import time\ntime.sleep(0.05)\n")
                start_time = remote_http._process_start_time(handle.pid)
                self.assertIsNotNone(start_time)
                try:
                    patch = (
                        mock.patch.object(
                            remote_http,
                            "_identity_present",
                            return_value=True,
                        )
                        if identity_sequence is None
                        else mock.patch.object(
                            remote_http,
                            "_identity_present",
                            side_effect=identity_sequence,
                        )
                    )
                    with patch as identity_present:
                        self.assertEqual(
                            remote_http._wait_for_descendants_exit(
                                {handle.pid: start_time or ""},
                                time.monotonic_ns() + budget_ns,
                                time.monotonic_ns,
                            ),
                            expected,
                        )
                    if identity_sequence is None:
                        self.assertGreater(identity_present.call_count, 2)
                    else:
                        self.assertEqual(identity_present.call_count, len(identity_sequence))
                finally:
                    if handle.process.poll() is None:
                        handle.process.kill()
                    handle.process.wait()

    def test_descendant_exit_identity_read_exceptions_fail_closed(self) -> None:
        cases = (
            ("initial", (RuntimeError("initial read failed"),), None),
            (
                "after pidfd open failure",
                (True, RuntimeError("post-open read failed")),
                OSError("pidfd vanished"),
            ),
            ("after pidfd verification", (True, RuntimeError("verify failed")), 123),
        )
        for name, identity_sequence, pidfd_result in cases:
            with self.subTest(name=name):
                opener = mock.Mock()
                if isinstance(pidfd_result, BaseException):
                    opener.side_effect = pidfd_result
                else:
                    opener.return_value = pidfd_result
                with (
                    mock.patch.object(remote_http.os, "pidfd_open", opener),
                    mock.patch.object(remote_http.os, "set_inheritable"),
                    mock.patch.object(remote_http, "_close_fd"),
                    mock.patch.object(
                        remote_http,
                        "_identity_present",
                        side_effect=identity_sequence,
                    ),
                ):
                    self.assertFalse(
                        remote_http._wait_for_descendants_exit(
                            {123: "start"},
                            2_000_000_000,
                            time.monotonic_ns,
                        )
                    )

    def test_descendant_exit_rejects_identity_false_at_deadline(self) -> None:
        class FakeClock:
            def __init__(self, values: tuple[int, ...]) -> None:
                self._values = iter(values)

            def __call__(self) -> int:
                return next(self._values)

        cases = (
            ("before deadline", (0, 1, 1, 1), True),
            ("at deadline", (0, 1, 1, 100), False),
            ("after deadline", (0, 1, 1, 101), False),
        )
        for name, clock_values, expected in cases:
            with self.subTest(name=name):
                with (
                    mock.patch.object(remote_http.os, "pidfd_open", return_value=123),
                    mock.patch.object(remote_http.os, "set_inheritable"),
                    mock.patch.object(remote_http, "_close_fd"),
                    mock.patch.object(
                        remote_http,
                        "_identity_present",
                        side_effect=(True, False),
                    ),
                ):
                    self.assertEqual(
                        remote_http._wait_for_descendants_exit(
                            {123: "start"},
                            100,
                            FakeClock(clock_values),
                        ),
                        expected,
                    )

    def test_descendant_pidfd_is_owned_before_deadline_abort(self) -> None:
        handle = _start_test_child("import time\ntime.sleep(10)\n")
        real_opener = os.pidfd_open
        opened: list[int] = []

        def open_pidfd(pid: int, flags: int) -> int:
            pidfd = real_opener(pid, flags)
            opened.append(pidfd)
            return pidfd

        clock_values = iter((0, 0, 100))
        try:
            with (
                mock.patch.object(remote_http.os, "pidfd_open", side_effect=open_pidfd),
                mock.patch.object(remote_http, "_identity_present", return_value=True),
                mock.patch.object(
                    remote_http,
                    "_close_fd",
                    wraps=remote_http._close_fd,
                ) as close_fd,
            ):
                self.assertFalse(
                    remote_http._wait_for_descendants_exit(
                        {handle.pid: "start"},
                        100,
                        lambda: next(clock_values),
                    )
                )
            self.assertEqual(len(opened), 1)
            close_fd.assert_called_once_with(opened[0])
            with self.assertRaises(OSError):
                os.fstat(opened[0])
        finally:
            _dispose_test_child(handle)

    def test_descendant_selector_close_failure_still_closes_owned_pidfd(self) -> None:
        handle = _start_test_child("import time\ntime.sleep(10)\n")
        real_opener = os.pidfd_open
        opened: list[int] = []

        def open_pidfd(pid: int, flags: int) -> int:
            pidfd = real_opener(pid, flags)
            opened.append(pidfd)
            return pidfd

        selector = mock.Mock()
        selector.select.return_value = []
        selector.close.side_effect = OSError("selector close failed")
        try:
            with (
                mock.patch.object(remote_http.os, "pidfd_open", side_effect=open_pidfd),
                mock.patch.object(remote_http, "_identity_present", return_value=True),
                mock.patch.object(
                    remote_http.selectors,
                    "DefaultSelector",
                    return_value=selector,
                ),
                mock.patch.object(
                    remote_http,
                    "_close_fd",
                    wraps=remote_http._close_fd,
                ) as close_fd,
            ):
                self.assertFalse(
                    remote_http._wait_for_descendants_exit(
                        {handle.pid: "start"},
                        time.monotonic_ns() + 1_000_000_000,
                        time.monotonic_ns,
                    )
                )
            self.assertEqual(len(opened), 1)
            close_fd.assert_called_once_with(opened[0])
            with self.assertRaises(OSError):
                os.fstat(opened[0])
        finally:
            _dispose_test_child(handle)

    def test_descendant_exit_handles_multiple_pidfds(self) -> None:
        handles = [
            _start_test_child("import time\ntime.sleep(0.05)\n"),
            _start_test_child("import time\ntime.sleep(0.05)\n"),
        ]
        descendants = {
            handle.pid: remote_http._process_start_time(handle.pid) or ""
            for handle in handles
        }
        try:
            with mock.patch.object(
                remote_http,
                "_identity_present",
                side_effect=(True, True, True, True, False, False),
            ) as identity_present:
                self.assertTrue(
                    remote_http._wait_for_descendants_exit(
                        descendants,
                        time.monotonic_ns() + 2_000_000_000,
                        time.monotonic_ns,
                    )
                )
            self.assertEqual(identity_present.call_count, 6)
        finally:
            for handle in handles:
                if handle.process.poll() is None:
                    handle.process.kill()
                handle.process.wait()

    def test_pump_binds_setsid_descendant_before_root_exit_and_cleanup_kills_it(self) -> None:
        frame = self._valid_frame()
        for attempt in range(20):
            with self.subTest(attempt=attempt):
                release_read, release_write = os.pipe()
                os.set_inheritable(release_read, True)
                source = (
                    "import os, signal\n"
                    f"frame={frame!r}\n"
                    "os.read(0, 65536)\n"
                    "child_pid = os.fork()\n"
                    "if child_pid == 0:\n"
                    "    os.setsid()\n"
                    "    os.close(0)\n"
                    "    os.close(1)\n"
                    "    os.close(2)\n"
                    "    signal.pause()\n"
                    "else:\n"
                    f"    os.read({release_read}, 1)\n"
                    "    os.write(1, frame)\n"
                )
                handle = _start_test_child(source, pass_fds=(release_read,))
                os.close(release_read)
                released = {"value": False}

                real_snapshot = remote_http._descendant_snapshot

                def snapshot(
                    root_pid: int,
                    expected_root_start_time: str | None = None,
                ) -> dict[int, str] | None:
                    result = real_snapshot(root_pid, expected_root_start_time)
                    if result and not released["value"]:
                        released["value"] = True
                        os.write(release_write, b"r")
                        os.close(release_write)
                    return result

                try:
                    with mock.patch.object(remote_http, "_descendant_snapshot", side_effect=snapshot):
                        received = remote_http._pump_worker(
                            handle,
                            b"request",
                            time.monotonic_ns() + 2_000_000_000,
                            time.monotonic_ns,
                            None,
                        )
                    self.assertEqual(received, frame)
                    descendants = dict(handle.descendants)
                    self.assertTrue(descendants)
                    self.assertFalse(
                        remote_http._cleanup_worker(
                            handle,
                            time.monotonic_ns() + 3_000_000_000,
                            time.monotonic_ns,
                        )
                    )
                    descendant_deadline = time.monotonic_ns() + 1_000_000_000
                    self.assertTrue(
                        remote_http._wait_for_descendants_exit(
                            descendants,
                            descendant_deadline,
                            time.monotonic_ns,
                        )
                    )
                    for pid, start_time in descendants.items():
                        self.assertFalse(remote_http._identity_present(pid, start_time))
                finally:
                    if not released["value"]:
                        os.close(release_write)
                    for pid, start_time in handle.descendants.items():
                        if remote_http._identity_present(pid, start_time) is True:
                            remote_http._send_descendant_signal(pid, start_time, signal.SIGKILL)
                    if handle.process.poll() is None:
                        handle.process.kill()
                        handle.process.wait()

    def test_fast_root_exit_is_cleanup_unconfirmed_and_never_success(self) -> None:
        frame = self._valid_frame()
        for attempt in range(20):
            with self.subTest(attempt=attempt):
                read_fd, child_report_fd = os.pipe()
                os.set_inheritable(child_report_fd, True)
                source = (
                    "import os, time\n"
                    f"frame={frame!r}\n"
                    "os.read(0, 65536)\n"
                    "child_pid = os.fork()\n"
                    "if child_pid == 0:\n"
                    "    os.setsid()\n"
                    f"    os.write({child_report_fd}, str(os.getpid()).encode())\n"
                    f"    os.close({child_report_fd})\n"
                    "    os.close(0)\n"
                    "    os.close(1)\n"
                    "    os.close(2)\n"
                    "    time.sleep(10)\n"
                    "else:\n"
                    f"    os.close({child_report_fd})\n"
                    "    os.write(1, frame)\n"
                )
                control_parent, control_child = socket.socketpair(
                    socket.AF_UNIX,
                    socket.SOCK_STREAM,
                )
                process = subprocess.Popen(
                    [sys.executable, "-c", source],
                    stdin=control_child.fileno(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    pass_fds=(child_report_fd,),
                    start_new_session=True,
                )
                control_child.close()
                process.stdin = control_parent
                os.close(child_report_fd)
                handle = remote_http._WorkerHandle(
                    process,
                    process.pid,
                    remote_http._process_start_time(process.pid) or "",
                    os.pidfd_open(process.pid, 0),
                )
                try:
                    request = _request(
                        deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS
                    )
                    with mock.patch.object(remote_http, "_spawn_worker", return_value=handle):
                        result = remote_http.run_list_ollama_models(request)
                    child_pid = int(os.read(read_fd, 64))
                    child_start_time = remote_http._process_start_time(child_pid)
                    self.assertEqual(result["status"], "error")
                    self.assertEqual(result["error_code"], "remote-worker-cleanup-unconfirmed")
                    self.assertTrue(handle.root_exited_before_release)
                    if child_start_time is not None:
                        if remote_http._identity_present(child_pid, child_start_time) is True:
                            self.assertTrue(
                                remote_http._send_descendant_signal(
                                    child_pid,
                                    child_start_time,
                                    signal.SIGKILL,
                                )
                            )
                        descendant_deadline = time.monotonic_ns() + 1_000_000_000
                        self.assertTrue(
                            remote_http._wait_for_descendants_exit(
                                {child_pid: child_start_time},
                                descendant_deadline,
                                time.monotonic_ns,
                            )
                        )
                        self.assertFalse(
                            remote_http._identity_present(child_pid, child_start_time)
                        )
                finally:
                    os.close(read_fd)
                    if process.stdin is not None:
                        process.stdin.close()
                    if process.poll() is None:
                        process.kill()
                    process.wait()

    def test_cleanup_slow_snapshot_deadline_stops_without_later_phases(self) -> None:
        handle = _start_test_child("import time\ntime.sleep(1)\n")
        calls = {"snapshot": 0}

        def slow_snapshot(*_args: object, **_kwargs: object) -> dict[int, str]:
            calls["snapshot"] += 1
            time.sleep(0.15)
            return {}

        try:
            with (
                mock.patch.object(remote_http, "_descendant_snapshot", side_effect=slow_snapshot),
                mock.patch.object(remote_http, "_send_worker_tree_signal") as send_tree,
                mock.patch.object(remote_http, "_drain_worker_output") as drain_output,
            ):
                self.assertFalse(
                    remote_http._cleanup_worker(
                        handle,
                        time.monotonic_ns() + 20_000_000,
                        time.monotonic_ns,
                    )
                )
            self.assertEqual(calls["snapshot"], 1)
            send_tree.assert_not_called()
            drain_output.assert_not_called()
        finally:
            if handle.process.poll() is None:
                handle.process.kill()
                handle.process.wait()

    def test_post_release_fork_is_os_blocked_and_never_success(self) -> None:
        frame = self._valid_frame()
        source = (
            "import os\n"
            f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})\n"
            "from speed_of_cinnamon import remote_http_worker\n"
            "remote_http_worker._install_no_fork_boundary()\n"
            f"frame={frame!r}\n"
            "os.read(0, 65536)\n"
            "os.write(1, frame)\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "os.read(0, 128)\n"
            "os.fork()\n"
        )
        for attempt in range(21):
            with self.subTest(attempt=attempt):
                handle = _start_test_child(source)
                try:
                    request = _request(
                        deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS
                    )
                    with mock.patch.object(remote_http, "_spawn_worker", return_value=handle):
                        result = remote_http.run_list_ollama_models(request)
                    self.assertEqual(result["status"], "error")
                    self.assertIn(
                        result["error_code"],
                        {"remote-worker-protocol-invalid", "remote-worker-cleanup-unconfirmed"},
                    )
                    self.assertLess(handle.process.returncode or 0, 0)
                    self.assertTrue(handle.release_pending)
                    self.assertFalse(handle.descendants)
                finally:
                    if handle.process.poll() is None:
                        handle.process.kill()
                        handle.process.wait()

    def test_no_fork_filter_arch_and_x32_jumps_are_fail_closed(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        instructions = remote_http_worker._NO_FORK_FILTER_INSTRUCTIONS
        kill_index = len(instructions) - 1
        self.assertEqual(
            instructions[0],
            (
                remote_http_worker._BPF_LD_W_ABS,
                0,
                0,
                remote_http_worker._SECCOMP_DATA_ARCH_OFFSET,
            ),
        )
        self.assertEqual(instructions[1][0], remote_http_worker._BPF_JMP_JEQ_K)
        self.assertEqual(instructions[1][3], remote_http_worker._AUDIT_ARCH_X86_64)
        self.assertEqual(1 + 1 + instructions[1][2], kill_index)
        self.assertEqual(instructions[3][0], remote_http_worker._BPF_ALU_AND_K)
        self.assertEqual(instructions[3][3], remote_http_worker._X32_SYSCALL_BIT)
        self.assertEqual(instructions[4][0], remote_http_worker._BPF_JMP_JEQ_K)
        self.assertEqual(4 + 1 + instructions[4][1], 6)
        self.assertEqual(4 + 1 + instructions[4][2], 5)
        for index in range(7, 11):
            self.assertEqual(index + 1 + instructions[index][1], kill_index)
        self.assertEqual(instructions[-2][3], remote_http_worker._SECCOMP_RET_ALLOW)
        self.assertEqual(instructions[-1][3], remote_http_worker._SECCOMP_RET_KILL_PROCESS)
        with mock.patch.object(
            remote_http_worker.os,
            "uname",
            return_value=mock.Mock(machine="unsupported-abi"),
        ):
            with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                remote_http_worker._install_no_fork_boundary()
        self.assertEqual(context.exception.code, "remote-operation-failed")

    def test_no_fork_filter_kills_native_and_x32_syscalls(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        if os.uname().machine not in {"x86_64", "amd64"}:
            self.skipTest("raw seccomp syscall test is x86_64-specific")
        source_root = Path(__file__).resolve().parents[1] / "src"
        calls = (
            ("fork", f"libc.syscall({remote_http_worker._SYS_FORK})"),
            ("vfork", f"libc.syscall({remote_http_worker._SYS_VFORK})"),
            (
                "clone",
                f"libc.syscall({remote_http_worker._SYS_CLONE}, 0, 0, 0, 0, 0)",
            ),
            ("clone3", f"libc.syscall({remote_http_worker._SYS_CLONE3}, 0, 0)"),
            (
                "x32-fork",
                f"libc.syscall({remote_http_worker._SYS_FORK | remote_http_worker._X32_SYSCALL_BIT})",
            ),
        )
        for name, syscall in calls:
            with self.subTest(name=name):
                source = (
                    "import ctypes, os, sys\n"
                    f"sys.path.insert(0, {str(source_root)!r})\n"
                    "from speed_of_cinnamon import remote_http_worker\n"
                    "remote_http_worker._install_no_fork_boundary()\n"
                    "libc = ctypes.CDLL(None, use_errno=True)\n"
                    "libc.syscall.restype = ctypes.c_long\n"
                    f"result = {syscall}\n"
                    "os._exit(42 if result >= 0 else 43)\n"
                )
                process = subprocess.Popen(
                    [sys.executable, "-B", "-c", source],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                    self.fail(f"{name} syscall child timed out: {stdout!r} {stderr!r}")
                self.assertEqual(process.returncode, -signal.SIGSYS)
                self.assertEqual(stdout, b"")
                self.assertEqual(stderr, b"")

    def test_worker_installs_boundary_before_request_and_http_run(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        events: list[str] = []
        with (
            mock.patch.object(
                remote_http_worker,
                "_prctl",
                side_effect=lambda _option, _argument: events.append("dumpable"),
            ),
            mock.patch.object(
                remote_http_worker,
                "_set_resource_limits",
                side_effect=lambda: events.append("limits"),
            ),
            mock.patch.object(
                remote_http_worker,
                "_bind_parent",
                side_effect=lambda: events.append("parent") or -1,
            ),
            mock.patch.object(
                remote_http_worker,
                "_install_no_fork_boundary",
                side_effect=lambda: events.append("seccomp"),
            ),
            mock.patch.object(
                remote_http_worker,
                "_run",
                side_effect=lambda _parent_pidfd: events.append("run") or 0,
            ),
        ):
            self.assertEqual(remote_http_worker.main(), 0)
        self.assertEqual(events, ["dumpable", "limits", "parent", "seccomp", "run"])

    def test_spawn_uses_fixed_isolated_worker_command_and_environment(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        with (
            mock.patch.object(remote_http.subprocess, "Popen", return_value=process) as popen,
            mock.patch.object(remote_http.os, "pidfd_open", return_value=77),
            mock.patch.object(remote_http.os, "set_inheritable"),
            mock.patch.object(remote_http, "_process_start_time", return_value="start"),
        ):
            handle = remote_http._spawn_worker(b"request")
        self.assertEqual(handle.pid, 1234)
        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            [sys.executable, "-I", "-B", "-m", "speed_of_cinnamon.remote_http_worker"],
        )
        self.assertIsInstance(kwargs["stdin"], int)
        self.assertIsInstance(process.stdin, socket.socket)
        self.assertIsInstance(process.stdout, socket.socket)
        self.assertIsInstance(process.stderr, socket.socket)
        self.assertIsInstance(kwargs["stdout"], int)
        self.assertIsInstance(kwargs["stderr"], int)
        self.assertNotEqual(kwargs["stdout"], process.stdout.fileno())
        self.assertNotEqual(kwargs["stderr"], process.stderr.fileno())
        self.assertNotEqual(kwargs["stdin"], process.stdin.fileno())
        self.assertEqual(process.stdin.family, socket.AF_UNIX)
        self.assertEqual(process.stdin.type & socket.SOCK_STREAM, socket.SOCK_STREAM)
        self.assertFalse(os.get_inheritable(process.stdin.fileno()))
        for stream in (process.stdout, process.stderr):
            self.assertEqual(stream.family, socket.AF_UNIX)
            self.assertEqual(stream.type & socket.SOCK_STREAM, socket.SOCK_STREAM)
            self.assertFalse(os.get_inheritable(stream.fileno()))
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["env"], {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "LC_CTYPE": "C.UTF-8"})
        with self.assertRaises(OSError):
            os.fstat(kwargs["stdout"])
        with self.assertRaises(OSError):
            os.fstat(kwargs["stderr"])
        remote_http._close_worker_streams(handle)
        remote_http._close_fd(handle.pidfd)

        failed_process = mock.Mock()
        failed_process.pid = 1235
        failed_process.poll.return_value = None
        with (
            mock.patch.object(remote_http.subprocess, "Popen", return_value=failed_process),
            mock.patch.object(remote_http.os, "pidfd_open", return_value=78),
            mock.patch.object(remote_http.os, "set_inheritable"),
            mock.patch.object(remote_http, "_process_start_time", return_value=None),
            mock.patch.object(remote_http.signal, "pidfd_send_signal") as send_signal,
            mock.patch.object(remote_http, "_close_fd"),
        ):
            with self.assertRaises(remote_http._SupervisorFailure) as context:
                remote_http._spawn_worker(b"request")
        self.assertEqual(context.exception.code, "remote-worker-unavailable")
        send_signal.assert_called_once_with(78, signal.SIGKILL, None, 0)
        failed_process.wait.assert_called_once()

    def test_spawn_popen_failure_closes_all_socket_ends(self) -> None:
        endpoints: list[socket.socket] = []
        real_socketpair = socket.socketpair

        def socketpair(*args: object, **kwargs: object) -> tuple[socket.socket, socket.socket]:
            pair = real_socketpair(*args, **kwargs)  # type: ignore[arg-type]
            endpoints.extend(pair)
            return pair

        with (
            mock.patch.object(remote_http.socket, "socketpair", side_effect=socketpair),
            mock.patch.object(
                remote_http.subprocess,
                "Popen",
                side_effect=OSError("popen failed"),
            ),
        ):
            with self.assertRaises(remote_http._SupervisorFailure) as context:
                remote_http._spawn_worker(b"request")
        self.assertEqual(context.exception.code, "remote-worker-unavailable")
        self.assertEqual(len(endpoints), 6)
        self.assertTrue(all(endpoint.fileno() == -1 for endpoint in endpoints))

    def test_spawn_set_inheritable_failure_closes_socket_ends(self) -> None:
        endpoints: list[socket.socket] = []
        real_socketpair = socket.socketpair
        real_set_inheritable = os.set_inheritable

        def socketpair(*args: object, **kwargs: object) -> tuple[socket.socket, socket.socket]:
            pair = real_socketpair(*args, **kwargs)  # type: ignore[arg-type]
            endpoints.extend(pair)
            return pair

        calls = 0

        def set_inheritable(fd: int, inheritable: bool) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("set inheritable failed")
            real_set_inheritable(fd, inheritable)

        with (
            mock.patch.object(remote_http.socket, "socketpair", side_effect=socketpair),
            mock.patch.object(remote_http.os, "set_inheritable", side_effect=set_inheritable),
            mock.patch.object(remote_http.subprocess, "Popen") as popen,
        ):
            with self.assertRaises(remote_http._SupervisorFailure) as context:
                remote_http._spawn_worker(b"request")
        self.assertEqual(context.exception.code, "remote-worker-unavailable")
        popen.assert_not_called()
        self.assertEqual(len(endpoints), 6)
        self.assertTrue(all(endpoint.fileno() == -1 for endpoint in endpoints))

    def test_spawn_child_socket_close_failure_reaps_root_and_returns_no_handle(self) -> None:
        endpoints: list[socket.socket] = []
        wrappers: list[object] = []
        real_socketpair = socket.socketpair
        real_popen = remote_http.subprocess.Popen
        captured_process: list[subprocess.Popen[bytes]] = []

        class CloseFailingSocket(socket.socket):
            def __init__(self, endpoint: socket.socket, fail_close: bool) -> None:
                super().__init__(fileno=endpoint.detach())
                self.fail_close = fail_close

            def detach(self) -> int:
                fd = super().detach()
                if self.fail_close and fd >= 0:
                    os.close(fd)
                    raise OSError("child socket detach failed")
                return fd

        def socketpair(*args: object, **kwargs: object) -> tuple[object, object]:
            raw_parent, raw_child = real_socketpair(*args, **kwargs)  # type: ignore[arg-type]
            first_endpoint = len(endpoints)
            failing_child = first_endpoint + 2 in (2, 6)
            pair = (
                CloseFailingSocket(raw_parent, False),
                CloseFailingSocket(raw_child, failing_child),
            )
            endpoints.extend(pair)
            wrappers.extend(pair)
            return pair

        def popen(_args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(
                [sys.executable, "-B", "-c", "import time; time.sleep(10)"],
                stdin=kwargs["stdin"],
                stdout=kwargs["stdout"],
                stderr=kwargs["stderr"],
                close_fds=True,
                start_new_session=True,
            )
            captured_process.append(process)
            return process

        try:
            with (
                mock.patch.object(remote_http.socket, "socketpair", side_effect=socketpair),
                mock.patch.object(remote_http.subprocess, "Popen", side_effect=popen),
            ):
                with self.assertRaises(remote_http._SupervisorFailure) as context:
                    remote_http._spawn_worker(b"request")
            self.assertEqual(context.exception.code, "remote-worker-cleanup-unconfirmed")
            self.assertEqual(len(wrappers), 6)
            self.assertEqual(len(captured_process), 1)
            process = captured_process[0]
            self.assertIsNotNone(process.poll())
            self.assertTrue(all(endpoint.fileno() == -1 for endpoint in endpoints))
        finally:
            if captured_process and captured_process[0].poll() is None:
                captured_process[0].kill()
                captured_process[0].wait()
            for endpoint in endpoints:
                if endpoint.fileno() >= 0:
                    endpoint.close()
            self.assertTrue(all(endpoint.fileno() == -1 for endpoint in endpoints))

    def test_spawn_pidfd_failure_kills_reaps_and_closes_all_socket_ends(self) -> None:
        endpoints: list[socket.socket] = []
        real_socketpair = socket.socketpair
        process = mock.Mock()
        process.pid = 1236

        def socketpair(*args: object, **kwargs: object) -> tuple[socket.socket, socket.socket]:
            pair = real_socketpair(*args, **kwargs)  # type: ignore[arg-type]
            endpoints.extend(pair)
            return pair

        with (
            mock.patch.object(remote_http.socket, "socketpair", side_effect=socketpair),
            mock.patch.object(remote_http.subprocess, "Popen", return_value=process),
            mock.patch.object(remote_http.os, "pidfd_open", side_effect=OSError("pidfd failed")),
        ):
            with self.assertRaises(remote_http._SupervisorFailure) as context:
                remote_http._spawn_worker(b"request")
        self.assertEqual(context.exception.code, "remote-worker-cleanup-unconfirmed")
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        self.assertEqual(len(endpoints), 6)
        self.assertTrue(all(endpoint.fileno() == -1 for endpoint in endpoints))

    def test_production_spawn_parent_sockets_reject_same_uid_proc_fd_injection(self) -> None:
        with _isolated_worker_python() as worker_python:
            with mock.patch.object(remote_http.sys, "executable", str(worker_python)):
                handle = remote_http._spawn_worker(b"request")
            try:
                for stream in (
                    handle.process.stdin,
                    handle.process.stdout,
                    handle.process.stderr,
                ):
                    self.assertIsInstance(stream, socket.socket)
                    fd = stream.fileno()
                    for flags in (os.O_WRONLY, os.O_RDWR):
                        with self.subTest(fd=fd, flags=flags):
                            with self.assertRaises(OSError) as context:
                                os.open(f"/proc/{os.getpid()}/fd/{fd}", flags)
                            self.assertEqual(context.exception.errno, errno.ENXIO)
            finally:
                self.assertTrue(
                    remote_http._cleanup_worker(
                        handle,
                        time.monotonic_ns() + 2_000_000_000,
                        time.monotonic_ns,
                    )
                )
                self.assertIsNotNone(handle.process.poll())

    def test_pidfd_getfd_same_uid_cannot_inject_accepted_success(self) -> None:
        if not callable(getattr(os, "pidfd_open", None)):
            self.skipTest("pidfd unavailable")
        control_read, control_write = os.pipe()
        ready_read, ready_write = os.pipe()
        go_read, go_write = os.pipe()
        response_frame = encode_response(_success([]))
        worker_source = (
            "import os\n"
            f"control_fd = {control_read}\n"
            "os.read(0, 65536)\n"
            "os.read(control_fd, 1)\n"
            "for fd in (1, 2):\n"
            "    try:\n"
            "        os.close(fd)\n"
            "    except OSError:\n"
            "        pass\n"
        )
        real_popen = remote_http.subprocess.Popen
        real_send_control_chunk = remote_http._send_control_chunk
        attacker_pid: int | None = None
        handle: remote_http._WorkerHandle | None = None
        injection_ready = b""
        go_sent = False
        duplicated_fds: list[int] = []
        pidfds: list[int] = []
        real_spawn = remote_http._spawn_worker

        def controlled_popen(_args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            nonlocal control_read
            process = real_popen(
                [sys.executable, "-B", "-c", worker_source],
                stdin=kwargs["stdin"],
                stdout=kwargs["stdout"],
                stderr=kwargs["stderr"],
                close_fds=True,
                pass_fds=(control_read,),
                start_new_session=True,
            )
            os.close(control_read)
            control_read = -1
            return process

        def spawn_and_arm(frame: bytes) -> remote_http._WorkerHandle:
            nonlocal attacker_pid, handle, injection_ready
            handle = real_spawn(frame)
            worker_pidfd = os.pidfd_open(handle.pid, 0)
            supervisor_pidfd = os.pidfd_open(os.getpid(), 0)
            pidfds.extend((worker_pidfd, supervisor_pidfd))
            target_fds: list[int] = []
            try:
                for target_fd in (1, 2):
                    try:
                        target_fds.append(_pidfd_getfd(worker_pidfd, target_fd))
                    except OSError:
                        target_fds.append(-1)
                try:
                    target_fds.append(
                        _pidfd_getfd(supervisor_pidfd, handle.process.stdin.fileno())
                    )
                except OSError:
                    target_fds.append(-1)
                duplicated_fds.extend(fd for fd in target_fds if fd >= 0)
                attacker_pid = os.fork()
                if attacker_pid == 0:
                    keep = {
                        go_read,
                        ready_write,
                        control_write,
                        *[fd for fd in target_fds if fd >= 0],
                    }
                    inherited = (
                        handle.process.stdin.fileno(),
                        handle.process.stdout.fileno(),
                        handle.process.stderr.fileno(),
                        handle.pidfd,
                        worker_pidfd,
                        supervisor_pidfd,
                        go_write,
                        ready_read,
                    )
                    for fd in inherited:
                        if fd not in keep and fd >= 0:
                            try:
                                os.close(fd)
                            except OSError:
                                pass
                    status = b"1" if target_fds[0] >= 0 and target_fds[1] >= 0 else b"0"
                    os.write(ready_write, status)
                    try:
                        os.read(go_read, 1)
                        if target_fds[0] >= 0:
                            os.write(target_fds[0], response_frame)
                        if target_fds[2] >= 0:
                            os.write(target_fds[2], b"late-release")
                        os.write(control_write, b"x")
                    except OSError:
                        pass
                    for fd in target_fds:
                        if fd >= 0:
                            try:
                                os.close(fd)
                            except OSError:
                                pass
                    try:
                        os.close(go_read)
                    except OSError:
                        pass
                    os._exit(0)
                os.close(ready_write)
                os.close(go_read)
                injection_ready = os.read(ready_read, 1)
                return handle
            finally:
                for fd in target_fds:
                    if fd >= 0:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                for fd in pidfds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                pidfds.clear()

        try:
            with mock.patch.object(remote_http.subprocess, "Popen", side_effect=controlled_popen):
                def guarded_send(
                    stream: socket.socket,
                    data: bytes,
                    credentials: bytes,
                    *_deadline_args: object,
                ) -> int:
                    nonlocal go_sent
                    written = real_send_control_chunk(stream, data, credentials)
                    if (
                        not go_sent
                        and handle is not None
                        and written == len(data)
                        and stream.fileno() == handle.process.stdin.fileno()
                    ):
                        os.write(go_write, b"g")
                        go_sent = True
                    return written

                with mock.patch.object(remote_http, "_spawn_worker", side_effect=spawn_and_arm):
                    with mock.patch.object(
                        remote_http,
                        "_send_control_chunk",
                        side_effect=guarded_send,
                    ):
                        result = remote_http.run_list_ollama_models(
                            _request(deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS)
                        )
            self.assertEqual(injection_ready, b"1")
            self.assertIsNotNone(handle)
            self.assertNotEqual(result["status"], "ok")
            self.assertIsNotNone(handle.process.poll())  # type: ignore[union-attr]
        finally:
            if not go_sent:
                try:
                    os.write(go_write, b"g")
                except OSError:
                    pass
            for fd in (control_write, ready_read, go_write):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if control_read >= 0:
                os.close(control_read)
            if attacker_pid is not None:
                _pid, _status = os.waitpid(attacker_pid, 0)
            if handle is not None and handle.process.poll() is None:
                handle.process.kill()
                handle.process.wait()

    def test_spawn_requires_pidfd_facilities_before_popen(self) -> None:
        for target in ("pidfd_open", "pidfd_send_signal"):
            with self.subTest(target=target):
                with (
                    mock.patch.object(remote_http.subprocess, "Popen") as popen,
                    mock.patch.object(
                        remote_http.os if target == "pidfd_open" else remote_http.signal,
                        target,
                        None,
                    ),
                ):
                    with self.assertRaises(remote_http._SupervisorFailure) as context:
                        remote_http._spawn_worker(b"request")
                self.assertEqual(context.exception.code, "remote-worker-unavailable")
                popen.assert_not_called()

    def test_abandon_after_pidfd_signal_failure_reaps_root_but_stays_unconfirmed(self) -> None:
        for sender in (None, mock.Mock(side_effect=OSError("pidfd signal failed"))):
            with self.subTest(sender=sender):
                handle = _start_test_child("import time\ntime.sleep(10)\n")
                try:
                    with mock.patch.object(remote_http.signal, "pidfd_send_signal", sender):
                        self.assertFalse(
                            remote_http._abandon_spawned_worker(
                                handle.process,
                                handle.pidfd,
                            )
                        )
                    self.assertIsNotNone(handle.process.poll())
                finally:
                    if handle.process.poll() is None:
                        handle.process.kill()
                    handle.process.wait()

    def test_abandon_without_pidfd_is_cleanup_unconfirmed(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(remote_http, "_close_worker_streams") as close_streams:
            self.assertFalse(remote_http._abandon_spawned_worker(process, -1))
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        close_streams.assert_called_once()

    def test_supervisor_returns_worker_result_and_cleanup_failure_is_terminal(self) -> None:
        deadline = time.monotonic_ns() + LISTING_DEADLINE_NS
        request = _request(deadline_monotonic_ns=deadline)
        fake_handle = mock.Mock()
        fake_handle.process.poll.return_value = 0
        with (
            mock.patch.object(remote_http, "_spawn_worker", return_value=fake_handle),
            mock.patch.object(remote_http, "_pump_worker", return_value=encode_response(_success([]))),
            mock.patch.object(remote_http, "_cleanup_worker", return_value=True),
        ):
            self.assertEqual(remote_http.run_list_ollama_models(request), _success([]))

        with (
            mock.patch.object(remote_http, "_spawn_worker", return_value=fake_handle),
            mock.patch.object(remote_http, "_pump_worker", return_value=encode_response(_success([]))),
            mock.patch.object(remote_http, "_cleanup_worker", return_value=False),
        ):
            result = remote_http.run_list_ollama_models(request)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "remote-worker-cleanup-unconfirmed")

    def test_supervisor_reuses_shared_runner_for_postprocess_operation(self) -> None:
        deadline = time.monotonic_ns() + POSTPROCESS_DEADLINE_NS
        request = _postprocess_request(deadline_monotonic_ns=deadline)
        fake_handle = mock.Mock()
        fake_handle.process.poll.return_value = 0
        response_frame = encode_response(
            _postprocess_success("processed"),
            operation=POSTPROCESS_OLLAMA_OPERATION,
        )
        with (
            mock.patch.object(remote_http, "_spawn_worker", return_value=fake_handle),
            mock.patch.object(remote_http, "_pump_worker", return_value=response_frame),
            mock.patch.object(remote_http, "_cleanup_worker", return_value=True) as cleanup,
        ):
            result = remote_http.run_postprocess_ollama(request)
        self.assertEqual(result, _postprocess_success("processed"))
        cleanup.assert_called_once_with(
            fake_handle,
            deadline + remote_http.CLEANUP_GRACE_NS,
            mock.ANY,
        )

    def test_supervisor_reuses_shared_runner_for_openai_postprocess_operation(self) -> None:
        deadline = time.monotonic_ns() + POSTPROCESS_DEADLINE_NS
        request = _openai_postprocess_request(deadline_monotonic_ns=deadline, api_key="")
        fake_handle = mock.Mock()
        fake_handle.process.poll.return_value = 0
        response_frame = encode_response(
            _postprocess_success("processed"),
            operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        )
        with (
            mock.patch.object(remote_http, "_spawn_worker", return_value=fake_handle),
            mock.patch.object(remote_http, "_pump_worker", return_value=response_frame),
            mock.patch.object(remote_http, "_cleanup_worker", return_value=True) as cleanup,
        ):
            result = remote_http.run_postprocess_openai_compatible(request)
        self.assertEqual(result, _postprocess_success("processed"))
        cleanup.assert_called_once_with(
            fake_handle,
            deadline + remote_http.CLEANUP_GRACE_NS,
            mock.ANY,
        )

    def test_openai_postprocess_runner_rejects_secret_in_worker_response(self) -> None:
        deadline = time.monotonic_ns() + POSTPROCESS_DEADLINE_NS
        request = _openai_postprocess_request(deadline_monotonic_ns=deadline)
        fake_handle = mock.Mock()
        fake_handle.process.poll.return_value = 0
        response_frame = encode_response(
            _postprocess_success("contains secret-token"),
            operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        )
        with (
            mock.patch.object(remote_http, "_spawn_worker", return_value=fake_handle),
            mock.patch.object(remote_http, "_pump_worker", return_value=response_frame),
            mock.patch.object(remote_http, "_cleanup_worker", return_value=True),
        ):
            result = remote_http.run_postprocess_openai_compatible(request)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "remote-worker-protocol-invalid")
        self.assertNotIn("secret-token", repr(result))

    def test_worker_has_parent_boundary_without_subprocess_api(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        self.assertFalse(hasattr(remote_http_worker, "subprocess"))
        self.assertTrue(callable(remote_http_worker._bind_parent))
        self.assertEqual(
            remote_http_worker._decode_listing('{"models":null}'),
            {"listing_state": "missing-model-list", "models": []},
        )

    def test_worker_source_has_no_process_creation_primitives(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        source = Path(remote_http_worker.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_calls = {
            "fork",
            "fork1",
            "posix_spawn",
            "spawn",
            "Popen",
            "setsid",
        }
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        self.assertTrue(forbidden_calls.isdisjoint(calls))
        imported_modules = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertNotIn("subprocess", imported_modules)

    def test_remote_runners_are_cli_boundary_and_legacy_is_nonremote_only(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src" / "speed_of_cinnamon"
        postprocessor_source = (source_root / "postprocessor.py").read_text(encoding="utf-8")
        cli_source = (source_root / "cli.py").read_text(encoding="utf-8")
        self.assertIn("return post_process_with_ollama(", postprocessor_source)
        self.assertIn("text = post_process_text(", cli_source)
        for runner_name in (
            "remote_http.run_list_ollama_models",
            "remote_http.run_list_openai_compatible_models",
            "remote_http.run_postprocess_ollama",
            "remote_http.run_postprocess_openai_compatible",
        ):
            self.assertIn(runner_name, cli_source)
        self.assertIn('post_process_backend == "ollama"', cli_source)
        self.assertIn(
            'post_process_backend in {"openai-compatible", "openai", "local-openai"}',
            cli_source,
        )
        self.assertIn('else:\n        text = post_process_text(', cli_source)

    def test_cli_product_adapters_use_real_worker_for_all_four_operations(self) -> None:
        from speed_of_cinnamon import cli, postprocessor

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        server_port = server.getsockname()[1]
        report_read, report_write = os.pipe()
        server_pid = os.fork()
        if server_pid == 0:
            os.close(report_read)
            records: list[dict[str, object]] = []
            exit_code = 0
            try:
                server.settimeout(10.0)
                for _ in range(4):
                    connection, _address = server.accept()
                    try:
                        connection.settimeout(10.0)
                        request_bytes = bytearray()
                        while b"\r\n\r\n" not in request_bytes:
                            chunk = connection.recv(4096)
                            if not chunk:
                                raise OSError("request headers ended early")
                            request_bytes.extend(chunk)
                        header_end = request_bytes.find(b"\r\n\r\n")
                        header_lines = bytes(request_bytes[:header_end]).split(b"\r\n")
                        method, path, _version = header_lines[0].split(b" ", 2)
                        headers = {
                            key.decode("ascii").lower(): value.decode("iso-8859-1").strip()
                            for key, value in (
                                line.split(b":", 1)
                                for line in header_lines[1:]
                            )
                        }
                        content_length = int(headers.get("content-length", "0"))
                        while len(request_bytes) - header_end - 4 < content_length:
                            chunk = connection.recv(4096)
                            if not chunk:
                                raise OSError("request body ended early")
                            request_bytes.extend(chunk)
                        body = bytes(request_bytes[header_end + 4 : header_end + 4 + content_length])
                        records.append(
                            {
                                "body": body.decode("utf-8"),
                                "headers": headers,
                                "method": method.decode("ascii"),
                                "path": path.decode("ascii"),
                            }
                        )
                        if path == b"/api/tags":
                            response_payload = {"models": [_model("ollama-cli-model")]}
                        elif path == b"/v1/models":
                            response_payload = {
                                "data": [{"id": "openai-cli-model", "owned_by": "provider"}]
                            }
                        elif path == b"/api/generate":
                            response_payload = {
                                "done": True,
                                "response": "Transcript: cli-loopback-transcript",
                            }
                        elif path == b"/v1/chat/completions":
                            response_payload = {
                                "choices": [
                                    {
                                        "finish_reason": "stop",
                                        "message": {"content": "Transcript: cli-loopback-transcript"},
                                    }
                                ]
                            }
                        else:
                            raise OSError(f"unexpected endpoint {path!r}")
                        response_body = json.dumps(
                            response_payload,
                            separators=(",", ":"),
                        ).encode("utf-8")
                        connection.sendall(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: application/json\r\n"
                            + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
                            + b"Connection: close\r\n\r\n"
                            + response_body
                        )
                    finally:
                        connection.close()
                os.write(report_write, json.dumps(records, separators=(",", ":")).encode("utf-8"))
            except Exception:
                exit_code = 1
            finally:
                server.close()
                os.close(report_write)
            os._exit(exit_code)

        server.close()
        os.close(report_write)
        secret = "cli-loopback-api-key"
        public_responses: list[dict[str, object]] = []
        cleanup_observations: list[tuple[bool, int | None]] = []
        observed_stderr = bytearray()
        source_text = "cli-loopback-transcript"
        security = {
            "blacklist_added": [],
            "blacklist_opened": False,
            "redacted_words": 0,
            "blacklist_hits": 0,
        }
        common_args = {
            "openai_compatible_api_key": secret,
            "openai_compatible_api_key_stdin": False,
            "openai_compatible_flex_processing": True,
            "openai_compatible_model": "gpt-5.6-luna",
            "openai_compatible_text_model": "gpt-5.6-luna",
            "openai_compatible_url": f"http://127.0.0.1:{server_port}/v1",
            "ollama_model": "llama3.2:3b",
            "ollama_url": f"http://127.0.0.1:{server_port}",
            "personal_context": "",
            "post_process_command": "",
            "post_process_prompt": "",
            "soften_profanity": False,
            "vocabulary": "",
        }
        ollama_args = SimpleNamespace(**common_args, post_process_backend="ollama")
        openai_args = SimpleNamespace(
            **common_args,
            post_process_backend="openai-compatible",
        )
        text_models_args = SimpleNamespace(
            backend="openai-compatible",
            openai_compatible_api_key=secret,
            openai_compatible_api_key_stdin=False,
            openai_compatible_url=f"http://127.0.0.1:{server_port}/v1",
        )
        real_cleanup = remote_http._cleanup_worker
        real_read_output = remote_http._read_worker_output

        def observe_cleanup(
            handle: remote_http._WorkerHandle,
            lifecycle_deadline_ns: int,
            clock: object,
        ) -> bool:
            confirmed = real_cleanup(
                handle,
                lifecycle_deadline_ns,
                clock,  # type: ignore[arg-type]
            )
            cleanup_observations.append((confirmed, handle.process.poll()))
            return confirmed

        def capture_output(
            handle: remote_http._WorkerHandle,
            stream: object,
            fd: int,
            deadline_ns: int,
            clock: object,
        ) -> bytes:
            chunk = real_read_output(
                handle,
                stream,
                fd,
                deadline_ns,
                clock,  # type: ignore[arg-type]
            )
            if stream is handle.process.stderr:
                observed_stderr.extend(chunk)
            return chunk

        def observe_runner(name: str):
            real_runner = getattr(remote_http, name)

            def run(request: dict[str, object]) -> dict[str, object]:
                response = real_runner(request)
                self.assertNotIn("operation", response)
                public_responses.append(response)
                return response

            return run

        try:
            with _isolated_worker_python() as worker_python:
                with (
                    mock.patch.object(remote_http.sys, "executable", str(worker_python)),
                    mock.patch.object(remote_http, "_cleanup_worker", side_effect=observe_cleanup),
                    mock.patch.object(remote_http, "_read_worker_output", side_effect=capture_output),
                    mock.patch.object(
                        remote_http,
                        "run_list_ollama_models",
                        side_effect=observe_runner("run_list_ollama_models"),
                    ),
                    mock.patch.object(
                        remote_http,
                        "run_list_openai_compatible_models",
                        side_effect=observe_runner("run_list_openai_compatible_models"),
                    ),
                    mock.patch.object(
                        remote_http,
                        "run_postprocess_ollama",
                        side_effect=observe_runner("run_postprocess_ollama"),
                    ),
                    mock.patch.object(
                        remote_http,
                        "run_postprocess_openai_compatible",
                        side_effect=observe_runner("run_postprocess_openai_compatible"),
                    ),
                    mock.patch.object(cli, "post_process_text", side_effect=AssertionError("legacy bypass")),
                    mock.patch.object(postprocessor, "list_ollama_models", side_effect=AssertionError("legacy bypass")),
                    mock.patch.object(postprocessor, "list_openai_compatible_models", side_effect=AssertionError("legacy bypass")),
                    mock.patch.object(postprocessor, "post_process_with_ollama", side_effect=AssertionError("legacy bypass")),
                    mock.patch.object(postprocessor, "post_process_with_openai_compatible", side_effect=AssertionError("legacy bypass")),
                    mock.patch.object(postprocessor, "post_process_text", side_effect=AssertionError("legacy bypass")),
                    mock.patch.object(
                        cli,
                        "_apply_security_post_processing",
                        side_effect=lambda value: (value, security),
                    ),
                    mock.patch.object(
                        cli,
                        "_apply_security_mask_only",
                        side_effect=lambda value: (value, security),
                    ),
                ):
                    ollama_listing = cli.list_ollama_models(common_args["ollama_url"])
                    openai_listing = cli.command_text_models(text_models_args)
                    ollama_text, _ollama_security = cli._process_transcript(
                        source_text,
                        ollama_args,
                        "en",
                    )
                    openai_text, _openai_security = cli._process_transcript(
                        source_text,
                        openai_args,
                        "en",
                    )

            records = json.loads(os.read(report_read, 1_000_000).decode("utf-8"))
            self.assertEqual(
                [record["path"] for record in records],
                ["/api/tags", "/v1/models", "/api/generate", "/v1/chat/completions"],
            )
            self.assertEqual(ollama_listing["models"][0]["name"], "ollama-cli-model")
            self.assertEqual(
                openai_listing["models"],
                [{"name": "openai-cli-model", "model": "openai-cli-model"}],
            )
            self.assertEqual(ollama_text, source_text)
            self.assertEqual(openai_text, source_text)
            self.assertEqual(len(public_responses), 4)
            self.assertEqual(cleanup_observations, [(True, 0)] * 4)
            self.assertEqual(observed_stderr, b"")
            for record in records:
                header = record["headers"]
                body = record["body"]
                if record["path"] in {"/v1/models", "/v1/chat/completions"}:
                    self.assertEqual(header["authorization"], f"Bearer {secret}")
                else:
                    self.assertNotIn("authorization", header)
                self.assertNotIn(secret, body)
            self.assertNotIn(secret, repr(ollama_listing))
            self.assertNotIn(secret, repr(openai_listing))
            self.assertNotIn(secret, ollama_text)
            self.assertNotIn(secret, openai_text)
        finally:
            os.close(report_read)
            _pid, status = os.waitpid(server_pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    def test_worker_listing_parser_rejects_nonfinite_float_at_json_boundary(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        hostile_bodies = (
            "1e400",
            "-1e400",
            '{"models":[{"name":"m","size":1e400}]}',
            '{"models":[{"name":"m","details":{"size":-1e400}}]}',
        )
        for body in hostile_bodies:
            with self.subTest(body=body):
                with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                    remote_http_worker._decode_listing(body)
                self.assertEqual(context.exception.code, "remote-response-invalid")

        finite = remote_http_worker._decode_listing(
            '{"models":[{"name":"m","size":1.5}]}'
        )
        self.assertEqual(finite["listing_state"], "listed")
        self.assertEqual(finite["models"][0]["size"], 0)  # type: ignore[index]

    def test_worker_openai_listing_normalizes_and_discards_provider_metadata(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        result = remote_http_worker._decode_openai_listing(
            '{"data":[{"id":" zeta ","owned_by":"provider","description":"ignored"},'
            '{"name":"Alpha","owned_by":"provider"},{"id":"ada-nope"},'
            '{"id":"zeta","owned_by":"second"}]}',
            "secret-token",
        )
        self.assertEqual(
            result,
            {
                "listing_state": "listed",
                "models": [_openai_model("Alpha"), _openai_model("zeta")],
            },
        )
        self.assertEqual(
            remote_http_worker._decode_openai_listing('{"data":null}', ""),
            {"listing_state": "missing-model-list", "models": []},
        )
        self.assertEqual(
            remote_http_worker._decode_openai_listing("{}", ""),
            {"listing_state": "missing-model-list", "models": []},
        )

    def test_worker_openai_listing_rejects_hostile_json_and_secret_anywhere(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        nested = "null"
        for _ in range(65):
            nested = "[" + nested + "]"
        hostile_bodies = (
            '{"data":[{"id":"m","id":"m"}]}',
            '{"data":[{"id":"m","value":NaN}]}',
            '{"data":[{"id":"m","value":Infinity}]}',
            '{"data":[{"id":"m","value":1e400}]}',
            '{"data":[{"__proto__":"x"}]}',
            '{"data":' + nested + "}",
            '{"data":[{"id":"secret-token-model"}]}',
            '{"data":[{"id":"safe","owned_by":"secret-token"}]}',
            '{"data":[{"id":"safe","metadata":{"nested":"has-secret-token"}}]}',
            '{"data":[{"id":"safe","ignored":"secret-token"}]}',
        )
        for body in hostile_bodies:
            with self.subTest(body=body[:80]):
                with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                    remote_http_worker._decode_openai_listing(body, "secret-token")
                self.assertEqual(context.exception.code, "remote-response-invalid")
        too_many = json.dumps(
            {"data": [{"id": f"model-{index}"} for index in range(MAX_MODEL_LIST_ENTRIES + 1)]},
            separators=(",", ":"),
        )
        with self.assertRaises(remote_http_worker._WorkerFailure) as context:
            remote_http_worker._decode_openai_listing(too_many, "")
        self.assertEqual(context.exception.code, "remote-response-invalid")

    def test_worker_openai_listing_body_limit_is_fail_closed(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        response = mock.Mock(status=200)
        connection = mock.Mock()
        with (
            mock.patch.object(
                remote_http_worker,
                "_open_connection",
                return_value=(connection, response),
            ),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                side_effect=remote_http_worker.PostProcessError("remote response is too large"),
            ),
        ):
            with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                remote_http_worker._read_openai_listing(
                    "http://127.0.0.1:1",
                    "",
                    time.monotonic_ns() + LISTING_DEADLINE_NS,
                )
        self.assertEqual(context.exception.code, "remote-response-too-large")
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_worker_openai_listing_revalidates_same_origin_redirect_with_headers(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        redirect_response = mock.Mock(status=302)
        redirect_response.getheader.return_value = "/models?redirect=1"
        success_response = mock.Mock(status=200)
        redirect_connection = mock.Mock()
        success_connection = mock.Mock()
        opened: list[tuple[str, dict[str, str]]] = []

        def open_connection(
            url: str,
            _deadline_ns: int,
            *,
            headers: dict[str, str],
        ) -> tuple[object, object]:
            opened.append((url, headers))
            if len(opened) == 1:
                return redirect_connection, redirect_response
            return success_connection, success_response

        with (
            mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                return_value='{"data":[{"id":"gpt-test"}]}',
            ),
        ):
            result = remote_http_worker._read_openai_listing(
                "http://127.0.0.1:1/v1",
                "secret-token",
                time.monotonic_ns() + LISTING_DEADLINE_NS,
            )
        self.assertEqual(result, {"listing_state": "listed", "models": [_openai_model("gpt-test")]})
        self.assertEqual(
            [url for url, _headers in opened],
            [
                "http://127.0.0.1:1/v1/models",
                "http://127.0.0.1:1/models?redirect=1",
            ],
        )
        for _url, headers in opened:
            self.assertEqual(
                headers,
                {
                    "Accept": "application/json",
                    "Authorization": "Bearer secret-token",
                    "Connection": "close",
                    "Content-Type": "application/json",
                },
            )
        redirect_response.close.assert_called_once()
        success_response.close.assert_called_once()
        redirect_connection.close.assert_called_once()
        success_connection.close.assert_called_once()

    def test_listing_close_failures_are_fixed_worker_errors_for_both_operations(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        cases = (
            (
                "ollama",
                _request(deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS),
                remote_http_worker._read_listing,
                '{"models":[]}',
                "list-ollama-models",
            ),
            (
                "openai",
                _openai_request(
                    api_key="secret-token",
                    deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS,
                ),
                remote_http_worker._read_openai_listing,
                '{"data":[]}',
                LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
            ),
        )
        for name, request, reader, body, operation in cases:
            for failing_close in ("response", "connection"):
                with self.subTest(name=name, failing_close=failing_close):
                    request_frame = encode_request(request)
                    response_frames: list[bytes] = []
                    response = mock.Mock(status=200)
                    connection = mock.Mock()
                    getattr(response if failing_close == "response" else connection, "close").side_effect = OSError(
                        "close failed"
                    )
                    with (
                        mock.patch.object(
                            remote_http_worker,
                            "_open_control_socket",
                            return_value=(mock.Mock(), (123, 1000, 1000)),
                        ),
                        mock.patch.object(
                            remote_http_worker,
                            "_read_request_frame",
                            return_value=request_frame,
                        ),
                        mock.patch.object(remote_http_worker, "_install_deadline"),
                        mock.patch.object(
                            remote_http_worker,
                            "_open_connection",
                            return_value=(connection, response),
                        ),
                        mock.patch.object(
                            remote_http_worker,
                            "_read_response_text",
                            return_value=body,
                        ),
                        mock.patch.object(
                            remote_http_worker,
                            "_write_response_frame",
                            side_effect=lambda _parent_pidfd, frame, _deadline: response_frames.append(frame),
                        ),
                        mock.patch.object(remote_http_worker, "_wait_for_release"),
                    ):
                        self.assertEqual(remote_http_worker._run(-1), remote_http.WORKER_EXIT_CODE)
                    self.assertEqual(len(response_frames), 1)
                    self.assertEqual(
                        decode_response(
                            response_frames[0],
                            expected_nonce=NONCE,
                            operation=operation,
                        ),
                        {
                            "error_code": "remote-operation-failed",
                            "nonce": NONCE,
                            "schema_version": 1,
                            "status": "error",
                        },
                    )
                    response.close.assert_called_once()
                    connection.close.assert_called_once()

    def test_worker_postprocess_posts_exact_body_headers_and_prompt(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        response = mock.Mock(status=200)
        connection = mock.Mock()
        opened: list[tuple[str, str, bytes, dict[str, str]]] = []

        def open_connection(
            url: str,
            _deadline_ns: int,
            *,
            headers: dict[str, str],
            method: str,
            body: bytes,
        ) -> tuple[object, object]:
            opened.append((url, method, body, headers))
            return connection, response

        source_text = "hello transcript sentinel"
        with (
            mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                return_value='{"done":true,"response":"Transcript: hello transcript sentinel"}',
            ),
        ):
            result = remote_http_worker._read_ollama_postprocess(
                "http://127.0.0.1:11434/base",
                "llama3.2:3b",
                source_text,
                "en",
                "context sentinel",
                "vocabulary sentinel",
                "instruction sentinel",
                time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
            )
        self.assertEqual(result, {"text": source_text})
        self.assertEqual(len(opened), 1)
        url, method, body, headers = opened[0]
        self.assertEqual(url, "http://127.0.0.1:11434/base/api/generate")
        self.assertEqual(method, "POST")
        self.assertEqual(
            headers,
            {
                "Accept": "application/json",
                "Connection": "close",
                "Content-Type": "application/json",
            },
        )
        decoded_body = json.loads(body)
        self.assertEqual(set(decoded_body), {"model", "prompt", "stream"})
        self.assertEqual(decoded_body["model"], "llama3.2:3b")
        self.assertIs(decoded_body["stream"], False)
        for sentinel in (source_text, "context sentinel", "vocabulary sentinel", "instruction sentinel"):
            self.assertIn(sentinel, decoded_body["prompt"])
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_worker_postprocess_response_hostile_cases_fail_closed(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        deep = "{}"
        for _ in range(70):
            deep = '{"nested":' + deep + "}"
        hostile = (
            '{"done":true,"response":"ok","response":"evil"}',
            '{"done":true,"response":NaN}',
            '{"done":true,"response":1e400}',
            '{"done":false,"response":"ok"}',
            '{"response":"ok"}',
            '{"done":true,"response":""}',
            '{"done":true,"response":[]}',
            '{"done":true,"response":"ok","__proto__":{}}',
            '{"done":true,"response":"\\ud800"}',
            deep,
            json.dumps({"done": True, "response": "x" * 1_000_001}),
        )
        for raw_text in hostile:
            with self.subTest(raw_text=raw_text[:48]):
                with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                    remote_http_worker._decode_ollama_postprocess_response(raw_text, "source")
                self.assertEqual(context.exception.code, "remote-response-invalid")

    def test_worker_postprocess_provider_error_cannot_be_success(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        with self.assertRaises(remote_http_worker._WorkerFailure) as context:
            remote_http_worker._decode_ollama_postprocess_response(
                '{"done":true,"error":"backend failed","response":"bad"}',
                "source",
            )
        self.assertEqual(context.exception.code, "remote-http-failed")

    def test_worker_postprocess_response_bytes_are_bounded_and_utf8_checked(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        for name, chunk, expected_code in (
            ("oversize", b"x" * 1_500_001, "remote-response-too-large"),
            ("invalid utf8", b"\xff", "remote-response-invalid"),
        ):
            with self.subTest(name=name):
                response = mock.Mock(status=200)
                response.read.side_effect = (chunk, b"")
                connection = mock.Mock()
                with mock.patch.object(
                    remote_http_worker,
                    "_open_connection",
                    return_value=(connection, response),
                ):
                    with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                        remote_http_worker._read_ollama_postprocess(
                            "http://127.0.0.1:11434",
                            "model",
                            "source",
                            "en",
                            "",
                            "",
                            "",
                            time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                        )
                self.assertEqual(context.exception.code, expected_code)
                response.close.assert_called_once()
                connection.close.assert_called_once()

    def test_worker_openai_postprocess_posts_exact_body_headers_and_flex(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        cases = (
            (
                "openai flex with key",
                "https://api.openai.com/v1",
                "secret-token",
                True,
                True,
                True,
            ),
            (
                "local without key",
                "http://127.0.0.1:1/v1",
                "",
                True,
                True,
                False,
            ),
        )
        for name, url, api_key, flex_processing, service_tier_fallback, expect_flex in cases:
            with self.subTest(name=name):
                response = mock.Mock(status=200)
                connection = mock.Mock()
                opened: list[tuple[str, str, bytes, dict[str, str]]] = []

                def open_connection(
                    opened_url: str,
                    _deadline_ns: int,
                    *,
                    headers: dict[str, str],
                    method: str,
                    body: bytes,
                ) -> tuple[object, object]:
                    opened.append((opened_url, method, body, headers))
                    return connection, response

                with (
                    mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
                    mock.patch.object(
                        remote_http_worker,
                        "_read_response_text",
                        return_value='{"choices":[{"message":{"content":"processed"},"finish_reason":"stop"}]}',
                    ),
                ):
                    result = remote_http_worker._read_openai_postprocess(
                        url,
                        "gpt-5.6-luna",
                        "source",
                        "en",
                        "context",
                        "vocabulary",
                        "instruction",
                        api_key,
                        flex_processing,
                        service_tier_fallback,
                        time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                    )
                self.assertEqual(result, {"text": "processed"})
                self.assertEqual(len(opened), 1)
                opened_url, method, body, headers = opened[0]
                self.assertEqual(opened_url, url.rstrip("/") + "/chat/completions")
                self.assertEqual(method, "POST")
                self.assertEqual(
                    headers,
                    {
                        "Accept": "application/json",
                        "Connection": "close",
                        "Content-Type": "application/json",
                        **({"Authorization": "Bearer secret-token"} if api_key else {}),
                    },
                )
                decoded_body = json.loads(body)
                expected_keys = {"model", "messages", "stream"}
                if expect_flex:
                    expected_keys.add("service_tier")
                self.assertEqual(set(decoded_body), expected_keys)
                self.assertEqual(decoded_body["model"], "gpt-5.6-luna")
                self.assertIs(decoded_body["stream"], False)
                if expect_flex:
                    self.assertEqual(decoded_body["service_tier"], "flex")
                if api_key and api_key in body.decode("utf-8"):
                    self.fail("API key leaked into request body")
                response.close.assert_called_once()
                connection.close.assert_called_once()

    def test_worker_openai_postprocess_service_tier_fallback_is_single_and_bounded(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        redirect_error = (
            '{"error":{"message":"service_tier unsupported",'
            '"type":"invalid_request_error","param":"service_tier",'
            '"code":"unsupported_parameter"}}'
        )
        success = '{"choices":[{"message":{"content":"processed"},"finish_reason":"stop"}]}'
        responses = [mock.Mock(status=400), mock.Mock(status=200)]
        connections = [mock.Mock(), mock.Mock()]
        opened: list[tuple[str, str, bytes]] = []

        def open_connection(
            url: str,
            _deadline_ns: int,
            *,
            headers: dict[str, str],
            method: str,
            body: bytes,
        ) -> tuple[object, object]:
            del headers
            index = len(opened)
            opened.append((url, method, body))
            return connections[index], responses[index]

        with (
            mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                side_effect=(redirect_error, success),
            ),
        ):
            result = remote_http_worker._read_openai_postprocess(
                "https://api.openai.com/v1",
                "gpt-5.6-luna",
                "source",
                "en",
                "",
                "",
                "",
                "secret-token",
                True,
                True,
                time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
            )
        self.assertEqual(result, {"text": "processed"})
        self.assertEqual(len(opened), 2)
        self.assertEqual([item[1] for item in opened], ["POST", "POST"])
        self.assertIn(b'"service_tier":"flex"', opened[0][2])
        self.assertNotIn(b'"service_tier"', opened[1][2])
        for response, connection in zip(responses, connections):
            response.close.assert_called_once()
            connection.close.assert_called_once()

    def test_worker_openai_postprocess_does_not_repeat_failed_fallback(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        response = mock.Mock(status=400)
        second_response = mock.Mock(status=400)
        connection = mock.Mock()
        second_connection = mock.Mock()
        opened: list[bytes] = []

        def open_connection(
            _url: str,
            _deadline_ns: int,
            *,
            headers: dict[str, str],
            method: str,
            body: bytes,
        ) -> tuple[object, object]:
            del headers, method
            opened.append(body)
            return (
                (connection, response)
                if len(opened) == 1
                else (second_connection, second_response)
            )

        with (
            mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                side_effect=(
                    '{"error":{"message":"service_tier unsupported",'
                    '"type":"invalid_request_error","param":"service_tier",'
                    '"code":"unsupported_parameter"}}',
                    '{"error":{"message":"still unsupported",'
                    '"type":"invalid_request_error","param":"service_tier",'
                    '"code":"unsupported_parameter"}}',
                ),
            ),
        ):
            with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                remote_http_worker._read_openai_postprocess(
                    "https://api.openai.com/v1",
                    "gpt-5.6-luna",
                    "source",
                    "en",
                    "",
                    "",
                    "",
                    "secret-token",
                    True,
                    True,
                    time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                )
        self.assertEqual(context.exception.code, "remote-http-failed")
        self.assertEqual(len(opened), 2)
        self.assertNotIn(b'"service_tier"', opened[1])
        for response_object, connection_object in (
            (response, connection),
            (second_response, second_connection),
        ):
            response_object.close.assert_called_once()
            connection_object.close.assert_called_once()

    def test_worker_openai_postprocess_gpt_payload_has_no_optional_generation_fields(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        body = remote_http_worker._openai_postprocess_request_body(
            "https://api.openai.com/v1/chat/completions",
            "gpt-5.6-luna",
            "synthetic text",
            "en",
            "",
            "",
            "",
            False,
            False,
        )
        keys = set(json.loads(body))
        self.assertEqual(keys, {"model", "messages", "stream"})
        self.assertFalse(
            keys
            & {
                "temperature",
                "max_tokens",
                "max_completion_tokens",
                "response_format",
                "reasoning_effort",
            }
        )

    def test_worker_openai_postprocess_generic_payload_has_no_optional_generation_fields(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        body = remote_http_worker._openai_postprocess_request_body(
            "http://127.0.0.1:8080/v1/chat/completions",
            "provider-chat-v1",
            "synthetic text",
            "en",
            "",
            "",
            "",
            False,
            False,
        )
        keys = set(json.loads(body))
        self.assertEqual(keys, {"model", "messages", "stream"})
        self.assertFalse(
            keys
            & {
                "temperature",
                "max_tokens",
                "max_completion_tokens",
                "response_format",
                "reasoning_effort",
            }
        )

    def test_worker_openai_postprocess_service_tier_requires_flex(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        for flex_processing in (False, True):
            with self.subTest(flex_processing=flex_processing):
                body = remote_http_worker._openai_postprocess_request_body(
                    "https://api.openai.com/v1/chat/completions",
                    "gpt-5.6-luna",
                    "synthetic text",
                    "en",
                    "",
                    "",
                    "",
                    flex_processing,
                    False,
                )
                payload = json.loads(body)
                self.assertEqual(
                    set(payload),
                    {"model", "messages", "stream"}
                    | ({"service_tier"} if flex_processing else set()),
                )
                if flex_processing:
                    self.assertEqual(payload.get("service_tier"), "flex")

    def test_worker_openai_postprocess_temperature_error_is_not_retried(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        response = mock.Mock(status=400)
        connection = mock.Mock()
        opened: list[bytes] = []

        def open_connection(
            _url: str,
            _deadline_ns: int,
            *,
            headers: dict[str, str],
            method: str,
            body: bytes,
        ) -> tuple[object, object]:
            del headers, method
            opened.append(body)
            return connection, response

        with (
            mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                return_value=(
                    '{"error":{"message":"Unsupported parameter: temperature",'
                    '"type":"invalid_request_error","param":"temperature",'
                    '"code":"unsupported_parameter"}}'
                ),
            ),
        ):
            with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                remote_http_worker._read_openai_postprocess(
                    "https://api.openai.com/v1",
                    "gpt-5.6-luna",
                    "source",
                    "en",
                    "",
                    "",
                    "",
                    "secret-token",
                    True,
                    False,
                    time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                )
        self.assertEqual(context.exception.code, "remote-http-failed")
        self.assertEqual(len(opened), 1)
        if "temperature" in json.loads(opened[0]):
            self.fail("temperature field triggered a retry path")
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_worker_openai_postprocess_loopback_rejects_temperature_and_succeeds_once(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        server: socket.socket | None = None
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(2)
        except OSError as error:
            if server is not None:
                server.close()
            if error.errno in {errno.EACCES, errno.EPERM}:
                self.skipTest("AF_INET loopback is blocked by the host sandbox")
            raise

        state = {
            "failed": False,
            "requests": 0,
            "successful_responses": 0,
            "temperature_seen": False,
        }

        def serve() -> None:
            assert server is not None
            try:
                server.settimeout(3.0)
                for _attempt in range(2):
                    connection, _address = server.accept()
                    with connection:
                        connection.settimeout(3.0)
                        request = bytearray()
                        while b"\r\n\r\n" not in request:
                            chunk = connection.recv(4096)
                            if not chunk or len(request) + len(chunk) > 65_536:
                                raise ValueError("invalid bounded request headers")
                            request.extend(chunk)
                        header_end = request.index(b"\r\n\r\n")
                        header_lines = bytes(request[:header_end]).split(b"\r\n")
                        content_lengths = [
                            int(line.split(b":", 1)[1].strip())
                            for line in header_lines
                            if line.lower().startswith(b"content-length:")
                        ]
                        if len(content_lengths) != 1:
                            raise ValueError("invalid content length")
                        content_length = content_lengths[0]
                        if not 0 <= content_length <= MAX_REQUEST_FRAME_BYTES:
                            raise ValueError("unbounded request body")
                        body = request[header_end + 4 :]
                        while len(body) < content_length:
                            chunk = connection.recv(
                                min(4096, content_length - len(body))
                            )
                            if not chunk:
                                raise ValueError("truncated request body")
                            body.extend(chunk)
                        payload = json.loads(bytes(body).decode("utf-8"))
                        has_temperature = "temperature" in payload
                        state["requests"] += 1
                        state["temperature_seen"] |= has_temperature
                        if has_temperature:
                            response_body = (
                                b'{"error":{"message":"Unsupported parameter: temperature",'
                                b'"type":"invalid_request_error","param":"temperature",'
                                b'"code":"unsupported_parameter"}}'
                            )
                            status = b"400 Bad Request"
                        else:
                            response_body = (
                                b'{"choices":[{"message":{"content":"processed"},'
                                b'"finish_reason":"stop"}]}'
                            )
                            status = b"200 OK"
                            state["successful_responses"] += 1
                        connection.sendall(
                            b"HTTP/1.1 "
                            + status
                            + b"\r\nContent-Type: application/json\r\n"
                            + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
                            + b"Connection: close\r\n\r\n"
                            + response_body
                        )
                        if not has_temperature:
                            return
            except BaseException:
                state["failed"] = True
            finally:
                server.close()

        provider = threading.Thread(target=serve, daemon=True)
        provider.start()
        sensitive_text = "private-transcript-sentinel"
        sensitive_key = "private-api-key-sentinel"
        try:
            result = remote_http_worker._read_openai_postprocess(
                f"http://127.0.0.1:{server.getsockname()[1]}/v1",
                "gpt-5.6-luna",
                sensitive_text,
                "en",
                "",
                "",
                "",
                sensitive_key,
                False,
                False,
                time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
            )
        finally:
            provider.join(timeout=4.0)
            if provider.is_alive():
                server.close()
                provider.join(timeout=1.0)
        if provider.is_alive() or state["failed"]:
            self.fail("private loopback provider did not finish cleanly")
        self.assertEqual(result, {"text": "processed"})
        self.assertEqual(state["requests"], 1)
        self.assertEqual(state["successful_responses"], 1)
        self.assertFalse(state["temperature_seen"])
        for sensitive_value in (sensitive_text, sensitive_key):
            if sensitive_value in repr(result):
                self.fail("sensitive request data escaped worker result")

    def test_worker_openai_postprocess_service_tier_fallback_requires_flag(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        response = mock.Mock(status=400)
        connection = mock.Mock()
        with (
            mock.patch.object(
                remote_http_worker,
                "_open_connection",
                return_value=(connection, response),
            ),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                return_value='{"error":{"message":"service_tier unsupported"}}',
            ),
        ):
            with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                remote_http_worker._read_openai_postprocess(
                    "https://api.openai.com/v1",
                    "gpt-5.6-luna",
                    "source",
                    "en",
                    "",
                    "",
                    "",
                    "secret-token",
                    True,
                    False,
                    time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                )
        self.assertEqual(context.exception.code, "remote-http-failed")
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_worker_openai_postprocess_http_error_gate_rejects_malformed_without_retry(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        deep = "0"
        for _ in range(70):
            deep = '{"nested":' + deep + "}"
        cases = (
            (
                "service tier nonfinite",
                '{"error":{"message":"service_tier unsupported","x":1e400}}',
            ),
            (
                "temperature nonfinite",
                '{"error":{"message":"temperature does not support 0","x":-1e400}}',
            ),
            (
                "constant nonfinite",
                '{"error":{"message":"service_tier unsupported","x":NaN}}',
            ),
            (
                "duplicate nested key",
                '{"error":{"message":"service_tier unsupported",'
                '"message":"service_tier unsupported"}}',
            ),
            (
                "forbidden nested key",
                '{"error":{"message":"service_tier unsupported",' '"__proto__":{}}}',
            ),
            (
                "unexpected nested field",
                '{"error":{"message":"service_tier unsupported","x":"bad"}}',
            ),
            (
                "wrong optional type",
                '{"error":{"message":"service_tier unsupported","type":1}}',
            ),
            (
                "boolean optional type",
                '{"error":{"message":"service_tier unsupported","param":false}}',
            ),
            (
                "secret collision",
                '{"error":{"message":"service_tier unsupported secret-token"}}',
            ),
            (
                "too deep",
                '{"error":{"message":"service_tier unsupported",' f'"metadata":{deep}}}',
            ),
            ("wrong error shape", '{"error":"service_tier unsupported"}'),
            ("malformed json", '{"error":{"message":"service_tier unsupported"}'),
        )
        for name, error_body in cases:
            with self.subTest(name=name):
                response = mock.Mock(status=400)
                connection = mock.Mock()
                opened: list[bytes] = []

                def open_connection(
                    _url: str,
                    _deadline_ns: int,
                    *,
                    headers: dict[str, str],
                    method: str,
                    body: bytes,
                ) -> tuple[object, object]:
                    del headers, method
                    opened.append(body)
                    return connection, response

                with (
                    mock.patch.object(
                        remote_http_worker,
                        "_open_connection",
                        side_effect=open_connection,
                    ),
                    mock.patch.object(
                        remote_http_worker,
                        "_read_response_text",
                        return_value=error_body,
                    ),
                ):
                    with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                        remote_http_worker._read_openai_postprocess(
                            "https://api.openai.com/v1",
                            "gpt-5.6-luna",
                            "source",
                            "en",
                            "",
                            "",
                            "",
                            "secret-token",
                            True,
                            True,
                            time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                        )
                self.assertEqual(context.exception.code, "remote-http-failed")
                self.assertEqual(len(opened), 1)
                response.close.assert_called_once()
                connection.close.assert_called_once()

    def test_worker_openai_postprocess_response_hostile_cases_fail_closed(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        deep = "null"
        for _ in range(70):
            deep = "{\"nested\":" + deep + "}"
        hostile = (
            ('{"choices":[],"choices":[]}', "remote-response-invalid"),
            ('{"choices":[{"message":{"content":"ok"},"finish_reason":1e400}]}', "remote-response-invalid"),
            ('{"choices":[{"message":{"content":"ok"},"finish_reason":NaN}]}', "remote-response-invalid"),
            ('{"choices":[{"message":{"content":"ok"}}],"metadata":' + deep + "}", "remote-response-invalid"),
            ('{"__proto__":{},"choices":[{"message":{"content":"ok"}}]}', "remote-response-invalid"),
            ('{"error":"backend failed","choices":[{"message":{"content":"ok"}}]}', "remote-response-invalid"),
            ('{"error":{"message":"backend failed"},"choices":[{"message":{"content":"ok"}}]}', "remote-response-invalid"),
            ('{"metadata":{"secret-token":"x"},"choices":[{"message":{"content":"ok"}}]}', "remote-response-invalid"),
            ('{"metadata":{"nested":"contains secret-token"},"choices":[{"message":{"content":"ok"}}]}', "remote-response-invalid"),
            ('{"choices":[]}', "remote-response-invalid"),
            ('{"choices":[{"message":{"content":[]}}]}', "remote-response-invalid"),
            ('{"choices":[{"message":{"content":"ok"},"finish_reason":"length"}]}', "remote-response-invalid"),
        )
        for raw_text, expected_code in hostile:
            with self.subTest(raw_text=raw_text[:64]):
                with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                    remote_http_worker._decode_openai_postprocess_response(
                        raw_text,
                        "source",
                        "secret-token",
                    )
                self.assertEqual(context.exception.code, expected_code)

        result = remote_http_worker._decode_openai_postprocess_response(
            '{"choices":[{"message":{"content":[{"type":"text","text":"Transcript: source"}]},'
            '"finish_reason":"stop"}]}',
            "source",
            "",
        )
        self.assertEqual(result, {"text": "source"})

    def test_worker_openai_postprocess_request_body_is_bounded(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        with (
            mock.patch.object(
                remote_http_worker,
                "build_openai_compatible_messages",
                return_value=[{"role": "user", "content": "x"}],
            ),
            mock.patch.object(remote_http_worker.remote_http, "MAX_REQUEST_FRAME_BYTES", 32),
        ):
            with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                remote_http_worker._openai_postprocess_request_body(
                    "https://api.openai.com/v1/chat/completions",
                    "gpt-5.6-luna",
                    "source",
                    "en",
                    "",
                    "",
                    "",
                    True,
                    False,
                )
        self.assertEqual(context.exception.code, "remote-request-invalid")

    def test_worker_openai_postprocess_response_bytes_are_bounded_and_utf8_checked(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        for name, chunk, expected_code in (
            ("oversize", b"x" * 1_500_001, "remote-response-too-large"),
            ("invalid utf8", b"\xff", "remote-response-invalid"),
        ):
            with self.subTest(name=name):
                response = mock.Mock(status=200)
                response.read.side_effect = (chunk, b"")
                connection = mock.Mock()
                with mock.patch.object(
                    remote_http_worker,
                    "_open_connection",
                    return_value=(connection, response),
                ):
                    with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                        remote_http_worker._read_openai_postprocess(
                            "https://api.openai.com/v1",
                            "gpt-5.6-luna",
                            "source",
                            "en",
                            "",
                            "",
                            "",
                            "secret-token",
                            True,
                            False,
                            time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                        )
                self.assertEqual(context.exception.code, expected_code)
                response.close.assert_called_once()
                connection.close.assert_called_once()

    def test_worker_postprocess_preserves_method_on_same_origin_redirect_only(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        redirect_response = mock.Mock(status=307)
        redirect_response.getheader.return_value = "/base/api/generate-2"
        success_response = mock.Mock(status=200)
        redirect_connection = mock.Mock()
        success_connection = mock.Mock()
        opened: list[tuple[str, str, bytes]] = []

        def open_connection(
            url: str,
            _deadline_ns: int,
            *,
            headers: dict[str, str],
            method: str,
            body: bytes,
        ) -> tuple[object, object]:
            del headers
            opened.append((url, method, body))
            if len(opened) == 1:
                return redirect_connection, redirect_response
            return success_connection, success_response

        with (
            mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                return_value='{"done":true,"response":"ok"}',
            ),
        ):
            result = remote_http_worker._read_ollama_postprocess(
                "http://127.0.0.1:11434/base",
                "model",
                "source",
                "en",
                "",
                "",
                "",
                time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
            )
        self.assertEqual(result, {"text": "ok"})
        self.assertEqual([item[0] for item in opened], [
            "http://127.0.0.1:11434/base/api/generate",
            "http://127.0.0.1:11434/base/api/generate-2",
        ])
        self.assertEqual([item[1] for item in opened], ["POST", "POST"])
        self.assertEqual(opened[0][2], opened[1][2])
        redirect_response.close.assert_called_once()
        success_response.close.assert_called_once()
        redirect_connection.close.assert_called_once()
        success_connection.close.assert_called_once()

    def test_worker_openai_postprocess_preserves_post_on_307_and_308(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        for status in (307, 308):
            with self.subTest(status=status):
                redirect_response = mock.Mock(status=status)
                redirect_response.getheader.return_value = "/v1/chat/completions-2"
                success_response = mock.Mock(status=200)
                redirect_connection = mock.Mock()
                success_connection = mock.Mock()
                opened: list[tuple[str, str, bytes]] = []

                def open_connection(
                    url: str,
                    _deadline_ns: int,
                    *,
                    headers: dict[str, str],
                    method: str,
                    body: bytes,
                ) -> tuple[object, object]:
                    del headers
                    opened.append((url, method, body))
                    if len(opened) == 1:
                        return redirect_connection, redirect_response
                    return success_connection, success_response

                with (
                    mock.patch.object(remote_http_worker, "_open_connection", side_effect=open_connection),
                    mock.patch.object(
                        remote_http_worker,
                        "_read_response_text",
                        return_value='{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}',
                    ),
                ):
                    result = remote_http_worker._read_openai_postprocess(
                        "https://api.openai.com/v1",
                        "gpt-5.6-luna",
                        "source",
                        "en",
                        "",
                        "",
                        "",
                        "secret-token",
                        True,
                        False,
                        time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                    )
                self.assertEqual(result, {"text": "ok"})
                self.assertEqual(
                    [item[0] for item in opened],
                    [
                        "https://api.openai.com/v1/chat/completions",
                        "https://api.openai.com/v1/chat/completions-2",
                    ],
                )
                self.assertEqual([item[1] for item in opened], ["POST", "POST"])
                self.assertEqual(opened[0][2], opened[1][2])
                redirect_response.close.assert_called_once()
                success_response.close.assert_called_once()
                redirect_connection.close.assert_called_once()
                success_connection.close.assert_called_once()

    def test_worker_openai_postprocess_rejects_301_302_303_and_cross_origin_redirects(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        cases = (
            (301, "/v1/chat/completions-2"),
            (302, "/v1/chat/completions-2"),
            (303, "/v1/chat/completions-2"),
            (307, "https://example.invalid/chat/completions"),
            (307, "http://127.0.0.1:1/chat/completions"),
        )
        for status, location in cases:
            with self.subTest(status=status, location=location):
                response = mock.Mock(status=status)
                response.getheader.return_value = location
                connection = mock.Mock()
                with mock.patch.object(
                    remote_http_worker,
                    "_open_connection",
                    return_value=(connection, response),
                ):
                    with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                        remote_http_worker._read_openai_postprocess(
                            "https://api.openai.com/v1",
                            "gpt-5.6-luna",
                            "source",
                            "en",
                            "",
                            "",
                            "",
                            "secret-token",
                            True,
                            True,
                            time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                        )
                self.assertEqual(context.exception.code, "remote-url-unsafe")
                response.close.assert_called_once()
                connection.close.assert_called_once()

    def test_worker_postprocess_rejects_non_method_preserving_or_cross_origin_redirect(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        cases = (
            (302, "/base/api/generate-2"),
            (307, "https://example.invalid/api/generate"),
            (307, "http://127.0.0.2:11434/api/generate"),
        )
        for status, location in cases:
            with self.subTest(status=status, location=location):
                response = mock.Mock(status=status)
                response.getheader.return_value = location
                connection = mock.Mock()
                with mock.patch.object(
                    remote_http_worker,
                    "_open_connection",
                    return_value=(connection, response),
                ):
                    with self.assertRaises(remote_http_worker._WorkerFailure) as context:
                        remote_http_worker._read_ollama_postprocess(
                            "http://127.0.0.1:11434/base",
                            "model",
                            "source",
                            "en",
                            "",
                            "",
                            "",
                            time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                        )
                self.assertEqual(context.exception.code, "remote-url-unsafe")
                response.close.assert_called_once()
                connection.close.assert_called_once()

    def test_http_reader_primary_failure_survives_resource_close_failures(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        cases = (
            (
                "ollama deadline",
                remote_http_worker._read_listing,
                ("http://127.0.0.1:1", time.monotonic_ns() + LISTING_DEADLINE_NS),
                remote_http_worker._WorkerDeadline(),
            ),
            (
                "openai deadline",
                remote_http_worker._read_openai_listing,
                (
                    "http://127.0.0.1:1",
                    "secret-token",
                    time.monotonic_ns() + LISTING_DEADLINE_NS,
                ),
                remote_http_worker._WorkerDeadline(),
            ),
            (
                "ollama worker failure",
                remote_http_worker._read_listing,
                ("http://127.0.0.1:1", time.monotonic_ns() + LISTING_DEADLINE_NS),
                remote_http_worker._WorkerFailure("remote-response-invalid"),
            ),
            (
                "openai worker failure",
                remote_http_worker._read_openai_listing,
                (
                    "http://127.0.0.1:1",
                    "secret-token",
                    time.monotonic_ns() + LISTING_DEADLINE_NS,
                ),
                remote_http_worker._WorkerFailure("remote-response-invalid"),
            ),
        )
        for name, reader, args, primary in cases:
            with self.subTest(name=name):
                response = mock.Mock(status=200)
                connection = mock.Mock()
                response.close.side_effect = OSError("response close failed")
                connection.close.side_effect = OSError("connection close failed")
                with (
                    mock.patch.object(
                        remote_http_worker,
                        "_open_connection",
                        return_value=(connection, response),
                    ),
                    mock.patch.object(
                        remote_http_worker,
                        "_read_response_text",
                        side_effect=primary,
                    ),
                ):
                    with self.assertRaises(type(primary)) as context:
                        reader(*args)
                self.assertIs(context.exception, primary)
                response.close.assert_called_once()
                connection.close.assert_called_once()
                if isinstance(primary, remote_http_worker._WorkerFailure):
                    self.assertEqual(context.exception.code, primary.code)

    def test_http_resource_close_abort_priority_and_both_attempts(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        cases = (
            (
                "response deadline",
                remote_http_worker._WorkerDeadline(),
                OSError("connection close failed"),
                remote_http_worker._WorkerDeadline(),
            ),
            (
                "connection deadline",
                OSError("response close failed"),
                remote_http_worker._WorkerDeadline(),
                remote_http_worker._WorkerDeadline(),
            ),
            (
                "response keyboard interrupt",
                KeyboardInterrupt(),
                None,
                KeyboardInterrupt(),
            ),
            (
                "connection system exit",
                None,
                SystemExit(17),
                SystemExit(17),
            ),
            (
                "ordinary close errors",
                OSError("response close failed"),
                OSError("connection close failed"),
                remote_http_worker._WorkerFailure("remote-operation-failed"),
            ),
        )
        for name, response_error, connection_error, expected in cases:
            with self.subTest(name=name):
                response = mock.Mock()
                connection = mock.Mock()
                response.close.side_effect = response_error
                connection.close.side_effect = connection_error
                expected_type = type(expected)
                with self.assertRaises(expected_type) as context:
                    remote_http_worker._close_http_resources(response, connection)
                response.close.assert_called_once()
                connection.close.assert_called_once()
                if isinstance(expected, remote_http_worker._WorkerFailure):
                    self.assertEqual(context.exception.code, expected.code)
                elif isinstance(expected, (remote_http_worker._WorkerDeadline, KeyboardInterrupt)):
                    self.assertEqual(context.exception.args, expected.args)
                elif isinstance(expected, SystemExit):
                    self.assertEqual(context.exception.code, expected.code)

    def test_open_connection_close_abort_wins_over_ordinary_request_error(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        close_cases = (
            ("deadline", remote_http_worker._WorkerDeadline(), remote_http_worker._WorkerDeadline),
            ("keyboard interrupt", KeyboardInterrupt(), KeyboardInterrupt),
            ("system exit", SystemExit(23), SystemExit),
        )
        for name, close_error, expected_type in close_cases:
            with self.subTest(name=name):
                connection = mock.Mock()
                connection.request.side_effect = OSError("request failed")
                connection.close.side_effect = close_error
                with (
                    mock.patch.object(remote_http_worker, "_resolve_addresses", return_value=()),
                    mock.patch.object(
                        remote_http_worker,
                        "_PinnedHTTPConnection",
                        return_value=connection,
                    ),
                ):
                    with self.assertRaises(expected_type) as context:
                        remote_http_worker._open_connection(
                            "http://127.0.0.1:11434",
                            time.monotonic_ns() + LISTING_DEADLINE_NS,
                        )
                connection.close.assert_called_once()
                if isinstance(close_error, SystemExit):
                    self.assertEqual(context.exception.code, close_error.code)

    def test_open_connection_semantic_request_error_survives_close_abort(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        cases = (
            (
                "deadline primary",
                remote_http_worker._WorkerDeadline(),
                KeyboardInterrupt(),
                remote_http_worker._WorkerDeadline,
            ),
            (
                "worker failure primary",
                remote_http_worker._WorkerFailure("remote-response-invalid"),
                SystemExit(29),
                remote_http_worker._WorkerFailure,
            ),
        )
        for name, request_error, close_error, expected_type in cases:
            with self.subTest(name=name):
                connection = mock.Mock()
                connection.request.side_effect = request_error
                connection.close.side_effect = close_error
                with (
                    mock.patch.object(remote_http_worker, "_resolve_addresses", return_value=()),
                    mock.patch.object(
                        remote_http_worker,
                        "_PinnedHTTPConnection",
                        return_value=connection,
                    ),
                ):
                    with self.assertRaises(expected_type) as context:
                        remote_http_worker._open_connection(
                            "http://127.0.0.1:11434",
                            time.monotonic_ns() + LISTING_DEADLINE_NS,
                        )
                connection.close.assert_called_once()
                if isinstance(request_error, remote_http_worker._WorkerFailure):
                    self.assertEqual(context.exception.code, request_error.code)

    def test_worker_control_credentials_are_required_per_recvmsg_chunk(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        expected = (os.getpid(), os.getuid(), os.getgid())
        credentials = remote_http._UNIX_CREDENTIALS.pack(*expected)
        sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            receiver.setblocking(False)
            sender.sendmsg(
                [b"a"],
                [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)],
            )
            self.assertEqual(
                remote_http_worker._recv_control_chunk(
                    receiver,
                    64,
                    expected,
                    "remote-request-invalid",
                ),
                b"a",
            )
            sender.sendmsg(
                [b"b"],
                [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)],
            )
            self.assertEqual(
                remote_http_worker._recv_control_chunk(
                    receiver,
                    64,
                    expected,
                    "remote-request-invalid",
                ),
                b"b",
            )
        finally:
            sender.close()
            receiver.close()

        sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            receiver.setblocking(False)
            sender.send(b"missing")
            with self.assertRaises(remote_http_worker._WorkerFailure):
                remote_http_worker._recv_control_chunk(
                    receiver,
                    64,
                    expected,
                    "remote-request-invalid",
                )
        finally:
            sender.close()
            receiver.close()

        sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            receiver.setblocking(False)
            sender.sendmsg(
                [b"wrong"],
                [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)],
            )
            wrong_expected = (expected[0] + 1, expected[1], expected[2])
            with self.assertRaises(remote_http_worker._WorkerFailure):
                remote_http_worker._recv_control_chunk(
                    receiver,
                    64,
                    wrong_expected,
                    "remote-request-invalid",
                )
        finally:
            sender.close()
            receiver.close()

    def test_worker_control_release_is_explicit_and_response_bound(self) -> None:
        probe_sender, probe_receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            try:
                probe_receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EPERM}:
                    self.skipTest("SO_PASSCRED is blocked by the host sandbox")
                raise
        finally:
            probe_sender.close()
            probe_receiver.close()

        from speed_of_cinnamon import remote_http_worker

        expected = (os.getpid(), os.getuid(), os.getgid())
        credentials = remote_http._UNIX_CREDENTIALS.pack(*expected)
        nonce = NONCE
        response = encode_response(_success([]))
        valid = remote_http._encode_control_release(nonce, response)
        wrong_nonce = remote_http._encode_control_release(
            "fedcba9876543210fedcba9876543210",
            response,
        )
        wrong_digest = bytearray(valid)
        wrong_digest[-1] ^= 1
        replayed = remote_http._encode_control_release(nonce, encode_response(_success([_model()])))
        cases = (
            ("valid", valid, response, True),
            ("wrong nonce", wrong_nonce, response, False),
            ("wrong digest", bytes(wrong_digest), response, False),
            ("replayed", replayed, response, False),
            ("extra bytes", valid + b"x", response, False),
        )
        for name, release, expected_response, accepted in cases:
            with self.subTest(name=name):
                sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
                pidfd = os.pidfd_open(os.getpid(), 0)
                try:
                    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                    receiver.setblocking(False)
                    sender.sendmsg(
                        [release],
                        [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)],
                    )
                    if accepted:
                        remote_http_worker._wait_for_release(
                            receiver,
                            pidfd,
                            expected,
                            nonce,
                            expected_response,
                            time.monotonic_ns() + 1_000_000_000,
                        )
                    else:
                        with self.assertRaises(remote_http_worker._WorkerFailure):
                            remote_http_worker._wait_for_release(
                                receiver,
                                pidfd,
                                expected,
                                nonce,
                                expected_response,
                                time.monotonic_ns() + 1_000_000_000,
                            )
                finally:
                    os.close(pidfd)
                    sender.close()
                    receiver.close()

        sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        pidfd = os.pidfd_open(os.getpid(), 0)
        try:
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            receiver.setblocking(False)
            sender.close()
            with self.assertRaises(remote_http_worker._WorkerFailure):
                remote_http_worker._wait_for_release(
                    receiver,
                    pidfd,
                    expected,
                    nonce,
                    response,
                    time.monotonic_ns() + 1_000_000_000,
                )
        finally:
            os.close(pidfd)
            receiver.close()

    def test_real_listing_result_reaches_worker_response_without_second_decode(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        deadline = time.monotonic_ns() + LISTING_DEADLINE_NS
        request = _request(deadline_monotonic_ns=deadline)
        request_frame = encode_request(request)
        response_frames: list[bytes] = []
        fake_connection = mock.Mock()
        fake_response = mock.Mock(status=200)
        fake_control = mock.Mock()
        with (
            mock.patch.object(
                remote_http_worker,
                "_open_control_socket",
                return_value=(fake_control, (123, 1000, 1000)),
            ),
            mock.patch.object(remote_http_worker, "_read_request_frame", return_value=request_frame),
            mock.patch.object(remote_http_worker, "_install_deadline"),
            mock.patch.object(
                remote_http_worker,
                "_open_connection",
                return_value=(fake_connection, fake_response),
            ),
            mock.patch.object(
                remote_http_worker,
                "_read_response_text",
                return_value='{"models":[]}',
            ),
            mock.patch.object(
                remote_http_worker,
                "_write_response_frame",
                side_effect=lambda _parent_pidfd, frame, _deadline: response_frames.append(frame),
            ),
            mock.patch.object(remote_http_worker, "_wait_for_release") as release,
        ):
            exit_code = remote_http_worker._run(-1)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(response_frames), 1)
        self.assertEqual(
            decode_response(response_frames[0], expected_nonce=NONCE),
            _success([]),
        )
        release.assert_called_once()
        fake_response.close.assert_called_once()
        fake_connection.close.assert_called_once()

    def test_real_openai_listing_reaches_operation_specific_worker_response(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        deadline = time.monotonic_ns() + LISTING_DEADLINE_NS
        request = _openai_request(
            api_key="  secret-token  ",
            deadline_monotonic_ns=deadline,
        )
        request_frame = encode_request(request)
        response_frames: list[bytes] = []
        fake_control = mock.Mock()
        result = _openai_success([_openai_model("gpt-test")])
        with (
            mock.patch.object(
                remote_http_worker,
                "_open_control_socket",
                return_value=(fake_control, (123, 1000, 1000)),
            ),
            mock.patch.object(remote_http_worker, "_read_request_frame", return_value=request_frame),
            mock.patch.object(remote_http_worker, "_install_deadline"),
            mock.patch.object(
                remote_http_worker,
                "_read_openai_listing",
                return_value=result["result"],
            ) as read_listing,
            mock.patch.object(
                remote_http_worker,
                "_write_response_frame",
                side_effect=lambda _parent_pidfd, frame, _deadline: response_frames.append(frame),
            ),
            mock.patch.object(remote_http_worker, "_wait_for_release"),
        ):
            exit_code = remote_http_worker._run(-1)
        self.assertEqual(exit_code, 0)
        read_listing.assert_called_once_with(
            "http://127.0.0.1:11434",
            "secret-token",
            deadline,
        )
        self.assertEqual(
            decode_response(
                response_frames[0],
                expected_nonce=NONCE,
                operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
            ),
            result,
        )

    def test_real_openai_postprocess_reaches_operation_specific_worker_response(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        deadline = time.monotonic_ns() + POSTPROCESS_DEADLINE_NS
        request = _openai_postprocess_request(
            api_key="  secret-token  ",
            deadline_monotonic_ns=deadline,
        )
        request_frame = encode_request(request)
        response_frames: list[bytes] = []
        fake_control = mock.Mock()
        result = _postprocess_success("processed")
        with (
            mock.patch.object(
                remote_http_worker,
                "_open_control_socket",
                return_value=(fake_control, (123, 1000, 1000)),
            ),
            mock.patch.object(remote_http_worker, "_read_request_frame", return_value=request_frame),
            mock.patch.object(remote_http_worker, "_install_deadline"),
            mock.patch.object(
                remote_http_worker,
                "_read_openai_postprocess",
                return_value=result["result"],
            ) as read_postprocess,
            mock.patch.object(
                remote_http_worker,
                "_write_response_frame",
                side_effect=lambda _parent_pidfd, frame, _deadline: response_frames.append(frame),
            ),
            mock.patch.object(remote_http_worker, "_wait_for_release"),
        ):
            exit_code = remote_http_worker._run(-1)
        self.assertEqual(exit_code, 0)
        read_postprocess.assert_called_once_with(
            "https://api.openai.com/v1",
            "gpt-5.6-luna",
            "Transcript: hello world",
            "en",
            "",
            "",
            "",
            "secret-token",
            True,
            False,
            deadline,
        )
        self.assertEqual(
            decode_response(
                response_frames[0],
                expected_nonce=NONCE,
                operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
            ),
            result,
        )

    def test_real_worker_loopback_success_uses_release_handshake(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server_port = server.getsockname()[1]
        server_pid = os.fork()
        if server_pid == 0:
            exit_code = 0
            try:
                server.settimeout(2.0)
                connection, _address = server.accept()
                try:
                    connection.settimeout(2.0)
                    connection.recv(4096)
                    body = b'{"models":[]}'
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        + f"Content-Length: {len(body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                        + body
                    )
                finally:
                    connection.close()
            except Exception:
                exit_code = 1
            finally:
                server.close()
            os._exit(exit_code)
        server.close()
        process: subprocess.Popen[bytes] | None = None
        handle: remote_http._WorkerHandle | None = None
        try:
            source_root = Path(__file__).resolve().parents[1] / "src"
            worker_script = (
                "import os\n"
                "from speed_of_cinnamon import remote_http_worker\n"
                "parent_pidfd = remote_http_worker._bind_parent()\n"
                "remote_http_worker._install_no_fork_boundary()\n"
                "os._exit(remote_http_worker._run(parent_pidfd))\n"
            )
            request_deadline = time.monotonic_ns() + LISTING_DEADLINE_NS
            request = _request(
                deadline_monotonic_ns=request_deadline,
                payload={"url": f"http://127.0.0.1:{server_port}"},
            )
            request_frame = encode_request(request)
            control_parent, control_child = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", worker_script],
                stdin=control_child.fileno(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                cwd=source_root.parent,
                env={
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "LC_CTYPE": "C.UTF-8",
                    "PYTHONPATH": str(source_root),
                },
            )
            control_child.close()
            process.stdin = control_parent
            pidfd = os.pidfd_open(process.pid, 0)
            start_time = remote_http._process_start_time(process.pid)
            self.assertIsNotNone(start_time)
            handle = remote_http._WorkerHandle(process, process.pid, start_time or "", pidfd)
            received = remote_http._pump_worker(
                handle,
                request_frame,
                request_deadline,
                time.monotonic_ns,
                None,
            )
            self.assertIsNone(process.poll())
            self.assertEqual(
                decode_response(received, expected_nonce=NONCE),
                _success([]),
            )
            self.assertTrue(
                remote_http._cleanup_worker(
                    handle,
                    time.monotonic_ns() + 5_000_000_000,
                    time.monotonic_ns,
                )
            )
            self.assertEqual(process.wait(timeout=2.0), 0)
        finally:
            if handle is not None and process is not None and process.poll() is None:
                remote_http._cleanup_worker(
                    handle,
                    time.monotonic_ns() + 2_000_000_000,
                    time.monotonic_ns,
                )
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            _pid, _status = os.waitpid(server_pid, 0)

    def test_production_spawn_real_loopback_success(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server_port = server.getsockname()[1]
        server_pid = os.fork()
        if server_pid == 0:
            exit_code = 0
            try:
                server.settimeout(3.0)
                connection, _address = server.accept()
                try:
                    connection.settimeout(3.0)
                    connection.recv(4096)
                    body = b'{"models":[]}'
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        + f"Content-Length: {len(body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                        + body
                    )
                finally:
                    connection.close()
            except Exception:
                exit_code = 1
            finally:
                server.close()
            os._exit(exit_code)
        server.close()
        try:
            with _isolated_worker_python() as worker_python:
                request = _request(
                    deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS,
                    payload={"url": f"http://127.0.0.1:{server_port}"},
                )
                with mock.patch.object(remote_http.sys, "executable", str(worker_python)):
                    result = remote_http.run_list_ollama_models(request)
            self.assertEqual(result, _success([]))
        finally:
            _pid, status = os.waitpid(server_pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    def test_production_spawn_real_postprocess_loopback_secret_boundary(self) -> None:
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError as error:
            if error.errno == errno.EPERM:
                self.skipTest("AF_INET loopback is blocked by the host sandbox")
            raise
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server_port = server.getsockname()[1]
        report_read, report_write = os.pipe()
        server_pid = os.fork()
        source_text = "transcript-sensitive-sentinel"
        context = "context-sensitive-sentinel"
        vocabulary = "vocabulary-sensitive-sentinel"
        instruction = "instruction-sensitive-sentinel"
        if server_pid == 0:
            os.close(report_read)
            exit_code = 0
            try:
                server.settimeout(5.0)
                connection, _address = server.accept()
                try:
                    connection.settimeout(5.0)
                    request_bytes = bytearray()
                    while b"\r\n\r\n" not in request_bytes:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        request_bytes.extend(chunk)
                    header_end = request_bytes.find(b"\r\n\r\n")
                    if header_end < 0:
                        raise OSError
                    header_bytes = bytes(request_bytes[:header_end]).lower()
                    content_length = next(
                        int(line.split(b":", 1)[1].strip())
                        for line in header_bytes.split(b"\r\n")
                        if line.startswith(b"content-length:")
                    )
                    body_start = header_end + 4
                    while len(request_bytes) - body_start < content_length:
                        chunk = connection.recv(4096)
                        if not chunk:
                            raise OSError
                        request_bytes.extend(chunk)
                    os.write(report_write, bytes(request_bytes))
                    response_body = json.dumps(
                        {
                            "done": True,
                            "response": f"Transcript: {source_text}",
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                        + response_body
                    )
                finally:
                    connection.close()
            except Exception:
                exit_code = 1
            finally:
                server.close()
                os.close(report_write)
            os._exit(exit_code)
        server.close()
        os.close(report_write)
        observed_proc_data: list[tuple[bytes, bytes]] = []
        try:
            with _isolated_worker_python() as worker_python:
                deadline = time.monotonic_ns() + POSTPROCESS_DEADLINE_NS
                request = _postprocess_request(
                    text=source_text,
                    personal_context=context,
                    vocabulary=vocabulary,
                    prompt=instruction,
                    url=f"http://127.0.0.1:{server_port}",
                    deadline_monotonic_ns=deadline,
                )
                real_pump = remote_http._pump_worker

                def inspect_worker(
                    handle: remote_http._WorkerHandle,
                    request_frame: bytes,
                    deadline_ns: int,
                    clock: object,
                    cancel: object,
                ) -> bytes:
                    cmdline = Path(f"/proc/{handle.pid}/cmdline").read_bytes()
                    environ = Path(f"/proc/{handle.pid}/environ").read_bytes()
                    observed_proc_data.append((cmdline, environ))
                    return real_pump(
                        handle,
                        request_frame,
                        deadline_ns,
                        clock,  # type: ignore[arg-type]
                        cancel,  # type: ignore[arg-type]
                    )

                with (
                    mock.patch.object(remote_http.sys, "executable", str(worker_python)),
                    mock.patch.object(remote_http, "_pump_worker", side_effect=inspect_worker),
                ):
                    result = remote_http.run_postprocess_ollama(request)
            request_bytes = os.read(report_read, 2_000_000)
            self.assertEqual(result, _postprocess_success(source_text))
            self.assertIn(b"POST /api/generate HTTP/1.1", request_bytes)
            self.assertIn(b"Accept: application/json", request_bytes)
            self.assertIn(b"Content-Type: application/json", request_bytes)
            self.assertIn(b"Connection: close", request_bytes)
            self.assertNotIn(b"Authorization:", request_bytes)
            header_end = request_bytes.find(b"\r\n\r\n")
            self.assertGreaterEqual(header_end, 0)
            body = json.loads(request_bytes[header_end + 4 :])
            self.assertEqual(set(body), {"model", "prompt", "stream"})
            for sentinel in (source_text, context, vocabulary, instruction):
                self.assertIn(sentinel, body["prompt"])
            self.assertTrue(observed_proc_data)
            for cmdline, environ in observed_proc_data:
                for sentinel in (source_text, context, vocabulary, instruction):
                    self.assertNotIn(sentinel.encode("utf-8"), cmdline)
                    self.assertNotIn(sentinel.encode("utf-8"), environ)
        finally:
            os.close(report_read)
            _pid, status = os.waitpid(server_pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    def test_production_spawn_real_openai_loopback_auth_and_secret_boundary(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server_port = server.getsockname()[1]
        report_read, report_write = os.pipe()
        server_pid = os.fork()
        if server_pid == 0:
            os.close(report_read)
            exit_code = 0
            try:
                server.settimeout(3.0)
                connection, _address = server.accept()
                try:
                    connection.settimeout(3.0)
                    request_bytes = bytearray()
                    while b"\r\n\r\n" not in request_bytes:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        request_bytes.extend(chunk)
                    os.write(report_write, bytes(request_bytes))
                    body = (
                        b'{"object":"list","data":[{"id":"gpt-test",'
                        b'"owned_by":"provider","description":"discard me"}]}'
                    )
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        + f"Content-Length: {len(body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                        + body
                    )
                finally:
                    connection.close()
            except Exception:
                exit_code = 1
            finally:
                server.close()
                os.close(report_write)
            os._exit(exit_code)
        server.close()
        os.close(report_write)
        secret = "secret-token"
        observed_proc_data: list[tuple[bytes, bytes]] = []
        try:
            with _isolated_worker_python() as worker_python:
                request = _openai_request(
                    api_key=f"  {secret}  ",
                    deadline_monotonic_ns=time.monotonic_ns() + LISTING_DEADLINE_NS,
                    payload={"url": f"http://127.0.0.1:{server_port}", "api_key": f"  {secret}  "},
                )
                real_pump = remote_http._pump_worker

                def inspect_worker(
                    handle: remote_http._WorkerHandle,
                    request_frame: bytes,
                    deadline_ns: int,
                    clock: object,
                    cancel: object,
                ) -> bytes:
                    cmdline = Path(f"/proc/{handle.pid}/cmdline").read_bytes()
                    environ = Path(f"/proc/{handle.pid}/environ").read_bytes()
                    observed_proc_data.append((cmdline, environ))
                    return real_pump(
                        handle,
                        request_frame,
                        deadline_ns,
                        clock,  # type: ignore[arg-type]
                        cancel,  # type: ignore[arg-type]
                    )

                with (
                    mock.patch.object(remote_http.sys, "executable", str(worker_python)),
                    mock.patch.object(remote_http, "_pump_worker", side_effect=inspect_worker),
                ):
                    result = remote_http.run_list_openai_compatible_models(request)
            request_bytes = os.read(report_read, 65_536)
            self.assertEqual(result, _openai_success([_openai_model("gpt-test")]))
            self.assertIn(b"GET /models HTTP/1.1", request_bytes)
            self.assertIn(b"Authorization: Bearer secret-token", request_bytes)
            self.assertIn(b"Accept: application/json", request_bytes)
            self.assertIn(b"Content-Type: application/json", request_bytes)
            self.assertIn(b"Connection: close", request_bytes)
            self.assertNotIn(secret.encode("ascii"), repr(result).encode("utf-8"))
            self.assertTrue(observed_proc_data)
            for cmdline, environ in observed_proc_data:
                self.assertNotIn(secret.encode("ascii"), cmdline)
                self.assertNotIn(secret.encode("ascii"), environ)
        finally:
            os.close(report_read)
            _pid, status = os.waitpid(server_pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    def test_production_spawn_real_openai_postprocess_loopback_auth_secret_boundary(self) -> None:
        server: socket.socket | None = None
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError as error:
            if error.errno == errno.EPERM:
                self.skipTest("AF_INET loopback is blocked by the host sandbox")
            raise
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server_port = server.getsockname()[1]
        report_read, report_write = os.pipe()
        server_pid = os.fork()
        source_text = "transcript-sensitive-sentinel"
        context = "context-sensitive-sentinel"
        vocabulary = "vocabulary-sensitive-sentinel"
        instruction = "instruction-sensitive-sentinel"
        secret = "openai-api-key-sentinel"
        if server_pid == 0:
            os.close(report_read)
            exit_code = 0
            try:
                server.settimeout(5.0)
                connection, _address = server.accept()
                try:
                    connection.settimeout(5.0)
                    request_bytes = bytearray()
                    while b"\r\n\r\n" not in request_bytes:
                        chunk = connection.recv(4096)
                        if not chunk:
                            raise OSError
                        request_bytes.extend(chunk)
                    header_end = request_bytes.find(b"\r\n\r\n")
                    if header_end < 0:
                        raise OSError
                    header_text = bytes(request_bytes[:header_end]).decode("iso-8859-1")
                    content_length = next(
                        int(line.split(":", 1)[1].strip())
                        for line in header_text.split("\r\n")
                        if line.lower().startswith("content-length:")
                    )
                    body_start = header_end + 4
                    while len(request_bytes) - body_start < content_length:
                        chunk = connection.recv(4096)
                        if not chunk:
                            raise OSError
                        request_bytes.extend(chunk)
                    os.write(report_write, bytes(request_bytes))
                    response_body = json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": f"Transcript: {source_text}",
                                    },
                                    "finish_reason": "stop",
                                }
                            ]
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                        + response_body
                    )
                finally:
                    connection.close()
            except Exception:
                exit_code = 1
            finally:
                server.close()
                os.close(report_write)
            os._exit(exit_code)
        server.close()
        os.close(report_write)
        observed_proc_data: list[tuple[bytes, bytes]] = []
        observed_stderr = bytearray()
        observed_pids: list[int] = []
        cleanup_calls: list[tuple[bool, int | None]] = []
        try:
            with _isolated_worker_python() as worker_python:
                request = _openai_postprocess_request(
                    api_key=f"  {secret}  ",
                    flex_processing=False,
                    deadline_monotonic_ns=time.monotonic_ns() + POSTPROCESS_DEADLINE_NS,
                    payload={
                        "api_key": f"  {secret}  ",
                        "flex_processing": False,
                        "language": "en",
                        "model": "gpt-5.6-luna",
                        "personal_context": context,
                        "prompt": instruction,
                        "service_tier_fallback": False,
                        "text": source_text,
                        "url": f"http://127.0.0.1:{server_port}/v1",
                        "vocabulary": vocabulary,
                    },
                )
                real_pump = remote_http._pump_worker
                real_read = remote_http._read_worker_output
                real_cleanup = remote_http._cleanup_worker

                def inspect_worker(
                    handle: remote_http._WorkerHandle,
                    request_frame: bytes,
                    deadline_ns: int,
                    clock: object,
                    cancel: object,
                ) -> bytes:
                    observed_pids.append(handle.pid)
                    observed_proc_data.append(
                        (
                            Path(f"/proc/{handle.pid}/cmdline").read_bytes(),
                            Path(f"/proc/{handle.pid}/environ").read_bytes(),
                        )
                    )
                    return real_pump(
                        handle,
                        request_frame,
                        deadline_ns,
                        clock,  # type: ignore[arg-type]
                        cancel,  # type: ignore[arg-type]
                    )

                def capture_output(
                    handle: remote_http._WorkerHandle,
                    stream: object,
                    fd: int,
                    deadline_ns: int,
                    clock: object,
                ) -> bytes:
                    chunk = real_read(
                        handle,
                        stream,
                        fd,
                        deadline_ns,
                        clock,  # type: ignore[arg-type]
                    )
                    if stream is handle.process.stderr:
                        observed_stderr.extend(chunk)
                    return chunk

                def observe_cleanup(
                    handle: remote_http._WorkerHandle,
                    lifecycle_deadline_ns: int,
                    clock: object,
                ) -> bool:
                    confirmed = real_cleanup(
                        handle,
                        lifecycle_deadline_ns,
                        clock,  # type: ignore[arg-type]
                    )
                    cleanup_calls.append((confirmed, handle.process.poll()))
                    return confirmed

                with (
                    mock.patch.object(remote_http.sys, "executable", str(worker_python)),
                    mock.patch.object(remote_http, "_pump_worker", side_effect=inspect_worker),
                    mock.patch.object(
                        remote_http,
                        "_read_worker_output",
                        side_effect=capture_output,
                    ),
                    mock.patch.object(
                        remote_http,
                        "_cleanup_worker",
                        side_effect=observe_cleanup,
                    ),
                ):
                    result = remote_http.run_postprocess_openai_compatible(request)
            request_bytes = os.read(report_read, 2_000_000)
            if result != _postprocess_success(source_text):
                self.fail("unexpected OpenAI-compatible post-process result")
            self.assertTrue(observed_pids)
            self.assertEqual(cleanup_calls, [(True, 0)])
            self.assertFalse(Path(f"/proc/{observed_pids[0]}").exists())
            if secret.encode("ascii") in repr(result).encode("utf-8"):
                self.fail("API key leaked into worker result")
            if secret.encode("ascii") in bytes(observed_stderr):
                self.fail("API key leaked into worker stderr")
            self.assertTrue(observed_proc_data)
            for cmdline, environ in observed_proc_data:
                if secret.encode("ascii") in cmdline:
                    self.fail("API key leaked into worker argv")
                if secret.encode("ascii") in environ:
                    self.fail("API key leaked into worker environment")

            header_end = request_bytes.find(b"\r\n\r\n")
            self.assertGreaterEqual(header_end, 0)
            header_text = request_bytes[:header_end].decode("iso-8859-1")
            self.assertIn("POST /v1/chat/completions HTTP/1.1", header_text)
            if "Authorization: Bearer " + secret not in header_text:
                self.fail("API key missing from private provider authorization")
            self.assertIn("Accept: application/json", header_text)
            self.assertIn("Content-Type: application/json", header_text)
            self.assertIn("Connection: close", header_text)
            content_length = next(
                int(line.split(":", 1)[1].strip())
                for line in header_text.split("\r\n")
                if line.lower().startswith("content-length:")
            )
            body_bytes = request_bytes[header_end + 4 :]
            self.assertEqual(len(body_bytes), content_length)
            self.assertLessEqual(len(body_bytes), remote_http.MAX_REQUEST_FRAME_BYTES)
            if secret.encode("ascii") in body_bytes:
                self.fail("API key leaked into request body")
            body = json.loads(body_bytes)
            self.assertEqual(
                set(body),
                {"model", "messages", "stream"},
            )
            self.assertEqual(body["model"], "gpt-5.6-luna")
            self.assertIs(body["stream"], False)
            self.assertEqual(len(body["messages"]), 2)
            message_text = "\n".join(message["content"] for message in body["messages"])
            for sentinel in (source_text, context, vocabulary, instruction):
                if sentinel not in message_text:
                    self.fail("expected private prompt component is missing")
        finally:
            os.close(report_read)
            _pid, status = os.waitpid(server_pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    def test_installed_package_starts_under_exact_isolated_venv_worker_command(self) -> None:
        with _isolated_worker_python() as venv_python:
            completed = subprocess.run(
                [str(venv_python), "-I", "-B", "-m", "speed_of_cinnamon.remote_http_worker"],
                input=b"\x00\x00\x00\x00",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=venv_python.parent.parent.parent,
                env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "LC_CTYPE": "C.UTF-8"},
                timeout=10,
                check=False,
            )
        self.assertEqual(completed.returncode, 65)
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"")


class SocP001RemoteDiagnosticsTest(unittest.TestCase):
    def test_http_statuses_map_to_allowlisted_reasons(self) -> None:
        from speed_of_cinnamon import remote_http_worker as worker

        cases = {
            400: "http_400_invalid_request",
            401: "http_401_authentication",
            403: "http_403_permission",
            404: "http_404_model_or_endpoint",
            409: "http_409_conflict",
            422: "http_422_unprocessable_request",
            429: "http_429_rate_limit",
            418: "http_provider_error",
            500: "http_5xx_provider",
            599: "http_5xx_provider",
        }
        for status, reason in cases.items():
            with self.subTest(status=status):
                failure = worker._http_failure(status)
                self.assertEqual(failure.code, "remote-http-failed")
                self.assertEqual(failure.reason, reason)
                self.assertEqual(failure.status, status)
                self.assertEqual(str(failure), "remote-http-failed")

    def test_unsupported_parameter_requires_exact_structured_fields(self) -> None:
        valid = (
            {
                "message": "fixed provider message",
                "type": "invalid_request_error",
                "param": "temperature",
                "code": "unsupported_parameter",
            },
            {
                "message": "fixed provider message",
                "type": "unsupported_parameter",
                "param": "service_tier",
                "code": None,
            },
        )
        for case, error in enumerate(valid):
            with self.subTest(case=case):
                unsupported, flex_rejected = remote_http.classify_openai_error_fields(error)
                self.assertTrue(unsupported)
                self.assertEqual(flex_rejected, error["param"] == "service_tier")

        invalid_markers = (
            {
                "message": "parameter is not unsupported_parameter",
                "type": "invalid_request_error",
                "param": "temperature",
                "code": None,
            },
            {
                "message": "fixed",
                "type": "invalid_request_error",
                "param": "temperature-prefix",
                "code": "unsupported_parameter",
            },
            {
                "message": "fixed",
                "type": "invalid_request_error",
                "param": "temperature",
                "code": "unsupported_parameter_suffix",
            },
            {
                "message": "unsupported_parameter" * 100,
                "type": "invalid_request_error",
                "param": "temperature",
                "code": None,
            },
        )
        for case, error in enumerate(invalid_markers):
            with self.subTest(case=case):
                try:
                    unsupported, flex_rejected = remote_http.classify_openai_error_fields(error)
                except ValueError:
                    unsupported = flex_rejected = False
                self.assertFalse(unsupported or flex_rejected)

        for case, error in enumerate(
            (
                {"message": "fixed", "type": 1, "param": "temperature", "code": "unsupported_parameter"},
                {"message": "fixed", "type": "invalid_request_error", "param": False, "code": "unsupported_parameter"},
                {"message": "fixed", "type": "invalid_request_error", "param": "temperature", "code": 1},
            )
        ):
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    remote_http.classify_openai_error_fields(error)

    def test_failure_metadata_relation_rejects_mismatches(self) -> None:
        valid = (
            ("remote-http-failed", "http_400_invalid_request", 400),
            ("remote-http-failed", "http_5xx_provider", 599),
            ("remote-http-failed", "http_provider_error", 418),
            ("remote-dns-failed", "network_dns", None),
            ("remote-connect-failed", "timeout", None),
            ("remote-response-invalid", "provider_malformed_payload", None),
            ("remote-response-too-large", "provider_malformed_payload", 200),
        )
        for error_code, reason, status in valid:
            with self.subTest(reason=reason, status=status):
                self.assertEqual(
                    remote_http.validate_failure_metadata(
                        reason,
                        status,
                        error_code=error_code,
                    ),
                    (reason, status),
                )

        invalid = (
            ("remote-http-failed", "http_401_authentication", 403),
            ("remote-connect-failed", "network_tls", 500),
            ("remote-response-invalid", "provider_malformed_payload", 400),
            ("remote-http-failed", "http_provider_error", 401),
            ("remote-http-failed", "http_provider_error", 200),
            ("remote-http-failed", None, 418),
            ("remote-http-failed", "http_5xx_provider", 2**80),
            ("remote-http-failed", "http_400_invalid_request", True),
        )
        for case, (error_code, reason, status) in enumerate(invalid):
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    remote_http.validate_failure_metadata(
                        reason,
                        status,
                        error_code=error_code,
                    )

    def test_worker_unsupported_marker_is_structured_exact_and_bounded(self) -> None:
        from speed_of_cinnamon import remote_http_worker as worker

        worker._ensure_runtime()
        positive = {
            "error": {
                "message": "fixed",
                "type": "invalid_request_error",
                "param": "service_tier",
                "code": "unsupported_parameter",
            }
        }
        self.assertEqual(
            worker._decode_openai_http_error(json.dumps(positive), ""),
            (True, True),
        )
        false_cases = (
            {
                "error": {
                    "message": "service_tier is not unsupported",
                    "type": "invalid_request_error",
                    "param": "service_tier",
                    "code": None,
                }
            },
            {
                "error": {
                    "message": "fixed",
                    "type": "invalid_request_error",
                    "param": "service_tier-prefix",
                    "code": "unsupported_parameter",
                }
            },
            {
                "error": {
                    "message": "fixed",
                    "type": "invalid_request_error",
                    "param": "service_tier",
                    "code": "unsupported_parameter-suffix",
                }
            },
        )
        for case, payload in enumerate(false_cases):
            with self.subTest(case=case):
                self.assertEqual(
                    worker._decode_openai_http_error(json.dumps(payload), ""),
                    (False, False),
                )

        malformed_cases = (
            {
                "error": {
                    "message": "fixed",
                    "type": "invalid_request_error",
                    "param": "service_tier",
                    "code": None,
                    "metadata": "unsupported_parameter",
                }
            },
            {
                "error": {
                    "message": "x" * (remote_http._OPENAI_ERROR_MESSAGE_MAX_CHARS + 1)
                    + "unsupported_parameter",
                    "type": "invalid_request_error",
                    "param": "service_tier",
                    "code": None,
                }
            },
        )
        for case, payload in enumerate(malformed_cases):
            with self.subTest(case=case):
                with self.assertRaises(worker._WorkerFailure) as caught:
                    worker._decode_openai_http_error(json.dumps(payload), "")
                self.assertEqual(caught.exception.reason, "provider_malformed_payload")

    def test_response_read_failures_separate_timeout_size_and_malformed(self) -> None:
        from speed_of_cinnamon import remote_http_worker as worker
        from speed_of_cinnamon.postprocessor import PostProcessError

        timeout_cause = TimeoutError("private timeout detail")
        timeout_error = PostProcessError("fixed read failure")
        timeout_error.__cause__ = timeout_cause
        timeout_failure = worker._response_read_failure(
            timeout_error,
            time.monotonic_ns() + 5_000_000_000,
            status=200,
        )
        self.assertEqual(timeout_failure.reason, "timeout")
        self.assertIsNone(timeout_failure.status)

        too_large = worker._response_read_failure(
            PostProcessError("remote response is too large (max 1024 bytes)"),
            time.monotonic_ns() + 5_000_000_000,
            status=200,
        )
        self.assertEqual(too_large.code, "remote-response-too-large")
        self.assertEqual(too_large.reason, "provider_malformed_payload")
        self.assertEqual(too_large.status, 200)

        http_too_large = worker._response_read_failure(
            PostProcessError("remote response is too large (max 1024 bytes)"),
            time.monotonic_ns() + 5_000_000_000,
            status=503,
        )
        self.assertEqual(http_too_large.code, "remote-response-too-large")
        self.assertEqual(http_too_large.reason, "provider_malformed_payload")
        self.assertIsNone(http_too_large.status)

        http_malformed = worker._response_read_failure(
            PostProcessError("remote response contains invalid UTF-8"),
            time.monotonic_ns() + 5_000_000_000,
            status=418,
        )
        self.assertEqual(http_malformed.code, "remote-response-invalid")
        self.assertEqual(http_malformed.reason, "provider_malformed_payload")
        self.assertIsNone(http_malformed.status)

        malformed = worker._response_read_failure(
            PostProcessError("fixed read failure"),
            time.monotonic_ns() + 5_000_000_000,
            status=200,
        )
        self.assertEqual(malformed.code, "remote-response-invalid")
        self.assertEqual(malformed.reason, "provider_malformed_payload")

    def test_network_failures_use_only_type_errno_and_bounded_causes(self) -> None:
        import ssl

        from speed_of_cinnamon import remote_http_worker as worker

        cases = (
            (socket.gaierror(-2, "secret dns text"), "network_dns"),
            (ConnectionRefusedError(111, "secret endpoint"), "network_connect"),
            (ssl.SSLError("secret certificate"), "network_tls"),
            (TimeoutError("secret timeout"), "timeout"),
        )
        for error, reason in cases:
            with self.subTest(reason=reason):
                failure = worker._network_failure(error)
                self.assertEqual(failure.reason, reason)
                self.assertNotIn("secret", str(failure))

    def test_error_frame_round_trip_contains_only_allowlisted_metadata(self) -> None:
        from speed_of_cinnamon import remote_http_worker as worker

        for operation in remote_http.SUPPORTED_OPERATIONS:
            with self.subTest(operation=operation):
                frame = worker._worker_response(
                    NONCE,
                    None,
                    "remote-http-failed",
                    operation,
                    "http_401_authentication",
                    401,
                )
                decoded = decode_response(
                    frame,
                    expected_nonce=NONCE,
                    operation=operation,
                    secret="dummy-api-key",
                )
                self.assertEqual(decoded["failure_reason"], "http_401_authentication")
                self.assertEqual(decoded["provider_status"], 401)
                self.assertFalse(
                    b"dummy-api-key" in frame,
                    "worker error frame leaked API credential",
                )
                for forbidden in (
                    b"https://user:pass@example.invalid/?key=x",
                    b"private-model-id",
                    b"dummy-prompt-value",
                    b"dummy-transcript-value",
                ):
                    self.assertFalse(
                        forbidden in frame.lower(),
                        "worker error frame leaked request data",
                    )

    def test_malformed_diagnostics_fail_closed_as_worker_protocol(self) -> None:
        response = {
            "error_code": "remote-http-failed",
            "nonce": NONCE,
            "schema_version": 1,
            "status": "error",
        }
        malformed = (
            {**response, "failure_reason": "provider supplied secret"},
            {**response, "failure_reason": None},
            {**response, "provider_status": True},
            {**response, "provider_status": 99},
            {**response, "provider_status": 600},
            {**response, "failure_detail": "secret body"},
        )
        for case, value in enumerate(malformed):
            with self.subTest(case=case):
                with self.assertRaises(RemoteProtocolError) as raised:
                    encode_response(
                        value,
                        operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
                    )
                self.assertEqual(raised.exception.code, "remote-worker-protocol-invalid")
                self.assertFalse(
                    "secret" in str(raised.exception),
                    "protocol exception leaked provider data",
                )

    def test_malformed_provider_payload_never_enters_failure_text(self) -> None:
        from speed_of_cinnamon import remote_http_worker as worker

        secret = "sk-dummy-secret transcript-value https://user:pass@example.invalid/?key=x"
        for raw in ("not-json " + secret, '{"error":{"message":"' + secret + '","extra":1}}'):
            with self.subTest(raw=raw[:8]):
                with self.assertRaises(worker._WorkerFailure) as raised:
                    worker._decode_openai_http_error(raw, "sk-dummy-secret")
                self.assertEqual(raised.exception.reason, "provider_malformed_payload")
                self.assertFalse(
                    "dummy" in str(raised.exception)
                    or "transcript" in str(raised.exception),
                    "worker exception leaked provider data",
                )

    def test_flex_rejection_then_fallback_success_has_no_failure(self) -> None:
        from speed_of_cinnamon import remote_http_worker as worker

        first_response = mock.Mock(status=400)
        second_response = mock.Mock(status=200)
        first_connection = mock.Mock()
        second_connection = mock.Mock()
        with (
            mock.patch.object(
                worker,
                "_open_connection",
                side_effect=(
                    (first_connection, first_response),
                    (second_connection, second_response),
                ),
            ) as opened,
            mock.patch.object(
                worker,
                "_read_response_text",
                side_effect=(
                    '{"error":{"message":"service_tier unsupported",'
                    '"type":"invalid_request_error","param":"service_tier",'
                    '"code":"unsupported_parameter"}}',
                    '{"choices":[{"message":{"content":"clean text"},'
                    '"finish_reason":"stop"}]}',
                ),
            ),
        ):
            result = worker._read_openai_postprocess(
                "https://api.openai.com/v1",
                "gpt-test",
                "source text",
                "en",
                "",
                "",
                "",
                "",
                True,
                True,
                time.monotonic_ns() + 5_000_000_000,
            )

        self.assertEqual(result, {"text": "clean text"})
        self.assertEqual(opened.call_count, 2)

    def test_supervisor_failures_have_stable_diagnostics(self) -> None:
        cases = {
            "remote-operation-timeout": "timeout",
            "remote-worker-unavailable": "worker_startup",
            "remote-worker-protocol-invalid": "worker_protocol",
            "remote-worker-cleanup-unconfirmed": "worker_protocol",
        }
        for code, reason in cases.items():
            with self.subTest(code=code):
                self.assertEqual(
                    remote_http._supervisor_error(NONCE, code)["failure_reason"],
                    reason,
                )


class RemoteHttpBootstrapTest(unittest.TestCase):
    def test_original_modules_own_patchable_runtime_definitions(self) -> None:
        from speed_of_cinnamon import http_safety
        from speed_of_cinnamon import postprocessor

        self.assertIs(remote_http.encode_request.__globals__, remote_http.__dict__)
        self.assertIs(
            postprocessor._read_response_text.__globals__,
            postprocessor.__dict__,
        )
        self.assertIs(
            postprocessor.post_process_text.__globals__,
            postprocessor.__dict__,
        )
        self.assertIs(
            http_safety._connect_to_pinned_addresses.__globals__,
            http_safety.__dict__,
        )
        self.assertIs(
            http_safety._PinnedHTTPConnection.connect.__globals__,
            http_safety.__dict__,
        )

    def test_worker_import_surface_and_security_order_are_fail_closed(self) -> None:
        worker_path = Path(remote_http.__file__).with_name("remote_http_worker.py")
        source = worker_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertEqual(imports, ["__future__", "ctypes", "os", "resource", "signal"])

        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        main_calls = {
            node.func.id: node.lineno
            for node in ast.walk(functions["main"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        order = (
            "_prctl",
            "_set_resource_limits",
            "_bind_parent",
            "_install_no_fork_boundary",
            "_load_runtime",
            "_run",
        )
        self.assertEqual(
            [main_calls[name] for name in order],
            sorted(main_calls[name] for name in order),
        )
        parent_death_names = {
            node.id
            for node in ast.walk(functions["_parent_death"])
            if isinstance(node, ast.Name)
        }
        self.assertIn("_WORKER_EXIT_CODE", parent_death_names)
        self.assertNotIn("remote_http", parent_death_names)
        loader_imports = [
            node
            for node in ast.walk(functions["_load_runtime"])
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertTrue(loader_imports)

    def test_worker_sets_exact_384_mib_address_space_limit(self) -> None:
        from speed_of_cinnamon import remote_http_worker

        observed = {}

        def getrlimit(limit: int) -> tuple[int, int]:
            return observed.get(
                limit,
                (remote_http_worker.resource.RLIM_INFINITY, remote_http_worker.resource.RLIM_INFINITY),
            )

        def setrlimit(limit: int, value: tuple[int, int]) -> None:
            observed[limit] = value

        with (
            mock.patch.object(remote_http_worker.resource, "getrlimit", side_effect=getrlimit),
            mock.patch.object(remote_http_worker.resource, "setrlimit", side_effect=setrlimit),
        ):
            remote_http_worker._set_resource_limits()
        expected = 384 * 1024 * 1024
        self.assertEqual(remote_http_worker._ADDRESS_SPACE_LIMIT, expected)
        self.assertEqual(
            observed[remote_http_worker.resource.RLIMIT_AS],
            (expected, expected),
        )

    def test_runtime_loader_is_idempotent_under_384_mib(self) -> None:
        source_root = str(Path(remote_http.__file__).resolve().parents[1])
        code = """
import resource
import sys
sys.path.insert(0, sys.argv[1])
limit = 384 * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
before = set(sys.modules)
from speed_of_cinnamon import remote_http_worker as worker
forbidden = {
    "speed_of_cinnamon.remote_http",
    "speed_of_cinnamon.postprocessor",
    "speed_of_cinnamon.http_safety",
    "http.client",
    "json",
    "ssl",
    "urllib.parse",
}
if forbidden & (set(sys.modules) - before):
    raise SystemExit(10)
worker._load_runtime()
protocol_identity = id(worker.remote_http)
worker._load_runtime()
if id(worker.remote_http) != protocol_identity:
    raise SystemExit(11)
heavy_modules = {
    "speed_of_cinnamon.command_chain",
    "speed_of_cinnamon.process_priority",
    "multiprocessing",
}
if heavy_modules & set(sys.modules):
    raise SystemExit(13)
soft, hard = resource.getrlimit(resource.RLIMIT_AS)
if (soft, hard) != (limit, limit):
    raise SystemExit(12)
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, source_root],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)

    def test_runtime_constant_manipulation_fails_closed(self) -> None:
        source_root = str(Path(remote_http.__file__).resolve().parents[1])
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from speed_of_cinnamon import remote_http_worker as worker
from speed_of_cinnamon import remote_http
remote_http.WORKER_EXIT_CODE = 66
try:
    worker._load_runtime()
except worker._WorkerFailure as failure:
    if failure.code == "remote-operation-failed":
        raise SystemExit(0)
raise SystemExit(1)
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, source_root],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)

    def test_worker_runtime_attribute_compatibility_is_lazy_and_allowlisted(self) -> None:
        source_root = str(Path(remote_http.__file__).resolve().parents[1])
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from speed_of_cinnamon import remote_http_worker as worker
if worker._RUNTIME_LOADED:
    raise SystemExit(10)
try:
    worker._missing_runtime_compatibility_name
except AttributeError:
    pass
else:
    raise SystemExit(11)
if worker._RUNTIME_LOADED:
    raise SystemExit(12)
read_response_text = worker._read_response_text
pinned_connection = worker._PinnedHTTPConnection
from speed_of_cinnamon import http_safety, postprocessor, remote_http
if read_response_text is not postprocessor._read_response_text:
    raise SystemExit(13)
if pinned_connection is not http_safety._PinnedHTTPConnection:
    raise SystemExit(14)
if worker.remote_http is not remote_http:
    raise SystemExit(15)
if not worker._RUNTIME_LOADED:
    raise SystemExit(16)
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, source_root],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)

    def test_worker_direct_helpers_initialize_runtime_globals(self) -> None:
        source_root = str(Path(remote_http.__file__).resolve().parents[1])
        cases = {
            "close": "worker._close_http_resources(None, None)",
            "decode": """
if worker._decode_listing('{"models":null}') != {
    "listing_state": "missing-model-list",
    "models": [],
}:
    raise SystemExit(20)
""",
            "control": """
def stop(*_args):
    raise worker._WorkerDeadline
worker._read_request_frame = stop
try:
    worker._run_with_control(-1, object(), (0, 0, 0))
except worker._WorkerDeadline:
    pass
else:
    raise SystemExit(21)
""",
        }
        for name, case in cases.items():
            with self.subTest(case=name):
                code = f"""
import sys
sys.path.insert(0, sys.argv[1])
from speed_of_cinnamon import remote_http_worker as worker
if worker._RUNTIME_LOADED:
    raise SystemExit(10)
{case}
if not worker._RUNTIME_LOADED:
    raise SystemExit(11)
"""
                completed = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", code, source_root],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
