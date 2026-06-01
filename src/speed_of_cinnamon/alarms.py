from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .paths import alarms_file

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


def now_local() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def parse_alarm_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        raise ValueError("alarm time must use HH:MM")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("alarm time is outside 00:00-23:59")
    return hour, minute


def parse_repeat_days(value: str) -> list[str]:
    text = value.strip().lower()
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
    return f"{int(alarm.get('hour', 0)):02d}:{int(alarm.get('minute', 0)):02d}"


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
    state = "disabled" if not alarm.get("enabled", True) else alarm.get("urgency", "normal")
    return f"{format_alarm_time(alarm)} - {format_repeat_days(alarm_days(alarm))} - {str(state).capitalize()}"


def alarm_days(alarm: dict[str, Any]) -> list[str]:
    days = alarm.get("days")
    if not isinstance(days, list):
        return list(DAY_CODES)
    normalized = [str(day).lower()[:3] for day in days]
    return [day for day in DAY_CODES if day in normalized] or list(DAY_CODES)


def empty_store() -> dict[str, Any]:
    return {"version": STORE_VERSION, "alarms": [], "last_checked_at": ""}


def load_alarm_store(path: Path | None = None) -> dict[str, Any]:
    store_path = path or alarms_file()
    if not store_path.exists():
        return empty_store()
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"alarm store could not be parsed: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("alarm store must be a JSON object")
    alarms = raw.get("alarms", [])
    if not isinstance(alarms, list):
        raise RuntimeError("alarm store field 'alarms' must be a list")
    return {
        "version": STORE_VERSION,
        "alarms": [normalize_alarm(alarm) for alarm in alarms if isinstance(alarm, dict)],
        "last_checked_at": str(raw.get("last_checked_at") or ""),
    }


def save_alarm_store(store: dict[str, Any], path: Path | None = None) -> None:
    store_path = path or alarms_file()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STORE_VERSION,
        "alarms": [normalize_alarm(alarm) for alarm in store.get("alarms", []) if isinstance(alarm, dict)],
        "last_checked_at": str(store.get("last_checked_at") or ""),
    }
    tmp_path = store_path.with_suffix(store_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(store_path)


def normalize_alarm(alarm: dict[str, Any]) -> dict[str, Any]:
    hour = int(alarm.get("hour", 0))
    minute = int(alarm.get("minute", 0))
    hour = min(max(hour, 0), 23)
    minute = min(max(minute, 0), 59)
    urgency = str(alarm.get("urgency") or "normal").lower()
    if urgency not in URGENCIES:
        urgency = "normal"
    alarm_id = str(alarm.get("id") or "").strip()
    if not alarm_id:
        alarm_id = f"alarm-{hour:02d}{minute:02d}"
    return {
        "id": alarm_id,
        "name": str(alarm.get("name") or "").strip(),
        "hour": hour,
        "minute": minute,
        "days": alarm_days(alarm),
        "enabled": bool(alarm.get("enabled", True)),
        "urgency": urgency,
        "last_triggered_at": str(alarm.get("last_triggered_at") or ""),
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
    hour, minute = parse_alarm_time(alarm_time)
    normalized_urgency = urgency.strip().lower()
    if normalized_urgency not in URGENCIES:
        raise ValueError(f"urgency must be one of: {', '.join(sorted(URGENCIES))}")
    store = load_alarm_store(path)
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
    store["alarms"] = sorted(store["alarms"], key=lambda item: (int(item["hour"]), int(item["minute"]), str(item["id"])))
    save_alarm_store(store, path)
    return alarm_public_payload(alarm)


def remove_alarm(alarm_id: str, path: Path | None = None) -> dict[str, Any]:
    store = load_alarm_store(path)
    before = len(store["alarms"])
    store["alarms"] = [alarm for alarm in store["alarms"] if str(alarm.get("id")) != alarm_id]
    removed = len(store["alarms"]) != before
    save_alarm_store(store, path)
    return {"status": "done", "removed": removed, "id": alarm_id, "message": "alarm removed" if removed else "alarm not found"}


def set_alarm_enabled(alarm_id: str, enabled: bool, path: Path | None = None) -> dict[str, Any]:
    store = load_alarm_store(path)
    changed = False
    selected: dict[str, Any] | None = None
    for alarm in store["alarms"]:
        if str(alarm.get("id")) == alarm_id:
            alarm["enabled"] = enabled
            changed = True
            selected = alarm
            break
    save_alarm_store(store, path)
    return {
        "status": "done",
        "changed": changed,
        "id": alarm_id,
        "enabled": enabled,
        "alarm": alarm_public_payload(selected) if selected else None,
    }


def parse_local_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def iter_dates(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(max(days, 0) + 1)]


def alarm_occurrence(alarm: dict[str, Any], day: date) -> datetime | None:
    code = DAY_CODES[day.weekday()]
    if code not in alarm_days(alarm):
        return None
    return datetime.combine(day, time(int(alarm["hour"]), int(alarm["minute"])))


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
    current = (now or now_local()).replace(second=0, microsecond=0)
    store = load_alarm_store(path)
    last_checked = parse_local_datetime(str(store.get("last_checked_at") or ""))
    max_catch_up = max(0, int(catch_up_minutes))
    window_start = current - timedelta(minutes=max(1, max_catch_up))
    if last_checked:
        last_checked = last_checked.replace(second=0, microsecond=0)
        if last_checked <= current:
            window_start = max(last_checked, current - timedelta(minutes=max_catch_up))

    due: list[dict[str, Any]] = []
    for alarm in store["alarms"]:
        if not alarm.get("enabled", True):
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
    active = [alarm for alarm in alarms if alarm.get("enabled", True)]
    if not active:
        return "All alarms disabled"
    current = (now or now_local()).replace(second=0, microsecond=0)
    next_items = [(next_occurrence(alarm, current), alarm) for alarm in active]
    next_items = [(when, alarm) for when, alarm in next_items if when is not None]
    if not next_items:
        return f"{len(active)} active alarm(s)"
    next_at, next_alarm = min(next_items, key=lambda item: item[0])
    assert next_at is not None
    prefix = "1 active alarm" if len(active) == 1 else f"{len(active)} active alarms"
    label = format_alarm_name(next_alarm)
    if next_at.date() == current.date():
        return f"{prefix} - next {label} at {next_at.strftime('%H:%M')}"
    return f"{prefix} - next {label} on {DAY_LABELS[DAY_CODES[next_at.weekday()]]} at {next_at.strftime('%H:%M')}"
