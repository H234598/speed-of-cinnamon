from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


_TEST_ROOT_ENV = "SPEED_OF_CINNAMON_TEST_ROOT"
_TEST_MODE_ENV = "SPEED_OF_CINNAMON_TEST_MODE"
_TEST_ROOT_OWNER_ENV = "SPEED_OF_CINNAMON_TEST_ROOT_OWNER"
_AUTOMATIC_PARENT = Path("/tmp")
_AUTOMATIC_PREFIX = "speed-of-cinnamon-tests-"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRIVATE_DIRECTORIES = (
    ("TMPDIR", "tmp"),
    ("XDG_STATE_HOME", "state"),
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_CONFIG_HOME", "config"),
)
_ORIGINAL_XDG_STATE_HOME = os.environ.get("XDG_STATE_HOME")
_owned_root: tuple[Path, int, int, int] | None = None


def _directory_open_flags() -> int:
    try:
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    except AttributeError as exc:
        raise OSError("symlink-safe directory operations are unavailable") from exc


def _is_normalized_absolute(path: Path) -> bool:
    return path.is_absolute() and path == Path(os.path.normpath(os.fspath(path)))


def _open_directory_without_symlinks(path: Path) -> int:
    if not _is_normalized_absolute(path):
        raise ValueError("directory path must be normalized and absolute")
    flags = _directory_open_flags()
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _is_private_directory_stat(current: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(current.st_mode)
        and current.st_uid == os.getuid()
        and stat.S_IMODE(current.st_mode) == 0o700
    )


def _is_repo_path(path: Path) -> bool:
    try:
        path.relative_to(_REPO_ROOT)
    except ValueError:
        return False
    return True


def _is_private_owned_directory(path: Path) -> bool:
    if not _is_normalized_absolute(path) or _is_repo_path(path):
        return False
    try:
        descriptor = _open_directory_without_symlinks(path)
    except (OSError, ValueError):
        return False
    try:
        return _is_private_directory_stat(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _prepare_private_root(root: str) -> bool:
    root_path = Path(root)
    if not _is_normalized_absolute(root_path) or _is_repo_path(root_path):
        return False
    try:
        root_descriptor = _open_directory_without_symlinks(root_path)
    except (OSError, ValueError):
        return False
    try:
        if not _is_private_directory_stat(os.fstat(root_descriptor)):
            return False
        missing: list[str] = []
        flags = _directory_open_flags()
        for _, name in _PRIVATE_DIRECTORIES:
            try:
                descriptor = os.open(name, flags, dir_fd=root_descriptor)
            except FileNotFoundError:
                missing.append(name)
                continue
            except OSError:
                return False
            try:
                if not _is_private_directory_stat(os.fstat(descriptor)):
                    return False
            finally:
                os.close(descriptor)
        for name in missing:
            try:
                os.mkdir(name, mode=0o700, dir_fd=root_descriptor)
            except FileExistsError:
                pass
            except OSError:
                return False
            try:
                descriptor = os.open(name, flags, dir_fd=root_descriptor)
            except OSError:
                return False
            try:
                if not _is_private_directory_stat(os.fstat(descriptor)):
                    return False
            finally:
                os.close(descriptor)
        return True
    finally:
        os.close(root_descriptor)


def _automatic_parent_is_secure() -> bool:
    if _AUTOMATIC_PARENT != Path("/tmp"):
        return False
    try:
        descriptor = _open_directory_without_symlinks(_AUTOMATIC_PARENT)
    except (OSError, ValueError):
        return False
    try:
        current = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    mode = stat.S_IMODE(current.st_mode)
    if not stat.S_ISDIR(current.st_mode) or mode & (stat.S_ISUID | stat.S_ISGID):
        return False
    if not os.access(_AUTOMATIC_PARENT, os.W_OK | os.X_OK):
        return False
    if current.st_uid == os.getuid():
        return not (mode & 0o022) or bool(mode & stat.S_ISVTX)
    return bool(mode & stat.S_ISVTX) and (mode & 0o003) == 0o003


def _owner_marker(root: Path, pid: int, device: int, inode: int) -> str:
    return f"{pid}:{device}:{inode}:{root}"


def _parse_owner_marker(value: str) -> tuple[int, int, int, Path] | None:
    try:
        pid_text, device_text, inode_text, root_text = value.split(":", 3)
        return int(pid_text), int(device_text), int(inode_text), Path(root_text)
    except (TypeError, ValueError):
        return None


def _candidate_is_reusable(root: Path) -> bool:
    marker_value = os.environ.get(_TEST_ROOT_OWNER_ENV)
    if marker_value is None:
        return True
    marker = _parse_owner_marker(marker_value)
    if marker is None:
        return False
    marker_pid, marker_device, marker_inode, marker_root = marker
    if marker_root != root:
        return True
    if _owned_root != (root, marker_pid, marker_device, marker_inode):
        return False
    if marker_pid != os.getpid():
        return False
    try:
        current = root.lstat()
    except OSError:
        return False
    return (current.st_dev, current.st_ino) == (marker_device, marker_inode)


def _cleanup_owned_root() -> None:
    """Remove this process's unchanged root. SIGKILL cannot run atexit cleanup."""

    global _owned_root
    owned_root = _owned_root
    _owned_root = None
    if owned_root is None:
        return
    root, owner_pid, expected_device, expected_inode = owned_root
    if owner_pid != os.getpid():
        return
    if root.parent != _AUTOMATIC_PARENT or not root.name.startswith(_AUTOMATIC_PREFIX):
        return
    if not _automatic_parent_is_secure():
        return
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        return
    try:
        parent_descriptor = _open_directory_without_symlinks(_AUTOMATIC_PARENT)
    except (OSError, ValueError):
        return
    try:
        try:
            current = os.stat(root.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError:
            return
        if not _is_private_directory_stat(current):
            return
        if (current.st_dev, current.st_ino) != (expected_device, expected_inode):
            return
        try:
            shutil.rmtree(root.name, dir_fd=parent_descriptor)
        except OSError:
            return
    finally:
        os.close(parent_descriptor)


def _discard_inherited_ownership() -> None:
    global _owned_root
    _owned_root = None


def _create_automatic_root() -> Path:
    global _owned_root
    if not _automatic_parent_is_secure():
        raise RuntimeError("/tmp is not a secure automatic test-root parent")
    root = Path(
        tempfile.mkdtemp(
            prefix=_AUTOMATIC_PREFIX,
            dir=os.fspath(_AUTOMATIC_PARENT),
        )
    )
    try:
        current = root.lstat()
    except OSError as exc:
        raise RuntimeError("could not inspect automatic test root") from exc
    if root.parent != _AUTOMATIC_PARENT or not _is_private_directory_stat(current):
        raise RuntimeError("automatic test root is not private")
    _owned_root = (root, os.getpid(), current.st_dev, current.st_ino)
    if not _prepare_private_root(os.fspath(root)):
        _cleanup_owned_root()
        raise RuntimeError("could not prepare automatic test root")
    os.environ[_TEST_ROOT_OWNER_ENV] = _owner_marker(
        root,
        os.getpid(),
        current.st_dev,
        current.st_ino,
    )
    return root


def original_xdg_state_home() -> str | None:
    """Return the state-home value captured before test isolation."""

    return _ORIGINAL_XDG_STATE_HOME


def isolate_user_state() -> str:
    """Create the default private runtime before any product import."""

    candidate = os.environ.get(_TEST_ROOT_ENV)
    root: Path | None = None
    if candidate is not None and os.environ.get(_TEST_MODE_ENV) == "1":
        candidate_path = Path(candidate)
        if _prepare_private_root(candidate) and _candidate_is_reusable(candidate_path):
            root = candidate_path
    if root is None:
        root = _create_automatic_root()
    elif _owned_root is None or _owned_root[0] != root:
        os.environ.pop(_TEST_ROOT_OWNER_ENV, None)

    os.environ[_TEST_ROOT_ENV] = os.fspath(root)
    os.environ[_TEST_MODE_ENV] = "1"
    for variable, name in _PRIVATE_DIRECTORIES:
        os.environ[variable] = os.fspath(root / name)
    os.environ["TEMP"] = os.environ["TMPDIR"]
    os.environ["TMP"] = os.environ["TMPDIR"]
    tempfile.tempdir = os.environ["TMPDIR"]
    return os.fspath(root)


def ensure_source_path() -> None:
    source_path = _REPO_ROOT / "src"
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))


atexit.register(_cleanup_owned_root)
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_discard_inherited_ownership)
