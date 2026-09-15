"""Bounded child process for local faster-whisper inference."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path


MAX_TRANSCRIPT_CHARS = 1_000_000
LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$", re.ASCII)
RUNTIME_DIAGNOSTIC_SCHEMA_VERSION = 1
FASTER_WHISPER_REQUESTED_COMPUTE_TYPE = "int8"
FASTER_WHISPER_REQUESTED_CPU_THREADS = None
FASTER_WHISPER_REQUESTED_NUM_WORKERS = None
WORKER_ERROR_MESSAGES = {
    "load": "faster-whisper model could not be loaded",
    "inference": "faster-whisper inference failed",
    "invalid-segment": "faster-whisper returned invalid segment text",
    "empty": "faster-whisper completed without transcript",
    "too-large": "faster-whisper transcript is too large",
    "runtime": "faster-whisper worker failed",
}


class WorkerFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in WORKER_ERROR_MESSAGES:
            code = "runtime"
        self.code = code
        super().__init__(WORKER_ERROR_MESSAGES[code])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--model", required=True)
    return parser


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _path_contains_symlink(path: Path) -> bool:
    current = path
    while True:
        try:
            if current.is_symlink():
                return True
        except (OSError, ValueError, UnicodeError):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _validate_arguments(args: argparse.Namespace) -> tuple[Path, str, Path] | None:
    audio = getattr(args, "audio", None)
    language = getattr(args, "language", None)
    model = getattr(args, "model", None)
    if isinstance(audio, bool) or not isinstance(audio, str) or not audio:
        return None
    if isinstance(language, bool) or not isinstance(language, str) or not language:
        return None
    if isinstance(model, bool) or not isinstance(model, str) or not model:
        return None
    if LANGUAGE_RE.fullmatch(language) is None:
        return None
    try:
        audio_path = Path(audio)
        model_path = Path(model)
        if _path_contains_symlink(audio_path) or _path_contains_symlink(model_path):
            return None
        audio_stat = audio_path.stat(follow_symlinks=False)
        model_stat = model_path.stat(follow_symlinks=False)
    except (OSError, ValueError, UnicodeError):
        return None
    if not stat.S_ISREG(audio_stat.st_mode) or not stat.S_ISDIR(model_stat.st_mode):
        return None
    if audio_stat.st_nlink != 1:
        return None
    return audio_path, language, model_path


def _transcribe(audio_path: Path, language: str, model_path: Path) -> str:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise WorkerFailure("load") from exc
    try:
        model = WhisperModel(
            str(model_path),
            device="cpu",
            compute_type=FASTER_WHISPER_REQUESTED_COMPUTE_TYPE,
            local_files_only=True,
        )
    except Exception as exc:
        raise WorkerFailure("load") from exc
    try:
        segments, _info = model.transcribe(
            str(audio_path),
            language=language or None,
            task="transcribe",
            beam_size=5,
        )
    except Exception as exc:
        raise WorkerFailure("inference") from exc
    text_parts: list[str] = []
    transcript_chars = 0
    transcript_bytes = 0
    try:
        for segment in segments:
            raw_segment_text = getattr(segment, "text", None)
            if raw_segment_text is not None and not isinstance(raw_segment_text, str):
                raise WorkerFailure("invalid-segment")
            segment_text = (raw_segment_text or "").strip()
            if not segment_text:
                continue
            separator_chars = 1 if text_parts else 0
            transcript_chars += separator_chars + len(segment_text)
            transcript_bytes += separator_chars + len(segment_text.encode("utf-8"))
            if transcript_chars > MAX_TRANSCRIPT_CHARS or transcript_bytes > MAX_TRANSCRIPT_CHARS:
                raise WorkerFailure("too-large")
            text_parts.append(segment_text)
    except WorkerFailure:
        raise
    except Exception as exc:
        raise WorkerFailure("inference") from exc
    text = " ".join(text_parts).strip()
    if not text:
        raise WorkerFailure("empty")
    return text


def _write_failure(code: str) -> None:
    safe_code = code if code in WORKER_ERROR_MESSAGES else "runtime"
    sys.stdout.write(json.dumps({"status": "error", "error_code": safe_code}) + "\n")
    sys.stdout.flush()


def _module_version(module: object) -> str | None:
    try:
        version = getattr(module, "__version__", None)
    except Exception:
        return None
    return version if isinstance(version, str) else None


def _diagnose_runtime_payload() -> dict[str, object]:
    try:
        import ctranslate2  # type: ignore[import-not-found]
    except Exception:
        ctranslate2_available = False
        ctranslate2_version = None
        supported_compute_types = None
    else:
        ctranslate2_available = True
        ctranslate2_version = _module_version(ctranslate2)
        try:
            raw_compute_types = ctranslate2.get_supported_compute_types("cpu")
        except Exception:
            supported_compute_types = None
        else:
            if (
                isinstance(raw_compute_types, (list, tuple, set, frozenset))
                and len(raw_compute_types) <= 32
                and all(isinstance(item, str) for item in raw_compute_types)
            ):
                supported_compute_types = sorted(set(raw_compute_types))
            else:
                supported_compute_types = None

    try:
        import faster_whisper  # type: ignore[import-not-found]
    except Exception:
        faster_whisper_available = False
        faster_whisper_version = None
    else:
        faster_whisper_available = True
        faster_whisper_version = _module_version(faster_whisper)

    return {
        "schema_version": RUNTIME_DIAGNOSTIC_SCHEMA_VERSION,
        "ctranslate2": {
            "available": ctranslate2_available,
            "version": ctranslate2_version,
            "supported_compute_types": supported_compute_types,
        },
        "faster_whisper": {
            "available": faster_whisper_available,
            "version": faster_whisper_version,
        },
    }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--diagnose-runtime"]:
        sys.stdout.write(json.dumps(_diagnose_runtime_payload(), ensure_ascii=True) + "\n")
        sys.stdout.flush()
        return 0
    if "--diagnose-runtime" in arguments:
        return 2
    try:
        args = _parser().parse_args(arguments)
    except SystemExit:
        return 2
    validated = _validate_arguments(args)
    if validated is None:
        return _fail("faster-whisper worker arguments are invalid")
    try:
        audio_path, language, model_path = validated
        text = _transcribe(audio_path, language, model_path)
        sys.stdout.write(json.dumps({"status": "done", "transcript": text}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except WorkerFailure as exc:
        _write_failure(exc.code)
        return 1
    except Exception:
        _write_failure("runtime")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
