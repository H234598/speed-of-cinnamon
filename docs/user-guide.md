# Speed of Cinnamon User Guide

This guide covers the normal Cinnamon applet workflow. For command examples, use
[CLI Reference](cli-reference.md). For design boundaries, use [Architecture](architecture.md).

## Applet Workflow

Add `Speed of Cinnamon` from Cinnamon's applet settings after installation. The panel item shows a compact microphone
status and a short state label:

- `SET`: setup is incomplete.
- `REC`: recording is active.
- `RDY`: a recording reached its limit and is waiting for transcription.
- `...`: transcription or post-processing is running.
- `OK`: the last operation completed.
- `ERR`: the last operation failed.

The default global shortcut is `Super+Z`. Optional shortcuts can start dictation directly with the configured primary
or secondary language. The applet menu's `Keyboard shortcuts` entry shows the live Cinnamon bindings and can copy them
for setup notes.

## Setup Menu

On startup, the applet runs a lightweight doctor check using its current Cinnamon settings. If the pipeline is not ready,
use:

- `Copy setup plan` for a readable checklist.
- `Copy setup commands` for only the concrete shell commands.
- `Run doctor` for the current readiness state.
- `Restart applet` to reload the Cinnamon applet after an update or stale setting state.
- `Open applet settings` for Cinnamon's settings UI.
- `Open setup guide` for the Fedora runbook.
- `Voice model` to download or select a local whisper.cpp model.

The applet does not run `sudo`, `pkexec`, package managers, or portal helpers. Guide and folder actions are launched
through Cinnamon's GJS/Gio default-app API.

## Languages

Configure `Primary recognition language` and `Secondary recognition language` in the applet settings. The `Language`
submenu can:

- select the active runtime language,
- start a recording directly with the primary language,
- start a recording directly with the secondary language.

The presets use common Whisper-compatible ISO codes, including English, German, Spanish, French, Portuguese, Polish,
Russian, Ukrainian, Turkish, Arabic, Chinese, Japanese, Korean, and Hindi.

## Recorder And Input Source

Use the `Recorder` submenu to choose:

- `Automatic`
- `PipeWire pw-record`
- `PulseAudio parecord`
- `ALSA arecord`

Use `Input source` to select either `System default` or a concrete PipeWire/Pulse source. The backend helper lists
source names with:

```bash
speed-of-cinnamon list-inputs --json
```

Store the `name` value, not the localized description. It is passed to `pw-record --target`, `parecord --device`, or
`arecord --device`, depending on the selected recorder.

## Recording Options

Use `Duration` to set a common maximum recording length without opening settings. Changing it while recording affects
the next recording.

Use `Recording options` to toggle:

- `Auto-transcribe at time limit`
- `Keep recording files`

When a recording reaches its maximum length, the backend preserves the audio as `recorded`. By default, the applet starts
transcription as soon as it sees that state. Disable auto-transcription to keep the recording at `RDY` until the next
shortcut press.

Recording files are deleted after successful transcription by default. Keep them only when debugging recorder or ASR
behavior.

## Output Modes

Use the `Output` submenu to switch insertion mode:

- `Clipboard and paste`
- `Clipboard only`
- `Direct typing`
- `Do not insert`

When invoked from the applet, the backend returns text without inserting it. The applet then applies the selected output
mode. Clipboard copy uses Cinnamon's `St.Clipboard`. Automatic paste and direct typing use `xdotool` on X11 when it is
available. Without `xdotool`, dictation can still complete as a Cinnamon clipboard copy.

For `Clipboard and paste`, the applet checks the current clipboard targets before overwriting them. If the clipboard
contains non-text data such as images, files, or app-specific MIME data, a short confirmation dialog describes the
detected target types and asks whether to replace the clipboard content before continuing the paste.

Use `Text options` to toggle:

- `Append trailing space`
- `Replace accents before output`

Use `Auto-Paste` to choose one or more target-window markers such as `codex`, `Terminal`, `PDF`, `Excel`, or `Teams`. Built-in marker names such as `Terminal`, `PDF`, `Excel`, and `Teams` match known window classes/app IDs before inserted text gets a trailing Enter. `codex` and other custom comma-separated or line-separated markers match the full window title case-insensitively; an empty custom value disables Auto-Paste. The default marker is `codex`.

Accent replacement is a compatibility fallback for direct typing. Saved transcripts stay unchanged.

### Security and blacklist

Sensitive tokens can be masked before text is inserted or saved. The local blacklist file is:

`~/.local/share/speed-of-cinnamon/blacklist.txt`

Matches are normalized and deduplicated on load. Say `blacklisteintrag: <term>` while dictating to add new words. The applet hides redacted terms and reports how many matches were removed.

## Target Window

When dictation starts through the global shortcut or panel menu, the applet remembers Cinnamon's currently focused
normal window. Before optional paste or direct typing, it reactivates that window and waits briefly for focus to settle.
This keeps panel-triggered dictation aimed at the application you were using, not the applet menu.

## Transcript History

Each completed transcription is saved below:

```text
~/.local/state/speed-of-cinnamon/transcripts/
```

The applet's `Recent transcripts` menu offers:

- `Insert transcript` with the current output mode.
- `Copy transcript` through Cinnamon's clipboard.

Use `Insert last transcript` to retry the most recent transcript after changing output mode or refocusing a target
application.

## Voice Models

The `Voice model` menu exposes a local voice model catalog split into `CTranslate2` and `GGML`. It can download, select,
and remove catalog models, or switch back to `Automatic voice model`. Selecting a model chooses the matching local engine
automatically.

For German dictation, `ct2-base-int8` is the default starter model because it is small, fast, and close to base quality
on short local tests. Compare it with `ct2-base` and GGML `base` when accuracy matters. `ct2-small-de` is a German
CTranslate2 small model, while `ct2-tiny-de` and GGML `tiny-de` prioritize speed. The `.en` models are English-only.
GGML `large-v3-turbo-q5_0` is much more accurate on some recordings but can be very slow on CPU.

CTranslate2 models require faster-whisper:

```bash
python3 -m pip install --user faster-whisper
```

Use the same short local recording to compare model speed and output:

```bash
speed-of-cinnamon benchmark-models ~/testaufnahme.wav --language de --models ct2-base-int8 ct2-base ct2-small-de ct2-tiny-de ct2-small tiny-de tiny base --json
```

The benchmark only uses local downloaded catalog files. It reports missing models, failed runs, elapsed seconds, and the
raw transcript for each model.

Downloaded files are stored below:

```text
~/.local/share/speed-of-cinnamon/models/whisper.cpp/
~/.local/share/speed-of-cinnamon/models/ctranslate2/
```

GGML downloads verify SHA-1 checksums before activation. CTranslate2 downloads verify the required model files are
present. If faster-whisper, `whisper-cli`, `whisper.cpp`, or Fedora's `pwcpp` is installed, Automatic transcription can
use a downloaded model even when the explicit voice model setting is empty.

## Text Models

The `Text model` menu controls optional text polishing. It can:

- disable polishing,
- use the custom command backend,
- select a model returned by a local Ollama `/api/tags` endpoint,
- select a model returned by an OpenAI-compatible `/v1/models` endpoint.

If the selected endpoint is not reachable, the menu stays usable and shows the connection status instead of blocking
dictation.

Install an Ollama text model directly from the terminal when you need a specific polishing model:

```bash
speed-of-cinnamon install-text-model --model llama3.2:3b --json
```

## Notifications

Completion and error notifications are enabled by default. Recording-start and time-limit notifications can be enabled
separately. Use the `Notifications` submenu to toggle recording, completion, and error notifications without opening
Cinnamon settings.

Notifications are emitted by the Cinnamon applet through Cinnamon APIs. The backend stays quiet for CLI smoke tests and
scripted runs.

## Alarms

Alarms are Cinnamon-local. Definitions live in:

```text
~/.local/share/speed-of-cinnamon/alarms.json
```

The backend evaluates due alarms, and the applet emits Cinnamon notifications. The `Alarms` menu lists configured
alarms, can check them immediately, opens the local alarm store folder, and copies starter CLI commands.

The periodic applet check marks due alarms so they are not repeated on every refresh. A short catch-up window covers
alarms that became due while the applet was not running.

## Cleanup

Use `Preview cleanup` before `Clean old files`. Cleanup removes old transcript files and cached recordings while
skipping the currently referenced state paths. Recordings and their companion logs are handled as one cache group.

The matching CLI dry run is:

```bash
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 20 --dry-run --json
```

## Settings Backup

Use `Export settings` and `Import settings` in the applet menu to write or restore:

```text
~/.local/share/speed-of-cinnamon/settings-export.json
```

The export includes hotkeys, languages, recorder/backend choices, command templates, personalization, output mode,
recording retention, notification settings, and the local alarm store. It intentionally excludes machine-local
`cli-path` and `openai-compatible-api-key`.

Treat the export as private. Command templates, personal context, vocabulary, and alarm names may contain private data.

## Diagnostics

The applet provides `Copy diagnostics` and `Save diagnostics`. Saved reports are written below:

```text
~/.local/state/speed-of-cinnamon/diagnostics/
```

Diagnostics include app/runtime paths, desktop/session details, doctor checks, input-source metadata, model catalog
status, and state. They omit transcript contents, private command templates, and personalization text.
