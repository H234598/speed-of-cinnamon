from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

try:
    from tests._test_env import ensure_source_path, isolate_user_state as _initialize_test_environment
except ImportError:
    from _test_env import ensure_source_path, isolate_user_state as _initialize_test_environment


_TEST_ROOT = Path(_initialize_test_environment())
ensure_source_path()


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _prepare_pytest_cache(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    current = path.lstat()
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_uid != os.getuid()
        or stat.S_IMODE(current.st_mode) != 0o700
    ):
        raise RuntimeError("pytest cache directory is not private")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    run_id = f"pytest-{os.getpid()}-{os.urandom(6).hex()}"
    basetemp = _TEST_ROOT / "tmp" / run_id
    cache_dir = _TEST_ROOT / "cache" / run_id
    _prepare_pytest_cache(cache_dir)
    config.option.basetemp = os.fspath(basetemp)
    # pytest has no public conftest-time setter for its scalar cache_dir ini value.
    config._inicache["cache_dir"] = os.fspath(cache_dir)


@pytest.fixture(autouse=True)
def isolate_user_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep direct pytest runs away from the user's runtime directories."""

    test_tmp = tmp_path / "tmp"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    data_home = tmp_path / "data"
    config_home = tmp_path / "config"
    for directory in (test_tmp, state_home, cache_home, data_home, config_home):
        directory.mkdir(mode=0o700)
    monkeypatch.setenv("TMPDIR", os.fspath(test_tmp))
    monkeypatch.setenv("TEMP", os.fspath(test_tmp))
    monkeypatch.setenv("TMP", os.fspath(test_tmp))
    monkeypatch.setenv("XDG_STATE_HOME", os.fspath(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", os.fspath(cache_home))
    monkeypatch.setenv("XDG_DATA_HOME", os.fspath(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", os.fspath(config_home))
    monkeypatch.setattr(tempfile, "tempdir", os.fspath(test_tmp))
