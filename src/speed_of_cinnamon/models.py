from __future__ import annotations

import json
import hashlib
import os
import secrets
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import ctranslate2_models_dir, models_dir
from .path_safety import (
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)

HUGGING_FACE_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
TINY_DE_MODEL_URL = "https://huggingface.co/wabisabisocial/whisper-tiny-german-ggml/resolve/main/ggml-tiny-de.bin"
HUGGING_FACE_RESOLVE_URL = "https://huggingface.co/{repo}/resolve/main/{filename}"
MAX_MODEL_DOWNLOAD_BYTES = 1_200_000_000
MAX_MODEL_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


_MODEL_DOWNLOAD_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError("value must be text")
    lowered = (value or "").lower()
    if (
        "\r" in lowered
        or "\n" in lowered
        or "\\r" in lowered
        or "\\n" in lowered
        or "\\u000d" in lowered
        or "\\u000a" in lowered
        or "\\x0a" in lowered
        or "\\x0d" in lowered
    ):
        return True
    for char in lowered:
        if ord(char) < 0x20 or ord(char) == 0x7F:
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


def _load_model_checksum_cache() -> None:
    global _model_checksum_cache_loaded
    if _model_checksum_cache_loaded:
        return
    _model_checksum_cache_loaded = True

    cache_path = _model_checksum_cache_path()
    if not cache_path.exists():
        return

    try:
        if cache_path.stat().st_size > MAX_MODEL_CHECKSUM_JSON_BYTES:
            try:
                cache_path.unlink()
            except OSError:
                pass
            return
        text = read_text_without_following_symlinks(cache_path, field_name="model checksum cache path")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        try:
            cache_path.unlink()
        except OSError:
            pass
        return
    if _contains_escaped_null(text):
        try:
            cache_path.unlink()
        except OSError:
            pass
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            cache_path.unlink()
        except OSError:
            pass
        return

    if not isinstance(payload, dict):
        try:
            cache_path.unlink()
        except OSError:
            pass
        return

    for key, raw_entry in payload.items():
        if (
            not isinstance(key, str)
            or len(key) > MAX_MODEL_CHECKSUM_PATH_CHARS
            or len(key.encode("utf-8")) > MAX_MODEL_CHECKSUM_PATH_CHARS
            or _contains_escaped_null(key)
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
        if len(rendered.encode("utf-8")) > MAX_MODEL_CHECKSUM_JSON_BYTES:
            _model_checksum_cache.clear()
            try:
                cache_path.unlink()
            except OSError:
                pass
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
        or len(key.encode("utf-8")) > MAX_MODEL_CHECKSUM_PATH_CHARS
        or _contains_escaped_null(key)
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


def _cached_or_computed_sha1(path: Path) -> str:
    _load_model_checksum_cache()
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise ModelError("secure model file open is not supported on this platform")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow_flag)
    except OSError as exc:
        raise ModelError(str(exc)) from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            key = str(path)
            cached = _model_checksum_cache.get(key)
            if (
                isinstance(cached, dict)
                and cached.get("size") == info.st_size
                and cached.get("mtime_ns") == info.st_mtime_ns
            ):
                checksum = cached.get("checksum")
                if isinstance(checksum, str):
                    return checksum

            digest = hashlib.sha1(usedforsecurity=False)
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

            checksum = digest.hexdigest()
            _set_model_checksum_cache(path, checksum, info)
            return checksum
    except OSError as exc:
        raise ModelError(str(exc)) from exc


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
            if number <= 0:
                raise ModelError(f"invalid model size for {value!r}: must be positive")
            return int(number * factor)
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
    normalized = (url or "").strip()
    if not normalized:
        raise ModelError(f"{field_name} is required")
    if _contains_escaped_null(normalized):
        raise ModelError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(normalized):
        raise ModelError(f"{field_name} contains invalid control character")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ModelError(f"{field_name} must use http:// or https://")
    if not parsed.netloc:
        raise ModelError(f"{field_name} is missing network location")
    hostname = (parsed.hostname or "").lower()
    if allowed_urls is not None and normalized not in allowed_urls:
        raise ModelError(f"{field_name} is not allowed")
    if allowed_hosts is not None and hostname not in allowed_hosts:
        raise ModelError(f"{field_name} host is not allowed: {parsed.netloc}")
    return normalized


def _url_matches_allowed_base(url: str, allowed_url: str) -> bool:
    if not isinstance(url, str) or not isinstance(allowed_url, str):
        return False
    parsed = urllib.parse.urlsplit(url)
    allowed = urllib.parse.urlsplit(allowed_url)
    return (
        parsed.scheme == allowed.scheme
        and parsed.netloc == allowed.netloc
        and parsed.path == allowed.path
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
    allowed_urls: set[str] | None,
) -> object:
    current_url = url
    for _ in range(MAX_MODEL_DOWNLOAD_REDIRECTS + 1):
        try:
            return _open_model_download_url(current_url, timeout=30)
        except urllib.error.HTTPError as exc:
            redirect_url = _model_download_redirect_target(exc, current_url)
            if redirect_url is None:
                raise ModelError(f"model download failed with HTTP status {exc.code}") from exc
            redirect_url = _assert_download_url(
                redirect_url,
                field_name="model download redirect URL",
                allowed_hosts=allowed_hosts,
            )
            if allowed_urls is not None and not any(
                _url_matches_allowed_base(redirect_url, allowed_url) for allowed_url in allowed_urls
            ):
                raise ModelError("model download redirect URL is not allowed") from exc
            current_url = redirect_url
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

    @property
    def url(self) -> str:
        if self.download_url:
            return self.download_url
        if self.repo_id and self.files:
            return f"https://huggingface.co/{self.repo_id}"
        if self.repo_id and not self.files:
            return HUGGING_FACE_RESOLVE_URL.format(repo=self.repo_id, filename=self.filename)
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
    normalized = (value or "").strip()
    if not normalized:
        raise ModelError(f"{field_name} is required")
    if _contains_escaped_null(normalized):
        raise ModelError(f"{field_name} contains invalid null byte")
    path = Path(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ModelError(f"{field_name} must be a relative path without parent traversal")
    return path


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
    os.close(parent_fd)


def _open_model_parent_directory(path: Path, root: Path, *, field_name: str = "model path") -> int:
    _assert_model_parent_for_atomic_replace(path, root, field_name=field_name)
    try:
        parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name=f"{field_name} parent")
    except (OSError, RuntimeError) as exc:
        raise ModelError(f"{field_name} parent is not safe: {path.parent}") from exc
    try:
        _assert_model_parent_for_atomic_replace(path, root, field_name=field_name)
    except (ModelError, OSError, RuntimeError) as exc:
        os.close(parent_fd)
        raise ModelError(f"{field_name} parent is not safe: {path.parent}") from exc
    return parent_fd


def _replace_model_sibling_path(source: Path, target: Path, root: Path, *, field_name: str = "model path") -> None:
    if source.parent != target.parent:
        raise ModelError(f"{field_name} source and target must share a parent directory")
    parent_fd = _open_model_parent_directory(target, root, field_name=field_name)
    try:
        _assert_model_path_for_atomic_replace(source, root, field_name=f"{field_name} source")
        _assert_model_path_for_atomic_replace(target, root, field_name=field_name)
        os.replace(source.name, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def model_download_urls(model: ModelSpec) -> list[tuple[str, str]]:
    filename = _validated_catalog_path_fragment(model.filename, field_name="model filename")
    if model.files:
        return [
            (
                str(_validated_catalog_path_fragment(filename, field_name="model file path")),
                HUGGING_FACE_RESOLVE_URL.format(repo=model.repo_id, filename=filename),
            )
            for filename in model.files
        ]
    return [(str(filename), model.url)]


def is_english_language(language: str) -> bool:
    if not isinstance(language, str):
        return False
    normalized = (language or "").strip().lower().replace("_", "-")
    return normalized in ENGLISH_LANGUAGE_CODES or normalized.startswith("en-")


def _language_matches(language: str, allowed: str) -> bool:
    normalized = (language or "").strip().lower().replace("_", "-")
    allowed_normalized = (allowed or "").strip().lower().replace("_", "-")
    if not allowed_normalized:
        return True
    if allowed_normalized == "en":
        return normalized in ENGLISH_LANGUAGE_CODES or normalized.startswith("en-")
    return normalized == allowed_normalized or normalized.startswith(f"{allowed_normalized}-")


def model_name_is_english_only(name: str) -> bool:
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
    model = _catalog_model_for_path(path)
    if model is not None and model.languages:
        return any(_language_matches(language, allowed) for allowed in model.languages)
    return is_english_language(language) or not model_path_is_english_only(path)


def sha1_file(path: Path) -> str:
    return _cached_or_computed_sha1(path)


def _sha1_file_without_cache(path: Path) -> str:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise ModelError("secure model file open is not supported on this platform")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow_flag)
    except OSError as exc:
        raise ModelError(str(exc)) from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            digest = hashlib.sha1(usedforsecurity=False)
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError as exc:
        raise ModelError(str(exc)) from exc


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
    if path.is_symlink():
        return False
    if model.files:
        return path.is_dir() and all(
            (path / _validated_catalog_path_fragment(filename, field_name="model file path")).is_file()
            for filename in model.files
        )
    return path.is_file()


def _model_is_verified(model: ModelSpec, path: Path, checksum: str = "") -> bool:
    if not _model_is_downloaded(model, path):
        return False
    if model.files:
        return True
    current_checksum = checksum or sha1_file(path)
    return bool(model.sha1 and current_checksum == model.sha1)


def _download_url_to_file(url: str, tmp_dir: Path, size_limit: int, model_name: str, *, prefix: str) -> tuple[Path, int]:
    return _download_url_to_file_with_fd(url, tmp_dir, None, size_limit, model_name, prefix=prefix)


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
    if tmp_dir_fd is None:
        try:
            assert_no_symlink_ancestors(tmp_dir, field_name="model temporary directory")
        except RuntimeError as exc:
            raise ModelError(str(exc)) from exc
    allowed_hosts = {"huggingface.co"}
    allowed_urls = {TINY_DE_MODEL_URL} if model_name == "tiny-de" else None
    url = _assert_download_url(
        url,
        field_name="model download URL",
        allowed_hosts=allowed_hosts,
        allowed_urls=allowed_urls,
    )
    temporary_name: str | None = None
    tmp_path: Path | None = None
    try:
        if tmp_dir_fd is None:
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=tmp_dir, prefix=prefix) as output:
                tmp_path = Path(output.name)
                try:
                    os.fchmod(output.fileno(), 0o600)
                except OSError:
                    pass
                with _open_model_download_response(
                    url, allowed_hosts=allowed_hosts, allowed_urls=allowed_urls
                ) as response:
                    geturl = getattr(response, "geturl", None)
                    if callable(geturl):
                        final_url = _assert_download_url(
                            geturl(),
                            field_name="model download redirect URL",
                            allowed_hosts=allowed_hosts,
                        )
                        if allowed_urls is not None and not any(
                            _url_matches_allowed_base(final_url, allowed_url) for allowed_url in allowed_urls
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
                        downloaded += len(chunk)
                        if downloaded > size_limit:
                            raise ModelError(f"downloaded model too large for {model_name}: {downloaded} > {size_limit}")
                        output.write(chunk)
                    if content_length is not None and downloaded != content_length:
                        raise ModelError(
                            f"downloaded model size mismatch for {model_name}: {downloaded} != {content_length}"
                        )
            return tmp_path, downloaded

        temporary_name, tmp_fd = _create_temporary_file_in_parent_directory(tmp_dir_fd, prefix=prefix)
        tmp_path = tmp_dir / temporary_name
        with os.fdopen(tmp_fd, "wb") as output:
            try:
                os.fchmod(output.fileno(), 0o600)
            except OSError:
                pass
            with _open_model_download_response(url, allowed_hosts=allowed_hosts, allowed_urls=allowed_urls) as response:
                geturl = getattr(response, "geturl", None)
                if callable(geturl):
                    final_url = _assert_download_url(
                        geturl(),
                        field_name="model download redirect URL",
                        allowed_hosts=allowed_hosts,
                    )
                    if allowed_urls is not None and not any(
                        _url_matches_allowed_base(final_url, allowed_url) for allowed_url in allowed_urls
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
                    downloaded += len(chunk)
                    if downloaded > size_limit:
                        raise ModelError(f"downloaded model too large for {model_name}: {downloaded} > {size_limit}")
                    output.write(chunk)
                if content_length is not None and downloaded != content_length:
                    raise ModelError(f"downloaded model size mismatch for {model_name}: {downloaded} != {content_length}")
        return tmp_path, downloaded
    except Exception:
        if tmp_dir_fd is not None and temporary_name is not None:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=tmp_dir_fd)
        elif tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()
        raise


def _download_directory_model(model: ModelSpec, path: Path, force: bool) -> dict[str, object]:
    root = _model_root(model)
    _assert_model_parent_for_atomic_replace(path, root, field_name="model path")
    if path.exists() and not force:
        status = model_status(model, verify=True)
        if status["verified"]:
            return {**status, "status": "done", "message": f"model already downloaded: {path}"}
    parent_fd = _open_model_parent_directory(path, root, field_name="model path")
    _assert_model_path_for_atomic_replace(path, root, field_name="model path")
    tmp_dir: Path | None = None
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

        size_limit = _download_size_limit(model)
        if model.files and not model.repo_id:
            raise ModelError(f"model catalog entry {model.name} is missing repo_id for multi-file download")

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
            target = tmp_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            _assert_model_path_for_atomic_replace(target, root, field_name="model file path")
            target_parent_fd = _open_model_parent_directory(target, root, field_name="model file path")
            try:
                tmp_path, downloaded = _download_url_to_file_with_fd(
                    url,
                    target.parent,
                    target_parent_fd,
                    size_limit,
                    model.name,
                    prefix=f".{target.name}.",
                )
            finally:
                os.close(target_parent_fd)
            downloaded_total += downloaded
            try:
                _replace_model_sibling_path(tmp_path, target, root, field_name="model file path")
            except (OSError, ModelError) as exc:
                raise ModelError(f"failed to persist downloaded model file: {target}") from exc
        if downloaded_total > size_limit:
            raise ModelError(f"downloaded model too large for {model.name}: {downloaded_total} > {size_limit}")
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
                raise ModelError(f"failed to prepare existing model directory backup: {path}") from exc
            _assert_safe_model_directory(path)
        try:
            _replace_model_sibling_path(tmp_dir, path, root, field_name="model path")
        except (OSError, ModelError) as exc:
            if backup_dir is not None:
                try:
                    _replace_model_sibling_path(backup_dir, path, root, field_name="model path")
                except (OSError, ModelError) as restore_exc:
                    if path.exists():
                        try:
                            _remove_model_backup_path(path)
                        except OSError:
                            pass
                    raise ModelError(f"failed to restore existing model directory after download failure: {path}") from restore_exc
            raise ModelError(f"failed to persist downloaded model directory: {path}") from exc
        if backup_dir is not None:
            try:
                _remove_model_backup_path(backup_dir)
            except OSError as cleanup_exc:
                orphan_path = backup_dir.with_name(f"{backup_dir.name}.{secrets.token_hex(8)}.orphan")
                try:
                    backup_dir.replace(orphan_path)
                    _remove_model_backup_path(orphan_path)
                except OSError:
                    raise ModelError(f"failed to remove model backup after successful download: {backup_dir}") from cleanup_exc
    except Exception:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        os.close(parent_fd)
    return {**model_status(model, verify=True), "status": "done", "message": f"model downloaded: {path}"}


def _restore_model_file_backup(path: Path, backup_path: Path) -> None:
    root = path.parent
    _replace_model_sibling_path(backup_path, path, root, field_name="model backup path")


def _remove_model_backup_path(backup_path: Path) -> None:
    if backup_path.is_dir() and not backup_path.is_symlink():
        shutil.rmtree(backup_path)
        return
    backup_path.unlink()


def download_model(name: str, force: bool = False) -> dict[str, object]:
    if not isinstance(force, bool):
        raise ModelError("force must be a boolean")
    model = resolve_model(name)
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
            return {**status, "status": "done", "message": f"model already downloaded: {path}"}

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
            os.close(parent_fd)
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
            _replace_model_sibling_path(tmp_path, path, root, field_name="model path")
            replaced_path = True
            _clear_model_checksum_cache(tmp_path)
            _set_model_checksum_cache(path, checksum, tmp_stat)
        except OSError as exc:
            raise ModelError(f"failed to persist downloaded model file: {path}") from exc
    except Exception:
        if tmp_path is not None:
            _clear_model_checksum_cache(tmp_path)
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        if replaced_path:
            _clear_model_checksum_cache(path)
            if backup_path is not None:
                try:
                    _restore_model_file_backup(path, backup_path)
                except (OSError, ModelError) as restore_exc:
                    if path.exists():
                        try:
                            _remove_model_backup_path(path)
                        except OSError:
                            pass
                    raise ModelError(f"failed to restore existing model file after download failure: {path}") from restore_exc
                if previous_cache_entry_exists and previous_cache_entry is not None:
                    _model_checksum_cache[str(path)] = dict(previous_cache_entry)
                    _write_model_checksum_cache()
            else:
                try:
                    _remove_model_backup_path(path)
                except OSError as cleanup_exc:
                    raise ModelError(f"failed to remove partially installed model file after download failure: {path}") from cleanup_exc
        elif backup_path is not None and not path.exists():
            try:
                _restore_model_file_backup(path, backup_path)
            except (OSError, ModelError) as restore_exc:
                raise ModelError(f"failed to restore existing model file after download failure: {path}") from restore_exc
            if previous_cache_entry_exists and previous_cache_entry is not None:
                _model_checksum_cache[str(path)] = dict(previous_cache_entry)
                _write_model_checksum_cache()
        raise
    if backup_path is not None:
        try:
            _remove_model_backup_path(backup_path)
        except OSError as cleanup_exc:
            raise ModelError(f"failed to remove model backup after successful download: {backup_path}") from cleanup_exc
    return {**model_status(model, verify=True), "status": "done", "message": f"model downloaded: {path}"}


def remove_model(name: str) -> dict[str, object]:
    model = resolve_model(name)
    path = model_path(model)
    root = _model_root(model)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    removed = False
    removed_tmp = False
    if path.is_symlink():
        raise ModelError(f"model path must not be a symlink: {path}")
    elif path.is_dir():
        _assert_path_within_model_root(path, root)
        if not path.parent.exists() or not path.parent.is_dir():
            raise ModelError(f"model path parent is not a directory: {path.parent}")
        shutil.rmtree(path)
        removed = True
    else:
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            pass
    try:
        tmp_path.unlink()
        removed_tmp = True
    except FileNotFoundError:
        pass
    except OSError as exc:
        if removed:
            try:
                _clear_model_checksum_cache(path)
            except ModelError:
                pass
        raise ModelError(f"failed to remove temporary model file: {tmp_path}") from exc
    if removed:
        _clear_model_checksum_cache(path)
    if removed_tmp:
        _clear_model_checksum_cache(tmp_path)
    return {
        **asdict(model),
        "status": "done",
        "message": f"model removed: {path}" if removed else f"model was not downloaded: {path}",
        "path": str(path),
        "removed": removed,
        "removed_tmp": removed_tmp,
    }
