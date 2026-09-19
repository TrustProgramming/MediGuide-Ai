"""Test isolation.

Each test gets its own SQLite database file, created from the SQLAlchemy
metadata, so tests never touch the real one and never see each other's rows.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    from app.database import engine as engine_module
    from app.database.models import Base

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'test.db').as_posix()}")
    engine_module.reset_engine()
    Base.metadata.create_all(engine_module.get_engine())

    yield

    engine_module.reset_engine()
