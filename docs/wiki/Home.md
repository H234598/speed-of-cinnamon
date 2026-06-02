# Speed of Cinnamon

Cinnamon-native voice typing for Fedora Cinnamon.

Speed of Cinnamon keeps the useful workflow from Speed of Sound: press a shortcut, speak, transcribe, and insert the
result into the focused application. The implementation is Cinnamon-specific. The applet owns the panel UI, global
hotkeys, clipboard handling, notifications, and target-window restore; the Python backend records audio and runs the
selected local pipeline.

## Start Here

- [[User Guide]] covers normal applet usage.
- [[CLI Reference]] lists backend commands and examples.
- [[Architecture]] explains the Cinnamon-native boundary and why there is no portal core path.
- [[Development]] covers tests, coverage, archives, RPMs, CI, and releases.
- [[Fedora Cinnamon Runbook]] is the operational Fedora setup and troubleshooting guide.

## Quick Install

```bash
git clone https://github.com/H234598/speed-of-cinnamon.git
cd speed-of-cinnamon
./scripts/install-fedora-deps.sh
make install-local
```

Then add `Speed of Cinnamon` from Cinnamon's applet settings.

After updating an installed checkout, `make install-local` reloads the applet through Cinnamon when a session bus is
available. The applet menu also includes `Restart applet` for stale UI or settings state.

Use `make uninstall-local` to remove the local applet, backend wrapper, installed Python package, and man pages. It
preserves user data such as downloaded models, alarms, transcripts, diagnostics, and settings exports.

## Current Runtime Paths

```text
Applet:      ~/.local/share/cinnamon/applets/speed-of-cinnamon@H234598/
Backend:     ~/.local/bin/speed-of-cinnamon or /usr/bin/speed-of-cinnamon
State:       ~/.local/state/speed-of-cinnamon/state.json
Transcripts: ~/.local/state/speed-of-cinnamon/transcripts/
Recordings:  ~/.cache/speed-of-cinnamon/recordings/
Models:      ~/.local/share/speed-of-cinnamon/models/whisper.cpp/
Alarms:      ~/.local/share/speed-of-cinnamon/alarms.json
```
