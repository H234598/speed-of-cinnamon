from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class RecorderError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecorderCommand:
    name: str
    argv: list[str]


def choose_recorder(preference: str, audio_path: Path, max_seconds: int) -> RecorderCommand:
    preference = (preference or "auto").strip().lower()
    candidates = [preference] if preference != "auto" else ["pw-record", "parecord", "arecord"]
    for candidate in candidates:
        if candidate == "pw-record" and shutil.which("pw-record"):
            argv = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
            if max_seconds > 0:
                argv.extend(["--sample-count", str(16000 * max_seconds)])
            argv.append(str(audio_path))
            return RecorderCommand(candidate, argv)
        if candidate == "parecord" and shutil.which("parecord"):
            argv = ["parecord", "--file-format=wav", "--rate=16000", "--channels=1", str(audio_path)]
            return RecorderCommand(candidate, argv)
        if candidate == "arecord" and shutil.which("arecord"):
            argv = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1"]
            if max_seconds > 0:
                argv.extend(["-d", str(max_seconds)])
            argv.append(str(audio_path))
            return RecorderCommand(candidate, argv)
    raise RecorderError("no supported recorder found; install pipewire-utils or alsa-utils")


def start_recorder(command: RecorderCommand, log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        return subprocess.Popen(command.argv, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        raise RecorderError(f"failed to start {command.name}: {exc}") from exc
    finally:
        log_file.close()


def stop_process(pid: int, timeout_seconds: float = 5.0) -> None:
    try:
        subprocess.run(["kill", "-INT", str(pid)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            subprocess.run(["kill", "-0", str(pid)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return
        time.sleep(0.1)

    try:
        subprocess.run(["kill", "-TERM", str(pid)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass

    time.sleep(0.5)
    try:
        subprocess.run(["kill", "-0", str(pid)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return

    try:
        subprocess.run(["kill", "-KILL", str(pid)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise RecorderError(f"failed to stop recorder process {pid}: {exc}") from exc
