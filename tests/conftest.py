"""Shared fixtures: the truth model and generated data sets (session scope)."""

import pytest

from ingest.generate import generate
from ingest.world import build_world


@pytest.fixture(scope="session")
def world():
    return build_world(42)


@pytest.fixture(scope="session")
def drift_data(tmp_path_factory):
    out = tmp_path_factory.mktemp("drift")
    generate(42, str(out), inject=True)
    return str(out)


@pytest.fixture(scope="session")
def clean_data(tmp_path_factory):
    out = tmp_path_factory.mktemp("nodrift")
    generate(42, str(out), inject=False)
    return str(out)
