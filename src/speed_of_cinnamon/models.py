from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import models_dir

HUGGING_FACE_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"


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
    key = (name or "").strip()
    models = catalog_by_name()
    if key in models:
        return models[key]
    raise ModelError(f"unknown model: {name}")


def model_path(model: ModelSpec) -> Path:
    return models_dir() / model.filename


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_status(model: ModelSpec, verify: bool = False) -> dict[str, object]:
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


def downloaded_model_paths() -> list[Path]:
    paths: list[Path] = []
    for model in CATALOG:
        path = model_path(model)
        if path.exists() and path.is_file() and sha1_file(path) == model.sha1:
            paths.append(path)
    return paths


def default_whisper_cpp_model_path() -> str:
    for path in downloaded_model_paths():
        return str(path)
    return ""


def download_model(name: str, force: bool = False) -> dict[str, object]:
    model = resolve_model(name)
    path = model_path(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        status = model_status(model, verify=True)
        if status["verified"]:
            return {**status, "status": "done", "message": f"model already downloaded: {path}"}

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with (
            urllib.request.urlopen(model.url, timeout=30) as response,
            tmp_path.open("wb") as output,
        ):
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        checksum = sha1_file(tmp_path)
        if checksum != model.sha1:
            raise ModelError(f"downloaded checksum mismatch for {model.name}: {checksum}")
        os.replace(tmp_path, path)
    except Exception:
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
