# Speed of Cinnamon Architecture

Speed of Cinnamon is intentionally Cinnamon-native. It borrows the user workflow from Speed of Sound, but not the
integration stack.

## Runtime Pipeline

```text
Cinnamon keybinding / panel click
  -> files/speed-of-cinnamon@H234598/applet.js
  -> ~/.local/bin/speed-of-cinnamon or /usr/bin/speed-of-cinnamon
  -> Python backend:
       recorder.py        pw-record / parecord / arecord and pactl source discovery
       transcriber.py     ASR preset resolver and command runners
       command_chain.py   shell-free command-template execution with limited && chaining
       personalization.py context/vocabulary prompt and environment helpers
       postprocessor.py   optional command, Ollama, or OpenAI-compatible local text polishing
       alarms.py          local repeating alarm store and due-alarm scheduler
       settings_export.py portable settings snapshot helpers
       cli.py             state commands, cleanup, diagnostics, history, and CLI entry point
       output.py          xclip / xdotool helpers for standalone CLI output
       state.py           JSON state for applet status
```

## Applet Boundary

The applet owns the Cinnamon integration:

- panel UI,
- global keybindings,
- Cinnamon clipboard copy,
- completion/error/recording notifications,
- target-window restore,
- menu state and applet settings,
- output-mode application for applet-triggered dictation.

The backend records, transcribes, post-processes, stores state, and returns structured results. When called by the
applet, the backend is asked to return text without inserting it. The applet then copies, pastes, types, or does nothing
according to the current output mode.

## No Portal Core Path

There is no XDG portal core path. The portal-oriented parts of Speed of Sound do not fit this goal because Cinnamon
already has the panel, keybinding, clipboard, and notification APIs needed here.

Guide and folder actions use Cinnamon's GJS/Gio default-app launcher. Notifications are emitted through Cinnamon's
`Main.notify` and `Main.criticalNotify`. Clipboard copy uses Cinnamon's `St.Clipboard`. `xdotool` is used only for the
X11 keystrokes needed by automatic paste or direct typing.

## Recorder Boundary

The recorder layer supports:

- PipeWire `pw-record`,
- PulseAudio `parecord`,
- ALSA `arecord`.

`pactl` is used for input-source discovery when available. Path lengths, recording durations, input-device strings, and
recorder command output are bounded before they can affect the rest of the pipeline.

## ASR Boundary

ASR is replaceable. The backend supports:

- Automatic resolver,
- OpenAI Whisper command,
- whisper.cpp-compatible CLIs such as `whisper-cli`, `whisper.cpp`, or Fedora's `pwcpp`,
- custom command template.

Custom command templates are rendered with quoted placeholders and then parsed into an argument list. They are not run
through a shell. Limited `&&` chaining is supported because it is useful for commands such as whisper.cpp followed by
`cat {text}`. Other shell operators and redirections are rejected.

Command stdout/stderr, input size, command length, segment count, and runtime are bounded. Audio paths and transcript
sizes are validated before use.

## Text Polishing Boundary

Text polishing is optional and local-first:

- disabled,
- custom command,
- local Ollama `/api/generate`,
- local OpenAI-compatible `/v1/chat/completions`.

The backend sends transcript text, language, personal context, and vocabulary to the selected local processor. Response
size and output text length are bounded. Diagnostics do not include private command templates, context, or vocabulary.

## State And Privacy

Runtime state is per-user:

```text
State:       ~/.local/state/speed-of-cinnamon/state.json
Transcripts: ~/.local/state/speed-of-cinnamon/transcripts/
Recordings:  ~/.cache/speed-of-cinnamon/recordings/
Models:      ~/.local/share/speed-of-cinnamon/models/whisper.cpp/
Alarms:      ~/.local/share/speed-of-cinnamon/alarms.json
```

Temporary recordings are deleted after successful transcription by default. Settings exports are private backups because
they may contain personal context, vocabulary, command templates, hotkeys, and alarm names.

## Doctor And Diagnostics

The doctor command accepts applet settings as JSON and evaluates the configured pipeline, not just installed binaries.
With `--applet`, the doctor evaluates Cinnamon's clipboard path and treats missing `xdotool` in applet
`clipboard-paste` mode as a warning instead of a hard failure.

Diagnostics embed derived doctor status and local runtime metadata. They intentionally omit transcript contents and
private settings.

## Known Limits

- The app is local-first and does not bundle an ASR model.
- Automatic paste and direct typing need `xdotool` on X11.
- The applet targets Cinnamon 6.x style settings and keybinding APIs.
- CLI clipboard insertion outside the applet still needs an X11 clipboard helper such as `xclip` or `xsel`.
