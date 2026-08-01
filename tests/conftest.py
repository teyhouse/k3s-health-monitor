import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from k8s_monitor import config, discord_client, groq, kube, orchestrate, reporting  # noqa: E402
from utils import shell  # noqa: E402


@pytest.fixture(scope="session")
def km():
    """Backwards-compat fixture: yield the orchestrate module as 'km'.

    Tests reference km.main(), km.DISCORD_COLORS, km.GROQ_MODEL, km.GROQ_API_KEY,
    km.DISCORD_WEBHOOK, km.WARNING_FILTER, km.VELERO_CHECK_ENABLED — all now on
    config — and km.subprocess (now on utils.shell).
    """
    return orchestrate


@pytest.fixture(scope="session")
def km_config() -> object:
    return config


@pytest.fixture(scope="session")
def km_kube() -> object:
    return kube


@pytest.fixture(scope="session")
def km_groq() -> object:
    return groq


@pytest.fixture(scope="session")
def km_discord() -> object:
    return discord_client


@pytest.fixture(scope="session")
def km_reporting() -> object:
    return reporting


@pytest.fixture(scope="session")
def km_shell() -> object:
    return shell
