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

## Transcript History

Each completed transcription is saved as a text file under the transcript directory. Inspect the recent history with:

```bash
speed-of-cinnamon history --limit 5 --json
```

The Cinnamon applet exposes the same recent entries under `Recent transcripts`; selecting one copies it through
Cinnamon's clipboard API.

## Cleanup

Old transcript files and cached recordings can be pruned without touching the currently referenced state files:

```bash
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --json
```

The Cinnamon applet exposes the same conservative cleanup as `Clean old files`. Recordings and their companion logs are
handled as one cache group, and the current `state.json` audio/log/transcript paths are skipped.

## Settings Backup

Use the applet menu actions `Export settings` and `Import settings` to save or restore the portable Cinnamon settings
snapshot:

```text
~/.local/share/speed-of-cinnamon/settings-export.json
```

The same file can be imported from the CLI, and scripts can export an explicit settings object:

```bash
speed-of-cinnamon settings-export --settings-json '{"language":"de","append-space":true}' --json
speed-of-cinnamon settings-import --json
```

The export includes the hotkey, languages, recorder/backend choices, command templates, personalization, output mode,
and notification settings. It intentionally excludes the machine-local `cli-path`. Because command templates and
personal context may contain private data, keep the export file private.

## Language Switching

Configure `Primary recognition language` and `Secondary recognition language` in the applet settings. The `Language`
menu item switches the active runtime language for future recordings. The shortcut and panel action then pass that
active language to the backend as `--language`.

## Personalization

Speed of Cinnamon keeps personalization simple and local for Cinnamon: configure `Personal context` and `Custom
vocabulary` in the applet settings, or pass them through CLI flags:

```bash
speed-of-cinnamon toggle \
  --personal-context "Use Fedora Cinnamon project terms." \
  --vocabulary "PipeWire"
```

Custom transcriber and post-process commands can read the values from placeholders:

```text
{context} {vocabulary} {prompt}
```

They are also available as environment variables:

```text
SPEED_OF_CINNAMON_CONTEXT
SPEED_OF_CINNAMON_VOCABULARY
SPEED_OF_CINNAMON_PROMPT
```

The built-in `diagnostics` output does not include these values.

## Notifications

The Cinnamon applet can notify when dictation completes or fails. Completion and error notifications are enabled by
default; recording-start and time-limit notifications can be enabled separately in the applet settings.

Notifications are sent through Cinnamon's own `Main.notify` and `Main.criticalNotify` APIs. The backend does not emit
notifications by itself, so CLI smoke tests and scripted runs stay quiet.

## Direct Typing Compatibility

For direct typing through `xdotool`, enable `Replace accents before output` in the applet settings or pass:

```bash
speed-of-cinnamon toggle --sanitize-special-chars --insert-method type
```

This maps common accented and special characters to ASCII before output. Saved transcripts stay unchanged. Keep this
disabled for normal Cinnamon clipboard output unless a target application cannot handle accented characters.

## Validation

```bash
make check
make smoke-backend
~/.local/bin/speed-of-cinnamon doctor --json
```

The backend smoke records short audio samples through `pw-record`, uses harmless dummy transcribers, disables insertion,
and verifies manual stop, cancel/discard, and the preserved-audio path used when a recording expires at its maximum
length.

## Diagnostics

For support reports, run:

```bash
speed-of-cinnamon diagnostics --json
```

The Cinnamon applet also has `Copy diagnostics`. The bundle includes app/runtime paths, desktop/session details,
doctor checks, input-source metadata, and state. It intentionally omits transcript contents.

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

The default PipeWire/Pulse input is used when `Input device` is empty. In the Cinnamon applet, open `Input source` and
select either `System default` or a concrete source. For scripts or manual settings, inspect the source names:

```bash
speed-of-cinnamon list-inputs --json
```

Use the `name` value, not the localized description:

```bash
speed-of-cinnamon start --input-device alsa_input.usb-Creative_Technology_Ltd_Sound_BlasterX_G6_8400614358X-00.analog-stereo
```

The same value can be stored in the applet's `Input device` setting or selected from the applet menu. It is passed to
`pw-record --target`, `parecord --device`, or `arecord --device`, depending on the selected recorder.

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

If a recording reaches the configured maximum length, the backend preserves the audio and reports `recorded`. The
Cinnamon applet polls while recording; by default, it immediately starts transcription once it sees that `recorded`
state. Disable `Transcribe automatically at the time limit` in the applet settings to keep the recording at `RDY` and
transcribe it on the next shortcut press instead.

Use `speed-of-cinnamon cancel` or the applet's `Cancel recording` menu item to discard the current recording without
transcription. This stops a live recorder process when needed and removes the temporary audio/log files.
