#!/usr/bin/env bash
set -euo pipefail

if ! compgen -G '.github/workflows/*.yml' > /dev/null && ! compgen -G '.github/workflows/*.yaml' > /dev/null; then
  printf 'No workflow files found under .github/workflows.\n' >&2
  exit 1
fi

workflows=(.github/workflows/*.yml .github/workflows/*.yaml)
actionlint_strict="${ACTIONLINT_STRICT:-false}"

run_actionlint() {
  if command -v actionlint >/dev/null 2>&1; then
    actionlint "$@"
    return 0
  fi

  actionlint_image="rhysd/actionlint:v1.7.12"
  if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
      if docker run --rm -v "${PWD}:/repo" -w /repo "${actionlint_image}" "$@"; then
        return 0
      fi
      return 2
    fi
  fi

  if [[ "${actionlint_strict}" == "true" ]]; then
    printf 'actionlint unavailable in strict mode. Install actionlint (or ensure docker access) for workflow checks.\n' >&2
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

if command -v python3 >/dev/null 2>&1; then
  if python3 - <<'PY'
import sys
import glob

try:
    import yaml
except ImportError:
    sys.exit(2)

for workflow in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    with open(workflow, 'r', encoding='utf-8') as stream:
        yaml.safe_load(stream)
    print(f"yaml_ok: {workflow}")
PY
  then
    exit 0
  else
    printf 'Python YAML parser unavailable. Install actionlint for full workflow checks.\n' >&2
    exit 1
  fi
fi

printf 'No actionlint or YAML fallback available. Install actionlint, docker, or python3+PyYAML.\n' >&2
exit 1
