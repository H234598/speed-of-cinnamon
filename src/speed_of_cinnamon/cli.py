from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
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
from .settings_export import read_export, write_export
from .setup_plan import build_setup_plan
from .state import RecordingState, StateStore, now_iso, process_is_alive
from .text_utils import sanitize_special_chars
from .transcriber import transcribe

RECORDER_START_GRACE_SECONDS = 0.2
DEFAULT_KEEP_TRANSCRIPTS = 100
DEFAULT_KEEP_RECORDINGS = 25


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
    return StateStore(Path(args.state_file).expanduser())


def read_log_excerpt(path: Path | None, max_chars: int = 2000) -> str:
    if not path or not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-max_chars:]


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

    entries: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
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
    return Path(path_value).expanduser().resolve(strict=False)


def active_artifact_paths(state: RecordingState) -> set[Path]:
    paths: set[Path] = set()
    for value in (state.audio_path, state.log_path, state.transcript_path):
        path = normalized_path(value)
        if path:
            paths.add(path)
    return paths


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
        if process_is_alive(current.pid):
            return {"status": "recording", "message": "already recording", "pid": current.pid}
        if current.audio_path and Path(current.audio_path).exists() and Path(current.audio_path).stat().st_size > 0:
            recorded = store.update(status="recorded", stopped_at=current.stopped_at or now_iso())
            return {
                "status": "recorded",
                "message": "previous recording has exited; run stop or toggle to transcribe",
                "audio_path": recorded.audio_path,
            }
        store.update(status="error", stopped_at=current.stopped_at or now_iso(), error="recording exited before audio was saved")

    stamp = timestamp()
    audio_path = recordings_dir() / f"{stamp}.wav"
    log_path = recordings_dir() / f"{stamp}.log"
    command = choose_recorder(args.recorder, audio_path, args.max_seconds, args.input_device)
    proc = start_recorder(command, log_path)
    time.sleep(RECORDER_START_GRACE_SECONDS)
    if proc.poll() is not None:
        detail = read_log_excerpt(log_path) or f"exit code {proc.returncode}"
        raise RuntimeError(f"{command.name} exited immediately: {detail}")
    state = RecordingState(
        status="recording",
        pid=proc.pid,
        audio_path=str(audio_path),
        log_path=str(log_path),
        started_at=now_iso(),
        language=args.language,
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
    }


def finalize_recording(args: argparse.Namespace, store: StateStore, state: RecordingState) -> dict[str, object]:
    if not state.audio_path:
        raise RuntimeError("no recording is available")
    audio_path = Path(state.audio_path)
    text_path = transcript_dir() / f"{audio_path.stem}.txt"
    try:
        text = transcribe(
            audio_path=audio_path,
            language=args.language or state.language,
            text_path=text_path,
            command_template=args.transcriber_command,
            backend=args.transcriber,
            whisper_model=args.whisper_model,
            personal_context=args.personal_context,
            vocabulary=args.vocabulary,
        )
        text = post_process_text(
            text,
            args.language or state.language,
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
        text_path.write_text(text.strip() + "\n", encoding="utf-8")
        text_to_insert = prepare_output_text(text, args.append_space, args.sanitize_special_chars)
        inserted = insert_text(text_to_insert, args.insert_method, args.typing_delay_ms)
    except Exception as exc:
        store.update(status="error", stopped_at=state.stopped_at or now_iso(), error=str(exc))
        raise
    done = store.update(
        status="done",
        stopped_at=state.stopped_at or now_iso(),
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
    }


def remove_file(path_value: str | None) -> bool:
    if not path_value:
        return False
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

    if process_is_alive(state.pid):
        stop_process(int(state.pid))
    state = store.update(status="processing", stopped_at=now_iso())
    return finalize_recording(args, store, state)


def command_cancel(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    state = store.read()
    if state.status == "recording" and process_is_alive(state.pid):
        stop_process(int(state.pid))

    discarded_audio_path = state.audio_path
    audio_deleted = remove_file(state.audio_path)
    log_deleted = remove_file(state.log_path)
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
        if process_is_alive(state.pid):
            return command_stop(args)
        if state.audio_path:
            store.update(status="processing", stopped_at=state.stopped_at or now_iso())
            return command_stop(args)
    return command_start(args)


def command_status(args: argparse.Namespace) -> dict[str, object]:
    state = build_store(args).read()
    payload = asdict(state)
    if state.status == "recording" and not process_is_alive(state.pid):
        payload["status"] = "recorded"
        payload["message"] = "recording process has exited; run stop to transcribe"
    return payload


def command_doctor(args: argparse.Namespace) -> dict[str, object]:
    return doctor_report(
        parse_settings_json(getattr(args, "settings_json", "")),
        applet=getattr(args, "applet", False),
    )


def command_setup(args: argparse.Namespace) -> dict[str, object]:
    doctor_payload = doctor_report(
        parse_settings_json(getattr(args, "settings_json", "")),
        applet=getattr(args, "applet", False),
    )
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
        url = (args.openai_compatible_url or DEFAULT_OPENAI_COMPATIBLE_URL).rstrip("/")
        return {
            "status": "done",
            "backend": "openai-compatible",
            "url": url,
            **list_openai_compatible_models(url),
        }
    return {
        "status": "done",
        "backend": "ollama",
        "url": (args.ollama_url or DEFAULT_OLLAMA_URL).rstrip("/"),
        **list_ollama_models(args.ollama_url),
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
        path = Path(output).expanduser() if output else diagnostics_dir() / f"diagnostics-{timestamp()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        payload["saved_path"] = str(path)
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
        payload["message"] = f"diagnostics saved to {path}"
    return payload


def build_diagnostics_payload(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    settings = parse_settings_json(getattr(args, "settings_json", ""))
    applet = getattr(args, "applet", False)
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
            "state_file": str(Path(args.state_file).expanduser()),
            "transcript_dir": str(transcript_dir()),
            "recordings_dir": str(recordings_dir()),
            "diagnostics_dir": str(diagnostics_dir()),
        },
        "state": state_payload,
        "doctor": doctor_report(settings, applet=applet),
        "inputs": source_payload,
        "models": list_models(),
        "recent_transcripts": transcript_entries,
    }


def command_settings_export(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    path = Path(args.output).expanduser() if args.output else default_settings_export_file()
    try:
        settings = json.loads(args.settings_json or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"settings JSON could not be parsed: {exc}") from exc
    if not isinstance(settings, dict):
        raise RuntimeError("settings JSON must be an object")
    payload = write_export(path, settings)
    return {
        "status": "done",
        "message": f"settings exported to {path}",
        "path": str(path),
        "settings_count": len(payload["settings"]),
    }


def command_settings_import(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    path = Path(args.input).expanduser() if args.input else default_settings_export_file()
    payload = read_export(path)
    return {
        "status": "done",
        "message": f"settings imported from {path}",
        "path": str(path),
        "settings": payload["settings"],
        "settings_count": len(payload["settings"]),
        "export_version": payload["version"],
    }


def command_insert_text(args: argparse.Namespace) -> dict[str, object]:
    text = sanitize_special_chars(args.text) if args.sanitize_special_chars else args.text
    inserted = insert_text(text, args.insert_method, args.typing_delay_ms)
    return {"status": "done", "inserted": inserted}


def command_transcribe_file(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    audio_path = Path(args.audio_path).expanduser()
    text_path = transcript_dir() / f"{audio_path.stem}.txt"
    text = transcribe(
        audio_path=audio_path,
        language=args.language,
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
    text_path.write_text(text.strip() + "\n", encoding="utf-8")
    return {"status": "done", "transcript": text, "transcript_path": str(text_path)}


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", default=str(default_state_file()))
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def add_pipeline_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", default="en")
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
