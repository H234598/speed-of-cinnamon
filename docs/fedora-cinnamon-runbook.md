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

Release archives can be built and verified from the repo:

```bash
make dist-check
```

The archive is written below `dist/` with a matching `.sha256` file. Verification extracts the archive, runs the normal
checks, and installs it into a temporary home directory to prove the shipped applet and backend wrapper are complete.

An experimental noarch RPM can also be built locally:

```bash
make rpm
make rpm-check
```

The RPM installs the backend command into `/usr/bin/speed-of-cinnamon` and the Cinnamon applet into
`/usr/share/cinnamon/applets/speed-of-cinnamon@H234598/`. It is intended for Fedora-style system installation; the
per-user `make install-local` path remains the fastest development install. The RPM check extracts the built package,
verifies package metadata and installed paths, and runs the packaged `/usr/bin/speed-of-cinnamon` wrapper against the
extracted Python package.

Successful GitHub Actions runs upload two downloadable artifacts: `speed-of-cinnamon-source-<commit>` with the source
archive and checksum, and `speed-of-cinnamon-rpm-<commit>` with the noarch RPM and source RPM.

Pushing a version tag that matches `pyproject.toml`, for example `v0.1.0`, runs the release workflow. It repeats the
normal checks and publishes a GitHub Release with the source archive, checksum, Fedora noarch RPM, and source RPM. The
same workflow has a manual `dry_run=true` path to validate release automation without publishing.

`make check` also runs `scripts/verify-authorship.sh`. That guard verifies the expected GitHub repo URL, commit
author/committer identity, applet metadata, Python project metadata, RPM spec metadata, and rejects upstream author
markers in tracked text files.

On first load, the applet runs a lightweight setup check against its current Cinnamon settings. If the panel shows
`SET`, open the applet menu and use `Copy setup plan`, `Run doctor`, `Open applet settings`, `Open setup guide`, and
`Voice model` to finish the local pipeline setup. The startup check does not use portals and does not open a separate
window.

The same setup plan is available from the CLI:

```bash
speed-of-cinnamon setup --applet \
  --settings-json '{"transcriber":"auto","insert-method":"clipboard-paste"}' \
  --json
```

## Runtime Paths

```text
Applet:      ~/.local/share/cinnamon/applets/speed-of-cinnamon@H234598/
Backend:     ~/.local/bin/speed-of-cinnamon or /usr/bin/speed-of-cinnamon
State:       ~/.local/state/speed-of-cinnamon/state.json
Transcripts: ~/.local/state/speed-of-cinnamon/transcripts/
Recordings:  ~/.cache/speed-of-cinnamon/recordings/
Models:      ~/.local/share/speed-of-cinnamon/models/whisper.cpp/
```

Leave the applet's `Backend command` setting empty for normal installs. The applet first uses the per-user backend
from `~/.local/bin`, then the RPM/system backend from `/usr/bin`, then `speed-of-cinnamon` from `PATH`.

Recording files are temporary runtime artifacts. By default, Speed of Cinnamon deletes the WAV file and recorder log
after a successful transcription. Enable `Keep recording files after transcription` only when you need those files for
debugging a recorder or ASR problem.

## Transcript History

Each completed transcription is saved as a text file under the transcript directory. Inspect the recent history with:

```bash
speed-of-cinnamon history --limit 5 --json
```

The Cinnamon applet exposes the same recent entries under `Recent transcripts`; selecting one copies it through
Cinnamon's clipboard API.

## Recording Progress

While recording, the applet keeps the panel label compact (`REC 12s`) and exposes the full elapsed/maximum time in the
tooltip and status menu. The panel actor also switches Cinnamon CSS status classes for recording, processing, ready,
setup, recorded, and error states, so the indicator remains scannable even without opening a separate window.

## Cleanup

Old transcript files and cached recordings can be pruned without touching the currently referenced state files:

```bash
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --json
```

The Cinnamon applet exposes the same conservative cleanup as `Clean old files`. Recordings and their companion logs are
handled as one cache group, and the current `state.json` audio/log/transcript paths are skipped. In normal use,
successful recordings have already been removed; cleanup is mainly for retained debug artifacts, cancelled runs, and
older caches.

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
recording retention, and notification settings. It intentionally excludes the machine-local `cli-path`. Because command
templates and personal context may contain private data, keep the export file private.

## Language Switching

Configure `Primary recognition language` and `Secondary recognition language` in the applet settings. The `Language`
menu item switches the active runtime language for future recordings. The shortcut and panel action then pass that
active language to the backend as `--language`.

The applet also exposes `Start primary language` and `Start secondary language` menu actions. Optional Cinnamon global
shortcuts for the same actions can be configured as `Start dictation with the primary language` and `Start dictation
with the secondary language`. If a recording is already active, those actions stop or transcribe the current recording
without changing its saved language.

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

## Voice Models

The applet's `Voice model` menu exposes a small whisper.cpp catalog. Use `Download starter model` for `tiny.en`, or open
each catalog model to download, select, or remove it. The same actions are available from the CLI:

```bash
speed-of-cinnamon models --json
speed-of-cinnamon download-model tiny.en --json
speed-of-cinnamon download-model base --json
speed-of-cinnamon remove-model tiny.en --json
```

Downloaded files are saved below:

```text
~/.local/share/speed-of-cinnamon/models/whisper.cpp/
```

Each download is written through a temporary file, verified against the upstream SHA-1, and then moved into place. Once
`whisper-cli` is installed, Automatic transcription can use a verified downloaded model even if the applet's
`whisper.cpp model` setting is empty. Selecting a downloaded model from the applet sets the transcriber to
`whisper.cpp` and stores the model path explicitly. Removing the active model from the applet clears that explicit
selection and returns the transcriber setting to `Automatic`.

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
speed-of-cinnamon doctor --json
speed-of-cinnamon doctor \
  --applet \
  --settings-json '{"transcriber":"command","transcriber-command":"printf ok","insert-method":"clipboard-paste"}' \
  --json
```

The backend smoke records short audio samples through `pw-record`, uses harmless dummy transcribers, disables insertion,
and verifies manual stop, cancel/discard, and the preserved-audio path used when a recording expires at its maximum
length.

The doctor report is configuration-aware. The Cinnamon applet passes its current settings plus `--applet`, so the report
can distinguish between the selected recorder, the selected ASR backend, and the selected output mode. Missing ASR is a
readiness failure because dictation cannot complete without a transcriber. Missing `xdotool` in applet
`clipboard-paste` mode is only a warning because Cinnamon clipboard copy still works. In pure CLI mode,
`clipboard-paste` still needs a clipboard helper and a keyboard helper.

## Diagnostics

For support reports, run:

```bash
speed-of-cinnamon diagnostics --json
speed-of-cinnamon diagnostics --save --json
speed-of-cinnamon diagnostics --applet \
  --settings-json '{"transcriber":"command","transcriber-command":"printf ok","insert-method":"clipboard-paste"}' \
  --json
```

The Cinnamon applet also has `Copy diagnostics` and `Save diagnostics`. Saved reports are written under:

```text
~/.local/state/speed-of-cinnamon/diagnostics/
```

The bundle includes app/runtime paths, desktop/session details, doctor checks, input-source metadata, and state. When
the applet creates the bundle, it passes the current settings to the doctor so the readiness section reflects the
configured pipeline. The report intentionally omits transcript contents and does not include private command templates
or personalization text. It includes non-private model catalog status so support reports can tell whether local
whisper.cpp models are present.

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
clipboard insertion outside the applet; `xsel` is supported as another X11 CLI clipboard helper. Install `alsa-utils`
only if you want the `arecord` fallback recorder.

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
configure `Text polishing`. The `Custom command` backend receives the raw transcript on stdin and must print the final
text.

Example:

```bash
speed-of-cinnamon toggle \
  --post-process-command "python3 -c 'import sys; print(sys.stdin.read().strip().capitalize())'"
```

For local LLM tooling, either point the command setting at a wrapper script that reads stdin and prints only the
polished text, use the built-in Ollama backend, or use a local OpenAI-compatible chat-completions server such as vLLM,
llama.cpp, or LM Studio:

```bash
speed-of-cinnamon toggle \
  --post-process-backend ollama \
  --ollama-url http://127.0.0.1:11434 \
  --ollama-model llama3.2:3b

speed-of-cinnamon toggle \
  --post-process-backend openai-compatible \
  --openai-compatible-url http://127.0.0.1:8000/v1 \
  --openai-compatible-model local-llama
```

The Ollama backend calls `/api/generate` with `stream=false`. It sends the transcript, language, personal context, and
vocabulary to the local server and expects the polished text in the `response` field. The backend stores and inserts the
post-processed text. The OpenAI-compatible backend calls `/v1/chat/completions` with `stream=false` and the same local
context. If your local server requires a bearer token, set `SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY` in the
environment that starts the backend.

To inspect installed local text models from the backend:

```bash
speed-of-cinnamon text-models --json
speed-of-cinnamon text-models --backend openai-compatible --openai-compatible-url http://127.0.0.1:8000/v1 --json
```

The Cinnamon applet exposes the same local lists in `Text model`. Selecting a local model switches `Text polishing` to
the matching provider and stores the chosen model name. If the selected local server is not running, the menu reports
that local status without changing the current dictation pipeline.

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
