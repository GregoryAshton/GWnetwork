from __future__ import annotations

import pytest


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Isolated SQLite database per test."""
    import gwnetwork.db as db
    from gwnetwork.config import CONFIG

    monkeypatch.setattr(CONFIG, "db_url", f"sqlite:///{tmp_path/'t.db'}")
    db._engine = None
    db._Session = None
    db.init_db()
    yield db
    db._engine = None
    db._Session = None
