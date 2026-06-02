# Speed of Cinnamon

Cinnamon-native voice typing for current Fedora Cinnamon sessions.

Speed of Cinnamon uses Speed of Sound as a product reference, not as a blind fork. The useful workflow is kept:
press a shortcut, speak, transcribe, then insert the result into the focused application. The implementation is built
for Cinnamon: a Cinnamon applet owns the panel UI, global hotkeys, clipboard handling, notifications, and target-window
restore; a small Python backend records audio, runs a configurable local transcriber, and reports state. There is no
XDG portal core path.

## What It Does

- Cinnamon panel applet with microphone status, setup hints, transcript preview, and menu actions.
- Global Cinnamon shortcut, default `Super+Z`, plus optional primary/secondary language shortcuts.
- Recording through PipeWire `pw-record`, PulseAudio `parecord`, or ALSA `arecord`.
- ASR through Automatic mode, OpenAI Whisper CLI, whisper.cpp, or a hardened custom command template.
- Optional local text polishing through a command, Ollama, or an OpenAI-compatible local server.
- Cinnamon clipboard output, optional X11 paste/direct typing via `xdotool`, and clipboard-only/no-insert modes.
- Transcript history, last-transcript retry, cleanup preview/removal, diagnostics, and portable settings backup.
- Cinnamon-local repeating alarms with applet notifications and CLI management.
- Local model catalog for checksum-verified whisper.cpp downloads.
- Python tests, source archive verification, RPM checks, CI artifacts, release automation, and QLTY coverage upload.

## Quick Install

For the intended Fedora Cinnamon/X11 path:

```bash
git clone https://github.com/H234598/speed-of-cinnamon.git
cd speed-of-cinnamon
./scripts/install-fedora-deps.sh
make install-local
```

Then add `Speed of Cinnamon` from Cinnamon's applet settings. If Cinnamon does not refresh the list immediately,
reload Cinnamon with:

```text
Alt+F2, r, Enter
```

The backend command is installed to:

```text
~/.local/bin/speed-of-cinnamon
```

When the applet's `Backend command` setting is empty, it auto-detects the backend in this order:
`~/.local/bin/speed-of-cinnamon`, `/usr/bin/speed-of-cinnamon`, then `speed-of-cinnamon` from `PATH`.

## First Setup

On startup, the applet runs a lightweight doctor check against its current Cinnamon settings. A `SET` panel label means
the selected pipeline still needs setup, usually because no ASR backend or local voice model is available yet.

Use the applet menu actions:

- `Copy setup plan`
- `Copy setup commands`
- `Run doctor`
- `Restart applet`
- `Open applet settings`
- `Open setup guide`
- `Voice model`

`Copy setup commands` copies only concrete shell commands. The applet does not run `sudo`, `pkexec`, a package manager,
or a portal helper.

For Fedora RPM installs, the package requires `python3-pywhispercpp`, which provides the `pwcpp` whisper.cpp-compatible
CLI. Local source installs only warn when no ASR backend is present because they do not run a system package manager.

For backend-only testing without touching the focused application:

```bash
speed-of-cinnamon toggle --insert-method none --transcriber command --transcriber-command "printf 'test'"
```

## Documentation

- [User Guide](docs/user-guide.md): applet workflow, output modes, history, models, alarms, cleanup, and backups.
- [CLI Reference](docs/cli-reference.md): command examples grouped by setup, recording, models, alarms, and maintenance.
- [Architecture](docs/architecture.md): Cinnamon-native design, backend boundary, command hardening, privacy, and limits.
- [Development](docs/development.md): tests, coverage, release archives, RPMs, CI, and release publishing.
- [Fedora Cinnamon Runbook](docs/fedora-cinnamon-runbook.md): operational Fedora setup and troubleshooting runbook.
- [Man pages](docs/man/): installable `speed-of-cinnamon(1)` and `speed-of-cinnamon-alarms(1)`.
- [Wiki source](docs/wiki/): source pages published to the GitHub wiki.

## Common Commands

```bash
speed-of-cinnamon doctor --json
speed-of-cinnamon setup --applet --settings-json '{"transcriber":"auto","insert-method":"clipboard-paste"}' --json
speed-of-cinnamon diagnostics --save --json
speed-of-cinnamon list-inputs --json
speed-of-cinnamon models --json
speed-of-cinnamon benchmark-models ~/testaufnahme.wav --language de --models ct2-base ct2-small-de ct2-tiny-de ct2-small tiny-de tiny base --json
speed-of-cinnamon text-models --json
speed-of-cinnamon history --limit 5 --json
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
speed-of-cinnamon settings-export --settings-json '{"language":"de","append-space":true}' --json
speed-of-cinnamon alarms add --time 09:00 --name "Standup" --days weekdays --json
```

Custom transcriber and post-process templates are executed as command argument lists, not through a shell. Optional
`&&` command chaining is supported for sensible multi-step local commands. Shell operators and redirections such as
`|`, `||`, `&`, `>`, and `2>` are rejected.

## Development

```bash
make check
```

The check target runs Python unit tests, compiles Python files, validates Cinnamon JSON metadata/settings, verifies
project authorship metadata, and runs a backend doctor smoke check.

Coverage is generated separately so normal local checks do not require the coverage package:

```bash
python -m pip install coverage
make coverage
```

This writes `reports/lcov.info`. GitHub Actions uploads that file through `qltysh/qlty-action/coverage@v2` when the
repository secret `QLTY_COVERAGE_TOKEN` is available.

To verify the source release archive:

```bash
make dist-check
```

To build and verify the experimental Fedora noarch RPM:

```bash
make rpm
make rpm-check
```

Successful GitHub Actions runs upload:

- `speed-of-cinnamon-source-<commit>` with the source archive and `.sha256`.
- `speed-of-cinnamon-rpm-<commit>` with the noarch RPM and source RPM.

Release publishing is documented in [Development](docs/development.md).

## Runtime Paths

```text
Applet:      ~/.local/share/cinnamon/applets/speed-of-cinnamon@H234598/
Backend:     ~/.local/bin/speed-of-cinnamon or /usr/bin/speed-of-cinnamon
State:       ~/.local/state/speed-of-cinnamon/state.json
Transcripts: ~/.local/state/speed-of-cinnamon/transcripts/
Recordings:  ~/.cache/speed-of-cinnamon/recordings/
Models:      ~/.local/share/speed-of-cinnamon/models/whisper.cpp/
Alarms:      ~/.local/share/speed-of-cinnamon/alarms.json
```

Temporary recording files are deleted after successful transcription by default. Enable `Keep recording files` only when
debugging recorder or ASR behavior.
