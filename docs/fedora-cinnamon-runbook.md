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

The backend smoke records short audio samples through `pw-record`, uses harmless dummy transcribers, disables insertion,
and verifies manual stop, cancel/discard, and the path where a recording expires at its maximum length before the next
shortcut.

## Dependencies

Required for the intended Fedora Cinnamon X11 path:

```bash
sudo dnf install -y python3 pipewire-utils xdotool libnotify
```

Install `pulseaudio-utils` as well for `pactl` input source discovery and the `parecord` fallback recorder:

```bash
sudo dnf install -y pulseaudio-utils
```

`xdotool` is needed only for automatic paste or direct typing. The applet uses Cinnamon's own clipboard API for copying
the transcript, and falls back to copy-only when automatic paste is not available. `xclip` is optional for standalone CLI
clipboard insertion outside the applet.

## Input Source Selection

The default PipeWire/Pulse input is used when `Input device` is empty. To pin a microphone, inspect the source names:

```bash
speed-of-cinnamon list-inputs --json
```

Use the `name` value, not the localized description:

```bash
speed-of-cinnamon start --input-device alsa_input.usb-Creative_Technology_Ltd_Sound_BlasterX_G6_8400614358X-00.analog-stereo
```

The same value can be stored in the applet's `Input device` setting. It is passed to `pw-record --target`,
`parecord --device`, or `arecord --device`, depending on the selected recorder.

## ASR Configuration

Choose one transcriber in the applet settings:

- `Automatic`: uses a custom command when configured, otherwise the installed `whisper` command, otherwise whisper.cpp
  when a model path is set.
- `OpenAI Whisper command`: runs `whisper` and writes the transcript into the runtime transcript directory.
- `whisper.cpp`: runs `whisper-cli`; set `whisper.cpp model` to a local model file such as `ggml-base.bin`.
- `Custom command`: runs `Transcriber command template`.

Custom template placeholders:

```text
{audio} {language} {text} {output_dir} {output_base}
```

Examples:

```text
whisper {audio} --language {language} --output_format txt --output_dir {output_dir}
whisper-cli -m ~/.local/share/whisper/models/ggml-base.bin -f {audio} -l {language} -otxt -of {output_base} && cat {text}
```

Equivalent CLI examples:

```bash
speed-of-cinnamon toggle --language de --transcriber whisper
speed-of-cinnamon toggle --language de --transcriber whisper-cpp --whisper-model ~/.local/share/whisper/models/ggml-base.bin
speed-of-cinnamon toggle --language de --transcriber command --transcriber-command "printf 'test transcript'"
```

## Text Polishing

Speed of Cinnamon supports a small Cinnamon-friendly equivalent of Speed of Sound's optional text-model polishing:
configure `Post-process command`. The command receives the raw transcript on stdin and must print the final text.

Example:

```bash
speed-of-cinnamon toggle \
  --post-process-command "python3 -c 'import sys; print(sys.stdin.read().strip().capitalize())'"
```

For local LLM tooling, point this setting at a wrapper script that reads stdin and prints only the polished text. The
backend stores and inserts the post-processed text.

## Current Known Limits

- The app is intentionally local-first and does not bundle an ASR model.
- Automatic paste/direct typing needs `xdotool` on X11.
- The applet can copy the last transcript again from its menu.
- The applet is designed for Cinnamon 6.x style settings and keybinding APIs.

## Timeout Behavior

If a recording reaches the configured maximum length, the backend preserves the audio and reports `recorded`. The next
toggle/shortcut transcribes that existing recording instead of starting a new one. The applet polls while recording so the
panel state can move from `REC` to `RDY` after the recorder exits.

Use `speed-of-cinnamon cancel` or the applet's `Cancel recording` menu item to discard the current recording without
transcription. This stops a live recorder process when needed and removes the temporary audio/log files.
