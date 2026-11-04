from __future__ import annotations

import pytest

from northwind_data.dataset import Dataset
from northwind_data.generate import generate
from northwind_data.version import DEFAULT_SEED


@pytest.fixture(scope="session")
def ds() -> Dataset:
    """The dataset as the generator produces it now (tests don't trust committed files)."""
    return Dataset(generate(DEFAULT_SEED))
