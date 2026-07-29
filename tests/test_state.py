from __future__ import annotations

import fcntl
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon.state import (
    MAX_PENDING_CLEANUP_BACKUP_ENTRIES,
    MAX_PENDING_CLEANUP_OWNER_PATH_CHARS,
    MAX_PENDING_CLEANUP_OWNER_PATHS,
    MAX_STATE_INT,
    MAX_STATE_FILE_BYTES,
    MAX_STATE_STRING_CHARS,
    MAX_STATE_PATH_CHARS,
    RecordingState,
    StateStore,
    process_is_alive,
    _contains_escaped_null,
)
from speed_of_cinnamon import state as state_module


class StateStoreTest(unittest.TestCase):
    def test_state_lock_retries_interrupted_exclusive_lock(self) -> None:
        operations: list[int] = []

        def interrupt_first_lock(_fd: int, operation: int) -> None:
            operations.append(operation)
            if len(operations) == 1:
                raise InterruptedError()

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with mock.patch.object(state_module.fcntl, "flock", side_effect=interrupt_first_lock):
                with store._locked():
                    pass

        self.assertEqual(operations, [fcntl.LOCK_EX, fcntl.LOCK_EX, fcntl.LOCK_UN])

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            _contains_escaped_null(1)  # type: ignore[arg-type]

    def test_state_store_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be a Path"):
            StateStore("state.json")  # type: ignore[arg-type]

    def test_state_store_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            StateStore(Path("state\x00.json"))

    def test_state_store_rejects_escaped_null_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            StateStore(Path("state\\\\x00.json"))

    def test_state_store_rejects_parent_traversal_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "unsafe path component"):
                StateStore(Path(tmp) / ".." / "outside" / "state.json")
            with self.assertRaisesRegex(RuntimeError, "unsafe path component"):
                StateStore(Path(tmp) / "state" / ".." / "state.json")

    def test_state_store_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "state file path must be absolute"):
            StateStore(Path("./state.json"))

    def test_state_store_rejects_control_character_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid control character"):
            StateStore(Path("state\x85spoof.json"))

    def test_state_store_rejects_escaped_control_character_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid control character"):
            StateStore(Path("state\\x85spoof.json"))

    def test_state_store_rejects_oversized_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "state file path is invalid"):
            StateStore(Path("a" * (MAX_STATE_PATH_CHARS + 1)))

    def test_state_store_rejects_oversized_path_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.state.MAX_STATE_PATH_CHARS", 4):
            with self.assertRaisesRegex(RuntimeError, "state file path is invalid"):
                StateStore(Path("é" * 3))

    def test_state_store_rejects_unencodable_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "state file path is invalid"):
            StateStore(Path("state\ud800.json"))

    def test_missing_state_defaults_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json").read()
        self.assertEqual(state.status, "idle")
        self.assertEqual(state.transcript, "")
        self.assertEqual(state.pending_cleanup_owner_paths, ())

    def test_pending_cleanup_owner_paths_round_trip_as_immutable_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            owners = (
                str(Path(tmp) / "recordings" / "recording.trimmed-retry.wav"),
                str(Path(tmp) / "recordings" / "recording.encoded-retry.flac"),
            )
            store = StateStore(path)

            store.write(
                RecordingState(
                    status="error",
                    pending_cleanup_owner_paths=owners,
                )
            )
            rendered = json.loads(path.read_text(encoding="utf-8"))
            loaded = store.read()

        self.assertEqual(rendered["pending_cleanup_owner_paths"], list(owners))
        self.assertEqual(loaded.pending_cleanup_owner_paths, owners)
        self.assertIsInstance(loaded.pending_cleanup_owner_paths, tuple)

    def test_read_rejects_malformed_pending_cleanup_owner_paths(self) -> None:
        oversized_text = "/" + ("a" * MAX_PENDING_CLEANUP_OWNER_PATH_CHARS)
        oversized_bytes = "/" + (
            "é" * (MAX_PENDING_CLEANUP_OWNER_PATH_CHARS // 2)
        )
        invalid_values = {
            "scalar": "/tmp/artifact.wav",
            "object": {"owner": "/tmp/artifact.wav"},
            "non-text entry": [1],
            "empty entry": [""],
            "relative entry": ["artifact.wav"],
            "parent traversal": ["/tmp/../artifact.wav"],
            "non-canonical entry": ["/tmp//artifact.wav"],
            "duplicate entry": ["/tmp/artifact.wav", "/tmp/artifact.wav"],
            "oversized entry": [oversized_text],
            "oversized bytes": [oversized_bytes],
            "too many entries": [
                f"/tmp/artifact-{index}.wav"
                for index in range(MAX_PENDING_CLEANUP_OWNER_PATHS + 1)
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            for case, value in invalid_values.items():
                with self.subTest(case=case):
                    path.write_text(
                        json.dumps(
                            {
                                "status": "error",
                                "pending_cleanup_owner_paths": value,
                            }
                        ),
                        encoding="utf-8",
                    )
                    path.chmod(0o600)

                    state = store.read()

                    self.assertEqual(state.error, "state file could not be read")
                    self.assertEqual(state.pending_cleanup_owner_paths, ())

    def test_pending_cleanup_owner_path_accepts_exact_shared_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            owner = "/" + (
                "a" * (MAX_PENDING_CLEANUP_OWNER_PATH_CHARS - 1)
            )
            store = StateStore(path)

            store.write(
                RecordingState(
                    status="error",
                    pending_cleanup_owner_paths=(owner,),
                )
            )
            loaded = store.read()

        self.assertEqual(loaded.pending_cleanup_owner_paths, (owner,))

    def test_pending_cleanup_backup_entries_round_trip(self) -> None:
        entry = (
            ".cleanup.0123456789abcdef.fedcba9876543210.bak"
            "|1|2|33152|1|6|7|8"
        )
        v2_entry = (
            ".cleanup.v2."
            "0123456789abcdef0123456789abcdef."
            "fedcba9876543210fedcba9876543210."
            "00112233445566778899aabbccddeeff.bak"
            "|9|10|33152|1|11|12|13"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)

            store.write(
                RecordingState(
                    status="error",
                    pending_cleanup_backup_entries=(entry, v2_entry),
                    cleanup_backup_journal_overflow=True,
                )
            )
            rendered = json.loads(path.read_text(encoding="utf-8"))
            loaded = store.read()

        self.assertEqual(
            rendered["pending_cleanup_backup_entries"],
            [entry, v2_entry],
        )
        self.assertIs(rendered["cleanup_backup_journal_overflow"], True)
        self.assertEqual(
            loaded.pending_cleanup_backup_entries,
            (entry, v2_entry),
        )
        self.assertIsInstance(loaded.pending_cleanup_backup_entries, tuple)
        self.assertIs(loaded.cleanup_backup_journal_overflow, True)

    def test_cleanup_backup_restore_journal_round_trip(self) -> None:
        audio_entry = (
            ".cleanup.v2."
            f"{'1' * 32}.{'2' * 32}.{'3' * 32}.bak"
            "|1|2|33152|1|6|7|8"
        )
        log_entry = (
            ".cleanup.v2."
            f"{'1' * 32}.{'4' * 32}.{'5' * 32}.bak"
            "|9|10|33152|1|11|12|13"
        )
        owners = (
            "/tmp/recording.wav",
            "/tmp/recording.log",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            store.write(
                RecordingState(
                    status="finalizing",
                    audio_path="/tmp/recording.wav",
                    pending_cleanup_restore_owner_paths=owners,
                    pending_cleanup_backup_entries=(
                        audio_entry,
                        log_entry,
                    ),
                    cleanup_backup_journal_restore=True,
                )
            )
            rendered = json.loads(path.read_text(encoding="utf-8"))
            loaded = store.read()

        self.assertIs(rendered["cleanup_backup_journal_restore"], True)
        self.assertIs(loaded.cleanup_backup_journal_restore, True)
        self.assertEqual(
            loaded.pending_cleanup_restore_owner_paths,
            owners,
        )
        self.assertEqual(
            loaded.pending_cleanup_backup_entries,
            (audio_entry, log_entry),
        )

    def test_read_rejects_non_boolean_cleanup_backup_journal_restore(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "finalizing",
                        "audio_path": "/tmp/recording.wav",
                        "cleanup_backup_journal_restore": 1,
                    }
                ),
                encoding="utf-8",
            )
            path.chmod(0o600)

            state = StateStore(path).read()

        self.assertEqual(state.error, "state file could not be read")
        self.assertIs(state.cleanup_backup_journal_restore, False)

    def test_read_rejects_invalid_cleanup_backup_restore_combinations(
        self,
    ) -> None:
        entry = (
            ".cleanup.v2."
            f"{'1' * 32}.{'2' * 32}.{'3' * 32}.bak"
            "|1|2|33152|1|6|7|8"
        )
        legacy_entry = (
            ".cleanup.0123456789abcdef.fedcba9876543210.bak"
            "|1|2|33152|1|6|7|8"
        )
        invalid_values = {
            "missing-entry": {},
            "legacy-entry": {
                "pending_cleanup_backup_entries": [legacy_entry],
                "pending_cleanup_restore_owner_paths": [
                    "/tmp/recording.wav"
                ],
            },
            "missing-owner": {
                "pending_cleanup_backup_entries": [entry],
            },
            "owner-count-mismatch": {
                "pending_cleanup_backup_entries": [entry],
                "pending_cleanup_restore_owner_paths": [
                    "/tmp/recording.wav",
                    "/tmp/recording.log",
                ],
            },
            "overflow": {
                "pending_cleanup_backup_entries": [entry],
                "pending_cleanup_restore_owner_paths": [
                    "/tmp/recording.wav"
                ],
                "cleanup_backup_journal_overflow": True,
            },
            "active-process": {
                "pending_cleanup_backup_entries": [entry],
                "pending_cleanup_restore_owner_paths": [
                    "/tmp/recording.wav"
                ],
                "pid": 1234,
                "process_identity": "owner-identity",
            },
            "idle": {
                "status": "idle",
                "pending_cleanup_backup_entries": [entry],
                "pending_cleanup_restore_owner_paths": [
                    "/tmp/recording.wav"
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            for case, values in invalid_values.items():
                with self.subTest(case=case):
                    payload = {
                        "status": "finalizing",
                        "audio_path": "/tmp/recording.wav",
                        "cleanup_backup_journal_restore": True,
                        **values,
                    }
                    path.write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )
                    path.chmod(0o600)

                    state = StateStore(path).read()

                    self.assertEqual(
                        state.error,
                        "state file could not be read",
                    )
                    self.assertIs(
                        state.cleanup_backup_journal_restore,
                        False,
                    )

    def test_read_rejects_malformed_pending_cleanup_backup_entries(self) -> None:
        valid_name = ".cleanup.0123456789abcdef.fedcba9876543210.bak"
        valid_entry = f"{valid_name}|1|2|33152|1|6|7|8"
        invalid_values = {
            "scalar": valid_entry,
            "non-text": [1],
            "bad basename": [f"../{valid_name}|1|2|33152|1|6|7|8"],
            "uppercase hex": [
                ".cleanup.0123456789ABCDEF.fedcba9876543210.bak"
                "|1|2|33152|1|6|7|8"
            ],
            "missing identity": [valid_name],
            "extra identity": [f"{valid_entry}|9"],
            "negative identity": [f"{valid_name}|-1|2|33152|1|6|7|8"],
            "noncanonical identity": [
                f"{valid_name}|01|2|33152|1|6|7|8"
            ],
            "oversized identity": [
                f"{valid_name}|18446744073709551616|2|33152|1|6|7|8"
            ],
            "duplicate": [valid_entry, valid_entry],
            "duplicate basename": [
                valid_entry,
                f"{valid_name}|9|2|33152|1|6|7|8",
            ],
            "too many entries": [
                (
                    f".cleanup.{index:016x}.fedcba9876543210.bak"
                    "|1|2|33152|1|6|7|8"
                )
                for index in range(MAX_PENDING_CLEANUP_BACKUP_ENTRIES + 1)
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            for case, value in invalid_values.items():
                with self.subTest(case=case):
                    path.write_text(
                        json.dumps(
                            {
                                "status": "error",
                                "pending_cleanup_backup_entries": value,
                            }
                        ),
                        encoding="utf-8",
                    )
                    path.chmod(0o600)

                    state = store.read()

                    self.assertEqual(
                        state.error,
                        "state file could not be read",
                    )
                    self.assertEqual(
                        state.pending_cleanup_backup_entries,
                        (),
                    )

    def test_read_rejects_non_boolean_cleanup_backup_journal_overflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "error",
                        "cleanup_backup_journal_overflow": 1,
                    }
                ),
                encoding="utf-8",
            )
            path.chmod(0o600)

            state = StateStore(path).read()

        self.assertEqual(state.error, "state file could not be read")
        self.assertIs(state.cleanup_backup_journal_overflow, False)

    def test_read_rejects_existing_state_without_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o600)
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_wraps_json_recursion_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle"}', encoding="utf-8")
            path.chmod(0o600)
            with mock.patch("speed_of_cinnamon.state.json.loads", side_effect=RecursionError("too deep")):
                state = StateStore(path).read()

        self.assertEqual(state.error, "state file could not be read")

    def test_read_wraps_json_memory_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle"}', encoding="utf-8")
            path.chmod(0o600)
            with mock.patch("speed_of_cinnamon.state.json.loads", side_effect=MemoryError("too large")):
                state = StateStore(path).read()

        self.assertEqual(state.error, "state file could not be read")

    def test_state_store_rejects_non_private_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o777)
            store = StateStore(Path(tmp) / "state.json")
            with self.assertRaisesRegex(RuntimeError, "state lock directory must be private"):
                store.write(RecordingState(status="idle"))

    def test_state_lock_closes_parent_fd_when_directory_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with (
                mock.patch("speed_of_cinnamon.state.ensure_directory_without_following_symlinks", return_value=123),
                mock.patch(
                    "speed_of_cinnamon.state.assert_fd_is_private_directory",
                    side_effect=RuntimeError("directory not private"),
                ),
                mock.patch("speed_of_cinnamon.state.os.close") as mocked_close,
            ):
                with self.assertRaisesRegex(RuntimeError, "directory not private"):
                    store.write(RecordingState())

            mocked_close.assert_called_once_with(123)

    def test_state_lock_closes_parent_fd_when_directory_validation_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with (
                mock.patch("speed_of_cinnamon.state.ensure_directory_without_following_symlinks", return_value=123),
                mock.patch(
                    "speed_of_cinnamon.state.assert_fd_is_private_directory",
                    side_effect=KeyboardInterrupt,
                ),
                mock.patch("speed_of_cinnamon.state.os.close") as mocked_close,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    store.write(RecordingState())

            mocked_close.assert_called_once_with(123)

    def test_state_lock_preserves_directory_validation_error_when_parent_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with (
                mock.patch("speed_of_cinnamon.state.ensure_directory_without_following_symlinks", return_value=123),
                mock.patch(
                    "speed_of_cinnamon.state.assert_fd_is_private_directory",
                    side_effect=RuntimeError("directory not private"),
                ),
                mock.patch("speed_of_cinnamon.state.os.close", side_effect=OSError("parent close failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "directory not private") as caught:
                    store.write(RecordingState())

            self.assertIn("state lock cleanup failed", "\n".join(caught.exception.__notes__))

    def test_state_lock_preserves_lock_validation_error_when_lock_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")

            def fail_lock_close(fd: int) -> None:
                if fd == 456:
                    raise OSError("lock fd close failed")

            with (
                mock.patch("speed_of_cinnamon.state.ensure_directory_without_following_symlinks", return_value=123),
                mock.patch("speed_of_cinnamon.state.assert_fd_is_private_directory"),
                mock.patch(
                    "speed_of_cinnamon.state.assert_fd_is_regular_private_file",
                    side_effect=RuntimeError("lock file not private"),
                ),
                mock.patch("speed_of_cinnamon.state.os.open", return_value=456),
                mock.patch("speed_of_cinnamon.state.fcntl.flock"),
                mock.patch("speed_of_cinnamon.state.os.close", side_effect=fail_lock_close),
            ):
                with self.assertRaisesRegex(RuntimeError, "lock file not private") as caught:
                    with store._locked():
                        pass

            self.assertIn("state lock cleanup failed", "\n".join(caught.exception.__notes__))

    def test_state_lock_closes_parent_fd_when_lock_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            close_calls: list[int] = []

            def fail_lock_close(fd: int) -> None:
                close_calls.append(fd)
                if fd == 456:
                    raise OSError("lock fd close failed")

            with (
                mock.patch("speed_of_cinnamon.state.ensure_directory_without_following_symlinks", return_value=123),
                mock.patch("speed_of_cinnamon.state.assert_fd_is_private_directory"),
                mock.patch("speed_of_cinnamon.state.assert_fd_is_regular_private_file"),
                mock.patch("speed_of_cinnamon.state.os.open", return_value=456),
                mock.patch("speed_of_cinnamon.state.fcntl.flock"),
                mock.patch("speed_of_cinnamon.state.os.close", side_effect=fail_lock_close),
            ):
                with self.assertRaisesRegex(OSError, "lock fd close failed"):
                    with store._locked():
                        pass

            self.assertEqual(close_calls, [456, 123])

    def test_state_lock_continues_cleanup_when_unlock_is_interrupted(self) -> None:
        close_calls: list[int] = []

        def interrupt_unlock(_fd: int, operation: int) -> None:
            if operation == fcntl.LOCK_UN:
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with (
                mock.patch("speed_of_cinnamon.state.ensure_directory_without_following_symlinks", return_value=123),
                mock.patch("speed_of_cinnamon.state.assert_fd_is_private_directory"),
                mock.patch("speed_of_cinnamon.state.assert_fd_is_regular_private_file"),
                mock.patch("speed_of_cinnamon.state.os.open", return_value=456),
                mock.patch("speed_of_cinnamon.state.fcntl.flock", side_effect=interrupt_unlock),
                mock.patch("speed_of_cinnamon.state.os.close", side_effect=close_calls.append),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    with store._locked():
                        pass

        self.assertEqual(close_calls, [456, 123])

    @mock.patch("speed_of_cinnamon.state.os.open", wraps=os.open)
    def test_state_lock_opens_nonblocking(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            StateStore(path).write(RecordingState())

        lock_name = f".{path.name}.lock"
        self.assertTrue(
            any(
                args[0] == lock_name
                and isinstance(args[1], int)
                and args[1] & getattr(os, "O_NONBLOCK", 0)
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_write_and_update_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.update(status="recording", pid=123, language="de")
            self.assertEqual(state.status, "recording")
            self.assertNotEqual(state.updated_at, "")
            loaded = store.read()
        self.assertEqual(loaded.pid, 123)
        self.assertEqual(loaded.language, "de")

    def test_update_rejects_corrupt_state_without_clobbering_recovery_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            original = '{"status":"finalizing","audio_path":"/tmp/audio.wav","log_path":"/tmp/rec.log"'
            path.write_text(original, encoding="utf-8")
            path.chmod(0o600)
            store = StateStore(path)

            with self.assertRaisesRegex(RuntimeError, "^state file could not be read$"):
                store.update(status="finalizing")

            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_update_reads_after_lock_to_avoid_lost_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            lock_acquired = False
            original_read = StateStore._read_unlocked

            def fake_flock(fd: int, operation: int) -> None:
                nonlocal lock_acquired
                if operation == fcntl.LOCK_EX:
                    lock_acquired = True

            def guarded_read(target: StateStore) -> RecordingState:
                self.assertTrue(lock_acquired)
                return original_read(target)

            with (
                mock.patch("speed_of_cinnamon.state.fcntl.flock", side_effect=fake_flock),
                mock.patch.object(StateStore, "_read_unlocked", guarded_read),
            ):
                state = store.update(status="recording", language="de")

        self.assertEqual(state.status, "recording")
        self.assertEqual(state.language, "de")

    def test_read_locks_before_loading_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            store.write(RecordingState(status="done"))
            with mock.patch.object(store, "_locked", wraps=store._locked) as mocked_lock:
                self.assertEqual(store.read().status, "done")

        mocked_lock.assert_called_once_with()

    def test_state_lock_rejects_hardlinked_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            lock_path = path.with_name(f".{path.name}.lock")
            backing = Path(tmp) / "foreign-lock"
            backing.write_text("lock\n", encoding="utf-8")
            try:
                os.link(backing, lock_path)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "must not be hardlinked"):
                StateStore(path).write(RecordingState(status="recording"))

            self.assertTrue(lock_path.exists())
            self.assertTrue(backing.exists())

    def test_update_returns_persisted_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.update(status="recording")
            self.assertNotEqual(state.updated_at, "")
            self.assertEqual(state.updated_at, store.read().updated_at)

    def test_update_avoids_second_read_after_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with mock.patch.object(store, "_read_unlocked", wraps=store._read_unlocked) as mocked_read:
                state = store.update(status="recording")

        self.assertEqual(state.status, "recording")
        mocked_read.assert_called_once()

    def test_invalid_boolean_error_does_not_echo_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "^state inserted contains invalid boolean value$") as raised:
            StateStore._coerce_boolean("secret-token")

        self.assertNotIn("secret-token", str(raised.exception))

    def test_state_roundtrip_preserves_text_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            store.write(RecordingState(transcript="  hello  ", audio_path=" /tmp/audio.wav "))
            loaded = store.read()
        self.assertEqual(loaded.transcript, "  hello  ")
        self.assertEqual(loaded.audio_path, " /tmp/audio.wav ")

    def test_state_roundtrip_preserves_unset_optional_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            store.write(RecordingState())
            loaded = store.read()

        self.assertIsNone(loaded.audio_path)
        self.assertIsNone(loaded.log_path)
        self.assertIsNone(loaded.started_at)
        self.assertIsNone(loaded.stopped_at)
        self.assertIsNone(loaded.transcript_path)

    def test_state_roundtrip_preserves_multiline_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            store.write(RecordingState(transcript="hello\nworld\tagain\r\n"))
            loaded = store.read()
        self.assertEqual(loaded.transcript, "hello\nworld\tagain\r\n")

    def test_state_rejects_non_newline_transcript_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with self.assertRaisesRegex(ValueError, "state transcript contains invalid control character"):
                store.write(RecordingState(transcript="hello\x1fworld"))

    @mock.patch("speed_of_cinnamon.path_safety._rename_without_replacing", side_effect=OSError("disk full"))
    def test_write_raises_runtime_error_when_atomic_activation_fails(self, mocked_link: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(RuntimeError, "^failed to persist state$"):
                store.write(store.read())
        self.assertGreaterEqual(mocked_link.call_count, 1)
        self.assertEqual(mocked_link.call_args_list[0].args[1], path.name)

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_write_uses_secure_directory_relative_replace(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            StateStore(path).write(RecordingState(status="done"))

        self.assertTrue(
            any(
                isinstance(args[0], str)
                and args[0].startswith(f".{path.name}.")
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_write_creates_parent_without_pathlib_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            with mock.patch.object(Path, "mkdir", side_effect=AssertionError("unsafe mkdir")):
                StateStore(path).write(RecordingState(status="done"))

            self.assertEqual(StateStore(path).read().status, "done")

    @mock.patch("speed_of_cinnamon.path_safety.os.chmod")
    def test_write_does_not_chmod_target_path_after_replace(self, mocked_chmod: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            StateStore(path).write(RecordingState(status="done"))

        mocked_chmod.assert_not_called()

    def test_write_does_not_mutate_input_state_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = RecordingState(updated_at="original")
            StateStore(Path(tmp) / "state.json").write(state)
        self.assertEqual(state.updated_at, "original")

    def test_read_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("x" * (MAX_STATE_FILE_BYTES + 1), encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file is too large")

    def test_read_rejects_state_file_replaced_by_broken_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            path.symlink_to(Path(tmp) / "missing.json")

            state = store.read()

        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_state_file_replaced_by_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            outside = Path(tmp) / "outside.json"
            outside.write_text('{"status":"recording"}', encoding="utf-8")
            path.symlink_to(outside)

            state = store.read()

        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_file_that_grows_after_size_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o600)

            with mock.patch(
                "speed_of_cinnamon.state.read_text_without_following_symlinks",
                side_effect=OSError("state file path is too large"),
            ) as mocked_read:
                state = StateStore(path).read()

        self.assertEqual(state.error, "state file is too large")
        mocked_read.assert_called_once_with(
            path,
            field_name="state file path",
            max_bytes=MAX_STATE_FILE_BYTES,
            require_private_mode=True,
            expected_stat=mock.ANY,
        )

    def test_read_rejects_regular_state_file_swap_before_secure_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"recording"}', encoding="utf-8")
            path.chmod(0o600)
            store = StateStore(path)
            real_read = state_module.read_text_without_following_symlinks

            def read_and_swap(*args: object, **kwargs: object) -> str:
                path.rename(Path(tmp) / "state-original.json")
                path.write_text('{"status":"done"}', encoding="utf-8")
                path.chmod(0o600)
                return real_read(*args, **kwargs)

            with mock.patch("speed_of_cinnamon.state.read_text_without_following_symlinks", side_effect=read_and_swap):
                state = store.read()

        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_world_readable_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle","transcript":"secret"}', encoding="utf-8")
            path.chmod(0o644)
            state = StateStore(path).read()

        self.assertEqual(state.error, "state file could not be read")

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_read_uses_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle"}', encoding="utf-8")
            path.chmod(0o600)
            state = StateStore(path).read()
        self.assertEqual(state.status, "idle")
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

    def test_read_rejects_invalid_utf8_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_bytes(b"\xff")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_escaped_x00_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle\\\\x00"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_null_byte_text_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle\\u0000"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_control_char_text_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"id\\u000ale"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_null_language_text_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle","language":null}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_null_status_text_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":null}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_invalid_status_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"weird"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_non_object_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('["idle"]', encoding="utf-8")
            path.chmod(0o600)
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file is malformed")

    def test_read_rejects_oversized_long_text_state(self) -> None:
        long_value = "X" * (MAX_STATE_STRING_CHARS + 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(f'{{"status":"{long_value}"}}', encoding="utf-8")
            path.chmod(0o600)
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file is too large")

    def test_write_rejects_invalid_text_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(ValueError, "state status contains invalid null byte"):
                store.write(RecordingState(status="oops\x00"))

    def test_write_rejects_control_char_text_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(ValueError, "state status contains invalid control character"):
                store.write(RecordingState(status="oops\rextra"))

    @mock.patch("speed_of_cinnamon.state.json.dumps", return_value='{"state":"\ud800"}')
    def test_write_rejects_unencodable_rendered_payload(self, mocked_dumps: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(RuntimeError, "state payload is not valid UTF-8"):
                store.write(RecordingState(status="done"))
        mocked_dumps.assert_called_once()

    def test_write_wraps_json_memory_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with (
                mock.patch("speed_of_cinnamon.state.json.dumps", side_effect=MemoryError("too large")),
                self.assertRaisesRegex(RuntimeError, "state payload could not be rendered"),
            ):
                store.write(RecordingState(status="done"))

    def test_write_wraps_json_recursion_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with (
                mock.patch("speed_of_cinnamon.state.json.dumps", side_effect=RecursionError("too deep")),
                self.assertRaisesRegex(RuntimeError, "state payload could not be rendered"),
            ):
                store.write(RecordingState(status="done"))

    def test_write_rejects_oversized_state(self) -> None:
        long_value = "Y" * (MAX_STATE_STRING_CHARS + 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            state = store.read()
            state.transcript = long_value
            with self.assertRaisesRegex(ValueError, "is too large"):
                store.update(transcript=long_value)

    def test_sanitize_text_field_rejects_oversized_text_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.state.MAX_STATE_STRING_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "is too large"):
                StateStore._sanitize_text_field("😀" * 2, field_name="status")

    def test_write_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path).read()
            state.status = "done"
            StateStore(path).write(state)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_sanitize_text_field_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            StateStore._sanitize_text_field(12, field_name="status")

    def test_sanitize_text_field_rejects_unencodable_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid Unicode characters"):
            StateStore._sanitize_text_field("label\ud800", field_name="status")

    def test_process_is_alive_rejects_non_int(self) -> None:
        self.assertFalse(process_is_alive("123"))  # type: ignore[arg-type]
        self.assertFalse(process_is_alive(True))

    def test_process_is_alive_fails_closed_on_unexpected_os_error(self) -> None:
        with mock.patch("speed_of_cinnamon.state.os.kill", side_effect=OSError("unexpected kernel error")):
            self.assertFalse(process_is_alive(1234))

    def test_process_is_alive_fails_closed_on_pid_range_error(self) -> None:
        for error in (OverflowError("out of range"), ValueError("invalid pid")):
            with self.subTest(error=type(error).__name__), mock.patch(
                "speed_of_cinnamon.state.os.kill", side_effect=error
            ):
                self.assertFalse(process_is_alive(1234))

    def test_read_rejects_invalid_boolean_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"inserted":"maybe"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_string_boolean_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"inserted":"true"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_integer_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"inserted":1}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_boolean_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"pid": true}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_boolean_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"max_seconds": true}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_float_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"pid": 12.5}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_float_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"max_seconds": 12.5}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_negative_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"pid": -1}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_negative_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"max_seconds": -5}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_oversized_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(f'{{"pid": {MAX_STATE_INT + 1}}}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_oversized_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(f'{{"max_seconds": {MAX_STATE_INT + 1}}}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")


if __name__ == "__main__":
    unittest.main()
