from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404
from contextlib import contextmanager
from functools import wraps
from typing import Iterator, Sequence


REQUESTED_CPU_NICE = -5
IO_PRIORITY_CLASS = "2"
IO_PRIORITY_LEVEL = "0"
LOCAL_MODEL_CPU_NICE = 10
LOCAL_MODEL_IO_PRIORITY_LEVEL = "7"
IONICE_TIMEOUT_SECONDS = 1.0
_IO_PRIORITY_RE = re.compile(r"\bprio\s+([0-7])\b", re.ASCII)


def _current_cpu_priority() -> int | None:
    try:
        return os.getpriority(os.PRIO_PROCESS, 0)
    except (AttributeError, OSError, OverflowError, ValueError):
        return None


def _set_cpu_priority(value: int) -> bool:
    try:
        os.setpriority(os.PRIO_PROCESS, 0, value)
    except (AttributeError, OSError, OverflowError, ValueError):
        return False
    return True


def _ionice_command(arguments: list[str]) -> bool:
    ionice = shutil.which("ionice")
    if not ionice:
        return False
    try:
        result = subprocess.run(  # nosec B603
            [ionice, *arguments],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=IONICE_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, OverflowError, ValueError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _set_io_priority(level: str) -> bool:
    return _ionice_command(
        [
            "--class",
            IO_PRIORITY_CLASS,
            "--classdata",
            level,
            "--pid",
            str(os.getpid()),
        ]
    )


def local_model_command(argv: Sequence[str]) -> list[str]:
    command = list(argv)
    ionice = shutil.which("ionice")
    nice = shutil.which("nice")
    prefix: list[str] = []
    if ionice:
        prefix.extend(
            [
                ionice,
                "--class",
                IO_PRIORITY_CLASS,
                "--classdata",
                LOCAL_MODEL_IO_PRIORITY_LEVEL,
                "--",
            ]
        )
    if nice:
        prefix.extend([nice, "--adjustment", str(LOCAL_MODEL_CPU_NICE)])
    return [*prefix, *command]


def _current_io_priority() -> int | None:
    ionice = shutil.which("ionice")
    if not ionice:
        return None
    try:
        result = subprocess.run(  # nosec B603
            [ionice, "--pid", str(os.getpid())],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=IONICE_TIMEOUT_SECONDS,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, OverflowError, ValueError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return None
    match = _IO_PRIORITY_RE.search(result.stdout[:256])
    return int(match.group(1)) if match else None


def apply_process_priority() -> tuple[bool, bool]:
    """Apply modest high-priority settings without making startup fail."""

    return _set_cpu_priority(REQUESTED_CPU_NICE), _set_io_priority(IO_PRIORITY_LEVEL)


@contextmanager
def local_model_priority() -> Iterator[None]:
    previous_cpu = _current_cpu_priority()
    previous_io = _current_io_priority()
    lower_cpu = previous_cpu is not None and (previous_cpu < 0 or os.geteuid() == 0)
    if lower_cpu:
        _set_cpu_priority(LOCAL_MODEL_CPU_NICE)
    _set_io_priority(LOCAL_MODEL_IO_PRIORITY_LEVEL)
    try:
        yield
    finally:
        if lower_cpu and previous_cpu is not None:
            _set_cpu_priority(previous_cpu)
        if previous_io is not None:
            _set_io_priority(str(previous_io))


def with_local_model_priority(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with local_model_priority():
            return function(*args, **kwargs)

    return wrapped
