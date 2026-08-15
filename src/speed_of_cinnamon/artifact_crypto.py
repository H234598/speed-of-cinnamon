from __future__ import annotations

import base64
import errno
import fcntl
import json
import os
import secrets
import shutil
import stat
import subprocess  # nosec B404
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .output import (
    _clipboard_lock_identity_for_pid,
    _kill_output_process_with_pidfd,
    _terminate_output_process_group,
)
from .paths import APP_ID, APP_NAME, config_dir
from .proc_safety import _read_proc_stat, _read_proc_stat_path
from .path_safety import (
    assert_fd_is_private_directory,
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
    open_file_without_following_symlinks,
    _rename_without_replacing,
    write_bytes_atomically_without_following_symlinks,
)
from .secure_delete import secure_wipe_regular_file_at

ARTIFACT_ENCRYPTION_OFF = "off"
ARTIFACT_ENCRYPTION_PASSPHRASE = "passphrase"  # nosec B105
ARTIFACT_ENCRYPTION_KEYRING = "keyring"
ARTIFACT_ENCRYPTION_CHOICES = (
    ARTIFACT_ENCRYPTION_OFF,
    ARTIFACT_ENCRYPTION_PASSPHRASE,
    ARTIFACT_ENCRYPTION_KEYRING,
)
ENCRYPTED_SUFFIX = ".socenc"
ENVELOPE_MAGIC = "SOCENC1"
LEGACY_ENVELOPE_VERSION = 1
ENVELOPE_VERSION = 2
ENVELOPE_ALGORITHM = "AES-256-GCM"
KEY_SIZE_BYTES = 32
NONCE_SIZE_BYTES = 12
SALT_SIZE_BYTES = 16
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
MAX_PASSPHRASE_CHARS = 4096
MAX_PASSPHRASE_FILE_BYTES = 16384
MAX_PASSPHRASE_FILE_PATH_CHARS = 4096
MIN_PASSPHRASE_CHARS = 32
MIN_PASSPHRASE_DISTINCT_CHARS = 8
MIN_GENERATED_PASSPHRASE_BYTES = KEY_SIZE_BYTES
MAX_ENCRYPTED_ARTIFACT_BYTES = 340 * 1024 * 1024
PASSPHRASE_ENV = "SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE"  # nosec B105
PASSPHRASE_FILE_ENV = "SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE_FILE"  # nosec B105
DEFAULT_PASSPHRASE_FILE_NAME = "artifact.key"  # nosec B105
DEFAULT_PASSPHRASE_HISTORY_FILE_NAME = "artifact.key.history"  # nosec B105
MAX_PASSPHRASE_HISTORY_ENTRIES = 8
MAX_PASSPHRASE_HISTORY_BYTES = 8192
KEYRING_INITIALIZATION_LOCK_FILE_NAME = ".artifact-keyring.lock"
KEYRING_INITIALIZATION_LOCK_TIMEOUT_SECONDS = 10.0
_SECRET_TOOL_TIMEOUT_SECONDS = 10
MAX_SECRET_TOOL_OUTPUT_BYTES = 64 * 1024
MAX_SECRET_TOOL_ARG_CHARS = 4096
_SECRET_TOOL_ATTRIBUTES = ["application", APP_ID, "purpose", "artifact-encryption"]
_SECRET_TOOL_COMMANDS = frozenset({"lookup", "store"})
_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:/var/lib/snapd/snap/bin"
_ALLOWED_SECRET_TOOL_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "XDG_CURRENT_DESKTOP",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
}
_SAFE_DBUS_SESSION_PREFIX = "unix:path="
_ACL_XATTR = "system.posix_acl_access"


def _reject_non_finite_json_number(value: str) -> object:
    raise ValueError("non-finite JSON number is not allowed")


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    try:
        primary.add_note("artifact encryption cleanup failed")
    except BaseException:
        pass


def _safe_utf8_length(value: str, *, field_name: str) -> int:
    encoded, error = _capture_normal_error(
        lambda: value.encode("utf-8"),
        f"{_safe_public_field_label(field_name)} must be valid UTF-8",
    )
    if error is not None:
        raise error
    return len(encoded)


class ArtifactCryptoError(RuntimeError):
    pass


class _PassphraseHistoryError(ArtifactCryptoError):
    pass


def _capture_normal_error(operation: Any, message: str) -> tuple[Any, ArtifactCryptoError | None]:
    try:
        return operation(), None
    except Exception:
        return None, ArtifactCryptoError(message)


def _safe_public_field_label(value: object) -> str:
    if isinstance(value, str) and value and len(value) <= 64 and all(
        char.isascii() and (char.isalnum() or char in "_-") for char in value
    ):
        return value
    return "artifact"


def _crypto_backend() -> tuple[type[BaseException], Any, Any]:
    backend_unavailable = False
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError:
        backend_unavailable = True
    if backend_unavailable:
        raise ArtifactCryptoError(
            "cryptography is required for artifact encryption; install python3-cryptography or disable encryption"
        )
    return InvalidTag, AESGCM, Scrypt


@dataclass(frozen=True)
class KeyMaterial:
    mode: str
    key: bytes


def normalize_artifact_encryption(value: object) -> str:
    if isinstance(value, bool):
        raise ArtifactCryptoError("artifact encryption mode must be text")
    if value is None:
        return ARTIFACT_ENCRYPTION_OFF
    if not isinstance(value, str):
        raise ArtifactCryptoError("artifact encryption mode must be text")
    normalized = value.strip().casefold().replace("_", "-")
    aliases = {
        "": ARTIFACT_ENCRYPTION_OFF,
        "none": ARTIFACT_ENCRYPTION_OFF,
        "disabled": ARTIFACT_ENCRYPTION_OFF,
        "false": ARTIFACT_ENCRYPTION_OFF,
        "0": ARTIFACT_ENCRYPTION_OFF,
        "off": ARTIFACT_ENCRYPTION_OFF,
        "variant-1": ARTIFACT_ENCRYPTION_PASSPHRASE,
        "variante-1": ARTIFACT_ENCRYPTION_PASSPHRASE,
        "pass": ARTIFACT_ENCRYPTION_PASSPHRASE,
        "password": ARTIFACT_ENCRYPTION_PASSPHRASE,
        "passphrase": ARTIFACT_ENCRYPTION_PASSPHRASE,
        "scrypt": ARTIFACT_ENCRYPTION_PASSPHRASE,
        "variant-2": ARTIFACT_ENCRYPTION_KEYRING,
        "variante-2": ARTIFACT_ENCRYPTION_KEYRING,
        "keyring": ARTIFACT_ENCRYPTION_KEYRING,
        "secret-service": ARTIFACT_ENCRYPTION_KEYRING,
        "secretservice": ARTIFACT_ENCRYPTION_KEYRING,
    }
    choices = ", ".join(ARTIFACT_ENCRYPTION_CHOICES)
    result, error = _capture_normal_error(
        lambda: aliases[normalized],
        f"unsupported artifact encryption mode; choose one of: {choices}",
    )
    if error is not None:
        raise error
    return result


def encryption_enabled(value: object) -> bool:
    return normalize_artifact_encryption(value) != ARTIFACT_ENCRYPTION_OFF


def encrypted_path_for(path: Path) -> Path:
    if not isinstance(path, Path):
        raise ArtifactCryptoError("encrypted artifact path must be a Path")
    if _contains_forbidden_environment_chars(str(path)):
        raise ArtifactCryptoError("encrypted artifact path is not safe")
    _, error = _capture_normal_error(
        lambda: assert_safe_path_components(path, field_name="encrypted artifact path"),
        "encrypted artifact path is not safe",
    )
    if error is not None:
        raise error
    if path.name.casefold().endswith(ENCRYPTED_SUFFIX):
        return path
    return path.with_name(path.name + ENCRYPTED_SUFFIX)


def is_encrypted_path(path: Path) -> bool:
    return isinstance(path, Path) and path.name.casefold().endswith(ENCRYPTED_SUFFIX)


def _is_encrypted_envelope(envelope: object) -> bool:
    return isinstance(envelope, dict) and envelope.get("magic") == ENVELOPE_MAGIC and isinstance(
        envelope.get("ciphertext"), str
    )


def is_encrypted_payload(payload: bytes) -> bool:
    if isinstance(payload, bool) or not isinstance(payload, bytes):
        return False
    if len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES:
        return False
    stripped = payload.lstrip()
    if not stripped.startswith(b"{"):
        return False
    try:
        envelope = json.loads(stripped.decode("utf-8"), parse_constant=_reject_non_finite_json_number)
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        return False
    return _is_encrypted_envelope(envelope)


def _is_json_like_payload(payload: bytes) -> bool:
    if isinstance(payload, bool) or not isinstance(payload, bytes):
        return False
    return payload.lstrip().startswith(b"{")


def _normalize_kind(kind: str) -> str:
    if not isinstance(kind, str) or isinstance(kind, bool) or not kind.strip():
        raise ArtifactCryptoError("artifact encryption kind must be text")
    safe_kind = kind.strip().casefold()
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_." for char in safe_kind):
        raise ArtifactCryptoError("artifact encryption kind contains unsupported characters")
    return safe_kind


def _aad(
    kind: str,
    *,
    version: int = ENVELOPE_VERSION,
    algorithm: str = ENVELOPE_ALGORITHM,
    mode: str = "",
    salt: bytes = b"",
    nonce: bytes = b"",
) -> bytes:
    safe_kind = _normalize_kind(kind)
    if version == LEGACY_ENVELOPE_VERSION:
        return f"{APP_ID}:{safe_kind}:v{LEGACY_ENVELOPE_VERSION}".encode("utf-8")
    if version != ENVELOPE_VERSION or not isinstance(algorithm, str) or not isinstance(mode, str):
        raise ArtifactCryptoError("encrypted artifact envelope version is unsupported")
    if not isinstance(salt, bytes) or not isinstance(nonce, bytes):
        raise ArtifactCryptoError("encrypted artifact envelope metadata is invalid")
    canonical = {
        "algorithm": algorithm,
        "app": APP_ID,
        "kind": safe_kind,
        "mode": mode,
        "nonce": _b64encode(nonce),
        "salt": _b64encode(salt) if salt else "",
        "version": version,
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64decode(value: object, *, field_name: str) -> bytes:
    if isinstance(value, bool) or not isinstance(value, str) or not value:
        raise ArtifactCryptoError(f"encrypted artifact {field_name} is invalid")
    decoded, error = _capture_normal_error(
        lambda: base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True),
        f"encrypted artifact {_safe_public_field_label(field_name)} is invalid",
    )
    if error is not None:
        raise error
    return decoded


def _contains_forbidden_secret_chars(value: str) -> bool:
    lowered = value.lower()
    if "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered:
        return True
    return any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)


def _contains_forbidden_environment_chars(value: str) -> bool:
    lowered = value.lower()
    if "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered:
        return True
    return any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)


def _is_valid_utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _new_generated_passphrase() -> str:
    return _b64encode(secrets.token_bytes(KEY_SIZE_BYTES))


def _decoded_generated_passphrase_bytes(value: str) -> bytes | None:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        return None
    if len(decoded) < MIN_GENERATED_PASSPHRASE_BYTES:
        return None
    if len(set(decoded)) < MIN_PASSPHRASE_DISTINCT_CHARS:
        return None
    return decoded


def _passphrase_is_strong(passphrase: str) -> bool:
    value = passphrase.strip()
    if not value:
        return False
    if _decoded_generated_passphrase_bytes(value) is not None:
        return True
    return len(value) >= MIN_PASSPHRASE_CHARS and len(set(value)) >= MIN_PASSPHRASE_DISTINCT_CHARS


def default_passphrase_file() -> Path:
    return config_dir() / DEFAULT_PASSPHRASE_FILE_NAME


def _default_passphrase_history_path(path: Path) -> Path:
    return path.with_name(DEFAULT_PASSPHRASE_HISTORY_FILE_NAME)


def _read_default_passphrase_history(path: Path) -> list[str]:
    history_path = _default_passphrase_history_path(path)
    history_inspection_failed = False
    try:
        history_stat = os.lstat(history_path)
    except FileNotFoundError:
        return []
    except OSError:
        history_inspection_failed = True
    if history_inspection_failed:
        raise _PassphraseHistoryError("artifact encryption passphrase history could not be inspected")
    if (
        not stat.S_ISREG(history_stat.st_mode)
        or getattr(history_stat, "st_nlink", 1) != 1
        or (hasattr(os, "getuid") and history_stat.st_uid != os.getuid())
        or history_stat.st_mode & 0o077
    ):
        raise _PassphraseHistoryError("artifact encryption passphrase history is not private")
    acl_failed = False
    try:
        _assert_no_posix_acl(history_path, field_name="artifact encryption passphrase history")
    except ArtifactCryptoError:
        acl_failed = True
    if acl_failed:
        raise _PassphraseHistoryError("artifact encryption passphrase history is not private")
    read_failed = False
    try:
        raw = read_private_bytes(
            history_path,
            field_name="artifact encryption passphrase history",
            max_bytes=MAX_PASSPHRASE_HISTORY_BYTES,
        )
    except ArtifactCryptoError:
        read_failed = True
    if read_failed:
        raise _PassphraseHistoryError("artifact encryption passphrase history could not be read")
    parse_failed = False
    try:
        document = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_finite_json_number)
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        parse_failed = True
    if parse_failed:
        raise _PassphraseHistoryError("artifact encryption passphrase history is invalid")
    if not isinstance(document, dict) or set(document) != {"version", "keys"}:
        raise _PassphraseHistoryError("artifact encryption passphrase history is invalid")
    version = document.get("version")
    if type(version) is not int or version != 1 or not isinstance(document.get("keys"), list):
        raise _PassphraseHistoryError("artifact encryption passphrase history is invalid")
    encoded_keys = document["keys"]
    if len(encoded_keys) > MAX_PASSPHRASE_HISTORY_ENTRIES:
        raise _PassphraseHistoryError("artifact encryption passphrase history is invalid")
    result: list[str] = []
    for encoded_key in encoded_keys:
        if isinstance(encoded_key, bool) or not isinstance(encoded_key, str):
            raise _PassphraseHistoryError("artifact encryption passphrase history is invalid")
        decode_failed = False
        try:
            decoded_key = base64.b64decode(encoded_key.encode("ascii"), altchars=b"-_", validate=True)
            passphrase = decoded_key.decode("utf-8")
        except (UnicodeDecodeError, ValueError, UnicodeError):
            decode_failed = True
        if decode_failed:
            raise _PassphraseHistoryError("artifact encryption passphrase history is invalid")
        if (
            not passphrase
            or len(decoded_key) > MAX_PASSPHRASE_CHARS
            or _contains_forbidden_secret_chars(passphrase)
            or passphrase in result
        ):
            raise _PassphraseHistoryError("artifact encryption passphrase history is invalid")
        result.append(passphrase)
    return result


def _write_default_passphrase_history(path: Path, previous_payload: bytes) -> None:
    try:
        previous_passphrase = previous_payload.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        raise _PassphraseHistoryError("artifact encryption passphrase history could not be prepared") from None
    if (
        not previous_passphrase
        or _contains_forbidden_secret_chars(previous_passphrase)
        or len(previous_passphrase.encode("utf-8")) > MAX_PASSPHRASE_CHARS
    ):
        raise _PassphraseHistoryError("artifact encryption passphrase history could not be prepared")
    existing = _read_default_passphrase_history(path)
    entries = [previous_passphrase, *(value for value in existing if value != previous_passphrase)]
    if len(entries) > MAX_PASSPHRASE_HISTORY_ENTRIES:
        raise _PassphraseHistoryError("artifact encryption passphrase history is too large")
    document = {
        "version": 1,
        "keys": [_b64encode(value.encode("utf-8")) for value in entries],
    }
    rendered, render_error = _capture_normal_error(
        lambda: json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
        "artifact encryption passphrase history could not be written",
    )
    if render_error is not None:
        raise _PassphraseHistoryError("artifact encryption passphrase history could not be written")
    if len(rendered) > MAX_PASSPHRASE_HISTORY_BYTES:
        raise _PassphraseHistoryError("artifact encryption passphrase history is too large")
    _, write_error = _capture_normal_error(
        lambda: write_bytes_atomically_without_following_symlinks(
            _default_passphrase_history_path(path),
            rendered,
            field_name="artifact encryption passphrase history",
        ),
        "artifact encryption passphrase history could not be written",
    )
    if write_error is not None:
        raise _PassphraseHistoryError("artifact encryption passphrase history could not be written")


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(fd, view[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("short write")
        offset += written


def _create_private_temp_passphrase_file(parent_fd: int, final_name: str) -> tuple[int, str]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise ArtifactCryptoError("secure artifact encryption passphrase temporary file creation is not supported")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag | getattr(os, "O_CLOEXEC", 0)
    safe_name = final_name.replace("/", "_") or DEFAULT_PASSPHRASE_FILE_NAME
    for _ in range(100):
        temp_name = f".{safe_name}.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(temp_name, flags, 0o600, dir_fd=parent_fd), temp_name
        except FileExistsError:
            continue
    raise ArtifactCryptoError("artifact encryption passphrase temporary file could not be created")


def _fsync_fd(fd: int) -> None:
    while True:
        try:
            os.fsync(fd)
            return
        except InterruptedError:
            continue
        except OSError:
            break
    raise ArtifactCryptoError("artifact encryption passphrase file could not be synchronized")


def _has_posix_acl(path: Path) -> bool:
    acl_error = False
    try:
        os.getxattr(path, _ACL_XATTR, follow_symlinks=False)
    except AttributeError:
        return False
    except OSError as error:
        if error.errno in {
            getattr(errno, "ENODATA", 61),
            getattr(errno, "ENOATTR", 93),
            errno.EOPNOTSUPP,
            errno.ENOTSUP,
        }:
            return False
        acl_error = True
    if acl_error:
        raise ArtifactCryptoError("artifact encryption passphrase file ACL could not be inspected")
    return True


def _assert_no_posix_acl(path: Path, *, field_name: str) -> None:
    if _has_posix_acl(path):
        raise ArtifactCryptoError(f"{field_name} must not have extended ACL permissions")


def _temp_passphrase_cleanup_error() -> ArtifactCryptoError:
    return ArtifactCryptoError("artifact encryption passphrase temporary file could not be removed")


def _scrub_temp_passphrase_file(
    parent_fd: int,
    temp_name: str,
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    if not temp_name:
        return
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise ArtifactCryptoError("secure artifact encryption passphrase temporary file scrubbing is not supported")
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd = os.open(
        temp_name,
        os.O_WRONLY | nofollow_flag | nonblock_flag | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_fd,
    )
    primary_error: BaseException | None = None
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            if expected_stat is not None:
                raise OSError("artifact encryption passphrase temporary file changed before scrubbing")
            return
        if expected_stat is not None and (
            file_stat.st_dev != expected_stat.st_dev
            or file_stat.st_ino != expected_stat.st_ino
            or file_stat.st_mode != expected_stat.st_mode
            or getattr(file_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
        ):
            raise OSError("artifact encryption passphrase temporary file changed before scrubbing")
        remaining = int(file_stat.st_size)
        if remaining > 0:
            os.lseek(fd, 0, os.SEEK_SET)
            chunk = b"\x00" * min(remaining, 65536)
            while remaining > 0:
                try:
                    written = os.write(fd, chunk[: min(remaining, len(chunk))])
                except InterruptedError:
                    continue
                if written <= 0:
                    break
                remaining -= written
            with suppress(OSError, RuntimeError):
                _fsync_fd(fd)
        while True:
            try:
                os.ftruncate(fd, 0)
                break
            except InterruptedError:
                continue
        with suppress(OSError, RuntimeError):
            _fsync_fd(fd)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise


def _generate_default_passphrase_file(path: Path, *, replace: bool = False) -> str:
    if path != default_passphrase_file():
        raise ArtifactCryptoError("only the default artifact encryption passphrase file can be generated automatically")
    passphrase = _new_generated_passphrase()
    payload = passphrase.encode("ascii") + b"\n"
    parent_fd = -1
    temp_fd = -1
    temp_name = ""
    backup_name = ""
    backup_created = False
    target_removed = False
    activation_attempted = False
    activation_stat: os.stat_result | None = None
    temporary_stat: os.stat_result | None = None
    existing_stat: os.stat_result | None = None
    previous_payload: bytes | None = None
    transaction_active = False
    primary_error: BaseException | None = None
    deferred_error: BaseException | None = None
    existing_passphrase: str | None = None

    def _same_leaf_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            getattr(first, "st_nlink", 1),
            first.st_size,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            getattr(second, "st_nlink", 1),
            second.st_size,
        )

    def _same_leaf_inode(first: os.stat_result, second: os.stat_result) -> bool:
        return (first.st_dev, first.st_ino, first.st_mode) == (second.st_dev, second.st_ino, second.st_mode)

    def _assert_expected_file(name: str, expected_stat: os.stat_result | None, *, description: str) -> None:
        if expected_stat is None:
            raise OSError(f"{description} identity is unavailable")
        current_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_leaf_inode(current_stat, expected_stat):
            raise OSError(f"{description} changed before cleanup")

    def _remove_expected_file(
        name: str,
        expected_stat: os.stat_result | None,
        *,
        description: str,
        secure_wipe: bool = True,
    ) -> None:
        if expected_stat is None:
            raise OSError(f"{description} identity is unavailable")
        if type(secure_wipe) is not bool:
            raise TypeError(f"{description} secure wipe flag is invalid")
        _assert_expected_file(name, expected_stat, description=description)
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
            raise OSError(f"{description} secure cleanup is not supported")
        for _ in range(100):
            cleanup_name = f"{name}.{secrets.token_hex(8)}.cleanup"
            renamed = False
            try:
                _rename_without_replacing(
                    name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name=f"{description} cleanup",
                )
                renamed = True
            except FileExistsError:
                continue
            cleanup_fd = -1
            unlinked = False
            claimed_stat: os.stat_result | None = None
            primary_error: BaseException | None = None
            try:
                cleanup_fd = os.open(
                    cleanup_name,
                    os.O_RDONLY | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                claimed_stat = os.fstat(cleanup_fd)
                if (
                    not stat.S_ISREG(claimed_stat.st_mode)
                    or (hasattr(os, "getuid") and claimed_stat.st_uid != os.getuid())
                    or not _same_leaf_inode(claimed_stat, expected_stat)
                ):
                    raise OSError(f"{description} changed before cleanup")
                current_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not _same_leaf_inode(current_stat, claimed_stat):
                    raise OSError(f"{description} changed before cleanup")
                if secure_wipe:
                    secure_wipe_regular_file_at(
                        parent_fd,
                        cleanup_name,
                        claimed_stat,
                        field_name=f"{description} secure cleanup",
                    )
                os.unlink(cleanup_name, dir_fd=parent_fd)
                unlinked = True
            except BaseException as exc:
                primary_error = exc
                if renamed and not unlinked:
                    try:
                        current_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current_stat = None
                    except BaseException as restore_probe_error:
                        _note_cleanup_failure(exc, restore_probe_error)
                    else:
                        try:
                            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            original_missing = True
                        except BaseException as restore_probe_error:
                            original_missing = False
                            _note_cleanup_failure(exc, restore_probe_error)
                        else:
                            original_missing = False
                        comparison_stat = claimed_stat or expected_stat
                        if (
                            original_missing
                            and current_stat is not None
                            and comparison_stat is not None
                            and _same_leaf_inode(current_stat, comparison_stat)
                        ):
                            try:
                                _rename_without_replacing(
                                    cleanup_name,
                                    name,
                                    directory_fd=parent_fd,
                                    field_name=f"{description} restore",
                                )
                            except BaseException as restore_error:
                                _note_cleanup_failure(exc, restore_error)
                raise
            finally:
                if cleanup_fd >= 0:
                    try:
                        os.close(cleanup_fd)
                    except BaseException as close_error:
                        if primary_error is not None:
                            _note_cleanup_failure(primary_error, close_error)
                        else:
                            raise
            return
        raise OSError(f"{description} cleanup path could not be claimed")

    def _remove_temporary_file() -> None:
        nonlocal temp_name
        if not temp_name:
            return
        try:
            _assert_expected_file(
                temp_name,
                temporary_stat,
                description="artifact encryption passphrase temporary file",
            )
        except FileNotFoundError:
            temp_name = ""
            return
        _remove_expected_file(
            temp_name,
            temporary_stat,
            description="artifact encryption passphrase temporary file",
        )
        temp_name = ""

    def _same_pre_activation_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            first.st_uid,
            first.st_gid,
            getattr(first, "st_nlink", 1),
            first.st_size,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            second.st_uid,
            second.st_gid,
            getattr(second, "st_nlink", 1),
            second.st_size,
        )

    def _read_previous_passphrase_payload(expected_stat: os.stat_result) -> bytes:
        nonblock_flag = getattr(os, "O_NONBLOCK", 0)
        fd = open_file_without_following_symlinks(
            path,
            os.O_RDONLY | nonblock_flag,
            field_name="artifact encryption passphrase file",
        )
        handle: Any | None = None
        primary_read_error: BaseException | None = None
        try:
            handle = os.fdopen(fd, "rb")
            fd = -1
            opened_stat = os.fstat(handle.fileno())
            if not _same_pre_activation_identity(opened_stat, expected_stat):
                raise OSError("artifact encryption passphrase file changed before backup activation")
            payload = handle.read(MAX_PASSPHRASE_FILE_BYTES + 1)
            final_stat = os.fstat(handle.fileno())
            if not _same_pre_activation_identity(final_stat, expected_stat):
                raise OSError("artifact encryption passphrase file changed while preparing rollback")
        except BaseException as exc:
            primary_read_error = exc
            raise
        finally:
            cleanup_read_error: BaseException | None = None
            if handle is not None:
                try:
                    handle.close()
                except BaseException as exc:
                    cleanup_read_error = exc
            if fd >= 0:
                try:
                    os.close(fd)
                except BaseException as exc:
                    if cleanup_read_error is None:
                        cleanup_read_error = exc
            if cleanup_read_error is not None:
                if primary_read_error is not None:
                    _note_cleanup_failure(primary_read_error, cleanup_read_error)
                else:
                    raise cleanup_read_error
        if len(payload) > MAX_PASSPHRASE_FILE_BYTES:
            raise OSError("artifact encryption passphrase file is too large")
        return payload

    def _restore_previous_passphrase_payload() -> None:
        if previous_payload is None:
            raise OSError("artifact encryption passphrase rollback payload is unavailable")
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise OSError("artifact encryption passphrase target exists during rollback")
        write_bytes_atomically_without_following_symlinks(
            path,
            previous_payload,
            field_name="artifact encryption passphrase file",
        )

    def _rollback_passphrase_activation() -> None:
        nonlocal backup_created, backup_name, target_removed
        if not transaction_active:
            return
        activation_visible = False
        if activation_attempted:
            try:
                current_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                current_stat = None
            expected_stat = activation_stat or temporary_stat
            if current_stat is not None and expected_stat is not None and _same_leaf_identity(current_stat, expected_stat):
                activation_visible = True
            elif existing_stat is None and current_stat is None:
                pass
            elif existing_stat is not None and current_stat is None and backup_created:
                target_removed = True
            elif target_removed and current_stat is None:
                pass
            elif existing_stat is not None and current_stat is not None and _same_pre_activation_identity(current_stat, existing_stat):
                pass
            else:
                raise OSError("artifact encryption passphrase target changed during rollback")
            if activation_visible:
                _remove_expected_file(
                    path.name,
                    expected_stat,
                    description="artifact encryption passphrase target",
                )
                _fsync_fd(parent_fd)
        elif backup_created:
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                target_removed = True
        if existing_stat is not None and target_removed and previous_payload is not None:
            _restore_previous_passphrase_payload()
            if backup_created:
                try:
                    _remove_expected_file(
                        backup_name,
                        existing_stat,
                        description="artifact encryption passphrase recovery backup",
                    )
                except FileNotFoundError:
                    pass
                else:
                    _fsync_fd(parent_fd)
                backup_name = ""
                backup_created = False
            return
        if backup_created:
            if not activation_visible:
                if target_removed:
                    try:
                        os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        _assert_expected_file(
                            backup_name,
                            existing_stat,
                            description="artifact encryption passphrase recovery backup",
                        )
                        _rename_without_replacing(
                            backup_name,
                            path.name,
                            directory_fd=parent_fd,
                            field_name="artifact encryption passphrase file",
                        )
                        backup_created = False
                        _fsync_fd(parent_fd)
                    else:
                        raise OSError("artifact encryption passphrase target exists during rollback")
                else:
                    _remove_expected_file(
                        backup_name,
                        existing_stat,
                        description="artifact encryption passphrase recovery backup",
                        secure_wipe=False,
                    )
                    _fsync_fd(parent_fd)
            else:
                try:
                    os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    _assert_expected_file(
                        backup_name,
                        existing_stat,
                        description="artifact encryption passphrase recovery backup",
                    )
                    _rename_without_replacing(
                        backup_name,
                        path.name,
                        directory_fd=parent_fd,
                        field_name="artifact encryption passphrase file",
                    )
                    backup_created = False
                    _fsync_fd(parent_fd)
                else:
                    raise OSError("artifact encryption passphrase target exists during rollback")

    try:
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="artifact encryption passphrase file directory",
        )
        os.fchmod(parent_fd, 0o700)
        parent_stat = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise ArtifactCryptoError("artifact encryption passphrase file directory must be a directory")
        if hasattr(os, "getuid") and parent_stat.st_uid != os.getuid():
            raise ArtifactCryptoError("artifact encryption passphrase file directory must be owned by the current user")
        if parent_stat.st_mode & 0o077:
            raise ArtifactCryptoError("artifact encryption passphrase file directory must be private")
        _assert_no_posix_acl(path.parent, field_name="artifact encryption passphrase file directory")
        temp_fd, temp_name = _create_private_temp_passphrase_file(parent_fd, path.name)
        assert_fd_is_regular_private_file(
            temp_fd,
            field_name="artifact encryption passphrase temporary file",
            require_private_mode=True,
        )
        file_stat = os.fstat(temp_fd)
        temporary_stat = file_stat
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            raise ArtifactCryptoError("artifact encryption passphrase file must be owned by the current user")
        if file_stat.st_mode & 0o077:
            raise ArtifactCryptoError("artifact encryption passphrase file must be private")
        os.fchmod(temp_fd, 0o600)
        _write_all(temp_fd, payload)
        _fsync_fd(temp_fd)
        temp_fd_to_close = temp_fd
        temp_fd = -1
        os.close(temp_fd_to_close)
        if replace:
            try:
                existing_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing_stat = None
            transaction_active = True
            if existing_stat is not None:
                if not stat.S_ISREG(existing_stat.st_mode) or getattr(existing_stat, "st_nlink", 1) != 1:
                    raise ArtifactCryptoError("artifact encryption passphrase file is not safe to replace")
                previous_payload = _read_previous_passphrase_payload(existing_stat)
                _write_default_passphrase_history(path, previous_payload)
                for _ in range(100):
                    backup_name = f".{path.name}.{secrets.token_hex(8)}.bak"
                    try:
                        os.link(
                            path.name,
                            backup_name,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        backup_name = ""
                        continue
                    try:
                        backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
                        current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                        if (
                            not stat.S_ISREG(backup_stat.st_mode)
                            or getattr(backup_stat, "st_nlink", 1) < 2
                            or not _same_leaf_inode(backup_stat, existing_stat)
                            or not stat.S_ISREG(current_target_stat.st_mode)
                            or not _same_leaf_inode(current_target_stat, existing_stat)
                        ):
                            raise OSError("artifact encryption passphrase file changed during backup activation")
                        backup_created = True
                        _remove_expected_file(
                            path.name,
                            existing_stat,
                            description="artifact encryption passphrase target",
                            secure_wipe=False,
                        )
                        target_removed = True
                        _fsync_fd(parent_fd)
                        break
                    except BaseException as backup_error:
                        if not backup_created:
                            try:
                                candidate_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
                                if _same_leaf_inode(candidate_stat, existing_stat):
                                    _remove_expected_file(
                                        backup_name,
                                        existing_stat,
                                        description="artifact encryption passphrase recovery backup",
                                    )
                                    _fsync_fd(parent_fd)
                            except FileNotFoundError:
                                pass
                            except BaseException as cleanup_backup_error:
                                _note_cleanup_failure(backup_error, cleanup_backup_error)
                        raise
                if not backup_created:
                    raise OSError("artifact encryption passphrase recovery backup could not be created")
            activation_attempted = True
            _rename_without_replacing(
                temp_name,
                path.name,
                directory_fd=parent_fd,
                field_name="artifact encryption passphrase file",
            )
            temp_name = ""
            try:
                activation_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as stat_error:
                raise OSError("artifact encryption passphrase file could not be inspected after activation") from stat_error
            _fsync_fd(parent_fd)
            if backup_created:
                _scrub_temp_passphrase_file(parent_fd, backup_name, expected_stat=existing_stat)
                _remove_expected_file(
                    backup_name,
                    existing_stat,
                    description="artifact encryption passphrase recovery backup",
                )
                _fsync_fd(parent_fd)
                backup_name = ""
                backup_created = False
                transaction_active = False
        else:
            try:
                _rename_without_replacing(
                    temp_name,
                    path.name,
                    directory_fd=parent_fd,
                    field_name="artifact encryption passphrase file",
                )
            except FileExistsError:
                _remove_temporary_file()
                _fsync_fd(parent_fd)
                try:
                    existing_passphrase = _read_private_passphrase_file(
                        path,
                        allow_default_generation=False,
                        rotate_weak_default=False,
                    )
                except BaseException as exc:
                    primary_error = exc
                    raise
            temp_name = ""
        _fsync_fd(parent_fd)
        transaction_active = False
    except FileExistsError:
        if replace and transaction_active and backup_created:
            try:
                if existing_stat is None:
                    raise OSError("artifact encryption passphrase recovery backup identity is unavailable")
                _scrub_temp_passphrase_file(parent_fd, backup_name, expected_stat=existing_stat)
                _remove_expected_file(
                    backup_name,
                    existing_stat,
                    description="artifact encryption passphrase recovery backup",
                )
                _fsync_fd(parent_fd)
                backup_name = ""
                backup_created = False
                transaction_active = False
            except BaseException:
                primary_error = ArtifactCryptoError(
                    "artifact encryption passphrase recovery backup could not be removed"
                )
                deferred_error = primary_error
        if deferred_error is None:
            try:
                existing_passphrase = _read_private_passphrase_file(
                    path,
                    allow_default_generation=False,
                    rotate_weak_default=False,
                )
            except BaseException as exc:
                primary_error = exc
                deferred_error = exc
    except _PassphraseHistoryError as exc:
        primary_error = exc
        try:
            _rollback_passphrase_activation()
        except BaseException as rollback_error:
            _note_cleanup_failure(primary_error, rollback_error)
        raise
    except (OSError, RuntimeError):
        if primary_error is not None:
            raise
        primary_error = ArtifactCryptoError("artifact encryption passphrase file could not be generated")
        try:
            _rollback_passphrase_activation()
        except BaseException as rollback_error:
            _note_cleanup_failure(primary_error, rollback_error)
        deferred_error = primary_error
    except BaseException as exc:
        primary_error = exc
        try:
            _rollback_passphrase_activation()
        except BaseException as rollback_error:
            _note_cleanup_failure(primary_error, rollback_error)
        deferred_error = primary_error
    finally:
        cleanup_failure: BaseException | None = None
        if temp_fd >= 0:
            try:
                os.close(temp_fd)
            except BaseException as exc:
                cleanup_failure = exc
        if temp_name and parent_fd >= 0:
            try:
                _remove_temporary_file()
                _fsync_fd(parent_fd)
            except BaseException as exc:
                with suppress(BaseException):
                    _scrub_temp_passphrase_file(parent_fd, temp_name, expected_stat=temporary_stat)
                if cleanup_failure is None:
                    cleanup_failure = exc
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        if cleanup_failure is not None:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_failure)
            else:
                deferred_error = _temp_passphrase_cleanup_error()
    if deferred_error is not None:
        raise deferred_error
    if existing_passphrase is not None:
        return existing_passphrase
    return passphrase


def _stat_private_passphrase_parent(path: Path) -> None:
    parent_stat: os.stat_result | None = None
    try:
        parent_stat = path.parent.stat()
    except OSError:
        pass
    if parent_stat is None:
        raise ArtifactCryptoError("artifact encryption passphrase file directory could not be inspected")
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ArtifactCryptoError("artifact encryption passphrase file directory must be a directory")
    if hasattr(os, "getuid") and parent_stat.st_uid != os.getuid():
        raise ArtifactCryptoError("artifact encryption passphrase file directory must be owned by the current user")
    if parent_stat.st_mode & 0o077:
        raise ArtifactCryptoError("artifact encryption passphrase file directory must be private")
    _assert_no_posix_acl(path.parent, field_name="artifact encryption passphrase file directory")


def _read_private_passphrase_file(
    path: Path,
    *,
    allow_default_generation: bool = False,
    rotate_weak_default: bool = False,
) -> str:
    resolved_path, path_error = _capture_normal_error(
        lambda: path.expanduser(),
        "artifact encryption passphrase file path could not be resolved",
    )
    if path_error is not None:
        raise path_error
    path = resolved_path
    default_path = default_passphrase_file()
    is_default_path = path == default_path
    if is_default_path and allow_default_generation and not path.exists() and not path.is_symlink():
        return _generate_default_passphrase_file(path)
    def open_passphrase_file() -> int:
        assert_no_symlink_ancestors(path, field_name="artifact encryption passphrase file")
        _stat_private_passphrase_parent(path)
        nonblock_flag = getattr(os, "O_NONBLOCK", 0)
        return open_file_without_following_symlinks(
            path,
            os.O_RDONLY | nonblock_flag,
            field_name="artifact encryption passphrase file",
        )

    fd, open_error = _capture_normal_error(
        open_passphrase_file,
        "artifact encryption passphrase file could not be read",
    )
    if open_error is not None:
        raise open_error
    handle: Any | None = None
    payload: bytes | None = None
    primary_error: BaseException | None = None
    try:
        validation_message: str | None = None
        try:
            assert_fd_is_regular_private_file(
                fd,
                field_name="artifact encryption passphrase file",
                require_private_mode=True,
            )
            file_stat = os.fstat(fd)
        except (OSError, RuntimeError) as exc:
            validation_message = (
                "artifact encryption passphrase file must be private"
                if "must be private" in str(exc)
                else "artifact encryption passphrase file is not private"
            )
        if validation_message is not None:
            primary_error = ArtifactCryptoError(validation_message)
        elif hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            primary_error = ArtifactCryptoError("artifact encryption passphrase file must be owned by the current user")
        elif file_stat.st_mode & 0o077:
            primary_error = ArtifactCryptoError("artifact encryption passphrase file must be private")
        else:
            try:
                _assert_no_posix_acl(path, field_name="artifact encryption passphrase file")
                handle = os.fdopen(fd, "rb")
                fd = -1
                payload = handle.read(MAX_PASSPHRASE_FILE_BYTES + 1)
            except ArtifactCryptoError as exc:
                message = str(exc)
                if "owned" in message:
                    message = "artifact encryption passphrase file must be owned by the current user"
                elif "private" in message:
                    message = "artifact encryption passphrase file must be private"
                else:
                    message = "artifact encryption passphrase file could not be read"
                primary_error = ArtifactCryptoError(message)
            except Exception:
                primary_error = ArtifactCryptoError("artifact encryption passphrase file could not be read")
    except BaseException as exc:
        if primary_error is None:
            primary_error = exc
    finally:
        cleanup_error: BaseException | None = None
        if handle is not None:
            try:
                handle.close()
            except BaseException as exc:
                cleanup_error = exc
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            elif isinstance(cleanup_error, Exception):
                primary_error = ArtifactCryptoError("artifact encryption passphrase file could not be read")
            else:
                primary_error = cleanup_error
    if primary_error is not None:
        raise primary_error
    if payload is None:
        raise ArtifactCryptoError("artifact encryption passphrase file could not be read")
    if len(payload) > MAX_PASSPHRASE_FILE_BYTES:
        raise ArtifactCryptoError("artifact encryption passphrase file is too large")
    decoded_payload, decode_error = _capture_normal_error(
        lambda: payload.decode("utf-8"),
        "artifact encryption passphrase file must be valid UTF-8",
    )
    if decode_error is not None:
        raise decode_error
    passphrase = decoded_payload.rstrip("\r\n")
    if not passphrase or _contains_forbidden_secret_chars(passphrase) or not _passphrase_is_strong(passphrase):
        if is_default_path and rotate_weak_default:
            return _generate_default_passphrase_file(path, replace=True)
        raise ArtifactCryptoError("artifact encryption passphrase file is not strong enough")
    return passphrase


def _explicit_passphrase_file() -> Path | None:
    file_path = os.environ.get(PASSPHRASE_FILE_ENV, "")
    if file_path is None or isinstance(file_path, bool) or not isinstance(file_path, str):
        return None
    normalized = file_path.strip()
    if not normalized:
        return None
    if _contains_forbidden_environment_chars(normalized):
        raise ArtifactCryptoError("artifact encryption passphrase file path contains invalid control character")
    if len(normalized) > MAX_PASSPHRASE_FILE_PATH_CHARS or _safe_utf8_length(
        normalized,
        field_name="artifact encryption passphrase file path",
    ) > MAX_PASSPHRASE_FILE_PATH_CHARS:
        raise ArtifactCryptoError("artifact encryption passphrase file path is too large")
    path, path_error = _capture_normal_error(
        lambda: Path(normalized).expanduser(),
        "artifact encryption passphrase file path could not be resolved",
    )
    if path_error is not None:
        raise path_error
    if not path.is_absolute():
        raise ArtifactCryptoError("artifact encryption passphrase file path must be absolute")
    _, safety_error = _capture_normal_error(
        lambda: assert_safe_path_components(path, field_name="artifact encryption passphrase file path"),
        "artifact encryption passphrase file path is not safe",
    )
    if safety_error is not None:
        raise safety_error
    return path


def _explicit_passphrase_source_configured() -> bool:
    return bool(os.environ.get(PASSPHRASE_ENV)) or _explicit_passphrase_file() is not None


def _configured_passphrase_file(*, include_default: bool = True) -> Path | None:
    explicit_path = _explicit_passphrase_file()
    if explicit_path is not None:
        return explicit_path
    if not include_default:
        return None
    path = default_passphrase_file()
    try:
        if path.exists() or path.is_symlink():
            return path
    except OSError:
        return path
    return None


def _passphrase_from_sources(
    *,
    allow_default_generation: bool,
    rotate_weak_default: bool,
    allow_implicit_default: bool = True,
) -> str | None:
    passphrase_file = _explicit_passphrase_file()
    passphrase_env = os.environ.get(PASSPHRASE_ENV, "")
    if passphrase_file is not None and passphrase_env:
        raise ArtifactCryptoError(
            "configure either artifact encryption passphrase environment or passphrase file, not both"
        )
    if passphrase_file is not None:
        passphrase = _read_private_passphrase_file(
            passphrase_file,
            allow_default_generation=allow_default_generation,
            rotate_weak_default=False,
        )
    else:
        passphrase = passphrase_env
        if not passphrase:
            passphrase_file = _configured_passphrase_file(include_default=allow_implicit_default)
            if passphrase_file is not None:
                passphrase = _read_private_passphrase_file(
                    passphrase_file,
                    allow_default_generation=allow_default_generation,
                    rotate_weak_default=rotate_weak_default and passphrase_file == default_passphrase_file(),
                )
            elif allow_default_generation and allow_implicit_default:
                passphrase = _read_private_passphrase_file(
                    default_passphrase_file(),
                    allow_default_generation=True,
                    rotate_weak_default=rotate_weak_default,
                )
            else:
                return None
    if not passphrase:
        return None
    passphrase_bytes = _safe_utf8_length(
        passphrase,
        field_name="artifact encryption passphrase",
    )
    if len(passphrase) > MAX_PASSPHRASE_CHARS or passphrase_bytes > MAX_PASSPHRASE_CHARS:
        raise ArtifactCryptoError("artifact encryption passphrase is too large")
    if _contains_forbidden_secret_chars(passphrase):
        raise ArtifactCryptoError("artifact encryption passphrase contains invalid control characters")
    if not _passphrase_is_strong(passphrase):
        raise ArtifactCryptoError("artifact encryption passphrase is not strong enough")
    return passphrase


def _derive_passphrase_key(passphrase: str, salt: bytes) -> bytes:
    if len(salt) != SALT_SIZE_BYTES:
        raise ArtifactCryptoError("artifact encryption salt has invalid length")
    _invalid_tag, _aesgcm, scrypt = _crypto_backend()
    kdf = scrypt(salt=salt, length=KEY_SIZE_BYTES, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def _secret_tool_path() -> str:
    path = shutil.which("secret-tool", path=_TRUSTED_COMMAND_PATH)
    if not path:
        raise ArtifactCryptoError("Secret Service keyring helper secret-tool is not installed")
    return path


def _filtered_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if (
            key in _ALLOWED_SECRET_TOOL_ENV
            and isinstance(value, str)
            and _is_valid_utf8(value)
            and not _contains_forbidden_environment_chars(value)
        )
    }
    runtime_dir = _safe_xdg_runtime_dir(env.get("XDG_RUNTIME_DIR", ""))
    if runtime_dir is None:
        env.pop("XDG_RUNTIME_DIR", None)
    dbus_address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if runtime_dir is not None and _safe_dbus_session_bus_address(dbus_address, runtime_dir):
        env["DBUS_SESSION_BUS_ADDRESS"] = dbus_address
    env["PATH"] = _TRUSTED_COMMAND_PATH
    return env


def _canonical_xdg_runtime_dir() -> Path | None:
    if not hasattr(os, "getuid"):
        return None
    return Path("/run/user") / str(os.getuid())


def _safe_xdg_runtime_dir(value: str) -> Path | None:
    if not value or _contains_forbidden_environment_chars(value):
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    canonical = _canonical_xdg_runtime_dir()
    if canonical is None or path != canonical:
        return None
    try:
        if path.is_symlink():
            return None
    except OSError:
        return None
    try:
        path_stat = path.stat()
    except OSError:
        return None
    if not stat.S_ISDIR(path_stat.st_mode):
        return None
    if hasattr(os, "getuid") and path_stat.st_uid != os.getuid():
        return None
    if path_stat.st_mode & 0o077:
        return None
    return path


def _safe_dbus_session_bus_address(value: str, runtime_dir: Path) -> bool:
    if not value or _contains_forbidden_environment_chars(value):
        return False
    if not value.startswith(_SAFE_DBUS_SESSION_PREFIX):
        return False
    address_body = value[len(_SAFE_DBUS_SESSION_PREFIX):]
    bus_path_text, separator, parameters = address_body.partition(",")
    if separator and (
        not parameters.startswith("guid=")
        or len(parameters) != len("guid=") + 32
        or any(char not in "0123456789abcdefABCDEF" for char in parameters[len("guid="):])
    ):
        return False
    bus_path = Path(bus_path_text)
    if not bus_path.is_absolute():
        return False
    if bus_path != runtime_dir / "bus":
        return False
    try:
        bus_stat = os.lstat(bus_path)
    except OSError:
        return False
    if stat.S_ISLNK(bus_stat.st_mode) or not stat.S_ISSOCK(bus_stat.st_mode):
        return False
    if hasattr(os, "getuid") and bus_stat.st_uid != os.getuid():
        return False
    return True


def _read_secret_tool_output(handle: Any, *, field_name: str) -> bytes:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size > MAX_SECRET_TOOL_OUTPUT_BYTES:
        raise ArtifactCryptoError(f"Secret Service keyring {field_name} exceeded safe output limit")
    handle.seek(0)
    payload = handle.read(MAX_SECRET_TOOL_OUTPUT_BYTES + 1)
    if not isinstance(payload, bytes) or len(payload) > MAX_SECRET_TOOL_OUTPUT_BYTES:
        raise ArtifactCryptoError(f"Secret Service keyring {field_name} exceeded safe output limit")
    return payload


def _validate_secret_tool_output(payload: object, *, field_name: str) -> bytes:
    if not isinstance(payload, bytes) or len(payload) > MAX_SECRET_TOOL_OUTPUT_BYTES:
        raise ArtifactCryptoError(f"Secret Service keyring {field_name} exceeded safe output limit")
    return payload


def _validate_secret_tool_text(value: object, *, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ArtifactCryptoError(f"Secret Service keyring {field_name} must be text")
    if not value:
        raise ArtifactCryptoError(f"Secret Service keyring {field_name} must not be empty")
    if len(value) > MAX_SECRET_TOOL_ARG_CHARS or _safe_utf8_length(
        value,
        field_name=f"Secret Service keyring {field_name}",
    ) > MAX_SECRET_TOOL_ARG_CHARS:
        raise ArtifactCryptoError(f"Secret Service keyring {field_name} is too large")
    if _contains_forbidden_environment_chars(value):
        raise ArtifactCryptoError(f"Secret Service keyring {field_name} contains invalid control character")
    return value


def _validate_secret_tool_args(args: object) -> list[str]:
    if isinstance(args, bool) or not isinstance(args, list) or not args:
        raise ArtifactCryptoError("Secret Service keyring arguments must be a non-empty list")
    validated = [_validate_secret_tool_text(arg, field_name="argument") for arg in args]
    if validated[0] not in _SECRET_TOOL_COMMANDS:
        raise ArtifactCryptoError("Secret Service keyring command is not allowed")
    return validated


def _secret_tool_same_session_process_group_ids(session_id: int) -> set[int] | None:
    if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id <= 0:
        return None
    try:
        proc_entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    process_group_ids: set[int] = set()
    scan_incomplete = False
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        try:
            raw = _read_proc_stat_path(proc_entry.joinpath("stat"))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            scan_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_group = int(fields[2])
            member_session_id = int(fields[3])
        except (IndexError, ValueError):
            scan_incomplete = True
            continue
        if member_session_id != session_id:
            continue
        if process_group <= 0:
            scan_incomplete = True
            continue
        process_group_ids.add(process_group)
    if scan_incomplete:
        return None
    return process_group_ids


def _secret_tool_process_group_has_live_descendants(process_group_id: int) -> bool | None:
    if not isinstance(process_group_id, int) or isinstance(process_group_id, bool) or process_group_id <= 0:
        return None
    try:
        proc_entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    scan_incomplete = False
    group_live = False
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        process_id = int(proc_entry.name)
        try:
            raw = _read_proc_stat_path(proc_entry.joinpath("stat"))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            scan_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_state = fields[0]
            process_group = int(fields[2])
            session_id = int(fields[3])
        except (IndexError, ValueError):
            scan_incomplete = True
            continue
        if session_id != process_group_id:
            continue
        if process_group != process_group_id:
            if process_state not in {"Z", "X", "x"}:
                group_live = True
            continue
        if process_id != process_group_id and process_state not in {"Z", "X", "x"}:
            group_live = True
    if scan_incomplete:
        return None
    return group_live


def _secret_tool_leader_is_gone_or_zombie(process_id: int) -> bool:
    try:
        raw = _read_proc_stat(process_id)
        close = raw.rindex(")")
        process_state = raw[close + 2 :].split()[0]
    except FileNotFoundError:
        return True
    except (OSError, UnicodeDecodeError, IndexError, ValueError):
        return False
    return process_state in {"Z", "X", "x"}


def _secret_tool_process_start_time(pid: object) -> str | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        raw = _read_proc_stat(pid)
        close = raw.rindex(")")
        start_time = raw[close + 2 :].split()[19]
    except (FileNotFoundError, OSError, UnicodeDecodeError, IndexError, ValueError):
        return None
    if not start_time.isascii() or not start_time.isdigit():
        return None
    return start_time


def _stop_secret_tool_process(proc: subprocess.Popen[bytes]) -> bool:
    process_identity = getattr(proc, "_soc_process_identity", None)
    if isinstance(process_identity, str) and process_identity:
        try:
            terminated = _terminate_output_process_group(proc)
        except BaseException:
            return False
        if terminated is not True:
            return False
        try:
            proc.wait(timeout=1)
        except BaseException:
            return False
        return True

    # Without boot-id identity, only signal the exact process through PIDFD.
    # Never infer a process group from a bare, reusable PID.
    pid = getattr(proc, "pid", None)
    start_time = _secret_tool_process_start_time(pid)
    if start_time is None or not isinstance(pid, int) or isinstance(pid, bool):
        return False
    try:
        if _kill_output_process_with_pidfd(pid, start_time) is not True:
            return False
        proc.wait(timeout=1)
    except BaseException:
        return False
    return True


def _stop_secret_tool_process_and_note(
    proc: subprocess.Popen[bytes],
    primary_error: BaseException,
) -> None:
    if not _stop_secret_tool_process(proc):
        _note_cleanup_failure(
            primary_error,
            ArtifactCryptoError("Secret Service keyring helper process could not be stopped safely"),
        )


def _read_secret_tool_pipes_bounded(
    proc: subprocess.Popen[bytes],
    *,
    deadline: float | None = None,
) -> tuple[bytes, bytes]:
    stream_error = False
    try:
        stdout = proc.stdout
        stderr = proc.stderr
    except Exception:
        stream_error = True
    if stream_error:
        error = ArtifactCryptoError("Secret Service keyring helper output could not be captured safely")
        _stop_secret_tool_process_and_note(proc, error)
        raise error
    if stdout is None or stderr is None:
        error = ArtifactCryptoError("Secret Service keyring helper output could not be captured safely")
        _stop_secret_tool_process_and_note(proc, error)
        raise error
    outputs: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    streams = ((stdout, "stdout"), (stderr, "stderr"))
    active: dict[int, str] = {}
    for stream, field_name in streams:
        stream_info, error = _capture_normal_error(
            lambda stream=stream: (stream.fileno(), os.set_blocking(stream.fileno(), False)),
            "Secret Service keyring helper output could not be captured safely",
        )
        if error is not None:
            _stop_secret_tool_process_and_note(proc, error)
            raise error
        fd = stream_info[0]
        active[fd] = field_name
    if deadline is None:
        deadline = time.monotonic() + _SECRET_TOOL_TIMEOUT_SECONDS
    while active:
        if time.monotonic() >= deadline:
            error = ArtifactCryptoError("Secret Service keyring request timed out")
            _stop_secret_tool_process_and_note(proc, error)
            raise error
        progressed = False
        for fd, field_name in list(active.items()):
            read_failed = False
            try:
                chunk = os.read(fd, 8192)
            except BlockingIOError:
                continue
            except InterruptedError:
                continue
            except Exception:
                read_failed = True
            if read_failed:
                error = ArtifactCryptoError("Secret Service keyring helper output could not be captured safely")
                _stop_secret_tool_process_and_note(proc, error)
                raise error
            if not chunk:
                active.pop(fd, None)
                progressed = True
                continue
            progressed = True
            output_failed = False
            try:
                outputs[field_name].extend(chunk)
            except Exception:
                output_failed = True
            if output_failed:
                error = ArtifactCryptoError("Secret Service keyring helper output could not be captured safely")
                _stop_secret_tool_process_and_note(proc, error)
                raise error
            if len(outputs[field_name]) > MAX_SECRET_TOOL_OUTPUT_BYTES:
                error = ArtifactCryptoError(f"Secret Service keyring {field_name} exceeded safe output limit")
                _stop_secret_tool_process_and_note(proc, error)
                raise error
        if not progressed:
            time.sleep(0.01)
    return bytes(outputs["stdout"]), bytes(outputs["stderr"])


def _run_secret_tool(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[bytes]:
    args = _validate_secret_tool_args(args)
    if input_text is not None:
        input_text = _validate_secret_tool_text(input_text, field_name="input")
    command = [_secret_tool_path(), *args]
    env = _filtered_environment()
    proc: subprocess.Popen[bytes] | None = None
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    result: subprocess.CompletedProcess[bytes] | None = None
    try:
        start_failed = False
        try:
            proc = subprocess.Popen(  # nosec B603
                command,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=env,
                start_new_session=True,
            )
            proc_pid = getattr(proc, "pid", None)
            if isinstance(proc_pid, int) and not isinstance(proc_pid, bool) and proc_pid > 0:
                setattr(proc, "_soc_process_identity", _clipboard_lock_identity_for_pid(proc_pid) or "")
        except (OSError, ValueError):
            start_failed = True
        if start_failed:
            raise ArtifactCryptoError("Secret Service keyring helper could not be started")
        deadline = time.monotonic() + _SECRET_TOOL_TIMEOUT_SECONDS
        if input_text is not None:
            stdin, input_error = _capture_normal_error(
                lambda: proc.stdin,
                "Secret Service keyring helper input could not be sent safely",
            )
            if input_error is not None:
                _stop_secret_tool_process_and_note(proc, input_error)
                raise input_error
            if stdin is None:
                error = ArtifactCryptoError("Secret Service keyring helper input could not be sent safely")
                _stop_secret_tool_process_and_note(proc, error)
                raise error
            _, input_error = _capture_normal_error(
                lambda: (stdin.write(input_text.encode("utf-8")), stdin.close()),
                "Secret Service keyring helper input could not be sent safely",
            )
            if input_error is not None:
                _stop_secret_tool_process_and_note(proc, input_error)
                raise input_error
        stdout, stderr = _read_secret_tool_pipes_bounded(proc, deadline=deadline)
        timed_out = False
        wait_failed = False
        returncode: int | None = None
        try:
            returncode = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception:
            wait_failed = True
        if timed_out:
            primary_error = ArtifactCryptoError("Secret Service keyring request timed out")
            _stop_secret_tool_process_and_note(proc, primary_error)
        elif wait_failed:
            primary_error = ArtifactCryptoError("Secret Service keyring helper could not be reaped safely")
            _stop_secret_tool_process_and_note(proc, primary_error)
        else:
            stdout = _validate_secret_tool_output(stdout, field_name="stdout")
            stderr = _validate_secret_tool_output(stderr, field_name="stderr")
            result = subprocess.CompletedProcess(command, returncode, stdout, stderr)
    except BaseException as exc:
        primary_error = exc
        if proc is not None and not isinstance(exc, Exception):
            _stop_secret_tool_process_and_note(proc, primary_error)
    finally:
        if proc is not None:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as close_error:
                        if cleanup_error is None:
                            cleanup_error = close_error
    if cleanup_error is not None:
        if primary_error is not None:
            _note_cleanup_failure(primary_error, cleanup_error)
        elif isinstance(cleanup_error, Exception):
            primary_error = ArtifactCryptoError("Secret Service keyring helper streams could not be closed safely")
        else:
            primary_error = cleanup_error
    if primary_error is not None:
        raise primary_error
    if result is None:
        raise ArtifactCryptoError("Secret Service keyring helper failed")
    return result


def _parse_keyring_secret(raw: bytes) -> bytes | None:
    text = raw.decode("utf-8", errors="strict").strip()
    if not text:
        return None
    key = _b64decode(text, field_name="keyring key")
    if len(key) != KEY_SIZE_BYTES:
        raise ArtifactCryptoError("Secret Service keyring returned a key with invalid length")
    return key


def _lookup_keyring_key() -> bytes | None:
    proc = _run_secret_tool(["lookup", *_SECRET_TOOL_ATTRIBUTES])
    if proc.returncode != 0:
        if proc.stdout or proc.stderr:
            raise ArtifactCryptoError("Secret Service keyring lookup failed")
        return None
    decoded: bytes | None = None
    invalid_utf8 = False
    try:
        decoded = _parse_keyring_secret(proc.stdout)
    except UnicodeDecodeError:
        invalid_utf8 = True
    if invalid_utf8:
        raise ArtifactCryptoError("Secret Service keyring returned invalid UTF-8")
    return decoded


def _store_keyring_key(key: bytes) -> None:
    encoded = _b64encode(key)
    proc = _run_secret_tool(
        ["store", "--label", f"{APP_NAME} artifact encryption key", *_SECRET_TOOL_ATTRIBUTES],
        input_text=encoded,
    )
    if proc.returncode != 0:
        raise ArtifactCryptoError("Secret Service keyring could not store the artifact encryption key")


@contextmanager
def _keyring_initialization_lock():
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow_flag, int) or isinstance(nofollow_flag, bool) or nofollow_flag <= 0:
        raise ArtifactCryptoError("Secret Service keyring initialization locking is not supported")
    lock_path = config_dir() / KEYRING_INITIALIZATION_LOCK_FILE_NAME
    assert_no_symlink_ancestors(lock_path, field_name="artifact encryption keyring lock")
    parent_fd = -1
    lock_fd = -1
    acquired = False
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            lock_path.parent,
            field_name="artifact encryption keyring lock directory",
        )
        assert_fd_is_private_directory(parent_fd, field_name="artifact encryption keyring lock directory")
        lock_fd = os.open(
            lock_path.name,
            os.O_RDWR | os.O_CREAT | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        assert_fd_is_regular_private_file(lock_fd, field_name="artifact encryption keyring lock")
        deadline = time.monotonic() + KEYRING_INITIALIZATION_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ArtifactCryptoError("Secret Service keyring initialization lock timed out")
                time.sleep(0.01)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise ArtifactCryptoError("Secret Service keyring initialization lock failed") from None
            else:
                acquired = True
                break
        yield
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, ArtifactCryptoError):
            raise
        raise ArtifactCryptoError("Secret Service keyring initialization lock failed") from None
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        if acquired:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except BaseException as exc:
                cleanup_error = exc
        if lock_fd >= 0:
            try:
                os.close(lock_fd)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise ArtifactCryptoError("Secret Service keyring initialization lock cleanup failed") from None


def _load_keyring_key() -> bytes:
    existing = _lookup_keyring_key()
    if existing is not None:
        return existing
    with _keyring_initialization_lock():
        existing = _lookup_keyring_key()
        if existing is not None:
            return existing
        key = secrets.token_bytes(KEY_SIZE_BYTES)
        _store_keyring_key(key)
        confirmed = _lookup_keyring_key()
        if confirmed is None:
            raise ArtifactCryptoError("Secret Service keyring did not return the stored artifact encryption key")
        if confirmed != key:
            raise ArtifactCryptoError("Secret Service keyring returned a different artifact encryption key after storing")
        return confirmed


def _passphrase_key_for_encryption(*, allow_implicit_default: bool = True) -> tuple[bytes, bytes]:
    passphrase = _passphrase_from_sources(
        allow_default_generation=allow_implicit_default,
        rotate_weak_default=allow_implicit_default,
        allow_implicit_default=allow_implicit_default,
    )
    if not passphrase:
        raise ArtifactCryptoError(
            f"artifact encryption passphrase is not configured; create {default_passphrase_file()} with mode 0600, "
            f"or set {PASSPHRASE_FILE_ENV} or {PASSPHRASE_ENV}"
        )
    salt = secrets.token_bytes(SALT_SIZE_BYTES)
    return _derive_passphrase_key(passphrase, salt), salt


def _passphrase_key_for_decryption(salt: bytes) -> bytes:
    return _derive_passphrase_key(_passphrase_for_decryption(), salt)


def _passphrase_for_decryption() -> str:
    passphrase = _passphrase_from_sources(
        allow_default_generation=False,
        rotate_weak_default=False,
        allow_implicit_default=True,
    )
    if not passphrase:
        raise ArtifactCryptoError(
            f"artifact decryption passphrase is not configured; create {default_passphrase_file()} with mode 0600, "
            f"or set {PASSPHRASE_FILE_ENV} or {PASSPHRASE_ENV}"
        )
    return passphrase


def _passphrase_history_for_decryption(current_passphrase: str) -> list[str]:
    if os.environ.get(PASSPHRASE_ENV):
        return []
    explicit_file = _explicit_passphrase_file()
    if explicit_file is not None and explicit_file != default_passphrase_file():
        return []
    try:
        history = _read_default_passphrase_history(default_passphrase_file())
    except _PassphraseHistoryError:
        return []
    return [value for value in history if value != current_passphrase]


def _key_for_encryption(requested_mode: str) -> tuple[KeyMaterial, dict[str, object]]:
    mode = normalize_artifact_encryption(requested_mode)
    if mode == ARTIFACT_ENCRYPTION_OFF:
        raise ArtifactCryptoError("artifact encryption is disabled")
    if mode == ARTIFACT_ENCRYPTION_PASSPHRASE:
        key, salt = _passphrase_key_for_encryption()
        return KeyMaterial(mode=ARTIFACT_ENCRYPTION_PASSPHRASE, key=key), {
            "kdf": "scrypt",
            "salt": _b64encode(salt),
            "scrypt_n": SCRYPT_N,
            "scrypt_r": SCRYPT_R,
            "scrypt_p": SCRYPT_P,
        }
    try:
        return KeyMaterial(mode=ARTIFACT_ENCRYPTION_KEYRING, key=_load_keyring_key()), {"kdf": "none"}
    except ArtifactCryptoError as keyring_error:
        raise ArtifactCryptoError(
            "Secret Service keyring is unavailable; choose passphrase mode explicitly if keyring storage is not usable"
        ) from keyring_error


def _key_for_decryption(envelope: dict[str, object]) -> KeyMaterial:
    mode = normalize_artifact_encryption(envelope.get("mode", ""))
    if mode == ARTIFACT_ENCRYPTION_PASSPHRASE:
        salt = _b64decode(envelope.get("salt", ""), field_name="salt")
        return KeyMaterial(mode=mode, key=_passphrase_key_for_decryption(salt))
    if mode == ARTIFACT_ENCRYPTION_KEYRING:
        existing_key = _lookup_keyring_key()
        if existing_key is None:
            raise ArtifactCryptoError("Secret Service keyring does not contain the artifact encryption key")
        return KeyMaterial(mode=mode, key=existing_key)
    raise ArtifactCryptoError("encrypted artifact mode is invalid")


def encrypt_bytes(payload: bytes, requested_mode: object, *, kind: str) -> tuple[bytes, str]:
    if isinstance(payload, bool) or not isinstance(payload, bytes):
        raise ArtifactCryptoError("artifact payload must be bytes")
    mode = normalize_artifact_encryption(requested_mode)
    if mode == ARTIFACT_ENCRYPTION_OFF:
        return payload, ARTIFACT_ENCRYPTION_OFF
    if len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES:
        raise ArtifactCryptoError("artifact payload is too large")
    safe_kind = _normalize_kind(kind)
    key_material, metadata = _key_for_encryption(mode)
    _invalid_tag, aesgcm, _scrypt = _crypto_backend()
    nonce = secrets.token_bytes(NONCE_SIZE_BYTES)
    salt = _b64decode(metadata["salt"], field_name="salt") if key_material.mode == ARTIFACT_ENCRYPTION_PASSPHRASE else b""
    try:
        aad = _aad(
            safe_kind,
            version=ENVELOPE_VERSION,
            algorithm=ENVELOPE_ALGORITHM,
            mode=key_material.mode,
            salt=salt,
            nonce=nonce,
        )
        ciphertext = aesgcm(key_material.key).encrypt(nonce, payload, aad)
    except (MemoryError, RecursionError):
        ciphertext = b""
        aad_error = True
    else:
        aad_error = False
    if aad_error:
        raise ArtifactCryptoError("encrypted artifact envelope could not be rendered")
    envelope: dict[str, object] = {
        "magic": ENVELOPE_MAGIC,
        "version": ENVELOPE_VERSION,
        "algorithm": ENVELOPE_ALGORITHM,
        "mode": key_material.mode,
        "kind": safe_kind,
        "nonce": _b64encode(nonce),
        "ciphertext": _b64encode(ciphertext),
        **metadata,
    }
    rendered, render_error = _capture_normal_error(
        lambda: json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
        "encrypted artifact envelope could not be rendered",
    )
    if render_error is not None:
        raise render_error
    if len(rendered) > MAX_ENCRYPTED_ARTIFACT_BYTES:
        raise ArtifactCryptoError("encrypted artifact payload is too large")
    return rendered, key_material.mode


def decrypt_bytes(payload: bytes, *, kind: str, require_encrypted: bool = True) -> bytes:
    if isinstance(payload, bool) or not isinstance(payload, bytes):
        raise ArtifactCryptoError("artifact payload must be bytes")
    if len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES and require_encrypted:
        raise ArtifactCryptoError("artifact payload is too large")
    if len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES and _is_json_like_payload(payload):
        raise ArtifactCryptoError("encrypted artifact payload is too large")
    if len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES and not require_encrypted:
        return payload
    parse_failed = False
    try:
        envelope = json.loads(payload.decode("utf-8"), parse_constant=_reject_non_finite_json_number)
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        parse_failed = True
    if parse_failed:
        if require_encrypted:
            raise ArtifactCryptoError("encrypted artifact envelope is missing")
        return payload
    if not _is_encrypted_envelope(envelope):
        if require_encrypted:
            raise ArtifactCryptoError("encrypted artifact envelope is missing")
        return payload
    if not isinstance(envelope, dict):
        raise ArtifactCryptoError("encrypted artifact envelope must be an object")
    version = envelope.get("version")
    if envelope.get("magic") != ENVELOPE_MAGIC or isinstance(version, bool) or not isinstance(version, int):
        raise ArtifactCryptoError("encrypted artifact envelope version is unsupported")
    if version not in {LEGACY_ENVELOPE_VERSION, ENVELOPE_VERSION}:
        raise ArtifactCryptoError("encrypted artifact envelope version is unsupported")
    if envelope.get("algorithm") != ENVELOPE_ALGORITHM:
        raise ArtifactCryptoError("encrypted artifact algorithm is unsupported")
    safe_kind = _normalize_kind(kind)
    if envelope.get("kind") != safe_kind:
        raise ArtifactCryptoError("encrypted artifact kind does not match the requested use")
    mode = normalize_artifact_encryption(envelope.get("mode", ""))
    if version == ENVELOPE_VERSION:
        expected_fields = {
            "magic",
            "version",
            "algorithm",
            "mode",
            "kind",
            "nonce",
            "ciphertext",
            "kdf",
        }
        if mode == ARTIFACT_ENCRYPTION_PASSPHRASE:
            expected_fields.update({"salt", "scrypt_n", "scrypt_r", "scrypt_p"})
        if set(envelope) != expected_fields:
            raise ArtifactCryptoError("encrypted artifact envelope metadata is unsupported")
        if mode == ARTIFACT_ENCRYPTION_PASSPHRASE:
            if (
                envelope.get("kdf") != "scrypt"
                or envelope.get("scrypt_n") != SCRYPT_N
                or envelope.get("scrypt_r") != SCRYPT_R
                or envelope.get("scrypt_p") != SCRYPT_P
            ):
                raise ArtifactCryptoError("encrypted artifact envelope metadata is unsupported")
        elif envelope.get("kdf") != "none":
            raise ArtifactCryptoError("encrypted artifact envelope metadata is unsupported")
    nonce = _b64decode(envelope.get("nonce", ""), field_name="nonce")
    if len(nonce) != NONCE_SIZE_BYTES:
        raise ArtifactCryptoError("encrypted artifact nonce has invalid length")
    ciphertext = _b64decode(envelope.get("ciphertext", ""), field_name="ciphertext")
    salt = b""
    current_passphrase: str | None = None
    if mode == ARTIFACT_ENCRYPTION_PASSPHRASE:
        salt = _b64decode(envelope.get("salt", ""), field_name="salt")
        if len(salt) != SALT_SIZE_BYTES:
            raise ArtifactCryptoError("encrypted artifact salt has invalid length")
        current_passphrase = _passphrase_for_decryption()
        keys = [KeyMaterial(mode=mode, key=_derive_passphrase_key(current_passphrase, salt))]
    elif mode == ARTIFACT_ENCRYPTION_KEYRING:
        keys = [_key_for_decryption(envelope)]
    else:
        raise ArtifactCryptoError("encrypted artifact mode is invalid")
    invalid_tag, aesgcm, _scrypt = _crypto_backend()
    aad = _aad(
        safe_kind,
        version=version,
        algorithm=ENVELOPE_ALGORITHM,
        mode=mode if version == ENVELOPE_VERSION else "",
        salt=salt if version == ENVELOPE_VERSION else b"",
        nonce=nonce if version == ENVELOPE_VERSION else b"",
    )
    for key_material in keys:
        try:
            return aesgcm(key_material.key).decrypt(nonce, ciphertext, aad)
        except invalid_tag:
            continue
    if mode == ARTIFACT_ENCRYPTION_PASSPHRASE and current_passphrase is not None:
        for previous_passphrase in _passphrase_history_for_decryption(current_passphrase):
            previous_key = _derive_passphrase_key(previous_passphrase, salt)
            try:
                return aesgcm(previous_key).decrypt(nonce, ciphertext, aad)
            except invalid_tag:
                continue
    raise ArtifactCryptoError("encrypted artifact authentication failed")


def _same_private_file_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
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


def _same_private_file_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_mode,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_mode,
    )


def read_private_bytes(
    path: Path,
    *,
    field_name: str,
    max_bytes: int | None = None,
    expected_stat: os.stat_result | None = None,
) -> bytes:
    public_field_name = _safe_public_field_label(field_name)
    if not isinstance(path, Path):
        raise ArtifactCryptoError(f"{public_field_name} must be a path")
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0):
        raise ArtifactCryptoError("max_bytes must be a non-negative integer")
    if max_bytes is not None and max_bytes > MAX_ENCRYPTED_ARTIFACT_BYTES:
        raise ArtifactCryptoError(
            f"max_bytes must not exceed {MAX_ENCRYPTED_ARTIFACT_BYTES} bytes"
        )
    effective_max_bytes = MAX_ENCRYPTED_ARTIFACT_BYTES if max_bytes is None else max_bytes
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise ArtifactCryptoError(f"secure {public_field_name} open is not supported")
    open_failed = False
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
        fd = open_file_without_following_symlinks(
            path,
            os.O_RDONLY | nofollow_flag | getattr(os, "O_NONBLOCK", 0),
            field_name=field_name,
        )
    except (OSError, RuntimeError):
        open_failed = True
    if open_failed:
        raise ArtifactCryptoError(f"failed to read {public_field_name}")
    handle: Any | None = None
    primary_error: BaseException | None = None
    try:
        assert_fd_is_regular_private_file(fd, field_name=field_name)
        opened_stat: os.stat_result | None = None
        if expected_stat is not None:
            opened_stat = os.fstat(fd)
            if not _same_private_file_snapshot(opened_stat, expected_stat):
                raise OSError(f"{public_field_name} changed before reading")
            current_path_stat = path.lstat()
            if not _same_private_file_inode(current_path_stat, opened_stat):
                raise OSError(f"{public_field_name} changed before reading")
        handle = os.fdopen(fd, "rb")
        fd = -1
        data = handle.read(effective_max_bytes + 1)
        if expected_stat is not None and opened_stat is not None:
            final_stat = os.fstat(handle.fileno())
            if not _same_private_file_snapshot(final_stat, opened_stat):
                raise OSError(f"{public_field_name} changed while reading")
            final_path_stat = path.lstat()
            if not _same_private_file_inode(final_path_stat, final_stat):
                raise OSError(f"{public_field_name} changed while reading")
    except Exception:
        primary_error = ArtifactCryptoError(f"failed to read {public_field_name}")
    except BaseException as exc:
        primary_error = exc
    finally:
        cleanup_error: BaseException | None = None
        if handle is not None:
            try:
                handle.close()
            except BaseException as exc:
                cleanup_error = exc
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            elif isinstance(cleanup_error, Exception):
                primary_error = ArtifactCryptoError(f"failed to read {public_field_name}")
            else:
                primary_error = cleanup_error
    if primary_error is not None:
        raise primary_error
    if len(data) > effective_max_bytes:
        raise ArtifactCryptoError(f"{public_field_name} is too large")
    return data


def write_encrypted_bytes_atomically(path: Path, payload: bytes, requested_mode: object, *, kind: str, field_name: str) -> tuple[Path, str]:
    public_field_name = _safe_public_field_label(field_name)
    mode = normalize_artifact_encryption(requested_mode)
    target_path = path if mode == ARTIFACT_ENCRYPTION_OFF else encrypted_path_for(path)
    encrypted_payload, effective_mode = encrypt_bytes(payload, mode, kind=kind)
    write_failed = False
    try:
        write_bytes_atomically_without_following_symlinks(target_path, encrypted_payload, field_name=field_name)
    except (OSError, RuntimeError):
        write_failed = True
    if write_failed:
        raise ArtifactCryptoError(f"failed to write encrypted {public_field_name}")
    return target_path, effective_mode


def read_decrypted_bytes_from_file(
    path: Path,
    *,
    kind: str,
    field_name: str,
    max_bytes: int | None = None,
    require_encrypted: bool = True,
    expected_stat: os.stat_result | None = None,
) -> bytes:
    data = read_private_bytes(
        path,
        field_name=field_name,
        max_bytes=max_bytes,
        expected_stat=expected_stat,
    )
    return decrypt_bytes(data, kind=kind, require_encrypted=require_encrypted)
