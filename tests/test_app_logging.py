from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import app_logging


class AppLoggingTest(unittest.TestCase):
    def test_log_event_redacts_sensitive_fields_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            app_logging.configure_logging("error", base_dir=log_dir)
            app_logging.log_event(
                "error",
                "api failed",
                api_key="sk-secret",
                command="doctor",
                command_template="printf secret",
                transcript="do not log me",
                error_message="Bearer sk-secret token=abc123",
            )

            log_files = list(log_dir.glob("speed-of-cinnamon-*.log"))
            self.assertEqual(len(log_files), 1)
            payload = json.loads(log_files[0].read_text(encoding="utf-8").strip())
            self.assertEqual(payload["api_key"], "[redacted]")
            self.assertEqual(payload["command"], "doctor")
            self.assertEqual(payload["command_template"], "[redacted]")
            self.assertEqual(payload["transcript"], "[redacted]")
            self.assertNotIn("sk-secret", json.dumps(payload))
            self.assertNotIn("abc123", json.dumps(payload))

    def test_info_is_not_logged_when_default_error_level_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            app_logging.configure_logging(app_logging.DEFAULT_LOG_LEVEL, base_dir=log_dir)
            app_logging.log_event("info", "command_start", command="doctor")

            log_files = list(log_dir.glob("speed-of-cinnamon-*.log"))
            self.assertEqual(log_files, [])

    def test_active_daily_log_rotates_at_one_file_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_bytes(b"x" * 240)

            with mock.patch("speed_of_cinnamon.app_logging.MAX_DAILY_LOG_BYTES", 256):
                app_logging.configure_logging("error", base_dir=log_dir)
                app_logging.log_event("error", "rotated")

            self.assertTrue(active.exists())
            self.assertTrue(active.with_name(f"{active.stem}.1{active.suffix}").exists())
            self.assertIn("rotated", active.read_text(encoding="utf-8"))
            for log_file in log_dir.glob("speed-of-cinnamon-*.log"):
                self.assertLessEqual(log_file.stat().st_size, 256)

    def test_configure_logging_rejects_symlinked_active_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            real = log_dir / "real.log"
            real.write_text("hello\n", encoding="utf-8")
            active.symlink_to(real)

            with self.assertRaisesRegex(RuntimeError, "log file must not be a symlink"):
                app_logging.configure_logging("error", base_dir=log_dir)

            self.assertTrue(active.is_symlink())
            self.assertTrue(real.exists())

    def test_maintain_logs_compresses_daily_logs_older_than_three_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_daily = log_dir / "speed-of-cinnamon-2026-06-01.log"
            old_daily.write_text("old\n", encoding="utf-8")

            app_logging.maintain_logs(log_dir, today=date(2026, 6, 5))

            self.assertFalse(old_daily.exists())
            with gzip.open(str(old_daily) + ".gz", "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old\n")

    def test_maintain_logs_ignores_preexisting_daily_tmp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            target = Path(tmp) / "pwned-daily"
            old_daily = log_dir / "speed-of-cinnamon-2026-06-01.log"
            old_daily.write_text("old\n", encoding="utf-8")
            tmp_target = old_daily.with_suffix(old_daily.suffix + ".tmp")
            tmp_target.symlink_to(target)

            app_logging.maintain_logs(log_dir, today=date(2026, 6, 5))

            self.assertFalse(target.exists())
            self.assertTrue(tmp_target.is_symlink())
            self.assertFalse(old_daily.exists())
            self.assertTrue((log_dir / "speed-of-cinnamon-2026-06-01.log.gz").exists())

    def test_maintain_logs_rejects_symlinked_daily_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            real = log_dir / "real.log"
            real.write_text("secret\n", encoding="utf-8")
            old_daily = log_dir / "speed-of-cinnamon-2026-06-01.log"
            old_daily.symlink_to(real)

            with self.assertRaisesRegex(RuntimeError, "daily log file must not be a symlink"):
                app_logging.maintain_logs(log_dir, today=date(2026, 6, 5))

            self.assertTrue(real.exists())
            self.assertTrue(old_daily.is_symlink())

    def test_maintain_logs_merges_previous_month_to_single_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            may_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            may_gz = log_dir / "speed-of-cinnamon-2026-05-31.log.gz"
            may_daily.write_text("may-30\n", encoding="utf-8")
            with gzip.open(may_gz, "wt", encoding="utf-8") as handle:
                handle.write("may-31\n")

            app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            self.assertTrue(archive.exists())
            self.assertFalse(may_daily.exists())
            self.assertFalse(may_gz.exists())
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("may-30", content)
            self.assertIn("may-31", content)

    def test_maintain_logs_ignores_preexisting_monthly_tmp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            target = Path(tmp) / "pwned-monthly"
            may_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            may_daily.write_text("may-30\n", encoding="utf-8")
            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            tmp_archive = archive.with_suffix(".log.gz.tmp")
            tmp_archive.symlink_to(target)

            app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertFalse(target.exists())
            self.assertTrue(tmp_archive.is_symlink())
            self.assertTrue(archive.exists())
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                self.assertIn("may-30", handle.read())

    def test_maintain_logs_deletes_oldest_files_over_total_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            oldest = log_dir / "speed-of-cinnamon-2026-06-01.log.gz"
            newest = log_dir / "speed-of-cinnamon-2026-06-02.log.gz"
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            oldest.write_bytes(b"o" * 40)
            newest.write_bytes(b"n" * 40)
            active.write_bytes(b"a" * 10)

            with mock.patch("speed_of_cinnamon.app_logging.MAX_TOTAL_LOG_BYTES", 55):
                app_logging.maintain_logs(log_dir, today=date.today())

            self.assertFalse(oldest.exists())
            self.assertTrue(active.exists())


if __name__ == "__main__":
    unittest.main()
