from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .doctor import report as doctor_report
from .output import insert_text
from .paths import APP_NAME, default_state_file, ensure_runtime_dirs, recordings_dir, transcript_dir
from .recorder import choose_recorder, start_recorder, stop_process
from .state import RecordingState, StateStore, now_iso, process_is_alive
from .transcriber import transcribe

RECORDER_START_GRACE_SECONDS = 0.2


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
    command = choose_recorder(args.recorder, audio_path, args.max_seconds)
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
    )
    store.write(state)
    return {
        "status": "recording",
        "message": "recording started",
        "pid": proc.pid,
        "audio_path": str(audio_path),
        "recorder": command.name,
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
        )
        text_to_insert = append_space_if_needed(text, args.append_space)
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
    return doctor_report()


def command_insert_text(args: argparse.Namespace) -> dict[str, object]:
    inserted = insert_text(args.text, args.insert_method, args.typing_delay_ms)
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
    )
    return {"status": "done", "transcript": text, "transcript_path": str(text_path)}


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", default=str(default_state_file()))
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def add_pipeline_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", default="en")
    parser.add_argument("--max-seconds", type=int, default=30)
    parser.add_argument("--recorder", default="auto", choices=["auto", "pw-record", "parecord", "arecord"])
    parser.add_argument("--transcriber", default="auto", choices=["auto", "whisper", "whisper-cpp", "command"])
    parser.add_argument("--transcriber-command", default="")
    parser.add_argument("--whisper-model", default="")
    parser.add_argument(
        "--insert-method",
        default="clipboard-paste",
        choices=["clipboard-paste", "clipboard", "type", "none"],
    )
    parser.add_argument("--typing-delay-ms", type=int, default=8)
    parser.add_argument("--append-space", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="speed-of-cinnamon")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in [("start", command_start), ("stop", command_stop), ("toggle", command_toggle)]:
        child = subparsers.add_parser(name)
        add_common_options(child)
        add_pipeline_options(child)
        child.set_defaults(handler=handler)

    status = subparsers.add_parser("status")
    add_common_options(status)
    status.set_defaults(handler=command_status)

    doctor = subparsers.add_parser("doctor")
    add_common_options(doctor)
    doctor.set_defaults(handler=command_doctor)

    insert = subparsers.add_parser("insert-text")
    add_common_options(insert)
    insert.add_argument("text")
    insert.add_argument("--insert-method", default="clipboard-paste", choices=["clipboard-paste", "clipboard", "type", "none"])
    insert.add_argument("--typing-delay-ms", type=int, default=8)
    insert.set_defaults(handler=command_insert_text)

    transcribe_file = subparsers.add_parser("transcribe-file")
    add_common_options(transcribe_file)
    transcribe_file.add_argument("audio_path")
    transcribe_file.add_argument("--language", default="en")
    transcribe_file.add_argument("--transcriber", default="auto", choices=["auto", "whisper", "whisper-cpp", "command"])
    transcribe_file.add_argument("--transcriber-command", default="")
    transcribe_file.add_argument("--whisper-model", default="")
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
