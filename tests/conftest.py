"""Load statusline.py as a module, pointed at a throwaway config dir."""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "statusline.py"


def load_statusline():
    spec = importlib.util.spec_from_file_location("statusline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sl(tmp_path):
    """The module, with every path it touches redirected into tmp_path."""
    module = load_statusline()
    module.CLAUDE_DIR = tmp_path
    module.CREDENTIALS = tmp_path / ".credentials.json"
    module.CACHE = tmp_path / "fable-usage-cache.json"
    module.LOCK = tmp_path / "fable-usage-cache.lock"
    return module


@pytest.fixture
def script_path():
    return SCRIPT
