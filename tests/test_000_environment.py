from __future__ import annotations

try:
    from tests._test_env import ensure_source_path, isolate_user_state
except ImportError:
    from _test_env import ensure_source_path, isolate_user_state


# unittest discover imports this first even when it does not import the tests
# directory as a package.
isolate_user_state()
ensure_source_path()
