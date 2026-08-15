from __future__ import annotations

from pathlib import Path


MAX_PROC_STAT_BYTES = 64 * 1024
MAX_PROC_BOOT_ID_BYTES = 128


def _read_proc_stat_path(path: Path) -> str:
    with path.open("r", encoding="ascii") as handle:
        return handle.read(MAX_PROC_STAT_BYTES).strip()


def _read_proc_stat(pid: int) -> str:
    return _read_proc_stat_path(Path(f"/proc/{pid}/stat"))


def _read_proc_boot_id() -> str:
    with Path("/proc/sys/kernel/random/boot_id").open("r", encoding="ascii") as handle:
        return handle.read(MAX_PROC_BOOT_ID_BYTES).strip()
