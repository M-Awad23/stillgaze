from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from datetime import UTC, datetime


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "stillgaze.sqlite3"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                manual_title INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat_created
                ON messages(chat_id, created_at);

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memories_updated
                ON memories(updated_at DESC);
            """
        )
        ensure_column(db, "messages", "tools_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(db, "messages", "sources_json", "TEXT NOT NULL DEFAULT '[]'")


def ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_chat(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "pinned": bool(row["pinned"]),
        "archived": bool(row["archived"]),
        "manual_title": bool(row["manual_title"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "role": row["role"],
        "content": row["content"],
        "tools": parse_json_list(row["tools_json"]) if "tools_json" in row.keys() else [],
        "sources": parse_json_list(row["sources_json"]) if "sources_json" in row.keys() else [],
        "created_at": row["created_at"],
    }


def parse_json_list(value: str | None) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def row_to_memory(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "content": row["content"],
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_chats() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM chats
            ORDER BY archived ASC, pinned DESC, updated_at DESC
            """
        ).fetchall()
        return [row_to_chat(row) for row in rows]


def get_chat(chat_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        return row_to_chat(row) if row else None


def create_chat(title: str = "New chat") -> dict:
    chat_id = str(uuid4())
    timestamp = now_iso()
    with connect() as db:
        db.execute(
            """
            INSERT INTO chats (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, title, timestamp, timestamp),
        )
    return get_chat(chat_id) or {}


def update_chat(
    chat_id: str,
    *,
    title: str | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
    manual_title: bool | None = None,
) -> dict | None:
    updates: list[str] = []
    values: list[object] = []
    if title is not None:
        updates.append("title = ?")
        values.append(title)
    if pinned is not None:
        updates.append("pinned = ?")
        values.append(int(pinned))
    if archived is not None:
        updates.append("archived = ?")
        values.append(int(archived))
        if archived:
            updates.append("pinned = ?")
            values.append(0)
    if manual_title is not None:
        updates.append("manual_title = ?")
        values.append(int(manual_title))

    if not updates:
        return get_chat(chat_id)

    updates.append("updated_at = ?")
    values.append(now_iso())
    values.append(chat_id)

    with connect() as db:
        db.execute(f"UPDATE chats SET {', '.join(updates)} WHERE id = ?", values)
    return get_chat(chat_id)


def delete_chat(chat_id: str) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return cursor.rowcount > 0


def list_messages(chat_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM messages
            WHERE chat_id = ?
            ORDER BY created_at ASC
            """,
            (chat_id,),
        ).fetchall()
        return [row_to_message(row) for row in rows]


def create_message(
    chat_id: str,
    role: str,
    content: str,
    tools: list[dict] | None = None,
    sources: list[dict] | None = None,
) -> dict | None:
    if get_chat(chat_id) is None:
        return None

    message_id = str(uuid4())
    timestamp = now_iso()
    with connect() as db:
        db.execute(
            """
            INSERT INTO messages (id, chat_id, role, content, tools_json, sources_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                chat_id,
                role,
                content,
                json.dumps(tools or []),
                json.dumps(sources or []),
                timestamp,
            ),
        )
        db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (timestamp, chat_id))

    with connect() as db:
        row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return row_to_message(row) if row else None


def list_memories(limit: int = 50) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM memories
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row_to_memory(row) for row in rows]


def create_memory(content: str, source: str = "user") -> dict:
    memory_id = str(uuid4())
    timestamp = now_iso()
    with connect() as db:
        db.execute(
            """
            INSERT INTO memories (id, content, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (memory_id, content, source, timestamp, timestamp),
        )
    with connect() as db:
        row = db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return row_to_memory(row)


def delete_memory(memory_id: str) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0
