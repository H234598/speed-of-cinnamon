# Speed of Cinnamon

Cinnamon-native voice typing for current Fedora Cinnamon sessions.

This project uses Speed of Sound as a product reference, not as a blind fork. The useful idea is the workflow:
press a shortcut, speak, transcribe, then put the result into the focused application. The implementation is
Cinnamon-specific: a Cinnamon applet owns the panel UI and global hotkey, and a small Python backend records audio,
runs a configurable transcriber, and inserts text through X11 clipboard/keyboard tools. There is no XDG portal core
path.

## Features

- Cinnamon panel applet with microphone icon, concise status label, status menu, transcript preview, and a doctor check.
- Status-colored Cinnamon panel indicator for recording, processing, ready, setup, recorded, and error states.
- Configuration-aware doctor check for the selected recorder, ASR backend, desktop session, and output mode.
- Startup setup check in the applet, plus direct menu actions for Cinnamon applet settings and the setup runbook.
- Applet guide/folder actions use Cinnamon's GJS/Gio default-app launcher instead of shelling out to `xdg-open`.
- Live recording progress in the panel tooltip and menu, with a compact elapsed-time panel label.
- Copyable or saveable diagnostics bundle for support reports without transcript contents.
- Cinnamon global hotkey via `Main.keybindingManager`; default is `Super+Z`, with optional dedicated shortcuts for
  starting dictation in the primary or secondary language.
- Cinnamon desktop notifications for completion and failures, with optional recording-state notifications.
- Primary/secondary recognition languages with quick switching and language-specific start actions from the applet menu.
- PipeWire/PulseAudio/ALSA recording through `pw-record`, `parecord`, or `arecord`.
- Optional microphone/source selection from the applet menu or by PipeWire/Pulse source name, with a `list-inputs`
  helper.
- ASR presets for Automatic, OpenAI Whisper command, whisper.cpp with a model path, or a custom command template.
- Local whisper.cpp model catalog with checksum-verified downloads into the user's XDG data directory.
- Personal context and custom vocabulary fields for local ASR/post-process command wrappers.
- Optional text polishing through a custom command, a local Ollama model, or a local OpenAI-compatible server such as
  vLLM, llama.cpp, or LM Studio, with applet selection of discovered local models.
- Cinnamon clipboard output through the applet, optional `xdotool` paste/direct typing, clipboard-only, or no insertion.
- Quick output-mode switcher in the applet for clipboard+paste, clipboard-only, direct typing, or no insertion.
- Target-window restore before Cinnamon clipboard paste, so panel-triggered dictation can return focus to the last
  normal application window.
- Optional accent/special-character fallback for direct typing compatibility on X11.
- Applet menu action to copy the last transcript again.
- Applet menu action to insert the last transcript again with the current output mode.
- Applet and CLI transcript history for quickly copying recent results.
- Applet and CLI action to cancel and discard a current recording.
- Applet and CLI cleanup for old transcript/history files and cached recordings.
- Applet and CLI settings export/import for portable Cinnamon backups.
- Applet recordings that hit the configured maximum length are transcribed automatically, with a setting to keep the
  old "ready, then transcribe on next shortcut" behavior.
- Temporary recording files are discarded after successful transcription by default, with an opt-in setting to keep
  them for debugging.
- Per-user runtime state under `~/.local/state/speed-of-cinnamon/` and temporary recordings under
  `~/.cache/speed-of-cinnamon/`.
- Local install and uninstall scripts, reproducible source release archives, Python unit tests, shell checks, and GitHub
  Actions CI.
- CI authorship guard for the GitHub owner, commit identities, applet metadata, Python metadata, and RPM metadata.
- CI uploads source archive, checksum, noarch RPM, and source RPM artifacts for each successful workflow run.
- Experimental noarch RPM build for Fedora-style system installation.

## Fedora Cinnamon Dependencies

Required for the intended Cinnamon/X11 path:

```bash
sudo dnf install python3 pipewire-utils pulseaudio-utils xdotool libnotify
```

`pulseaudio-utils` provides `pactl` for source discovery and `parecord` as a fallback recorder. Install `alsa-utils`
only if you want the `arecord` fallback recorder. The applet uses Cinnamon's own clipboard API, so `xclip` is not
required for normal panel usage. Install `xclip` or `xsel` only if you want to use `speed-of-cinnamon` as a standalone
CLI clipboard inserter outside Cinnamon.
If `xdotool` is missing, the applet still copies transcripts through Cinnamon and reports that automatic paste is
unavailable.

`parecord` and `arecord` are supported fallback recorders when installed. For transcription, install one local ASR
backend and choose it in the applet settings:

- `Automatic`: uses a custom command when configured, otherwise `whisper`, otherwise whisper.cpp when a model path is set.
- `OpenAI Whisper command`: runs the installed `whisper` CLI.
- `whisper.cpp`: runs `whisper-cli` with the configured model file.
- `Custom command`: runs the command template below.

Custom command examples:

```text
printf 'test transcript'
whisper {audio} --language {language} --output_format txt --output_dir {output_dir}
whisper-cli -m ~/.local/share/whisper/models/ggml-base.bin -f {audio} -l {language} -otxt -of {output_base} && cat {text}
```

Template placeholders are:

```text
{audio} {language} {text} {output_dir} {output_base}
```

Personalization is local-only. Configure `Personal context` and `Custom vocabulary` in the applet settings, or pass
them through the CLI. Custom transcriber and custom post-process commands can use these shell-quoted placeholders;
Ollama text polishing receives the same context and vocabulary in its generated prompt:

```text
{context} {vocabulary} {prompt}
```

The same values are exposed as environment variables:

```text
SPEED_OF_CINNAMON_CONTEXT
SPEED_OF_CINNAMON_VOCABULARY
SPEED_OF_CINNAMON_PROMPT
```

For text cleanup after ASR, configure `Text polishing`. `Custom command` receives the transcript on stdin and must print
the final text. `{text}`, `{language}`, `{context}`, `{vocabulary}`, and `{prompt}` can also be used as shell-quoted
placeholders. `Ollama local model` sends the transcript, language, context, and vocabulary to a local Ollama
`/api/generate` endpoint and expects the final text in the `response` field. `OpenAI-compatible local server` sends a
chat-completions request to a local `/v1/chat/completions` API such as vLLM, llama.cpp, or LM Studio. If your local
server requires a bearer token, set `SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY` in the environment that starts the
backend.

## Install Locally

```bash
make install-local
```

Then add `Speed of Cinnamon` from Cinnamon's applet settings. If Cinnamon does not refresh the applet list immediately,
reload Cinnamon with:

```text
Alt+F2, r, Enter
```

The backend command is installed to:

```text
~/.local/bin/speed-of-cinnamon
```

When the `Backend command` setting is empty, the applet auto-detects the backend in this order:
`~/.local/bin/speed-of-cinnamon`, `/usr/bin/speed-of-cinnamon`, then `speed-of-cinnamon` from `PATH`. This keeps the
per-user development install and the Fedora RPM/system install usable without editing the setting.

On startup, the applet runs a lightweight doctor check against its current Cinnamon settings. A `SET` panel label means
the configured pipeline needs setup, usually because no ASR backend or local voice model is available yet. Use the
applet menu's `Copy setup plan`, `Open applet settings`, `Open setup guide`, `Run doctor`, and `Voice model` actions to
finish setup without leaving Cinnamon's applet workflow.

## CLI

```bash
speed-of-cinnamon doctor --json
speed-of-cinnamon doctor --applet --settings-json '{"transcriber":"command","transcriber-command":"printf ok","insert-method":"clipboard-paste"}' --json
speed-of-cinnamon setup --applet --settings-json '{"transcriber":"auto","insert-method":"clipboard-paste"}' --json
speed-of-cinnamon diagnostics --json
speed-of-cinnamon diagnostics --save --json
speed-of-cinnamon diagnostics --applet --settings-json '{"transcriber":"command","transcriber-command":"printf ok"}' --json
speed-of-cinnamon list-inputs --json
speed-of-cinnamon models --json
speed-of-cinnamon text-models --json
speed-of-cinnamon text-models --backend openai-compatible --openai-compatible-url http://127.0.0.1:8000/v1 --json
speed-of-cinnamon download-model tiny.en --json
speed-of-cinnamon remove-model tiny.en --json
speed-of-cinnamon history --limit 5 --json
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
speed-of-cinnamon settings-export --settings-json '{"language":"de","append-space":true}' --json
speed-of-cinnamon settings-import --json
speed-of-cinnamon start --language de
speed-of-cinnamon start --language de --input-device alsa_input.usb-Creative_Technology_Ltd_Sound_BlasterX_G6_8400614358X-00.analog-stereo
speed-of-cinnamon stop --language de --insert-method clipboard-paste
speed-of-cinnamon cancel
speed-of-cinnamon toggle --language de --transcriber whisper
speed-of-cinnamon toggle --language de --transcriber whisper-cpp --whisper-model ~/.local/share/whisper/models/ggml-base.bin
speed-of-cinnamon toggle --language de --transcriber command --transcriber-command "printf 'Hallo Cinnamon'"
speed-of-cinnamon toggle --post-process-command "python3 -c 'import sys; print(sys.stdin.read().strip().capitalize())'"
speed-of-cinnamon toggle --post-process-backend ollama --ollama-model llama3.2:3b
speed-of-cinnamon toggle --post-process-backend openai-compatible --openai-compatible-model local-llama --openai-compatible-url http://127.0.0.1:8000/v1
speed-of-cinnamon toggle --personal-context "Use Fedora Cinnamon project terms." --vocabulary "PipeWire"
speed-of-cinnamon toggle --sanitize-special-chars --insert-method type
speed-of-cinnamon toggle --keep-recording-artifacts --insert-method none
```

The applet menu can export and import its current settings to:

```text
~/.local/share/speed-of-cinnamon/settings-export.json
```

This export includes personal context, vocabulary, command templates, hotkeys, and recording retention. Treat it as a
private backup. Machine-local `cli-path` is intentionally not exported.

The applet's `Voice model` menu can download, select, and remove whisper.cpp catalog models. Models are stored under:

```text
~/.local/share/speed-of-cinnamon/models/whisper.cpp/
```

Downloads use the upstream whisper.cpp ggml model files from Hugging Face and verify their SHA-1 checksums before the
model is activated. If `whisper-cli` is installed, Automatic transcription can use a verified downloaded model even when
the `whisper.cpp model` setting is empty. Removing a model from the applet clears the explicit whisper.cpp selection
when that model was active.

The applet's `Text model` menu can disable polishing, use the custom command backend, select a model returned by a local
Ollama `/api/tags` endpoint, or select a model returned by a local OpenAI-compatible `/v1/models` endpoint. If the
selected local server is not running, the menu stays usable and shows the local connection message instead of blocking
recording.

For backend-only testing without touching the focused application:

```bash
speed-of-cinnamon toggle --insert-method none --transcriber command --transcriber-command "printf 'test'"
```

## Development

```bash
make check
```

The checks run Python unit tests, compile Python files, validate Cinnamon JSON metadata/settings, verify project
authorship metadata, and run a backend doctor smoke check. CI runs the same checks plus `shellcheck`.

For a live backend check in a Cinnamon session:

```bash
make smoke-backend
```

This records short audio samples, uses harmless dummy transcriber commands, disables insertion, and verifies manual stop,
auto-expired recording finalization, and cancel/discard behavior.

To build and verify a release archive:

```bash
make dist-check
```

This writes `dist/speed-of-cinnamon-<version>.tar.gz`, verifies its checksum file, extracts it, runs `make check`, and
installs the package into a temporary home directory to prove the shipped applet and backend wrapper are complete.

To build a noarch RPM from the verified source archive:

```bash
make rpm
make rpm-check
```

The RPM installs the backend command to `/usr/bin/speed-of-cinnamon` and the Cinnamon applet under
`/usr/share/cinnamon/applets/speed-of-cinnamon@H234598/`. `make rpm-check` extracts the built RPM, verifies the payload
paths and metadata, then starts the packaged `/usr/bin/speed-of-cinnamon` wrapper against the extracted Python package.

GitHub Actions publishes two artifacts for successful runs:

- `speed-of-cinnamon-source-<commit>` with the source archive and `.sha256`.
- `speed-of-cinnamon-rpm-<commit>` with the noarch RPM and source RPM.

Pushing a version tag that matches `pyproject.toml`, for example `v0.1.0`, runs the release workflow. It repeats the
normal checks, verifies the source archive and RPM payload, then publishes a GitHub Release with the source archive,
checksum, Fedora noarch RPM, and source RPM:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The same workflow can be run manually with `dry_run=true` to validate the release path without publishing.

## Architecture

```text
Cinnamon keybinding / panel click
  -> files/speed-of-cinnamon@H234598/applet.js
  -> ~/.local/bin/speed-of-cinnamon or /usr/bin/speed-of-cinnamon
  -> Python backend:
       recorder.py      pw-record / parecord / arecord and pactl source discovery
       transcriber.py   ASR preset resolver and command runners
       personalization.py context/vocabulary prompt and environment helpers
       postprocessor.py optional command, Ollama, or OpenAI-compatible local text polishing
       settings_export.py portable settings snapshot helpers
       cli.py           transcript history and state commands
       output.py        xclip / xdotool for standalone CLI output
       state.py         JSON state for applet status
```

The applet asks the backend to return text without inserting it, then handles the selected output mode itself. Normal
clipboard output goes through Cinnamon's `St.Clipboard`; the applet menu exposes the output mode, remembers the last
focused normal window, restores that window before paste or direct typing, can reinsert the last transcript with the
current output mode, and opens guide/folder actions through GJS/Gio's default-app launcher. It uses `xdotool` only for
the X11 paste or direct-typing keystrokes when they are available. Without `xdotool`, dictation still completes as a
Cinnamon clipboard copy. This keeps desktop integration in Cinnamon where it belongs and keeps ASR replaceable. The
Speed of Sound JVM/GTK portal stack is intentionally not reused because its central integration point is the part that
does not fit this goal.

The doctor command accepts the applet settings as JSON and evaluates the configured pipeline, not only installed
binaries. The applet passes `--applet`, which tells the doctor to evaluate Cinnamon's own clipboard path. A missing ASR
backend is reported as not ready, while a missing `xdotool` with applet clipboard-paste mode is a warning because the
applet can still copy through Cinnamon's clipboard.

Diagnostics accepts the same settings flags and embeds only the derived doctor status, not the private settings or
command template contents.

The `Replace accents before output` setting is an optional compatibility fallback for direct typing. It maps common
diacritics to ASCII before output while leaving the saved transcript unchanged.

Desktop notifications are emitted by the Cinnamon applet through Cinnamon's `Main.notify`/`Main.criticalNotify` APIs,
not by the backend. This keeps normal CLI runs quiet and avoids depending on the XDG portal notification path.

When a live applet recording reaches `Maximum recording length`, the backend preserves the audio as `recorded` until it
is transcribed, and the applet starts transcription once it observes that state. Disable `Transcribe automatically at
the time limit` to keep the preserved recording at `RDY` until the next shortcut press. After successful
transcription, the temporary WAV/log files are deleted unless `Keep recording files after transcription` is enabled.
