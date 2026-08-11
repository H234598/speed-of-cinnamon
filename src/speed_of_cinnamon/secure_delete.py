from __future__ import annotations

import os
import stat


SECURE_DELETE_CHUNK_BYTES = 1024 * 1024
_OS_CLOSE = os.close
_OS_FSYNC = os.fsync
_OS_FSTAT = os.fstat
_OS_FTRUNCATE = os.ftruncate
_OS_LSEEK = os.lseek
_OS_OPEN = os.open
_OS_PWRITE = getattr(os, "pwrite", None)
_OS_WRITE = os.write


def _same_claim_identity(first: os.stat_result, second: os.stat_result) -> bool:
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


def secure_wipe_regular_file_at(
    parent_fd: int,
    name: str,
    expected_stat: os.stat_result,
    *,
    field_name: str,
) -> None:
    if isinstance(parent_fd, bool) or not isinstance(parent_fd, int) or parent_fd < 0:
        raise RuntimeError(f"{field_name} secure deletion parent is invalid")
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise RuntimeError(f"{field_name} secure deletion name is invalid")
    if not isinstance(expected_stat, os.stat_result):
        raise RuntimeError(f"{field_name} secure deletion identity is invalid")
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RuntimeError(f"secure deletion is not supported for {field_name}")
    secure_fd = -1
    primary_error: BaseException | None = None
    try:
        secure_fd = _OS_OPEN(
            name,
            os.O_RDWR | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        opened_stat = _OS_FSTAT(secure_fd)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or getattr(opened_stat, "st_nlink", 1) != 1
            or not _same_claim_identity(opened_stat, expected_stat)
        ):
            raise RuntimeError(f"{field_name} changed before secure deletion")
        size = opened_stat.st_size
        zero_chunk = b"\x00" * min(SECURE_DELETE_CHUNK_BYTES, size)
        offset = 0
        while offset < size:
            chunk = zero_chunk[: min(len(zero_chunk), size - offset)]
            if not chunk:
                raise RuntimeError(f"{field_name} secure deletion made no progress")
            if callable(_OS_PWRITE):
                written = _OS_PWRITE(secure_fd, chunk, offset)
            else:
                _OS_LSEEK(secure_fd, offset, os.SEEK_SET)
                written = _OS_WRITE(secure_fd, chunk)
            if not isinstance(written, int) or written <= 0:
                raise RuntimeError(f"{field_name} secure deletion made no progress")
            offset += written
        # Remove bytes appended after the initial size snapshot before syncing.
        # This keeps concurrent writers from leaving unwiped tail data behind.
        _OS_FTRUNCATE(secure_fd, size)
        _OS_FSYNC(secure_fd)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if secure_fd >= 0:
            try:
                _OS_CLOSE(secure_fd)
            except BaseException:
                if primary_error is not None:
                    primary_error.add_note(f"{field_name} secure deletion close failed")
                else:
                    raise
