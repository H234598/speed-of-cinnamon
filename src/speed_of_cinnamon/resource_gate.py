from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import TextIO

from . import process_priority


EX_OK = 0
EX_USAGE = 2
EX_SOFTWARE = 70
EX_IOERR = 74
EX_TEMPFAIL = 75
MAX_JSON_BYTES = 16 * 1024
MIN_MEMORY_HEADROOM_BYTES = 8 * 1024**3
MAX_LOADAVG_BYTES = 256
MAX_MEMINFO_BYTES = 64 * 1024
MAX_PSI_BYTES = 4 * 1024
_LOAD_THRESHOLDS = (0.25, 0.35, 0.50)
_PSI_THRESHOLDS = (5.0, 1.0, 1.0)
_LOADAVG = Path("/proc/loadavg")
_MEMINFO = Path("/proc/meminfo")
_PSI_CPU = Path("/proc/pressure/cpu")
_PSI_MEMORY = Path("/proc/pressure/memory")
_PSI_IO = Path("/proc/pressure/io")
_DECIMAL_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$", re.ASCII)
_UINT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$", re.ASCII)
_MAX_METRIC_TOKEN_CHARS = 32
_UINT64_MAX = (1 << 64) - 1


def _read_system_file(path: Path, *, max_bytes: int) -> str | None:
    return process_priority._read_affinity_file(path, max_bytes=max_bytes)


def _finite_decimal(value: str) -> float | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_METRIC_TOKEN_CHARS
        or _DECIMAL_RE.fullmatch(value) is None
    ):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _bounded_uint(value: str) -> int | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 20
        or _UINT_RE.fullmatch(value) is None
    ):
        return None
    try:
        result = int(value)
    except (OverflowError, ValueError):
        return None
    return result if result <= _UINT64_MAX else None


def _parse_loadavg(contents: str | None) -> tuple[float, float, float] | None:
    if not isinstance(contents, str):
        return None
    fields = contents.split()
    if len(fields) < 3:
        return None
    parsed = tuple(_finite_decimal(value) for value in fields[:3])
    if any(value is None for value in parsed):
        return None
    first, fifth, fifteenth = parsed
    assert first is not None and fifth is not None and fifteenth is not None
    return first, fifth, fifteenth


def _parse_meminfo(contents: str | None) -> dict[str, int] | None:
    if not isinstance(contents, str):
        return None
    required = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    values: dict[str, int] = {}
    for line in contents.splitlines():
        key, separator, remainder = line.partition(":")
        if key not in required:
            continue
        fields = remainder.split()
        if not separator or len(fields) != 2 or fields[1] != "kB" or key in values:
            return None
        kib = _bounded_uint(fields[0])
        if kib is None or kib > _UINT64_MAX // 1024:
            return None
        values[key] = kib * 1024
    if set(values) != required:
        return None
    if values["MemTotal"] <= 0 or values["MemAvailable"] > values["MemTotal"]:
        return None
    if values["SwapFree"] > values["SwapTotal"]:
        return None
    return values


def _parse_psi_avg10(contents: str | None, pressure_kind: str) -> float | None:
    if not isinstance(contents, str) or pressure_kind not in {"some", "full"}:
        return None
    matches = [line for line in contents.splitlines() if line.startswith(pressure_kind + " ")]
    if len(matches) != 1:
        return None
    fields = matches[0].split()
    metrics: dict[str, str] = {}
    for field in fields[1:]:
        key, separator, value = field.partition("=")
        if not separator or key in metrics:
            return None
        metrics[key] = value
    if set(metrics) != {"avg10", "avg60", "avg300", "total"}:
        return None
    if any(_finite_decimal(metrics[key]) is None for key in ("avg10", "avg60", "avg300")):
        return None
    if _bounded_uint(metrics["total"]) is None:
        return None
    return _finite_decimal(metrics["avg10"])


def _affinity_snapshot() -> frozenset[int] | None:
    try:
        affinity = frozenset(os.sched_getaffinity(0))
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return None
    if (
        not affinity
        or len(affinity) > process_priority.MAX_CPU_LIST_COUNT
        or any(
            not isinstance(cpu, int)
            or isinstance(cpu, bool)
            or not 0 <= cpu <= process_priority.MAX_CPU_INDEX
            for cpu in affinity
        )
    ):
        return None
    return affinity


def _empty_checks(*, require_full_online: bool) -> dict[str, bool | None]:
    return {
        "affinity_matches_cpuset": None,
        "cpu_quota": None,
        "full_online": None if require_full_online else True,
        "load": None,
        "memory_available": None,
        "memory_headroom": None,
        "psi": None,
    }


def _defer_payload(reason: str, *, require_full_online: bool) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "defer",
        "checks": _empty_checks(require_full_online=require_full_online),
        "metrics": None,
        "reasons": [reason],
        "warnings": [],
    }


def _build_payload(*, require_full_online: bool) -> dict[str, object]:
    namespace_identity = process_priority.initial_namespace_identity()
    if namespace_identity is None:
        return _defer_payload(
            "initial_namespace_unavailable",
            require_full_online=require_full_online,
        )
    affinity_before = _affinity_snapshot()
    if affinity_before is None:
        return _defer_payload(
            "affinity_unavailable",
            require_full_online=require_full_online,
        )
    load = _parse_loadavg(
        _read_system_file(_LOADAVG, max_bytes=MAX_LOADAVG_BYTES)
    )
    if load is None:
        return _defer_payload("load_unavailable", require_full_online=require_full_online)
    memory = _parse_meminfo(
        _read_system_file(_MEMINFO, max_bytes=MAX_MEMINFO_BYTES)
    )
    if memory is None:
        return _defer_payload(
            "memory_unavailable",
            require_full_online=require_full_online,
        )
    psi_values = (
        _parse_psi_avg10(
            _read_system_file(_PSI_CPU, max_bytes=MAX_PSI_BYTES),
            "some",
        ),
        _parse_psi_avg10(
            _read_system_file(_PSI_MEMORY, max_bytes=MAX_PSI_BYTES),
            "full",
        ),
        _parse_psi_avg10(
            _read_system_file(_PSI_IO, max_bytes=MAX_PSI_BYTES),
            "full",
        ),
    )
    if any(value is None for value in psi_values):
        return _defer_payload("psi_unavailable", require_full_online=require_full_online)
    snapshot = process_priority.current_cgroup_resource_snapshot(
        include_online_cpus=require_full_online
    )
    if snapshot is None:
        return _defer_payload(
            "resource_boundary_unavailable",
            require_full_online=require_full_online,
        )
    affinity_after = _affinity_snapshot()
    if affinity_after is None or affinity_after != affinity_before:
        return _defer_payload(
            "affinity_changed",
            require_full_online=require_full_online,
        )
    if process_priority.initial_namespace_identity() != namespace_identity:
        return _defer_payload(
            "initial_namespace_changed",
            require_full_online=require_full_online,
        )

    cpu_count = len(affinity_before)
    load_per_cpu = tuple(value / cpu_count for value in load)
    load_ok = all(
        value <= threshold
        for value, threshold in zip(load_per_cpu, _LOAD_THRESHOLDS, strict=True)
    )
    available = memory["MemAvailable"]
    total = memory["MemTotal"]
    memory_available_ok = (
        available >= MIN_MEMORY_HEADROOM_BYTES and available * 4 >= total
    )
    cpu_psi, memory_psi, io_psi = psi_values
    assert cpu_psi is not None and memory_psi is not None and io_psi is not None
    psi_ok = all(
        value <= threshold
        for value, threshold in zip(
            (cpu_psi, memory_psi, io_psi),
            _PSI_THRESHOLDS,
            strict=True,
        )
    )
    affinity_ok = affinity_before == snapshot.effective_cpus
    quota_ok = (
        snapshot.cpu_quota_us is None
        or snapshot.cpu_quota_us >= cpu_count * snapshot.cpu_period_us
    )
    memory_headroom_ok = all(
        value is None or value >= MIN_MEMORY_HEADROOM_BYTES
        for value in (
            snapshot.memory_max_headroom_bytes,
            snapshot.memory_high_headroom_bytes,
        )
    )
    full_online_ok = (
        not require_full_online
        or snapshot.online_cpus == snapshot.effective_cpus
    )
    checks = {
        "affinity_matches_cpuset": affinity_ok,
        "cpu_quota": quota_ok,
        "full_online": full_online_ok,
        "load": load_ok,
        "memory_available": memory_available_ok,
        "memory_headroom": memory_headroom_ok,
        "psi": psi_ok,
    }
    reasons = [name for name, passed in checks.items() if not passed]
    swap_in_use = memory["SwapFree"] < memory["SwapTotal"]
    return {
        "schema_version": 1,
        "status": "green" if not reasons else "defer",
        "checks": checks,
        "metrics": {
            "affinity_cpus": cpu_count,
            "effective_cpuset_cpus": len(snapshot.effective_cpus),
            "online_cpus": (
                len(snapshot.online_cpus)
                if snapshot.online_cpus is not None
                else None
            ),
            "loadavg": {
                "1m": load[0],
                "5m": load[1],
                "15m": load[2],
            },
            "load_per_affined_cpu": {
                "1m": load_per_cpu[0],
                "5m": load_per_cpu[1],
                "15m": load_per_cpu[2],
            },
            "memory": {
                "available_bytes": available,
                "total_bytes": total,
                "available_ratio": available / total,
                "current_bytes": snapshot.memory_current_bytes,
                "max_bytes": snapshot.memory_max_bytes,
                "high_bytes": snapshot.memory_high_bytes,
                "max_headroom_bytes": snapshot.memory_max_headroom_bytes,
                "high_headroom_bytes": snapshot.memory_high_headroom_bytes,
            },
            "cpu_max": {
                "quota_us": snapshot.cpu_quota_us,
                "period_us": snapshot.cpu_period_us,
            },
            "psi_avg10": {
                "cpu_some": cpu_psi,
                "memory_full": memory_psi,
                "io_full": io_psi,
            },
            "swap": {
                "total_bytes": memory["SwapTotal"],
                "free_bytes": memory["SwapFree"],
                "in_use": swap_in_use,
            },
        },
        "reasons": reasons,
        "warnings": ["swap_in_use"] if swap_in_use else [],
    }


def _render_payload(payload: dict[str, object]) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"
    if len(rendered.encode("ascii")) > MAX_JSON_BYTES:
        raise ValueError("resource gate JSON exceeds bounded output")
    return rendered


def _write_payload(stdout: TextIO, rendered: str) -> bool:
    try:
        written = stdout.write(rendered)
        if written != len(rendered):
            return False
        stdout.flush()
    except (OSError, UnicodeError, ValueError):
        return False
    return True


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    output = sys.stdout if stdout is None else stdout
    if arguments not in ([], ["--require-full-online"]):
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": "error",
            "error": "invalid_arguments",
        }
        exit_code = EX_USAGE
    else:
        require_full_online = arguments == ["--require-full-online"]
        try:
            bootstrapped = process_priority.ensure_resource_gate_priority_scope(
                arguments
            )
            payload = (
                _build_payload(require_full_online=require_full_online)
                if bootstrapped
                else _defer_payload(
                    "high_qos_unavailable",
                    require_full_online=require_full_online,
                )
            )
            exit_code = EX_OK if payload["status"] == "green" else EX_TEMPFAIL
        except process_priority.PriorityScopeError:
            payload = _defer_payload(
                "high_qos_unavailable",
                require_full_online=require_full_online,
            )
            exit_code = EX_TEMPFAIL
        except Exception:
            payload = {
                "schema_version": 1,
                "status": "error",
                "error": "internal_invariant",
            }
            exit_code = EX_SOFTWARE
    try:
        rendered = _render_payload(payload)
    except (OverflowError, TypeError, ValueError):
        rendered = '{"error":"internal_invariant","schema_version":1,"status":"error"}\n'
        exit_code = EX_SOFTWARE
    if not _write_payload(output, rendered):
        return EX_IOERR
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
