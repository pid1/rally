"""Migration 029: every family member ends up on the closed palette.

The migration is what makes the closed set true of data that predates it. It
runs on container start, so the properties that matter are that it is total
(no legacy value survives), that it does not collide two members onto one
color, and that running it again — every restart does — changes nothing.
"""

import importlib.util
import pathlib
import sqlite3

import pytest

from rally import member_colors

SCHEMA = """
CREATE TABLE family_members (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    color VARCHAR(7) NOT NULL
)
"""


def _load_migration():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "migrations"
        / "migrate_029_member_color_palette.py"
    )
    spec = importlib.util.spec_from_file_location("migrate_029", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "rally.db"
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setenv("RALLY_DB_PATH", str(db_path))
    return db_path


def _seed(db_path, rows):
    conn = sqlite3.connect(db_path)
    conn.executemany("INSERT INTO family_members (id, name, color) VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _colors(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [row[0] for row in conn.execute("SELECT color FROM family_members ORDER BY id")]
    finally:
        conn.close()


def test_the_old_default_becomes_five_distinct_palette_colors(db):
    """The bug this feature exists to fix: four members, four identical dots."""
    _seed(db, [(i, f"M{i}", "#333333") for i in range(1, 5)])

    assert _load_migration().migrate() is True

    colors = _colors(db)
    assert colors == list(member_colors.MEMBER_COLORS[:4])
    assert len(set(colors)) == 4


def test_legacy_seed_colors_are_all_replaced(db):
    _seed(
        db,
        [
            (1, "Mom", "#4a6741"),
            (2, "Dad", "#5b4a8a"),
            (3, "Emma", "#8a4a5b"),
            (4, "Jake", "#4a708a"),
        ],
    )

    assert _load_migration().migrate() is True

    for color in _colors(db):
        assert member_colors.is_palette_color(color)


def test_a_member_already_on_a_palette_color_keeps_it_and_holds_its_slot(db):
    """Assigning around them would hand their color to somebody else."""
    kept = member_colors.PALETTE[1].value
    _seed(db, [(1, "Mom", "#4a6741"), (2, "Dad", kept), (3, "Emma", "#8a4a5b")])

    assert _load_migration().migrate() is True

    colors = _colors(db)
    assert colors[1] == kept
    assert len(set(colors)) == 3


def test_running_twice_changes_nothing(db):
    _seed(db, [(i, f"M{i}", "#333333") for i in range(1, 4)])
    migration = _load_migration()

    assert migration.migrate() is True
    after_first = _colors(db)
    assert migration.migrate() is True

    assert _colors(db) == after_first


def test_a_color_chosen_after_the_migration_survives_the_next_restart(db):
    """A container restart re-runs every migration; it must not undo a choice."""
    _seed(db, [(1, "Mom", "#333333"), (2, "Dad", "#333333")])
    migration = _load_migration()
    assert migration.migrate() is True

    chosen = member_colors.PALETTE[4].value
    conn = sqlite3.connect(db)
    conn.execute("UPDATE family_members SET color = ? WHERE id = 1", (chosen,))
    conn.commit()
    conn.close()

    assert migration.migrate() is True

    assert _colors(db)[0] == chosen


def test_more_members_than_the_palette_cycles(db):
    _seed(db, [(i, f"M{i}", "#333333") for i in range(1, 8)])

    assert _load_migration().migrate() is True

    colors = _colors(db)
    assert len(colors) == 7
    assert colors[5] == member_colors.PALETTE[0].value
    assert colors[6] == member_colors.PALETTE[1].value


def test_an_empty_or_absent_table_is_not_an_error(db, tmp_path, monkeypatch):
    assert _load_migration().migrate() is True  # empty table

    bare = tmp_path / "bare.db"
    sqlite3.connect(bare).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(bare))
    assert _load_migration().migrate() is True  # no family_members table
