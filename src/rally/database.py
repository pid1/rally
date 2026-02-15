import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = os.environ.get(
    "RALLY_DB_PATH",
    str(Path(__file__).resolve().parent.parent.parent / "rally.db"),
)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
    """
    engine.dispose()
    Base.metadata.create_all(bind=engine)
