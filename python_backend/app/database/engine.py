"""Database engine and session management.

One engine, one session factory, one place that knows about connection setup.

The binding is chosen by DATABASE_URL, so the same schema, migrations and
repositories run on SQLite (the default local deployment) or PostgreSQL
(a server deployment) without code changes.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_SQLITE_PATH = DATA_DIR / "mediguide.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_url() -> str:
    """Resolve the connection URL.

    Defaults to a local SQLite file so the app runs with no server to install;
    set DATABASE_URL to a postgresql+psycopg:// URL to run against PostgreSQL.
    """
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        return configured
    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


def is_sqlite(url: str | None = None) -> bool:
    return (url or database_url()).startswith("sqlite")


def get_engine() -> Engine:
    """Create the engine once and reuse it (connection pooling)."""
    global _engine
    if _engine is not None:
        return _engine

    url = database_url()
    if is_sqlite(url):
        # check_same_thread=False: the app serves requests from a thread pool and
        # sessions are short-lived and never shared between threads.
        _engine = create_engine(
            url, future=True, echo=_echo(),
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            # WAL lets readers run while a writer holds the lock, which is what
            # makes concurrent requests workable on SQLite.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            # Off by default in SQLite; without this the FKs are decorative.
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
    else:
        _engine = create_engine(
            url, future=True, echo=_echo(),
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            pool_pre_ping=True,   # drop connections a server closed behind our back
            pool_recycle=1800,
        )

    logger.info("Database engine ready (%s)", _engine.dialect.name)
    return _engine


def _echo() -> bool:
    return os.getenv("DB_ECHO", "").strip().lower() in {"1", "true", "yes"}


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transaction. Commits on success, rolls back on any exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine. Used by tests that repoint DATABASE_URL."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def healthcheck() -> dict:
    from sqlalchemy import text

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "dialect": get_engine().dialect.name}
    except Exception as exc:
        logger.exception("Database healthcheck failed")
        return {"status": "error", "error": type(exc).__name__, "message": str(exc)[:200]}
