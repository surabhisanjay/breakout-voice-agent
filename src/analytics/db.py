import sqlite3
import os
from pathlib import Path

DB_PATH = Path("/Users/chandrikasanjay/breakout-voice-agent/memory/analytics.db")

def get_db_connection() -> sqlite3.Connection:
    """Returns a thread-safe connection to the SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Runs the schema.sql file to initialize database tables."""
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Analytics schema not found at {schema_path}")
    
    conn = get_db_connection()
    try:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
