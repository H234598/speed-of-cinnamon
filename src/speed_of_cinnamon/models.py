from __future__ import annotations

import json
import hashlib
import math
import os
import secrets
import shutil
import stat as stat_module
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .http_safety import is_loopback_hostname
from .paths import ctranslate2_models_dir, models_dir
from .path_safety import (
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    open_directory_without_following_symlinks,
    open_file_without_following_symlinks,
    read_text_without_following_symlinks,
    _rename_without_replacing,
    write_text_atomically_without_following_symlinks,
)

HUGGING_FACE_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
TINY_DE_MODEL_URL = "https://huggingface.co/wabisabisocial/whisper-tiny-german-ggml/resolve/main/ggml-tiny-de.bin"
HUGGING_FACE_RESOLVE_URL = "https://huggingface.co/{repo}/resolve/main/{filename}"
HUGGING_FACE_DOWNLOAD_HOST = "huggingface.co"
HUGGING_FACE_STORAGE_REDIRECT_HOSTS = {"cas-bridge.xethub.hf.co", "cdn-lfs.huggingface.co"}
MAX_MODEL_DOWNLOAD_BYTES = 1_200_000_000
MAX_MODEL_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MAX_MODEL_DOWNLOAD_URL_CHARS = 16_384
MODEL_SIZE_SLACK_BYTES = 32 * 1024 * 1024
MAX_MODEL_CHECKSUM_JSON_BYTES = 1_000_000
MAX_MODEL_CHECKSUM_PATH_CHARS = 1_024
MAX_MODEL_CHECKSUM_CHARS = 40
_MODEL_CHECKSUM_CACHE_FILE = "model_checksums.json"
_model_checksum_cache: dict[str, dict[str, int | str]] = {}
_model_checksum_cache_loaded = False
ENGLISH_LANGUAGE_CODES = {"", "en", "eng", "english"}
MODEL_DOWNLOAD_REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_MODEL_DOWNLOAD_REDIRECTS = 5
MODEL_ORPHAN_CLEANUP_MIN_AGE_SECONDS = 60 * 60


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note(f"model artifact cleanup failed: {cleanup_error}")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


def _build_model_download_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirectHandler, urllib.request.ProxyHandler({}))


def _safe_utf8_length(value: str, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError(f"{field_name} must be text")
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ModelError(f"{field_name} contains malformed UTF-8") from exc


_MODEL_DOWNLOAD_OPENER = _build_model_download_opener()


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError("value must be text")
    lowered = (value or "").lower()
    control_codepoints = tuple(range(0x20)) + (0x7F,) + tuple(range(0x80, 0xA0))
    if any(sequence in lowered for sequence in ("\\a", "\\b", "\\f", "\\n", "\\r", "\\t", "\\v")):
        return True
    if any(f"\\x{codepoint:02x}" in lowered or f"\\u00{codepoint:02x}" in lowered for codepoint in control_codepoints):
        return True
    for char in lowered:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _is_valid_checksum(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == MAX_MODEL_CHECKSUM_CHARS
        and all(char in "0123456789abcdef" for char in value.lower())
    )


def _is_valid_cache_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    checksum = entry.get("checksum")
    size = entry.get("size")
    mtime_ns = entry.get("mtime_ns")
    return (
        _is_valid_checksum(checksum)
        and isinstance(size, int)
        and not isinstance(size, bool)
        and isinstance(mtime_ns, int)
        and not isinstance(mtime_ns, bool)
        and size >= 0
        and mtime_ns >= 0
    )


def _model_checksum_cache_path() -> Path:
    path = models_dir() / _MODEL_CHECKSUM_CACHE_FILE
    try:
        assert_no_symlink_ancestors(path, field_name="model checksum cache path")
    except RuntimeError as exc:
        raise ModelError(str(exc)) from exc
    return path


def _remove_model_checksum_cache_file(cache_path: Path) -> bool:
    parent_fd: int | None = None
    try:
        assert_no_symlink_ancestors(cache_path, field_name="model checksum cache path")
        parent_fd = ensure_directory_without_following_symlinks(
            cache_path.parent,
            field_name="model checksum cache directory",
        )
        os.unlink(cache_path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError):
        return False
    finally:
        if parent_fd is not None:
            with suppress(OSError):
                os.close(parent_fd)


def _load_model_checksum_cache() -> None:
    global _model_checksum_cache_loaded
    if _model_checksum_cache_loaded:
        return
    _model_checksum_cache_loaded = True

    cache_path = _model_checksum_cache_path()
    try:
        cache_stat = cache_path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if (
        not stat_module.S_ISREG(cache_stat.st_mode)
        or getattr(cache_stat, "st_nlink", 1) != 1
    ):
        _remove_model_checksum_cache_file(cache_path)
        return

    try:
        if cache_stat.st_size > MAX_MODEL_CHECKSUM_JSON_BYTES:
            _remove_model_checksum_cache_file(cache_path)
            return
        text = read_text_without_following_symlinks(
            cache_path,
            field_name="model checksum cache path",
            max_bytes=MAX_MODEL_CHECKSUM_JSON_BYTES,
            expected_stat=cache_stat,
        )
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        _remove_model_checksum_cache_file(cache_path)
        return
    if _contains_escaped_null(text):
        _remove_model_checksum_cache_file(cache_path)
        return
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        _remove_model_checksum_cache_file(cache_path)
        return

    if not isinstance(payload, dict):
        _remove_model_checksum_cache_file(cache_path)
        return

    for key, raw_entry in payload.items():
        try:
            key_byte_length = _safe_utf8_length(key, field_name="model checksum cache path key")
        except ModelError:
            continue
        if (
            not isinstance(key, str)
            or len(key) > MAX_MODEL_CHECKSUM_PATH_CHARS
            or key_byte_length > MAX_MODEL_CHECKSUM_PATH_CHARS
            or _contains_escaped_null(key)
            or _contains_http_header_control_chars(key)
            or not _is_valid_cache_entry(raw_entry)
        ):
            continue
        _model_checksum_cache[key] = {
            "checksum": raw_entry["checksum"],
            "size": raw_entry["size"],
            "mtime_ns": raw_entry["mtime_ns"],
        }

    _prune_model_checksum_cache()


def _prune_model_checksum_cache() -> None:
    removed = False
    for key, cached in list(_model_checksum_cache.items()):
        if not isinstance(key, str) or not isinstance(cached, dict):
            _model_checksum_cache.pop(key, None)
            removed = True
            continue
        if not _is_valid_cache_entry(cached):
            _model_checksum_cache.pop(key, None)
            removed = True
            continue
        try:
            path = Path(key)
        except (TypeError, ValueError):
            _model_checksum_cache.pop(key, None)
            removed = True
            continue
        try:
            if path.is_symlink() or not path.exists() or not path.is_file():
                _model_checksum_cache.pop(key, None)
                removed = True
                continue
        except OSError:
            _model_checksum_cache.pop(key, None)
            removed = True
            continue
    if removed:
        _write_model_checksum_cache()


def _write_model_checksum_cache() -> None:
    cache_path = _model_checksum_cache_path()
    try:
        rendered = json.dumps(_model_checksum_cache, indent=2, sort_keys=True) + "\n"
        if _safe_utf8_length(rendered, field_name="model checksum cache JSON") > MAX_MODEL_CHECKSUM_JSON_BYTES:
            _model_checksum_cache.clear()
            _remove_model_checksum_cache_file(cache_path)
            return
        write_text_atomically_without_following_symlinks(
            cache_path,
            rendered,
            field_name="model checksum cache path",
        )
    except (OSError, RuntimeError):
        return


def _set_model_checksum_cache(path: Path, checksum: str, stat: os.stat_result) -> None:
    key = str(path)
    if (
        not isinstance(path, Path)
        or not _is_valid_checksum(checksum)
        or not isinstance(stat, os.stat_result)
        or not isinstance(stat.st_size, int)
        or isinstance(stat.st_size, bool)
        or not isinstance(stat.st_mtime_ns, int)
        or isinstance(stat.st_mtime_ns, bool)
        or stat.st_size < 0
        or stat.st_mtime_ns < 0
        or not isinstance(key, str)
        or len(key) > MAX_MODEL_CHECKSUM_PATH_CHARS
        or _safe_utf8_length(key, field_name="model checksum cache path") > MAX_MODEL_CHECKSUM_PATH_CHARS
        or _contains_escaped_null(key)
        or _contains_http_header_control_chars(key)
    ):
        raise ModelError(f"invalid model checksum cache state for {path!r}")
    _model_checksum_cache[str(path)] = {
        "checksum": checksum,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    _write_model_checksum_cache()


def _clear_model_checksum_cache(path: Path) -> None:
    if _model_checksum_cache.pop(str(path), None) is not None:
        _write_model_checksum_cache()


def _open_model_hash_file(path: Path) -> int:
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name="model file")
        assert_fd_is_regular_private_file(fd, field_name="model file")
    except OSError as exc:
        raise ModelError(str(exc)) from exc
    except RuntimeError as exc:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        raise ModelError(str(exc)) from exc
    return fd


def _same_model_hash_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_mode,
        getattr(first, "st_nlink", 1),
        first.st_size,
        first.st_mtime_ns,
        first.st_ctime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_mode,
        getattr(second, "st_nlink", 1),
        second.st_size,
        second.st_mtime_ns,
        second.st_ctime_ns,
    )


def _same_model_artifact_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_mode,
        getattr(first, "st_nlink", 1),
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_mode,
        getattr(second, "st_nlink", 1),
        second.st_size,
        second.st_mtime_ns,
    )


def _same_model_temporary_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        stat_module.S_ISREG(first.st_mode)
        and stat_module.S_ISREG(second.st_mode)
        and first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_mode == second.st_mode
        and getattr(first, "st_nlink", 1) == getattr(second, "st_nlink", 1)
    )


def _same_model_directory_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        stat_module.S_ISDIR(first.st_mode)
        and stat_module.S_ISDIR(second.st_mode)
        and first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_mode == second.st_mode
        and getattr(first, "st_nlink", 1) == getattr(second, "st_nlink", 1)
    )


def _hash_model_file(path: Path) -> tuple[str, os.stat_result]:
    fd = _open_model_hash_file(path)
    try:
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            opened_stat = os.fstat(handle.fileno())
            digest = hashlib.sha1(usedforsecurity=False)
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            final_stat = os.fstat(handle.fileno())
            if not _same_model_hash_snapshot(final_stat, opened_stat):
                raise OSError("model file changed during checksum")
            return digest.hexdigest(), final_stat
    except (OSError, ValueError) as exc:
        raise ModelError(str(exc)) from exc
    finally:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)


def _cached_or_computed_sha1(path: Path) -> str:
    _load_model_checksum_cache()
    checksum, info = _hash_model_file(path)
    _set_model_checksum_cache(path, checksum, info)
    return checksum


def _parse_model_size_bytes(value: str) -> int:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError(f"invalid model size for {value!r}: must be text")
    stripped = (value or "").strip().lower().replace(" ", "")
    if not stripped:
        raise ModelError(f"invalid model size for {value!r}: empty value")
    unit_map = {
        "kib": 1024,
        "kb": 1000,
        "mib": 1024 ** 2,
        "mb": 1000 ** 2,
        "gib": 1024 ** 3,
        "gb": 1000 ** 3,
    }
    for suffix, factor in unit_map.items():
        if stripped.endswith(suffix):
            try:
                number = float(stripped[: -len(suffix)])
            except ValueError as exc:
                raise ModelError(f"invalid model size for {value!r}: {exc}") from exc
            if not math.isfinite(number) or number <= 0:
                raise ModelError(f"invalid model size for {value!r}: must be positive")
            try:
                parsed = int(number * factor)
            except (OverflowError, ValueError) as exc:
                raise ModelError(f"invalid model size for {value!r}: {exc}") from exc
            if parsed <= 0:
                raise ModelError(f"invalid model size for {value!r}: must be positive")
            return parsed
    raise ModelError(f"invalid model size for {value!r}: unsupported format")


def _download_size_limit(model: ModelSpec) -> int:
    expected = _parse_model_size_bytes(model.size)
    if expected <= 0:
        raise ModelError(f"invalid model size for {model.name}: {model.size!r}")
    return min(MAX_MODEL_DOWNLOAD_BYTES, expected + max(MODEL_SIZE_SLACK_BYTES, int(expected * 0.2)))


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ModelError(f"invalid content-length header: {value!r}")
    if not isinstance(value, str):
        raise ModelError(f"invalid content-length header: {value!r}")
    try:
        parsed = int(value)
    except (ValueError, TypeError):
        raise ModelError(f"invalid content-length header: {value!r}")
    if parsed <= 0:
        raise ModelError(f"invalid content-length header: {value!r}")
    return parsed


def _read_content_length(response: Any) -> int | None:
    value: str | None

    headers = getattr(response, "headers", None)
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            value = getter("Content-Length")
            parsed = _parse_content_length(value)
            if parsed is not None:
                return parsed

    info = getattr(response, "info", None)
    if callable(info):
        headers = info()
        if headers is not None:
            getter = getattr(headers, "get", None)
            if callable(getter):
                value = getter("Content-Length")
                parsed = _parse_content_length(value)
                if parsed is not None:
                    return parsed

    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        value = getheader("Content-Length")
        parsed = _parse_content_length(value)
        if parsed is not None:
            return parsed

    return None


def _assert_download_url(
    url: str,
    *,
    field_name: str = "model download URL",
    allowed_hosts: set[str] | None = None,
    allowed_urls: set[str] | None = None,
) -> str:
    if not isinstance(url, str) or isinstance(url, bool):
        raise ModelError(f"{field_name} must be text")
    raw = url or ""
    if _contains_escaped_null(raw):
        raise ModelError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(raw):
        raise ModelError(f"{field_name} contains invalid control character")
    normalized = raw.strip()
    if not normalized:
        raise ModelError(f"{field_name} is required")
    if len(normalized) > MAX_MODEL_DOWNLOAD_URL_CHARS:
        raise ModelError(f"{field_name} is too large (max {MAX_MODEL_DOWNLOAD_URL_CHARS} characters)")
    if _safe_utf8_length(normalized, field_name=f"{field_name} URL") > MAX_MODEL_DOWNLOAD_URL_CHARS:
        raise ModelError(f"{field_name} is too large (max {MAX_MODEL_DOWNLOAD_URL_CHARS} bytes)")
    try:
        parsed = urllib.parse.urlparse(normalized)
    except ValueError as exc:
        raise ModelError(f"{field_name} is invalid") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ModelError(f"{field_name} must use http:// or https://")
    if not parsed.netloc:
        raise ModelError(f"{field_name} is missing network location")
    if not parsed.hostname:
        raise ModelError(f"{field_name} is missing hostname")
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise ModelError(f"{field_name} must use https:// unless host is local loopback")
    try:
        parsed.port
    except ValueError as exc:
        raise ModelError(f"{field_name} has invalid port") from exc
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise ModelError(f"{field_name} must not contain userinfo")
    if parsed.fragment:
        raise ModelError(f"{field_name} must not contain fragment")
    hostname = (parsed.hostname or "").lower()
    if allowed_urls is not None and normalized not in allowed_urls:
        raise ModelError(f"{field_name} is not allowed")
    if allowed_hosts is not None and hostname not in allowed_hosts:
        raise ModelError(f"{field_name} host is not allowed")
    return normalized


def _url_matches_allowed_base(url: str, allowed_url: str) -> bool:
    if not isinstance(url, str) or not isinstance(allowed_url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        allowed = urllib.parse.urlsplit(allowed_url)
    except ValueError:
        return False
    if parsed.scheme != allowed.scheme:
        return False
    if (parsed.hostname or "").lower() != (allowed.hostname or "").lower():
        return False
    try:
        parsed_port = parsed.port
        allowed_port = allowed.port
    except ValueError:
        return False
    if parsed_port is None:
        parsed_port = 80 if parsed.scheme == "http" else 443 if parsed.scheme == "https" else None
    if allowed_port is None:
        allowed_port = 80 if allowed.scheme == "http" else 443 if allowed.scheme == "https" else None
    if parsed_port != allowed_port:
        return False
    return parsed.path == allowed.path


def _url_matches_exact_allowed_url(url: str, allowed_url: str) -> bool:
    if not _url_matches_allowed_base(url, allowed_url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        allowed = urllib.parse.urlsplit(allowed_url)
    except ValueError:
        return False
    parsed_query = tuple(sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)))
    allowed_query = tuple(sorted(urllib.parse.parse_qsl(allowed.query, keep_blank_values=True)))
    return parsed_query == allowed_query and parsed.fragment == allowed.fragment


def _huggingface_resolve_parts(url: str) -> tuple[str, str] | None:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if (parsed.hostname or "").lower() != HUGGING_FACE_DOWNLOAD_HOST:
        return None
    path = urllib.parse.unquote(parsed.path).lstrip("/")
    marker = "/resolve/main/"
    if marker not in path:
        return None
    repo, filename = path.split(marker, 1)
    if not repo or not filename:
        return None
    return repo, filename


def _huggingface_resolve_cache_redirect_matches(url: str, allowed_url: str) -> bool:
    allowed_parts = _huggingface_resolve_parts(allowed_url)
    if allowed_parts is None:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if (parsed.hostname or "").lower() != HUGGING_FACE_DOWNLOAD_HOST:
        return False
    repo, filename = allowed_parts
    redirect_parts = [part for part in urllib.parse.unquote(parsed.path).split("/") if part]
    repo_parts = [part for part in repo.split("/") if part]
    filename_parts = [part for part in filename.split("/") if part]
    prefix_length = 3 + len(repo_parts)
    if len(redirect_parts) != prefix_length + 1 + len(filename_parts):
        return False
    if redirect_parts[:3] != ["api", "resolve-cache", "models"]:
        return False
    if redirect_parts[3:prefix_length] != repo_parts:
        return False
    return redirect_parts[prefix_length + 1 :] == filename_parts


def _huggingface_storage_redirect_matches(url: str, allowed_url: str) -> bool:
    allowed_parts = _huggingface_resolve_parts(allowed_url)
    if allowed_parts is None:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if (parsed.hostname or "").lower() not in HUGGING_FACE_STORAGE_REDIRECT_HOSTS:
        return False
    if "%2f" in parsed.path.lower() or "%5c" in parsed.path.lower():
        return False
    _repo, filename = allowed_parts
    leaf = filename.rsplit("/", 1)[-1]
    redirect_path = urllib.parse.unquote(parsed.path).rstrip("/")
    if not redirect_path.endswith(f"/{leaf}"):
        return False
    disposition_values = urllib.parse.parse_qs(parsed.query).get("response-content-disposition", [])
    if len(disposition_values) != 1:
        return False
    disposition_value = urllib.parse.unquote(disposition_values[0]).strip()
    if not disposition_value:
        return False
    parts = [part.strip() for part in disposition_value.split(";") if part.strip()]
    if len(parts) < 2:
        return False
    filename_seen = False
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        key = key.strip().lower()
        value = raw_value.strip().strip('"')
        if key == "filename":
            filename_seen = True
            if value != leaf:
                return False
        elif key == "filename*":
            filename_seen = True
            if value.lower().startswith("utf-8''"):
                value = urllib.parse.unquote(value[7:])
            if value != leaf:
                return False
    return filename_seen


def _download_redirect_matches_allowed_url(url: str, allowed_url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        allowed = urllib.parse.urlsplit(allowed_url)
    except ValueError:
        return False
    if (
        parsed.username is not None
        or parsed.password is not None
        or allowed.username is not None
        or allowed.password is not None
    ):
        return False
    if allowed.query or allowed.fragment:
        return _url_matches_exact_allowed_url(url, allowed_url)
    if parsed.fragment:
        return False
    return (
        _url_matches_allowed_base(url, allowed_url)
        or _huggingface_resolve_cache_redirect_matches(url, allowed_url)
        or _huggingface_storage_redirect_matches(url, allowed_url)
    )


def _model_download_redirect_target(exc: urllib.error.HTTPError, base_url: str) -> str | None:
    if exc.code not in MODEL_DOWNLOAD_REDIRECT_CODES:
        return None
    headers = getattr(exc, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        getter = getattr(getattr(exc, "hdrs", None), "get", None)
    if not callable(getter):
        return None
    location = getter("Location")
    if not isinstance(location, str) or not location.strip():
        return None
    return urllib.parse.urljoin(base_url, location.strip())


def _open_model_download_url(url: str, *, timeout: int = 30) -> object:
    return _MODEL_DOWNLOAD_OPENER.open(url, timeout=timeout)


def _open_model_download_response(
    url: str,
    *,
    allowed_hosts: set[str],
    redirect_allowed_hosts: set[str],
    allowed_urls: set[str] | None,
) -> object:
    current_url = url
    for _ in range(MAX_MODEL_DOWNLOAD_REDIRECTS + 1):
        try:
            return _open_model_download_url(current_url, timeout=30)
        except urllib.error.HTTPError as exc:
            primary_error: BaseException | None = None
            try:
                redirect_url = _model_download_redirect_target(exc, current_url)
                if redirect_url is None:
                    raise ModelError(f"model download failed with HTTP status {exc.code}") from exc
                redirect_url = _assert_download_url(
                    redirect_url,
                    field_name="model download redirect URL",
                    allowed_hosts=redirect_allowed_hosts,
                )
                if allowed_urls is not None and not any(
                    _download_redirect_matches_allowed_url(redirect_url, allowed_url) for allowed_url in allowed_urls
                ):
                    raise ModelError("model download redirect URL is not allowed") from exc
                current_url = redirect_url
            except BaseException as error:
                primary_error = error
                raise
            finally:
                try:
                    exc.close()
                except OSError:
                    pass
                except BaseException as cleanup_error:
                    if primary_error is not None:
                        _note_cleanup_failure(primary_error, cleanup_error)
                    else:
                        raise
    raise ModelError("model download has too many redirects")


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    filename: str
    size: str
    sha1: str
    description: str
    languages: tuple[str, ...] = ()
    download_url: str = ""
    backend: str = "whisper-cpp"
    model_format: str = "ggml"
    repo_id: str = ""
    files: tuple[str, ...] = ()
    file_sha1s: tuple[tuple[str, str], ...] = ()

    @property
    def url(self) -> str:
        if self.download_url:
            return self.download_url
        if self.repo_id and self.files:
            repo_id = _validated_huggingface_repo_id(self.repo_id)
            return f"https://huggingface.co/{repo_id}"
        if self.repo_id and not self.files:
            repo_id = _validated_huggingface_repo_id(self.repo_id)
            return HUGGING_FACE_RESOLVE_URL.format(repo=repo_id, filename=self.filename)
        return f"{HUGGING_FACE_BASE_URL}/{self.filename}"


CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="tiny.en",
        filename="ggml-tiny.en.bin",
        size="75 MiB",
        sha1="c78c86eb1a8faa21b369bcd33207cc90d64ae9df",
        description="Fast English-only starter model",
        languages=("en",),
    ),
    ModelSpec(
        name="tiny",
        filename="ggml-tiny.bin",
        size="75 MiB",
        sha1="bd577a113a864445d4c299885e0cb97d4ba92b5f",
        description="Fast multilingual starter model",
    ),
    ModelSpec(
        name="tiny-de",
        filename="ggml-tiny-de.bin",
        size="74 MiB",
        sha1="d69d0a00ed0ab978e22faf86c73960cb6ed21b25",
        description="Fast German-only starter model",
        languages=("de",),
        download_url=TINY_DE_MODEL_URL,
    ),
    ModelSpec(
        name="base.en",
        filename="ggml-base.en.bin",
        size="142 MiB",
        sha1="137c40403d78fd54d454da0f9bd998f78703390c",
        description="Better English accuracy, still light",
        languages=("en",),
    ),
    ModelSpec(
        name="base",
        filename="ggml-base.bin",
        size="142 MiB",
        sha1="465707469ff3a37a2b9b8d8f89f2f99de7299dac",
        description="Better multilingual accuracy, still light",
    ),
    ModelSpec(
        name="ct2-base-int8",
        filename="base-int8",
        size="76 MiB",
        sha1="",
        description="CTranslate2 multilingual base int8 starter model",
        backend="faster-whisper",
        model_format="ctranslate2",
        repo_id="rhasspy/faster-whisper-base-int8",
        files=("config.json", "model.bin", "vocabulary.txt"),
    ),
    ModelSpec(
        name="ct2-base",
        filename="base",
        size="141 MiB",
        sha1="",
        description="CTranslate2 multilingual base model",
        backend="faster-whisper",
        model_format="ctranslate2",
        repo_id="Systran/faster-whisper-base",
        files=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
    ),
    ModelSpec(
        name="ct2-tiny-de",
        filename="tiny-de",
        size="75 MiB",
        sha1="",
        description="CTranslate2 German tiny model",
        languages=("de",),
        backend="faster-whisper",
        model_format="ctranslate2",
        repo_id="pbechhaus/whisper-tiny-german-ct2",
        files=("config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"),
    ),
    ModelSpec(
        name="ct2-small-de",
        filename="small-de",
        size="462 MiB",
        sha1="",
        description="CTranslate2 German small model",
        languages=("de",),
        backend="faster-whisper",
        model_format="ctranslate2",
        repo_id="mkenfenheuer/whisper-small-cv11-german-ct2",
        files=("config.json", "model.bin", "tokenizer_config.json", "vocabulary.json"),
    ),
    ModelSpec(
        name="ct2-small",
        filename="small",
        size="464 MiB",
        sha1="",
        description="CTranslate2 multilingual small model",
        backend="faster-whisper",
        model_format="ctranslate2",
        repo_id="Systran/faster-whisper-small",
        files=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
    ),
    ModelSpec(
        name="small.en",
        filename="ggml-small.en.bin",
        size="466 MiB",
        sha1="db8a495a91d927739e50b3fc1cc4c6b8f6c2d022",
        description="Higher English accuracy, slower",
        languages=("en",),
    ),
    ModelSpec(
        name="small",
        filename="ggml-small.bin",
        size="466 MiB",
        sha1="55356645c2b361a969dfd0ef2c5a50d530afd8d5",
        description="Higher multilingual accuracy, slower",
    ),
    ModelSpec(
        name="large-v3-turbo-q5_0",
        filename="ggml-large-v3-turbo-q5_0.bin",
        size="547 MiB",
        sha1="e050f7970618a659205450ad97eb95a18d69c9ee",
        description="Strong multilingual turbo model, quantized",
    ),
)

_catalog_index_source: tuple[ModelSpec, ...] | None = None
_catalog_name_index: dict[str, ModelSpec] = {}
_catalog_lower_name_index: dict[str, ModelSpec] = {}
_catalog_filename_index: dict[str, ModelSpec] = {}


def _catalog_indexes() -> tuple[dict[str, ModelSpec], dict[str, ModelSpec], dict[str, ModelSpec]]:
    global _catalog_index_source, _catalog_name_index, _catalog_lower_name_index, _catalog_filename_index
    if _catalog_index_source is not CATALOG:
        _catalog_name_index = {model.name: model for model in CATALOG}
        _catalog_lower_name_index = {model.name.lower(): model for model in CATALOG}
        _catalog_filename_index = {model.filename.lower(): model for model in CATALOG}
        _catalog_index_source = CATALOG
    return _catalog_name_index, _catalog_lower_name_index, _catalog_filename_index


def catalog_by_name() -> dict[str, ModelSpec]:
    names, _lower_names, _filenames = _catalog_indexes()
    return dict(names)


def resolve_model(name: str) -> ModelSpec:
    if isinstance(name, bool) or not isinstance(name, str):
        raise ModelError("model name must be text")
    key = (name or "").strip()
    models, _lower_names, _filenames = _catalog_indexes()
    if key in models:
        return models[key]
    raise ModelError(f"unknown model: {name}")


def _validated_catalog_path_fragment(value: str, *, field_name: str) -> Path:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError(f"{field_name} must be text")
    raw = value or ""
    if _contains_escaped_null(raw):
        raise ModelError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(raw):
        raise ModelError(f"{field_name} contains invalid control character")
    normalized = raw.strip()
    if not normalized:
        raise ModelError(f"{field_name} is required")
    path = Path(normalized)
    if path == Path(".") or path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ModelError(f"{field_name} must be a relative path without parent traversal")
    return path


def _validated_huggingface_repo_id(value: str, *, field_name: str = "model repo_id") -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError(f"{field_name} must be text")
    raw = value or ""
    if _contains_escaped_null(raw):
        raise ModelError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(raw):
        raise ModelError(f"{field_name} contains invalid control character")
    normalized = raw.strip()
    if normalized != raw:
        raise ModelError(f"{field_name} must not contain leading or trailing whitespace")
    if not normalized:
        raise ModelError("missing repo_id")
    parts = normalized.split("/")
    if len(parts) != 2 or not all(parts):
        raise ModelError(f"{field_name} must be in namespace/name form")
    allowed_chars = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    for part in parts:
        if part in {".", ".."} or any(ch not in allowed_chars for ch in part):
            raise ModelError(f"{field_name} contains invalid character")
    return normalized


def model_path(model: ModelSpec) -> Path:
    filename = _validated_catalog_path_fragment(model.filename, field_name="model filename")
    if model.model_format == "ctranslate2":
        path = ctranslate2_models_dir() / filename
    else:
        path = models_dir() / filename
    try:
        assert_no_symlink_ancestors(path, field_name="model path")
    except RuntimeError as exc:
        raise ModelError(str(exc)) from exc
    return path


def _model_root(model: ModelSpec) -> Path:
    return ctranslate2_models_dir() if model.model_format == "ctranslate2" else models_dir()


def _assert_path_within_model_root(path: Path, root: Path, *, field_name: str = "model path") -> None:
    if not isinstance(path, Path):
        raise ModelError(f"{field_name} must be a path")
    if not isinstance(root, Path):
        raise ModelError("model root must be a path")
    if not path.is_relative_to(root):
        raise ModelError(f"{field_name} is outside the model directory: {path}")


def _assert_model_path_for_atomic_replace(path: Path, root: Path, *, field_name: str = "model path") -> None:
    if path.is_symlink():
        raise ModelError(f"{field_name} must not be a symlink: {path}")
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
    except RuntimeError as exc:
        raise ModelError(str(exc)) from exc
    _assert_path_within_model_root(path, root, field_name=field_name)
    if not path.parent.exists() or not path.parent.is_dir():
        raise ModelError(f"{field_name} parent is not a directory: {path.parent}")


def _assert_model_parent_for_atomic_replace(path: Path, root: Path, *, field_name: str = "model path") -> None:
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
    except RuntimeError as exc:
        raise ModelError(str(exc)) from exc
    _assert_path_within_model_root(path, root, field_name=field_name)
    if path.parent.exists() and not path.parent.is_dir():
        raise ModelError(f"{field_name} parent is not a directory: {path.parent}")


def _ensure_model_parent_directory(path: Path, root: Path, *, field_name: str = "model path") -> None:
    parent_fd = _open_model_parent_directory(path, root, field_name=field_name)
    try:
        os.close(parent_fd)
    except OSError:
        pass


def _open_model_parent_directory(path: Path, root: Path, *, field_name: str = "model path") -> int:
    _assert_model_parent_for_atomic_replace(path, root, field_name=field_name)
    try:
        parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name=f"{field_name} parent")
    except (OSError, RuntimeError) as exc:
        raise ModelError(f"{field_name} parent is not safe: {path.parent}") from exc
    try:
        _assert_model_parent_for_atomic_replace(path, root, field_name=field_name)
    except (ModelError, OSError, RuntimeError) as exc:
        error = ModelError(f"{field_name} parent is not safe: {path.parent}")
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(error, cleanup_error)
        raise error from exc
    return parent_fd


def _replace_model_sibling_path(source: Path, target: Path, root: Path, *, field_name: str = "model path") -> None:
    if source.parent != target.parent:
        raise ModelError(f"{field_name} source and target must share a parent directory")
    parent_fd = _open_model_parent_directory(target, root, field_name=field_name)
    try:
        _assert_model_path_for_atomic_replace(source, root, field_name=f"{field_name} source")
        _assert_model_path_for_atomic_replace(target, root, field_name=field_name)
        source_path_stat = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
        if not (stat_module.S_ISREG(source_path_stat.st_mode) or stat_module.S_ISDIR(source_path_stat.st_mode)):
            raise ModelError(f"{field_name} source must be a regular file or directory: {source}")
        source_fd = os.open(
            source.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        source_primary_error: BaseException | None = None
        try:
            source_stat = os.fstat(source_fd)
            if not (stat_module.S_ISREG(source_stat.st_mode) or stat_module.S_ISDIR(source_stat.st_mode)):
                raise ModelError(f"{field_name} source must be a regular file or directory: {source}")
            if not _same_model_artifact_identity(source_stat, source_path_stat):
                raise ModelError(f"{field_name} source changed before activation: {source}")
            os.fsync(source_fd)
        except BaseException as exc:
            source_primary_error = exc
            raise
        finally:
            try:
                os.close(source_fd)
            except OSError as cleanup_error:
                if source_primary_error is not None:
                    _note_cleanup_failure(source_primary_error, cleanup_error)
                else:
                    pass
        current_source_stat = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_model_artifact_identity(current_source_stat, source_stat):
            raise ModelError(f"{field_name} source changed before activation: {source}")
        _rename_without_replacing(
            source.name,
            target.name,
            directory_fd=parent_fd,
            field_name=field_name,
        )
        os.fsync(parent_fd)
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _unlink_model_file_leaf(
    path: Path,
    root: Path,
    *,
    field_name: str = "model file",
    expected_stat: os.stat_result | None = None,
) -> bool:
    _assert_path_within_model_root(path, root, field_name=field_name)
    try:
        assert_no_symlink_ancestors(path.parent, field_name=f"{field_name} parent")
    except RuntimeError as exc:
        raise ModelError(str(exc)) from exc
    if not path.parent.exists():
        return False
    if not path.parent.is_dir():
        raise ModelError(f"{field_name} parent is not a directory: {path.parent}")
    try:
        parent_fd = open_directory_without_following_symlinks(path.parent, field_name=f"{field_name} parent")
    except OSError as exc:
        raise ModelError(f"{field_name} parent is not safe: {path.parent}") from exc
    try:
        primary_error: BaseException | None = None
        try:
            file_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if stat_module.S_ISLNK(file_stat.st_mode):
            raise ModelError(f"{field_name} must not be a symlink: {path}")
        if not stat_module.S_ISREG(file_stat.st_mode):
            raise ModelError(f"{field_name} must be a regular file: {path}")
        if expected_stat is not None and not _same_model_artifact_identity(file_stat, expected_stat):
            raise ModelError(f"{field_name} changed before cleanup: {path}")
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                pass


def _unlink_model_file_if_same(
    path: Path,
    root: Path,
    expected_stat: os.stat_result,
    *,
    field_name: str,
) -> bool:
    _assert_path_within_model_root(path, root, field_name=field_name)
    parent_fd = _open_model_parent_directory(path, root, field_name=field_name)
    primary_error: BaseException | None = None
    try:
        try:
            current_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat_module.S_ISREG(current_stat.st_mode) or not _same_model_artifact_identity(current_stat, expected_stat):
            raise ModelError(f"{field_name} changed before cleanup: {path}")
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                pass


def _remove_model_directory_leaf(
    path: Path,
    root: Path,
    *,
    field_name: str = "model directory",
    expected_stat: os.stat_result | None = None,
) -> bool:
    _assert_path_within_model_root(path, root, field_name=field_name)
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise ModelError("secure recursive model directory removal is not supported on this platform")
    try:
        assert_no_symlink_ancestors(path.parent, field_name=f"{field_name} parent")
    except RuntimeError as exc:
        raise ModelError(str(exc)) from exc
    if not path.parent.exists():
        return False
    if not path.parent.is_dir():
        raise ModelError(f"{field_name} parent is not a directory: {path.parent}")
    try:
        parent_fd = open_directory_without_following_symlinks(path.parent, field_name=f"{field_name} parent")
    except OSError as exc:
        raise ModelError(f"{field_name} parent is not safe: {path.parent}") from exc
    try:
        primary_error: BaseException | None = None
        try:
            file_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if stat_module.S_ISLNK(file_stat.st_mode):
            raise ModelError(f"{field_name} must not be a symlink: {path}")
        if not stat_module.S_ISDIR(file_stat.st_mode):
            raise ModelError(f"{field_name} must be a directory: {path}")
        if expected_stat is not None and not _same_model_directory_identity(file_stat, expected_stat):
            raise ModelError(f"{field_name} changed before cleanup: {path}")
        shutil.rmtree(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except OSError as exc:
        raise ModelError(f"failed to remove {field_name}: {path}") from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                pass


def _remove_model_directory_if_same(
    path: Path,
    root: Path,
    expected_stat: os.stat_result,
    *,
    field_name: str,
) -> bool:
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise ModelError("secure recursive model directory removal is not supported on this platform")
    _assert_path_within_model_root(path, root, field_name=field_name)
    parent_fd = _open_model_parent_directory(path, root, field_name=field_name)
    primary_error: BaseException | None = None
    try:
        try:
            current_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat_module.S_ISDIR(current_stat.st_mode) or not _same_model_directory_identity(current_stat, expected_stat):
            raise ModelError(f"{field_name} changed before cleanup: {path}")
        shutil.rmtree(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                pass


def model_download_urls(model: ModelSpec) -> list[tuple[str, str]]:
    filename = _validated_catalog_path_fragment(model.filename, field_name="model filename")
    if model.files:
        repo_id = _validated_huggingface_repo_id(model.repo_id)
        urls: list[tuple[str, str]] = []
        for raw_filename in model.files:
            normalized_filename = _validated_catalog_path_fragment(raw_filename, field_name="model file path")
            url_filename = urllib.parse.quote(normalized_filename.as_posix(), safe="/")
            urls.append(
                (
                    str(normalized_filename),
                    HUGGING_FACE_RESOLVE_URL.format(repo=repo_id, filename=url_filename),
                )
            )
        return urls
    return [(str(filename), model.url)]


def is_english_language(language: str) -> bool:
    if not isinstance(language, str):
        return False
    if _contains_escaped_null(language) or _contains_http_header_control_chars(language):
        return False
    normalized = (language or "").strip().lower().replace("_", "-")
    return normalized in ENGLISH_LANGUAGE_CODES or normalized.startswith("en-")


def _language_matches(language: str, allowed: str) -> bool:
    if (
        _contains_escaped_null(language)
        or _contains_http_header_control_chars(language)
        or _contains_escaped_null(allowed)
        or _contains_http_header_control_chars(allowed)
    ):
        return False
    normalized = (language or "").strip().lower().replace("_", "-")
    allowed_normalized = (allowed or "").strip().lower().replace("_", "-")
    if not allowed_normalized:
        return True
    if allowed_normalized == "en":
        return normalized in ENGLISH_LANGUAGE_CODES or normalized.startswith("en-")
    return normalized == allowed_normalized or normalized.startswith(f"{allowed_normalized}-")


def model_name_is_english_only(name: str) -> bool:
    if not isinstance(name, str) or _contains_escaped_null(name) or _contains_http_header_control_chars(name):
        return False
    return (name or "").strip().lower().endswith(".en")


def model_path_is_english_only(path: str | Path) -> bool:
    if not isinstance(path, (str, Path)):
        return False
    filename = Path(path).name.lower()
    _models, lower_names, filenames = _catalog_indexes()
    model = filenames.get(filename) or lower_names.get(filename)
    if model is not None:
        return model_name_is_english_only(model.name)
    return ".en." in filename or filename.endswith(".en.bin")


def _catalog_model_for_path(path: str | Path) -> ModelSpec | None:
    try:
        normalized = Path(path)
    except (TypeError, ValueError):
        return None
    filename = normalized.name.lower()
    _models, lower_names, filenames = _catalog_indexes()
    return filenames.get(filename) or lower_names.get(filename)


def model_backend_for_path(path: str | Path) -> str:
    model = _catalog_model_for_path(path)
    return model.backend if model is not None else ""


def model_supports_language(path: str | Path, language: str) -> bool:
    if not isinstance(path, (str, Path)) or not isinstance(language, str):
        return False
    if _contains_escaped_null(str(path)):
        return False
    if _contains_http_header_control_chars(str(path)):
        return False
    if _contains_escaped_null(language) or _contains_http_header_control_chars(language):
        return False
    model = _catalog_model_for_path(path)
    if model is not None and model.languages:
        return any(_language_matches(language, allowed) for allowed in model.languages)
    return is_english_language(language) or not model_path_is_english_only(path)


def sha1_file(path: Path) -> str:
    return _cached_or_computed_sha1(path)


def _sha1_file_without_cache(path: Path) -> str:
    checksum, _stat = _hash_model_file(path)
    return checksum


def model_status(model: ModelSpec, verify: bool = False) -> dict[str, object]:
    if not isinstance(verify, bool):
        raise ModelError("verify must be a boolean")
    path = model_path(model)
    exists = path.exists()
    downloaded = _model_is_downloaded(model, path)
    checksum = _sha1_file_without_cache(path) if verify and downloaded and path.is_file() and model.sha1 else ""
    return {
        **asdict(model),
        "url": model.url,
        "urls": dict(model_download_urls(model)),
        "path": str(path),
        "downloadable": _model_is_downloadable(model),
        "downloaded": downloaded,
        "verified": _model_is_verified(model, path, checksum) if verify else False,
        "checksum": checksum,
    }


def list_models() -> list[dict[str, object]]:
    return [model_status(model) for model in CATALOG]


def downloaded_model_paths(language: str = "") -> list[Path]:
    paths: list[Path] = []
    for model in CATALOG:
        path = model_path(model)
        if _model_is_verified(model, path) and model_supports_language(path, language):
            paths.append(path)
    return paths


def default_whisper_cpp_model_path(language: str = "") -> str:
    for model in CATALOG:
        path = model_path(model)
        if model.backend == "whisper-cpp" and _model_is_verified(model, path) and model_supports_language(path, language):
            return str(path)
    return ""


def default_ctranslate2_model_path(language: str = "") -> str:
    for model in CATALOG:
        path = model_path(model)
        if model.backend == "faster-whisper" and _model_is_verified(model, path) and model_supports_language(path, language):
            return str(path)
    return ""


def _model_is_downloaded(model: ModelSpec, path: Path) -> bool:
    try:
        path_stat = path.stat(follow_symlinks=False)
    except OSError:
        return False
    if model.files:
        if not stat_module.S_ISDIR(path_stat.st_mode):
            return False
        for filename in model.files:
            file_path = path / _validated_catalog_path_fragment(filename, field_name="model file path")
            if not _model_file_is_regular(file_path):
                return False
        return True
    return _model_file_is_regular(path)


def _model_file_is_regular(path: Path) -> bool:
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name="model file")
        assert_fd_is_regular_private_file(fd, field_name="model file")
        return True
    except (OSError, RuntimeError):
        return False
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)


def _model_is_verified(model: ModelSpec, path: Path, checksum: str = "") -> bool:
    if not _model_is_downloaded(model, path):
        return False
    if model.files:
        try:
            expected_hashes = _model_file_sha1s(model)
        except ModelError:
            return False
        for filename, expected_checksum in expected_hashes.items():
            try:
                if _sha1_file_without_cache(path / filename) != expected_checksum:
                    return False
            except ModelError:
                return False
        return True
    current_checksum = checksum or _sha1_file_without_cache(path)
    return bool(model.sha1 and current_checksum == model.sha1)


def _model_is_downloadable(model: ModelSpec) -> bool:
    if model.files:
        if not model.repo_id:
            return False
        try:
            _model_file_sha1s(model)
        except ModelError:
            return False
        return True
    return bool(model.sha1)


def _model_file_sha1s(model: ModelSpec) -> dict[str, str]:
    expected_files = {
        str(_validated_catalog_path_fragment(filename, field_name="model file path"))
        for filename in model.files
    }
    expected_hashes = {
        str(_validated_catalog_path_fragment(filename, field_name="model file hash path")): checksum
        for filename, checksum in model.file_sha1s
    }
    if not expected_hashes:
        raise ModelError(f"model catalog entry {model.name} is missing per-file checksums")
    if set(expected_hashes) != expected_files:
        raise ModelError(f"model catalog entry {model.name} has mismatched per-file checksums")
    for checksum in expected_hashes.values():
        if not _is_valid_checksum(checksum):
            raise ModelError(f"model catalog entry {model.name} has invalid per-file checksum")
    return {filename: checksum.lower() for filename, checksum in expected_hashes.items()}


def _download_url_to_file(url: str, tmp_dir: Path, size_limit: int, model_name: str, *, prefix: str) -> tuple[Path, int]:
    return _download_url_to_file_with_fd(url, tmp_dir, None, size_limit, model_name, prefix=prefix)


def _unlink_temporary_download_name(
    parent_fd: int,
    temporary_name: str,
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    if expected_stat is None:
        raise ModelError("temporary model file identity is unavailable")
    try:
        current_stat = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_model_temporary_identity(current_stat, expected_stat):
            raise ModelError("temporary model file changed before cleanup")
        os.unlink(temporary_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as exc:
        raise ModelError("failed to remove temporary model file") from exc


def _unlink_temporary_download_path(path: Path, *, expected_stat: os.stat_result | None = None) -> None:
    parent_fd: int | None = None
    try:
        parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name="model temporary directory")
        _unlink_temporary_download_name(parent_fd, path.name, expected_stat=expected_stat)
    finally:
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except OSError:
                pass


def _create_temporary_file_in_parent_directory(parent_fd: int, *, prefix: str) -> tuple[str, int]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise OSError("secure temporary file creation is not supported for model downloads")
    for _ in range(100):
        temporary_name = f"{prefix}{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag,
                0o600,
                dir_fd=parent_fd,
            )
            return temporary_name, fd
        except FileExistsError:
            continue
    raise OSError("failed to create temporary model file in parent directory")


def _create_temporary_directory_in_parent_directory(parent_fd: int, *, prefix: str) -> str:
    for _ in range(100):
        temporary_name = f"{prefix}{secrets.token_hex(8)}"
        try:
            os.mkdir(temporary_name, 0o700, dir_fd=parent_fd)
            return temporary_name
        except FileExistsError:
            continue
    raise OSError("failed to create temporary model directory in parent directory")


def _download_url_to_file_with_fd(
    url: str,
    tmp_dir: Path,
    tmp_dir_fd: int | None,
    size_limit: int,
    model_name: str,
    *,
    prefix: str,
) -> tuple[Path, int]:
    close_tmp_dir_fd = False
    if tmp_dir_fd is None:
        try:
            assert_no_symlink_ancestors(tmp_dir, field_name="model temporary directory")
            tmp_dir_fd = ensure_directory_without_following_symlinks(tmp_dir, field_name="model temporary directory")
            close_tmp_dir_fd = True
        except (OSError, RuntimeError) as exc:
            raise ModelError(str(exc)) from exc
    allowed_hosts = {HUGGING_FACE_DOWNLOAD_HOST}
    redirect_allowed_hosts = allowed_hosts | HUGGING_FACE_STORAGE_REDIRECT_HOSTS
    allowed_urls = {TINY_DE_MODEL_URL} if model_name == "tiny-de" else {url}
    try:
        url = _assert_download_url(
            url,
            field_name="model download URL",
            allowed_hosts=allowed_hosts,
            allowed_urls=allowed_urls,
        )
    except BaseException as exc:
        if close_tmp_dir_fd and tmp_dir_fd is not None:
            try:
                os.close(tmp_dir_fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            finally:
                tmp_dir_fd = None
        raise
    temporary_name: str | None = None
    tmp_path: Path | None = None
    tmp_fd: int | None = None
    temporary_stat: os.stat_result | None = None
    primary_error: BaseException | None = None
    try:
        temporary_name, tmp_fd = _create_temporary_file_in_parent_directory(tmp_dir_fd, prefix=prefix)
        tmp_path = tmp_dir / temporary_name
        try:
            output = os.fdopen(tmp_fd, "wb")
        except (OSError, ValueError) as exc:
            raise OSError("failed to open temporary model file") from exc
        tmp_fd = None
        with output:
            try:
                temporary_stat = os.fstat(output.fileno())
            except (OSError, ValueError) as exc:
                raise OSError("failed to inspect temporary model file") from exc
            try:
                os.fchmod(output.fileno(), 0o600)
            except OSError:
                pass
            with _open_model_download_response(
                url,
                allowed_hosts=allowed_hosts,
                redirect_allowed_hosts=redirect_allowed_hosts,
                allowed_urls=allowed_urls,
            ) as response:
                geturl = getattr(response, "geturl", None)
                if callable(geturl):
                    final_url = _assert_download_url(
                        geturl(),
                        field_name="model download redirect URL",
                        allowed_hosts=redirect_allowed_hosts,
                    )
                    if allowed_urls is not None and not any(
                        _download_redirect_matches_allowed_url(final_url, allowed_url) for allowed_url in allowed_urls
                    ):
                        raise ModelError("model download redirect URL is not allowed")
                content_length = _read_content_length(response)
                if content_length is not None and content_length > size_limit:
                    raise ModelError(f"downloaded model too large for {model_name}: {content_length} > {size_limit}")

                downloaded = 0
                while True:
                    chunk = response.read(MAX_MODEL_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise ModelError("model download response chunk must be bytes")
                    downloaded += len(chunk)
                    if downloaded > size_limit:
                        raise ModelError(f"downloaded model too large for {model_name}: {downloaded} > {size_limit}")
                    output.write(chunk)
                if content_length is not None and downloaded != content_length:
                    raise ModelError(f"downloaded model size mismatch for {model_name}: {downloaded} != {content_length}")
                output.flush()
                os.fsync(output.fileno())
        return tmp_path, downloaded
    except BaseException as exc:
        primary_error = exc
        try:
            if tmp_dir_fd is not None and temporary_name is not None:
                _unlink_temporary_download_name(tmp_dir_fd, temporary_name, expected_stat=temporary_stat)
            elif tmp_path is not None:
                _unlink_temporary_download_path(tmp_path, expected_stat=temporary_stat)
        except BaseException as cleanup_error:
            _note_cleanup_failure(primary_error, cleanup_error)
        raise
    finally:
        if tmp_fd is not None:
            with suppress(OSError):
                os.close(tmp_fd)
        if close_tmp_dir_fd and tmp_dir_fd is not None:
            try:
                os.close(tmp_dir_fd)
            except OSError as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    pass


def _download_directory_model(model: ModelSpec, path: Path, force: bool) -> dict[str, object]:
    root = _model_root(model)
    _assert_model_parent_for_atomic_replace(path, root, field_name="model path")
    if path.exists() and not force:
        status = model_status(model, verify=True)
        if status["verified"]:
            return {**status, "status": "done", "message": f"model already downloaded: {model.name}"}
    parent_fd = _open_model_parent_directory(path, root, field_name="model path")
    _assert_model_path_for_atomic_replace(path, root, field_name="model path")
    tmp_dir: Path | None = None
    tmp_dir_stat: os.stat_result | None = None
    try:
        try:
            tmp_dir = path.parent / _create_temporary_directory_in_parent_directory(
                parent_fd, prefix=f".{model.filename}."
            )
        except OSError as exc:
            raise ModelError(str(exc)) from exc
        try:
            assert_no_symlink_ancestors(tmp_dir, field_name="model temporary directory")
        except RuntimeError as exc:
            raise ModelError(str(exc)) from exc
        try:
            tmp_dir_stat = tmp_dir.stat(follow_symlinks=False)
        except OSError as exc:
            raise ModelError(f"failed to inspect model temporary directory: {tmp_dir}") from exc
        if not stat_module.S_ISDIR(tmp_dir_stat.st_mode):
            raise ModelError(f"model temporary path is not a directory: {tmp_dir}")

        size_limit = _download_size_limit(model)
        if model.files and not model.repo_id:
            raise ModelError(f"model catalog entry {model.name} is missing repo_id for multi-file download")
        expected_hashes = _model_file_sha1s(model)

        def _assert_safe_model_directory(target: Path) -> None:
            try:
                assert_no_symlink_ancestors(target, field_name="model path")
            except RuntimeError as exc:
                raise ModelError(str(exc)) from exc
            _assert_path_within_model_root(target, root)
            if not target.parent.exists() or not target.parent.is_dir():
                raise ModelError(f"model path parent is not a directory: {target.parent}")
            if target.exists():
                if target.is_symlink():
                    raise ModelError(f"model path must not be a symlink: {target}")
                if not target.is_dir():
                    raise ModelError(f"model path must be a directory: {target}")

        downloaded_total = 0
        for filename, url in model_download_urls(model):
            filename_key = str(_validated_catalog_path_fragment(filename, field_name="model file path"))
            remaining_size_limit = size_limit - downloaded_total
            if remaining_size_limit <= 0:
                raise ModelError(f"downloaded model too large for {model.name}: {downloaded_total} > {size_limit}")
            target = tmp_dir / filename_key
            target_parent_fd = _open_model_parent_directory(target, root, field_name="model file path")
            try:
                _assert_model_path_for_atomic_replace(target, root, field_name="model file path")
                tmp_path, downloaded = _download_url_to_file_with_fd(
                    url,
                    target.parent,
                    target_parent_fd,
                    remaining_size_limit,
                    model.name,
                    prefix=f".{target.name}.",
                )
            finally:
                try:
                    os.close(target_parent_fd)
                except OSError:
                    pass
            downloaded_total += downloaded
            expected_checksum = expected_hashes[filename_key]
            if _sha1_file_without_cache(tmp_path) != expected_checksum:
                raise ModelError(f"downloaded model file checksum mismatch for {model.name}: {filename_key}")
            try:
                _replace_model_sibling_path(tmp_path, target, root, field_name="model file path")
            except (OSError, ModelError) as exc:
                raise ModelError(f"failed to persist downloaded model file: {target}") from exc
        if downloaded_total > size_limit:
            raise ModelError(f"downloaded model too large for {model.name}: {downloaded_total} > {size_limit}")
        if tmp_dir is None:
            raise ModelError(f"model temporary directory is unavailable: {model.name}")
        try:
            current_tmp_dir_stat = tmp_dir.stat(follow_symlinks=False)
        except OSError as exc:
            raise ModelError(f"failed to inspect model temporary directory: {tmp_dir}") from exc
        if tmp_dir_stat is None or not _same_model_directory_identity(current_tmp_dir_stat, tmp_dir_stat):
            raise ModelError(f"model temporary directory changed during download: {tmp_dir}")
        if path.exists() and path.is_dir():
            _assert_safe_model_directory(path)
        backup_dir: Path | None = None
        if path.exists():
            backup_dir = path.with_name(f".{path.name}.{secrets.token_hex(8)}.backup")
            _assert_model_path_for_atomic_replace(path, root, field_name="model path")
            try:
                assert_no_symlink_ancestors(backup_dir, field_name="model backup directory")
            except RuntimeError as exc:
                raise ModelError(str(exc)) from exc
            _assert_path_within_model_root(backup_dir, root)
            if backup_dir.exists() or backup_dir.is_symlink():
                raise ModelError(f"model backup path already exists: {backup_dir}")
            try:
                _replace_model_sibling_path(path, backup_dir, root, field_name="model backup directory")
            except (OSError, ModelError) as exc:
                # The helper can raise after moving the directory when parent fsync fails.
                if not path.exists() and backup_dir.exists():
                    try:
                        _replace_model_sibling_path(backup_dir, path, root, field_name="model path")
                    except (OSError, ModelError) as restore_exc:
                        raise ModelError(
                            f"failed to restore existing model directory after backup failure: {path}"
                        ) from restore_exc
                raise ModelError(f"failed to prepare existing model directory backup: {path}") from exc
            _assert_safe_model_directory(path)
        try:
            _replace_model_sibling_path(tmp_dir, path, root, field_name="model path")
        except (OSError, ModelError) as exc:
            if backup_dir is not None:
                try:
                    if tmp_dir_stat is None:
                        raise ModelError("model temporary directory identity is unavailable")
                    _remove_model_directory_if_same(
                        path,
                        root,
                        tmp_dir_stat,
                        field_name="model restore target",
                    )
                    _replace_model_sibling_path(backup_dir, path, root, field_name="model path")
                except (OSError, ModelError) as restore_exc:
                    raise ModelError(f"failed to restore existing model directory after download failure: {path}") from restore_exc
            elif tmp_dir_stat is not None and not tmp_dir.exists() and path.exists():
                try:
                    _remove_model_directory_if_same(
                        path,
                        root,
                        tmp_dir_stat,
                        field_name="partially installed model directory",
                    )
                except (OSError, ModelError) as cleanup_exc:
                    raise ModelError(f"failed to remove partially installed model directory: {path}") from cleanup_exc
            raise ModelError(f"failed to persist downloaded model directory: {path}") from exc
        if backup_dir is not None:
            try:
                _remove_model_backup_path(backup_dir)
            except (OSError, ModelError) as cleanup_exc:
                orphan_path = backup_dir.with_name(f"{backup_dir.name}.{secrets.token_hex(8)}.orphan")
                try:
                    _replace_model_sibling_path(backup_dir, orphan_path, root, field_name="model backup orphan path")
                    _remove_model_backup_path(orphan_path)
                except (OSError, ModelError):
                    raise ModelError(f"failed to remove model backup after successful download: {backup_dir}") from cleanup_exc
    except BaseException:
        if tmp_dir is not None and tmp_dir_stat is not None:
            with suppress(OSError, ModelError):
                _remove_model_directory_leaf(
                    tmp_dir,
                    root,
                    field_name="model temporary directory",
                    expected_stat=tmp_dir_stat,
                )
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass
    return {**model_status(model, verify=True), "status": "done", "message": f"model downloaded: {model.name}"}


def _restore_model_file_backup(
    path: Path,
    backup_path: Path,
    *,
    expected_target_stat: os.stat_result | None = None,
) -> None:
    root = path.parent
    if expected_target_stat is not None:
        _unlink_model_file_if_same(
            path,
            root,
            expected_target_stat,
            field_name="model restore target",
        )
    elif path.exists() or path.is_symlink():
        raise ModelError(f"model restore target must be absent: {path}")
    _replace_model_sibling_path(backup_path, path, root, field_name="model backup path")


def _remove_model_backup_path(backup_path: Path) -> None:
    if backup_path.is_dir() and not backup_path.is_symlink():
        _remove_model_directory_leaf(backup_path, backup_path.parent, field_name="model backup directory")
        return
    _unlink_model_file_leaf(backup_path, backup_path.parent, field_name="model backup file")


def _is_model_orphan_name(model_name: str, candidate_name: str, *, allow_suffixless: bool = False) -> bool:
    prefix = f".{model_name}."
    if not candidate_name.startswith(prefix):
        return False
    remainder = candidate_name[len(prefix):]
    if allow_suffixless and len(remainder) == 16 and all(char in "0123456789abcdef" for char in remainder):
        return True
    if remainder.endswith(".tmp"):
        token = remainder[:-4]
        return len(token) == 16 and all(char in "0123456789abcdef" for char in token)
    if remainder.endswith(".backup"):
        token = remainder[:-7]
        return len(token) == 16 and all(char in "0123456789abcdef" for char in token)
    if remainder.endswith(".orphan"):
        backup_remainder = remainder[:-7]
        parts = backup_remainder.split(".backup.")
        if len(parts) != 2:
            return False
        return all(len(token) == 16 and all(char in "0123456789abcdef" for char in token) for token in parts)
    return False


def _remove_model_orphan_paths(path: Path, root: Path, *, allow_suffixless: bool = False, preflight: bool = False) -> int:
    _assert_path_within_model_root(path, root, field_name="model orphan path")
    parent = path.parent
    try:
        assert_no_symlink_ancestors(parent, field_name="model orphan parent")
    except RuntimeError as exc:
        raise ModelError(str(exc)) from exc
    if not parent.exists():
        return 0
    parent_fd = open_directory_without_following_symlinks(parent, field_name="model orphan parent")
    removed = 0
    scan_started_at = time.time()
    primary_error: BaseException | None = None
    try:
        try:
            names = os.listdir(parent_fd)
        except OSError as exc:
            raise ModelError(f"failed to scan model orphan parent: {parent}") from exc
        for name in names:
            if not _is_model_orphan_name(path.name, str(name), allow_suffixless=allow_suffixless):
                continue
            candidate = parent / str(name)
            try:
                candidate_stat = os.stat(str(name), dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat_module.S_ISLNK(candidate_stat.st_mode):
                raise ModelError(f"model orphan path must not be a symlink: {candidate}")
            if not stat_module.S_ISDIR(candidate_stat.st_mode) and not stat_module.S_ISREG(candidate_stat.st_mode):
                raise ModelError(f"model orphan path must be a regular file or directory: {candidate}")
            if preflight:
                continue
            if scan_started_at - candidate_stat.st_mtime < MODEL_ORPHAN_CLEANUP_MIN_AGE_SECONDS:
                continue
            if stat_module.S_ISDIR(candidate_stat.st_mode):
                if _remove_model_directory_leaf(
                    candidate,
                    root,
                    field_name="model orphan directory",
                    expected_stat=candidate_stat,
                ):
                    removed += 1
                continue
            if stat_module.S_ISREG(candidate_stat.st_mode):
                if _unlink_model_file_leaf(
                    candidate,
                    root,
                    field_name="model orphan file",
                    expected_stat=candidate_stat,
                ):
                    removed += 1
                continue
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                pass
    return removed


def download_model(name: str, force: bool = False) -> dict[str, object]:
    if not isinstance(force, bool):
        raise ModelError("force must be a boolean")
    model = resolve_model(name)
    if not model.files and not _model_is_downloadable(model):
        raise ModelError(f"model catalog entry {model.name} is not downloadable without pinned checksums")
    path = model_path(model)
    root = _model_root(model)
    if model.files:
        return _download_directory_model(model, path, force)
    _assert_path_within_model_root(path, root)
    _assert_model_parent_for_atomic_replace(path, root, field_name="model path")
    _ensure_model_parent_directory(path, root, field_name="model path")
    _assert_model_path_for_atomic_replace(path, root, field_name="model path")
    if path.exists() and not force:
        status = model_status(model, verify=True)
        if status["verified"]:
            return {**status, "status": "done", "message": f"model already downloaded: {model.name}"}

    size_limit = _download_size_limit(model)
    tmp_path: Path | None = None
    replaced_path = False
    backup_path: Path | None = None
    previous_cache_entry: dict[str, int | str] | None = None
    previous_cache_entry_exists = False
    try:
        parent_fd = _open_model_parent_directory(path, root, field_name="model path")
        try:
            tmp_path, _ = _download_url_to_file_with_fd(
                model.url,
                path.parent,
                parent_fd,
                size_limit,
                model.name,
                prefix=f".{path.name}.",
            )
        finally:
            try:
                os.close(parent_fd)
            except OSError:
                pass
        checksum = sha1_file(tmp_path)
        if checksum != model.sha1:
            raise ModelError(f"downloaded checksum mismatch for {model.name}: {checksum}")
        tmp_stat = tmp_path.stat()
        try:
            _assert_model_path_for_atomic_replace(path, root, field_name="model path")
            if path.exists():
                _load_model_checksum_cache()
                cached_entry = _model_checksum_cache.get(str(path))
                if cached_entry is not None:
                    previous_cache_entry = dict(cached_entry)
                    previous_cache_entry_exists = True
                backup_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.backup")
                _assert_model_path_for_atomic_replace(backup_path, root, field_name="model backup path")
                try:
                    assert_no_symlink_ancestors(backup_path, field_name="model backup path")
                except RuntimeError as exc:
                    raise ModelError(str(exc)) from exc
                _assert_path_within_model_root(backup_path, root)
                if backup_path.exists() or backup_path.is_symlink():
                    raise ModelError(f"model backup path already exists: {backup_path}")
                _replace_model_sibling_path(path, backup_path, root, field_name="model backup path")
            _assert_model_path_for_atomic_replace(path, root, field_name="model path")
            # The helper can raise after activation when the parent fsync fails.
            try:
                _replace_model_sibling_path(tmp_path, path, root, field_name="model path")
            except (OSError, ModelError):
                replaced_path = tmp_path is not None and not tmp_path.exists()
                raise
            else:
                replaced_path = True
            _clear_model_checksum_cache(tmp_path)
            _set_model_checksum_cache(path, checksum, tmp_stat)
        except OSError as exc:
            raise ModelError(f"failed to persist downloaded model file: {path}") from exc
    except BaseException as primary_error:
        if tmp_path is not None:
            try:
                _clear_model_checksum_cache(tmp_path)
            except BaseException as cleanup_error:
                _note_cleanup_failure(primary_error, cleanup_error)
            try:
                _unlink_model_file_leaf(tmp_path, root, field_name="temporary model file")
            except FileNotFoundError:
                pass
            except (OSError, ModelError) as cleanup_error:
                _note_cleanup_failure(primary_error, cleanup_error)
        if replaced_path:
            try:
                _clear_model_checksum_cache(path)
            except BaseException as cleanup_error:
                _note_cleanup_failure(primary_error, cleanup_error)
            if backup_path is not None:
                try:
                    _restore_model_file_backup(
                        path,
                        backup_path,
                        expected_target_stat=tmp_stat,
                    )
                except (OSError, ModelError) as restore_exc:
                    raise ModelError(f"failed to restore existing model file after download failure: {path}") from restore_exc
                if previous_cache_entry_exists and previous_cache_entry is not None:
                    _model_checksum_cache[str(path)] = dict(previous_cache_entry)
                    _write_model_checksum_cache()
            else:
                try:
                    if tmp_stat is None:
                        raise ModelError("temporary model file identity is unavailable")
                    _unlink_model_file_if_same(
                        path,
                        root,
                        tmp_stat,
                        field_name="partially installed model file",
                    )
                except (OSError, ModelError) as cleanup_exc:
                    raise ModelError(f"failed to remove partially installed model file after download failure: {path}") from cleanup_exc
        elif backup_path is not None and not path.exists() and not path.is_symlink():
            try:
                _restore_model_file_backup(path, backup_path)
            except (OSError, ModelError) as restore_exc:
                raise ModelError(f"failed to restore existing model file after download failure: {path}") from restore_exc
            if previous_cache_entry_exists and previous_cache_entry is not None:
                _model_checksum_cache[str(path)] = dict(previous_cache_entry)
                _write_model_checksum_cache()
            else:
                _model_checksum_cache.pop(str(path), None)
        raise
    if backup_path is not None:
        try:
            _remove_model_backup_path(backup_path)
        except (OSError, ModelError) as cleanup_exc:
            raise ModelError(f"failed to remove model backup after successful download: {backup_path}") from cleanup_exc
    return {**model_status(model, verify=True), "status": "done", "message": f"model downloaded: {model.name}"}


def remove_model(name: str) -> dict[str, object]:
    model = resolve_model(name)
    path = model_path(model)
    root = _model_root(model)
    allow_suffixless_orphans = bool(model.files)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    removed = False
    removed_tmp = False
    removed_orphans = 0
    _remove_model_orphan_paths(path, root, allow_suffixless=allow_suffixless_orphans, preflight=True)
    if path.is_symlink():
        raise ModelError(f"model path must not be a symlink: {path}")
    elif path.is_dir():
        removed = _remove_model_directory_leaf(path, root, field_name="model directory")
    else:
        removed = _unlink_model_file_leaf(path, root, field_name="model file")
    try:
        removed_tmp = _unlink_model_file_leaf(tmp_path, root, field_name="temporary model file")
    except (OSError, ModelError) as exc:
        if removed:
            try:
                _clear_model_checksum_cache(path)
            except ModelError:
                pass
        raise ModelError(f"failed to remove temporary model file: {tmp_path}: {exc}") from exc
    if removed:
        _clear_model_checksum_cache(path)
    if removed_tmp:
        _clear_model_checksum_cache(tmp_path)
    removed_orphans = _remove_model_orphan_paths(path, root, allow_suffixless=allow_suffixless_orphans)
    return {
        **asdict(model),
        "status": "done",
        "message": f"model removed: {model.name}" if removed else f"model was not downloaded: {model.name}",
        "path": str(path),
        "removed": removed,
        "removed_tmp": removed_tmp,
        "removed_orphans": removed_orphans,
    }
