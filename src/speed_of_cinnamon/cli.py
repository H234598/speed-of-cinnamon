from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .alarms import (
    add_alarm,
    check_due_alarms,
    list_alarm_payload,
    load_alarm_store,
    remove_alarm,
    save_alarm_store,
    set_alarm_enabled,
)
from .doctor import parse_settings_json, report as doctor_report
from .models import download_model, list_models, remove_model
from .output import insert_text
from .paths import (
    APP_ID,
    APP_NAME,
    default_settings_export_file,
    default_state_file,
    diagnostics_dir,
    ensure_runtime_dirs,
    recordings_dir,
    state_dir,
    transcript_dir,
)
from .postprocessor import (
    DEFAULT_OLLAMA_URL,
    DEFAULT_OPENAI_COMPATIBLE_URL,
    list_ollama_models,
    list_openai_compatible_models,
    post_process_text,
)
from .recorder import choose_recorder, list_input_sources, start_recorder, stop_process
from .recorder import RecorderError, validate_recording_path
from .settings_export import read_export, write_export
from .setup_plan import build_setup_plan
from .state import RecordingState, StateStore, now_iso, process_is_alive
from .text_utils import sanitize_special_chars
from .transcriber import MAX_AUDIO_PATH_CHARS, validate_audio_file, transcribe

RECORDER_START_GRACE_SECONDS = 0.2
DEFAULT_KEEP_TRANSCRIPTS = 100
DEFAULT_KEEP_RECORDINGS = 25
MAX_LOG_EXCERPT_CHARS = 2000
MAX_TRANSCRIPT_HISTORY_TEXT_CHARS = 4_000
MAX_PATH_CHARS = 240
MAX_TRANSCRIBER_TEXT_CHARS = 65_535
MAX_SETTINGS_JSON_CHARS = 250_000
MAX_SETTINGS_FILE_BYTES = 1_000_000
MAX_DIAGNOSTICS_JSON_BYTES = 1_000_000
MAX_URL_CHARS = 2_048


def _assert_text_limit(value: str, *, field_name: str, max_chars: int) -> str:
    if len(value) > max_chars:
        if field_name == "audio file path":
            raise RuntimeError(f"{field_name} is too long (max {max_chars} characters)")
        raise RuntimeError(f"{field_name} is too large (max {max_chars} characters)")
    return value


def _assert_clean_text(value: str, *, field_name: str, max_chars: int) -> str:
    if "\x00" in value:
        raise RuntimeError(f"{field_name} contains invalid null byte")
    return _assert_text_limit(value, field_name=field_name, max_chars=max_chars)


def _validate_text_model_url(url: str, *, field_name: str) -> str:
    return _assert_clean_text(url, field_name=field_name, max_chars=MAX_URL_CHARS).rstrip("/")


def _validate_pipeline_text_args(
    args: argparse.Namespace,
    *,
    language: str,
) -> str:
    language = _assert_clean_text(language, field_name="language", max_chars=MAX_PATH_CHARS)
    _assert_clean_text(args.personal_context, field_name="personal context", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.vocabulary, field_name="vocabulary", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.transcriber_command, field_name="transcriber command", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.post_process_command, field_name="post-process command", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.whisper_model, field_name="whisper model", max_chars=MAX_PATH_CHARS)
    _assert_clean_text(args.post_process_prompt, field_name="post-process prompt", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.ollama_model, field_name="ollama model", max_chars=MAX_PATH_CHARS)
    _assert_clean_text(args.openai_compatible_model, field_name="openai-compatible model", max_chars=MAX_PATH_CHARS)
    _validate_text_model_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
    _validate_text_model_url(args.openai_compatible_url or DEFAULT_OPENAI_COMPATIBLE_URL, field_name="openai-compatible url")
    return language


def read_file_tail(path: Path, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    max_chars = max(0, min(max_chars, MAX_TRANSCRIPT_HISTORY_TEXT_CHARS))
    max_bytes = max_chars * 4
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size <= max_bytes:
            handle.seek(0)
        else:
            handle.seek(size - max_bytes)
        text = handle.read().decode("utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def print_result(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        status = payload.get("status", "ok")
        message = payload.get("message") or payload.get("error") or status
        print(f"{APP_NAME}: {message}")


def append_space_if_needed(text: str, append_space: bool) -> str:
    if append_space and text and not text.endswith((" ", "\n", "\t")):
        return text + " "
    return text


def prepare_output_text(text: str, append_space: bool, sanitize: bool) -> str:
    output = sanitize_special_chars(text) if sanitize else text
    return append_space_if_needed(output, append_space)


def build_store(args: argparse.Namespace) -> StateStore:
    state_path = normalized_path(args.state_file)
    if not state_path:
        raise RuntimeError("state file path is required")
    return StateStore(state_path)


def read_log_excerpt(path: Path | None, max_chars: int = 2000) -> str:
    if not path or not path.exists():
        return ""
    try:
        text = read_file_tail(path, min(max_chars, MAX_LOG_EXCERPT_CHARS))
    except OSError:
        return ""
    return text[-max_chars:].strip()


def transcript_preview(text: str, max_chars: int = 80) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3] + "..."


def read_transcript_history(limit: int = 10) -> list[dict[str, object]]:
    if limit <= 0:
        return []
    directory = transcript_dir()
    if not directory.exists():
        return []

    candidates: list[tuple[float, Path]] = []
    for path in directory.glob("*.txt"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, path))

    candidates = sorted(candidates, reverse=True)

    entries: list[dict[str, object]] = []
    for mtime, path in candidates:
        try:
            text = read_file_tail(path, MAX_TRANSCRIPT_HISTORY_TEXT_CHARS).strip()
            modified_at = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        except OSError:
            continue
        if not text:
            continue
        entries.append(
            {
                "path": str(path),
                "name": path.name,
                "modified_at": modified_at,
                "preview": transcript_preview(text),
                "text": text,
            }
        )
        if len(entries) >= limit:
            break
    return entries


def normalized_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    return _coerce_path(path_value, field_name="path", resolve=True)


def _assert_json_payload_size(payload: dict[str, object], *, max_bytes: int) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(rendered.encode("utf-8")) > max_bytes:
        raise RuntimeError(f"output JSON is too large (max {max_bytes} bytes)")


def _write_json_atomic(path: Path, payload: dict[str, object], *, max_bytes: int) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(content.encode("utf-8")) > max_bytes:
        raise RuntimeError(f"output JSON is too large (max {max_bytes} bytes)")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(content)
        tmp_path = Path(handle.name)
    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise RuntimeError(f"failed to write JSON output: {path}") from exc


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(text)
        tmp_path = Path(handle.name)
    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise RuntimeError(f"failed to write transcript file: {path}") from exc


def _require_json_path(path_value: str, *, field_name: str, default: Path | None = None) -> Path:
    if path_value:
        path = _coerce_path(path_value, field_name=field_name, resolve=True)
    elif default is not None:
        path = default
    else:
        raise RuntimeError(f"{field_name} is required")
    if path.suffix.lower() != ".json":
        raise RuntimeError(f"{field_name} must end with .json")
    return path


def _parse_cli_settings_json(raw: str) -> dict[str, object]:
    _assert_clean_text(raw, field_name="settings JSON", max_chars=MAX_SETTINGS_JSON_CHARS)
    if len(raw) > MAX_SETTINGS_JSON_CHARS:
        raise RuntimeError(f"settings JSON is too large (max {MAX_SETTINGS_JSON_CHARS} characters)")
    try:
        return parse_settings_json(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"settings JSON could not be parsed: {exc}") from exc


def _coerce_path(
    path_value: str,
    *,
    field_name: str,
    resolve: bool = False,
    max_chars: int = MAX_PATH_CHARS,
) -> Path:
    _assert_clean_text(path_value, field_name=field_name, max_chars=max_chars)
    path = Path(path_value).expanduser()
    return path.resolve(strict=False) if resolve else path


def active_artifact_paths(state: RecordingState) -> set[Path]:
    paths: set[Path] = set()
    audio_path = _safe_recording_artifact_path(state.audio_path, suffix=".wav")
    log_path = _safe_recording_artifact_path(state.log_path, suffix=".log")
    if audio_path:
        paths.add(audio_path)
    if log_path:
        paths.add(log_path)
    path = normalized_path(state.transcript_path)
    if path:
        paths.add(path)
    return paths


def _safe_recording_artifact_path(
    value: str | None,
    *,
    suffix: str,
    require_recordings_dir: bool = True,
) -> Path | None:
    if not value:
        return None
    try:
        return validate_recording_path(Path(value), suffix=suffix, require_recordings_dir=require_recordings_dir)
    except (RecorderError, ValueError, OSError):
        return None


def _is_recording_process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    return process_is_alive(pid)


def sorted_files(paths: list[Path]) -> list[Path]:
    entries: list[tuple[float, str, Path]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file():
            entries.append((stat.st_mtime, path.name, path))
    return [path for _, _, path in sorted(entries, reverse=True)]


def delete_artifact(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def prune_files_by_mtime(paths: list[Path], keep: int, active_paths: set[Path], dry_run: bool) -> dict[str, object]:
    planned_paths: list[str] = []
    deleted_paths: list[str] = []
    failed_paths: list[str] = []
    skipped_active: list[str] = []
    candidates = sorted_files(paths)[max(keep, 0) :]
    for path in candidates:
        normalized = path.resolve(strict=False)
        if normalized in active_paths:
            skipped_active.append(str(path))
            continue
        if dry_run:
            planned_paths.append(str(path))
            continue
        if delete_artifact(path):
            deleted_paths.append(str(path))
        else:
            failed_paths.append(str(path))
    return {
        "planned_paths": planned_paths,
        "deleted_paths": deleted_paths,
        "failed_paths": failed_paths,
        "skipped_active_paths": skipped_active,
    }


def recording_groups() -> list[dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    directory = recordings_dir()
    if not directory.exists():
        return []
    for path in list(directory.glob("*.wav")) + list(directory.glob("*.log")):
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        group = groups.setdefault(path.stem, {"stem": path.stem, "mtime": 0.0, "files": []})
        group["mtime"] = max(float(group["mtime"]), stat.st_mtime)
        group_files = group["files"]
        if isinstance(group_files, list):
            group_files.append(path)
    return sorted(groups.values(), key=lambda group: (float(group["mtime"]), str(group["stem"])), reverse=True)


def prune_recording_groups(keep: int, active_paths: set[Path], dry_run: bool) -> dict[str, object]:
    planned_recordings = 0
    planned_logs = 0
    planned_paths: list[str] = []
    deleted_recordings = 0
    deleted_logs = 0
    deleted_paths: list[str] = []
    failed_paths: list[str] = []
    skipped_active_paths: list[str] = []
    groups = recording_groups()[max(keep, 0) :]
    for group in groups:
        files = group.get("files", [])
        if not isinstance(files, list):
            continue
        if any(path.resolve(strict=False) in active_paths for path in files):
            skipped_active_paths.extend(str(path) for path in files)
            continue
        for path in files:
            if dry_run:
                planned_paths.append(str(path))
                if path.suffix == ".wav":
                    planned_recordings += 1
                elif path.suffix == ".log":
                    planned_logs += 1
                continue
            if delete_artifact(path):
                deleted_paths.append(str(path))
                if path.suffix == ".wav":
                    deleted_recordings += 1
                elif path.suffix == ".log":
                    deleted_logs += 1
            else:
                failed_paths.append(str(path))
    return {
        "planned_recordings": planned_recordings,
        "planned_logs": planned_logs,
        "planned_paths": planned_paths,
        "deleted_recordings": deleted_recordings,
        "deleted_logs": deleted_logs,
        "deleted_paths": deleted_paths,
        "failed_paths": failed_paths,
        "skipped_active_paths": skipped_active_paths,
    }


def command_start(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    current = store.read()
    if current.status == "recording":
        current_audio_path = _safe_recording_artifact_path(
            current.audio_path, suffix=".wav", require_recordings_dir=False
        )
        if _is_recording_process_alive(current.pid):
            return {
                "status": "recording",
                "message": "already recording",
                "pid": current.pid,
                "language": current.language,
            }
        if current_audio_path and current_audio_path.exists() and current_audio_path.stat().st_size > 0:
            recorded = store.update(status="recorded", stopped_at=current.stopped_at or now_iso())
            return {
                "status": "recorded",
                "message": "previous recording has exited; run stop or toggle to transcribe",
                "audio_path": recorded.audio_path,
                "language": recorded.language,
            }
        if current.audio_path and not current_audio_path:
            store.update(
                status="error",
                stopped_at=current.stopped_at or now_iso(),
                error="recording state references an invalid artifact path",
            )
            return {
                "status": "error",
                "message": "recording state references an invalid artifact path",
            }
        store.update(status="error", stopped_at=current.stopped_at or now_iso(), error="recording exited before audio was saved")
        return {
            "status": "error",
            "message": "recording exited before audio was saved",
        }

    stamp = timestamp()
    audio_path = recordings_dir() / f"{stamp}.wav"
    log_path = recordings_dir() / f"{stamp}.log"
    audio_path = validate_recording_path(audio_path, suffix=".wav", require_recordings_dir=True)
    log_path = validate_recording_path(log_path, suffix=".log", require_recordings_dir=True)
    command = choose_recorder(args.recorder, audio_path, args.max_seconds, args.input_device)
    proc = start_recorder(command, log_path)
    time.sleep(RECORDER_START_GRACE_SECONDS)
    if proc.poll() is not None:
        detail = read_log_excerpt(log_path) or f"exit code {proc.returncode}"
        raise RuntimeError(f"{command.name} exited immediately: {detail}")
    language = args.language or "en"
    state = RecordingState(
        status="recording",
        pid=proc.pid,
        audio_path=str(audio_path),
        log_path=str(log_path),
        started_at=now_iso(),
        language=language,
        recorder=command.name,
        max_seconds=args.max_seconds,
        input_device=args.input_device,
    )
    store.write(state)
    return {
        "status": "recording",
        "message": "recording started",
        "pid": proc.pid,
        "audio_path": str(audio_path),
        "recorder": command.name,
        "input_device": args.input_device,
        "language": language,
    }


def finalize_recording(args: argparse.Namespace, store: StateStore, state: RecordingState) -> dict[str, object]:
    if not state.audio_path:
        raise RuntimeError("no recording is available")
    audio_path = _safe_recording_artifact_path(state.audio_path, suffix=".wav", require_recordings_dir=False)
    if not audio_path:
        store.update(status="error", stopped_at=state.stopped_at or now_iso(), error="recording audio path is invalid")
        raise RuntimeError("recording audio path is invalid")
    chosen_language = state.language or args.language or "en"
    language = _validate_pipeline_text_args(args, language=chosen_language)
    try:
        audio_path = validate_audio_file(audio_path)
        text_path = transcript_dir() / f"{audio_path.stem}.txt"
        text = transcribe(
            audio_path=audio_path,
            language=language,
            text_path=text_path,
            command_template=args.transcriber_command,
            backend=args.transcriber,
            whisper_model=args.whisper_model,
            personal_context=args.personal_context,
            vocabulary=args.vocabulary,
        )
        text = post_process_text(
            text,
            language,
            args.post_process_command,
            args.personal_context,
            args.vocabulary,
            args.post_process_backend,
            args.ollama_model,
            args.ollama_url,
            args.post_process_prompt,
            args.openai_compatible_model,
            args.openai_compatible_url,
        )
        _write_text_atomic(text_path, text.strip() + "\n")
        text_to_insert = prepare_output_text(text, args.append_space, args.sanitize_special_chars)
        inserted = insert_text(text_to_insert, args.insert_method, args.typing_delay_ms)
    except Exception as exc:
        store.update(status="error", stopped_at=state.stopped_at or now_iso(), error=str(exc))
        raise
    keep_recording_artifacts = bool(getattr(args, "keep_recording_artifacts", False))
    audio_deleted = False
    log_deleted = False
    done_audio_path = state.audio_path
    done_log_path = state.log_path
    if not keep_recording_artifacts:
        audio_deleted = remove_file(state.audio_path, suffix=".wav")
        log_deleted = remove_file(state.log_path, suffix=".log")
        done_audio_path = None
        done_log_path = None
    done = store.update(
        status="done",
        stopped_at=state.stopped_at or now_iso(),
        audio_path=done_audio_path,
        log_path=done_log_path,
        transcript=text,
        transcript_path=str(text_path),
        inserted=inserted,
        error="",
    )
    return {
        "status": done.status,
        "message": "transcription completed",
        "transcript": text,
        "transcript_path": str(text_path),
        "inserted": inserted,
        "language": language,
        "recording_artifacts_kept": keep_recording_artifacts,
        "audio_deleted": audio_deleted,
        "log_deleted": log_deleted,
    }


def remove_file(path_value: str | None, *, suffix: str | None = None) -> bool:
    if not path_value:
        return False
    try:
        path_value = _assert_clean_text(path_value, field_name="path", max_chars=MAX_PATH_CHARS)
    except RuntimeError:
        return False
    if suffix:
        try:
            path = validate_recording_path(Path(path_value), suffix=suffix, require_recordings_dir=True)
        except (RecorderError, ValueError, OSError):
            return False
    else:
        path = Path(path_value)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def command_stop(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    state = store.read()
    if state.status != "recording":
        if state.status in {"recorded", "processing"} and state.audio_path:
            return finalize_recording(args, store, state)
        return {"status": state.status, "message": "not recording"}

    if _is_recording_process_alive(state.pid):
        stop_process(int(state.pid))
    state = store.update(status="processing", stopped_at=now_iso())
    return finalize_recording(args, store, state)


def command_cancel(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    state = store.read()
    if state.status == "recording" and _is_recording_process_alive(state.pid):
        stop_process(int(state.pid))

    discarded_audio_path = state.audio_path
    audio_deleted = remove_file(state.audio_path, suffix=".wav")
    log_deleted = remove_file(state.log_path, suffix=".log")
    store.write(
        RecordingState(
            status="idle",
            stopped_at=now_iso(),
            language=state.language,
            recorder=state.recorder,
            input_device=state.input_device,
            max_seconds=state.max_seconds,
        )
    )
    if state.status in {"recording", "recorded", "processing"} or discarded_audio_path:
        return {
            "status": "idle",
            "message": "recording discarded",
            "discarded_audio_path": discarded_audio_path,
            "audio_deleted": audio_deleted,
            "log_deleted": log_deleted,
        }
    return {"status": "idle", "message": "nothing to cancel"}


def command_toggle(args: argparse.Namespace) -> dict[str, object]:
    store = build_store(args)
    state = store.read()
    if state.status == "recording":
        if _is_recording_process_alive(state.pid):
            return command_stop(args)
        if state.audio_path:
            store.update(status="processing", stopped_at=state.stopped_at or now_iso())
            return command_stop(args)
    return command_start(args)


def command_status(args: argparse.Namespace) -> dict[str, object]:
    state = build_store(args).read()
    payload = asdict(state)
    if state.status == "recording" and not _is_recording_process_alive(state.pid):
        payload["status"] = "recorded"
        payload["message"] = "recording process has exited; run stop to transcribe"
    return payload


def command_doctor(args: argparse.Namespace) -> dict[str, object]:
    settings = _parse_cli_settings_json(getattr(args, "settings_json", ""))
    return doctor_report(settings, applet=getattr(args, "applet", False))


def command_setup(args: argparse.Namespace) -> dict[str, object]:
    settings = _parse_cli_settings_json(getattr(args, "settings_json", ""))
    doctor_payload = doctor_report(settings, applet=getattr(args, "applet", False))
    return {
        "status": "done",
        "doctor": doctor_payload,
        **build_setup_plan(doctor_payload),
    }


def command_list_inputs(args: argparse.Namespace) -> dict[str, object]:
    sources = list_input_sources(args.include_monitors)
    return {
        "status": "done",
        "sources": [
            {
                "id": source.id,
                "name": source.name,
                "description": source.description,
                "driver": source.driver,
                "state": source.state,
                "default": source.default,
                "monitor": source.monitor,
            }
            for source in sources
        ],
    }


def command_models(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return {"status": "done", "models": list_models()}


def command_text_models(args: argparse.Namespace) -> dict[str, object]:
    backend = (args.backend or "ollama").strip().lower().replace("_", "-")
    if backend in {"openai-compatible", "openai", "local-openai"}:
        url = _validate_text_model_url(args.openai_compatible_url or DEFAULT_OPENAI_COMPATIBLE_URL, field_name="openai-compatible url")
        return {
            "status": "done",
            "backend": "openai-compatible",
            "url": url,
            **list_openai_compatible_models(url),
        }
    url = _validate_text_model_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
    return {
        "status": "done",
        "backend": "ollama",
        "url": url,
        **list_ollama_models(url),
    }


def command_download_model(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return download_model(args.model, args.force)


def command_remove_model(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return remove_model(args.model)


def command_history(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return {"status": "done", "transcripts": read_transcript_history(max(args.limit, 0))}


def command_cleanup(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    state = build_store(args).read()
    active_paths = active_artifact_paths(state)
    transcript_result = prune_files_by_mtime(
        list(transcript_dir().glob("*.txt")),
        args.keep_transcripts,
        active_paths,
        args.dry_run,
    )
    recording_result = prune_recording_groups(args.keep_recordings, active_paths, args.dry_run)
    deleted_transcripts = len(transcript_result["deleted_paths"])
    deleted_recordings = int(recording_result["deleted_recordings"])
    deleted_logs = int(recording_result["deleted_logs"])
    would_delete_transcripts = len(transcript_result["planned_paths"])
    would_delete_recordings = int(recording_result["planned_recordings"])
    would_delete_logs = int(recording_result["planned_logs"])
    total = (
        would_delete_transcripts + would_delete_recordings + would_delete_logs
        if args.dry_run
        else deleted_transcripts + deleted_recordings + deleted_logs
    )
    verb = "would clean" if args.dry_run else "cleaned"
    return {
        "status": "done",
        "message": f"{verb} {total} old file(s)",
        "dry_run": args.dry_run,
        "keep_transcripts": max(args.keep_transcripts, 0),
        "keep_recordings": max(args.keep_recordings, 0),
        "deleted_transcripts": deleted_transcripts,
        "deleted_recordings": deleted_recordings,
        "deleted_logs": deleted_logs,
        "would_delete_transcripts": would_delete_transcripts,
        "would_delete_recordings": would_delete_recordings,
        "would_delete_logs": would_delete_logs,
        "deleted_paths": transcript_result["deleted_paths"] + recording_result["deleted_paths"],
        "would_delete_paths": transcript_result["planned_paths"] + recording_result["planned_paths"],
        "failed_paths": transcript_result["failed_paths"] + recording_result["failed_paths"],
        "skipped_active_paths": transcript_result["skipped_active_paths"] + recording_result["skipped_active_paths"],
    }


def command_diagnostics(args: argparse.Namespace) -> dict[str, object]:
    payload = build_diagnostics_payload(args)
    output = str(getattr(args, "output", "") or "").strip()
    if output or getattr(args, "save", False):
        path = (
            _require_json_path(output, field_name="diagnostics output")
            if output
            else diagnostics_dir() / f"diagnostics-{timestamp()}.json"
        )
        _assert_json_payload_size(payload, max_bytes=MAX_DIAGNOSTICS_JSON_BYTES)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload["saved_path"] = str(path)
        _write_json_atomic(path, payload, max_bytes=MAX_DIAGNOSTICS_JSON_BYTES)
        payload["message"] = f"diagnostics saved to {path}"
    return payload


def command_alarms_list(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return list_alarm_payload()


def command_alarms_add(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    alarm = add_alarm(
        args.time,
        name=args.name,
        days=args.days,
        urgency=args.urgency,
        enabled=not args.disabled,
    )
    return {"status": "done", "message": f"alarm added: {alarm['label']} at {alarm['time']}", "alarm": alarm}


def command_alarms_remove(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return remove_alarm(args.id)


def command_alarms_enable(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return set_alarm_enabled(args.id, True)


def command_alarms_disable(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return set_alarm_enabled(args.id, False)


def command_alarms_check(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return check_due_alarms(mark=args.mark, catch_up_minutes=args.catch_up_minutes)


def build_diagnostics_payload(args: argparse.Namespace) -> dict[str, object]:
    settings_json = getattr(args, "settings_json", "")
    settings = _parse_cli_settings_json(settings_json)
    ensure_runtime_dirs()
    applet = getattr(args, "applet", False)
    alarm_payload = list_alarm_payload()
    alarm_entries = alarm_payload.get("alarms", [])
    if not isinstance(alarm_entries, list):
        alarm_entries = []
    source_payload: dict[str, object]
    try:
        sources = list_input_sources(False)
        source_payload = {
            "ok": True,
            "sources": [
                {
                    "name": source.name,
                    "description": source.description,
                    "default": source.default,
                    "state": source.state,
                }
                for source in sources
            ],
        }
    except Exception as exc:
        source_payload = {"ok": False, "error": str(exc)}

    transcript_entries = [
        {key: entry[key] for key in ("name", "path", "modified_at") if key in entry}
        for entry in read_transcript_history(5)
    ]
    state_payload = asdict(build_store(args).read())
    state_payload["transcript_length"] = len(str(state_payload.get("transcript") or ""))
    state_payload.pop("transcript", None)
    state_file_path = normalized_path(args.state_file)
    if state_file_path is None:
        state_file_path = _coerce_path(str(args.state_file), field_name="state file")
    return {
        "status": "done",
        "message": "diagnostics collected",
        "app": {
            "id": APP_ID,
            "name": APP_NAME,
            "version": __version__,
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "desktop": {
            "current_desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
            "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
            "desktop_session": os.environ.get("DESKTOP_SESSION", ""),
        },
        "paths": {
            "state_dir": str(state_dir()),
            "state_file": str(state_file_path),
            "transcript_dir": str(transcript_dir()),
            "recordings_dir": str(recordings_dir()),
            "diagnostics_dir": str(diagnostics_dir()),
        },
        "state": state_payload,
        "doctor": doctor_report(settings, applet=applet),
        "inputs": source_payload,
        "models": list_models(),
        "alarms": {
            "configured": len(alarm_entries),
            "active": sum(1 for alarm in alarm_entries if isinstance(alarm, dict) and alarm.get("enabled", True)),
            "last_checked_at": str(alarm_payload.get("last_checked_at") or ""),
        },
        "recent_transcripts": transcript_entries,
    }


def command_settings_export(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    settings = _parse_cli_settings_json(args.settings_json)
    path = _require_json_path(args.output, field_name="settings export output", default=default_settings_export_file())
    payload = write_export(path, settings, load_alarm_store())
    if path.stat().st_size > MAX_SETTINGS_FILE_BYTES:
        raise RuntimeError(f"settings export file is too large (max {MAX_SETTINGS_FILE_BYTES} bytes)")
    return {
        "status": "done",
        "message": f"settings exported to {path}",
        "path": str(path),
        "settings_count": len(payload["settings"]),
        "alarms_count": len(payload["alarms"]["alarms"]),
    }


def command_settings_import(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    path = _require_json_path(args.input, field_name="settings import input", default=default_settings_export_file())
    if path.stat().st_size > MAX_SETTINGS_FILE_BYTES:
        raise RuntimeError(f"settings export file is too large (max {MAX_SETTINGS_FILE_BYTES} bytes)")
    payload = read_export(path)
    save_alarm_store(payload["alarms"])
    return {
        "status": "done",
        "message": f"settings imported from {path}",
        "path": str(path),
        "settings": payload["settings"],
        "settings_count": len(payload["settings"]),
        "alarms_count": len(payload["alarms"]["alarms"]),
        "export_version": payload["version"],
    }


def command_insert_text(args: argparse.Namespace) -> dict[str, object]:
    text = _assert_clean_text(args.text, field_name="text", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    if args.sanitize_special_chars:
        text = sanitize_special_chars(text)
    inserted = insert_text(text, args.insert_method, args.typing_delay_ms)
    return {"status": "done", "inserted": inserted}


def command_transcribe_file(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    audio_path = _coerce_path(args.audio_path, field_name="audio file path", max_chars=MAX_AUDIO_PATH_CHARS)
    language = _validate_pipeline_text_args(args, language=args.language)
    audio_path = validate_audio_file(audio_path)
    text_path = transcript_dir() / f"{audio_path.stem}.txt"
    text = transcribe(
        audio_path=audio_path,
        language=language,
        text_path=text_path,
        command_template=args.transcriber_command,
        backend=args.transcriber,
        whisper_model=args.whisper_model,
        personal_context=args.personal_context,
        vocabulary=args.vocabulary,
    )
    text = post_process_text(
        text,
        args.language,
        args.post_process_command,
        args.personal_context,
        args.vocabulary,
        args.post_process_backend,
        args.ollama_model,
        args.ollama_url,
        args.post_process_prompt,
        args.openai_compatible_model,
        args.openai_compatible_url,
    )
    _write_text_atomic(text_path, text.strip() + "\n")
    return {"status": "done", "transcript": text, "transcript_path": str(text_path)}


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", default=str(default_state_file()))
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def add_pipeline_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", default="")
    parser.add_argument("--max-seconds", type=int, default=30)
    parser.add_argument("--recorder", default="auto", choices=["auto", "pw-record", "parecord", "arecord"])
    parser.add_argument("--input-device", default="")
    parser.add_argument("--transcriber", default="auto", choices=["auto", "whisper", "whisper-cpp", "command"])
    parser.add_argument("--transcriber-command", default="")
    parser.add_argument("--whisper-model", default="")
    parser.add_argument("--post-process-backend", default="command", choices=["none", "command", "ollama", "openai-compatible"])
    parser.add_argument("--post-process-command", default="")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default="")
    parser.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    parser.add_argument("--openai-compatible-model", default="")
    parser.add_argument("--post-process-prompt", default="")
    parser.add_argument("--personal-context", default="")
    parser.add_argument("--vocabulary", default="")
    parser.add_argument(
        "--insert-method",
        default="clipboard-paste",
        choices=["clipboard-paste", "clipboard", "type", "none"],
    )
    parser.add_argument("--typing-delay-ms", type=int, default=8)
    parser.add_argument("--sanitize-special-chars", action="store_true")
    parser.add_argument("--append-space", action="store_true")
    parser.add_argument(
        "--keep-recording-artifacts",
        action="store_true",
        help="keep temporary WAV/log files after successful transcription",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="speed-of-cinnamon")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in [("start", command_start), ("stop", command_stop), ("toggle", command_toggle)]:
        child = subparsers.add_parser(name)
        add_common_options(child)
        add_pipeline_options(child)
        child.set_defaults(handler=handler)

    cancel = subparsers.add_parser("cancel")
    add_common_options(cancel)
    cancel.set_defaults(handler=command_cancel)

    status = subparsers.add_parser("status")
    add_common_options(status)
    status.set_defaults(handler=command_status)

    doctor = subparsers.add_parser("doctor")
    add_common_options(doctor)
    doctor.add_argument("--settings-json", default="")
    doctor.add_argument(
        "--applet",
        action="store_true",
        help="evaluate output readiness for the Cinnamon applet path",
    )
    doctor.set_defaults(handler=command_doctor)

    setup = subparsers.add_parser("setup")
    add_common_options(setup)
    setup.add_argument("--settings-json", default="")
    setup.add_argument(
        "--applet",
        action="store_true",
        help="build setup steps for the Cinnamon applet path",
    )
    setup.set_defaults(handler=command_setup)

    list_inputs = subparsers.add_parser("list-inputs")
    add_common_options(list_inputs)
    list_inputs.add_argument("--include-monitors", action="store_true")
    list_inputs.set_defaults(handler=command_list_inputs)

    models = subparsers.add_parser("models")
    add_common_options(models)
    models.set_defaults(handler=command_models)

    text_models = subparsers.add_parser("text-models")
    add_common_options(text_models)
    text_models.add_argument("--backend", default="ollama", choices=["ollama", "openai-compatible"])
    text_models.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    text_models.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    text_models.set_defaults(handler=command_text_models)

    download_model_parser = subparsers.add_parser("download-model")
    add_common_options(download_model_parser)
    download_model_parser.add_argument("model")
    download_model_parser.add_argument("--force", action="store_true")
    download_model_parser.set_defaults(handler=command_download_model)

    remove_model_parser = subparsers.add_parser("remove-model")
    add_common_options(remove_model_parser)
    remove_model_parser.add_argument("model")
    remove_model_parser.set_defaults(handler=command_remove_model)

    history = subparsers.add_parser("history")
    add_common_options(history)
    history.add_argument("--limit", type=int, default=10)
    history.set_defaults(handler=command_history)

    cleanup = subparsers.add_parser("cleanup")
    add_common_options(cleanup)
    cleanup.add_argument("--keep-transcripts", type=int, default=DEFAULT_KEEP_TRANSCRIPTS)
    cleanup.add_argument("--keep-recordings", type=int, default=DEFAULT_KEEP_RECORDINGS)
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.set_defaults(handler=command_cleanup)

    diagnostics = subparsers.add_parser("diagnostics")
    add_common_options(diagnostics)
    diagnostics.add_argument("--save", action="store_true")
    diagnostics.add_argument("--output", default="")
    diagnostics.add_argument("--settings-json", default="")
    diagnostics.add_argument(
        "--applet",
        action="store_true",
        help="evaluate doctor readiness for the Cinnamon applet path",
    )
    diagnostics.set_defaults(handler=command_diagnostics)

    alarms = subparsers.add_parser("alarms")
    alarm_subparsers = alarms.add_subparsers(dest="alarm_command", required=True)

    alarms_list = alarm_subparsers.add_parser("list")
    add_common_options(alarms_list)
    alarms_list.set_defaults(handler=command_alarms_list)

    alarms_add = alarm_subparsers.add_parser("add")
    add_common_options(alarms_add)
    alarms_add.add_argument("--time", required=True, help="local alarm time in HH:MM")
    alarms_add.add_argument("--name", default="")
    alarms_add.add_argument("--days", default="daily", help="daily, weekdays, weekends, or comma-separated day codes")
    alarms_add.add_argument("--urgency", default="normal", choices=["silent", "normal", "critical"])
    alarms_add.add_argument("--disabled", action="store_true")
    alarms_add.set_defaults(handler=command_alarms_add)

    alarms_remove = alarm_subparsers.add_parser("remove")
    add_common_options(alarms_remove)
    alarms_remove.add_argument("id")
    alarms_remove.set_defaults(handler=command_alarms_remove)

    alarms_enable = alarm_subparsers.add_parser("enable")
    add_common_options(alarms_enable)
    alarms_enable.add_argument("id")
    alarms_enable.set_defaults(handler=command_alarms_enable)

    alarms_disable = alarm_subparsers.add_parser("disable")
    add_common_options(alarms_disable)
    alarms_disable.add_argument("id")
    alarms_disable.set_defaults(handler=command_alarms_disable)

    alarms_check = alarm_subparsers.add_parser("check")
    add_common_options(alarms_check)
    alarms_check.add_argument("--mark", action="store_true", help="persist trigger state for due alarms")
    alarms_check.add_argument("--catch-up-minutes", type=int, default=15)
    alarms_check.set_defaults(handler=command_alarms_check)

    settings_export = subparsers.add_parser("settings-export")
    add_common_options(settings_export)
    settings_export.add_argument("--settings-json", default="{}")
    settings_export.add_argument("--output", default="")
    settings_export.set_defaults(handler=command_settings_export)

    settings_import = subparsers.add_parser("settings-import")
    add_common_options(settings_import)
    settings_import.add_argument("--input", default="")
    settings_import.set_defaults(handler=command_settings_import)

    insert = subparsers.add_parser("insert-text")
    add_common_options(insert)
    insert.add_argument("text")
    insert.add_argument("--insert-method", default="clipboard-paste", choices=["clipboard-paste", "clipboard", "type", "none"])
    insert.add_argument("--typing-delay-ms", type=int, default=8)
    insert.add_argument("--sanitize-special-chars", action="store_true")
    insert.set_defaults(handler=command_insert_text)

    transcribe_file = subparsers.add_parser("transcribe-file")
    add_common_options(transcribe_file)
    transcribe_file.add_argument("audio_path")
    transcribe_file.add_argument("--language", default="en")
    transcribe_file.add_argument("--transcriber", default="auto", choices=["auto", "whisper", "whisper-cpp", "command"])
    transcribe_file.add_argument("--transcriber-command", default="")
    transcribe_file.add_argument("--whisper-model", default="")
    transcribe_file.add_argument("--post-process-backend", default="command", choices=["none", "command", "ollama", "openai-compatible"])
    transcribe_file.add_argument("--post-process-command", default="")
    transcribe_file.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    transcribe_file.add_argument("--ollama-model", default="")
    transcribe_file.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    transcribe_file.add_argument("--openai-compatible-model", default="")
    transcribe_file.add_argument("--post-process-prompt", default="")
    transcribe_file.add_argument("--personal-context", default="")
    transcribe_file.add_argument("--vocabulary", default="")
    transcribe_file.set_defaults(handler=command_transcribe_file)
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
        print_result(payload, args.json)
        return 0 if not payload.get("error") else 1
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
        print_result(payload, getattr(args, "json", False))
        return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
