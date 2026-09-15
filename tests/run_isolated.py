"""Explicit bootstrap for running one test module or script in isolation."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

try:
    from tests._test_env import ensure_source_path, isolate_user_state
except ImportError:
    from _test_env import ensure_source_path, isolate_user_state


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m tests.run_isolated MODULE_OR_SCRIPT [ARGS ...]")
    isolate_user_state()
    ensure_source_path()
    target = sys.argv[1]
    sys.argv = [target, *sys.argv[2:]]
    if target.endswith(".py") or Path(target).exists():
        runpy.run_path(target, run_name="__main__")
    else:
        runpy.run_module(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
