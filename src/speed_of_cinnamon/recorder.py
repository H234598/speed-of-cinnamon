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


@dataclass(frozen=True)
class InputSource:
    id: str
    name: str
    description: str
    driver: str = ""
    state: str = ""
    default: bool = False
    monitor: bool = False


def normalize_input_device(value: str) -> str:
    device = (value or "").strip()
    if device.lower() in {"", "auto", "default", "@default_source@"}:
        return ""
    return device


def choose_recorder(preference: str, audio_path: Path, max_seconds: int, input_device: str = "") -> RecorderCommand:
    preference = (preference or "auto").strip().lower()
    target = normalize_input_device(input_device)
    candidates = [preference] if preference != "auto" else ["pw-record", "parecord", "arecord"]
    for candidate in candidates:
        if candidate == "pw-record" and shutil.which("pw-record"):
            argv = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
            if target:
                argv.extend(["--target", target])
            if max_seconds > 0:
                argv.extend(["--sample-count", str(16000 * max_seconds)])
            argv.append(str(audio_path))
            return RecorderCommand(candidate, argv)
        if candidate == "parecord" and shutil.which("parecord"):
            argv = ["parecord", "--file-format=wav", "--rate=16000", "--channels=1", str(audio_path)]
            if target:
                argv.insert(-1, f"--device={target}")
            return RecorderCommand(candidate, argv)
        if candidate == "arecord" and shutil.which("arecord"):
            argv = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1"]
            if target:
                argv.extend(["--device", target])
            if max_seconds > 0:
                argv.extend(["-d", str(max_seconds)])
            argv.append(str(audio_path))
            return RecorderCommand(candidate, argv)
    raise RecorderError("no supported recorder found; install pipewire-utils or alsa-utils")


def parse_pactl_sources(text: str, default_source: str = "", include_monitors: bool = False) -> list[InputSource]:
    sources: list[InputSource] = []
    current: dict[str, str] | None = None

    def finish() -> None:
        if not current or not current.get("name"):
            return
        name = current["name"]
        monitor = current.get("monitor_of_sink", "n/a") != "n/a" or name.endswith(".monitor")
        if monitor and not include_monitors:
            return
        sources.append(
            InputSource(
                id=current.get("id", ""),
                name=name,
                description=current.get("description", name),
                driver=current.get("driver", ""),
                state=current.get("state", ""),
                default=name == default_source,
                monitor=monitor,
            )
        )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Source #"):
            finish()
            current = {"id": line.removeprefix("Source #").strip()}
            continue
        if current is None or ": " not in line:
            continue
        key, value = line.split(": ", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        if normalized_key in {"state", "name", "description", "driver", "monitor_of_sink"}:
            current[normalized_key] = value.strip()
    finish()
    return sources


def list_input_sources(include_monitors: bool = False) -> list[InputSource]:
    if not shutil.which("pactl"):
        raise RecorderError("pactl is required to list input sources; install pulseaudio-utils")

    default_proc = subprocess.run(["pactl", "get-default-source"], text=True, capture_output=True, timeout=10)
    default_source = default_proc.stdout.strip() if default_proc.returncode == 0 else ""
    proc = subprocess.run(["pactl", "list", "sources"], text=True, capture_output=True, timeout=10)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "pactl list sources failed"
        raise RecorderError(detail)
    return parse_pactl_sources(proc.stdout, default_source, include_monitors)


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
