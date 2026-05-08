from __future__ import annotations

import sqlite3
import os
from pathlib import Path


DB_PATH = Path(os.getenv("AGENT_DB_PATH", str(Path(__file__).resolve().parent / "app.db")))


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT,
                intent TEXT,
                status TEXT
            )
            """
        )
        connection.commit()
