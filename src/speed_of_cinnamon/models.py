from __future__ import annotations

import json
import hashlib
import os
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import models_dir

HUGGING_FACE_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
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


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ModelError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


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
    return models_dir() / _MODEL_CHECKSUM_CACHE_FILE


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
        with cache_path.open("r", encoding="utf-8") as handle:
            text = handle.read()
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
            if not path.exists() or not path.is_file():
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
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(_model_checksum_cache, indent=2, sort_keys=True) + "\n"
        if len(rendered.encode("utf-8")) > MAX_MODEL_CHECKSUM_JSON_BYTES:
            _model_checksum_cache.clear()
            try:
                cache_path.unlink()
            except OSError:
                pass
            return
        with tempfile.NamedTemporaryFile("w", delete=False, dir=cache_path.parent, encoding="utf-8") as handle:
            handle.write(rendered)
            tmp_path = Path(handle.name)
        try:
            os.replace(tmp_path, cache_path)
            try:
                cache_path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return
    except OSError:
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
    info = path.stat()
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

    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    checksum = digest.hexdigest()
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


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    filename: str
    size: str
    sha1: str
    description: str

    @property
    def url(self) -> str:
        return f"{HUGGING_FACE_BASE_URL}/{self.filename}"


CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="tiny.en",
        filename="ggml-tiny.en.bin",
        size="75 MiB",
        sha1="c78c86eb1a8faa21b369bcd33207cc90d64ae9df",
        description="Fast English-only starter model",
    ),
    ModelSpec(
        name="tiny",
        filename="ggml-tiny.bin",
        size="75 MiB",
        sha1="bd577a113a864445d4c299885e0cb97d4ba92b5f",
        description="Fast multilingual starter model",
    ),
    ModelSpec(
        name="base.en",
        filename="ggml-base.en.bin",
        size="142 MiB",
        sha1="137c40403d78fd54d454da0f9bd998f78703390c",
        description="Better English accuracy, still light",
    ),
    ModelSpec(
        name="base",
        filename="ggml-base.bin",
        size="142 MiB",
        sha1="465707469ff3a37a2b9b8d8f89f2f99de7299dac",
        description="Better multilingual accuracy, still light",
    ),
    ModelSpec(
        name="small.en",
        filename="ggml-small.en.bin",
        size="466 MiB",
        sha1="db8a495a91d927739e50b3fc1cc4c6b8f6c2d022",
        description="Higher English accuracy, slower",
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


def catalog_by_name() -> dict[str, ModelSpec]:
    return {model.name: model for model in CATALOG}


def resolve_model(name: str) -> ModelSpec:
    if isinstance(name, bool) or not isinstance(name, str):
        raise ModelError("model name must be text")
    key = (name or "").strip()
    models = catalog_by_name()
    if key in models:
        return models[key]
    raise ModelError(f"unknown model: {name}")


def model_path(model: ModelSpec) -> Path:
    return models_dir() / model.filename


def is_english_language(language: str) -> bool:
    if not isinstance(language, str):
        return False
    normalized = (language or "").strip().lower().replace("_", "-")
    return normalized in ENGLISH_LANGUAGE_CODES or normalized.startswith("en-")


def model_name_is_english_only(name: str) -> bool:
    return (name or "").strip().lower().endswith(".en")


def model_path_is_english_only(path: str | Path) -> bool:
    if not isinstance(path, (str, Path)):
        return False
    filename = Path(path).name.lower()
    for model in CATALOG:
        if filename == model.filename.lower() or filename == model.name.lower():
            return model_name_is_english_only(model.name)
    return ".en." in filename or filename.endswith(".en.bin")


def model_supports_language(path: str | Path, language: str) -> bool:
    if not isinstance(path, (str, Path)) or not isinstance(language, str):
        return False
    if _contains_escaped_null(str(path)):
        return False
    return is_english_language(language) or not model_path_is_english_only(path)


def sha1_file(path: Path) -> str:
    return _cached_or_computed_sha1(path)


def model_status(model: ModelSpec, verify: bool = False) -> dict[str, object]:
    if not isinstance(verify, bool):
        raise ModelError("verify must be a boolean")
    path = model_path(model)
    exists = path.exists()
    checksum = sha1_file(path) if verify and exists and path.is_file() else ""
    return {
        **asdict(model),
        "url": model.url,
        "path": str(path),
        "downloaded": exists,
        "verified": bool(exists and checksum == model.sha1) if verify else False,
        "checksum": checksum,
    }


def list_models() -> list[dict[str, object]]:
    return [model_status(model) for model in CATALOG]


def downloaded_model_paths(language: str = "") -> list[Path]:
    paths: list[Path] = []
    for model in CATALOG:
        path = model_path(model)
        if path.exists() and path.is_file() and model_supports_language(path, language) and sha1_file(path) == model.sha1:
            paths.append(path)
    return paths


def default_whisper_cpp_model_path(language: str = "") -> str:
    for path in downloaded_model_paths(language):
        return str(path)
    return ""


def download_model(name: str, force: bool = False) -> dict[str, object]:
    if not isinstance(force, bool):
        raise ModelError("force must be a boolean")
    model = resolve_model(name)
    path = model_path(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        status = model_status(model, verify=True)
        if status["verified"]:
            return {**status, "status": "done", "message": f"model already downloaded: {path}"}

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    size_limit = _download_size_limit(model)
    try:
        with (
            urllib.request.urlopen(model.url, timeout=30) as response,
            tmp_path.open("wb") as output,
        ):
            content_length = _read_content_length(response)
            if content_length is not None and content_length > size_limit:
                raise ModelError(
                    f"downloaded model too large for {model.name}: {content_length} > {size_limit}"
                )

            downloaded = 0
            while True:
                chunk = response.read(MAX_MODEL_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > size_limit:
                    raise ModelError(f"downloaded model too large for {model.name}: {downloaded} > {size_limit}")
                output.write(chunk)
            if content_length is not None and downloaded != content_length:
                raise ModelError(
                    f"downloaded model size mismatch for {model.name}: {downloaded} != {content_length}"
                )
        checksum = sha1_file(tmp_path)
        if checksum != model.sha1:
            raise ModelError(f"downloaded checksum mismatch for {model.name}: {checksum}")
        try:
            os.replace(tmp_path, path)
            _clear_model_checksum_cache(tmp_path)
            _set_model_checksum_cache(path, checksum, path.stat())
        except OSError as exc:
            raise ModelError(f"failed to persist downloaded model file: {path}") from exc
    except Exception:
        _clear_model_checksum_cache(tmp_path)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return {**model_status(model, verify=True), "status": "done", "message": f"model downloaded: {path}"}


def remove_model(name: str) -> dict[str, object]:
    model = resolve_model(name)
    path = model_path(model)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    _clear_model_checksum_cache(path)
    _clear_model_checksum_cache(tmp_path)
    removed = False
    removed_tmp = False
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
    return {
        **asdict(model),
        "status": "done",
        "message": f"model removed: {path}" if removed else f"model was not downloaded: {path}",
        "path": str(path),
        "removed": removed,
        "removed_tmp": removed_tmp,
    }
