from __future__ import annotations

import gzip
import io
import json
import logging
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import app_logging


class AppLoggingTest(unittest.TestCase):
    def test_sanitize_error_message_redacts_tokens_and_commands(self) -> None:
        self.assertEqual(
            app_logging.sanitize_error_message("Bearer sk-secret token=abc123", max_chars=120),
            "Bearer [redacted] token=[redacted]",
        )
        self.assertEqual(
            app_logging.sanitize_error_message("backend stderr: secret transcript words", max_chars=120),
            "[redacted error details]",
        )

    def test_sanitize_error_message_redacts_secret_even_after_text_mutation(self) -> None:
        with mock.patch("speed_of_cinnamon.app_logging.HOME_DIR", "/tmp/fake-home"):
            self.assertEqual(
                app_logging.sanitize_error_message("/tmp/fake-home/.config/secret/settings", max_chars=120),
                "[redacted error details]",
            )
        self.assertEqual(
            app_logging.sanitize_error_message("line1\r\nline2 secret payload", max_chars=120),
            "[redacted error details]",
        )
        self.assertEqual(
            app_logging.sanitize_error_message("x" * 400 + " secret tail", max_chars=40),
            "[redacted error details]",
        )

    def test_sanitize_text_escapes_c1_control_characters(self) -> None:
        self.assertEqual(app_logging.sanitize_text("alpha\x85beta", max_chars=120), "alpha\\x85beta")

    def test_validate_log_level_rejects_control_characters_before_strip(self) -> None:
        for value in ("\x85debug", "debug\\x85"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "log level contains invalid control character"):
                    app_logging.validate_log_level(value)

    def test_sanitize_key_rejects_control_characters_before_strip(self) -> None:
        self.assertEqual(app_logging.sanitize_key("\x85api_key"), "")
        self.assertEqual(app_logging.sanitize_key("api_key\\x85"), "")

    def test_sanitize_error_message_redacts_bare_credentials(self) -> None:
        for message in ("token abc123", "password hunter2", "api key abc123"):
            with self.subTest(message=message):
                self.assertEqual(
                    app_logging.sanitize_error_message(message, max_chars=120),
                    "[redacted error details]",
                )

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
                details="token bare123",
            )

            log_files = list(log_dir.glob("speed-of-cinnamon-*.log"))
            self.assertEqual(len(log_files), 1)
            payload = json.loads(log_files[0].read_text(encoding="utf-8").strip())
            self.assertEqual(payload["api_key"], "[redacted]")
            self.assertEqual(payload["command"], "[redacted]")
            self.assertEqual(payload["command_template"], "[redacted]")
            self.assertEqual(payload["transcript"], "[redacted]")
            self.assertNotIn("doctor", json.dumps(payload))
            self.assertNotIn("sk-secret", json.dumps(payload))
            self.assertNotIn("abc123", json.dumps(payload))
            self.assertNotIn("bare123", json.dumps(payload))

    def test_info_is_not_logged_when_default_error_level_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            app_logging.configure_logging(app_logging.DEFAULT_LOG_LEVEL, base_dir=log_dir)
            app_logging.log_event("info", "command_start", command="doctor")

            log_files = list(log_dir.glob("speed-of-cinnamon-*.log"))
            self.assertEqual(log_files, [])

    def test_file_handler_does_not_maintain_logs_on_every_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = app_logging.SizeCappedJsonFileHandler(
                log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log",
                log_dir,
            )
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)
            with mock.patch("speed_of_cinnamon.app_logging.maintain_logs") as mocked_maintain:
                handler.emit(record)
                handler.emit(record)
                handler.close()

        mocked_maintain.assert_not_called()

    def test_file_handler_maintains_logs_immediately_after_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_bytes(b"x" * 90)
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "rotated", (), None)
            with (
                mock.patch("speed_of_cinnamon.app_logging.MAX_DAILY_LOG_BYTES", 100),
                mock.patch("speed_of_cinnamon.app_logging.maintain_logs") as mocked_maintain,
            ):
                handler.emit(record)
                handler.close()

        mocked_maintain.assert_called_once_with(log_dir)

    def test_file_handler_does_not_scan_total_limit_on_every_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            oldest = log_dir / "speed-of-cinnamon-2026-06-01.log.gz"
            newest = log_dir / "speed-of-cinnamon-2026-06-02.log.gz"
            oldest.write_bytes(b"o" * 120)
            newest.write_bytes(b"n" * 120)
            os.utime(oldest, (100, 100))
            os.utime(newest, (200, 200))
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "burst", (), None)
            with (
                mock.patch("speed_of_cinnamon.app_logging.MAX_TOTAL_LOG_BYTES", 300),
                mock.patch("speed_of_cinnamon.app_logging.maintain_logs") as mocked_maintain,
                mock.patch("speed_of_cinnamon.app_logging._enforce_total_size_limit") as mocked_total_enforce,
            ):
                handler.emit(record)
            handler.close()

            mocked_maintain.assert_not_called()
            mocked_total_enforce.assert_not_called()
            self.assertTrue(oldest.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(active.exists())

    def test_file_handler_suppresses_log_io_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = app_logging.SizeCappedJsonFileHandler(
                log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log",
                log_dir,
            )
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)
            stderr = io.StringIO()
            with (
                mock.patch.object(handler, "_open", side_effect=OSError("read only")),
                mock.patch("logging.raiseExceptions", True),
                redirect_stderr(stderr),
            ):
                handler.emit(record)
                handler.emit(record)
            handler.close()

        self.assertEqual(stderr.getvalue(), "")

    def test_file_handler_rejects_symlink_created_before_first_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            target = Path(tmp) / "outside.log"
            active.symlink_to(target)
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)

            handler.emit(record)
            handler.close()

            self.assertTrue(active.is_symlink())
            self.assertFalse(target.exists())

    def test_file_handler_rejects_hardlinked_active_log_before_first_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            real = log_dir / "real.log"
            real.write_text("old\n", encoding="utf-8")
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            try:
                os.link(real, active)
            except OSError:
                return
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)

            handler.emit(record)
            handler.close()

            self.assertEqual(real.read_text(encoding="utf-8"), "old\n")

    def test_file_handler_enforces_total_limit_during_maintenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            oldest = log_dir / "speed-of-cinnamon-2026-06-01.log.gz"
            newest = log_dir / "speed-of-cinnamon-2026-06-02.log.gz"
            oldest.write_bytes(b"o" * 120)
            newest.write_bytes(b"n" * 120)
            os.utime(oldest, (100, 100))
            os.utime(newest, (200, 200))
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler._next_maintenance_at = 0.0
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "burst", (), None)
            with mock.patch("speed_of_cinnamon.app_logging.MAX_TOTAL_LOG_BYTES", 300):
                handler.emit(record)
            handler.close()

            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(active.exists())

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


    def test_rotate_active_if_needed_is_noop_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            missing = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"

            app_logging._rotate_active_if_needed(missing)

            self.assertFalse(missing.exists())

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

    def test_configure_logging_rejects_symlinked_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_dir = base / "real-logs"
            real_dir.mkdir()
            symlink_dir = base / "logs"
            symlink_dir.symlink_to(real_dir, target_is_directory=True)

            with self.assertRaisesRegex((OSError, RuntimeError), "log directory"):
                app_logging.configure_logging("error", base_dir=symlink_dir)

            self.assertEqual(list(real_dir.iterdir()), [])
            self.assertTrue(symlink_dir.is_symlink())

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

    def test_maintain_logs_rejects_symlinked_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_dir = base / "real-logs"
            real_dir.mkdir()
            old_daily = real_dir / "speed-of-cinnamon-2026-06-01.log"
            old_daily.write_text("old\n", encoding="utf-8")
            symlink_dir = base / "logs"
            symlink_dir.symlink_to(real_dir, target_is_directory=True)

            with self.assertRaisesRegex((OSError, RuntimeError), "log directory"):
                app_logging.maintain_logs(symlink_dir, today=date(2026, 6, 5))

            self.assertTrue(old_daily.exists())
            self.assertFalse((real_dir / "speed-of-cinnamon-2026-06-01.log.gz").exists())
            self.assertTrue(symlink_dir.is_symlink())

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

    def test_enforce_file_size_limit_rejects_symlink_without_following(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            real = log_dir / "real.log"
            real.write_text("secret\n", encoding="utf-8")
            active = log_dir / "speed-of-cinnamon-2026-06-04.log"
            active.symlink_to(real)

            with self.assertRaisesRegex(RuntimeError, "log file must not be a symlink"):
                app_logging._enforce_file_size_limit(log_dir)

            self.assertTrue(real.exists())
            self.assertTrue(active.is_symlink())

    def test_enforce_total_size_limit_rejects_symlink_without_following(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            real = log_dir / "real.log"
            real.write_text("secret\n", encoding="utf-8")
            archive = log_dir / "speed-of-cinnamon-2026-06-01.log.gz"
            archive.symlink_to(real)

            with self.assertRaisesRegex(RuntimeError, "log file must not be a symlink"):
                app_logging._enforce_total_size_limit(log_dir)

            self.assertTrue(real.exists())
            self.assertTrue(archive.is_symlink())

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

    def test_maintain_logs_merge_retain_existing_archive_on_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            old_daily.write_text("may-30\n", encoding="utf-8")
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")

            with mock.patch("speed_of_cinnamon.app_logging.os.replace", side_effect=PermissionError("replace failed")):
                with self.assertRaises(PermissionError, msg="replace failure"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertTrue(old_archive.exists())
            with gzip.open(old_archive, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "legacy\n")
            self.assertTrue(old_daily.exists())

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

    def test_gzip_file_keeps_source_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("content\n", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.app_logging.os.replace", side_effect=PermissionError("replace failed")):
                with self.assertRaises(PermissionError, msg="replace failure"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "content\n")
            self.assertFalse(target.exists())
            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_gzip_file_rejects_fifo_source_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("fifo creation unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            try:
                os.mkfifo(source)
            except OSError as exc:
                self.skipTest(f"fifo creation unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "regular file"):
                app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertFalse(target.exists())
            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_copy_log_content_rejects_fifo_source_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("fifo creation unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.log"
            target = Path(tmp) / "target.log.gz"
            try:
                os.mkfifo(path)
            except OSError as exc:
                self.skipTest(f"fifo creation unavailable: {exc}")

            with gzip.open(target, "wb") as output:
                with self.assertRaisesRegex(RuntimeError, "regular file"):
                    app_logging._copy_log_content(path, output)

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
