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


def init_db() -> None:
    Base.metadata.create_all(engine())


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
