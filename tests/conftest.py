"""Configure pytest: register the objective markers.

The shared test helpers live in the ``tests.helpers`` module.
"""

import signal
from typing import TYPE_CHECKING

from tests.helpers import OBJECTIVES

if TYPE_CHECKING:
    import pytest

# Importing pprl (above, through helpers) installs a handler that makes Ctrl+C
# stop pprl quietly while it starts. Restore the default handler so pytest
# keeps its own handling of Ctrl+C.
signal.signal(signal.SIGINT, signal.default_int_handler)


def pytest_configure(config: pytest.Config) -> None:
    """Register one marker per objective, plus ``extended``.

    The objectives are OBJECTIVES in the helpers module. ``extended`` marks
    the exhaustive tests, which run only when every test is run.
    """
    for name, description in OBJECTIVES.items():
        config.addinivalue_line("markers", f"{name}: {description}")
    config.addinivalue_line(
        "markers",
        "extended: an exhaustive test, run only with every test (make test-all, or "
        "pytest -m 'extended or not extended'), "
        "before a release and on version tags",
    )
