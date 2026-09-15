#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"
readonly MAX_WORKFLOW_FILES=256
readonly ACTIONLINT_TIMEOUT_SECONDS=60
workflows=()
actionlint_path_override="${ACTIONLINT_PATH:-}"

validate_actionlint_override() {
  local path="$1"
  local owner=""
  local permissions=""
  local current_uid=""

  if [[ -z "${path}" || "${path}" != /* || "${path}" == *$'\n'* || "${path}" == *$'\r'* || "${path}" == *$'\t'* ]]; then
    printf 'actionlint override must be an absolute path without control characters.\n' >&2
    return 1
  fi
  if [[ ! -f "${path}" || -L "${path}" || ! -x "${path}" ]]; then
    printf 'actionlint override must be a non-symlink executable regular file.\n' >&2
    return 1
  fi
  if ! IFS=' ' read -r owner permissions < <(stat -c '%u %A' -- "${path}") || [[ ! "${owner}" =~ ^[0-9]+$ ]]; then
    printf 'actionlint override metadata could not be verified.\n' >&2
    return 1
  fi
  current_uid="$(id -u)"
  if [[ "${owner}" != "0" && "${owner}" != "${current_uid}" ]]; then
    printf 'actionlint override must be owned by root or the current user.\n' >&2
    return 1
  fi
  if [[ "${permissions:5:1}" == "w" || "${permissions:8:1}" == "w" ]]; then
    printf 'actionlint override must not be group- or world-writable.\n' >&2
    return 1
  fi
}

if [[ ! -d .github/workflows ]]; then
  printf 'Workflow directory is missing: .github/workflows.\n' >&2
  exit 1
fi
while IFS= read -r -d '' workflow; do
  workflows+=("$workflow")
  if (( ${#workflows[@]} > MAX_WORKFLOW_FILES )); then
    printf 'Too many workflow files (max %s).\n' "$MAX_WORKFLOW_FILES" >&2
    exit 1
  fi
done < <(find .github/workflows -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print0)
if [[ "${#workflows[@]}" -eq 0 ]]; then
  printf 'No workflow files found under .github/workflows.\n' >&2
  exit 1
fi
actionlint_strict="${ACTIONLINT_STRICT:-false}"

run_actionlint() {
  if [[ -n "${actionlint_path_override}" ]]; then
    validate_actionlint_override "${actionlint_path_override}" || return 1
    actionlint_path="${actionlint_path_override}"
  elif command -v -- actionlint >/dev/null 2>&1; then
    actionlint_path="$(command -v -- actionlint)"
  else
    if [[ "${actionlint_strict}" == "true" ]]; then
      printf 'actionlint unavailable in strict mode. Install actionlint for workflow checks.\n' >&2
      return 1
    fi
    return 2
  fi
  if ! command -v -- timeout >/dev/null 2>&1; then
    printf 'timeout unavailable; refusing unbounded actionlint execution.\n' >&2
    return 1
  fi
  if [[ -n "${actionlint_path}" ]]; then
    timeout --signal=TERM --kill-after=2s "${ACTIONLINT_TIMEOUT_SECONDS}s" "${actionlint_path}" "$@"
    return 0
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
  python3 - "${workflows[@]}" <<'PY' || yaml_status=$?
import sys

try:
    import yaml
except ImportError:
    sys.exit(2)

MAX_WORKFLOW_BYTES = 1 * 1024 * 1024
MAX_WORKFLOW_FILES = 256
workflow_paths = sys.argv[1:]
if len(workflow_paths) > MAX_WORKFLOW_FILES:
    raise ValueError(f"too many workflow files (max {MAX_WORKFLOW_FILES})")


class StrictLoader(yaml.SafeLoader):
    pass


def construct_strict_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ValueError("workflow YAML key is not hashable") from exc
        if duplicate:
            raise ValueError(f"duplicate workflow YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_strict_mapping,
)


for workflow in workflow_paths:
    try:
        with open(workflow, 'rb') as stream:
            payload = stream.read(MAX_WORKFLOW_BYTES + 1)
        if len(payload) > MAX_WORKFLOW_BYTES:
            raise ValueError(f"workflow exceeds {MAX_WORKFLOW_BYTES} bytes")
        text = payload.decode('utf-8')
        yaml.load(text, Loader=StrictLoader)
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
