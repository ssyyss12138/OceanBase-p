"""Business-state journal; narrative history is recalled from PowerContext, never this DB."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS players (
              player_hash TEXT PRIMARY KEY, generation TEXT NOT NULL,
              scope_id TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turns (
              id TEXT PRIMARY KEY, player_hash TEXT NOT NULL, payload_hash TEXT NOT NULL,
              expected_version INTEGER NOT NULL, input TEXT NOT NULL, prepared TEXT NOT NULL,
              proposal TEXT, next_state TEXT, receipt TEXT, source TEXT, memory TEXT,
              result TEXT, status TEXT NOT NULL DEFAULT 'prepared');
            CREATE INDEX IF NOT EXISTS player_turns ON turns(player_hash);
            """)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=35, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def player(self, player_hash):
        with self.db() as c:
            return c.execute("SELECT * FROM players WHERE player_hash=?", (player_hash,)).fetchone()

    def turn(self, turn_id):
        with self.db() as c:
            return c.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()

    def checkpoint(self, turn_id, column, value):
        if column not in ("source", "memory", "result"):
            raise ValueError("invalid checkpoint")
        with self.db() as c:
            c.execute(
                f"UPDATE turns SET {column}=? WHERE id=?", (json.dumps(value, ensure_ascii=False), turn_id)
            )
