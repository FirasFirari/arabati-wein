"""Shared fixtures.

Two kinds of test live here:

* PUBLIC tests run anywhere, including GitHub Actions. They read the fictional
  profile in tests/fixtures/config.test.yaml and synthetic strings only. No
  real identifier, contact or post text may appear in a public test: the
  repository is public.
* PRIVATE tests (`@pytest.mark.private`) read the real config.yaml and the
  database under data/. They are skipped automatically when either is missing
  or when CI is set, so they run on the operator's machine only.
"""
import os
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def private_available() -> bool:
    return (not os.environ.get("CI")
            and (ROOT / "config.yaml").exists()
            and (ROOT / "data" / "carhunt.db").exists())


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "private: needs the private config.yaml and data/; skipped in CI")


def pytest_collection_modifyitems(config, items):
    if private_available():
        return
    skip = pytest.mark.skip(reason="private config.yaml / data/ not available")
    for item in items:
        if "private" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def test_config() -> dict:
    return yaml.safe_load((FIXTURES / "config.test.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def car(test_config) -> dict:
    return test_config["car"]


@pytest.fixture(scope="session")
def real_config() -> dict:
    from carhunt.config import load_config
    return load_config()


@pytest.fixture()
def ro_db():
    """Read-only connection to the live database (private tests only)."""
    import sqlite3
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'carhunt.db'}?mode=ro", uri=True, timeout=60)
    con.row_factory = sqlite3.Row
    yield con
    con.close()
