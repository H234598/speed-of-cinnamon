"""Protocol-v1 framing and schema validation for remote HTTP operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import selectors
import signal
import socket
import stat
import struct
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from .postprocessor import (
    MAX_OLLAMA_MODEL_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    MAX_POSTPROCESS_PROMPT_CHARS,
    MAX_POSTPROCESS_TEXT_CHARS,
    MAX_POSTPROCESS_URL_CHARS,
    _assert_text_length,
    _assert_openai_compatible_text,
    _contains_escaped_null,
    _contains_http_header_control_chars,
    _openai_compatible_model_supports_text_polishing,
    _safe_prompt_language,
    _validate_http_url,
    normalize_context,
    normalize_vocabulary,
)

FRAME_PREFIX_BYTES = 4

MAX_REQUEST_FRAME_BYTES = 16 * 1024 * 1024

MAX_RESPONSE_FRAME_BYTES = 16 * 1024 * 1024

MAX_ERROR_FRAME_BYTES = 4 * 1024

LISTING_DEADLINE_NS = 5_000_000_000

POSTPROCESS_DEADLINE_NS = 180_000_000_000

MAX_URL_CHARS = 2_048

MAX_URL_BYTES = 2_048

MAX_MODEL_CHARS = 240

MAX_MODEL_BYTES = 240

MAX_POSTPROCESS_MODEL_CHARS = MAX_OLLAMA_MODEL_CHARS

MAX_MODEL_LIST_ENTRIES = 1_000

MAX_OPENAI_COMPATIBLE_API_KEY_CHARS = 4_096

MAX_OPENAI_COMPATIBLE_API_KEY_BYTES = 4_096

PROTOCOL_SCHEMA_VERSION = 1

LIST_OLLAMA_MODELS_OPERATION = "list-ollama-models"

LIST_OPENAI_COMPATIBLE_MODELS_OPERATION = "list-openai-compatible-models"

POSTPROCESS_OLLAMA_OPERATION = "postprocess-ollama"

POSTPROCESS_OPENAI_COMPATIBLE_OPERATION = "postprocess-openai-compatible"

CLEANUP_GRACE_NS = 5_000_000_000

WORKER_EXIT_CODE = 65

PROTOCOL_OPERATIONS = frozenset(
    {
        "list-ollama-models",
        "list-openai-compatible-models",
        "postprocess-ollama",
        "postprocess-openai-compatible",
    }
)

SUPPORTED_OPERATIONS = frozenset(
    {
        LIST_OLLAMA_MODELS_OPERATION,
        LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
        POSTPROCESS_OLLAMA_OPERATION,
        POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
    }
)

WORKER_ERROR_CODES = frozenset(
    {
        "remote-request-invalid",
        "remote-url-unsafe",
        "remote-dns-failed",
        "remote-connect-failed",
        "remote-http-failed",
        "remote-response-too-large",
        "remote-response-invalid",
        "remote-operation-failed",
    }
)

FAILURE_REASONS = frozenset(
    {
        "http_400_invalid_request",
        "http_400_unsupported_parameter",
        "http_401_authentication",
        "http_403_permission",
        "http_404_model_or_endpoint",
        "http_409_conflict",
        "http_422_unprocessable_request",
        "http_429_rate_limit",
        "http_5xx_provider",
        "http_provider_error",
        "network_dns",
        "network_connect",
        "network_tls",
        "timeout",
        "worker_startup",
        "worker_protocol",
        "provider_malformed_payload",
    }
)

_HTTP_REASON_STATUS = {
    "http_400_invalid_request": 400,
    "http_400_unsupported_parameter": 400,
    "http_401_authentication": 401,
    "http_403_permission": 403,
    "http_404_model_or_endpoint": 404,
    "http_409_conflict": 409,
    "http_422_unprocessable_request": 422,
    "http_429_rate_limit": 429,
}
_SPECIAL_HTTP_STATUSES = frozenset(_HTTP_REASON_STATUS.values())
_OPENAI_UNSUPPORTED_CODES = frozenset({"unsupported_parameter"})
_OPENAI_UNSUPPORTED_TYPES = frozenset(
    {"invalid_request_error", "unsupported_parameter"}
)
_OPENAI_UNSUPPORTED_PARAMETERS = frozenset(
    {
        "max_completion_tokens",
        "max_tokens",
        "reasoning_effort",
        "response_format",
        "service_tier",
        "temperature",
    }
)
_OPENAI_ERROR_MESSAGE_MAX_CHARS = 512
_OPENAI_ERROR_MARKER_MAX_CHARS = 128

SUPERVISOR_ERROR_CODES = frozenset(
    {
        "remote-operation-timeout",
        "remote-worker-unavailable",
        "remote-worker-protocol-invalid",
        "remote-worker-cancelled",
        "remote-worker-cleanup-unconfirmed",
    }
)

_ALL_ERROR_CODES = WORKER_ERROR_CODES | SUPERVISOR_ERROR_CODES

_FORBIDDEN_KEYS = frozenset({"__proto__", "constructor", "prototype"})

_REQUEST_KEYS = frozenset(
    {"deadline_monotonic_ns", "nonce", "operation", "payload", "schema_version"}
)

_REQUEST_PAYLOAD_KEYS = frozenset({"url"})

_OPENAI_REQUEST_PAYLOAD_KEYS = frozenset({"url", "api_key"})

_POSTPROCESS_REQUEST_PAYLOAD_KEYS = frozenset(
    {"language", "model", "personal_context", "prompt", "text", "url", "vocabulary"}
)

_OPENAI_POSTPROCESS_REQUEST_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "flex_processing",
        "language",
        "model",
        "personal_context",
        "prompt",
        "service_tier_fallback",
        "text",
        "url",
        "vocabulary",
    }
)

_SUCCESS_RESPONSE_KEYS = frozenset({"nonce", "operation", "result", "schema_version", "status"})

_ERROR_RESPONSE_KEYS = frozenset({"error_code", "nonce", "operation", "schema_version", "status"})
_ERROR_RESPONSE_OPTIONAL_KEYS = frozenset({"failure_reason", "provider_status"})

_LISTING_RESULT_KEYS = frozenset({"listing_state", "models"})

_POSTPROCESS_RESULT_KEYS = frozenset({"text"})

_OPENAI_MODEL_KEYS = frozenset({"name", "model"})

_MODEL_KEYS = frozenset(
    {
        "name",
        "model",
        "modified_at",
        "size",
        "size_label",
        "digest",
        "family",
        "parameter_size",
        "quantization",
        "description",
    }
)

_MODEL_TEXT_KEYS = (
    "modified_at",
    "size_label",
    "digest",
    "family",
    "parameter_size",
    "quantization",
)

_ESCAPED_CONTROL_RE = re.compile(
    r"(?i)\\(?:[abfnrtv]|x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|"
    r"u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
)

_Result = TypeVar("_Result")

class RemoteProtocolError(ValueError):
    """Fixed, data-independent protocol failure."""

    __slots__ = ("code", "reason", "status")

    def __init__(
        self,
        code: str,
        *,
        reason: str | None = None,
        status: int | None = None,
    ) -> None:
        if type(code) is not str or code not in _ALL_ERROR_CODES:
            raise ValueError("invalid remote protocol error code")
        reason, status = validate_failure_metadata(
            reason,
            status,
            error_code=code,
        )
        self.code = code
        self.reason = reason
        self.status = status
        super().__init__(code)


def validate_failure_metadata(
    reason: object,
    status: object,
    *,
    error_code: object | None = None,
) -> tuple[str | None, int | None]:
    if reason is not None and (type(reason) is not str or reason not in FAILURE_REASONS):
        raise ValueError("invalid remote failure reason")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError("invalid remote provider status")
    if error_code is not None and (
        type(error_code) is not str or error_code not in _ALL_ERROR_CODES
    ):
        raise ValueError("invalid remote failure error code")
    if reason is None:
        if status is not None:
            raise ValueError("remote provider status requires a failure reason")
        return None, None
    if error_code is None:
        error_code = default_error_code_for_failure_reason(reason)
    assert type(error_code) is str
    if reason in _HTTP_REASON_STATUS:
        if error_code != "remote-http-failed" or status != _HTTP_REASON_STATUS[reason]:
            raise ValueError("inconsistent remote HTTP failure metadata")
    elif reason == "http_5xx_provider":
        if error_code != "remote-http-failed" or status is None or not 500 <= status <= 599:
            raise ValueError("inconsistent remote provider failure metadata")
    elif reason == "http_provider_error":
        if (
            error_code != "remote-http-failed"
            or status is None
            or 200 <= status <= 299
            or status in _SPECIAL_HTTP_STATUSES
            or 500 <= status <= 599
        ):
            raise ValueError("inconsistent generic remote HTTP failure metadata")
    elif reason == "network_dns":
        if error_code != "remote-dns-failed" or status is not None:
            raise ValueError("inconsistent remote DNS failure metadata")
    elif reason in {"network_connect", "network_tls"}:
        if error_code != "remote-connect-failed" or status is not None:
            raise ValueError("inconsistent remote connection failure metadata")
    elif reason == "timeout":
        if error_code not in {"remote-connect-failed", "remote-operation-timeout"} or status is not None:
            raise ValueError("inconsistent remote timeout metadata")
    elif reason == "worker_startup":
        if error_code != "remote-worker-unavailable" or status is not None:
            raise ValueError("inconsistent remote worker startup metadata")
    elif reason == "worker_protocol":
        if error_code not in {
            "remote-operation-failed",
            "remote-worker-cleanup-unconfirmed",
            "remote-worker-protocol-invalid",
        } or status is not None:
            raise ValueError("inconsistent remote worker protocol metadata")
    elif reason == "provider_malformed_payload":
        if (
            error_code not in {"remote-response-invalid", "remote-response-too-large"}
            or (status is not None and not 200 <= status <= 299)
        ):
            raise ValueError("inconsistent malformed provider payload metadata")
    return reason, status


def default_error_code_for_failure_reason(reason: str) -> str:
    if reason in _HTTP_REASON_STATUS or reason in {"http_5xx_provider", "http_provider_error"}:
        return "remote-http-failed"
    if reason == "network_dns":
        return "remote-dns-failed"
    if reason in {"network_connect", "network_tls"}:
        return "remote-connect-failed"
    if reason == "timeout":
        return "remote-operation-timeout"
    if reason == "worker_startup":
        return "remote-worker-unavailable"
    if reason == "worker_protocol":
        return "remote-worker-protocol-invalid"
    if reason == "provider_malformed_payload":
        return "remote-response-invalid"
    raise ValueError("invalid remote failure reason")


def failure_reason_for_http_status(
    status: object,
    *,
    unsupported_parameter: bool = False,
) -> str:
    if type(status) is not int or not 100 <= status <= 599 or 200 <= status <= 299:
        raise ValueError("invalid failure HTTP status")
    if status == 400:
        return (
            "http_400_unsupported_parameter"
            if unsupported_parameter
            else "http_400_invalid_request"
        )
    for reason, expected_status in _HTTP_REASON_STATUS.items():
        if status == expected_status:
            return reason
    if 500 <= status <= 599:
        return "http_5xx_provider"
    return "http_provider_error"


def _validate_openai_error_field(
    value: object,
    *,
    required: bool,
    max_chars: int,
) -> str | None:
    if value is None and not required:
        return None
    if type(value) is not str or (required and not value.strip()):
        raise ValueError("invalid OpenAI-compatible error field")
    if len(value) > max_chars or len(value.encode("utf-8")) > max_chars * 4:
        raise ValueError("oversized OpenAI-compatible error field")
    return value.strip()


def classify_openai_error_fields(error: object) -> tuple[bool, bool]:
    if type(error) is not dict:
        raise ValueError("invalid OpenAI-compatible error object")
    allowed_fields = frozenset({"message", "type", "param", "code"})
    if "message" not in error or not frozenset(error).issubset(allowed_fields):
        raise ValueError("invalid OpenAI-compatible error object")
    _validate_openai_error_field(
        error.get("message"),
        required=True,
        max_chars=_OPENAI_ERROR_MESSAGE_MAX_CHARS,
    )
    error_type = _validate_openai_error_field(
        error.get("type"),
        required=False,
        max_chars=_OPENAI_ERROR_MARKER_MAX_CHARS,
    )
    parameter = _validate_openai_error_field(
        error.get("param"),
        required=False,
        max_chars=_OPENAI_ERROR_MARKER_MAX_CHARS,
    )
    code = _validate_openai_error_field(
        error.get("code"),
        required=False,
        max_chars=_OPENAI_ERROR_MARKER_MAX_CHARS,
    )
    unsupported = (
        code in _OPENAI_UNSUPPORTED_CODES
        and error_type in _OPENAI_UNSUPPORTED_TYPES | {None}
        and parameter in _OPENAI_UNSUPPORTED_PARAMETERS
    ) or (
        error_type == "unsupported_parameter"
        and code in _OPENAI_UNSUPPORTED_CODES | {None}
        and parameter in _OPENAI_UNSUPPORTED_PARAMETERS
    )
    return unsupported, unsupported and parameter == "service_tier"

def _run(code: str, operation: Callable[[], _Result]) -> _Result:
    try:
        result = operation()
    except Exception:
        failure = RemoteProtocolError(code)
    else:
        return result
    raise failure

def _require_exact_keys(value: object, expected: frozenset[str]) -> None:
    if type(value) is not dict:
        raise ValueError
    keys = value.keys()
    if any(type(key) is not str for key in keys):
        raise ValueError
    if any(key in _FORBIDDEN_KEYS for key in keys):
        raise ValueError
    if frozenset(keys) != expected:
        raise ValueError

def _reject_surrogates(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError

def _validate_text(
    value: object,
    *,
    max_chars: int,
    max_bytes: int,
    require_nonempty: bool = False,
    require_trimmed: bool = False,
) -> None:
    if type(value) is not str:
        raise ValueError
    _reject_surrogates(value)
    if require_nonempty and not value.strip():
        raise ValueError
    if require_trimmed and value != value.strip():
        raise ValueError
    if len(value) > max_chars:
        raise ValueError
    encoded = value.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError
    lowered = value.lower()
    if "\x00" in value or "\\x00" in lowered or "\\u0000" in lowered:
        raise ValueError
    if _ESCAPED_CONTROL_RE.search(lowered):
        raise ValueError
    if any(
        ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F
        for char in value
    ):
        raise ValueError

def _validate_nonce(value: object) -> None:
    if type(value) is not str or len(value) != 32:
        raise ValueError
    if any(char not in "0123456789abcdef" for char in value):
        raise ValueError

def _validated_now(monotonic_ns: Callable[[], object] | None) -> int:
    clock = time.monotonic_ns if monotonic_ns is None else monotonic_ns
    if not callable(clock):
        raise ValueError
    now = clock()
    if type(now) is not int:
        raise ValueError
    return now

def _validate_deadline(
    value: object,
    monotonic_ns: Callable[[], object] | None,
    *,
    max_duration_ns: int = LISTING_DEADLINE_NS,
) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError
    now = _validated_now(monotonic_ns)
    remaining = value - now
    if remaining <= 0 or remaining > max_duration_ns:
        raise ValueError

def _validate_url(value: object) -> None:
    _validate_text(
        value,
        max_chars=MAX_URL_CHARS,
        max_bytes=MAX_URL_BYTES,
        require_nonempty=True,
    )

def _validate_api_key(value: object) -> str:
    _validate_text(
        value,
        max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
        max_bytes=MAX_OPENAI_COMPATIBLE_API_KEY_BYTES,
    )
    assert type(value) is str
    normalized = value.strip()
    _validate_text(
        normalized,
        max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
        max_bytes=MAX_OPENAI_COMPATIBLE_API_KEY_BYTES,
    )
    return normalized

def _validate_request_envelope(
    value: object,
    monotonic_ns: Callable[[], object] | None,
) -> dict[str, object]:
    _require_exact_keys(value, _REQUEST_KEYS)
    assert type(value) is dict
    if type(value["schema_version"]) is not int or value["schema_version"] != PROTOCOL_SCHEMA_VERSION:
        raise ValueError
    _validate_nonce(value["nonce"])
    if type(value["operation"]) is not str or value["operation"] not in PROTOCOL_OPERATIONS:
        raise ValueError
    if value["operation"] not in SUPPORTED_OPERATIONS:
        raise ValueError
    max_duration_ns = (
        POSTPROCESS_DEADLINE_NS
        if value["operation"]
        in {POSTPROCESS_OLLAMA_OPERATION, POSTPROCESS_OPENAI_COMPATIBLE_OPERATION}
        else LISTING_DEADLINE_NS
    )
    _validate_deadline(
        value["deadline_monotonic_ns"],
        monotonic_ns,
        max_duration_ns=max_duration_ns,
    )
    return value

def _validate_postprocess_payload(payload: object) -> dict[str, object]:
    _require_exact_keys(payload, _POSTPROCESS_REQUEST_PAYLOAD_KEYS)
    assert type(payload) is dict
    url = payload["url"]
    if type(url) is not str or len(url) > MAX_POSTPROCESS_URL_CHARS:
        raise ValueError
    _validate_http_url(url, field_name="ollama url")
    _assert_text_length(
        payload["text"],
        field_name="input text",
        max_chars=MAX_POSTPROCESS_TEXT_CHARS,
    )
    _assert_text_length(
        payload["prompt"],
        field_name="prompt",
        max_chars=MAX_POSTPROCESS_PROMPT_CHARS,
    )
    model = payload["model"]
    if type(model) is not str:
        raise ValueError
    if _contains_escaped_null(model) or _contains_http_header_control_chars(model):
        raise ValueError
    _assert_text_length(
        model,
        field_name="ollama model",
        max_chars=MAX_POSTPROCESS_MODEL_CHARS,
    )
    if not model.strip():
        raise ValueError
    _safe_prompt_language(payload["language"])
    normalize_context(payload["personal_context"])
    normalize_vocabulary(payload["vocabulary"])
    return payload

def _validate_openai_postprocess_payload(payload: object) -> dict[str, object]:
    _require_exact_keys(payload, _OPENAI_POSTPROCESS_REQUEST_PAYLOAD_KEYS)
    assert type(payload) is dict
    url = payload["url"]
    if type(url) is not str or len(url) > MAX_POSTPROCESS_URL_CHARS:
        raise ValueError
    _validate_http_url(url, field_name="openai-compatible url")
    _assert_text_length(
        payload["text"],
        field_name="input text",
        max_chars=MAX_POSTPROCESS_TEXT_CHARS,
    )
    _assert_text_length(
        payload["prompt"],
        field_name="prompt",
        max_chars=MAX_POSTPROCESS_PROMPT_CHARS,
    )
    model = payload["model"]
    if type(model) is not str:
        raise ValueError
    model_name = _assert_openai_compatible_text(
        model,
        field_name="openai-compatible model",
        max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    ).strip()
    if not model_name or not _openai_compatible_model_supports_text_polishing(model_name):
        raise ValueError
    _safe_prompt_language(payload["language"])
    normalize_context(payload["personal_context"])
    normalize_vocabulary(payload["vocabulary"])
    if type(payload["flex_processing"]) is not bool:
        raise ValueError
    if type(payload["service_tier_fallback"]) is not bool:
        raise ValueError
    api_key = _validate_api_key(payload["api_key"])
    if api_key == payload["api_key"]:
        return payload
    normalized = dict(payload)
    normalized["api_key"] = api_key
    return normalized

def _validate_request_payload(value: dict[str, object]) -> dict[str, object]:
    payload = value["payload"]
    operation = value.get("operation")
    expected_keys = (
        _OPENAI_REQUEST_PAYLOAD_KEYS
        if operation == LIST_OPENAI_COMPATIBLE_MODELS_OPERATION
        else _REQUEST_PAYLOAD_KEYS
        if operation == LIST_OLLAMA_MODELS_OPERATION
        else _POSTPROCESS_REQUEST_PAYLOAD_KEYS
        if operation == POSTPROCESS_OLLAMA_OPERATION
        else _OPENAI_POSTPROCESS_REQUEST_PAYLOAD_KEYS
        if operation == POSTPROCESS_OPENAI_COMPATIBLE_OPERATION
        else None
    )
    if expected_keys is None:
        raise ValueError
    if operation == POSTPROCESS_OLLAMA_OPERATION:
        _validate_postprocess_payload(payload)
        return value
    if operation == POSTPROCESS_OPENAI_COMPATIBLE_OPERATION:
        normalized_payload = _validate_openai_postprocess_payload(payload)
        if normalized_payload is payload:
            return value
        normalized = dict(value)
        normalized["payload"] = normalized_payload
        return normalized
    _require_exact_keys(payload, expected_keys)
    assert type(payload) is dict
    _validate_url(payload["url"])
    if operation != LIST_OPENAI_COMPATIBLE_MODELS_OPERATION:
        return value
    api_key = _validate_api_key(payload["api_key"])
    if api_key == payload["api_key"]:
        return value
    normalized = dict(value)
    normalized_payload = dict(payload)
    normalized_payload["api_key"] = api_key
    normalized["payload"] = normalized_payload
    return normalized

def _validate_request(value: object, monotonic_ns: Callable[[], object] | None) -> dict[str, object]:
    return _validate_request_payload(_validate_request_envelope(value, monotonic_ns))

def _validate_model(value: object) -> str:
    _require_exact_keys(value, _MODEL_KEYS)
    assert type(value) is dict
    name = value["name"]
    model_name = value["model"]
    _validate_text(
        name,
        max_chars=MAX_MODEL_CHARS,
        max_bytes=MAX_MODEL_BYTES,
        require_nonempty=True,
        require_trimmed=True,
    )
    _validate_text(
        model_name,
        max_chars=MAX_MODEL_CHARS,
        max_bytes=MAX_MODEL_BYTES,
        require_nonempty=True,
        require_trimmed=True,
    )
    if model_name != name:
        raise ValueError
    if type(value["size"]) is not int or value["size"] < 0:
        raise ValueError
    for key in _MODEL_TEXT_KEYS:
        _validate_text(
            value[key],
            max_chars=MAX_MODEL_CHARS,
            max_bytes=MAX_MODEL_BYTES,
            require_trimmed=True,
        )
    description = value["description"]
    _validate_text(
        description,
        max_chars=MAX_MODEL_CHARS * 3 + 2,
        max_bytes=MAX_MODEL_BYTES * 3 + 2,
        require_trimmed=True,
    )
    expected_description = " ".join(
        part for part in (value["family"], value["parameter_size"], value["quantization"]) if part
    )
    if description != expected_description:
        raise ValueError
    if value["size"] == 0 and value["size_label"] != "":
        raise ValueError
    if value["size"] > 0 and value["size_label"] == "":
        raise ValueError
    return name

def _validate_openai_model(value: object) -> str:
    _require_exact_keys(value, _OPENAI_MODEL_KEYS)
    assert type(value) is dict
    name = value["name"]
    model_name = value["model"]
    for item in (name, model_name):
        _validate_text(
            item,
            max_chars=MAX_MODEL_CHARS,
            max_bytes=MAX_MODEL_BYTES,
            require_nonempty=True,
            require_trimmed=True,
        )
    if name != model_name:
        raise ValueError
    return name

def _validate_listing_result(
    value: object,
    operation: str = LIST_OLLAMA_MODELS_OPERATION,
) -> None:
    _require_exact_keys(value, _LISTING_RESULT_KEYS)
    assert type(value) is dict
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError
    state = value["listing_state"]
    if type(state) is not str or state not in {"listed", "missing-model-list"}:
        raise ValueError
    models = value["models"]
    if type(models) is not list or len(models) > MAX_MODEL_LIST_ENTRIES:
        raise ValueError
    if state == "missing-model-list" and models:
        raise ValueError
    names: set[str] = set()
    for model in models:
        name = (
            _validate_openai_model(model)
            if operation == LIST_OPENAI_COMPATIBLE_MODELS_OPERATION
            else _validate_model(model)
        )
        if name in names:
            raise ValueError
        names.add(name)

def _validate_postprocess_result(value: object) -> None:
    _require_exact_keys(value, _POSTPROCESS_RESULT_KEYS)
    assert type(value) is dict
    _assert_text_length(
        value["text"],
        field_name="post-process output",
        max_chars=MAX_POSTPROCESS_TEXT_CHARS,
    )
    if not value["text"]:
        raise ValueError

def _reject_secret_tree(value: object, secret: str, depth: int = 0) -> None:
    if not secret:
        return
    if depth > 64:
        raise ValueError
    if type(value) is str:
        if secret in value:
            raise ValueError
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError
            _reject_secret_tree(item, secret, depth + 1)
        return
    if type(value) is list:
        for item in value:
            _reject_secret_tree(item, secret, depth + 1)

def _validate_response(
    value: object,
    expected_nonce: str | None = None,
    *,
    operation: str = LIST_OLLAMA_MODELS_OPERATION,
    secret: str = "",
) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError
    if type(operation) is not str or operation not in SUPPORTED_OPERATIONS:
        raise ValueError
    _reject_secret_tree(value, secret)
    if type(value.get("status")) is not str:
        raise ValueError
    status = value["status"]
    expected_keys = _SUCCESS_RESPONSE_KEYS if status == "ok" else _ERROR_RESPONSE_KEYS if status == "error" else None
    if expected_keys is None:
        raise ValueError
    if status == "ok":
        _require_exact_keys(value, expected_keys)
    else:
        keys = frozenset(value)
        if not _ERROR_RESPONSE_KEYS.issubset(keys) or not keys.issubset(
            _ERROR_RESPONSE_KEYS | _ERROR_RESPONSE_OPTIONAL_KEYS
        ):
            raise ValueError
    assert type(value) is dict
    if type(value["schema_version"]) is not int or value["schema_version"] != PROTOCOL_SCHEMA_VERSION:
        raise ValueError
    if type(value["operation"]) is not str or value["operation"] != operation:
        raise ValueError
    _validate_nonce(value["nonce"])
    if expected_nonce is not None:
        _validate_nonce(expected_nonce)
        if value["nonce"] != expected_nonce:
            raise ValueError
    if status == "ok":
        if operation in {
            POSTPROCESS_OLLAMA_OPERATION,
            POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        }:
            _validate_postprocess_result(value["result"])
        else:
            _validate_listing_result(value["result"], operation)
    else:
        error_code = value["error_code"]
        if type(error_code) is not str or error_code not in WORKER_ERROR_CODES:
            raise ValueError
        if "failure_reason" in value and value["failure_reason"] is None:
            raise ValueError
        validate_failure_metadata(
            value.get("failure_reason"),
            value.get("provider_status"),
            error_code=error_code,
        )
    return value

def _object_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if type(key) is not str or key in _FORBIDDEN_KEYS or key in result:
            raise ValueError
        result[key] = item
    return result

def _parse_constant(_value: str) -> object:
    raise ValueError

def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError
    return parsed

def _validate_json_tree(value: object) -> None:
    value_type = type(value)
    if value_type is str:
        _reject_surrogates(value)
        return
    if value_type is dict:
        for key, item in value.items():
            if type(key) is not str or key in _FORBIDDEN_KEYS:
                raise ValueError
            _reject_surrogates(key)
            _validate_json_tree(item)
        return
    if value_type is list:
        for item in value:
            _validate_json_tree(item)
        return
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError
        return
    if value is None or value_type is bool or value_type is int:
        return
    raise ValueError

def _canonical_json(value: object) -> bytes:
    rendered = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return rendered.encode("ascii")

def _decode_json(body: bytes) -> object:
    if type(body) is not bytes:
        raise ValueError
    text = body.decode("utf-8")
    value = json.loads(
        text,
        object_pairs_hook=_object_pairs,
        parse_constant=_parse_constant,
        parse_float=_parse_float,
    )
    _validate_json_tree(value)
    if _canonical_json(value) != body:
        raise ValueError
    return value

def _decode_frame(frame: object, *, max_frame_bytes: int) -> object:
    if type(frame) is not bytes or len(frame) < FRAME_PREFIX_BYTES:
        raise ValueError
    json_length = int.from_bytes(frame[:FRAME_PREFIX_BYTES], "big", signed=False)
    frame_length = FRAME_PREFIX_BYTES + json_length
    if frame_length > max_frame_bytes or len(frame) != frame_length:
        raise ValueError
    return _decode_json(frame[FRAME_PREFIX_BYTES:])

def _encode_frame(value: object, *, max_frame_bytes: int) -> bytes:
    body = _canonical_json(value)
    frame_length = FRAME_PREFIX_BYTES + len(body)
    if frame_length > max_frame_bytes:
        raise ValueError
    return len(body).to_bytes(FRAME_PREFIX_BYTES, "big", signed=False) + body

def encode_request(
    request: object,
    *,
    monotonic_ns: Callable[[], object] | None = None,
) -> bytes:
    """Validate and encode one canonical model-list request frame."""

    def operation() -> bytes:
        value = _validate_request(request, monotonic_ns)
        return _encode_frame(value, max_frame_bytes=MAX_REQUEST_FRAME_BYTES)

    return _run("remote-request-invalid", operation)

def decode_request(
    frame: object,
    *,
    monotonic_ns: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Decode and validate exactly one canonical request frame."""

    def operation() -> dict[str, object]:
        value = _decode_frame(frame, max_frame_bytes=MAX_REQUEST_FRAME_BYTES)
        return _validate_request(value, monotonic_ns)

    return _run("remote-request-invalid", operation)

def encode_response(
    response: object,
    *,
    operation: str = LIST_OLLAMA_MODELS_OPERATION,
) -> bytes:
    """Validate and encode one canonical worker response frame."""

    def run_operation() -> bytes:
        if type(response) is not dict:
            raise ValueError
        wire_response = dict(response)
        if "operation" in wire_response and wire_response["operation"] != operation:
            raise ValueError
        wire_response["operation"] = operation
        value = _validate_response(wire_response, operation=operation)
        max_frame_bytes = MAX_ERROR_FRAME_BYTES if value["status"] == "error" else MAX_RESPONSE_FRAME_BYTES
        return _encode_frame(value, max_frame_bytes=max_frame_bytes)

    return _run("remote-worker-protocol-invalid", run_operation)

def decode_response(
    frame: object,
    *,
    expected_nonce: str | None = None,
    operation: str = LIST_OLLAMA_MODELS_OPERATION,
    secret: str = "",
) -> dict[str, object]:
    """Decode and validate exactly one canonical worker response frame."""

    def run_operation() -> dict[str, object]:
        value = _decode_frame(frame, max_frame_bytes=MAX_RESPONSE_FRAME_BYTES)
        validated = _validate_response(
            value,
            expected_nonce,
            operation=operation,
            secret=secret,
        )
        if validated["status"] == "error" and len(frame) > MAX_ERROR_FRAME_BYTES:
            raise ValueError
        public_response = dict(validated)
        public_response.pop("operation")
        return public_response

    return _run("remote-worker-protocol-invalid", run_operation)

_CONTROL_RELEASE_TAG = b"RHV1"

_CONTROL_RELEASE_BYTES = len(_CONTROL_RELEASE_TAG) + 32 + hashlib.sha256().digest_size

def _encode_control_release(nonce: str, response_frame: bytes) -> bytes:
    if type(nonce) is not str or type(response_frame) is not bytes:
        raise ValueError
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ValueError
    return (
        _CONTROL_RELEASE_TAG
        + nonce.encode("ascii")
        + hashlib.sha256(response_frame).digest()
    )

def _validate_control_release(
    release_frame: bytes,
    expected_nonce: str,
    response_frame: bytes,
) -> bool:
    try:
        expected = _encode_control_release(expected_nonce, response_frame)
    except (UnicodeError, ValueError):
        return False
    return type(release_frame) is bytes and len(release_frame) == _CONTROL_RELEASE_BYTES and hmac.compare_digest(
        release_frame,
        expected,
    )

def _request_nonce_from_frame(frame: bytes) -> str | None:
    try:
        value = _decode_frame(frame, max_frame_bytes=MAX_REQUEST_FRAME_BYTES)
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        return None
    if type(value) is not dict or type(value.get("nonce")) is not str:
        return None
    return value["nonce"]










































































class _SupervisorFailure(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in SUPERVISOR_ERROR_CODES:
            code = "remote-worker-protocol-invalid"
        self.code = code
        super().__init__(code)


class _CleanupDeadline(Exception):
    pass


class _CleanupCloseFailure(Exception):
    pass


@dataclass(slots=True)
class _WorkerHandle:
    process: subprocess.Popen[bytes]
    pid: int
    start_time: str
    pidfd: int
    descendants: dict[int, str] = field(default_factory=dict)
    observation_complete: bool = False
    observation_after_request: bool = False
    response_complete: bool = False
    release_pending: bool = False
    root_exited_before_release: bool = False
    output_credentials_required: bool = False
    request_nonce: str | None = None
    release_frame: bytes | None = None


_MAX_PROC_SCAN_ENTRIES = 100_000
_PUMP_READ_BYTES = 65_536
_TERM_WINDOW_NS = 1_000_000_000
_REAP_RESERVE_NS = 100_000_000
_DESCENDANT_SCAN_INTERVAL_NS = 25_000_000
_DESCENDANT_IDENTITY_RECHECK_NS = 1_000_000
_UNIX_CREDENTIALS = struct.Struct("=3i")


def _supervisor_error(nonce: str, code: str) -> dict[str, object]:
    result: dict[str, object] = {
        "error_code": code,
        "nonce": nonce,
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "status": "error",
    }
    reason = {
        "remote-operation-timeout": "timeout",
        "remote-worker-unavailable": "worker_startup",
        "remote-worker-protocol-invalid": "worker_protocol",
        "remote-worker-cleanup-unconfirmed": "worker_protocol",
    }.get(code)
    if reason is not None:
        validate_failure_metadata(reason, None, error_code=code)
        result["failure_reason"] = reason
    return result








def _clock_ns(monotonic_ns: Callable[[], object] | None) -> Callable[[], object]:
    clock = time.monotonic_ns if monotonic_ns is None else monotonic_ns
    if not callable(clock):
        raise _SupervisorFailure("remote-worker-protocol-invalid")
    return clock


def _now_ns(clock: Callable[[], object]) -> int:
    try:
        value = clock()
    except Exception:
        raise _SupervisorFailure("remote-worker-protocol-invalid")
    if type(value) is not int:
        raise _SupervisorFailure("remote-worker-protocol-invalid")
    return value


def _cancel_requested(cancel: Callable[[], object] | None) -> bool:
    if cancel is None:
        return False
    if not callable(cancel):
        raise _SupervisorFailure("remote-worker-cancelled")
    try:
        return cancel() is True
    except Exception:
        raise _SupervisorFailure("remote-worker-cancelled")


def _close_owned_socket(stream: socket.socket) -> bool:
    try:
        fd = stream.detach()
    except Exception:
        return False
    if type(fd) is not int:
        return False
    if fd < 0:
        return True
    try:
        os.close(fd)
    except Exception:
        return False
    return True


def _close_stream(stream: object | None) -> bool:
    if stream is None:
        return True
    if isinstance(stream, socket.socket):
        return _close_owned_socket(stream)
    try:
        close = getattr(stream, "close")
        close()
    except Exception:
        return False
    try:
        fd = stream.fileno()  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return True
    except Exception:
        return False
    if isinstance(fd, int):
        return fd < 0
    return False


def _close_worker_streams(handle: _WorkerHandle) -> bool:
    process = handle.process
    try:
        streams = (
            getattr(process, "stdin", None),
            getattr(process, "stdout", None),
            getattr(process, "stderr", None),
        )
    except Exception:
        return False
    closed = True
    for stream in streams:
        closed = _close_stream(stream) and closed
    return closed


def _close_spawn_socket(endpoint: socket.socket | None) -> None:
    if endpoint is None:
        return
    if not _close_stream(endpoint):
        raise OSError("worker output socket close failed")


def _read_worker_output(
    handle: _WorkerHandle,
    stream: object,
    fd: int,
    deadline_ns: int | None = None,
    clock: Callable[[], object] | None = None,
) -> bytes:
    if not handle.output_credentials_required:
        if deadline_ns is not None and clock is not None:
            _check_cleanup_deadline(deadline_ns, clock)
        return os.read(fd, _PUMP_READ_BYTES)
    recvmsg = getattr(stream, "recvmsg", None)
    if not callable(recvmsg):
        raise OSError("worker output credentials unavailable")
    if deadline_ns is not None and clock is not None:
        _check_cleanup_deadline(deadline_ns, clock)
    try:
        data, ancillary, flags, _address = recvmsg(
            _PUMP_READ_BYTES,
            socket.CMSG_SPACE(_UNIX_CREDENTIALS.size),
        )
    except BlockingIOError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise OSError("worker output receive failed") from error
    if not isinstance(data, bytes):
        raise OSError("worker output receive invalid")
    if not data:
        return data
    if flags & socket.MSG_CTRUNC:
        raise OSError("worker output credentials truncated")
    credentials: list[tuple[int, int, int]] = []
    for level, kind, payload in ancillary:
        if level != socket.SOL_SOCKET or kind != socket.SCM_CREDENTIALS:
            raise OSError("worker output credentials invalid")
        if len(payload) != _UNIX_CREDENTIALS.size:
            raise OSError("worker output credentials invalid")
        try:
            credentials.append(_UNIX_CREDENTIALS.unpack(payload))
        except struct.error as error:
            raise OSError("worker output credentials invalid") from error
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if not callable(getuid) or not callable(getgid) or len(credentials) != 1:
        raise OSError("worker output credentials unavailable")
    sender_pid, sender_uid, sender_gid = credentials[0]
    if sender_pid != handle.pid or sender_uid != getuid() or sender_gid != getgid():
        raise OSError("worker output sender invalid")
    return data


def _process_start_time(pid: int) -> str | None:
    if type(pid) is not int or pid <= 0:
        return None
    try:
        with Path(f"/proc/{pid}/stat").open("r", encoding="ascii") as stream:
            raw = stream.read(4096)
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    try:
        fields = raw[raw.rindex(")") + 2 :].split()
        start_time = fields[19]
    except (IndexError, ValueError):
        return None
    if not start_time.isascii() or not start_time.isdigit():
        return None
    return start_time


def _proc_record(pid: int) -> tuple[int, str, str] | None:
    if type(pid) is not int or pid <= 0:
        return None
    try:
        with Path(f"/proc/{pid}/stat").open("r", encoding="ascii") as stream:
            raw = stream.read(4096)
    except ProcessLookupError:
        raise
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
    try:
        fields = raw[raw.rindex(")") + 2 :].split()
        state = fields[0]
        parent_pid = int(fields[1])
        start_time = fields[19]
    except (IndexError, ValueError):
        raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
    if not start_time.isascii() or not start_time.isdigit():
        raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
    return parent_pid, start_time, state


def _descendant_snapshot(
    root_pid: int,
    expected_root_start_time: str | None = None,
) -> dict[int, str] | None:
    if type(root_pid) is not int or root_pid <= 0:
        return None
    if expected_root_start_time is not None:
        try:
            root_record = _proc_record(root_pid)
        except ProcessLookupError:
            return {}
        if root_record is None:
            return {}
        if root_record[1] != expected_root_start_time:
            return None
    records: dict[int, tuple[int, str]] = {}
    try:
        with os.scandir("/proc") as entries:
            for index, entry in enumerate(entries):
                if index >= _MAX_PROC_SCAN_ENTRIES:
                    return None
                if not entry.name.isdecimal():
                    continue
                pid = int(entry.name)
                try:
                    record = _proc_record(pid)
                except ProcessLookupError:
                    return None
                except _SupervisorFailure:
                    return None
                if record is None:
                    continue
                parent_pid, start_time, _state = record
                records[pid] = (parent_pid, start_time)
    except OSError:
        return None
    children: dict[int, list[int]] = {}
    for pid, (parent_pid, _start_time) in records.items():
        children.setdefault(parent_pid, []).append(pid)
    descendants: dict[int, str] = {}
    pending = [root_pid]
    while pending:
        parent_pid = pending.pop()
        for child_pid in children.get(parent_pid, ()):
            if child_pid in descendants:
                continue
            child_record = records.get(child_pid)
            if child_record is None:
                return None
            descendants[child_pid] = child_record[1]
            pending.append(child_pid)
    return descendants


def _observe_descendants(
    handle: _WorkerHandle,
    deadline_ns: int,
    clock: Callable[[], object],
    *,
    after_request: bool = False,
) -> tuple[bool, bool]:
    if _now_ns(clock) >= deadline_ns:
        return False, True
    try:
        snapshot = _descendant_snapshot(handle.pid, handle.start_time)
    except _SupervisorFailure:
        return False, False
    if _now_ns(clock) >= deadline_ns:
        return False, True
    if snapshot is None:
        return False, False
    handle.descendants.update(snapshot)
    handle.observation_complete = True
    if after_request:
        handle.observation_after_request = True
    return True, False


def _check_cleanup_deadline(deadline_ns: int, clock: Callable[[], object]) -> None:
    if _now_ns(clock) >= deadline_ns:
        raise _CleanupDeadline


def _wait_for_descendant_identity_absence(
    pid: int,
    start_time: str,
    deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    while True:
        _check_cleanup_deadline(deadline_ns, clock)
        try:
            present = _identity_present(pid, start_time)
        except Exception:
            return False
        _check_cleanup_deadline(deadline_ns, clock)
        if present is False:
            remaining_ns = deadline_ns - _now_ns(clock)
            if remaining_ns <= 0:
                return False
            try:
                time.sleep(
                    min(
                        _DESCENDANT_IDENTITY_RECHECK_NS / 1_000_000_000,
                        remaining_ns / 1_000_000_000,
                    )
                )
            except (OSError, OverflowError, ValueError):
                return False
            _check_cleanup_deadline(deadline_ns, clock)
            return True
        if present is None:
            return False
        remaining_ns = deadline_ns - _now_ns(clock)
        if remaining_ns <= 0:
            return False
        try:
            time.sleep(
                min(
                    _DESCENDANT_IDENTITY_RECHECK_NS / 1_000_000_000,
                    remaining_ns / 1_000_000_000,
                )
            )
        except (OSError, OverflowError, ValueError):
            return False


def _identity_present(pid: int, expected_start_time: str) -> bool | None:
    try:
        record = _proc_record(pid)
    except ProcessLookupError:
        return False
    except _SupervisorFailure:
        return None
    if record is None:
        return False
    if record[1] != expected_start_time:
        return None
    if record[2] == "Z":
        return False
    return True


def _send_pidfd_signal(pidfd: int, pid: int, expected_start_time: str, signal_number: int) -> bool:
    if _identity_present(pid, expected_start_time) is not True:
        return False
    sender = getattr(signal, "pidfd_send_signal", None)
    if not callable(sender) or type(pidfd) is not int or pidfd < 0:
        return False
    try:
        sender(pidfd, signal_number, None, 0)
    except ProcessLookupError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def _send_descendant_signal(pid: int, expected_start_time: str, signal_number: int) -> bool:
    present = _identity_present(pid, expected_start_time)
    if present is False:
        return True
    if present is not True:
        return False
    opener = getattr(os, "pidfd_open", None)
    if not callable(opener):
        return False
    try:
        pidfd = opener(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    if type(pidfd) is not int or pidfd < 0:
        _close_fd(pidfd)
        return False
    signal_ok = False
    try:
        try:
            os.set_inheritable(pidfd, False)
        except OSError:
            signal_ok = False
        else:
            signal_ok = _send_pidfd_signal(pidfd, pid, expected_start_time, signal_number)
    finally:
        close_ok = _close_fd(pidfd)
    return signal_ok and close_ok


def _close_fd(fd: object) -> bool:
    if type(fd) is not int or fd < 0:
        return True
    try:
        os.close(fd)
    except Exception:
        return False
    return True


def _send_worker_tree_signal(
    handle: _WorkerHandle,
    descendants: dict[int, str],
    signal_number: int,
    *,
    deadline_ns: int | None = None,
    clock: Callable[[], object] | None = None,
    include_root: bool = True,
) -> bool:
    def check_deadline() -> None:
        if deadline_ns is not None and clock is not None:
            _check_cleanup_deadline(deadline_ns, clock)

    check_deadline()
    if include_root:
        if not _send_pidfd_signal(handle.pidfd, handle.pid, handle.start_time, signal_number):
            if handle.process.poll() is None:
                return False
        check_deadline()
    for pid, start_time in sorted(descendants.items()):
        if pid == handle.pid:
            continue
        check_deadline()
        if not _send_descendant_signal(pid, start_time, signal_number):
            return False
        check_deadline()
    return True


def _wait_for_descendants_exit_body(
    descendants: dict[int, str],
    deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    """Confirm known descendant exit through identity-bound pidfds."""

    opener = getattr(os, "pidfd_open", None)
    if not callable(opener):
        return False
    selector = selectors.DefaultSelector()
    pidfds: dict[int, int] = {}
    owned_pidfds: list[int] = []

    def consume_pidfd(pidfd: int) -> bool:
        try:
            owned_pidfds.remove(pidfd)
        except ValueError:
            return False
        return _close_fd(pidfd)

    try:
        for pid, start_time in sorted(descendants.items()):
            _check_cleanup_deadline(deadline_ns, clock)
            try:
                present = _identity_present(pid, start_time)
            except Exception:
                return False
            _check_cleanup_deadline(deadline_ns, clock)
            if present is False:
                continue
            if present is not True:
                return False
            try:
                pidfd = opener(pid, 0)
            except (OSError, TypeError, ValueError):
                _check_cleanup_deadline(deadline_ns, clock)
                try:
                    present_after_open = _identity_present(pid, start_time)
                except Exception:
                    return False
                _check_cleanup_deadline(deadline_ns, clock)
                if present_after_open is False:
                    continue
                return False
            if type(pidfd) is not int or pidfd < 0:
                return False
            owned_pidfds.append(pidfd)
            try:
                os.set_inheritable(pidfd, False)
                _check_cleanup_deadline(deadline_ns, clock)
                try:
                    verified = _identity_present(pid, start_time)
                except Exception:
                    if not consume_pidfd(pidfd):
                        return False
                    return False
                _check_cleanup_deadline(deadline_ns, clock)
                if verified is False:
                    if not consume_pidfd(pidfd):
                        return False
                    continue
                if verified is not True:
                    if not consume_pidfd(pidfd):
                        return False
                    return False
                selector.register(pidfd, selectors.EVENT_READ, pid)
                pidfds[pid] = pidfd
            except (OSError, TypeError, ValueError):
                if not consume_pidfd(pidfd):
                    return False
                return False
        while pidfds:
            _check_cleanup_deadline(deadline_ns, clock)
            remaining = (deadline_ns - _now_ns(clock)) / 1_000_000_000
            try:
                events = selector.select(remaining)
            except (OSError, ValueError):
                return False
            _check_cleanup_deadline(deadline_ns, clock)
            if not events:
                return False
            for key, _mask in events:
                pid = key.data
                pidfd = pidfds.get(pid)
                if pidfd is None:
                    continue
                start_time = descendants.get(pid)
                if type(start_time) is not str:
                    return False
                pidfds.pop(pid, None)
                try:
                    selector.unregister(pidfd)
                except (KeyError, OSError, ValueError):
                    pass
                if not consume_pidfd(pidfd):
                    return False
                if not _wait_for_descendant_identity_absence(
                    pid,
                    start_time,
                    deadline_ns,
                    clock,
                ):
                    return False
        return True
    except _CleanupDeadline:
        return False
    finally:
        close_failure = False
        try:
            selector.close()
        except Exception:
            close_failure = True
        while owned_pidfds:
            pidfd = owned_pidfds.pop()
            if not _close_fd(pidfd):
                close_failure = True
        if close_failure:
            raise _CleanupCloseFailure


def _wait_for_descendants_exit(
    descendants: dict[int, str],
    deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    try:
        return _wait_for_descendants_exit_body(descendants, deadline_ns, clock)
    except _CleanupCloseFailure:
        return False


def _drain_worker_output(
    handle: _WorkerHandle,
    deadline_ns: int,
    clock: Callable[[], object],
    *,
    reject_output: bool = False,
) -> bool:
    selector = selectors.DefaultSelector()
    registered: set[int] = set()
    output_fds: list[int] = []
    output_streams: dict[int, object] = {}
    try:
        for stream in (getattr(handle.process, "stdout", None), getattr(handle.process, "stderr", None)):
            if stream is None:
                if reject_output:
                    return False
                continue
            fd = stream.fileno()
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, "output")
            registered.add(fd)
            output_fds.append(fd)
            output_streams[fd] = stream
        if handle.pidfd >= 0:
            selector.register(handle.pidfd, selectors.EVENT_READ, "pidfd")
            registered.add(handle.pidfd)
        while True:
            try:
                _check_cleanup_deadline(deadline_ns, clock)
            except _CleanupDeadline:
                return False
            root_exited = handle.process.poll() is not None
            if root_exited and reject_output:
                if not output_fds:
                    return False
                for fd in output_fds:
                    try:
                        chunk = _read_worker_output(
                            handle,
                            output_streams[fd],
                            fd,
                            deadline_ns,
                            clock,
                        )
                    except (_CleanupDeadline, BlockingIOError, OSError, ValueError):
                        return False
                    if chunk:
                        return False
                return True
            if root_exited and not registered:
                return True
            now = _now_ns(clock)
            if now >= deadline_ns:
                return False
            try:
                events = selector.select(min((deadline_ns - now) / 1_000_000_000, 0.05))
            except (OSError, ValueError):
                return False
            try:
                _check_cleanup_deadline(deadline_ns, clock)
            except _CleanupDeadline:
                return False
            if not events:
                continue
            for key, _mask in events:
                try:
                    _check_cleanup_deadline(deadline_ns, clock)
                except _CleanupDeadline:
                    return False
                fd = key.fd
                if key.data == "pidfd":
                    if handle.process.poll() is not None:
                        selector.unregister(fd)
                        registered.discard(fd)
                    continue
                try:
                    chunk = _read_worker_output(
                        handle,
                        output_streams[fd],
                        fd,
                        deadline_ns,
                        clock,
                    )
                except BlockingIOError:
                    continue
                except (_CleanupDeadline, OSError, ValueError):
                    return False
                if not chunk:
                    selector.unregister(fd)
                    registered.discard(fd)
                elif reject_output:
                    return False
    finally:
        selector.close()


def _cleanup_worker_body(
    handle: _WorkerHandle,
    lifecycle_deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    cleanup_ok = True
    descendants = dict(handle.descendants)
    observation_allowed = True
    try:
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        if not _close_stream(getattr(handle.process, "stdin", None)):
            cleanup_ok = False
        observed, deadline_hit = _observe_descendants(handle, lifecycle_deadline_ns, clock)
        if deadline_hit:
            return False
        descendants.update(handle.descendants)
        if not observed:
            cleanup_ok = False
            observation_allowed = False
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        if handle.process.poll() is None or descendants:
            term_targets_ok = _send_worker_tree_signal(
                handle,
                descendants,
                signal.SIGTERM,
                deadline_ns=lifecycle_deadline_ns,
                clock=clock,
            )
            cleanup_ok = cleanup_ok and term_targets_ok
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        term_deadline_ns = min(lifecycle_deadline_ns, _now_ns(clock) + _TERM_WINDOW_NS)
        next_scan_ns = _now_ns(clock) + _DESCENDANT_SCAN_INTERVAL_NS
        while True:
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            now = _now_ns(clock)
            if observation_allowed and now >= next_scan_ns:
                observed, deadline_hit = _observe_descendants(
                    handle,
                    lifecycle_deadline_ns,
                    clock,
                )
                if deadline_hit:
                    return False
                descendants.update(handle.descendants)
                if not observed:
                    cleanup_ok = False
                    observation_allowed = False
                else:
                    next_scan_ns = _now_ns(clock) + _DESCENDANT_SCAN_INTERVAL_NS
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            live_targets: list[bool | None] = []
            for pid, start_time in descendants.items():
                if pid == handle.pid:
                    continue
                _check_cleanup_deadline(lifecycle_deadline_ns, clock)
                present = _identity_present(pid, start_time)
                _check_cleanup_deadline(lifecycle_deadline_ns, clock)
                live_targets.append(present)
            root_exited = handle.process.poll() is not None
            if root_exited and not any(value is True for value in live_targets):
                break
            now = _now_ns(clock)
            if now >= term_deadline_ns:
                break
            _drain_worker_output(handle, term_deadline_ns, clock)
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        root_live = handle.process.poll() is None
        live_descendants = False
        for pid, start_time in descendants.items():
            if pid == handle.pid:
                continue
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            present = _identity_present(pid, start_time)
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            live_descendants = live_descendants or present is True
        if root_live or live_descendants:
            if not _send_worker_tree_signal(
                handle,
                descendants,
                signal.SIGKILL,
                deadline_ns=lifecycle_deadline_ns,
                clock=clock,
            ):
                cleanup_ok = False
            kill_drain_ok = _drain_worker_output(handle, lifecycle_deadline_ns, clock)
            cleanup_ok = cleanup_ok and kill_drain_ok
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            if not _wait_for_descendants_exit(descendants, lifecycle_deadline_ns, clock):
                cleanup_ok = False
        if handle.process.poll() is None:
            try:
                _check_cleanup_deadline(lifecycle_deadline_ns, clock)
                remaining = max(0.0, (lifecycle_deadline_ns - _now_ns(clock)) / 1_000_000_000)
                handle.process.wait(timeout=remaining)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                cleanup_ok = False
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        if handle.process.poll() is None:
            cleanup_ok = False
        if descendants and not _wait_for_descendants_exit(
            descendants,
            lifecycle_deadline_ns,
            clock,
        ):
            cleanup_ok = False
        if observation_allowed:
            observed, deadline_hit = _observe_descendants(handle, lifecycle_deadline_ns, clock)
            if deadline_hit:
                return False
            descendants.update(handle.descendants)
            if not observed:
                cleanup_ok = False
        else:
            cleanup_ok = False
        for pid, start_time in descendants.items():
            if pid == handle.pid:
                continue
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            present = _identity_present(pid, start_time)
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            if present is not False:
                cleanup_ok = False
        return cleanup_ok
    except _CleanupDeadline:
        return False
    except Exception:
        return False


def _cleanup_worker(
    handle: _WorkerHandle | None,
    lifecycle_deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    if handle is None:
        return True
    if handle.release_pending:
        return _cleanup_released_worker(handle, lifecycle_deadline_ns, clock)
    cleanup_ok = _cleanup_worker_body(handle, lifecycle_deadline_ns, clock)
    streams_closed = _close_worker_streams(handle)
    pidfd = handle.pidfd
    handle.pidfd = -1
    pidfd_closed = _close_fd(pidfd)
    return cleanup_ok and streams_closed and pidfd_closed


def _send_control_frame(
    handle: _WorkerHandle,
    frame: bytes,
    deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    stream = getattr(handle.process, "stdin", None)
    if not isinstance(stream, socket.socket) or type(frame) is not bytes:
        return False
    try:
        credentials = _control_credentials()
        fd = stream.fileno()
        if type(fd) is not int or fd < 0:
            return False
        os.set_blocking(fd, False)
        selector = selectors.DefaultSelector()
        try:
            selector.register(fd, selectors.EVENT_WRITE)
            offset = 0
            while offset < len(frame):
                _check_cleanup_deadline(deadline_ns, clock)
                if handle.process.poll() is not None:
                    return False
                remaining = (deadline_ns - _now_ns(clock)) / 1_000_000_000
                _check_cleanup_deadline(deadline_ns, clock)
                events = selector.select(remaining)
                _check_cleanup_deadline(deadline_ns, clock)
                if not events:
                    return False
                try:
                    written = _send_control_chunk(
                        stream,
                        frame[offset:],
                        credentials,
                        deadline_ns,
                        clock,
                    )
                except BlockingIOError:
                    continue
                if written <= 0:
                    return False
                offset += written
            _check_cleanup_deadline(deadline_ns, clock)
            return True
        finally:
            selector.close()
    except (_CleanupDeadline, OSError, TypeError, ValueError):
        return False


def _control_credentials() -> bytes:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    getpid = getattr(os, "getpid", None)
    if not callable(getuid) or not callable(getgid) or not callable(getpid):
        raise OSError("control credentials unavailable")
    try:
        return _UNIX_CREDENTIALS.pack(getpid(), getuid(), getgid())
    except (struct.error, TypeError, ValueError):
        raise OSError("control credentials unavailable")


def _send_control_chunk(
    stream: socket.socket,
    data: bytes,
    credentials: bytes,
    deadline_ns: int | None = None,
    clock: Callable[[], object] | None = None,
) -> int:
    sendmsg = getattr(stream, "sendmsg", None)
    if not callable(sendmsg):
        raise OSError("control send unavailable")
    if deadline_ns is not None and clock is not None:
        _check_cleanup_deadline(deadline_ns, clock)
    return sendmsg(
        [data],
        [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)],
    )


def _cleanup_released_worker_body(
    handle: _WorkerHandle,
    lifecycle_deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    """Release a response-complete worker only after its live tree is observed."""

    cleanup_ok = not handle.root_exited_before_release
    descendants = dict(handle.descendants)
    observation_allowed = True
    released = False
    try:
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        observed, deadline_hit = _observe_descendants(handle, lifecycle_deadline_ns, clock)
        if deadline_hit:
            return False
        descendants.update(handle.descendants)
        if not observed:
            cleanup_ok = False
            observation_allowed = False
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        if handle.process.poll() is not None:
            handle.root_exited_before_release = True
            cleanup_ok = False
        release_frame = handle.release_frame
        if release_frame is None or not _send_control_frame(
            handle,
            release_frame,
            lifecycle_deadline_ns,
            clock,
        ):
            cleanup_ok = False
        else:
            released = True
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)

        now = _now_ns(clock)
        remaining = lifecycle_deadline_ns - now
        if handle.process.poll() is None:
            drain_deadline_ns = (
                min(lifecycle_deadline_ns - _TERM_WINDOW_NS, now + _TERM_WINDOW_NS)
                if remaining > _TERM_WINDOW_NS
                else now
            )
        else:
            drain_deadline_ns = lifecycle_deadline_ns
        if drain_deadline_ns <= now:
            cleanup_ok = False
        else:
            drained = _drain_worker_output(
                handle,
                drain_deadline_ns,
                clock,
                reject_output=True,
            )
            cleanup_ok = cleanup_ok and drained
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)

        live_descendants = False
        for pid, start_time in descendants.items():
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            present = _identity_present(pid, start_time)
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            if present is True:
                live_descendants = True
            elif present is None:
                cleanup_ok = False

        root_alive = handle.process.poll() is None
        if root_alive or live_descendants:
            cleanup_ok = False
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            try:
                sent = _send_worker_tree_signal(
                    handle,
                    descendants,
                    signal.SIGTERM,
                    deadline_ns=lifecycle_deadline_ns,
                    clock=clock,
                )
            except _CleanupDeadline:
                return False
            if not sent:
                pidfd = handle.pidfd
                handle.pidfd = -1
                _abandon_spawned_worker(handle.process, pidfd)
                return False

            remaining = lifecycle_deadline_ns - _now_ns(clock)
            if remaining > _REAP_RESERVE_NS:
                term_deadline_ns = min(
                    lifecycle_deadline_ns - _REAP_RESERVE_NS,
                    _now_ns(clock) + _TERM_WINDOW_NS,
                )
                if term_deadline_ns > _now_ns(clock):
                    drained = _drain_worker_output(
                        handle,
                        term_deadline_ns,
                        clock,
                        reject_output=True,
                    )
                    cleanup_ok = cleanup_ok and drained
                    _check_cleanup_deadline(lifecycle_deadline_ns, clock)

            root_alive = handle.process.poll() is None
            live_descendants = False
            for pid, start_time in descendants.items():
                _check_cleanup_deadline(lifecycle_deadline_ns, clock)
                present = _identity_present(pid, start_time)
                _check_cleanup_deadline(lifecycle_deadline_ns, clock)
                if present is True:
                    live_descendants = True
                elif present is None:
                    cleanup_ok = False
            if root_alive or live_descendants:
                _check_cleanup_deadline(lifecycle_deadline_ns, clock)
                try:
                    sent = _send_worker_tree_signal(
                        handle,
                        descendants,
                        signal.SIGKILL,
                        deadline_ns=lifecycle_deadline_ns,
                        clock=clock,
                    )
                except _CleanupDeadline:
                    return False
                if not sent:
                    pidfd = handle.pidfd
                    handle.pidfd = -1
                    _abandon_spawned_worker(handle.process, pidfd)
                    return False

        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        try:
            if handle.process.poll() is None:
                remaining = (lifecycle_deadline_ns - _now_ns(clock)) / 1_000_000_000
                handle.process.wait(timeout=remaining)
            else:
                handle.process.wait(timeout=0)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            cleanup_ok = False
        _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        if handle.process.poll() is None:
            cleanup_ok = False
        if descendants and not _wait_for_descendants_exit(
            descendants,
            lifecycle_deadline_ns,
            clock,
        ):
            cleanup_ok = False
        if observation_allowed:
            observed, deadline_hit = _observe_descendants(handle, lifecycle_deadline_ns, clock)
            if deadline_hit:
                return False
            descendants.update(handle.descendants)
            if not observed:
                cleanup_ok = False
        else:
            cleanup_ok = False
        for pid, start_time in descendants.items():
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
            if _identity_present(pid, start_time) is not False:
                cleanup_ok = False
            _check_cleanup_deadline(lifecycle_deadline_ns, clock)
        return cleanup_ok and released
    except _CleanupDeadline:
        return False
    except Exception:
        return False


def _cleanup_released_worker(
    handle: _WorkerHandle,
    lifecycle_deadline_ns: int,
    clock: Callable[[], object],
) -> bool:
    cleanup_ok = _cleanup_released_worker_body(handle, lifecycle_deadline_ns, clock)
    streams_closed = _close_worker_streams(handle)
    pidfd = handle.pidfd
    handle.pidfd = -1
    pidfd_closed = _close_fd(pidfd)
    return cleanup_ok and streams_closed and pidfd_closed


def _validated_interpreter() -> str:
    interpreter = sys.executable
    if type(interpreter) is not str or not os.path.isabs(interpreter):
        raise _SupervisorFailure("remote-worker-unavailable")
    try:
        info = os.stat(interpreter)
    except OSError:
        raise _SupervisorFailure("remote-worker-unavailable")
    if not stat.S_ISREG(info.st_mode) or not os.access(interpreter, os.X_OK):
        raise _SupervisorFailure("remote-worker-unavailable")
    return interpreter


def _worker_environment() -> dict[str, str]:
    return {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "LC_CTYPE": "C.UTF-8"}


def _abandon_spawned_worker(
    process: subprocess.Popen[bytes],
    pidfd: int,
) -> bool:
    if type(pidfd) is not int or pidfd < 0:
        try:
            process.kill()
        except (OSError, TypeError, ValueError):
            pass
        try:
            process.wait(timeout=CLEANUP_GRACE_NS / 1_000_000_000)
        except (subprocess.TimeoutExpired, OSError, TypeError, ValueError):
            pass
        _close_worker_streams(
            _WorkerHandle(process=process, pid=0, start_time="", pidfd=-1)
        )
        return False
    signalled = False
    sender = getattr(signal, "pidfd_send_signal", None)
    if callable(sender):
        try:
            sender(pidfd, signal.SIGKILL, None, 0)
            signalled = True
        except (OSError, TypeError, ValueError):
            pass
    if not signalled:
        try:
            process.kill()
        except (OSError, TypeError, ValueError):
            pass
        try:
            process.wait(timeout=CLEANUP_GRACE_NS / 1_000_000_000)
        except (subprocess.TimeoutExpired, OSError, TypeError, ValueError):
            pass
        _close_worker_streams(
            _WorkerHandle(process=process, pid=0, start_time="", pidfd=-1)
        )
        _close_fd(pidfd)
        return False
    reaped = True
    try:
        process.wait(timeout=CLEANUP_GRACE_NS / 1_000_000_000)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        reaped = False
    streams_closed = _close_worker_streams(
        _WorkerHandle(process=process, pid=0, start_time="", pidfd=-1)
    )
    pidfd_closed = _close_fd(pidfd)
    return reaped and streams_closed and pidfd_closed


def _spawn_worker(request_frame: bytes) -> _WorkerHandle:
    interpreter = _validated_interpreter()
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if not callable(opener) or not callable(sender):
        raise _SupervisorFailure("remote-worker-unavailable")
    stdout_parent: socket.socket | None = None
    stdout_child: socket.socket | None = None
    stderr_parent: socket.socket | None = None
    stderr_child: socket.socket | None = None
    control_parent: socket.socket | None = None
    control_child: socket.socket | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        stdout_parent, stdout_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        stderr_parent, stderr_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        control_parent, control_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        required_socket_features = (
            getattr(socket, "SO_PASSCRED", None),
            getattr(socket, "SCM_CREDENTIALS", None),
            getattr(socket, "MSG_CTRUNC", None),
        )
        if any(not isinstance(feature, int) for feature in required_socket_features):
            raise OSError
        if not callable(getattr(stdout_parent, "recvmsg", None)) or not callable(
            getattr(stderr_parent, "recvmsg", None)
        ):
            raise OSError
        stdout_parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        stderr_parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        for endpoint in (
            stdout_parent,
            stdout_child,
            stderr_parent,
            stderr_child,
            control_parent,
            control_child,
        ):
            os.set_inheritable(endpoint.fileno(), False)
        process = subprocess.Popen(
            [interpreter, "-I", "-B", "-m", "speed_of_cinnamon.remote_http_worker"],
            stdin=control_child.fileno(),
            stdout=stdout_child.fileno(),
            stderr=stderr_child.fileno(),
            close_fds=True,
            start_new_session=True,
            shell=False,
            env=_worker_environment(),
        )
        _close_spawn_socket(stdout_child)
        stdout_child = None
        _close_spawn_socket(stderr_child)
        stderr_child = None
        _close_spawn_socket(control_child)
        control_child = None
        process.stdin = control_parent
        control_parent = None
        process.stdout = stdout_parent
        process.stderr = stderr_parent
        stdout_parent = None
        stderr_parent = None
    except (OSError, ValueError, TypeError):
        for endpoint in (
            stdout_parent,
            stdout_child,
            stderr_parent,
            stderr_child,
            control_parent,
            control_child,
        ):
            try:
                _close_spawn_socket(endpoint)
            except OSError:
                pass
        if process is not None:
            cleanup_confirmed = _abandon_spawned_worker(process, -1)
            raise _SupervisorFailure(
                "remote-worker-unavailable"
                if cleanup_confirmed
                else "remote-worker-cleanup-unconfirmed"
            )
        raise _SupervisorFailure("remote-worker-unavailable")
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        cleanup_confirmed = _abandon_spawned_worker(process, -1)
        raise _SupervisorFailure(
            "remote-worker-unavailable"
            if cleanup_confirmed
            else "remote-worker-cleanup-unconfirmed"
        )
    pidfd = -1
    try:
        pidfd = opener(pid, 0)
        if type(pidfd) is not int or pidfd < 0:
            raise OSError
        os.set_inheritable(pidfd, False)
        start_time = _process_start_time(pid)
        if not start_time:
            raise OSError
        return _WorkerHandle(
            process=process,
            pid=pid,
            start_time=start_time,
            pidfd=pidfd,
            output_credentials_required=True,
        )
    except (OSError, TypeError, ValueError):
        cleanup_confirmed = _abandon_spawned_worker(process, pidfd)
        raise _SupervisorFailure(
            "remote-worker-unavailable"
            if cleanup_confirmed
            else "remote-worker-cleanup-unconfirmed"
        )


def _pump_worker(
    handle: _WorkerHandle,
    request_frame: bytes,
    network_deadline_ns: int,
    clock: Callable[[], object],
    cancel: Callable[[], object] | None,
) -> bytes:
    selector = selectors.DefaultSelector()
    response = bytearray()
    request_offset = 0
    stdout_complete = False
    request_complete = False
    registered: set[int] = set()
    output_streams: dict[int, object] = {}
    try:
        if handle.process.stdin is None or handle.process.stdout is None or handle.process.stderr is None:
            raise _SupervisorFailure("remote-worker-protocol-invalid")
        if not isinstance(handle.process.stdin, socket.socket):
            raise _SupervisorFailure("remote-worker-protocol-invalid")
        try:
            control_credentials = _control_credentials()
        except OSError:
            raise _SupervisorFailure("remote-worker-protocol-invalid")
        stdin_fd = handle.process.stdin.fileno()
        stdout_fd = handle.process.stdout.fileno()
        stderr_fd = handle.process.stderr.fileno()
        for fd in (stdin_fd, stdout_fd, stderr_fd, handle.pidfd):
            if type(fd) is not int or fd < 0:
                raise _SupervisorFailure("remote-worker-protocol-invalid")
            os.set_blocking(fd, False)
        selector.register(stdin_fd, selectors.EVENT_WRITE, "stdin")
        selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
        selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
        selector.register(handle.pidfd, selectors.EVENT_READ, "pidfd")
        registered.update((stdin_fd, stdout_fd, stderr_fd, handle.pidfd))
        output_streams[stdout_fd] = handle.process.stdout
        output_streams[stderr_fd] = handle.process.stderr
        if handle.request_nonce is None:
            handle.request_nonce = _request_nonce_from_frame(request_frame)
        observed, deadline_hit = _observe_descendants(handle, network_deadline_ns, clock)
        if deadline_hit:
            raise _SupervisorFailure("remote-operation-timeout")
        if not observed:
            raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
        next_snapshot_ns = _now_ns(clock) + _DESCENDANT_SCAN_INTERVAL_NS
        request_view = memoryview(request_frame)
        while True:
            if _cancel_requested(cancel):
                raise _SupervisorFailure("remote-worker-cancelled")
            now = _now_ns(clock)
            if now >= network_deadline_ns:
                raise _SupervisorFailure("remote-operation-timeout")
            if request_complete and now >= next_snapshot_ns:
                observed, deadline_hit = _observe_descendants(
                    handle,
                    network_deadline_ns,
                    clock,
                    after_request=True,
                )
                if deadline_hit:
                    raise _SupervisorFailure("remote-operation-timeout")
                if not observed:
                    raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
                next_snapshot_ns = _now_ns(clock) + _DESCENDANT_SCAN_INTERVAL_NS
            if stdout_complete and registered.isdisjoint({stdout_fd, stderr_fd}):
                observed, deadline_hit = _observe_descendants(
                    handle,
                    network_deadline_ns,
                    clock,
                    after_request=request_complete,
                )
                if deadline_hit:
                    raise _SupervisorFailure("remote-operation-timeout")
                if not observed or not handle.observation_after_request:
                    raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
                handle.response_complete = True
                try:
                    handle.release_frame = _encode_control_release(
                        handle.request_nonce or "",
                        bytes(response),
                    )
                except (UnicodeError, ValueError):
                    handle.release_frame = None
                handle.release_pending = True
                if handle.process.poll() is not None:
                    handle.root_exited_before_release = True
                return bytes(response)
            try:
                timeout = (network_deadline_ns - now) / 1_000_000_000
                if request_complete:
                    timeout = min(timeout, max(0.0, (next_snapshot_ns - now) / 1_000_000_000))
                _check_cleanup_deadline(network_deadline_ns, clock)
                events = selector.select(timeout)
            except _CleanupDeadline:
                raise _SupervisorFailure("remote-operation-timeout")
            except (OSError, ValueError):
                raise _SupervisorFailure("remote-worker-protocol-invalid")
            try:
                _check_cleanup_deadline(network_deadline_ns, clock)
            except _CleanupDeadline:
                raise _SupervisorFailure("remote-operation-timeout")
            if not events:
                continue
            for key, _mask in events:
                try:
                    _check_cleanup_deadline(network_deadline_ns, clock)
                except _CleanupDeadline:
                    raise _SupervisorFailure("remote-operation-timeout")
                fd = key.fd
                if key.data == "pidfd":
                    observed, deadline_hit = _observe_descendants(
                        handle,
                        network_deadline_ns,
                        clock,
                        after_request=request_complete,
                    )
                    if deadline_hit:
                        raise _SupervisorFailure("remote-operation-timeout")
                    if not observed:
                        raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
                    if handle.process.poll() is not None:
                        selector.unregister(fd)
                        registered.discard(fd)
                    continue
                if key.data == "stdin":
                    if request_offset >= len(request_view):
                        selector.unregister(fd)
                        registered.discard(fd)
                        continue
                    try:
                        written = _send_control_chunk(
                            handle.process.stdin,
                            request_view[request_offset:],
                            control_credentials,
                            network_deadline_ns,
                            clock,
                        )
                    except BlockingIOError:
                        continue
                    except _CleanupDeadline:
                        raise _SupervisorFailure("remote-operation-timeout")
                    except (BrokenPipeError, OSError, TypeError, ValueError):
                        raise _SupervisorFailure("remote-worker-protocol-invalid")
                    if written <= 0:
                        raise _SupervisorFailure("remote-worker-protocol-invalid")
                    request_offset += written
                    if request_offset == len(request_view):
                        selector.unregister(fd)
                        registered.discard(fd)
                        request_complete = True
                        observed, deadline_hit = _observe_descendants(
                            handle,
                            network_deadline_ns,
                            clock,
                            after_request=True,
                        )
                        if deadline_hit:
                            raise _SupervisorFailure("remote-operation-timeout")
                        if not observed:
                            raise _SupervisorFailure("remote-worker-cleanup-unconfirmed")
                        next_snapshot_ns = _now_ns(clock) + _DESCENDANT_SCAN_INTERVAL_NS
                    continue
                try:
                    chunk = _read_worker_output(
                        handle,
                        output_streams[fd],
                        fd,
                        network_deadline_ns,
                        clock,
                    )
                except BlockingIOError:
                    continue
                except _CleanupDeadline:
                    raise _SupervisorFailure("remote-operation-timeout")
                except (OSError, ValueError):
                    raise _SupervisorFailure("remote-worker-protocol-invalid")
                if key.data == "stderr":
                    if chunk:
                        raise _SupervisorFailure("remote-worker-protocol-invalid")
                    selector.unregister(fd)
                    registered.discard(fd)
                    continue
                if not chunk:
                    selector.unregister(fd)
                    registered.discard(fd)
                    if not stdout_complete:
                        raise _SupervisorFailure("remote-worker-protocol-invalid")
                    continue
                if len(response) + len(chunk) > MAX_RESPONSE_FRAME_BYTES:
                    raise _SupervisorFailure("remote-worker-protocol-invalid")
                response.extend(chunk)
                if len(response) >= FRAME_PREFIX_BYTES:
                    declared_length = int.from_bytes(response[:FRAME_PREFIX_BYTES], "big", signed=False)
                    frame_length = FRAME_PREFIX_BYTES + declared_length
                    if frame_length > MAX_RESPONSE_FRAME_BYTES or len(response) > frame_length:
                        raise _SupervisorFailure("remote-worker-protocol-invalid")
                    if len(response) == frame_length:
                        stdout_complete = True
    finally:
        selector.close()


def _run_operation(
    request: object,
    *,
    operation: str,
    monotonic_ns: Callable[[], object] | None = None,
    cancel: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Run one isolated remote operation under supervisor ownership."""

    clock = _clock_ns(monotonic_ns)
    if type(request) is not dict or request.get("operation") != operation:
        raise RemoteProtocolError("remote-request-invalid")
    request_frame = encode_request(request, monotonic_ns=clock)
    nonce = request.get("nonce")
    deadline = request.get("deadline_monotonic_ns")
    if type(nonce) is not str or type(deadline) is not int:
        raise RemoteProtocolError("remote-request-invalid")
    secret = ""
    payload = request.get("payload")
    if operation in {
        LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
        POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
    } and type(payload) is dict:
        api_key = payload.get("api_key")
        if type(api_key) is str:
            secret = api_key.strip()
    lifecycle_deadline = deadline + CLEANUP_GRACE_NS
    result: dict[str, object] | None = None
    candidate_response: dict[str, object] | None = None
    handle: _WorkerHandle | None = None
    try:
        try:
            if _cancel_requested(cancel):
                raise _SupervisorFailure("remote-worker-cancelled")
            handle = _spawn_worker(request_frame)
            response_frame = _pump_worker(handle, request_frame, deadline, clock, cancel)
            candidate_response = decode_response(
                response_frame,
                expected_nonce=nonce,
                operation=operation,
                secret=secret,
            )
        except _SupervisorFailure as failure:
            result = _supervisor_error(nonce, failure.code)
        except Exception:
            result = _supervisor_error(nonce, "remote-worker-protocol-invalid")
    finally:
        cleanup_confirmed = _cleanup_worker(handle, lifecycle_deadline, clock)
        if not cleanup_confirmed:
            result = _supervisor_error(nonce, "remote-worker-cleanup-unconfirmed")
        elif candidate_response is not None and handle is not None:
            return_code = handle.process.poll()
            if candidate_response["status"] == "ok" and return_code == 0:
                result = candidate_response
            elif candidate_response["status"] == "error" and return_code == WORKER_EXIT_CODE:
                result = candidate_response
            else:
                result = _supervisor_error(nonce, "remote-worker-protocol-invalid")
    if result is None:
        return _supervisor_error(nonce, "remote-worker-protocol-invalid")
    return result


def run_list_ollama_models(
    request: object,
    *,
    monotonic_ns: Callable[[], object] | None = None,
    cancel: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Run one isolated list-ollama-models operation under supervisor ownership."""

    return _run_operation(
        request,
        operation=LIST_OLLAMA_MODELS_OPERATION,
        monotonic_ns=monotonic_ns,
        cancel=cancel,
    )


def run_list_openai_compatible_models(
    request: object,
    *,
    monotonic_ns: Callable[[], object] | None = None,
    cancel: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Run one isolated list-openai-compatible-models operation."""

    return _run_operation(
        request,
        operation=LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
        monotonic_ns=monotonic_ns,
        cancel=cancel,
    )


def run_postprocess_ollama(
    request: object,
    *,
    monotonic_ns: Callable[[], object] | None = None,
    cancel: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Run one isolated postprocess-ollama operation."""

    return _run_operation(
        request,
        operation=POSTPROCESS_OLLAMA_OPERATION,
        monotonic_ns=monotonic_ns,
        cancel=cancel,
    )


def run_postprocess_openai_compatible(
    request: object,
    *,
    monotonic_ns: Callable[[], object] | None = None,
    cancel: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Run one isolated postprocess-openai-compatible operation."""

    return _run_operation(
        request,
        operation=POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        monotonic_ns=monotonic_ns,
        cancel=cancel,
    )
