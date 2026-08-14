from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .path_safety import (
    assert_backup_source_regular_file,
    normalize_backup_archive_path,
    open_file_without_following_symlinks,
)

BACKUP_MANIFEST_SCHEMA_VERSION = 1
BACKUP_KINDS = ("config", "transcript", "audio")
BACKUP_SELECTION_KEYS = ("config", "transcripts", "audio")
BACKUP_KIND_SELECTION_KEYS = {
    "config": "config",
    "transcript": "transcripts",
    "audio": "audio",
}
BACKUP_ARCHIVE_PREFIXES = {
    "config": "config/",
    "transcript": "transcripts/",
    "audio": "audio/",
}
MAX_MANIFEST_BYTES = 1_000_000
MAX_MANIFEST_ARTIFACTS = 10_000
MAX_MANIFEST_WARNINGS = 256
MAX_MANIFEST_TEXT_CHARS = 4096
HASH_CHUNK_BYTES = 1024 * 1024
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BackupManifestError(ValueError):
    pass


def _reject_non_finite_json_number(value: str) -> object:
    raise BackupManifestError("manifest contains a non-finite number")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BackupManifestError("manifest contains a duplicate object key")
        result[key] = value
    return result


def _safe_text(value: object, *, field_name: str, max_chars: int = MAX_MANIFEST_TEXT_CHARS) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise BackupManifestError(f"manifest {field_name} must be text")
    if not value or len(value) > max_chars:
        raise BackupManifestError(f"manifest {field_name} is invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise BackupManifestError(f"manifest {field_name} contains an invalid control character")
    return value


def _safe_token(value: object, *, field_name: str) -> str:
    text = _safe_text(value, field_name=field_name, max_chars=256)
    if _SAFE_TOKEN.fullmatch(text) is None:
        raise BackupManifestError(f"manifest {field_name} is not an opaque token")
    return text


def _safe_integer(value: object, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BackupManifestError(f"manifest {field_name} must be a non-negative integer")
    return value


def _safe_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise BackupManifestError(f"manifest {field_name} must be a boolean")
    return value


def _require_exact_keys(value: Mapping[str, object], required: set[str], *, field_name: str) -> None:
    keys = set(value)
    if keys != required:
        raise BackupManifestError(f"manifest {field_name} has unsupported or missing fields")


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    kind: str
    archive_path: str
    source_identity: str
    size: int
    sha256: str
    mtime_ns: int

    def __post_init__(self) -> None:
        if isinstance(self.kind, bool) or not isinstance(self.kind, str) or self.kind not in BACKUP_KINDS:
            raise BackupManifestError("manifest artifact kind is unsupported")
        try:
            archive_path = normalize_backup_archive_path(self.archive_path, field_name="manifest archive path")
        except (RuntimeError, TypeError) as exc:
            raise BackupManifestError(str(exc)) from exc
        if not archive_path.startswith(BACKUP_ARCHIVE_PREFIXES[self.kind]):
            raise BackupManifestError("manifest archive path does not match artifact kind")
        source_identity = _safe_token(self.source_identity, field_name="artifact source identity")
        size = _safe_integer(self.size, field_name="artifact size")
        mtime_ns = _safe_integer(self.mtime_ns, field_name="artifact mtime")
        if isinstance(self.sha256, bool) or not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise BackupManifestError("manifest artifact sha256 is invalid")
        object.__setattr__(self, "archive_path", archive_path)
        object.__setattr__(self, "source_identity", source_identity)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "mtime_ns", mtime_ns)

    def to_dict(self) -> dict[str, object]:
        return {
            "archive_path": self.archive_path,
            "kind": self.kind,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "size": self.size,
            "source_identity": self.source_identity,
        }

    @classmethod
    def from_mapping(cls, value: object) -> BackupArtifact:
        if not isinstance(value, Mapping):
            raise BackupManifestError("manifest artifact must be an object")
        _require_exact_keys(
            value,
            {"kind", "archive_path", "source_identity", "size", "sha256", "mtime_ns"},
            field_name="artifact",
        )
        kind = value["kind"]
        if isinstance(kind, bool) or not isinstance(kind, str):
            raise BackupManifestError("manifest artifact kind must be text")
        archive_path = value["archive_path"]
        if isinstance(archive_path, bool) or not isinstance(archive_path, str):
            raise BackupManifestError("manifest artifact archive path must be text")
        source_identity = value["source_identity"]
        if isinstance(source_identity, bool) or not isinstance(source_identity, str):
            raise BackupManifestError("manifest artifact source identity must be text")
        sha256 = value["sha256"]
        if isinstance(sha256, bool) or not isinstance(sha256, str):
            raise BackupManifestError("manifest artifact sha256 must be text")
        return cls(
            kind=kind,
            archive_path=archive_path,
            source_identity=source_identity,
            size=value["size"],  # type: ignore[arg-type]
            sha256=sha256,
            mtime_ns=value["mtime_ns"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class BackupManifest:
    job_id: str
    created_at_utc: str
    app_version: str
    encryption_enabled: bool
    encryption_mode: str
    envelope_version: int
    selection: tuple[tuple[str, bool], ...]
    artifacts: tuple[BackupArtifact, ...]
    warnings: tuple[str, ...] = ()
    schema_version: int = BACKUP_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != BACKUP_MANIFEST_SCHEMA_VERSION:
            raise BackupManifestError("manifest schema version is unsupported")
        _safe_token(self.job_id, field_name="job id")
        _safe_text(self.created_at_utc, field_name="created_at_utc", max_chars=64)
        _safe_text(self.app_version, field_name="app version", max_chars=128)
        if (
            isinstance(self.encryption_mode, bool)
            or not isinstance(self.encryption_mode, str)
            or self.encryption_mode not in {"keyring", "passphrase", "off"}
        ):
            raise BackupManifestError("manifest encryption mode is unsupported")
        if not isinstance(self.encryption_enabled, bool):
            raise BackupManifestError("manifest encryption enabled must be a boolean")
        if self.encryption_enabled != (self.encryption_mode != "off"):
            raise BackupManifestError("manifest encryption fields are inconsistent")
        envelope_version = _safe_integer(self.envelope_version, field_name="envelope version")
        try:
            selection = dict(self.selection)
        except (TypeError, ValueError) as exc:
            raise BackupManifestError("manifest selection is invalid") from exc
        if set(selection) != set(BACKUP_SELECTION_KEYS) or any(not isinstance(value, bool) for value in selection.values()):
            raise BackupManifestError("manifest selection is invalid")
        if not any(selection.values()):
            raise BackupManifestError("manifest must select at least one category")
        if len(self.artifacts) > MAX_MANIFEST_ARTIFACTS:
            raise BackupManifestError("manifest contains too many artifacts")
        archive_paths: set[str] = set()
        for artifact in self.artifacts:
            if not isinstance(artifact, BackupArtifact):
                raise BackupManifestError("manifest artifacts are invalid")
            if artifact.archive_path in archive_paths:
                raise BackupManifestError("manifest contains duplicate archive paths")
            archive_paths.add(artifact.archive_path)
            if not selection[BACKUP_KIND_SELECTION_KEYS[artifact.kind]]:
                raise BackupManifestError("manifest artifact belongs to an unselected category")
        if len(self.warnings) > MAX_MANIFEST_WARNINGS:
            raise BackupManifestError("manifest contains too many warnings")
        for warning in self.warnings:
            _safe_text(warning, field_name="warning", max_chars=512)
        object.__setattr__(self, "envelope_version", envelope_version)
        object.__setattr__(self, "selection", tuple((key, selection[key]) for key in BACKUP_SELECTION_KEYS))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    def to_dict(self) -> dict[str, object]:
        return {
            "app_version": self.app_version,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "created_at_utc": self.created_at_utc,
            "encryption": {
                "enabled": self.encryption_enabled,
                "envelope_version": self.envelope_version,
                "mode": self.encryption_mode,
            },
            "job_id": self.job_id,
            "schema_version": self.schema_version,
            "selection": dict(self.selection),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_mapping(cls, value: object) -> BackupManifest:
        if not isinstance(value, Mapping):
            raise BackupManifestError("manifest must be an object")
        _require_exact_keys(
            value,
            {"schema_version", "job_id", "created_at_utc", "app_version", "encryption", "selection", "artifacts", "warnings"},
            field_name="root",
        )
        schema_version = value["schema_version"]
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise BackupManifestError("manifest schema version must be an integer")
        encryption = value["encryption"]
        if not isinstance(encryption, Mapping):
            raise BackupManifestError("manifest encryption must be an object")
        _require_exact_keys(encryption, {"enabled", "mode", "envelope_version"}, field_name="encryption")
        selection = value["selection"]
        if not isinstance(selection, Mapping):
            raise BackupManifestError("manifest selection must be an object")
        _require_exact_keys(selection, set(BACKUP_SELECTION_KEYS), field_name="selection")
        artifacts = value["artifacts"]
        if not isinstance(artifacts, list):
            raise BackupManifestError("manifest artifacts must be a list")
        warnings = value["warnings"]
        if not isinstance(warnings, list):
            raise BackupManifestError("manifest warnings must be a list")
        try:
            return cls(
                schema_version=schema_version,
                job_id=value["job_id"],  # type: ignore[arg-type]
                created_at_utc=value["created_at_utc"],  # type: ignore[arg-type]
                app_version=value["app_version"],  # type: ignore[arg-type]
                encryption_enabled=encryption["enabled"],  # type: ignore[arg-type]
                encryption_mode=encryption["mode"],  # type: ignore[arg-type]
                envelope_version=encryption["envelope_version"],  # type: ignore[arg-type]
                selection=tuple((key, selection[key]) for key in BACKUP_SELECTION_KEYS),
                artifacts=tuple(BackupArtifact.from_mapping(item) for item in artifacts),
                warnings=tuple(warnings),
            )
        except BackupManifestError:
            raise
        except (TypeError, ValueError) as exc:
            raise BackupManifestError("manifest contains invalid fields") from exc


def create_manifest(
    *,
    job_id: str,
    created_at_utc: str,
    app_version: str,
    encryption_mode: str,
    selection: Mapping[str, bool],
    artifacts: Sequence[BackupArtifact],
    warnings: Sequence[str] = (),
    envelope_version: int = 0,
) -> BackupManifest:
    if not isinstance(selection, Mapping):
        raise BackupManifestError("manifest selection must be a mapping")
    mode = encryption_mode.casefold() if isinstance(encryption_mode, str) else encryption_mode
    return BackupManifest(
        job_id=job_id,
        created_at_utc=created_at_utc,
        app_version=app_version,
        encryption_enabled=mode != "off",
        encryption_mode=mode,
        envelope_version=envelope_version,
        selection=tuple((key, selection.get(key, False)) for key in BACKUP_SELECTION_KEYS),
        artifacts=tuple(artifacts),
        warnings=tuple(warnings),
    )


def serialize_manifest(manifest: BackupManifest) -> bytes:
    if not isinstance(manifest, BackupManifest):
        raise BackupManifestError("manifest must be a BackupManifest")
    try:
        return (json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise BackupManifestError("manifest could not be serialized") from exc


def parse_manifest(payload: bytes | str) -> BackupManifest:
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raise BackupManifestError("manifest payload must be bytes or text")
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise BackupManifestError("manifest payload is too large or empty")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_number,
        )
    except BackupManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise BackupManifestError("manifest JSON is invalid") from exc
    return BackupManifest.from_mapping(document)


def hash_regular_file(path: Path, *, max_bytes: int | None = None) -> tuple[int, int, str]:
    """Hash one stable regular file without following symlinks or hardlinks."""
    try:
        before = assert_backup_source_regular_file(path)
        if max_bytes is not None and (
            isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0
        ):
            raise BackupManifestError("maximum backup source size is invalid")
        if max_bytes is not None and before.st_size > max_bytes:
            raise BackupManifestError("backup source exceeds the configured size limit")
        fd = open_file_without_following_symlinks(path, os.O_RDONLY, field_name="backup source")
    except BackupManifestError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise BackupManifestError(f"backup source could not be opened: {path.name}") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise BackupManifestError("backup source changed to an unsafe file")
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise BackupManifestError("backup source changed before hashing")
        digest = hashlib.sha256()
        offset = 0
        while offset < opened.st_size:
            try:
                chunk = os.pread(fd, min(HASH_CHUNK_BYTES, opened.st_size - offset), offset)
            except InterruptedError:
                continue
            if not chunk:
                raise BackupManifestError("backup source ended during hashing")
            digest.update(chunk)
            offset += len(chunk)
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ):
            raise BackupManifestError("backup source changed during hashing")
        return after.st_size, after.st_mtime_ns, digest.hexdigest()
    except BackupManifestError:
        raise
    except (OSError, OverflowError) as exc:
        raise BackupManifestError("backup source could not be hashed") from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def collect_artifact(
    *,
    kind: str,
    archive_path: str,
    source_identity: str,
    source_path: Path,
    max_bytes: int | None = None,
) -> BackupArtifact:
    size, mtime_ns, sha256 = hash_regular_file(source_path, max_bytes=max_bytes)
    try:
        return BackupArtifact(kind, archive_path, source_identity, size, sha256, mtime_ns)
    except (BackupManifestError, RuntimeError, TypeError, ValueError) as exc:
        if isinstance(exc, BackupManifestError):
            raise
        raise BackupManifestError("backup artifact metadata is invalid") from exc


def verify_artifact_source(artifact: BackupArtifact, source_path: Path) -> None:
    if not isinstance(artifact, BackupArtifact):
        raise BackupManifestError("backup artifact is invalid")
    size, mtime_ns, sha256 = hash_regular_file(source_path)
    if (size, mtime_ns, sha256) != (artifact.size, artifact.mtime_ns, artifact.sha256):
        raise BackupManifestError("backup artifact hash or metadata mismatch")
