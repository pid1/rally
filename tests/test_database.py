"""Tests for init_db: called on its own, it creates every table."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from rally.database import engine

SRC = Path(__file__).resolve().parent.parent / "src"


def test_the_suite_cannot_reach_a_real_database():
    # A route that opens its own session skips the `get_db` override. On a
    # developer's machine that session used to be `rally.db`, so a test could
    # replace the live dashboard snapshot. `conftest.py` sets the path.
    assert engine.url.database == ":memory:"


def test_init_db_alone_creates_the_tables(tmp_path):
    # A fresh interpreter, because this process has imported the models
    # already and would pass whether or not init_db imports them itself.
    # `resetdb`, `db-init` and the Docker entrypoint all call it this way.
    db_path = tmp_path / "rally.db"
    env = {**os.environ, "RALLY_DB_PATH": str(db_path), "PYTHONPATH": str(SRC)}
    subprocess.run(
        [sys.executable, "-c", "from rally.database import init_db; init_db()"],
        env=env,
        check=True,
    )

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert {"todos", "family_members", "settings", "packing_list_days"} <= tables
