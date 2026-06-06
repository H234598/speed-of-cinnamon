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

`--transcriber` accepts normalized aliases for backward compatibility:

```text
auto, openai, openai-whisper, whisper, whisper-cpp, faster-whisper, openai-compatible, external-api, command, custom, template
```

Equivalent forms are:

- `openai` and `openai-whisper` => `whisper`
- `faster-whisper` stays `faster-whisper`
- `openai-compatible` and `external-api` => `openai-compatible`
- `custom` and `template` => `command`

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

OpenAI-compatible API backend:

```bash
speed-of-cinnamon toggle \
  --post-process-backend openai-compatible \
  --openai-compatible-url https://api.openai.com/v1 \
  --openai-compatible-text-model gpt-4o-mini
```

For OpenAI API calls, Flex processing is enabled by default for speech-to-text
and text polishing. Disable it with `--no-openai-compatible-flex-processing`.
The flag is only sent to `api.openai.com`, not to local OpenAI-compatible
servers.

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

OpenAI-compatible speech-to-text:

```bash
speed-of-cinnamon transcribe-file ~/Downloads/Testaufnahme.flac \
  --language de \
  --transcriber openai-compatible \
  --openai-compatible-url https://api.openai.com/v1 \
  --openai-compatible-model gpt-4o-transcribe \
  --json
```

## Security: Blacklist and redaction

`transcribe-file` and `toggle`/`finalize` run a blacklist and sensitive-token redaction stage before output is returned or inserted.

- Configure entries in `~/.local/share/speed-of-cinnamon/blacklist.txt`.
- Entries are normalized (`trim`, punctuation cleanup, case-insensitive dedupe) and duplicates are ignored.
- Use the directive `blacklisteintrag: <entry>` to add terms during dictation.
- Add `--json` to inspect `security.blacklist_hits` and `security.blacklist_added`.

## Voice Models

```bash
speed-of-cinnamon install-text-model --backend ollama --model llama3.2:3b --json
speed-of-cinnamon models --json
speed-of-cinnamon download-model ct2-base-int8 --json
speed-of-cinnamon download-model ct2-base --json
speed-of-cinnamon download-model ct2-small-de --json
speed-of-cinnamon download-model ct2-tiny-de --json
speed-of-cinnamon download-model ct2-small --json
speed-of-cinnamon download-model tiny-de --json
speed-of-cinnamon download-model tiny --json
speed-of-cinnamon download-model base --json
speed-of-cinnamon remove-model tiny --json
```

The catalog contains GGML models for whisper.cpp and CTranslate2 models for faster-whisper. Selecting a model chooses
the matching backend automatically. Install faster-whisper before using CTranslate2 models:

```bash
python3 -m pip install --user faster-whisper
```

Use `ct2-base-int8` as the default German starter model. It is smaller and faster than `ct2-base` while staying close to
base quality on short local tests. Compare it with `ct2-base` or GGML `base` when accuracy matters, and use `tiny-de` or
`ct2-tiny-de` only when speed matters more than accuracy. The `.en` models are English-only and can produce placeholder
text such as `[speaking in foreign language]` when the recording language is not English.

Compare downloaded catalog models on the same local audio file:

```bash
speed-of-cinnamon benchmark-models ~/testaufnahme.wav --language de --models ct2-base-int8 ct2-base ct2-small-de ct2-tiny-de ct2-small tiny-de tiny base --json
```

Without `--models`, the benchmark uses downloaded catalog models that match the selected language. Missing models are
reported per entry and are not downloaded automatically.

Downloaded models are stored below:

```text
~/.local/share/speed-of-cinnamon/models/whisper.cpp/
~/.local/share/speed-of-cinnamon/models/ctranslate2/
```

Downloads verify SHA-1 checksums before a model is activated.

## Output Compatibility

```bash
speed-of-cinnamon insert-text "Hallo Terminal" --insert-method clipboard-paste --json
speed-of-cinnamon toggle --sanitize-special-chars --insert-method type
speed-of-cinnamon profanity-filter-document --json
```

`insert-text` exercises only the output path. It is useful for testing clipboard, terminal paste, direct typing, and
character sanitizing without recording or transcribing audio.

`--sanitize-special-chars` maps common accented and special characters to ASCII before output. Saved transcripts stay
unchanged.

`profanity-filter-document` creates the editable profanity replacement file if needed and returns its path. The applet
settings button opens the same file for editing.

## History And Cleanup

```bash
speed-of-cinnamon history --limit 5 --json
speed-of-cinnamon transcripts-document --limit 1000 --json
speed-of-cinnamon cleanup --keep-transcripts 0 --keep-recordings 0 --dry-run --json
speed-of-cinnamon cleanup --keep-transcripts 0 --keep-recordings 0 --json
```

`transcripts-document` writes a plain text document with complete transcript contents for scrolling, selecting, and
copying outside the applet menu.

Cleanup skips the currently referenced state paths. Use the dry run before deleting.

## Artifact Encryption

```bash
speed-of-cinnamon transcribe-file ~/sample.flac --artifact-encryption passphrase --json
speed-of-cinnamon toggle --artifact-encryption keyring --keep-recording-artifacts
```

`--artifact-encryption` accepts `off`, `passphrase`, or `keyring`. Passphrase mode derives per-file keys from
`SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE_FILE` or `SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE`; prefer the file variable
to avoid shell history exposure. Keyring mode stores one random master key in the desktop Secret Service through
`secret-tool`. If keyring access fails in CLI mode, the backend tries the passphrase fallback. If no usable key source is
available, encrypted writes fail closed instead of writing a plaintext archive file.

## Settings Backup

```bash
speed-of-cinnamon settings-export --settings-json '{"language":"de","append-space":true}' --json
speed-of-cinnamon settings-import --json
```

The default export/import path is:

```text
~/.local/share/speed-of-cinnamon/settings-export.json
```

The export includes the local alarm store and intentionally excludes machine-local `cli-path` and
`openai-compatible-api-key`.

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
