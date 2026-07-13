from __future__ import annotations

import base64
import errno
import json
import os
import secrets
import shutil
import stat
import subprocess  # nosec B404
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import APP_ID, APP_NAME, config_dir
from .path_safety import (
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
    open_file_without_following_symlinks,
    write_bytes_atomically_without_following_symlinks,
)

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
ENVELOPE_VERSION = 1
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


def _safe_utf8_length(value: str, *, field_name: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ArtifactCryptoError(f"{field_name} must be valid UTF-8") from exc


class ArtifactCryptoError(RuntimeError):
    pass


def _crypto_backend() -> tuple[type[BaseException], Any, Any]:
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise ArtifactCryptoError(
            "cryptography is required for artifact encryption; install python3-cryptography or disable encryption"
        ) from exc
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
    try:
        return aliases[normalized]
    except KeyError as exc:
        choices = ", ".join(ARTIFACT_ENCRYPTION_CHOICES)
        raise ArtifactCryptoError(f"unsupported artifact encryption mode: {value}; choose one of: {choices}") from exc


def encryption_enabled(value: object) -> bool:
    return normalize_artifact_encryption(value) != ARTIFACT_ENCRYPTION_OFF


def encrypted_path_for(path: Path) -> Path:
    if not isinstance(path, Path):
        raise ArtifactCryptoError("encrypted artifact path must be a Path")
    if _contains_forbidden_environment_chars(str(path)):
        raise ArtifactCryptoError("encrypted artifact path is not safe")
    try:
        assert_safe_path_components(path, field_name="encrypted artifact path")
    except RuntimeError as exc:
        raise ArtifactCryptoError("encrypted artifact path is not safe") from exc
    if path.name.endswith(ENCRYPTED_SUFFIX):
        return path
    return path.with_name(path.name + ENCRYPTED_SUFFIX)


def is_encrypted_path(path: Path) -> bool:
    return isinstance(path, Path) and path.name.endswith(ENCRYPTED_SUFFIX)


def is_encrypted_payload(payload: bytes) -> bool:
    if isinstance(payload, bool) or not isinstance(payload, bytes):
        return False
    if len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES:
        return False
    stripped = payload.lstrip()
    if not stripped.startswith(b"{"):
        return False
    try:
        envelope = json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(envelope, dict)
        and envelope.get("magic") == ENVELOPE_MAGIC
        and envelope.get("version") == ENVELOPE_VERSION
        and isinstance(envelope.get("ciphertext"), str)
    )


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


def _aad(kind: str) -> bytes:
    safe_kind = _normalize_kind(kind)
    return f"{APP_ID}:{safe_kind}:v{ENVELOPE_VERSION}".encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64decode(value: object, *, field_name: str) -> bytes:
    if isinstance(value, bool) or not isinstance(value, str) or not value:
        raise ArtifactCryptoError(f"encrypted artifact {field_name} is invalid")
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except Exception as exc:
        raise ArtifactCryptoError(f"encrypted artifact {field_name} is invalid") from exc


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


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(payload):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _create_private_temp_passphrase_file(parent_fd: int, final_name: str) -> tuple[int, str]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise ArtifactCryptoError("secure artifact encryption passphrase temporary file creation is not supported")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag
    safe_name = final_name.replace("/", "_") or DEFAULT_PASSPHRASE_FILE_NAME
    for _ in range(100):
        temp_name = f".{safe_name}.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(temp_name, flags, 0o600, dir_fd=parent_fd), temp_name
        except FileExistsError:
            continue
    raise ArtifactCryptoError("artifact encryption passphrase temporary file could not be created")


def _fsync_fd(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file could not be synchronized") from exc


def _has_posix_acl(path: Path) -> bool:
    try:
        os.getxattr(path, _ACL_XATTR, follow_symlinks=False)
    except AttributeError:
        return False
    except OSError as exc:
        if exc.errno in {
            getattr(errno, "ENODATA", 61),
            getattr(errno, "ENOATTR", 93),
            errno.EOPNOTSUPP,
            errno.ENOTSUP,
        }:
            return False
        raise ArtifactCryptoError("artifact encryption passphrase file ACL could not be inspected") from exc
    return True


def _assert_no_posix_acl(path: Path, *, field_name: str) -> None:
    if _has_posix_acl(path):
        raise ArtifactCryptoError(f"{field_name} must not have extended ACL permissions")


def _temp_passphrase_cleanup_error() -> ArtifactCryptoError:
    return ArtifactCryptoError("artifact encryption passphrase temporary file could not be removed")


def _scrub_temp_passphrase_file(parent_fd: int, temp_name: str) -> None:
    if not temp_name:
        return
    fd = os.open(temp_name, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            return
        remaining = int(file_stat.st_size)
        if remaining > 0:
            os.lseek(fd, 0, os.SEEK_SET)
            chunk = b"\x00" * min(remaining, 65536)
            while remaining > 0:
                written = os.write(fd, chunk[: min(remaining, len(chunk))])
                if written <= 0:
                    break
                remaining -= written
            with suppress(OSError, RuntimeError):
                _fsync_fd(fd)
        os.ftruncate(fd, 0)
        with suppress(OSError, RuntimeError):
            _fsync_fd(fd)
    finally:
        os.close(fd)


def _generate_default_passphrase_file(path: Path, *, replace: bool = False) -> str:
    if path != default_passphrase_file():
        raise ArtifactCryptoError("only the default artifact encryption passphrase file can be generated automatically")
    passphrase = _new_generated_passphrase()
    payload = passphrase.encode("ascii") + b"\n"
    parent_fd = -1
    temp_fd = -1
    temp_name = ""
    cleanup_error: Exception | None = None
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
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            raise ArtifactCryptoError("artifact encryption passphrase file must be owned by the current user")
        if file_stat.st_mode & 0o077:
            raise ArtifactCryptoError("artifact encryption passphrase file must be private")
        os.fchmod(temp_fd, 0o600)
        _write_all(temp_fd, payload)
        _fsync_fd(temp_fd)
        os.close(temp_fd)
        temp_fd = -1
        if replace:
            os.replace(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temp_name = ""
        else:
            try:
                os.link(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            except FileExistsError:
                os.unlink(temp_name, dir_fd=parent_fd)
                temp_name = ""
                _fsync_fd(parent_fd)
                return _read_private_passphrase_file(path, allow_default_generation=False, rotate_weak_default=False)
            os.unlink(temp_name, dir_fd=parent_fd)
            temp_name = ""
        _fsync_fd(parent_fd)
    except FileExistsError:
        return _read_private_passphrase_file(path, allow_default_generation=False, rotate_weak_default=False)
    except (OSError, RuntimeError) as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file could not be generated") from exc
    finally:
        if temp_fd >= 0:
            try:
                os.close(temp_fd)
            except Exception as exc:
                cleanup_error = exc
        if temp_name and parent_fd >= 0:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except Exception as exc:
                with suppress(Exception):
                    _scrub_temp_passphrase_file(parent_fd, temp_name)
                if cleanup_error is None:
                    cleanup_error = exc
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            raise _temp_passphrase_cleanup_error() from cleanup_error
    return passphrase


def _stat_private_passphrase_parent(path: Path) -> None:
    try:
        parent_stat = path.parent.stat()
    except OSError as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file directory could not be inspected") from exc
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
    try:
        path = path.expanduser()
    except (OSError, RuntimeError) as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file path could not be resolved") from exc
    default_path = default_passphrase_file()
    is_default_path = path == default_path
    if is_default_path and allow_default_generation and not path.exists() and not path.is_symlink():
        return _generate_default_passphrase_file(path)
    try:
        assert_no_symlink_ancestors(path, field_name="artifact encryption passphrase file")
        _stat_private_passphrase_parent(path)
        fd = open_file_without_following_symlinks(path, os.O_RDONLY, field_name="artifact encryption passphrase file")
    except (OSError, RuntimeError) as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file could not be read") from exc
    try:
        try:
            assert_fd_is_regular_private_file(
                fd,
                field_name="artifact encryption passphrase file",
                require_private_mode=True,
            )
            file_stat = os.fstat(fd)
        except (OSError, RuntimeError) as exc:
            if "must be private" in str(exc):
                raise ArtifactCryptoError("artifact encryption passphrase file must be private") from exc
            raise ArtifactCryptoError("artifact encryption passphrase file is not private") from exc
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            raise ArtifactCryptoError("artifact encryption passphrase file must be owned by the current user")
        if file_stat.st_mode & 0o077:
            raise ArtifactCryptoError("artifact encryption passphrase file must be private")
        _assert_no_posix_acl(path, field_name="artifact encryption passphrase file")
        try:
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                payload = handle.read(MAX_PASSPHRASE_FILE_BYTES + 1)
        except Exception as exc:
            raise ArtifactCryptoError("artifact encryption passphrase file could not be read") from exc
    finally:
        if fd >= 0:
            with suppress(Exception):
                os.close(fd)
    if len(payload) > MAX_PASSPHRASE_FILE_BYTES:
        raise ArtifactCryptoError("artifact encryption passphrase file is too large")
    try:
        passphrase = payload.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file must be valid UTF-8") from exc
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
    try:
        path = Path(normalized).expanduser()
    except (OSError, RuntimeError) as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file path could not be resolved") from exc
    if not path.is_absolute():
        raise ArtifactCryptoError("artifact encryption passphrase file path must be absolute")
    try:
        assert_safe_path_components(path, field_name="artifact encryption passphrase file path")
    except RuntimeError as exc:
        raise ArtifactCryptoError("artifact encryption passphrase file path is not safe") from exc
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
    if passphrase_file is not None:
        passphrase = _read_private_passphrase_file(
            passphrase_file,
            allow_default_generation=allow_default_generation,
            rotate_weak_default=False,
        )
    else:
        passphrase = os.environ.get(PASSPHRASE_ENV, "")
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
        if key in _ALLOWED_SECRET_TOOL_ENV and isinstance(value, str) and not _contains_forbidden_environment_chars(value)
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
    bus_path = Path(value[len(_SAFE_DBUS_SESSION_PREFIX):])
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


def _stop_secret_tool_process(proc: subprocess.Popen[bytes]) -> None:
    with suppress(Exception):
        proc.kill()
    with suppress(Exception):
        proc.wait(timeout=1)


def _read_secret_tool_pipes_bounded(proc: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    try:
        stdout = proc.stdout
        stderr = proc.stderr
    except Exception as exc:
        _stop_secret_tool_process(proc)
        raise ArtifactCryptoError("Secret Service keyring helper output could not be captured safely") from exc
    if stdout is None or stderr is None:
        _stop_secret_tool_process(proc)
        raise ArtifactCryptoError("Secret Service keyring helper output could not be captured safely")
    outputs: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    streams = ((stdout, "stdout"), (stderr, "stderr"))
    active: dict[int, str] = {}
    for stream, field_name in streams:
        try:
            fd = stream.fileno()
            os.set_blocking(fd, False)
        except Exception as exc:
            _stop_secret_tool_process(proc)
            raise ArtifactCryptoError("Secret Service keyring helper output could not be captured safely") from exc
        active[fd] = field_name
    deadline = time.monotonic() + _SECRET_TOOL_TIMEOUT_SECONDS
    while active:
        if time.monotonic() >= deadline:
            _stop_secret_tool_process(proc)
            raise ArtifactCryptoError("Secret Service keyring request timed out")
        progressed = False
        for fd, field_name in list(active.items()):
            try:
                chunk = os.read(fd, 8192)
            except BlockingIOError:
                continue
            except Exception as exc:
                _stop_secret_tool_process(proc)
                raise ArtifactCryptoError("Secret Service keyring helper output could not be captured safely") from exc
            if not chunk:
                active.pop(fd, None)
                progressed = True
                continue
            progressed = True
            try:
                outputs[field_name].extend(chunk)
            except Exception as exc:
                _stop_secret_tool_process(proc)
                raise ArtifactCryptoError("Secret Service keyring helper output could not be captured safely") from exc
            if len(outputs[field_name]) > MAX_SECRET_TOOL_OUTPUT_BYTES:
                _stop_secret_tool_process(proc)
                raise ArtifactCryptoError(f"Secret Service keyring {field_name} exceeded safe output limit")
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
    try:
        try:
            proc = subprocess.Popen(  # nosec B603
                command,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=env,
            )
        except (OSError, ValueError) as exc:
            raise ArtifactCryptoError("Secret Service keyring helper could not be started") from exc
        if input_text is not None:
            try:
                stdin = proc.stdin
            except Exception as exc:
                _stop_secret_tool_process(proc)
                raise ArtifactCryptoError("Secret Service keyring helper input could not be sent safely") from exc
            if stdin is None:
                _stop_secret_tool_process(proc)
                raise ArtifactCryptoError("Secret Service keyring helper input could not be sent safely")
            try:
                stdin.write(input_text.encode("utf-8"))
                stdin.close()
            except Exception as exc:
                _stop_secret_tool_process(proc)
                raise ArtifactCryptoError("Secret Service keyring helper input could not be sent safely") from exc
        stdout, stderr = _read_secret_tool_pipes_bounded(proc)
        try:
            returncode = proc.wait(timeout=_SECRET_TOOL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _stop_secret_tool_process(proc)
            raise ArtifactCryptoError("Secret Service keyring request timed out") from exc
        except Exception as exc:
            _stop_secret_tool_process(proc)
            raise ArtifactCryptoError("Secret Service keyring helper could not be reaped safely") from exc
        stdout = _validate_secret_tool_output(stdout, field_name="stdout")
        stderr = _validate_secret_tool_output(stderr, field_name="stderr")
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)
    finally:
        if proc is not None:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    with suppress(Exception):
                        stream.close()


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
        return None
    try:
        return _parse_keyring_secret(proc.stdout)
    except UnicodeDecodeError as exc:
        raise ArtifactCryptoError("Secret Service keyring returned invalid UTF-8") from exc


def _store_keyring_key(key: bytes) -> None:
    encoded = _b64encode(key)
    proc = _run_secret_tool(
        ["store", "--label", f"{APP_NAME} artifact encryption key", *_SECRET_TOOL_ATTRIBUTES],
        input_text=encoded,
    )
    if proc.returncode != 0:
        raise ArtifactCryptoError("Secret Service keyring could not store the artifact encryption key")


def _load_keyring_key() -> bytes:
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
    return _derive_passphrase_key(passphrase, salt)


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
    ciphertext = aesgcm(key_material.key).encrypt(nonce, payload, _aad(safe_kind))
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
    rendered = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
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
    if not is_encrypted_payload(payload):
        if require_encrypted:
            raise ArtifactCryptoError("encrypted artifact envelope is missing")
        return payload
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactCryptoError("encrypted artifact envelope is malformed") from exc
    if not isinstance(envelope, dict):
        raise ArtifactCryptoError("encrypted artifact envelope must be an object")
    if envelope.get("magic") != ENVELOPE_MAGIC or envelope.get("version") != ENVELOPE_VERSION:
        raise ArtifactCryptoError("encrypted artifact envelope version is unsupported")
    if envelope.get("algorithm") != ENVELOPE_ALGORITHM:
        raise ArtifactCryptoError("encrypted artifact algorithm is unsupported")
    safe_kind = _normalize_kind(kind)
    if envelope.get("kind") != safe_kind:
        raise ArtifactCryptoError("encrypted artifact kind does not match the requested use")
    nonce = _b64decode(envelope.get("nonce", ""), field_name="nonce")
    if len(nonce) != NONCE_SIZE_BYTES:
        raise ArtifactCryptoError("encrypted artifact nonce has invalid length")
    ciphertext = _b64decode(envelope.get("ciphertext", ""), field_name="ciphertext")
    key_material = _key_for_decryption(envelope)
    invalid_tag, aesgcm, _scrypt = _crypto_backend()
    try:
        return aesgcm(key_material.key).decrypt(nonce, ciphertext, _aad(safe_kind))
    except invalid_tag as exc:
        raise ArtifactCryptoError("encrypted artifact authentication failed") from exc


def read_private_bytes(path: Path, *, field_name: str, max_bytes: int | None = None) -> bytes:
    if not isinstance(path, Path):
        raise ArtifactCryptoError(f"{field_name} must be a path")
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0):
        raise ArtifactCryptoError("max_bytes must be a non-negative integer")
    effective_max_bytes = MAX_ENCRYPTED_ARTIFACT_BYTES if max_bytes is None else max_bytes
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise ArtifactCryptoError(f"secure {field_name} open is not supported")
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
        fd = open_file_without_following_symlinks(
            path,
            os.O_RDONLY | nofollow_flag | getattr(os, "O_NONBLOCK", 0),
            field_name=field_name,
        )
    except (OSError, RuntimeError) as exc:
        raise ArtifactCryptoError(f"failed to read {field_name}") from exc
    try:
        assert_fd_is_regular_private_file(fd, field_name=field_name)
        handle = os.fdopen(fd, "rb")
        fd = -1
        with handle:
            data = handle.read(effective_max_bytes + 1)
    except Exception as exc:
        raise ArtifactCryptoError(f"failed to read {field_name}") from exc
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    if len(data) > effective_max_bytes:
        raise ArtifactCryptoError(f"{field_name} is too large")
    return data


def write_encrypted_bytes_atomically(path: Path, payload: bytes, requested_mode: object, *, kind: str, field_name: str) -> tuple[Path, str]:
    mode = normalize_artifact_encryption(requested_mode)
    target_path = path if mode == ARTIFACT_ENCRYPTION_OFF else encrypted_path_for(path)
    encrypted_payload, effective_mode = encrypt_bytes(payload, mode, kind=kind)
    try:
        write_bytes_atomically_without_following_symlinks(target_path, encrypted_payload, field_name=field_name)
    except (OSError, RuntimeError) as exc:
        raise ArtifactCryptoError(f"failed to write encrypted {field_name}") from exc
    return target_path, effective_mode


def read_decrypted_bytes_from_file(
    path: Path,
    *,
    kind: str,
    field_name: str,
    max_bytes: int | None = None,
    require_encrypted: bool = True,
) -> bytes:
    data = read_private_bytes(
        path,
        field_name=field_name,
        max_bytes=max_bytes,
    )
    return decrypt_bytes(data, kind=kind, require_encrypted=require_encrypted)
