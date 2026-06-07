from __future__ import annotations

import json
import os
import re
import fcntl
import stat
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterator

from .paths import alarms_file
from .path_safety import (
    assert_no_symlink_ancestors,
    assert_fd_is_regular_private_file,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)

STORE_VERSION = 1
DAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}
URGENCIES = {"silent", "normal", "critical"}
DEFAULT_CATCH_UP_MINUTES = 15
MAX_ALARM_STORE_BYTES = 1_000_000
MAX_ALARM_COUNT = 256
MAX_ALARM_STORE_PATH_CHARS = 4_096
MAX_ALARM_ID_CHARS = 120
MAX_ALARM_NAME_CHARS = 200
MAX_ALARM_TRIGGER_CHARS = 40
MAX_ALARM_DAYS_CHARS = 128
MAX_ALARM_TIME_CHARS = 16
MAX_ALARM_URGENCY_CHARS = 16


def _assert_clean_path(path: Path, *, field_name: str) -> None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} path must be a path")
    text = str(path)
    if not text or len(text) > MAX_ALARM_STORE_PATH_CHARS:
        raise RuntimeError(f"{field_name} path is invalid")
    if len(text.encode("utf-8")) > MAX_ALARM_STORE_PATH_CHARS:
        raise RuntimeError(f"{field_name} path is invalid")
    if _contains_escaped_null(text):
        raise RuntimeError(f"{field_name} contains invalid null byte")
    if _contains_forbidden_control(text):
        raise RuntimeError(f"{field_name} contains invalid control character")
    assert_safe_path_components(path, field_name=field_name)
    assert_no_symlink_ancestors(path, field_name=field_name)


def _alarm_store_path(path: Path | None = None) -> Path:
    if path is not None and not isinstance(path, Path):
        raise RuntimeError("alarm store path must be a path")
    return path or alarms_file()


@contextmanager
def _locked_alarm_store(path: Path | None = None) -> Iterator[Path]:
    store_path = _alarm_store_path(path)
    _assert_clean_path(store_path, field_name="alarm store")
    lock_path = store_path.with_name(f".{store_path.name}.lock")
    _assert_clean_path(lock_path, field_name="alarm store lock")
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RuntimeError("secure alarm store lock open is not supported on this platform")
    parent_fd = ensure_directory_without_following_symlinks(lock_path.parent, field_name="alarm store lock directory")
    try:
        fd = os.open(lock_path.name, os.O_RDWR | os.O_CREAT | nofollow_flag, 0o600, dir_fd=parent_fd)
    except OSError as exc:
        os.close(parent_fd)
        raise RuntimeError("failed to open alarm store lock file") from exc
    try:
        assert_fd_is_regular_private_file(fd, field_name="alarm store lock file", require_private_mode=True)
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert_fd_is_regular_private_file(fd, field_name="alarm store lock file", require_private_mode=True)
        yield store_path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            os.close(parent_fd)


def _contains_escaped_null(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RuntimeError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_forbidden_control(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RuntimeError("value must be text")
    lowered = (value or "").lower()
    control_codepoints = tuple(range(0x20)) + (0x7F,) + tuple(range(0x80, 0xA0))
    return (
        any(sequence in lowered for sequence in ("\\a", "\\b", "\\f", "\\n", "\\r", "\\t", "\\v"))
        or any(f"\\x{codepoint:02x}" in lowered or f"\\u00{codepoint:02x}" in lowered for codepoint in control_codepoints)
        or any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)
    )


def _sanitize_text_field(value: object, *, field_name: str, max_chars: int) -> str:
    if value is None:
        raw = ""
    elif isinstance(value, str) and not isinstance(value, bool):
        raw = value
    else:
        raise ValueError(f"{field_name} must be text")
    text = raw.strip()
    if not text and all(char in " \t\r\n\v\f" for char in raw):
        return ""
    if _contains_escaped_null(raw):
        raise ValueError(f"{field_name} contains invalid null byte")
    if _contains_forbidden_control(raw):
        raise ValueError(f"{field_name} contains invalid control character")
    if len(text) > max_chars:
        raise ValueError(f"{field_name} is too large (max {max_chars} characters)")
    if len(text.encode("utf-8")) > max_chars:
        raise ValueError(f"{field_name} is too large (max {max_chars} bytes)")
    return text


def now_local() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


MAX_CATCH_UP_MINUTES = 14_400


def parse_alarm_time(value: str) -> tuple[int, int]:
    text = _sanitize_text_field(value, field_name="alarm time", max_chars=MAX_ALARM_TIME_CHARS)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError("alarm time must use HH:MM")
    hour = _coerce_alarm_component(match.group(1), field_name="alarm hour")
    minute = _coerce_alarm_component(match.group(2), field_name="alarm minute")
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("alarm time is outside 00:00-23:59")
    return hour, minute


def _coerce_alarm_component(value: object, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, float):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _coerce_alarm_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _is_alarm_enabled(alarm: dict[str, Any]) -> bool:
    try:
        return _coerce_alarm_bool(alarm.get("enabled", True), field_name="alarm enabled")
    except ValueError:
        return False


def parse_repeat_days(value: str) -> list[str]:
    text = _sanitize_text_field(value, field_name="alarm days", max_chars=MAX_ALARM_DAYS_CHARS).strip().lower()
    if text in {"", "all", "daily", "everyday"}:
        return list(DAY_CODES)
    if text in {"weekday", "weekdays", "workdays"}:
        return list(DAY_CODES[:5])
    if text in {"weekend", "weekends"}:
        return list(DAY_CODES[5:])

    days: list[str] = []
    for chunk in re.split(r"[, ]+", text):
        if not chunk:
            continue
        code = chunk[:3]
        if code not in DAY_CODES:
            raise ValueError(f"unknown repeat day: {chunk}")
        if code not in days:
            days.append(code)
    if not days:
        return list(DAY_CODES)
    return days


def format_alarm_time(alarm: dict[str, Any]) -> str:
    hour = _coerce_alarm_component(alarm.get("hour", 0), field_name="alarm hour")
    minute = _coerce_alarm_component(alarm.get("minute", 0), field_name="alarm minute")
    return f"{hour:02d}:{minute:02d}"


def format_alarm_name(alarm: dict[str, Any]) -> str:
    name = str(alarm.get("name") or "").strip()
    return name or f"Alarm {format_alarm_time(alarm)}"


def format_repeat_days(days: list[str]) -> str:
    normalized = [day for day in DAY_CODES if day in days]
    if normalized == list(DAY_CODES):
        return "Daily"
    if normalized == list(DAY_CODES[:5]):
        return "Weekdays"
    if normalized == list(DAY_CODES[5:]):
        return "Weekends"
    return ", ".join(DAY_LABELS[day] for day in normalized)


def format_alarm_summary(alarm: dict[str, Any]) -> str:
    state = "disabled" if not _is_alarm_enabled(alarm) else alarm.get("urgency", "normal")
    return f"{format_alarm_time(alarm)} - {format_repeat_days(alarm_days(alarm))} - {str(state).capitalize()}"


def alarm_days(alarm: dict[str, Any]) -> list[str]:
    days = alarm.get("days")
    if not isinstance(days, list):
        return list(DAY_CODES)
    if not all(isinstance(day, str) for day in days):
        return list(DAY_CODES)
    normalized = [str(day).lower()[:3] for day in days]
    return [day for day in DAY_CODES if day in normalized] or list(DAY_CODES)


def _normalize_alarm_list(alarms: object) -> list[dict[str, Any]]:
    if not isinstance(alarms, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw_alarm in alarms:
        if len(normalized) >= MAX_ALARM_COUNT:
            break
        if not isinstance(raw_alarm, dict):
            continue
        try:
            normalized.append(normalize_alarm(raw_alarm))
        except (TypeError, ValueError):
            continue
    return _dedupe_alarm_ids(normalized)


def _dedupe_alarm_ids(alarms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for alarm in alarms:
        item = dict(alarm)
        hour = _coerce_alarm_component(item.get("hour", 0), field_name="alarm hour")
        minute = _coerce_alarm_component(item.get("minute", 0), field_name="alarm minute")
        base_id = _sanitize_text_field(item.get("id"), field_name="alarm id", max_chars=MAX_ALARM_ID_CHARS)
        if not base_id:
            base_id = f"alarm-{hour:02d}{minute:02d}"
        candidate = base_id
        index = 2
        while not candidate or candidate in used_ids:
            suffix = f"-{index}"
            candidate = f"{base_id[: MAX_ALARM_ID_CHARS - len(suffix)]}{suffix}"
            index += 1
        item["id"] = candidate
        used_ids.add(candidate)
        normalized.append(item)
    return normalized


def empty_store() -> dict[str, Any]:
    return {"version": STORE_VERSION, "alarms": [], "last_checked_at": ""}


def load_alarm_store(path: Path | None = None) -> dict[str, Any]:
    store_path = path or alarms_file()
    _assert_clean_path(store_path, field_name="alarm store path")
    try:
        assert_no_symlink_ancestors(store_path, field_name="alarm store path")
        file_stat = store_path.lstat()
    except FileNotFoundError:
        return empty_store()
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"alarm store could not be read: {store_path}") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"alarm store could not be read: {store_path}")
    if file_stat.st_size > MAX_ALARM_STORE_BYTES:
        raise RuntimeError(f"alarm store is too large: {store_path}")
    try:
        text = read_text_without_following_symlinks(
            store_path,
            field_name="alarm store path",
            max_bytes=MAX_ALARM_STORE_BYTES,
            require_private_mode=True,
        )
    except OSError as exc:
        if "too large" in str(exc):
            raise RuntimeError(f"alarm store is too large: {store_path}") from exc
        raise RuntimeError(f"alarm store could not be read: {store_path}") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"alarm store could not be parsed: {exc}") from exc
    if _contains_escaped_null(text):
        raise RuntimeError("alarm store contains invalid null byte")
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError) as exc:
        if isinstance(exc, OSError):
            raise RuntimeError(f"alarm store could not be read: {store_path}") from exc
        raise RuntimeError(f"alarm store could not be parsed: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("alarm store must be a JSON object")
    try:
        raw_last_checked_at = _sanitize_text_field(
            raw.get("last_checked_at"), field_name="alarm store last_checked_at", max_chars=MAX_ALARM_TRIGGER_CHARS
        )
    except ValueError as exc:
        if "too large" in str(exc):
            raise RuntimeError(f"alarm store last_checked_at is too large (max {MAX_ALARM_TRIGGER_CHARS} characters)") from exc
        raise RuntimeError(f"alarm store last_checked_at contains invalid null byte") from exc
    return {
        "version": STORE_VERSION,
        "alarms": _normalize_alarm_list(raw.get("alarms", [])),
        "last_checked_at": raw_last_checked_at,
    }


def save_alarm_store(store: dict[str, Any], path: Path | None = None) -> None:
    store_path = path or alarms_file()
    _assert_clean_path(store_path, field_name="alarm store path")
    payload = {
        "version": STORE_VERSION,
        "alarms": _normalize_alarm_list(store.get("alarms", [])),
        "last_checked_at": _sanitize_text_field(
            store.get("last_checked_at"),
            field_name="alarm store last_checked_at",
            max_chars=MAX_ALARM_TRIGGER_CHARS,
        ),
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(rendered.encode("utf-8")) > MAX_ALARM_STORE_BYTES:
        raise RuntimeError("alarm store is too large")
    try:
        write_text_atomically_without_following_symlinks(
            store_path,
            rendered,
            field_name="alarm store path",
        )
    except OSError as exc:
        raise RuntimeError(f"failed to persist alarm store: {store_path}") from exc


def normalize_alarm(alarm: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(alarm, dict):
        raise ValueError("alarm must be a dictionary")
    if isinstance(alarm.get("hour", 0), bool):
        raise ValueError("alarm hour must be an integer")
    if isinstance(alarm.get("minute", 0), bool):
        raise ValueError("alarm minute must be an integer")
    hour = _coerce_alarm_component(alarm.get("hour", 0), field_name="alarm hour")
    minute = _coerce_alarm_component(alarm.get("minute", 0), field_name="alarm minute")
    if hour < 0 or hour > 23:
        raise ValueError("alarm hour must be between 0 and 23")
    if minute < 0 or minute > 59:
        raise ValueError("alarm minute must be between 0 and 59")
    urgency = str(alarm.get("urgency") or "normal").lower()
    if urgency not in URGENCIES:
        raise ValueError("alarm urgency must be one of: normal, silent, critical")
    alarm_id = _sanitize_text_field(alarm.get("id"), field_name="alarm id", max_chars=MAX_ALARM_ID_CHARS)
    if not alarm_id:
        alarm_id = f"alarm-{hour:02d}{minute:02d}"
    return {
        "id": alarm_id,
        "name": _sanitize_text_field(alarm.get("name"), field_name="alarm name", max_chars=MAX_ALARM_NAME_CHARS),
        "hour": hour,
        "minute": minute,
        "days": alarm_days(alarm),
        "enabled": _coerce_alarm_bool(alarm.get("enabled", True), field_name="alarm enabled"),
        "urgency": urgency,
        "last_triggered_at": _sanitize_text_field(
            alarm.get("last_triggered_at"),
            field_name="last_triggered_at",
            max_chars=MAX_ALARM_TRIGGER_CHARS,
        ),
    }


def alarm_public_payload(alarm: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_alarm(alarm)
    return {
        **normalized,
        "time": format_alarm_time(normalized),
        "label": format_alarm_name(normalized),
        "summary": format_alarm_summary(normalized),
    }


def list_alarm_payload(path: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    store = load_alarm_store(path)
    alarms = [alarm_public_payload(alarm) for alarm in store["alarms"]]
    return {
        "status": "done",
        "alarms": alarms,
        "summary": format_alarm_overview(alarms, now or now_local()),
        "last_checked_at": store.get("last_checked_at", ""),
    }


def next_alarm_id(store: dict[str, Any], hour: int, minute: int) -> str:
    existing = {str(alarm.get("id")) for alarm in store.get("alarms", [])}
    base = f"alarm-{hour:02d}{minute:02d}"
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def add_alarm(
    alarm_time: str,
    name: str = "",
    days: str = "daily",
    urgency: str = "normal",
    enabled: bool = True,
    path: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(alarm_time, str) or isinstance(alarm_time, bool):
        raise ValueError("alarm time must be text")
    if not isinstance(name, str) or isinstance(name, bool):
        raise ValueError("alarm name must be text")
    if not isinstance(days, str) or isinstance(days, bool):
        raise ValueError("alarm days must be text")
    if not isinstance(urgency, str) or isinstance(urgency, bool):
        raise ValueError("urgency must be text")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    hour, minute = parse_alarm_time(alarm_time)
    normalized_urgency = _sanitize_text_field(urgency, field_name="urgency", max_chars=MAX_ALARM_URGENCY_CHARS).strip().lower()
    if normalized_urgency not in URGENCIES:
        raise ValueError(f"urgency must be one of: {', '.join(sorted(URGENCIES))}")
    with _locked_alarm_store(path) as store_path:
        store = load_alarm_store(store_path)
        alarm = normalize_alarm(
            {
                "id": next_alarm_id(store, hour, minute),
                "name": name,
                "hour": hour,
                "minute": minute,
                "days": parse_repeat_days(days),
                "enabled": enabled,
                "urgency": normalized_urgency,
            }
        )
        store["alarms"].append(alarm)
        store["alarms"] = sorted(
            store["alarms"],
            key=lambda item: (
                _coerce_alarm_component(item.get("hour", 0), field_name="alarm hour"),
                _coerce_alarm_component(item.get("minute", 0), field_name="alarm minute"),
                str(item.get("id", "")),
            ),
        )
        save_alarm_store(store, store_path)
    return alarm_public_payload(alarm)


def remove_alarm(alarm_id: str, path: Path | None = None) -> dict[str, Any]:
    if not isinstance(alarm_id, str) or isinstance(alarm_id, bool):
        raise ValueError("alarm id must be text")
    normalized_alarm_id = _sanitize_text_field(alarm_id, field_name="alarm id", max_chars=MAX_ALARM_ID_CHARS)
    if not normalized_alarm_id:
        raise ValueError("alarm id is required")
    with _locked_alarm_store(path) as store_path:
        store = load_alarm_store(store_path)
        before = len(store["alarms"])
        store["alarms"] = [alarm for alarm in store["alarms"] if str(alarm.get("id")) != normalized_alarm_id]
        removed = len(store["alarms"]) != before
        if removed:
            save_alarm_store(store, store_path)
    return {
        "status": "done",
        "removed": removed,
        "id": normalized_alarm_id,
        "message": "alarm removed" if removed else "alarm not found",
    }


def set_alarm_enabled(alarm_id: str, enabled: bool, path: Path | None = None) -> dict[str, Any]:
    if not isinstance(alarm_id, str) or isinstance(alarm_id, bool):
        raise ValueError("alarm id must be text")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    normalized_alarm_id = _sanitize_text_field(alarm_id, field_name="alarm id", max_chars=MAX_ALARM_ID_CHARS)
    if not normalized_alarm_id:
        raise ValueError("alarm id is required")
    with _locked_alarm_store(path) as store_path:
        store = load_alarm_store(store_path)
        changed = False
        selected: dict[str, Any] | None = None
        for alarm in store["alarms"]:
            if str(alarm.get("id")) == normalized_alarm_id:
                selected = alarm
                if alarm.get("enabled") != enabled:
                    alarm["enabled"] = enabled
                    changed = True
                break
        if changed:
            save_alarm_store(store, store_path)
    return {
        "status": "done",
        "changed": changed,
        "id": normalized_alarm_id,
        "enabled": enabled,
        "alarm": alarm_public_payload(selected) if selected else None,
    }


def parse_local_datetime(value: str) -> datetime | None:
    if not isinstance(value, str) or isinstance(value, bool):
        raise ValueError("value must be text")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def iter_dates(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(max(days, 0) + 1)]


def alarm_occurrence(alarm: dict[str, Any], day: date) -> datetime | None:
    code = DAY_CODES[day.weekday()]
    if code not in alarm_days(alarm):
        return None
    return datetime.combine(
        day,
        time(
            _coerce_alarm_component(alarm.get("hour", 0), field_name="alarm hour"),
            _coerce_alarm_component(alarm.get("minute", 0), field_name="alarm minute"),
        ),
    )


def _coerce_required_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def due_occurrences(alarm: dict[str, Any], start: datetime, end: datetime) -> list[datetime]:
    occurrences: list[datetime] = []
    for day in iter_dates(start.date(), end.date()):
        candidate = alarm_occurrence(alarm, day)
        if candidate and start <= candidate <= end:
            occurrences.append(candidate)
    return occurrences


def notification_body(alarm: dict[str, Any], scheduled_at: datetime) -> str:
    return f"{format_alarm_name(alarm)} is due at {scheduled_at.strftime('%H:%M')}."


def check_due_alarms(
    path: Path | None = None,
    now: datetime | None = None,
    mark: bool = False,
    catch_up_minutes: int = DEFAULT_CATCH_UP_MINUTES,
) -> dict[str, Any]:
    mark = _coerce_required_bool(mark, field_name="mark")
    current = (now or now_local()).replace(second=0, microsecond=0)
    if mark:
        with _locked_alarm_store(path) as store_path:
            return _check_due_alarms_locked(
                path=store_path,
                current=current,
                mark=mark,
                catch_up_minutes=catch_up_minutes,
            )
    return _check_due_alarms_locked(path=path, current=current, mark=mark, catch_up_minutes=catch_up_minutes)


def _check_due_alarms_locked(
    path: Path | None,
    current: datetime,
    mark: bool,
    catch_up_minutes: int,
) -> dict[str, Any]:
    store = load_alarm_store(path)
    last_checked = parse_local_datetime(str(store.get("last_checked_at") or ""))
    if not isinstance(catch_up_minutes, int) or isinstance(catch_up_minutes, bool):
        raise ValueError("catch-up minutes must be an integer")
    max_catch_up = catch_up_minutes
    if max_catch_up < 0:
        raise ValueError("catch-up minutes must be at least 0")
    if max_catch_up > MAX_CATCH_UP_MINUTES:
        raise ValueError(f"catch-up minutes must be at most {MAX_CATCH_UP_MINUTES}")
    if max_catch_up == 0:
        window_start = current
    else:
        window_start = current - timedelta(minutes=max_catch_up)
    if last_checked and max_catch_up > 0:
        last_checked = last_checked.replace(second=0, microsecond=0)
        if last_checked <= current:
            window_start = max(last_checked, current - timedelta(minutes=max_catch_up))

    due: list[dict[str, Any]] = []
    for alarm in store["alarms"]:
        if not _is_alarm_enabled(alarm):
            continue
        occurrences = due_occurrences(alarm, window_start, current)
        if not occurrences:
            continue
        scheduled_at = occurrences[-1]
        scheduled_key = scheduled_at.isoformat(timespec="minutes")
        if str(alarm.get("last_triggered_at") or "") == scheduled_key:
            continue
        if mark:
            alarm["last_triggered_at"] = scheduled_key
        urgency = str(alarm.get("urgency") or "normal")
        due.append(
            {
                **alarm_public_payload(alarm),
                "scheduled_at": scheduled_key,
                "body": notification_body(alarm, scheduled_at),
                "notify": urgency != "silent",
                "critical": urgency == "critical",
            }
        )

    if mark:
        store["last_checked_at"] = current.isoformat(timespec="minutes")
        save_alarm_store(store, path)

    return {
        "status": "done",
        "due": due,
        "count": len(due),
        "checked_at": current.isoformat(timespec="minutes"),
        "window_start": window_start.isoformat(timespec="minutes"),
        "marked": mark,
    }


def next_occurrence(alarm: dict[str, Any], now: datetime) -> datetime | None:
    current = now.replace(second=0, microsecond=0)
    for offset in range(8):
        day = current.date() + timedelta(days=offset)
        candidate = alarm_occurrence(alarm, day)
        if candidate and candidate >= current:
            return candidate
    return None


def format_alarm_overview(alarms: list[dict[str, Any]], now: datetime | None = None) -> str:
    if not alarms:
        return "No alarms configured"
    active = [alarm for alarm in alarms if _is_alarm_enabled(alarm)]
    if not active:
        return "All alarms disabled"
    current = (now or now_local()).replace(second=0, microsecond=0)
    next_items = [(next_occurrence(alarm, current), alarm) for alarm in active]
    next_items = [(when, alarm) for when, alarm in next_items if when is not None]
    if not next_items:
        return f"{len(active)} active alarm(s)"
    next_at, next_alarm = min(next_items, key=lambda item: item[0])
    if next_at is None:
        raise RuntimeError("next occurrence is missing")
    prefix = "1 active alarm" if len(active) == 1 else f"{len(active)} active alarms"
    label = format_alarm_name(next_alarm)
    if next_at.date() == current.date():
        return f"{prefix} - next {label} at {next_at.strftime('%H:%M')}"
    return f"{prefix} - next {label} on {DAY_LABELS[DAY_CODES[next_at.weekday()]]} at {next_at.strftime('%H:%M')}"
