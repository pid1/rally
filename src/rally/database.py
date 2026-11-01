import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = os.environ.get(
    "RALLY_DB_PATH",
    str(Path(__file__).resolve().parent.parent.parent / "rally.db"),
)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class AuditedSession(Session):
    """A session that writes its pending audit reads before it closes.

    A job that only reads (the generator assembling a summary, say) never
    commits, so its reads would otherwise vanish with the session. Requests
    don't need this — the audit middleware flushes their reads — but every
    ``SessionLocal()`` gets it, so no job can forget.
    """

    def close(self) -> None:
        from rally.audit.recorder import flush_job_reads

        flush_job_reads(self)
        super().close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AuditedSession)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database schema (creates tables only if they don't exist).

    This is safe to call on every startup - it won't overwrite or delete existing data.
    SQLAlchemy's create_all() is idempotent and only creates missing tables.

    The models are imported here because create_all() only knows the tables
    that have been registered on Base. Called on its own (as `resetdb`,
    `db-init` and the Docker entrypoint do), nothing else has imported them,
    and it would create an empty database file that the migrations then fail
    against.
    """
    import rally.models  # noqa: F401

    engine.dispose()
    Base.metadata.create_all(bind=engine)
