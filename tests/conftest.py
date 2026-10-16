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


def _load(data, graph):
    """Fresh-load `data` into `graph`; returns the Loader (needs FalkorDB)."""
    import os
    from falkordb import FalkorDB
    from ingest.load import Loader
    host, port = os.environ.get("FALKORDB_HOST", "localhost"), int(os.environ.get("FALKORDB_PORT", 6379))
    db = FalkorDB(host=host, port=port)
    for name in db.list_graphs():  # the graph and its agent memory graphs (a fresh load starts with no memory)
        if name == graph or name.startswith(f"{graph}_memory"):
            db.select_graph(name).delete()
    ld = Loader(data, graph, host=host, port=port)
    ld.load()
    return ld


@pytest.fixture(scope="session")
def full_graph(drift_data):
    ld = _load(drift_data, "northwind_test_full")
    yield ld.g
    ld.g.delete()


@pytest.fixture(scope="session")
def demo_graph(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    generate(42, str(out), inject=True, subset="demo", gz=True)
    ld = _load(str(out), "northwind_test_demo")
    yield ld.g, str(out)
    ld.g.delete()


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    """Tests never use a live provider, whatever the shell or .env says."""
    from agent import llm
    for k in ("LLM_PROVIDER", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "NORTHWIND_MODELS", "OPENAI_API_KEYS",
              "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    llm.get_llm.cache_clear()
    yield
    llm.get_llm.cache_clear()
