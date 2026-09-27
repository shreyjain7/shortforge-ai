"""Engine/session management and schema migrations."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from shortforge.core.logging import get_logger

log = get_logger("db")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _configure_sqlite(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.close()


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=10,
        max_overflow=20,
    )
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def init_db(db_path: Path, *, migrate: bool = True) -> Engine:
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = make_engine(db_path)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    if migrate:
        run_migrations(_engine)
    return _engine


def run_migrations(engine: Engine) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    log.info("database schema is up to date")


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialised")
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("Database not initialised")
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on error."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
