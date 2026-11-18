"""SQLite 连接和初始化。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path | None = None) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with connect(path) as conn:
        conn.executescript(schema)
        # 轻量迁移：已有数据库没有新列时补齐。
        for table, column, decl in (
            ("observations", "sea_temp_c", "REAL"),
            ("station_current", "sea_temp_c", "REAL"),
            ("station_current", "sea_temp_source", "TEXT"),
        ):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
