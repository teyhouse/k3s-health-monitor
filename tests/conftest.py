import importlib.util
import pathlib
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "k8s-monitor.py"


@pytest.fixture(scope="session")
def km():
    """Load k8s-monitor.py under a normal module name so tests can import it."""
    spec = importlib.util.spec_from_file_location("k8s_monitor", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["k8s_monitor"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("k8s_monitor", None)
