from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def resources() -> Path:
    """Directory of self-contained example projects that the tests scan in place."""
    return Path(__file__).parent / "resources"
