from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import stat
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .artifact_crypto import (
    ARTIFACT_ENCRYPTION_OFF,
    MAX_ENCRYPTED_ARTIFACT_BYTES,
    ArtifactCryptoError,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted_path,
    normalize_artifact_encryption,
    read_decrypted_bytes_from_file,
    read_private_bytes,
)
from .backup_manifest import (
    BACKUP_KINDS,
    BACKUP_SELECTION_KEYS,
    BackupArtifact,
    BackupManifest,
    BackupManifestError,
    collect_artifact,
    create_manifest,
    parse_manifest,
    serialize_manifest,
)
from .backup_state import BackupStateStore
from .path_safety import (
    _rename_without_replacing,
    assert_backup_source_regular_file,
    assert_backup_target_not_within_sources,
    assert_fd_is_private_directory,
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    create_bytes_atomically_without_following_symlinks,
    ensure_directory_without_following_symlinks,
    normalize_backup_archive_path,
    open_file_without_following_symlinks,
    _resolve_no_follow_flag,
    write_bytes_atomically_without_following_symlinks,
)
from .secure_delete import secure_wipe_regular_file_at

MAX_BACKUP_MEMBER_COUNT = 10_001
MAX_BACKUP_ARCHIVE_BYTES = MAX_ENCRYPTED_ARTIFACT_BYTES
HASH_CHUNK_BYTES = 1024 * 1024


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackupInput:
    kind: str
    archive_path: str
    source_identity: str
    source_path: Path


@dataclass(frozen=True, slots=True)
class BackupResult:
    job_id: str
    archive_path: Path | None
    manifest: BackupManifest | None
    skipped: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RestoreDryRun:
    archive_path: Path
    destination_directory: Path
    manifest: BackupManifest
    archive_members: tuple[str, ...]
    conflicts: tuple[str, ...] = ()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_selection(selection: Mapping[str, bool]) -> dict[str, bool]:
    if not isinstance(selection, Mapping) or set(selection) != set(BACKUP_SELECTION_KEYS):
        raise BackupError("backup selection is invalid")
    normalized = {}
    for key in BACKUP_SELECTION_KEYS:
        value = selection[key]
        if not isinstance(value, bool):
            raise BackupError("backup selection must contain booleans")
        normalized[key] = value
    if not any(normalized.values()):
        raise BackupError("backup must select at least one category")
    return normalized


def _validate_input(item: BackupInput) -> None:
    if not isinstance(item, BackupInput):
        raise BackupError("backup input is invalid")
    if item.kind not in BACKUP_KINDS or not item.source_path.is_absolute():
        raise BackupError("backup input is invalid")
    try:
        assert_safe_path_components(item.source_path, field_name="backup source path")
        assert_no_symlink_ancestors(item.source_path, field_name="backup source path")
    except (RuntimeError, TypeError, ValueError) as exc:
        raise BackupError("backup source path is unsafe") from exc


def _selection_key(kind: str) -> str:
    return {"config": "config", "transcript": "transcripts", "audio": "audio"}[kind]


def _ledger_artifacts(collected: Sequence[tuple[BackupArtifact, Path]]) -> list[dict[str, object]]:
    return [
        {
            "kind": artifact.kind,
            "mtime_ns": artifact.mtime_ns,
            "sha256": artifact.sha256,
            "size": artifact.size,
            "source_identity": artifact.source_identity,
        }
        for artifact, _ in collected
    ]


def _archive_name(job_id: str, created_at_utc: str, *, encrypted: bool) -> str:
    timestamp = created_at_utc.replace("-", "").replace(":", "").replace("+00:00", "").replace("Z", "Z")
    suffix = ".socbackup.socenc" if encrypted else ".socbackup"
    return f"soc-backup-{timestamp}-{job_id[:16]}{suffix}"


def _close_fd(fd: int | None, *, strict: bool = False) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            if strict:
                raise


def _copy_source_to_stage(artifact: BackupArtifact, source: Path, destination: Path) -> None:
    before = assert_backup_source_regular_file(source, field_name="backup source")
    source_fd = open_file_without_following_symlinks(source, os.O_RDONLY, field_name="backup source")
    destination_fd: int | None = None
    parent_fd: int | None = None
    try:
        opened = os.fstat(source_fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise BackupError("backup source changed before copying")
        if (opened.st_size, opened.st_mtime_ns) != (artifact.size, artifact.mtime_ns):
            raise BackupError("backup source metadata changed before copying")
        parent_fd = ensure_directory_without_following_symlinks(destination.parent, field_name="backup staging directory")
        name = destination.name
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _resolve_no_follow_flag(field_name="backup staging file")
            | getattr(os, "O_CLOEXEC", 0)
        )
        destination_fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
        offset = 0
        while offset < opened.st_size:
            while True:
                try:
                    chunk = os.pread(source_fd, min(HASH_CHUNK_BYTES, opened.st_size - offset), offset)
                    break
                except InterruptedError:
                    continue
            if not chunk:
                raise BackupError("backup source ended during copying")
            written = 0
            while written < len(chunk):
                try:
                    written += os.write(destination_fd, chunk[written:])
                except InterruptedError:
                    continue
            offset += len(chunk)
        os.fsync(destination_fd)
        after = os.fstat(source_fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ):
            raise BackupError("backup source changed during copying")
    except OSError as exc:
        raise BackupError("backup source could not be copied") from exc
    finally:
        _close_fd(destination_fd)
        _close_fd(source_fd)
        _close_fd(parent_fd)


def _create_stage_directory(target_fd: int, target: Path) -> Path:
    for _ in range(32):
        name = f".socbackup-stage-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, 0o700, dir_fd=target_fd)
        except FileExistsError:
            continue
        stage = target / name
        assert_no_symlink_ancestors(stage, field_name="backup staging directory")
        return stage
    raise BackupError("backup staging directory could not be created")


def _create_archive_file(target_fd: int, archive_name: str) -> tuple[str, int]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | _resolve_no_follow_flag(field_name="backup archive file")
        | getattr(os, "O_CLOEXEC", 0)
    )
    for _ in range(32):
        temporary_name = f".{archive_name}.{secrets.token_hex(12)}.tmp"
        try:
            fd = os.open(temporary_name, flags, 0o600, dir_fd=target_fd)
            return temporary_name, fd
        except FileExistsError:
            continue
    raise BackupError("backup temporary archive could not be created")


def _write_fd_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(fd, view[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise BackupError("backup encrypted archive write made no progress")
        offset += written


def _secure_remove_temporary_file(target_fd: int, name: str) -> None:
    try:
        expected_stat = os.stat(name, dir_fd=target_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    secure_wipe_regular_file_at(
        target_fd,
        name,
        expected_stat,
        field_name="backup plaintext staging",
    )
    try:
        os.unlink(name, dir_fd=target_fd)
    except FileNotFoundError:
        return
    os.fsync(target_fd)


def _secure_remove_stage_directory(target_fd: int, stage: Path) -> None:
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise OSError("secure backup staging cleanup is not supported")
    shutil.rmtree(stage.name, dir_fd=target_fd)


def _add_stage_file(archive: tarfile.TarFile, path: Path, archive_path: str) -> None:
    try:
        assert_backup_source_regular_file(path, field_name="backup staged file")
        fd = open_file_without_following_symlinks(path, os.O_RDONLY, field_name="backup staged file")
    except (OSError, RuntimeError, ValueError) as exc:
        raise BackupError("backup staged file is unsafe") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BackupError("backup staged file is not regular")
        member = tarfile.TarInfo(name=archive_path)
        member.size = info.st_size
        member.mtime = info.st_mtime
        member.mode = stat.S_IMODE(info.st_mode)
        member.uid = 0
        member.gid = 0
        member.uname = ""
        member.gname = ""
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            archive.addfile(member, stream)
    finally:
        if fd != -1:
            _close_fd(fd)


def _build_archive(stage: Path, temporary_path: Path, manifest: BackupManifest) -> None:
    manifest_path = stage / "manifest.json"
    write_bytes_atomically_without_following_symlinks(
        manifest_path,
        serialize_manifest(manifest),
        field_name="backup manifest",
    )
    try:
        fd = open_file_without_following_symlinks(temporary_path, os.O_RDWR, field_name="backup temporary archive")
    except (OSError, RuntimeError, ValueError) as exc:
        raise BackupError("backup temporary archive could not be opened") from exc
    try:
        with os.fdopen(fd, "w+b", closefd=True) as stream:
            fd = -1
            with tarfile.open(fileobj=stream, mode="w") as archive:
                _add_stage_file(archive, manifest_path, "manifest.json")
                for artifact in manifest.artifacts:
                    _add_stage_file(archive, stage / artifact.archive_path, artifact.archive_path)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd != -1:
            _close_fd(fd)


def _hash_tar_member(archive: tarfile.TarFile, member: tarfile.TarInfo, expected_size: int) -> str:
    stream = archive.extractfile(member)
    if stream is None:
        raise BackupError("backup archive member cannot be read")
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(HASH_CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            raise BackupError("backup archive member is larger than its manifest")
        digest.update(chunk)
    if size != expected_size:
        raise BackupError("backup archive member size differs from its manifest")
    return digest.hexdigest()


def _verify_tar_archive(archive: tarfile.TarFile) -> BackupManifest:
    members: list[tarfile.TarInfo] = []
    for member in archive:
        members.append(member)
        if len(members) > MAX_BACKUP_MEMBER_COUNT:
            raise BackupError("backup archive member count is invalid")
    if not members:
        raise BackupError("backup archive member count is invalid")
    by_name: dict[str, tarfile.TarInfo] = {}
    for member in members:
        if not member.isreg() or member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise BackupError("backup archive contains an unsafe member")
        if member.name in by_name:
            raise BackupError("backup archive contains a duplicate member")
        if member.name != "manifest.json":
            try:
                normalize_backup_archive_path(member.name, field_name="backup archive member")
            except (RuntimeError, TypeError, ValueError) as exc:
                raise BackupError("backup archive contains an unsafe path") from exc
        by_name[member.name] = member
    manifest_member = by_name.get("manifest.json")
    if manifest_member is None or manifest_member.size > 1_000_000:
        raise BackupError("backup archive manifest is missing or too large")
    manifest_stream = archive.extractfile(manifest_member)
    if manifest_stream is None:
        raise BackupError("backup archive manifest cannot be read")
    manifest = parse_manifest(manifest_stream.read(manifest_member.size + 1))
    expected = {"manifest.json"} | {artifact.archive_path for artifact in manifest.artifacts}
    if set(by_name) != expected:
        raise BackupError("backup archive members differ from its manifest")
    by_artifact = {artifact.archive_path: artifact for artifact in manifest.artifacts}
    for archive_path, artifact in by_artifact.items():
        member = by_name[archive_path]
        if member.size != artifact.size or _hash_tar_member(archive, member, artifact.size) != artifact.sha256:
            raise BackupError("backup archive artifact hash mismatch")
    return manifest


def _verify_archive(path: Path) -> BackupManifest:
    archive_stat = assert_backup_source_regular_file(path, field_name="backup archive")
    if archive_stat.st_size > MAX_BACKUP_ARCHIVE_BYTES:
        raise BackupError("backup archive is too large")
    fd = open_file_without_following_symlinks(path, os.O_RDONLY, field_name="backup archive")
    try:
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                return _verify_tar_archive(archive)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError("backup archive could not be verified") from exc
    finally:
        if fd != -1:
            _close_fd(fd)


def _verify_archive_bytes(payload: bytes) -> BackupManifest:
    if not isinstance(payload, bytes) or len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES:
        raise BackupError("decrypted backup archive is too large")
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            return _verify_tar_archive(archive)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError("decrypted backup archive could not be verified") from exc


def verify_backup(archive_path: Path) -> BackupManifest:
    """Verify plain v1 bundle structure and all artifact hashes without extraction."""
    try:
        if not isinstance(archive_path, Path) or not archive_path.is_absolute():
            raise BackupError("backup archive path must be absolute")
        assert_safe_path_components(archive_path, field_name="backup archive path")
        assert_no_symlink_ancestors(archive_path, field_name="backup archive path")
        encrypted = is_encrypted_path(archive_path)
        if encrypted:
            payload = read_decrypted_bytes_from_file(
                archive_path,
                kind="backup",
                field_name="backup archive",
                max_bytes=MAX_ENCRYPTED_ARTIFACT_BYTES,
                require_encrypted=True,
            )
            manifest = _verify_archive_bytes(payload)
        else:
            manifest = _verify_archive(archive_path)
        if encrypted != (manifest.encryption_mode != ARTIFACT_ENCRYPTION_OFF):
            raise BackupError("backup archive encryption metadata is inconsistent")
        return manifest
    except BackupError:
        raise
    except (ArtifactCryptoError, BackupManifestError, OSError, RuntimeError, ValueError) as exc:
        raise BackupError("backup archive verification failed") from exc


def create_backup(
    target_directory: Path,
    *,
    sources: Sequence[BackupInput],
    source_roots: Sequence[Path],
    selection: Mapping[str, bool],
    app_version: str,
    encryption_mode: object = ARTIFACT_ENCRYPTION_OFF,
    job_id: str | None = None,
    created_at_utc: str | None = None,
    state_store: BackupStateStore | None = None,
) -> BackupResult:
    normalized_selection = _validate_selection(selection)
    try:
        effective_encryption_mode = normalize_artifact_encryption(encryption_mode)
    except ArtifactCryptoError as exc:
        raise BackupError("backup encryption mode is invalid") from exc
    if not isinstance(target_directory, Path) or not target_directory.is_absolute():
        raise BackupError("backup target directory must be absolute")
    if not source_roots:
        raise BackupError("backup source roots are required")
    try:
        assert_safe_path_components(target_directory, field_name="backup target directory")
        assert_no_symlink_ancestors(target_directory, field_name="backup target directory")
        assert_backup_target_not_within_sources(target_directory, source_roots, field_name="backup target directory")
    except (RuntimeError, TypeError, ValueError) as exc:
        raise BackupError("backup target directory is unsafe") from exc
    ledger = state_store or BackupStateStore()
    job = job_id or secrets.token_hex(16)
    created = created_at_utc or _utc_now()
    archive_name = _archive_name(job, created, encrypted=effective_encryption_mode != ARTIFACT_ENCRYPTION_OFF)
    target_fd: int | None = None
    stage: Path | None = None
    temporary_name: str | None = None
    plain_temporary_name: str | None = None
    published = False
    ledger_started = False
    manifest: BackupManifest | None = None
    result: BackupResult | None = None
    try:
        target_fd = ensure_directory_without_following_symlinks(target_directory, field_name="backup target directory")
        target_stat = os.fstat(target_fd)
        if not stat.S_ISDIR(target_stat.st_mode) or (hasattr(os, "getuid") and target_stat.st_uid != os.getuid()):
            raise BackupError("backup target directory is not owned by the current user")
        ledger_started = True
        ledger.record_job(job_id=job, status="running", created_at_utc=created)
        collected: list[tuple[BackupArtifact, Path]] = []
        seen_fingerprints: set[tuple[str, int, str]] = set()
        seen_archive_paths: set[str] = set()
        for item in sources:
            _validate_input(item)
            if not normalized_selection[_selection_key(item.kind)]:
                continue
            artifact = collect_artifact(
                kind=item.kind,
                archive_path=item.archive_path,
                source_identity=item.source_identity,
                source_path=item.source_path,
            )
            fingerprint = (artifact.kind, artifact.size, artifact.sha256)
            if fingerprint in seen_fingerprints:
                continue
            if artifact.archive_path in seen_archive_paths:
                raise BackupError("backup archive path collision")
            seen_fingerprints.add(fingerprint)
            seen_archive_paths.add(artifact.archive_path)
            if artifact.kind == "config" and ledger.has_unchanged_artifact(
                kind=artifact.kind,
                source_identity=artifact.source_identity,
                size=artifact.size,
                sha256=artifact.sha256,
                mtime_ns=artifact.mtime_ns,
                match_mtime=False,
            ):
                continue
            collected.append((artifact, item.source_path))
        if not collected:
            ledger.record_job(job_id=job, status="skipped", created_at_utc=created)
            result = BackupResult(job, None, None, skipped=True)
            return result
        effective_selection = dict(normalized_selection)
        for kind, selection_key in (("config", "config"), ("transcript", "transcripts"), ("audio", "audio")):
            if effective_selection[selection_key] and not any(artifact.kind == kind for artifact, _ in collected):
                effective_selection[selection_key] = False
        manifest = create_manifest(
            job_id=job,
            created_at_utc=created,
            app_version=app_version,
            encryption_mode=effective_encryption_mode,
            selection=effective_selection,
            artifacts=[artifact for artifact, _ in collected],
            envelope_version=2 if effective_encryption_mode != ARTIFACT_ENCRYPTION_OFF else 0,
        )
        stage = _create_stage_directory(target_fd, target_directory)
        for artifact, source_path in collected:
            _copy_source_to_stage(artifact, source_path, stage / artifact.archive_path)
        temporary_name, temporary_fd = _create_archive_file(target_fd, archive_name)
        _close_fd(temporary_fd)
        plain_temporary_name = temporary_name
        temporary_path = target_directory / temporary_name
        _build_archive(stage, temporary_path, manifest)
        verified_manifest = _verify_archive(temporary_path)
        if verified_manifest.encryption_mode != effective_encryption_mode:
            raise BackupError("backup manifest encryption mode differs from requested mode")
        if effective_encryption_mode != ARTIFACT_ENCRYPTION_OFF:
            plain_payload = read_private_bytes(
                temporary_path,
                field_name="backup plaintext staging",
                max_bytes=MAX_ENCRYPTED_ARTIFACT_BYTES,
            )
            encrypted_payload, _effective_mode = encrypt_bytes(
                plain_payload,
                effective_encryption_mode,
                kind="backup",
            )
            decrypted_payload = decrypt_bytes(encrypted_payload, kind="backup", require_encrypted=True)
            if hashlib.sha256(decrypted_payload).digest() != hashlib.sha256(plain_payload).digest():
                raise BackupError("encrypted backup payload failed round-trip verification")
            encrypted_temporary_name, encrypted_temporary_fd = _create_archive_file(target_fd, archive_name)
            temporary_name = encrypted_temporary_name
            temporary_path = target_directory / temporary_name
            try:
                _write_fd_all(encrypted_temporary_fd, encrypted_payload)
                os.fsync(encrypted_temporary_fd)
            finally:
                _close_fd(encrypted_temporary_fd)
            _secure_remove_temporary_file(target_fd, plain_temporary_name)
            plain_temporary_name = None
            del plain_payload, encrypted_payload, decrypted_payload
        temporary_stat = os.stat(temporary_path, follow_symlinks=False)
        _rename_without_replacing(
            temporary_name,
            archive_name,
            directory_fd=target_fd,
            expected_source_stat=temporary_stat,
            field_name="backup archive publication",
        )
        published = True
        result = BackupResult(job, target_directory / archive_name, verified_manifest)
        os.fsync(target_fd)
        ledger.record_job(
            job_id=job,
            status="success",
            created_at_utc=created,
            archive_name=archive_name,
            artifacts=_ledger_artifacts(collected),
        )
        return result
    except (BackupManifestError, OSError, RuntimeError, TypeError, ValueError, tarfile.TarError) as exc:
        if published and result is not None:
            warnings = list(result.warnings)
            warnings.append("backup published but post-publish bookkeeping failed")
            try:
                ledger.record_job(
                    job_id=job,
                    status="success",
                    created_at_utc=created,
                    archive_name=archive_name,
                    artifacts=_ledger_artifacts(collected),
                    error=warnings[-1],
                )
            except Exception:
                warnings.append("backup warning could not be recorded")
            object.__setattr__(result, "warnings", tuple(warnings))
            return result
        if ledger_started and not published:
            try:
                ledger.record_job(
                    job_id=job,
                    status="failed",
                    created_at_utc=created,
                    error=str(exc)[:512] or "backup job failed",
                )
            except Exception as ledger_error:
                exc.add_note("backup failure could not be recorded")
                exc.add_note(type(ledger_error).__name__)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("backup job failed") from exc
    except BaseException as exc:
        if ledger_started and not published:
            try:
                ledger.record_job(
                    job_id=job,
                    status="failed",
                    created_at_utc=created,
                    error="backup job interrupted",
                )
            except Exception as ledger_error:
                exc.add_note("backup interruption could not be recorded")
                exc.add_note(type(ledger_error).__name__)
        raise
    finally:
        cleanup_errors: list[OSError] = []
        if not published and target_fd is not None:
            if plain_temporary_name is not None:
                try:
                    _secure_remove_temporary_file(target_fd, plain_temporary_name)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    cleanup_errors.append(exc)
                else:
                    if temporary_name == plain_temporary_name:
                        temporary_name = None
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=target_fd)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    cleanup_errors.append(exc)
        if stage is not None:
            try:
                _secure_remove_stage_directory(target_fd, stage)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_errors.append(exc)
        try:
            _close_fd(target_fd, strict=True)
        except OSError as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            if published and result is not None:
                warnings = list(result.warnings)
                warnings.append("backup temporary cleanup failed")
                try:
                    ledger.record_job(
                        job_id=job,
                        status="success",
                        created_at_utc=created,
                        archive_name=archive_name,
                        artifacts=_ledger_artifacts(collected),
                        error=warnings[-1],
                    )
                except Exception:
                    warnings.append("backup cleanup warning could not be recorded")
                object.__setattr__(result, "warnings", tuple(warnings))
            else:
                pending_exception = sys.exc_info()[1]
                if pending_exception is not None:
                    pending_exception.add_note("backup temporary cleanup failed")
                    pending_exception.add_note(type(cleanup_errors[0]).__name__)
                else:
                    raise BackupError("backup temporary cleanup failed") from cleanup_errors[0]


def restore_dry_run(
    archive_path: Path,
    destination_directory: Path,
    *,
    source_roots: Sequence[Path] = (),
) -> RestoreDryRun:
    if not isinstance(destination_directory, Path) or not destination_directory.is_absolute():
        raise BackupError("restore destination must be absolute")
    try:
        assert_safe_path_components(destination_directory, field_name="restore destination")
        assert_no_symlink_ancestors(destination_directory, field_name="restore destination")
        try:
            destination_stat = destination_directory.lstat()
        except FileNotFoundError:
            destination_stat = None
        except OSError as exc:
            raise RuntimeError("restore destination could not be inspected") from exc
        if destination_stat is not None and not stat.S_ISDIR(destination_stat.st_mode):
            raise RuntimeError("restore destination must be a directory")
        if source_roots:
            assert_backup_target_not_within_sources(destination_directory, source_roots, field_name="restore destination")
    except (RuntimeError, TypeError, ValueError) as exc:
        raise BackupError("restore destination is unsafe") from exc
    manifest = verify_backup(archive_path)
    member_paths = ("manifest.json",) + tuple(artifact.archive_path for artifact in manifest.artifacts)
    conflicts: list[str] = []
    for member_path in member_paths:
        candidate = destination_directory / member_path
        try:
            assert_safe_path_components(candidate, field_name="restore member")
            assert_no_symlink_ancestors(candidate, field_name="restore member")
        except (RuntimeError, TypeError, ValueError) as exc:
            raise BackupError("restore destination is unsafe") from exc
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupError("restore member could not be inspected") from exc
        else:
            conflicts.append(member_path)
    return RestoreDryRun(
        archive_path=archive_path,
        destination_directory=destination_directory,
        manifest=manifest,
        archive_members=("manifest.json",) + tuple(artifact.archive_path for artifact in manifest.artifacts),
        conflicts=tuple(conflicts),
    )


def _read_backup_payload(archive_path: Path) -> bytes:
    if is_encrypted_path(archive_path):
        return read_decrypted_bytes_from_file(
            archive_path,
            kind="backup",
            field_name="backup archive",
            max_bytes=MAX_ENCRYPTED_ARTIFACT_BYTES,
            require_encrypted=True,
        )
    return read_private_bytes(
        archive_path,
        field_name="backup archive",
        max_bytes=MAX_ENCRYPTED_ARTIFACT_BYTES,
    )


def _create_restore_stage_directory(parent_fd: int, destination: Path) -> tuple[str, Path, int]:
    nofollow_flag = _resolve_no_follow_flag(field_name="restore staging directory")
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if isinstance(directory_flag, bool) or not isinstance(directory_flag, int) or directory_flag <= 0:
        raise BackupError("secure restore staging is not supported")
    for _ in range(100):
        stage_name = f".{destination.name}.{secrets.token_hex(16)}.restore"
        try:
            os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        stage_fd: int | None = None
        try:
            stage_fd = os.open(
                stage_name,
                os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            assert_fd_is_private_directory(stage_fd, field_name="restore staging directory")
            return stage_name, destination.parent / stage_name, stage_fd
        except BaseException:
            if stage_fd is not None:
                _close_fd(stage_fd)
            try:
                os.rmdir(stage_name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    raise BackupError("failed to create restore staging directory")


def restore_backup(
    archive_path: Path,
    destination_directory: Path,
    *,
    source_roots: Sequence[Path] = (),
) -> RestoreDryRun:
    plan = restore_dry_run(
        archive_path,
        destination_directory,
        source_roots=source_roots,
    )
    if plan.conflicts:
        raise BackupError("restore destination contains existing members")

    payload = _read_backup_payload(archive_path)
    payload_manifest = _verify_archive_bytes(payload)
    if payload_manifest != plan.manifest:
        raise BackupError("backup archive changed during restore")

    parent_fd: int | None = None
    stage_fd: int | None = None
    stage_name = ""
    stage_path: Path | None = None
    published = False
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            destination_directory.parent,
            field_name="restore destination parent",
        )
        try:
            os.stat(destination_directory.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BackupError("restore destination already exists")
        stage_name, stage_path, stage_fd = _create_restore_stage_directory(parent_fd, destination_directory)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member_name in plan.archive_members:
                member = archive.getmember(member_name)
                stream = archive.extractfile(member)
                if stream is None:
                    raise BackupError("backup archive member cannot be read")
                member_data = stream.read(member.size + 1)
                if len(member_data) != member.size:
                    raise BackupError("backup archive member size changed during restore")
                target = stage_path / member_name
                create_bytes_atomically_without_following_symlinks(
                    target,
                    member_data,
                    field_name="restore artifact",
                )
        os.fsync(stage_fd)
        stage_stat = os.fstat(stage_fd)
        _rename_without_replacing(
            stage_name,
            destination_directory.name,
            directory_fd=parent_fd,
            target_directory_fd=parent_fd,
            expected_source_stat=stage_stat,
            field_name="restore destination publication",
        )
        published = True
        os.fsync(parent_fd)
        return plan
    except (ArtifactCryptoError, BackupManifestError, OSError, RuntimeError, TypeError, ValueError, tarfile.TarError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("backup restore failed") from exc
    finally:
        if stage_fd is not None:
            _close_fd(stage_fd)
        if not published and stage_name and parent_fd is not None:
            try:
                _secure_remove_stage_directory(parent_fd, stage_path or destination_directory.parent / stage_name)
            except FileNotFoundError:
                pass
        if parent_fd is not None:
            _close_fd(parent_fd)
