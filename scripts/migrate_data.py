"""Migrate all data from SQLite to PostgreSQL using SQLAlchemy Core.

Usage: python scripts/migrate_data.py
"""
import json
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.engine import URL

# ---------- config ----------
SQLITE_PATH = "data/interview.db"
PG_DSN = "postgresql+asyncpg://postgres:postgres@localhost:5432/interview_db"

TABLE_ORDER = [
    "users",
    "problems",
    "metrics",
    "interviews",
    "interview_sessions",
    "scores",
]

DATETIME_COLS = {
    "users": {"created_at"},
    "problems": {"created_at"},
    "metrics": {"created_at"},
    "interviews": {"scheduled_at", "started_at", "ended_at", "created_at", "token_expires_at"},
    "interview_sessions": {"uploaded_at"},
    "scores": {"scored_at"},
}

JSON_COLS = {
    "problems": {"metric_ids"},
    "interview_sessions": {"proctoring_alerts"},
    "scores": {"scores", "red_flags"},
}

BOOL_COLS = {
    "metrics": {"is_custom"},
}


def convert_value(table, col, v):
    if v is None:
        return None
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, float):
        return v
    if col in BOOL_COLS.get(table, set()):
        return bool(int(v))
    if col in DATETIME_COLS.get(table, set()):
        raw = v.decode("utf-8") if isinstance(v, bytes) else str(v)
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    if col in JSON_COLS.get(table, set()):
        raw = v.decode("utf-8") if isinstance(v, bytes) else str(v)
        return json.loads(raw)
    s = v.decode("utf-8") if isinstance(v, bytes) else str(v)
    return s


def fetch_sqlite_rows(cursor, table):
    cursor.execute(f'SELECT * FROM "{table}"')
    col_names = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    return col_names, rows


def main():
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()

    pg_url = URL.create(
        "postgresql",
        username="postgres",
        password="postgres",
        host="localhost",
        port=5432,
        database="interview_db",
    )
    pg_engine = create_engine(pg_url)
    pg_metadata = MetaData()
    pg_metadata.reflect(bind=pg_engine)

    with pg_engine.begin() as pg_conn:
        for table in TABLE_ORDER:
            col_names, rows = fetch_sqlite_rows(cursor, table)
            print(f"Migrating {table} ({len(rows)} rows)...")
            if not rows:
                continue
            pg_table = Table(table, pg_metadata, autoload_with=pg_engine)
            for row in rows:
                data = {
                    col_names[i]: convert_value(table, col_names[i], row[i])
                    for i in range(len(col_names))
                }
                pg_conn.execute(pg_table.insert().values(data))
            print(f"  {table}: {len(rows)} rows migrated")

    pg_engine.dispose()
    sqlite_conn.close()
    print("Data migration complete.")


if __name__ == "__main__":
    main()
