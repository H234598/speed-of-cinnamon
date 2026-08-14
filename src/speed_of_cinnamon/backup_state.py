from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from .path_safety import (
    assert_fd_is_private_directory,
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
    open_file_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)
from .paths import default_backup_state_file

BACKUP_STATE_SCHEMA_VERSION = 1
MAX_BACKUP_STATE_BYTES = 512_000
MAX_BACKUP_STATE_JOBS = 256
MAX_BACKUP_STATE_ARTIFACTS_PER_JOB = 10_000
_STATUSES = {"running", "success", "failed", "skipped"}


class BackupStateError(RuntimeError):
    pass


def _reject_non_finite_json_number(value: str) -> object:
    raise BackupStateError("backup state contains a non-finite number")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BackupStateError("backup state contains a duplicate key")
        result[key] = value
    return result


def _safe_text(value: object, *, field_name: str, max_chars: int = 4096) -> str:
    if isinstance(value, bool) or not isinstance(value, str) or not value or len(value) > max_chars:
        raise BackupStateError(f"backup state {field_name} is invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise BackupStateError(f"backup state {field_name} contains a control character")
    return value


def _safe_token(value: object, *, field_name: str) -> str:
    text = _safe_text(value, field_name=field_name, max_chars=256)
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in text):
        raise BackupStateError(f"backup state {field_name} is invalid")
    return text


def _safe_nonnegative_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BackupStateError(f"backup state {field_name} is invalid")
    return value


def _empty_state() -> dict[str, object]:
    return {"schema_version": BACKUP_STATE_SCHEMA_VERSION, "jobs": []}


def _normalize_artifact(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "source_identity", "size", "sha256", "mtime_ns"}:
        raise BackupStateError("backup state artifact is invalid")
    kind = _safe_token(value["kind"], field_name="artifact kind")
    source_identity = _safe_token(value["source_identity"], field_name="artifact source identity")
    sha256 = _safe_token(value["sha256"], field_name="artifact hash")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise BackupStateError("backup state artifact hash is invalid")
    return {
        "kind": kind,
        "mtime_ns": _safe_nonnegative_integer(value["mtime_ns"], field_name="artifact mtime"),
        "sha256": sha256,
        "size": _safe_nonnegative_integer(value["size"], field_name="artifact size"),
        "source_identity": source_identity,
    }


def _normalize_job(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise BackupStateError("backup state job is invalid")
    required = {"job_id", "status", "created_at_utc", "updated_at_utc", "archive_name", "error", "artifacts"}
    if set(value) != required:
        raise BackupStateError("backup state job fields are invalid")
    status = _safe_token(value["status"], field_name="job status")
    if status not in _STATUSES:
        raise BackupStateError("backup state job status is invalid")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > MAX_BACKUP_STATE_ARTIFACTS_PER_JOB:
        raise BackupStateError("backup state job artifacts are invalid")
    return {
        "archive_name": "" if value["archive_name"] == "" else _safe_text(value["archive_name"], field_name="archive name", max_chars=512),
        "artifacts": [_normalize_artifact(item) for item in artifacts],
        "created_at_utc": _safe_text(value["created_at_utc"], field_name="created timestamp", max_chars=64),
        "error": "" if value["error"] == "" else _safe_text(value["error"], field_name="job error", max_chars=512),
        "job_id": _safe_token(value["job_id"], field_name="job id"),
        "status": status,
        "updated_at_utc": _safe_text(value["updated_at_utc"], field_name="updated timestamp", max_chars=64),
    }


def _normalize_state(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "jobs"}:
        raise BackupStateError("backup state root is invalid")
    if value["schema_version"] != BACKUP_STATE_SCHEMA_VERSION:
        raise BackupStateError("backup state schema version is unsupported")
    jobs = value["jobs"]
    if not isinstance(jobs, list) or len(jobs) > MAX_BACKUP_STATE_JOBS:
        raise BackupStateError("backup state jobs are invalid")
    return {"schema_version": BACKUP_STATE_SCHEMA_VERSION, "jobs": [_normalize_job(job) for job in jobs]}


class BackupStateStore:
    """Small, private, lock-protected ledger. It stores metadata only."""

    def __init__(self, path: Path | None = None):
        candidate = default_backup_state_file() if path is None else path
        if isinstance(candidate, bool) or not isinstance(candidate, Path) or not candidate.is_absolute():
            raise BackupStateError("backup state path must be absolute")
        assert_safe_path_components(candidate, field_name="backup state path")
        assert_no_symlink_ancestors(candidate, field_name="backup state path")
        self.path = candidate

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        parent_fd = ensure_directory_without_following_symlinks(lock_path.parent, field_name="backup state directory")
        fd: int | None = None
        try:
            assert_fd_is_private_directory(parent_fd, field_name="backup state directory")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(lock_path.name, flags, 0o600, dir_fd=parent_fd)
            assert_fd_is_regular_private_file(fd, field_name="backup state lock", require_private_mode=True)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    break
                except InterruptedError:
                    continue
            yield
        except OSError as exc:
            raise BackupStateError("backup state lock failed") from exc
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.close(parent_fd)
            except OSError:
                pass

    def _read_unlocked(self) -> dict[str, object]:
        if not self.path.exists():
            return _empty_state()
        try:
            fd = open_file_without_following_symlinks(self.path, os.O_RDONLY, field_name="backup state")
        except FileNotFoundError:
            return _empty_state()
        try:
            payload = os.read(fd, MAX_BACKUP_STATE_BYTES + 1)
        finally:
            os.close(fd)
        if len(payload) > MAX_BACKUP_STATE_BYTES:
            raise BackupStateError("backup state is too large")
        try:
            document = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_json_number,
            )
        except BackupStateError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise BackupStateError("backup state is invalid") from exc
        return _normalize_state(document)

    def load(self) -> dict[str, object]:
        with self._locked():
            return self._read_unlocked()

    def _write_unlocked(self, state: Mapping[str, object]) -> None:
        normalized = _normalize_state(state)
        rendered = (json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        if len(rendered.encode("utf-8")) > MAX_BACKUP_STATE_BYTES:
            raise BackupStateError("backup state is too large")
        try:
            write_text_atomically_without_following_symlinks(self.path, rendered, field_name="backup state")
        except OSError as exc:
            raise BackupStateError("backup state could not be written") from exc

    def record_job(
        self,
        *,
        job_id: str,
        status: str,
        created_at_utc: str,
        archive_name: str = "",
        error: str = "",
        artifacts: Sequence[Mapping[str, object]] = (),
    ) -> dict[str, object]:
        if status not in _STATUSES:
            raise BackupStateError("backup job status is invalid")
        now = created_at_utc
        entry = _normalize_job(
            {
                "archive_name": archive_name,
                "artifacts": list(artifacts),
                "created_at_utc": created_at_utc,
                "error": error,
                "job_id": job_id,
                "status": status,
                "updated_at_utc": now,
            }
        )
        with self._locked():
            state = self._read_unlocked()
            jobs = [job for job in state["jobs"] if job["job_id"] != entry["job_id"]]
            jobs.append(entry)
            state["jobs"] = jobs[-MAX_BACKUP_STATE_JOBS:]
            self._write_unlocked(state)
            return entry

    def has_unchanged_artifact(
        self,
        *,
        kind: str,
        source_identity: str,
        size: int,
        sha256: str,
        mtime_ns: int,
    ) -> bool:
        with self._locked():
            state = self._read_unlocked()
        wanted = {
            "kind": kind,
            "source_identity": source_identity,
            "size": size,
            "sha256": sha256,
            "mtime_ns": mtime_ns,
        }
        return any(
            job["status"] == "success" and wanted in job["artifacts"]
            for job in state["jobs"]
        )
