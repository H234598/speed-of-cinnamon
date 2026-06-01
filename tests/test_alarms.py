from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import cli
from speed_of_cinnamon.alarms import (
    add_alarm,
    load_alarm_store,
    save_alarm_store,
    check_due_alarms,
    format_alarm_overview,
    list_alarm_payload,
    parse_repeat_days,
    MAX_ALARM_STORE_BYTES,
)


class AlarmTest(unittest.TestCase):
    def test_load_alarm_store_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            load_alarm_store(Path("alarms\x00.json"))

    def test_save_alarm_store_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            save_alarm_store({}, Path("alarms\x00.json"))

    def test_load_alarm_store_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text("x" * (MAX_ALARM_STORE_BYTES + 1), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "alarm store is too large"):
                load_alarm_store(path)

    def test_repeat_day_parser_supports_common_groups(self) -> None:
        self.assertEqual(parse_repeat_days("daily"), ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        self.assertEqual(parse_repeat_days("weekdays"), ["mon", "tue", "wed", "thu", "fri"])
        self.assertEqual(parse_repeat_days("weekends"), ["sat", "sun"])
        self.assertEqual(parse_repeat_days("mon,wed,fri"), ["mon", "wed", "fri"])

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

    def test_load_alarm_store_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text("{invalid}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "alarm store could not be parsed"):
                load_alarm_store(path)

    def test_load_alarm_store_rejects_non_object_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "alarm store must be a JSON object"):
                load_alarm_store(path)

    @mock.patch("speed_of_cinnamon.alarms.os.replace", side_effect=OSError("disk full"))
    def test_save_alarm_store_raises_runtime_error_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alarms.json"
            with self.assertRaisesRegex(RuntimeError, "failed to persist alarm store"):
                save_alarm_store({}, path)
        mocked_replace.assert_called_once()
