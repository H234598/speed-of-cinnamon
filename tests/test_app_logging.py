from __future__ import annotations

import gzip
import io
import json
import logging
import os
import stat as stat_module
import tempfile
import time
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

    def test_sanitize_text_redacts_multiword_credential_values(self) -> None:
        for value in (
            "password: correct horse battery staple",
            "passphrase=correct horse battery staple",
            "token correct horse battery staple",
        ):
            with self.subTest(value=value):
                sanitized = app_logging.sanitize_text(value, max_chars=200)
                self.assertNotIn("correct", sanitized)
                self.assertNotIn("battery", sanitized)
                self.assertNotIn("staple", sanitized)

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

    def test_json_logging_rejects_nonfinite_float_values(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertIsNone(app_logging.sanitize_value("elapsed", value))

                record = logging.LogRecord("test", logging.INFO, __file__, 1, "event", (), None)
                record.fields = {"elapsed": value}
                rendered = app_logging.JsonLogFormatter().format(record)
                self.assertNotIn("NaN", rendered)
                self.assertNotIn("Infinity", rendered)
                self.assertIsNone(json.loads(rendered)["elapsed"])

    def test_sanitize_text_redacts_short_token_like_values(self) -> None:
        self.assertEqual(app_logging.sanitize_text("session sk-abc", max_chars=120), "session [redacted]")
        self.assertEqual(app_logging.sanitize_text("session sess-abc", max_chars=120), "session [redacted]")
        self.assertEqual(app_logging.sanitize_text("sk-standalone", max_chars=120), "[redacted]")
        self.assertEqual(app_logging.sanitize_text("sess-standalone", max_chars=120), "[redacted]")

    def test_sanitize_text_redacts_token_like_values_with_ignored_unicode(self) -> None:
        for value in (
            "s\u200bk-secret-token",
            "sk-\u200bsecret-token",
            "s\u0308k-secret-token",
            "t\u200boken abc123",
            "pass\u200bword: hunter2",
            "api\u200b key abc123",
        ):
            with self.subTest(value=repr(value)):
                sanitized = app_logging.sanitize_text(value, max_chars=120)

                self.assertNotIn("abc123", sanitized)
                self.assertNotIn("hunter2", sanitized)
                self.assertNotIn("secret-token", sanitized)

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

    def test_sanitize_text_redacts_obfuscated_url_credentials_and_paths(self) -> None:
        for value, secret in (
            ("http\u200b://secret-token@example.test/path", "secret-token"),
            ("https:\u200b//secret-token@example.test/path", "secret-token"),
            ("/t\u200bmp/private/transcript.txt", "/t\u200bmp"),
            ("/ho\u200bme/teladi/.config/secret.txt", "/ho\u200bme"),
        ):
            with self.subTest(value=repr(value)):
                sanitized = app_logging.sanitize_text(value, max_chars=120)
                self.assertNotIn(secret, sanitized)

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

    def test_sanitize_text_bounds_long_plaintext_scan(self) -> None:
        value = "x" * 32_000
        started = time.perf_counter()

        sanitized = app_logging.sanitize_text(value)

        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertTrue(sanitized.endswith("...[truncated]"))

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

    def test_file_handler_suppresses_close_failure_after_log_io_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = app_logging.SizeCappedJsonFileHandler(
                log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log",
                log_dir,
            )
            handler.setFormatter(app_logging.JsonLogFormatter())
            handler.stream = io.StringIO()
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)

            with (
                mock.patch.object(handler, "_open", side_effect=OSError("read only")),
                mock.patch.object(handler.stream, "close", side_effect=OSError("close failed")),
            ):
                handler.emit(record)

            self.assertIsNone(handler.stream)
            handler.close()

    def test_file_handler_rejects_active_path_swap_during_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_text("original\n", encoding="utf-8")
            active.chmod(0o600)
            handler = app_logging.SizeCappedJsonFileHandler(active, log_dir)
            real_open = app_logging.open_file_without_following_symlinks

            def open_and_swap(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                active.rename(log_dir / "active-original.log")
                active.write_text("replacement\n", encoding="utf-8")
                return fd

            with mock.patch.object(app_logging, "open_file_without_following_symlinks", side_effect=open_and_swap):
                with self.assertRaisesRegex(RuntimeError, "log file changed while opening"):
                    handler._open()

            self.assertIsNone(handler.stream)
            self.assertEqual(active.read_text(encoding="utf-8"), "replacement\n")
            handler.close()

    def test_file_handler_preserves_validation_error_when_fd_close_is_interrupted(self) -> None:
        handler = app_logging.SizeCappedJsonFileHandler(Path("/probe.log"), Path("/probe"))

        def close(fd: int) -> None:
            if fd == 123:
                raise KeyboardInterrupt

        with (
            mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(app_logging, "assert_fd_is_private_directory"),
            mock.patch.object(app_logging, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(app_logging.os, "fstat", side_effect=RuntimeError("not regular")),
            mock.patch.object(app_logging.os, "close", side_effect=close),
        ):
            with self.assertRaisesRegex(RuntimeError, "not regular") as caught:
                handler._open()

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_copy_log_content_closes_source_fd_when_fdopen_fails(self) -> None:
        with (
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fdopen", side_effect=ValueError("bad source fd")),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaisesRegex(ValueError, "bad source fd"):
                app_logging._copy_log_content(Path("/probe.log"), mock.Mock())

        mocked_close.assert_called_once_with(123)

    def test_copy_log_content_rejects_source_swap_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.log"
            path.write_text("original\n", encoding="utf-8")
            path.chmod(0o600)
            expected_stat = path.lstat()
            path.rename(Path(tmp) / "source-original.log")
            path.write_text("replacement\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "changed before opening"):
                app_logging._copy_log_content(path, mock.Mock(), expected_stat=expected_stat)

    def test_open_log_source_preserves_validation_error_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(app_logging, "_assert_regular_unlinked_file"),
            mock.patch.object(app_logging, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(app_logging, "assert_fd_is_regular_private_file", side_effect=RuntimeError("not regular")),
            mock.patch.object(app_logging.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "not regular") as caught:
                app_logging._open_log_source_file(Path("/probe.log"), field_name="log source file")

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_open_log_source_preserves_validation_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "_assert_regular_unlinked_file"),
            mock.patch.object(app_logging, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(app_logging, "assert_fd_is_regular_private_file", side_effect=RuntimeError("not regular")),
            mock.patch.object(app_logging.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(RuntimeError, "not regular") as caught:
                app_logging._open_log_source_file(Path("/probe.log"), field_name="log source file")

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_open_log_source_closes_fd_when_validation_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "_assert_regular_unlinked_file", return_value=os.stat(__file__)),
            mock.patch.object(app_logging, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(app_logging, "assert_fd_is_regular_private_file"),
            mock.patch.object(app_logging.os, "fstat", side_effect=KeyboardInterrupt),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                app_logging._open_log_source_file(Path("/probe.log"), field_name="log source file")

        mocked_close.assert_called_once_with(123)

    def test_open_log_source_rejects_path_swap_after_initial_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.log"
            path.write_text("original\n", encoding="utf-8")
            path.chmod(0o600)
            real_open = app_logging.open_file_without_following_symlinks

            def open_and_swap(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                path.rename(path.with_name("source-original.log"))
                path.write_text("replacement\n", encoding="utf-8")
                return fd

            with mock.patch.object(app_logging, "open_file_without_following_symlinks", side_effect=open_and_swap):
                with self.assertRaisesRegex(RuntimeError, "changed while opening"):
                    app_logging._open_log_source_file(path, field_name="log source file")

            self.assertEqual(path.read_text(encoding="utf-8"), "replacement\n")

    def test_copy_log_content_preserves_fdopen_error_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fdopen", side_effect=ValueError("bad source fd")),
            mock.patch.object(app_logging.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(ValueError, "bad source fd") as caught:
                app_logging._copy_log_content(Path("/probe.log"), mock.Mock())

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_copy_log_content_preserves_fdopen_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fdopen", side_effect=ValueError("bad source fd")),
            mock.patch.object(app_logging.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(ValueError, "bad source fd") as caught:
                app_logging._copy_log_content(Path("/probe.log"), mock.Mock())

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_copy_log_content_closes_source_when_fdopen_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fdopen", side_effect=KeyboardInterrupt),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                app_logging._copy_log_content(Path("/probe.log"), mock.Mock())

        mocked_close.assert_called_once_with(123)

    def test_copy_log_content_preserves_read_interrupt_when_source_close_fails(self) -> None:
        class _Source:
            def read(self, _size: int = -1) -> bytes:
                raise KeyboardInterrupt("log read interrupted")

            def close(self) -> None:
                raise OSError("source close failed")

        with (
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fdopen", return_value=_Source()),
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "log read interrupted") as caught:
                app_logging._copy_log_content(Path("/probe.log"), mock.Mock())

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("source close failed", "\n".join(caught.exception.__notes__))

    def test_gzip_file_preserves_copy_error_when_input_close_fails(self) -> None:
        input_file = mock.MagicMock()
        input_file.__enter__.return_value = input_file
        input_file.__exit__.side_effect = lambda *_args: input_file.close()
        input_file.close.side_effect = OSError("source close failed")
        raw_output = mock.MagicMock()
        raw_output.__enter__.return_value = raw_output
        raw_output.__exit__.return_value = False
        gzip_output = mock.MagicMock()
        gzip_output.__enter__.return_value = gzip_output
        gzip_output.__exit__.return_value = False

        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(app_logging.os, "fdopen", side_effect=[input_file, raw_output]),
            mock.patch.object(app_logging.gzip, "GzipFile", return_value=gzip_output),
            mock.patch.object(app_logging, "_copy_stream_capped", side_effect=RuntimeError("copy failed")),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close"),
        ):
            with self.assertRaisesRegex(RuntimeError, "copy failed") as caught:
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("source close failed", "\n".join(caught.exception.__notes__))

    def test_unlink_log_file_preserves_success_when_parent_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.log"
            path.write_text("content\n", encoding="utf-8")
            expected_stat = path.stat()
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == parent_fd:
                    raise OSError("close failed")
                real_close(fd)

            try:
                with (
                    mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(app_logging.os, "close", side_effect=close_wrapper),
                ):
                    self.assertTrue(app_logging._unlink_log_file_with_parent_fsync(path, expected_stat, field_name="log file"))
            finally:
                real_close(parent_fd)

            self.assertFalse(path.exists())

    def test_unlink_log_file_preserves_delete_error_when_parent_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(app_logging.os, "stat", return_value=os.stat(__file__)),
            mock.patch.object(app_logging.os, "unlink", side_effect=OSError("delete failed")),
            mock.patch.object(app_logging.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(OSError, "delete failed") as caught:
                app_logging._unlink_log_file_with_parent_fsync(
                    Path("/probe/source.log"), os.stat(__file__), field_name="log file"
                )

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_unlink_log_temp_does_not_remove_replaced_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            temp_path = root / ".archive.tmp"
            replacement = root / "replacement.log"
            temp_path.write_bytes(b"temporary")
            expected_stat = temp_path.stat()
            replacement.write_bytes(b"replacement")
            os.replace(replacement, temp_path)
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with self.assertRaisesRegex(RuntimeError, "changed before cleanup"):
                    app_logging._unlink_log_temp(parent_fd, temp_path.name, expected_stat=expected_stat)
            finally:
                os.close(parent_fd)

            self.assertEqual(temp_path.read_bytes(), b"replacement")

    def test_gzip_file_closes_source_when_source_inspection_fails(self) -> None:
        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", side_effect=OSError("inspect failed")),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(OSError, "inspect failed") as caught:
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_gzip_file_preserves_source_inspection_error_when_fd_close_is_interrupted(self) -> None:
        def close(fd: int) -> None:
            if fd == 123:
                raise KeyboardInterrupt

        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", side_effect=OSError("inspect failed")),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close", side_effect=close),
        ):
            with self.assertRaisesRegex(OSError, "inspect failed") as caught:
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_gzip_file_preserves_source_fdopen_error_when_close_fails(self) -> None:
        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(app_logging.os, "fdopen", side_effect=ValueError("source fdopen failed")),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(ValueError, "source fdopen failed") as caught:
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_gzip_file_closes_temp_fd_when_output_fdopen_fails(self) -> None:
        input_file = mock.Mock()
        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(app_logging.os, "fdopen", side_effect=[input_file, ValueError("output fdopen failed")]),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close", side_effect=OSError("close failed")) as mocked_close,
        ):
            with self.assertRaisesRegex(ValueError, "output fdopen failed") as caught:
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        input_file.close.assert_called_once_with()
        self.assertIn(mock.call(456), mocked_close.call_args_list)
        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_gzip_file_closes_source_and_temp_fds_when_fdopen_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(app_logging.os, "fdopen", side_effect=KeyboardInterrupt),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)
        mocked_close.assert_any_call(789)

    def test_gzip_file_closes_temp_fd_when_output_fdopen_is_interrupted(self) -> None:
        input_file = mock.Mock()
        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(app_logging.os, "fdopen", side_effect=[input_file, KeyboardInterrupt]),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        input_file.close.assert_called_once_with()
        mocked_close.assert_any_call(456)
        mocked_close.assert_any_call(789)

    def test_gzip_file_preserves_output_fdopen_error_when_input_close_is_interrupted(self) -> None:
        input_file = mock.Mock()
        input_file.close.side_effect = KeyboardInterrupt("input close interrupted")
        with (
            mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".target.tmp")),
            mock.patch.object(app_logging, "_open_log_source_file", return_value=123),
            mock.patch.object(app_logging.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(
                app_logging.os,
                "fdopen",
                side_effect=[input_file, ValueError("output fdopen failed")],
            ),
            mock.patch.object(app_logging, "_unlink_log_temp"),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaisesRegex(ValueError, "output fdopen failed") as caught:
                app_logging._gzip_file(Path("/probe/source.log"), Path("/probe/target.log.gz"))

        input_file.close.assert_called_once_with()
        mocked_close.assert_any_call(456)
        mocked_close.assert_any_call(789)
        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_monthly_merge_closes_temp_fd_when_fdopen_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "speed-of-cinnamon-2026-05-30.log"
            source.write_text("source\n", encoding="utf-8")
            source.chmod(0o600)
            with (
                mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".archive.tmp")),
                mock.patch.object(app_logging.os, "fdopen", side_effect=KeyboardInterrupt),
                mock.patch.object(app_logging.os, "close") as mocked_close,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    app_logging._merge_old_months(log_dir, date(2026, 6, 1))

            mocked_close.assert_any_call(456)
            mocked_close.assert_any_call(789)

    def test_monthly_merge_preserves_fdopen_error_when_temp_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "speed-of-cinnamon-2026-05-30.log"
            source.write_text("source\n", encoding="utf-8")
            source.chmod(0o600)

            def close(fd: int) -> None:
                if fd == 456:
                    raise KeyboardInterrupt

            with (
                mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".archive.tmp")),
                mock.patch.object(app_logging.os, "fdopen", side_effect=ValueError("temp fdopen failed")),
                mock.patch.object(app_logging, "_unlink_log_temp"),
                mock.patch.object(app_logging.os, "close", side_effect=close),
            ):
                with self.assertRaisesRegex(ValueError, "temp fdopen failed") as caught:
                    app_logging._merge_old_months(log_dir, date(2026, 6, 1))

            self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_monthly_merge_preserves_copy_error_when_temp_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "speed-of-cinnamon-2026-05-30.log"
            source.write_text("source\n", encoding="utf-8")
            source.chmod(0o600)
            raw_output = mock.MagicMock()
            raw_output.__enter__.return_value = raw_output
            raw_output.__exit__.side_effect = lambda *_args: raw_output.close()
            raw_output.close.side_effect = OSError("archive close failed")
            gzip_output = mock.MagicMock()
            gzip_output.__enter__.return_value = gzip_output
            gzip_output.__exit__.return_value = False

            with (
                mock.patch.object(app_logging, "_create_log_temp_file", return_value=(456, 789, ".archive.tmp")),
                mock.patch.object(app_logging.os, "fdopen", return_value=raw_output),
                mock.patch.object(app_logging.gzip, "GzipFile", return_value=gzip_output),
                mock.patch.object(app_logging, "_copy_log_content", side_effect=RuntimeError("copy failed")),
                mock.patch.object(app_logging, "_unlink_log_temp"),
                mock.patch.object(app_logging.os, "close"),
            ):
                with self.assertRaisesRegex(RuntimeError, "copy failed") as caught:
                    app_logging._merge_old_months(log_dir, date(2026, 6, 1))

            self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))
            self.assertIn("archive close failed", "\n".join(caught.exception.__notes__))

    def test_monthly_merge_removes_temp_archive_when_copy_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "speed-of-cinnamon-2026-05-30.log"
            source.write_text("source\n", encoding="utf-8")
            source.chmod(0o600)
            with mock.patch.object(app_logging, "_copy_log_content", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    app_logging._merge_old_months(log_dir, date(2026, 6, 1))

            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_monthly_merge_cleans_temp_when_initial_fd_stat_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "speed-of-cinnamon-2026-05-30.log"
            source.write_text("private log payload\n", encoding="utf-8")
            source.chmod(0o600)
            real_fstat = app_logging.os.fstat
            failed = False
            real_copy = app_logging._copy_log_content

            def fstat(fd: int) -> os.stat_result:
                nonlocal failed
                result = real_fstat(fd)
                if not failed and stat_module.S_ISREG(result.st_mode) and result.st_size == 0:
                    failed = True
                    raise OSError("temporary identity inspection failed")
                return result

            def copy_then_fail(
                path: Path,
                output: gzip.GzipFile,
                *,
                expected_stat: os.stat_result | None = None,
            ) -> None:
                real_copy(path, output, expected_stat=expected_stat)
                raise RuntimeError("copy failed after write")

            with (
                mock.patch.object(app_logging.os, "fstat", side_effect=fstat),
                mock.patch.object(app_logging, "_copy_log_content", side_effect=copy_then_fail),
            ):
                with self.assertRaisesRegex(RuntimeError, "copy failed after write"):
                    app_logging._merge_old_months(log_dir, date(2026, 6, 1))

            self.assertTrue(failed)
            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_create_log_temp_preserves_open_error_when_parent_close_fails(self) -> None:
        with (
            mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(app_logging.os, "open", side_effect=OSError("temp open failed")),
            mock.patch.object(app_logging.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(OSError, "temp open failed") as caught:
                app_logging._create_log_temp_file(Path("/probe"), prefix="daily", suffix=".tmp")

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_create_log_temp_preserves_open_error_when_parent_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(app_logging.os, "open", side_effect=OSError("temp open failed")),
            mock.patch.object(app_logging.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(OSError, "temp open failed") as caught:
                app_logging._create_log_temp_file(Path("/probe"), prefix="daily", suffix=".tmp")

        self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_create_log_temp_closes_parent_when_open_is_interrupted(self) -> None:
        with (
            mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(app_logging.os, "open", side_effect=KeyboardInterrupt),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                app_logging._create_log_temp_file(Path("/probe"), prefix="daily", suffix=".tmp")

        mocked_close.assert_called_once_with(456)

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

    def test_file_handler_disables_permanently_on_symlinked_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_dir = root / "real-logs"
            real_dir.mkdir()
            log_dir = root / "logs"
            log_dir.symlink_to(real_dir, target_is_directory=True)
            handler = app_logging.SizeCappedJsonFileHandler(log_dir / "active.log", log_dir)
            handler.setFormatter(app_logging.JsonLogFormatter())
            record = logging.LogRecord(app_logging.LOGGER_NAME, logging.ERROR, __file__, 1, "event", (), None)

            handler.emit(record)
            handler.close()

            self.assertTrue(handler._disabled)
            self.assertFalse((real_dir / "active.log").exists())

    def test_file_handler_preserves_directory_validation_error_when_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.log"
            handler = app_logging.SizeCappedJsonFileHandler(path, Path(tmp))
            with (
                mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
                mock.patch.object(app_logging, "assert_fd_is_private_directory", side_effect=RuntimeError("not private")),
                mock.patch.object(app_logging.os, "close", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaisesRegex(RuntimeError, "not private") as caught:
                    handler._open()

            self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

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

    def test_file_handler_closes_fd_when_open_validation_is_interrupted(self) -> None:
        handler = app_logging.SizeCappedJsonFileHandler(Path("/probe.log"), Path("/probe"))
        with (
            mock.patch.object(Path, "lstat", side_effect=FileNotFoundError),
            mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(app_logging, "assert_fd_is_private_directory"),
            mock.patch.object(app_logging, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(app_logging.os, "fstat", side_effect=KeyboardInterrupt),
            mock.patch.object(app_logging.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                handler._open()

        self.assertEqual(mocked_close.call_args_list, [mock.call(456), mock.call(123)])

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

    def test_maintain_logs_compresses_oversized_rotated_daily_log_without_rename_chain(self) -> None:
        today = date(2026, 7, 10)
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{today.isoformat()}.log"
            active.write_bytes(b"x" * 101)

            with mock.patch("speed_of_cinnamon.app_logging.MAX_DAILY_LOG_BYTES", 100):
                app_logging.maintain_logs(log_dir, today=today)
                app_logging.maintain_logs(log_dir, today=today)

            rotated = log_dir / f"speed-of-cinnamon-{today.isoformat()}.1.log"
            compressed = Path(f"{rotated}.gz")
            self.assertFalse(rotated.exists())
            self.assertTrue(compressed.exists())
            with gzip.open(compressed, "rb") as handle:
                self.assertEqual(handle.read(), b"x" * 101)
            self.assertEqual(list(log_dir.glob(f"{active.stem}.*.*.log")), [])

    def test_maintain_logs_uses_supplied_today_for_active_size_limits(self) -> None:
        today = date(2026, 6, 5)
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            active = log_dir / f"speed-of-cinnamon-{today.isoformat()}.log"
            active.write_bytes(b"x" * 101)

            with mock.patch("speed_of_cinnamon.app_logging.MAX_DAILY_LOG_BYTES", 100):
                app_logging.maintain_logs(log_dir, today=today)

            self.assertFalse(active.exists())
            self.assertTrue(active.with_name(f"{active.stem}.1{active.suffix}").exists())
            self.assertFalse(Path(f"{active}.gz").exists())


    def test_rotate_active_if_needed_is_noop_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            missing = log_dir / f"speed-of-cinnamon-{date.today().isoformat()}.log"

            app_logging._rotate_active_if_needed(missing)

            self.assertFalse(missing.exists())

    def test_rotate_active_preserves_rotation_error_when_parent_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            active = Path(tmp) / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_text("content\n", encoding="utf-8")
            active.chmod(0o600)
            with (
                mock.patch.object(app_logging, "_assert_regular_unlinked_file", return_value=os.stat(__file__)),
                mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", return_value=456),
                mock.patch.object(app_logging.os, "link", side_effect=OSError("link failed")),
                mock.patch.object(app_logging.os, "close", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaisesRegex(OSError, "link failed") as caught:
                    app_logging._rotate_active_if_needed(active, force=True)

            self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_rotate_active_if_needed_bounds_occupied_rotation_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            active = Path(tmp) / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_bytes(b"x")

            with (
                mock.patch.object(app_logging, "MAX_LOG_ROTATION_CANDIDATES", 3),
                mock.patch.object(app_logging.Path, "exists", return_value=True),
                mock.patch.object(app_logging.Path, "is_symlink", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to allocate log rotation slot"):
                    app_logging._rotate_active_if_needed(active, force=True)

    def test_rotate_active_if_needed_does_not_overwrite_racing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            active = Path(tmp) / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_text("active\n", encoding="utf-8")
            racing_candidate = active.with_name(f"{active.stem}.1{active.suffix}")
            real_ensure = app_logging.ensure_directory_without_following_symlinks

            def ensure_and_race(directory: object, *args: object, **kwargs: object) -> int:
                if directory == active.parent and not racing_candidate.exists():
                    racing_candidate.write_text("racing\n", encoding="utf-8")
                return real_ensure(directory, *args, **kwargs)

            with mock.patch.object(app_logging, "ensure_directory_without_following_symlinks", side_effect=ensure_and_race):
                app_logging._rotate_active_if_needed(active, force=True)

            self.assertEqual(racing_candidate.read_text(encoding="utf-8"), "racing\n")
            self.assertEqual(
                active.with_name(f"{active.stem}.2{active.suffix}").read_text(encoding="utf-8"),
                "active\n",
            )

    def test_rotate_active_preserves_candidate_when_source_unlink_outcome_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            active = Path(tmp) / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            active.write_text("active\n", encoding="utf-8")
            candidate = active.with_name(f"{active.stem}.1{active.suffix}")
            real_unlink = app_logging.os.unlink

            def unlink_then_fail(name: str, *, dir_fd: int | None = None) -> None:
                if name == active.name and dir_fd is not None:
                    real_unlink(name, dir_fd=dir_fd)
                    raise OSError("source unlink outcome unknown")
                real_unlink(name, dir_fd=dir_fd)

            with mock.patch.object(app_logging.os, "unlink", side_effect=unlink_then_fail):
                with self.assertRaisesRegex(OSError, "source unlink outcome unknown"):
                    app_logging._rotate_active_if_needed(active, force=True)

            self.assertFalse(active.exists())
            self.assertEqual(candidate.read_text(encoding="utf-8"), "active\n")

    def test_rotate_active_rejects_source_swap_before_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / f"speed-of-cinnamon-{date.today().isoformat()}.log"
            replacement = root / "replacement.log"
            active.write_text("active\n", encoding="utf-8")
            replacement.write_text("must survive\n", encoding="utf-8")
            real_stat = app_logging.os.stat
            source_stat_calls = 0

            def stat_with_swap(name: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal source_stat_calls
                if name == active.name and kwargs.get("dir_fd") is not None:
                    source_stat_calls += 1
                    if source_stat_calls == 2:
                        active.unlink()
                        replacement.rename(active)
                return real_stat(name, *args, **kwargs)

            with mock.patch.object(app_logging.os, "stat", side_effect=stat_with_swap):
                with self.assertRaisesRegex(RuntimeError, "active log changed during rotation"):
                    app_logging._rotate_active_if_needed(active, force=True)

            self.assertEqual(source_stat_calls, 2)
            self.assertEqual(active.read_text(encoding="utf-8"), "must survive\n")

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

    def test_maintain_logs_monthly_merge_does_not_duplicate_after_source_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            first = log_dir / "speed-of-cinnamon-2026-05-30.log"
            second = log_dir / "speed-of-cinnamon-2026-05-31.log"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            first.chmod(0o600)
            second.chmod(0o600)
            real_unlink = os.unlink
            failed = False

            def unlink_once(name: object, *args: object, **kwargs: object) -> None:
                nonlocal failed
                if name == second.name and kwargs.get("dir_fd") is not None and not failed:
                    failed = True
                    raise PermissionError("cleanup denied")
                real_unlink(name, *args, **kwargs)

            with mock.patch("speed_of_cinnamon.app_logging.os.unlink", side_effect=unlink_once):
                app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                content = handle.read()

        self.assertTrue(failed)
        self.assertEqual(content.count("first"), 1)
        self.assertEqual(content.count("second"), 1)

    def test_maintain_logs_monthly_merge_does_not_duplicate_after_interrupted_source_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            first = log_dir / "speed-of-cinnamon-2026-05-30.log"
            second = log_dir / "speed-of-cinnamon-2026-05-31.log"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            first.chmod(0o600)
            second.chmod(0o600)
            real_unlink = os.unlink
            interrupted = False

            def unlink_once(name: object, *args: object, **kwargs: object) -> None:
                nonlocal interrupted
                if name == second.name and kwargs.get("dir_fd") is not None and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(app_logging.os, "unlink", side_effect=unlink_once):
                with self.assertRaises(KeyboardInterrupt):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                content = handle.read()

        self.assertTrue(interrupted)
        self.assertEqual(content.count("first"), 1)
        self.assertEqual(content.count("second"), 1)

    def test_monthly_merge_rolls_back_when_source_quarantine_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "speed-of-cinnamon-2026-05-30.log"
            source.write_text("same\n", encoding="utf-8")
            source.chmod(0o600)
            real_unlink = os.unlink
            real_rename = app_logging._rename_without_replacing

            def fail_source_unlink(name: object, *args: object, **kwargs: object) -> None:
                if name == source.name and kwargs.get("dir_fd") is not None:
                    raise PermissionError("source unlink failed")
                real_unlink(name, *args, **kwargs)

            def fail_source_quarantine(src: object, dst: object, *args: object, **kwargs: object) -> None:
                if src == source.name:
                    raise OSError("source quarantine move failed")
                real_rename(src, dst, *args, **kwargs)

            with (
                mock.patch.object(app_logging.os, "unlink", side_effect=fail_source_unlink),
                mock.patch.object(app_logging, "_rename_without_replacing", side_effect=fail_source_quarantine),
            ):
                with self.assertRaisesRegex(PermissionError, "source unlink failed"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            self.assertFalse(archive.exists())
            self.assertTrue(source.exists())

            app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read().count("same"), 1)

    def test_maintain_logs_monthly_merge_rejects_source_swap_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            may_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            may_daily.write_text("may-30\n", encoding="utf-8")
            may_daily.chmod(0o600)
            real_rename = app_logging._rename_without_replacing

            def rename_and_swap(src: object, dst: object, *args: object, **kwargs: object) -> None:
                real_rename(src, dst, *args, **kwargs)
                may_daily.unlink()
                may_daily.write_text("attacker\n", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.app_logging._rename_without_replacing", side_effect=rename_and_swap):
                with self.assertRaisesRegex(RuntimeError, "monthly log source changed before deletion"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertTrue((log_dir / "speed-of-cinnamon-2026-05.log.gz").exists())
            self.assertTrue(may_daily.exists())
            self.assertEqual(may_daily.read_text(encoding="utf-8"), "attacker\n")

    def test_maintain_logs_monthly_merge_rejects_source_swap_after_initial_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "speed-of-cinnamon-2026-05-30.log"
            replacement = log_dir / "replacement.log"
            source.write_text("source\n", encoding="utf-8")
            source.chmod(0o600)
            replacement.write_text("must survive\n", encoding="utf-8")
            real_assert = app_logging._assert_same_log_file_identity
            source_checks = 0

            def assert_with_swap(path: Path, expected_stat: os.stat_result, *, field_name: str) -> None:
                nonlocal source_checks
                source_checks += 1
                if source_checks == 2:
                    source.unlink()
                    replacement.rename(source)
                real_assert(path, expected_stat, field_name=field_name)

            with mock.patch.object(app_logging, "_assert_same_log_file_identity", side_effect=assert_with_swap):
                with self.assertRaisesRegex(RuntimeError, "monthly log source changed before cleanup"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertEqual(source_checks, 2)
            merged_sources = list(log_dir.glob("*.merged"))
            self.assertEqual(len(merged_sources), 1)
            self.assertEqual(merged_sources[0].read_text(encoding="utf-8"), "must survive\n")

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

            real_rename = app_logging._rename_without_replacing

            def fail_activation_rename(src: object, dst: object, *args: object, **kwargs: object) -> None:
                if str(src).endswith(".tmp"):
                    raise PermissionError("replace failed")
                real_rename(src, dst, *args, **kwargs)

            with (
                mock.patch("speed_of_cinnamon.app_logging._rename_without_replacing", side_effect=fail_activation_rename),
                mock.patch("speed_of_cinnamon.app_logging.os.fsync", side_effect=record_fsync),
            ):
                with self.assertRaises(PermissionError, msg="replace failure"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertTrue(old_archive.exists())
            with gzip.open(old_archive, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "legacy\n")
            self.assertTrue(old_daily.exists())
            self.assertTrue(any(stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_maintain_logs_restores_archive_after_activation_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            old_daily.write_text("may-30\n", encoding="utf-8")
            old_daily.chmod(0o600)
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")
            old_archive.chmod(0o600)
            real_fsync = app_logging.os.fsync
            directory_syncs = 0

            def fail_activation_fsync(fd: int) -> None:
                nonlocal directory_syncs
                if stat_module.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 2:
                        raise OSError("activation fsync failed")
                real_fsync(fd)

            with mock.patch.object(app_logging.os, "fsync", side_effect=fail_activation_fsync):
                with self.assertRaisesRegex(OSError, "activation fsync failed"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertEqual(directory_syncs, 4)
            self.assertTrue(old_archive.exists())
            with gzip.open(old_archive, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "legacy\n")
            self.assertTrue(old_daily.exists())
            self.assertFalse(list(log_dir.glob("*.backup")))

    def test_maintain_logs_does_not_restore_replaced_backup_during_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            replacement = log_dir / "replacement.backup"
            old_daily.write_text("may-30\n", encoding="utf-8")
            old_daily.chmod(0o600)
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")
            old_archive.chmod(0o600)
            replacement.write_text("must survive\n", encoding="utf-8")
            real_fsync = app_logging.os.fsync
            directory_syncs = 0

            def fail_backup_fsync(fd: int) -> None:
                nonlocal directory_syncs
                if stat_module.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 1:
                        backups = list(log_dir.glob(f".{old_archive.name}.*.backup"))
                        self.assertEqual(len(backups), 1)
                        backup = backups[0]
                        backup.unlink()
                        replacement.rename(backup)
                        raise OSError("backup fsync failed")
                real_fsync(fd)

            with mock.patch.object(app_logging.os, "fsync", side_effect=fail_backup_fsync):
                with self.assertRaisesRegex(OSError, "backup fsync failed"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertEqual(directory_syncs, 2)
            self.assertFalse(old_archive.exists())
            backups = list(log_dir.glob(f".{old_archive.name}.*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "must survive\n")

    def test_maintain_logs_preserves_archive_replacement_during_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            replacement = log_dir / "replacement.archive"
            old_daily.write_text("may-30\n", encoding="utf-8")
            old_daily.chmod(0o600)
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")
            old_archive.chmod(0o600)
            replacement.write_text("must survive\n", encoding="utf-8")
            real_fsync = app_logging.os.fsync
            real_stat = app_logging.os.stat
            state = {"directory_syncs": 0, "activation_failed": False, "rollback_stat_calls": 0}

            def fail_activation_fsync(fd: int) -> None:
                if stat_module.S_ISDIR(os.fstat(fd).st_mode):
                    state["directory_syncs"] += 1
                    if state["directory_syncs"] == 2:
                        state["activation_failed"] = True
                        raise OSError("activation fsync failed")
                real_fsync(fd)

            def stat_with_swap(name: object, *args: object, **kwargs: object) -> os.stat_result:
                result = real_stat(name, *args, **kwargs)
                if state["activation_failed"] and name == old_archive.name and kwargs.get("dir_fd") is not None:
                    state["rollback_stat_calls"] += 1
                    if state["rollback_stat_calls"] == 1:
                        old_archive.unlink()
                        replacement.rename(old_archive)
                return result

            with (
                mock.patch.object(app_logging.os, "fsync", side_effect=fail_activation_fsync),
                mock.patch.object(app_logging.os, "stat", side_effect=stat_with_swap),
            ):
                with self.assertRaisesRegex(OSError, "activation fsync failed") as caught:
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertIn("monthly log archive changed during activation rollback", "\n".join(caught.exception.__notes__))
            self.assertEqual(state["directory_syncs"], 2)
            self.assertEqual(state["rollback_stat_calls"], 2)
            self.assertEqual(old_archive.read_text(encoding="utf-8"), "must survive\n")

    def test_maintain_logs_restores_archive_when_backup_unlink_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            old_daily.write_text("may-30\n", encoding="utf-8")
            old_daily.chmod(0o600)
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")
            old_archive.chmod(0o600)
            real_unlink = os.unlink
            interrupted = False

            def unlink_then_interrupt(name: object, *args: object, **kwargs: object) -> None:
                nonlocal interrupted
                if name == old_archive.name and not interrupted:
                    interrupted = True
                    real_unlink(name, *args, **kwargs)
                    raise KeyboardInterrupt
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(app_logging.os, "unlink", side_effect=unlink_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertTrue(old_archive.exists())
            with gzip.open(old_archive, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "legacy\n")
            self.assertTrue(old_daily.exists())
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_maintain_logs_does_not_remove_replaced_archive_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            replacement = log_dir / "replacement.backup"
            old_daily.write_text("may-30\n", encoding="utf-8")
            old_daily.chmod(0o600)
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")
            old_archive.chmod(0o600)
            replacement.write_text("must survive\n", encoding="utf-8")
            real_assert = app_logging._assert_regular_unlinked_file
            swapped = False

            def assert_with_swap(path: Path, *, field_name: str) -> os.stat_result:
                nonlocal swapped
                if field_name == "monthly log archive backup" and not swapped:
                    backups = list(log_dir.glob(f".{old_archive.name}.*.backup"))
                    self.assertEqual(len(backups), 1)
                    backup = backups[0]
                    backup.unlink()
                    replacement.rename(backup)
                    swapped = True
                return real_assert(path, field_name=field_name)

            with mock.patch.object(app_logging, "_assert_regular_unlinked_file", side_effect=assert_with_swap):
                with self.assertRaisesRegex(RuntimeError, "monthly log archive backup changed before deletion"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertTrue(swapped)
            backups = list(log_dir.glob("*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "must survive\n")

    def test_maintain_logs_monthly_merge_does_not_overwrite_existing_backup_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            old_daily = log_dir / "speed-of-cinnamon-2026-05-30.log"
            old_daily.write_text("may-30\n", encoding="utf-8")
            old_daily.chmod(0o600)
            with gzip.open(old_archive, "wt", encoding="utf-8") as handle:
                handle.write("legacy\n")
            old_archive.chmod(0o600)
            racing_candidate = log_dir / ".speed-of-cinnamon-2026-05.log.gz.fixed.backup"
            racing_candidate.write_text("racing backup\n", encoding="utf-8")

            with mock.patch.object(
                app_logging.secrets,
                "token_hex",
                side_effect=["temp", "fixed", "free"],
            ):
                app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            self.assertEqual(racing_candidate.read_text(encoding="utf-8"), "racing backup\n")
            self.assertFalse((log_dir / ".speed-of-cinnamon-2026-05.log.gz.free.backup").exists())
            self.assertTrue(old_archive.exists())
            self.assertFalse(old_daily.exists())

    def test_maintain_logs_rolls_back_monthly_archive_after_activation_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            first = log_dir / "speed-of-cinnamon-2026-05-30.log"
            second = log_dir / "speed-of-cinnamon-2026-05-31.log"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            failed = False
            real_fsync = os.fsync

            def fail_first_directory_fsync(fd: int) -> None:
                nonlocal failed
                if not failed and stat_module.S_ISDIR(os.fstat(fd).st_mode):
                    failed = True
                    raise OSError("archive activation fsync failed")
                real_fsync(fd)

            with mock.patch("speed_of_cinnamon.app_logging.os.fsync", side_effect=fail_first_directory_fsync):
                with self.assertRaisesRegex(OSError, "archive activation fsync failed"):
                    app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            archive = log_dir / "speed-of-cinnamon-2026-05.log.gz"
            self.assertFalse(archive.exists())
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

            app_logging.maintain_logs(log_dir, today=date(2026, 6, 1))

            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                content = handle.read()
            self.assertEqual(content.count("first"), 1)
            self.assertEqual(content.count("second"), 1)

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

            with mock.patch("speed_of_cinnamon.app_logging._rename_without_replacing", side_effect=PermissionError("replace failed")):
                with self.assertRaises(PermissionError, msg="replace failure"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "content\n")
            self.assertFalse(target.exists())
            self.assertEqual(list(log_dir.glob("*.tmp")), [])

    def test_gzip_file_rolls_back_existing_target_after_activation_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            real_fsync = os.fsync
            directory_syncs = 0

            def fail_activation_sync(fd: int) -> None:
                nonlocal directory_syncs
                if stat_module.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 2:
                        raise OSError("target activation fsync failed")
                real_fsync(fd)

            with mock.patch("speed_of_cinnamon.app_logging.os.fsync", side_effect=fail_activation_sync):
                with self.assertRaisesRegex(OSError, "target activation fsync failed"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "new content\n")
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old content\n")
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_gzip_file_keeps_new_target_when_post_backup_sync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            real_fsync = os.fsync
            directory_syncs = 0

            def fail_post_backup_sync(fd: int) -> None:
                nonlocal directory_syncs
                if stat_module.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 5:
                        raise OSError("post-backup sync failed")
                real_fsync(fd)

            with mock.patch.object(app_logging.os, "fsync", side_effect=fail_post_backup_sync):
                with self.assertRaisesRegex(OSError, "post-backup sync failed"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "new content\n")
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_gzip_file_restores_target_when_target_unlink_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            real_unlink = os.unlink
            interrupted = False

            def unlink_then_interrupt(name: object, *args: object, **kwargs: object) -> None:
                nonlocal interrupted
                if name == target.name and not interrupted:
                    interrupted = True
                    real_unlink(name, *args, **kwargs)
                    raise KeyboardInterrupt
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(app_logging.os, "unlink", side_effect=unlink_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    app_logging._gzip_file(source, target)

            self.assertTrue(interrupted)
            self.assertTrue(source.exists())
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old content\n")
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_gzip_file_rejects_target_swap_during_backup_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            real_link = os.link

            def link_then_swap(src: object, dst: object, *args: object, **kwargs: object) -> None:
                real_link(src, dst, *args, **kwargs)
                if src == target.name:
                    target.unlink()
                    with gzip.open(target, "wt", encoding="utf-8") as handle:
                        handle.write("replacement content\n")
                    target.chmod(0o600)

            with mock.patch("speed_of_cinnamon.app_logging.os.link", side_effect=link_then_swap):
                with self.assertRaisesRegex(RuntimeError, "log target changed during backup activation"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "replacement content\n")
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_gzip_file_does_not_remove_replaced_target_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            real_stat = app_logging.os.stat
            backup_stat_calls = 0
            replacement_backup: Path | None = None

            def stat_then_swap(name: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal backup_stat_calls, replacement_backup
                result = real_stat(name, *args, **kwargs)
                if isinstance(name, str) and name.endswith(".backup"):
                    backup_stat_calls += 1
                    if backup_stat_calls == 2:
                        backup_path = target.parent / name
                        moved_path = backup_path.with_name(f"{backup_path.name}.moved")
                        os.replace(backup_path, moved_path)
                        backup_path.write_bytes(b"replacement backup")
                        backup_path.chmod(0o600)
                        replacement_backup = backup_path
                        return real_stat(name, *args, **kwargs)
                return result

            with mock.patch.object(app_logging.os, "stat", side_effect=stat_then_swap):
                with self.assertRaisesRegex(RuntimeError, "log target backup changed before cleanup"):
                    app_logging._gzip_file(source, target)

            self.assertEqual(backup_stat_calls, 3)
            self.assertIsNotNone(replacement_backup)
            self.assertEqual(replacement_backup.read_bytes(), b"replacement backup")

    def test_gzip_file_does_not_clobber_target_created_during_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            racing = log_dir / "racing.log.gz"
            real_rename = app_logging._rename_without_replacing

            def rename_then_race(src: object, dst: object, *args: object, **kwargs: object) -> None:
                if str(src).endswith(".tmp") and dst == target.name:
                    racing.write_text("racing target\n", encoding="utf-8")
                    racing.replace(target)
                real_rename(src, dst, *args, **kwargs)

            with mock.patch.object(app_logging, "_rename_without_replacing", side_effect=rename_then_race):
                with self.assertRaises(OSError):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "racing target\n")
            self.assertTrue(list(log_dir.glob("*.backup")))

    def test_gzip_file_removes_target_backup_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)

            real_rename = app_logging._rename_without_replacing

            def fail_activation_rename(src: object, dst: object, *args: object, **kwargs: object) -> None:
                if str(src).endswith(".tmp"):
                    raise PermissionError("replace failed")
                real_rename(src, dst, *args, **kwargs)

            with mock.patch("speed_of_cinnamon.app_logging._rename_without_replacing", side_effect=fail_activation_rename):
                with self.assertRaisesRegex(PermissionError, "replace failed"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old content\n")
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_gzip_file_rolls_back_when_target_backup_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            real_unlink = app_logging.os.unlink

            def fail_backup_unlink(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and name.endswith(".backup"):
                    raise PermissionError("backup cleanup failed")
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(app_logging.os, "unlink", side_effect=fail_backup_unlink):
                with self.assertRaisesRegex(PermissionError, "backup cleanup failed"):
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old content\n")
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_gzip_file_preserves_target_replacement_during_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            replacement = log_dir / "replacement.target"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            replacement.write_text("must survive\n", encoding="utf-8")
            real_fsync = app_logging.os.fsync
            real_stat = app_logging.os.stat
            state = {"directory_syncs": 0, "activation_failed": False, "rollback_stat_calls": 0}

            def fail_activation_fsync(fd: int) -> None:
                if stat_module.S_ISDIR(os.fstat(fd).st_mode):
                    state["directory_syncs"] += 1
                    if state["directory_syncs"] == 3:
                        state["activation_failed"] = True
                        raise OSError("target activation fsync failed")
                real_fsync(fd)

            def stat_with_swap(name: object, *args: object, **kwargs: object) -> os.stat_result:
                result = real_stat(name, *args, **kwargs)
                if state["activation_failed"] and name == target.name and kwargs.get("dir_fd") is not None:
                    state["rollback_stat_calls"] += 1
                    if state["rollback_stat_calls"] == 1:
                        target.unlink()
                        replacement.rename(target)
                return result

            with (
                mock.patch.object(app_logging.os, "fsync", side_effect=fail_activation_fsync),
                mock.patch.object(app_logging.os, "stat", side_effect=stat_with_swap),
            ):
                with self.assertRaisesRegex(OSError, "target activation fsync failed") as caught:
                    app_logging._gzip_file(source, target)

            self.assertIn("log target changed during activation rollback", "\n".join(caught.exception.__notes__))
            self.assertEqual(state["rollback_stat_calls"], 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "must survive\n")

    def test_gzip_file_keeps_new_target_when_backup_unlink_outcome_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("new content\n", encoding="utf-8")
            source.chmod(0o600)
            with gzip.open(target, "wt", encoding="utf-8") as handle:
                handle.write("old content\n")
            target.chmod(0o600)
            real_unlink = app_logging.os.unlink

            def unlink_then_fail(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and name.endswith(".backup"):
                    real_unlink(name, *args, **kwargs)
                    raise OSError("backup unlink outcome unknown")
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(app_logging.os, "unlink", side_effect=unlink_then_fail):
                with self.assertRaisesRegex(OSError, "backup unlink outcome unknown") as caught:
                    app_logging._gzip_file(source, target)

            self.assertTrue(caught.exception.__notes__)
            self.assertTrue(source.exists())
            with gzip.open(target, "rt", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "new content\n")
            self.assertEqual(list(log_dir.glob("*.backup")), [])

    def test_gzip_file_reports_temp_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("content\n", encoding="utf-8")
            source.chmod(0o600)

            with (
                mock.patch("speed_of_cinnamon.app_logging._rename_without_replacing", side_effect=PermissionError("replace failed")),
                mock.patch("speed_of_cinnamon.app_logging.os.unlink", side_effect=PermissionError("cleanup denied")),
            ):
                with self.assertRaisesRegex(PermissionError, "replace failed") as caught:
                    app_logging._gzip_file(source, target)

            self.assertTrue(source.exists())
            self.assertFalse(target.exists())
            self.assertTrue(list(log_dir.glob("*.tmp")))
            self.assertIn("log cleanup failed", "\n".join(caught.exception.__notes__))

    def test_gzip_file_removes_temp_archive_when_source_open_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            source = log_dir / "source.log"
            target = log_dir / "target.log.gz"
            source.write_text("content\n", encoding="utf-8")
            source.chmod(0o600)
            with mock.patch.object(app_logging, "_open_log_source_file", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    app_logging._gzip_file(source, target)

            self.assertEqual(list(log_dir.glob("*.tmp")), [])

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
            real_rename = app_logging._rename_without_replacing

            def rename_and_swap(src: object, dst: object, *args: object, **kwargs: object) -> None:
                real_rename(src, dst, *args, **kwargs)
                source.unlink()
                source.write_text("attacker\n", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.app_logging._rename_without_replacing", side_effect=rename_and_swap):
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
