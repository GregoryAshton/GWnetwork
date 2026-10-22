from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import CONFIG
from .models import Base

_engine: Engine | None = None
_Session: sessionmaker | None = None


def engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(CONFIG.db_url, future=True)
        if _engine.dialect.name == "sqlite":
            @sa_event.listens_for(_engine, "connect")
            def _pragmas(dbapi_con, _):  # pragma: no cover
                cur = dbapi_con.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        _Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


# Columns added after their table first shipped. create_all() never alters an
# existing table, so each is added here if missing. Idempotent.
_ADDED_COLUMNS = [
    ("event_usage", "uses", "JSON NOT NULL DEFAULT '[]'"),
    ("paper", "fulltext_version", "INTEGER"),
    ("paper", "fulltext_version_date", "DATETIME"),
    ("paper", "fulltext_fetched_at", "DATETIME"),
    ("paper", "fulltext_converter", "VARCHAR(64)"),
    ("paper", "fulltext_issues", "JSON NOT NULL DEFAULT '[]'"),
    ("paper", "withdrawn", "BOOLEAN NOT NULL DEFAULT 0"),
    ("paper", "references_fetched_at", "DATETIME"),
    ("event_record", "detectors", "JSON"),
    ("event_record", "strain_detectors", "JSON"),
    ("event_record", "data_doi", "VARCHAR(128)"),
    ("event_record", "instruments", "VARCHAR(8)"),
    ("event_record", "instruments_source", "VARCHAR(32)"),
    ("event_view", "instruments", "VARCHAR(8)"),
    ("event_view", "instruments_basis", "VARCHAR(8)"),
]


def init_db() -> None:
    eng = engine()
    Base.metadata.create_all(eng)
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    with eng.begin() as con:
        for table, column, ddl in _ADDED_COLUMNS:
            if column not in {c["name"] for c in insp.get_columns(table)}:
                con.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


@contextmanager
def session() -> Iterator[Session]:
    engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
