"""Durable account budget shared by all trusted workers using this private state."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from carla_agentic_toolkit.managed_jev_config import TOKEN_RESERVATION


class PersistentAccountBudget:
    """Atomically charge attempts across processes; starting a run never resets limits."""

    def __init__(
        self, state_root: Path, max_requests: int = 200, max_tokens: int = 200 * TOKEN_RESERVATION
    ) -> None:
        """Create private counters once; existing limits survive all new workers."""
        self._path = state_root / "jev-account.sqlite3"
        descriptor = os.open(
            self._path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        os.close(descriptor)
        with closing(sqlite3.connect(self._path, timeout=0.1)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, "
                "requests INTEGER NOT NULL, tokens INTEGER NOT NULL)",
            )
            connection.execute(
                "INSERT OR IGNORE INTO budget VALUES (1, ?, ?)", (max_requests, max_tokens)
            )

    def reserve(self) -> bool:
        """Commit the full reservation before a network attempt, failing closed on contention."""
        try:
            with closing(sqlite3.connect(self._path, timeout=0.1)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                result = connection.execute(
                    "UPDATE budget SET requests=requests-1, tokens=tokens-? "
                    "WHERE id=1 AND requests>0 AND tokens>=?",
                    (TOKEN_RESERVATION, TOKEN_RESERVATION),
                )
                return result.rowcount == 1
        except sqlite3.DatabaseError:
            return False
