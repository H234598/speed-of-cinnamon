# Speed of Cinnamon CLI Reference

The CLI is the backend entry point used by the Cinnamon applet and by manual tests. Use `--json` for machine-readable
output where supported.

## Setup And Diagnostics

```bash
speed-of-cinnamon doctor --json
speed-of-cinnamon doctor \
  --applet \
  --settings-json '{"transcriber":"command","transcriber-command":"printf ok","insert-method":"clipboard-paste"}' \
  --json
speed-of-cinnamon setup \
  --applet \
  --settings-json '{"transcriber":"auto","insert-method":"clipboard-paste"}' \
  --json
speed-of-cinnamon diagnostics --json
speed-of-cinnamon diagnostics --save --json
speed-of-cinnamon diagnostics \
  --applet \
  --settings-json '{"transcriber":"command","transcriber-command":"printf ok"}' \
  --json
```

`doctor` is configuration-aware. With `--applet`, missing ASR is a readiness failure, while missing `xdotool` in
`clipboard-paste` mode is a warning because the applet can still copy through Cinnamon's clipboard.

Diagnostics include derived doctor status and runtime metadata. They omit transcript contents, command template
contents, personal context, and vocabulary.

## Recording

```bash
speed-of-cinnamon start --language de
speed-of-cinnamon start \
  --language de \
  --input-device alsa_input.usb-Creative_Technology_Ltd_Sound_BlasterX_G6_8400614358X-00.analog-stereo
speed-of-cinnamon stop --language de --insert-method clipboard-paste
speed-of-cinnamon cancel
speed-of-cinnamon toggle --language de --transcriber whisper
speed-of-cinnamon toggle --keep-recording-artifacts --insert-method none
```

For backend-only testing without touching the focused application:

```bash
speed-of-cinnamon toggle --insert-method none --transcriber command --transcriber-command "printf 'test'"
```

## Input Sources

```bash
speed-of-cinnamon list-inputs --json
```

Use the returned `name` value for `--input-device`. It is passed to the selected recorder backend.

## ASR Backends

```bash
speed-of-cinnamon toggle --language de --transcriber whisper
speed-of-cinnamon toggle \
  --language de \
  --transcriber whisper-cpp \
  --whisper-model ~/.local/share/whisper/models/ggml-base.bin
speed-of-cinnamon toggle \
  --language de \
  --transcriber command \
  --transcriber-command "printf 'Hallo Cinnamon'"
```

Automatic mode resolves in this order:

- custom command when configured,
- installed `whisper`,
- whisper.cpp when a model path is available and a compatible CLI is installed.

## Custom Command Templates

Custom command templates are executed directly as command argument lists. They are not passed through a shell.

Supported placeholders:

```text
{audio} {language} {text} {output_dir} {output_base}
```

Examples:

```text
printf 'test transcript'
whisper {audio} --language {language} --output_format txt --output_dir {output_dir}
whisper-cli -m ~/.local/share/whisper/models/ggml-base.bin -f {audio} -l {language} -otxt -of {output_base} && cat {text}
pwcpp -m ~/.local/share/whisper/models/ggml-base.bin --language {language} -otxt {audio} && cat {audio}.txt
```

Optional `&&` chaining is supported for sensible multi-step local commands. Shell operators and redirections are
rejected, including `|`, `||`, `&`, `>`, and `2>`.

## Personalization

```bash
speed-of-cinnamon toggle \
  --personal-context "Use Fedora Cinnamon project terms." \
  --vocabulary "PipeWire"
```

Custom transcriber and post-process commands can use these placeholders:

```text
{context} {vocabulary} {prompt}
```

The same values are exposed as environment variables:

```text
SPEED_OF_CINNAMON_CONTEXT
SPEED_OF_CINNAMON_VOCABULARY
SPEED_OF_CINNAMON_PROMPT
```

## Text Polishing

Custom command backend:

```bash
speed-of-cinnamon toggle \
  --post-process-command "python3 -c 'import sys; print(sys.stdin.read().strip().capitalize())'"
```

Ollama backend:

```bash
speed-of-cinnamon toggle \
  --post-process-backend ollama \
  --ollama-url http://127.0.0.1:11434 \
  --ollama-model llama3.2:3b
```

OpenAI-compatible local backend:

```bash
speed-of-cinnamon toggle \
  --post-process-backend openai-compatible \
  --openai-compatible-url http://127.0.0.1:8000/v1 \
  --openai-compatible-model local-llama
```

List local text models:

```bash
speed-of-cinnamon text-models --json
speed-of-cinnamon text-models \
  --backend openai-compatible \
  --openai-compatible-url http://127.0.0.1:8000/v1 \
  --json
```

If the OpenAI-compatible server requires a bearer token, set
`SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY` in the environment that starts the backend.

## Voice Models

```bash
speed-of-cinnamon models --json
speed-of-cinnamon download-model tiny-de --json
speed-of-cinnamon download-model tiny --json
speed-of-cinnamon download-model base --json
speed-of-cinnamon remove-model tiny --json
```

Use `tiny-de` for a small German-only test model, or multilingual models such as `tiny`, `base`, or `small` for German
and other non-English dictation. The `.en` models are English-only and can produce placeholder text such as
`[speaking in foreign language]` when the recording language is not English.

Compare downloaded catalog models on the same local audio file:

```bash
speed-of-cinnamon benchmark-models ~/testaufnahme.wav --language de --models tiny-de tiny base --json
```

Without `--models`, the benchmark uses downloaded catalog models that match the selected language. Missing models are
reported per entry and are not downloaded automatically.

Downloaded whisper.cpp models are stored below:

```text
~/.local/share/speed-of-cinnamon/models/whisper.cpp/
```

Downloads verify SHA-1 checksums before a model is activated.

## Output Compatibility

```bash
speed-of-cinnamon toggle --sanitize-special-chars --insert-method type
```

This maps common accented and special characters to ASCII before output. Saved transcripts stay unchanged.

## History And Cleanup

```bash
speed-of-cinnamon history --limit 5 --json
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
speed-of-cinnamon cleanup --keep-transcripts 100 --keep-recordings 25 --json
```

Cleanup skips the currently referenced state paths. Use the dry run before deleting.

## Settings Backup

```bash
speed-of-cinnamon settings-export --settings-json '{"language":"de","append-space":true}' --json
speed-of-cinnamon settings-import --json
```

The default export/import path is:

```text
~/.local/share/speed-of-cinnamon/settings-export.json
```

The export includes the local alarm store and intentionally excludes machine-local `cli-path`.

## Alarms

```bash
speed-of-cinnamon alarms add --time 09:00 --name "Standup" --days weekdays --json
speed-of-cinnamon alarms list --json
speed-of-cinnamon alarms check --mark --json
speed-of-cinnamon alarms disable alarm-0900 --json
speed-of-cinnamon alarms remove alarm-0900 --json
```

`--days` accepts `daily`, `weekdays`, `weekends`, or comma-separated day codes such as `mon,wed,fri`.
`--urgency` can be `silent`, `normal`, or `critical`. Silent alarms are still marked due but do not notify.
