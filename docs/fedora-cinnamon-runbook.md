# Speed of Cinnamon Fedora Cinnamon Runbook

Repository: <https://github.com/H234598/speed-of-cinnamon>

## Scope

Speed of Cinnamon is a Cinnamon-native voice typing applet plus local Python backend. It intentionally does not use the
Speed of Sound XDG portal core path. Speed of Sound remains the workflow and feature reference: shortcut, record,
transcribe, insert text.

## Install

```bash
git clone https://github.com/H234598/speed-of-cinnamon.git
cd speed-of-cinnamon
./scripts/install-fedora-deps.sh
make install-local
```

Then add `Speed of Cinnamon` from Cinnamon's applet settings. If needed, reload Cinnamon with `Alt+F2`, `r`, `Enter`.

## Runtime Paths

```text
Applet:      ~/.local/share/cinnamon/applets/speed-of-cinnamon@H234598/
Backend:     ~/.local/bin/speed-of-cinnamon
State:       ~/.local/state/speed-of-cinnamon/state.json
Transcripts: ~/.local/state/speed-of-cinnamon/transcripts/
Recordings:  ~/.cache/speed-of-cinnamon/recordings/
```

## Validation

```bash
make check
make smoke-backend
~/.local/bin/speed-of-cinnamon doctor --json
```

The backend smoke records one second of audio through `pw-record`, uses a harmless dummy transcriber, and disables
insertion.

## Dependencies

Required for the intended Fedora Cinnamon X11 path:

```bash
sudo dnf install -y python3 pipewire-utils xdotool libnotify
```

`xdotool` is needed only for automatic paste or direct typing. The applet uses Cinnamon's own clipboard API for copying
the transcript. `xclip` is optional for standalone CLI clipboard insertion outside the applet.

## ASR Configuration

Set the applet's `Transcriber command template` to an installed ASR command. Placeholders:

```text
{audio} {language} {text} {output_dir} {output_base}
```

Examples:

```text
whisper {audio} --language {language} --output_format txt --output_dir {output_dir}
whisper-cli -m ~/.local/share/whisper/models/ggml-base.bin -f {audio} -l {language} -otxt -of {output_base} && cat {text}
```

## Current Known Limits

- The first version is intentionally local-first and does not bundle an ASR model.
- Automatic paste/direct typing needs `xdotool` on X11.
- The applet is designed for Cinnamon 6.x style settings and keybinding APIs.

