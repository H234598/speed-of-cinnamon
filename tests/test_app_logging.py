from __future__ import annotations

import gzip
import io
import json
import logging
import os
import stat as stat_module
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
        for message in (
            "token abc123",
            "password hunter2",
            "api key abc123",
            "token is abc123",
            "password was hunter2",
            "api key was abc123",
            "access token is abc123",
            "refresh_token abc123",
            "client_secret abc123",
            "private_key abc123",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    app_logging.sanitize_error_message(message, max_chars=120),
                    "[redacted error details]",
                )

    def test_sanitize_error_message_preserves_short_failed_error_details(self) -> None:
        self.assertEqual(
            app_logging.sanitize_error_message("ls failed: missing permission", max_chars=120),
            "ls failed: missing permission",
        )

    def test_sanitize_error_message_redacts_failed_error_output_excerpt_with_newline(self) -> None:
        self.assertEqual(
            app_logging.sanitize_error_message("ffmpeg failed: Traceback (most recent call last):\n  File \"stdin\", line 1"),
            "[redacted error details]",
        )

    def test_sanitize_error_message_redacts_long_failed_error_output_excerpt(self) -> None:
        self.assertEqual(
            app_logging.sanitize_error_message(
                "cmd failed: " + ("A" * 200),
                max_chars=120,
            ),
            "[redacted error details]",
        )

    def test_sanitize_error_message_redacts_short_failed_secret_details(self) -> None:
        self.assertEqual(
            app_logging.sanitize_error_message("cmd failed: secret customer phrase", max_chars=120),
            "[redacted error details]",
        )

    def test_sanitize_value_redacts_hyphenated_and_passphrase_keys(self) -> None:
        self.assertEqual(app_logging.sanitize_value("api-key", "sk-short"), "[redacted]")
        self.assertEqual(app_logging.sanitize_value("passphrase", "correct horse battery staple"), "[redacted]")
        self.assertEqual(app_logging.sanitize_value("openai-compatible-api-key", "sk-short"), "[redacted]")

    def test_sanitize_value_does_not_redact_innocent_key_substrings(self) -> None:
        for key in ("monkey", "turkey", "keyboard", "context_id"):
            with self.subTest(key=key):
                self.assertEqual(app_logging.sanitize_value(key, "visible"), "visible")

    def test_sanitize_text_redacts_short_token_like_values(self) -> None:
        self.assertEqual(app_logging.sanitize_text("session sk-abc", max_chars=120), "session [redacted]")
        self.assertEqual(app_logging.sanitize_text("session sess-abc", max_chars=120), "session [redacted]")
        self.assertEqual(app_logging.sanitize_text("sk-standalone", max_chars=120), "[redacted]")
        self.assertEqual(app_logging.sanitize_text("sess-standalone", max_chars=120), "[redacted]")

    def test_sanitize_text_redacts_common_structured_credentials(self) -> None:
        for message in (
            "access_token=abc123",
            "refresh_token=abc123",
            "client_secret=abc123",
            "private_key=abc123",
        ):
            with self.subTest(message=message):
                sanitized = app_logging.sanitize_text(message, max_chars=120)
                self.assertNotIn("abc123", sanitized)
                self.assertIn("[redacted]", sanitized)

    def test_sanitize_error_message_redacts_opaque_failed_details(self) -> None:
        self.assertEqual(
            app_logging.sanitize_error_message("cmd failed: abc123", max_chars=120),
            "[redacted error details]",
        )
        self.assertEqual(
            app_logging.sanitize_error_message("cmd failed: file not found", max_chars=120),
            "cmd failed: file not found",
        )

    def test_sanitize_error_message_preserves_api_key_policy_errors(self) -> None:
        self.assertEqual(
            app_logging.sanitize_error_message("openai-compatible API key is too large", max_chars=120),
            "openai-compatible API key is too large",
        )

    def test_sanitize_text_redacts_url_credentials_with_colon_in_password(self) -> None:
        sanitized = app_logging.sanitize_text("https://user:p:a:s@example.test/path", max_chars=120)
        self.assertEqual(sanitized, "https://[redacted]@example.test/path")
        self.assertNotIn("user", sanitized)
        self.assertNotIn("p:a:s", sanitized)

    def test_sanitize_text_redacts_url_userinfo_without_password(self) -> None:
        sanitized = app_logging.sanitize_text("https://secret-token@example.test/path", max_chars=120)
        self.assertEqual(sanitized, "https://[redacted]@example.test/path")
        self.assertNotIn("secret-token", sanitized)

    def test_sanitize_text_redacts_local_absolute_paths(self) -> None:
        sanitized = app_logging.sanitize_text("failed to read /tmp/private/transcript.txt", max_chars=120)
        self.assertEqual(sanitized, "failed to read [redacted path]")
        self.assertNotIn("/tmp/private", sanitized)

    def test_sanitize_error_message_redacts_local_absolute_paths(self) -> None:
        sanitized = app_logging.sanitize_error_message("settings export not found: /var/tmp/private/settings.json", max_chars=120)
        self.assertEqual(sanitized, "settings export not found: [redacted path]")
        self.assertNotIn("/var/tmp/private", sanitized)

    def test_sanitize_hint_detects_url_userinfo_without_password(self) -> None:
        self.assertIsNotNone(app_logging._SANITIZE_HINT_RE.search("https://secret-token@example.test/path"))

    def test_log_path_insecure_rejects_non_private_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            os.chmod(log_dir, 0o777)
            handler = app_logging.SizeCappedJsonFileHandler(log_dir / "speed-of-cinnamon.log", log_dir)

            self.assertTrue(handler._is_log_path_insecure())

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

    def test_file_handler_transient_emit_failure_retries_after_temporary_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = app_logging.SizeCappedJsonFileHandler(
                log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log",
                log_dir,
            )
            handler._retry_base_delay = 0.0
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)
            original_open = handler._open
            open_calls = 0

            def transient_open() -> None:
                nonlocal open_calls
                open_calls += 1
                if open_calls == 1:
                    raise OSError("read only")
                original_open()

            with mock.patch.object(handler, "_open") as mocked_open:
                mocked_open.side_effect = transient_open
                handler.emit(record)
                handler.emit(record)
                handler.close()

            log_file = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            self.assertEqual(mocked_open.call_count, 2)
            self.assertTrue(log_file.exists())
            payload = json.loads(log_file.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["event"], "event")

    def test_file_handler_disables_permanently_on_insecure_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            target = Path(tmp) / "outside.log"
            active.symlink_to(target)
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)

            with mock.patch.object(handler, "_open", side_effect=RuntimeError("must not be a symlink")) as mocked_open:
                handler.emit(record)
                handler.emit(record)

            handler.close()

            self.assertTrue(handler._disabled)
            self.assertGreaterEqual(mocked_open.call_count, 1)

    def test_file_handler_disables_permanently_on_log_path_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)

            with (
                mock.patch.object(handler, "_open", side_effect=OSError("read only")) as mocked_open,
                mock.patch.object(type(active), "lstat", side_effect=PermissionError("denied")),
            ):
                handler.emit(record)
                handler.emit(record)

            handler.close()

            self.assertTrue(handler._disabled)
            self.assertEqual(mocked_open.call_count, 1)

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

    def test_file_handler_does_not_write_when_log_permissions_cannot_be_restricted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_text("old\n", encoding="utf-8")
            active.chmod(0o666)
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)

            with mock.patch("speed_of_cinnamon.app_logging.os.fchmod", side_effect=OSError("chmod denied")):
                handler.emit(record)
                handler.close()

            self.assertEqual(active.read_text(encoding="utf-8"), "old\n")

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
            old_daily.chmod(0o600)

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
            old_daily.chmod(0o600)
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
            old_daily.chmod(0o600)
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
            may_daily.chmod(0o600)
            with gzip.open(may_gz, "wt", encoding="utf-8") as handle:
                handle.write("may-31\n")
            may_gz.chmod(0o600)

            app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            self.assertTrue(archive.exists())
            self.assertFalse(may_daily.exists())
            self.assertFalse(may_gz.exists())
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("may-30", content)
            self.assertIn("may-31", content)

    def test_maintain_logs_monthly_merge_deletes_sources_with_dir_fd_and_fsync(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            may_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            may_daily.write_text("may-30\n", encoding="utf-8")
            may_daily.chmod(0o600)

            with (
                mock.patch("speed_of_cinnamon.app_logging.os.unlink", wraps=os.unlink) as mocked_unlink,
                mock.patch("speed_of_cinnamon.app_logging.os.fsync", side_effect=record_fsync),
            ):
                app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            self.assertTrue(archive.exists())
            self.assertFalse(may_daily.exists())

        unlink_calls = [
            (args, kwargs)
            for args, kwargs in mocked_unlink.call_args_list
            if args and args[0] == may_daily.name
        ]
        self.assertEqual(len(unlink_calls), 1)
        self.assertIsInstance(unlink_calls[0][1].get("dir_fd"), int)
        self.assertTrue(any(stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_maintain_logs_monthly_merge_rejects_source_swap_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            may_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            may_daily.write_text("may-30\n", encoding="utf-8")
            may_daily.chmod(0o600)
            real_replace = os.replace

            def replace_and_swap(src: object, dst: object, *args: object, **kwargs: object) -> None:
                real_replace(src, dst, *args, **kwargs)
                may_daily.unlink()
                may_daily.write_text("attacker\n", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.app_logging.os.replace", side_effect=replace_and_swap):
                with self.assertRaisesRegex(RuntimeError, "monthly log source changed before deletion"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertTrue((log_dir / "speed-of-cinnamon-2026-05.log.gz").exists())
            self.assertTrue(may_daily.exists())
            self.assertEqual(may_daily.read_text(encoding="utf-8"), "attacker\n")

    def test_maintain_logs_merge_retain_existing_archive_on_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            old_daily.write_text("may-30\n", encoding="utf-8")
            old_daily.chmod(0o600)
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")
            old_archive.chmod(0o600)
            fsync_modes: list[int] = []
            real_fsync = os.fsync

            def record_fsync(fd: int) -> None:
                fsync_modes.append(os.fstat(fd).st_mode)
                real_fsync(fd)

            with (
                mock.patch("speed_of_cinnamon.app_logging.os.replace", side_effect=PermissionError("replace failed")),
                mock.patch("speed_of_cinnamon.app_logging.os.fsync", side_effect=record_fsync),
            ):
                with self.assertRaises(PermissionError, msg="replace failure"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertTrue(old_archive.exists())
            with gzip.open(old_archive, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "legacy\n")
            self.assertTrue(old_daily.exists())
            self.assertTrue(any(stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_maintain_logs_ignores_preexisting_monthly_tmp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            target = Path(tmp) / "pwned-monthly"
            may_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            may_daily.write_text("may-30\n", encoding="utf-8")
            may_daily.chmod(0o600)
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
            source.chmod(0o600)

            with mock.patch("speed_of_cinnamon.app_logging.os.replace", side_effect=PermissionError("replace failed")):
                with self.assertRaises(PermissionError, msg="replace failure"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "content\n")
            self.assertFalse(target.exists())
            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_gzip_file_reports_temp_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("content\n", encoding="utf-8")
            source.chmod(0o600)

            with (
                mock.patch("speed_of_cinnamon.app_logging.os.replace", side_effect=PermissionError("replace failed")),
                mock.patch("speed_of_cinnamon.app_logging.os.unlink", side_effect=PermissionError("cleanup denied")),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to remove log temporary file"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertFalse(target.exists())
            self.assertTrue(list(log_dir.glob("*.tmp")))

    def test_gzip_file_rejects_oversized_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_bytes(b"x" * 32)
            source.chmod(0o600)

            with mock.patch("speed_of_cinnamon.app_logging.MAX_TOTAL_LOG_BYTES", 16):
                with self.assertRaisesRegex(RuntimeError, "log source content is too large"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertFalse(target.exists())
            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_gzip_file_rejects_source_swap_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("content\n", encoding="utf-8")
            source.chmod(0o600)
            real_replace = os.replace

            def replace_and_swap(src: object, dst: object, *args: object, **kwargs: object) -> None:
                real_replace(src, dst, *args, **kwargs)
                source.unlink()
                source.write_text("attacker\n", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.app_logging.os.replace", side_effect=replace_and_swap):
                with self.assertRaisesRegex(RuntimeError, "log source file changed before deletion"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(target.exists())
            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "attacker\n")
            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_gzip_file_fsyncs_temp_file_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("content\n", encoding="utf-8")
            source.chmod(0o600)

            with (
                mock.patch("speed_of_cinnamon.app_logging.os.fsync", wraps=os.fsync) as mocked_fsync,
                mock.patch("speed_of_cinnamon.app_logging.os.unlink", wraps=os.unlink) as mocked_unlink,
            ):
                app_logging._gzip_file(source, target)

            self.assertTrue(target.exists())
            self.assertFalse(source.exists())
            self.assertGreaterEqual(mocked_fsync.call_count, 3)
            unlink_calls = [
                (args, kwargs)
                for args, kwargs in mocked_unlink.call_args_list
                if args and args[0] == source.name
            ]
            self.assertEqual(len(unlink_calls), 1)
            self.assertIsInstance(unlink_calls[0][1].get("dir_fd"), int)

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

    def test_copy_log_content_rejects_oversized_decompressed_gzip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.log.gz"
            target = Path(tmp) / "target.log.gz"
            with gzip.open(path, "wb") as handle:
                handle.write(b"x" * 32)
            path.chmod(0o600)

            with (
                mock.patch("speed_of_cinnamon.app_logging.MAX_TOTAL_LOG_BYTES", 16),
                gzip.open(target, "wb") as output,
            ):
                with self.assertRaisesRegex(RuntimeError, "log source content is too large"):
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
                with mock.patch("speed_of_cinnamon.app_logging.os.unlink", wraps=os.unlink) as mocked_unlink:
                    app_logging.maintain_logs(log_dir, today=date.today())

            self.assertFalse(oldest.exists())
            self.assertTrue(active.exists())
            unlink_calls = [
                (args, kwargs)
                for args, kwargs in mocked_unlink.call_args_list
                if args and args[0] == oldest.name
            ]
            self.assertEqual(len(unlink_calls), 1)
            self.assertIsInstance(unlink_calls[0][1].get("dir_fd"), int)

    def test_enforce_total_size_limit_rejects_path_swap_before_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            oldest = log_dir / "speed-of-cinnamon-2026-06-01.log.gz"
            newest = log_dir / "speed-of-cinnamon-2026-06-02.log.gz"
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            oldest.write_bytes(b"o" * 40)
            newest.write_bytes(b"n" * 40)
            active.write_bytes(b"a" * 10)
            original_assert = app_logging._assert_regular_unlinked_file
            swapped = False

            def assert_with_swap(path: Path, *, field_name: str) -> os.stat_result:
                nonlocal swapped
                if path == oldest and field_name == "log file" and not swapped:
                    oldest.unlink()
                    oldest.write_bytes(b"attacker")
                    swapped = True
                return original_assert(path, field_name=field_name)

            with (
                mock.patch("speed_of_cinnamon.app_logging.MAX_TOTAL_LOG_BYTES", 55),
                mock.patch(
                    "speed_of_cinnamon.app_logging._assert_regular_unlinked_file",
                    side_effect=assert_with_swap,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "log file changed before deletion"):
                    app_logging._enforce_total_size_limit(log_dir)

            self.assertTrue(oldest.exists())
            self.assertEqual(oldest.read_bytes(), b"attacker")
            self.assertTrue(active.exists())


if __name__ == "__main__":
    unittest.main()
