from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import alarms as alarm_module
from speed_of_cinnamon import cli
from speed_of_cinnamon.alarms import (
    add_alarm,
    remove_alarm,
    load_alarm_store,
    save_alarm_store,
    check_due_alarms,
    set_alarm_enabled,
    format_alarm_overview,
    list_alarm_payload,
    parse_alarm_time,
    parse_repeat_days,
    normalize_alarm,
    format_alarm_time,
    alarm_occurrence,
    MAX_ALARM_STORE_BYTES,
    MAX_ALARM_NAME_CHARS,
    MAX_ALARM_COUNT,
    MAX_ALARM_STORE_PATH_CHARS,
    MAX_ALARM_ID_CHARS,
    MAX_ALARM_TRIGGER_CHARS,
    MAX_ALARM_DAYS_CHARS,
)


class AlarmTest(unittest.TestCase):
    def test_load_alarm_store_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            load_alarm_store(Path("alarms\x00.json"))

    def test_load_alarm_store_rejects_escaped_null_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            load_alarm_store(Path("alarms\\\\x00.json"))

    def test_load_alarm_store_rejects_oversized_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "path is invalid"):
            load_alarm_store(Path("a" * (MAX_ALARM_STORE_PATH_CHARS + 1)))

    def test_load_alarm_store_rejects_oversized_path_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.alarms.MAX_ALARM_STORE_PATH_CHARS", 4):
            with self.assertRaisesRegex(RuntimeError, "path is invalid"):
                load_alarm_store(Path("é" * 3))

    def test_save_alarm_store_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            save_alarm_store({}, Path("alarms\x00.json"))

    def test_save_alarm_store_rejects_oversized_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "path is invalid"):
            save_alarm_store({}, Path("a" * (MAX_ALARM_STORE_PATH_CHARS + 1)))

    def test_save_alarm_store_rejects_oversized_path_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.alarms.MAX_ALARM_STORE_PATH_CHARS", 4):
            with self.assertRaisesRegex(RuntimeError, "path is invalid"):
                save_alarm_store({}, Path("é" * 3))

    def test_load_alarm_store_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text("x" * (MAX_ALARM_STORE_BYTES + 1), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "alarm store is too large"):
                load_alarm_store(path)

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_load_alarm_store_uses_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text('{"version":1,"alarms":[],"last_checked_at":""}', encoding="utf-8")
            payload = load_alarm_store(path)
        self.assertEqual(payload["version"], 1)
        self.assertTrue(mocked_open.called)
        self.assertTrue(
            any(
                args[0] == path.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_repeat_day_parser_supports_common_groups(self) -> None:
        self.assertEqual(parse_repeat_days("daily"), ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        self.assertEqual(parse_repeat_days("weekdays"), ["mon", "tue", "wed", "thu", "fri"])
        self.assertEqual(parse_repeat_days("weekends"), ["sat", "sun"])
        self.assertEqual(parse_repeat_days("mon,wed,fri"), ["mon", "wed", "fri"])

    def test_repeat_day_parser_rejects_null_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm days contains invalid null byte"):
            parse_repeat_days("mon\x00,fri")

    def test_parse_alarm_time_rejects_null_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm time contains invalid null byte"):
            parse_alarm_time("09:00\x00")

    def test_parse_alarm_time_rejects_oversized_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "is too large"):
            parse_alarm_time(("9" * 20) + ":00")

    def test_parse_alarm_time_rejects_non_string_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm time must be text"):
            parse_alarm_time(123)  # type: ignore[arg-type]

    def test_format_alarm_time_rejects_boolean_hour(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm hour must be an integer"):
            format_alarm_time({"hour": True, "minute": 30})

    def test_format_alarm_time_rejects_boolean_minute(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm minute must be an integer"):
            format_alarm_time({"hour": 9, "minute": False})

    def test_repeat_day_parser_rejects_oversized_input(self) -> None:
        value = ",".join(["mon", "tue", "wed", "thu", "fri", "sat", "sun"] * 30)
        self.assertGreater(len(value), MAX_ALARM_DAYS_CHARS)
        with self.assertRaisesRegex(ValueError, "alarm days is too large"):
            parse_repeat_days(value)

    def test_repeat_day_parser_rejects_oversized_input_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.alarms.MAX_ALARM_DAYS_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "alarm days is too large"):
                parse_repeat_days("😀" * 2)

    def test_alarm_overview_reports_next_active_alarm(self) -> None:
        now = datetime(2026, 6, 1, 8, 0)
        alarms = [
            {
                "id": "alarm-0830",
                "name": "Standup",
                "hour": 8,
                "minute": 30,
                "days": ["mon", "wed", "fri"],
                "enabled": True,
                "urgency": "normal",
            },
            {
                "id": "alarm-0700",
                "name": "Disabled",
                "hour": 7,
                "minute": 0,
                "days": ["mon"],
                "enabled": False,
                "urgency": "normal",
            },
        ]

        self.assertEqual(format_alarm_overview(alarms, now), "1 active alarm - next Standup at 08:30")

    def test_due_check_marks_alarm_and_prevents_duplicate_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            add_alarm("09:00", name="Morning", days="mon", path=path)
            due_at = datetime(2026, 6, 1, 9, 0)

            first = check_due_alarms(path=path, now=due_at, mark=True)
            second = check_due_alarms(path=path, now=due_at + timedelta(seconds=30), mark=True)

        self.assertEqual(first["count"], 1)
        self.assertEqual(first["due"][0]["label"], "Morning")
        self.assertEqual(first["due"][0]["scheduled_at"], "2026-06-01T09:00")
        self.assertEqual(second["count"], 0)

    def test_due_check_catches_recent_missed_alarm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            add_alarm("09:00", name="Morning", days="mon", path=path)
            payload = check_due_alarms(path=path, now=datetime(2026, 6, 1, 9, 10), mark=True, catch_up_minutes=15)

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["due"][0]["scheduled_at"], "2026-06-01T09:00")

    def test_due_check_with_zero_catch_up_skips_past_alarm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            add_alarm("09:00", name="Morning", days="mon", path=path)
            payload = check_due_alarms(path=path, now=datetime(2026, 6, 1, 9, 10), mark=True, catch_up_minutes=0)

        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["due"], [])

    def test_cli_adds_lists_checks_and_removes_alarm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--name",
                    "Standup",
                    "--days",
                    "weekdays",
                    "--json",
                ])
            added = json.loads(stdout.getvalue())

            stdout = io.StringIO()
            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                list_code = cli.run(["alarms", "list", "--json"])
            listed = json.loads(stdout.getvalue())

            alarm_id = added["alarm"]["id"]
            stdout = io.StringIO()
            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                remove_code = cli.run(["alarms", "remove", alarm_id, "--json"])
            removed = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(list_code, 0)
        self.assertEqual(remove_code, 0)
        self.assertEqual(added["alarm"]["label"], "Standup")
        self.assertEqual(listed["alarms"][0]["days"], ["mon", "tue", "wed", "thu", "fri"])
        self.assertTrue(removed["removed"])

    def test_list_payload_uses_empty_store_when_no_alarms_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = list_alarm_payload(Path(tmp) / "missing.json", datetime(2026, 6, 1, 9, 0))

        self.assertEqual(payload["alarms"], [])
        self.assertEqual(payload["summary"], "No alarms configured")

    def test_normalize_alarm_rejects_null_byte_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm name contains invalid null byte"):
            normalize_alarm({"name": "Morning\x00", "hour": 9, "minute": 0})

    def test_normalize_alarm_rejects_null_byte_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm id contains invalid null byte"):
            normalize_alarm({"id": "alarm\x00id", "hour": 9, "minute": 0})

    def test_normalize_alarm_rejects_oversized_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm name is too large"):
            normalize_alarm({"name": "A" * (MAX_ALARM_NAME_CHARS + 10), "hour": 9, "minute": 0})

    def test_normalize_alarm_rejects_oversized_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm id is too large"):
            normalize_alarm({"id": "X" * (MAX_ALARM_ID_CHARS + 25), "hour": 9, "minute": 0})

    def test_normalize_alarm_rejects_hour_out_of_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm hour must be between"):
            normalize_alarm({"name": "Morning", "hour": 24, "minute": 0})

    def test_normalize_alarm_rejects_minute_out_of_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm minute must be between"):
            normalize_alarm({"name": "Morning", "hour": 9, "minute": 60})

    def test_normalize_alarm_rejects_invalid_urgency(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm urgency must be one of"):
            normalize_alarm({"name": "Morning", "hour": 9, "minute": 0, "urgency": "urgent"})

    def test_normalize_alarm_rejects_non_dict(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm must be a dictionary"):
            normalize_alarm([])  # type: ignore[arg-type]

    def test_check_due_alarms_rejects_negative_catch_up_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                '{"version":1,"alarms":[],"last_checked_at":""}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "catch-up minutes must be at least"):
                check_due_alarms(path=path, catch_up_minutes=-1)

    def test_check_due_alarms_rejects_excessive_catch_up_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                '{"version":1,"alarms":[],"last_checked_at":""}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "catch-up minutes must be at most"):
                check_due_alarms(path=path, catch_up_minutes=14401)

    def test_check_due_alarms_rejects_non_int_catch_up_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                '{"version":1,"alarms":[],"last_checked_at":""}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "catch-up minutes must be an integer"):
                check_due_alarms(path=path, catch_up_minutes="5")  # type: ignore[arg-type]

    def test_check_due_alarms_rejects_non_boolean_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                '{"version":1,"alarms":[],"last_checked_at":""}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "mark must be a boolean"):
                check_due_alarms(path=path, mark="true")  # type: ignore[arg-type]

    def test_normalize_alarm_rejects_null_byte_last_triggered_at(self) -> None:
        with self.assertRaisesRegex(ValueError, "last_triggered_at contains invalid null byte"):
            normalize_alarm({
                "name": "Morning",
                "hour": 9,
                "minute": 0,
                "last_triggered_at": "2026-06-01T09:00\x00",
            })

    def test_normalize_alarm_rejects_boolean_hour(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm hour must be an integer"):
            normalize_alarm({
                "name": "Morning",
                "hour": True,
                "minute": 30,
            })

    def test_normalize_alarm_rejects_boolean_minute(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm minute must be an integer"):
            normalize_alarm({
                "name": "Morning",
                "hour": 9,
                "minute": False,
            })

    def test_normalize_alarm_rejects_float_hour(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm hour must be an integer"):
            normalize_alarm({
                "name": "Morning",
                "hour": 9.5,  # type: ignore[arg-type]
                "minute": 30,
            })

    def test_normalize_alarm_rejects_float_minute(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm minute must be an integer"):
            normalize_alarm({
                "name": "Morning",
                "hour": 9,
                "minute": 30.0,  # type: ignore[arg-type]
            })

    def test_normalize_alarm_rejects_non_boolean_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm enabled must be a boolean"):
            normalize_alarm({
                "name": "Morning",
                "hour": 9,
                "minute": 30,
                "enabled": "nope",
            })

    def test_normalize_alarm_rejects_invalid_enabled_integer(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm enabled must be a boolean"):
            normalize_alarm({
                "name": "Morning",
                "hour": 9,
                "minute": 30,
                "enabled": 2,  # type: ignore[arg-type]
            })

    def test_alarm_occurrence_rejects_boolean_hour(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm hour must be an integer"):
            alarm_occurrence({"hour": True, "minute": 30}, date(2026, 6, 1))

    def test_alarm_occurrence_rejects_boolean_minute(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm minute must be an integer"):
            alarm_occurrence({"hour": 9, "minute": False}, date(2026, 6, 1))

    def test_alarm_occurrence_rejects_float_hour(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm hour must be an integer"):
            alarm_occurrence({"hour": 9.1, "minute": 30}, date(2026, 6, 1))  # type: ignore[arg-type]

    def test_alarm_occurrence_rejects_float_minute(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm minute must be an integer"):
            alarm_occurrence({"hour": 9, "minute": 30.9}, date(2026, 6, 1))  # type: ignore[arg-type]

    def test_normalize_alarm_rejects_oversized_last_triggered_at(self) -> None:
        with self.assertRaisesRegex(ValueError, "last_triggered_at is too large"):
            normalize_alarm({
                "name": "Morning",
                "hour": 9,
                "minute": 0,
                "last_triggered_at": "x" * (MAX_ALARM_TRIGGER_CHARS + 10),
            })

    def test_remove_alarm_rejects_empty_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm id is required"):
            remove_alarm("   ")

    def test_remove_alarm_rejects_non_text_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm id must be text"):
            remove_alarm(123)  # type: ignore[arg-type]

    def test_set_alarm_enabled_rejects_empty_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm id is required"):
            set_alarm_enabled(" \t", False)

    def test_set_alarm_enabled_rejects_null_byte_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm id contains invalid null byte"):
            set_alarm_enabled("alarm\x00id", False)

    def test_set_alarm_enabled_rejects_non_bool_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "enabled must be a boolean"):
            set_alarm_enabled("alarm-id", 1)  # type: ignore[arg-type]

    def test_set_alarm_enabled_rejects_non_text_alarm_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm id must be text"):
            set_alarm_enabled(123, True)  # type: ignore[arg-type]

    def test_add_alarm_rejects_invalid_types(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm time must be text"):
            add_alarm(123, name="Morning", path=Path("/tmp/test"))  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "alarm name must be text"):
            add_alarm("09:00", name=123)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "alarm days must be text"):
            add_alarm("09:00", days=123)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "urgency must be text"):
            add_alarm("09:00", urgency=123)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "enabled must be a boolean"):
            add_alarm("09:00", enabled="yes")  # type: ignore[arg-type]

    def test_parse_repeat_days_rejects_non_string_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "alarm days must be text"):
            parse_repeat_days(123)  # type: ignore[arg-type]

    def test_load_alarm_store_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text("{invalid}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "alarm store could not be parsed"):
                load_alarm_store(path)

    def test_load_alarm_store_rejects_null_byte_last_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                '{"version":1,"alarms":[],"last_checked_at":"2026-06-01T09:00\x00"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "alarm store contains invalid null byte"):
                load_alarm_store(path)

    def test_load_alarm_store_rejects_escaped_x00_last_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                '{"version":1,"alarms":[],"last_checked_at":"2026-06-01T09:00\\\\x00"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "contains invalid null byte"):
                load_alarm_store(path)

    def test_load_alarm_store_rejects_non_object_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "alarm store must be a JSON object"):
                load_alarm_store(path)

    def test_load_alarm_store_rejects_invalid_utf8_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(RuntimeError, "alarm store could not be parsed"):
                load_alarm_store(path)

    def test_load_alarm_store_skips_non_list_alarms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                '{"version":1,"alarms":{"id":"bad"},"last_checked_at":""}',
                encoding="utf-8",
            )
            payload = load_alarm_store(path)

        self.assertEqual(payload["alarms"], [])

    def test_load_alarm_store_rejects_oversized_last_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            payload = (
                '{"version":1,"alarms":[],"last_checked_at":"'
                + ("x" * (MAX_ALARM_TRIGGER_CHARS + 10))
                + '"}'
            )
            path.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "alarm store last_checked_at is too large"):
                load_alarm_store(path)

    def test_load_alarm_store_skips_invalid_alarm_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "last_checked_at": "",
                        "alarms": [
                            {"id": "good", "hour": 9, "minute": 0, "days": ["mon"], "name": "Good"},
                            {"id": "bad", "hour": "not-a-number", "minute": 0, "days": ["mon"], "name": "Bad"},
                            {"id": "bad-enabled", "hour": 9, "minute": 0, "days": ["mon"], "name": "Bad Enabled", "enabled": "yes"},
                        ],
                    },
                ),
                encoding="utf-8",
            )
            payload = load_alarm_store(path)

        self.assertEqual(len(payload["alarms"]), 1)
        self.assertEqual(payload["alarms"][0]["id"], "good")

    def test_load_alarm_store_skips_alarm_with_invalid_urgency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "last_checked_at": "",
                        "alarms": [
                            {"id": "good", "hour": 9, "minute": 0, "days": ["mon"], "name": "Good", "urgency": "normal"},
                            {"id": "bad", "hour": 9, "minute": 0, "days": ["mon"], "name": "Bad", "urgency": "urgent"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = load_alarm_store(path)

        self.assertEqual(len(payload["alarms"]), 1)
        self.assertEqual(payload["alarms"][0]["id"], "good")

    def test_load_and_save_alarm_store_assign_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "last_checked_at": "",
                    "alarms": [
                        {"id": "custom", "hour": 9, "minute": 0, "days": ["mon"], "name": "First"},
                        {"id": "", "hour": 9, "minute": 0, "days": ["mon"], "name": "Second"},
                        {"id": "custom", "hour": 10, "minute": 0, "days": ["mon"], "name": "Third"},
                    ],
                },
                path,
            )
            payload = load_alarm_store(path)

        self.assertEqual([alarm["id"] for alarm in payload["alarms"]], ["custom", "alarm-0900", "custom-2"])
        self.assertEqual(payload["alarms"][0]["name"], "First")
        self.assertEqual(payload["alarms"][1]["name"], "Second")
        self.assertEqual(payload["alarms"][2]["name"], "Third")

    def test_duplicate_alarm_id_suffixes_stay_within_limit(self) -> None:
        alarm_id = "a" * MAX_ALARM_ID_CHARS
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "last_checked_at": "",
                    "alarms": [
                        {"id": alarm_id, "hour": 9, "minute": 0},
                        {"id": alarm_id, "hour": 10, "minute": 0},
                    ],
                },
                path,
            )
            payload = load_alarm_store(path)

        self.assertEqual(payload["alarms"][0]["id"], alarm_id)
        self.assertEqual(len(payload["alarms"][1]["id"]), MAX_ALARM_ID_CHARS)
        self.assertTrue(payload["alarms"][1]["id"].endswith("-2"))

    def test_load_alarm_store_truncates_oversized_alarm_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            payload = {
                "version": 1,
                "last_checked_at": "",
                "alarms": [
                    {"id": f"alarm-{index}", "hour": 9, "minute": index % 60, "days": ["mon"], "name": "name"}
                    for index in range(MAX_ALARM_COUNT + 5)
                ],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_alarm_store(path)

        self.assertEqual(len(loaded["alarms"]), MAX_ALARM_COUNT)

    def test_save_alarm_store_rejects_oversized_last_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            with self.assertRaisesRegex(ValueError, "last_checked_at is too large"):
                save_alarm_store(
                    {
                        "alarms": [],
                        "last_checked_at": "y" * (MAX_ALARM_TRIGGER_CHARS + 10),
                    },
                    path,
                )

    @mock.patch("speed_of_cinnamon.alarms.os.replace", side_effect=OSError("disk full"))
    def test_save_alarm_store_raises_runtime_error_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            with self.assertRaisesRegex(RuntimeError, "failed to persist alarm store"):
                save_alarm_store({}, path)
        mocked_replace.assert_called_once()

    def test_save_alarm_store_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            save_alarm_store({}, path)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_save_alarm_store_rejects_null_byte_last_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            with self.assertRaisesRegex(ValueError, "alarm store last_checked_at contains invalid null byte"):
                save_alarm_store(
                    {
                        "alarms": [],
                        "last_checked_at": "2026-06-01T09:00\x00",
                    },
                    path,
                )

    def test_save_alarm_store_skips_invalid_alarm_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            save_alarm_store(
                {
                    "alarms": [
                        {"id": "good", "hour": 9, "minute": 0, "days": ["mon"], "name": "Good"},
                        {"id": "bad", "hour": "not-a-number", "minute": 0, "days": ["mon"], "name": "Bad"},
                    ],
                    "last_checked_at": "2026-06-01T09:00",
                },
                path,
            )
            payload = load_alarm_store(path)

        self.assertEqual(len(payload["alarms"]), 1)
        self.assertEqual(payload["alarms"][0]["id"], "good")

    def test_save_alarm_store_throws_when_payload_too_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            with mock.patch.object(alarm_module, "MAX_ALARM_STORE_BYTES", 200):
                with self.assertRaisesRegex(RuntimeError, "alarm store is too large"):
                    save_alarm_store(
                        {
                            "alarms": [
                                {"id": "x" * 100, "hour": 9, "minute": 0, "days": ["mon"], "name": "y" * 100}
                                for _ in range(20)
                            ],
                            "last_checked_at": "2026-06-01T09:00",
                        },
                        path,
                    )
