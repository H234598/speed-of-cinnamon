#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
shopt -s nullglob

workflows=(.github/workflows/*.yml .github/workflows/*.yaml)
if [[ "${#workflows[@]}" -eq 0 ]]; then
  printf 'No workflow files found under .github/workflows.\n' >&2
  exit 1
fi
actionlint_strict="${ACTIONLINT_STRICT:-false}"

run_actionlint() {
  if command -v -- actionlint >/dev/null 2>&1; then
    actionlint "$@"
    return 0
  fi

  if [[ "${actionlint_strict}" == "true" ]]; then
    printf 'actionlint unavailable in strict mode. Install actionlint for workflow checks.\n' >&2
    return 1
  fi

  return 2
}

run_status=0
run_actionlint "${workflows[@]}" || run_status=$?

if [[ "${run_status}" == "0" ]]; then
  exit 0
fi

if [[ "${run_status}" == "1" ]]; then
  exit 1
fi

if command -v -- python3 >/dev/null 2>&1; then
  yaml_status=0
  python3 - <<'PY' || yaml_status=$?
import sys
import glob

try:
    import yaml
except ImportError:
    sys.exit(2)

MAX_WORKFLOW_BYTES = 1 * 1024 * 1024

for workflow in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    try:
        with open(workflow, 'rb') as stream:
            payload = stream.read(MAX_WORKFLOW_BYTES + 1)
        if len(payload) > MAX_WORKFLOW_BYTES:
            raise ValueError(f"workflow exceeds {MAX_WORKFLOW_BYTES} bytes")
        text = payload.decode('utf-8')
        yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        print(f"workflow YAML validation failed: {workflow}: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
    print(f"yaml_ok: {workflow}")
PY
  if [[ "${yaml_status}" == "0" ]]; then
    exit 0
  fi
  if [[ "${yaml_status}" == "2" ]]; then
    printf 'Python YAML parser unavailable. Install actionlint for full workflow checks.\n' >&2
  fi
  exit 1
fi

printf 'No actionlint or YAML fallback available. Install actionlint or python3+PyYAML.\n' >&2
exit 1
