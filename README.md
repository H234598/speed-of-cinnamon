# Speed of Cinnamon

Cinnamon-native voice typing for current Fedora Cinnamon sessions.

This project uses Speed of Sound as a product reference, not as a blind fork. The useful idea is the workflow:
press a shortcut, speak, transcribe, then put the result into the focused application. The implementation is
Cinnamon-specific: a Cinnamon applet owns the panel UI and global hotkey, and a small Python backend records audio,
runs a configurable transcriber, and inserts text through X11 clipboard/keyboard tools. There is no XDG portal core
path.

## Features

- Cinnamon panel applet with microphone icon, concise status label, status menu, transcript preview, and a doctor check.
- Copyable diagnostics bundle for support reports without transcript contents.
- Cinnamon global hotkey via `Main.keybindingManager`; default is `Super+Z`.
- Cinnamon desktop notifications for completion and failures, with optional recording-state notifications.
- Primary/secondary recognition languages with quick switching from the applet menu.
- PipeWire/PulseAudio/ALSA recording through `pw-record`, `parecord`, or `arecord`.
- Optional microphone/source selection by PipeWire/Pulse source name, with a `list-inputs` helper.
- ASR presets for Automatic, OpenAI Whisper command, whisper.cpp with a model path, or a custom command template.
- Personal context and custom vocabulary fields for local ASR/post-process command wrappers.
- Optional post-process command for local text cleanup or LLM polishing.
- Cinnamon clipboard output through the applet, optional `xdotool` paste/direct typing, clipboard-only, or no insertion.
- Optional accent/special-character fallback for direct typing compatibility on X11.
- Applet menu action to copy the last transcript again.
- Applet and CLI transcript history for quickly copying recent results.
- Applet and CLI action to cancel and discard a current recording.
- Applet and CLI cleanup for old transcript/history files and cached recordings.
- Applet and CLI settings export/import for portable Cinnamon backups.
- Applet recordings that hit the configured maximum length are transcribed automatically, with a setting to keep the
  old "ready, then transcribe on next shortcut" behavior.
- Per-user runtime state under `~/.local/state/speed-of-cinnamon/` and temporary recordings under
  `~/.cache/speed-of-cinnamon/`.
- Local install and uninstall scripts, Python unit tests, shell checks, and GitHub Actions CI.

## Fedora Cinnamon Dependencies

Required for the intended Cinnamon/X11 path:

```bash
sudo dnf install python3 pipewire-utils pulseaudio-utils xdotool libnotify
```

`pulseaudio-utils` provides `pactl` for source discovery and `parecord` as a fallback recorder. The applet uses
Cinnamon's own clipboard API, so `xclip` is not required for normal panel usage. Install `xclip` or `xsel` only if you
want to use `speed-of-cinnamon` as a standalone CLI clipboard inserter outside Cinnamon.
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

Personalization is local-only and command-driven. Configure `Personal context` and `Custom vocabulary` in the applet
settings, or pass them through the CLI. Custom transcriber and post-process commands can use these shell-quoted
placeholders:

```text
{context} {vocabulary} {prompt}
```

The same values are exposed as environment variables:

```text
SPEED_OF_CINNAMON_CONTEXT
SPEED_OF_CINNAMON_VOCABULARY
SPEED_OF_CINNAMON_PROMPT
```

For text cleanup after ASR, configure `Post-process command`. The transcript is passed on stdin, and the command must
print the final text. `{text}`, `{language}`, `{context}`, `{vocabulary}`, and `{prompt}` can also be used as
shell-quoted placeholders.

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

## CLI

```bash
speed-of-cinnamon doctor --json
speed-of-cinnamon diagnostics --json
speed-of-cinnamon list-inputs --json
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
speed-of-cinnamon toggle --personal-context "Use Fedora Cinnamon project terms." --vocabulary "PipeWire"
speed-of-cinnamon toggle --sanitize-special-chars --insert-method type
```

The applet menu can export and import its current settings to:

```text
~/.local/share/speed-of-cinnamon/settings-export.json
```

This export includes personal context, vocabulary, command templates, and the hotkey. Treat it as a private backup.
Machine-local `cli-path` is intentionally not exported.

For backend-only testing without touching the focused application:

```bash
speed-of-cinnamon toggle --insert-method none --transcriber command --transcriber-command "printf 'test'"
```

## Development

```bash
make check
```

The checks run Python unit tests, compile Python files, validate Cinnamon JSON metadata/settings, and run a backend
doctor smoke check. CI runs the same checks plus `shellcheck`.

For a live backend check in a Cinnamon session:

```bash
make smoke-backend
```

This records short audio samples, uses harmless dummy transcriber commands, disables insertion, and verifies manual stop,
auto-expired recording finalization, and cancel/discard behavior.

## Architecture

```text
Cinnamon keybinding / panel click
  -> files/speed-of-cinnamon@H234598/applet.js
  -> ~/.local/bin/speed-of-cinnamon
  -> Python backend:
       recorder.py      pw-record / parecord / arecord and pactl source discovery
       transcriber.py   ASR preset resolver and command runners
       personalization.py context/vocabulary prompt and environment helpers
       postprocessor.py optional text polishing command
       settings_export.py portable settings snapshot helpers
       cli.py           transcript history and state commands
       output.py        xclip / xdotool for standalone CLI output
       state.py         JSON state for applet status
```

The applet handles the normal clipboard path through Cinnamon's `St.Clipboard`, then uses `xdotool` only for the X11
paste keystroke when it is available. Without `xdotool`, dictation still completes as a Cinnamon clipboard copy. This
keeps desktop integration in Cinnamon where it belongs and keeps ASR replaceable. The Speed of Sound JVM/GTK portal
stack is intentionally not reused because its central integration point is the part that does not fit this goal.

The `Replace accents before output` setting is an optional compatibility fallback for direct typing. It maps common
diacritics to ASCII before output while leaving the saved transcript unchanged.

Desktop notifications are emitted by the Cinnamon applet through Cinnamon's `Main.notify`/`Main.criticalNotify` APIs,
not by the backend. This keeps normal CLI runs quiet and avoids depending on the XDG portal notification path.

When a live applet recording reaches `Maximum recording length`, the backend preserves the audio as `recorded` and the
applet starts transcription once it observes that state. Disable `Transcribe automatically at the time limit` to keep
the preserved recording at `RDY` until the next shortcut press.
