from __future__ import annotations

import os
from pathlib import Path


MAX_PROC_STAT_BYTES = 64 * 1024
MAX_PROC_BOOT_ID_BYTES = 128
MAX_PROC_DIRECTORY_ENTRIES = 100_000
PROC_ROOT = Path("/proc")


def _bounded_proc_entries() -> tuple[Path, ...] | None:
    entries: list[Path] = []
    try:
        with os.scandir(PROC_ROOT) as proc_entries:
            for proc_entry in proc_entries:
                name = proc_entry.name
                if not isinstance(name, str) or not name.isdecimal():
                    continue
                if len(entries) >= MAX_PROC_DIRECTORY_ENTRIES:
                    return None
                entries.append(PROC_ROOT / name)
    except OSError:
        return None
    return tuple(entries)


def _read_proc_stat_path(path: Path) -> str:
    with path.open("r", encoding="ascii") as handle:
        return handle.read(MAX_PROC_STAT_BYTES).strip()


def _read_proc_stat(pid: int) -> str:
    return _read_proc_stat_path(Path(f"/proc/{pid}/stat"))


def _read_proc_boot_id() -> str:
    with Path("/proc/sys/kernel/random/boot_id").open("r", encoding="ascii") as handle:
        return handle.read(MAX_PROC_BOOT_ID_BYTES).strip()
