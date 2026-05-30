from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent / "data" / "app.db"

log = logging.getLogger(__name__)

# Один долгоживущий коннект + блокировка: меньше накладных расходов на open/close при 10k+ пользователей,
# записи сериализуются (нормально для SQLite WAL).
_db_lock = asyncio.Lock()
_db_conn: aiosqlite.Connection | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


async def _ensure_conn() -> aiosqlite.Connection:
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    async with _db_lock:
        if _db_conn is None:
            conn = await aiosqlite.connect(DB_PATH, timeout=60.0)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA busy_timeout=10000")
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA cache_size=-64000")
            await conn.execute("PRAGMA temp_store=MEMORY")
            await conn.execute("PRAGMA mmap_size=268435456")
            _db_conn = conn
            log.info("SQLite: persistent connection ready (WAL)")
        return _db_conn


async def close_db() -> None:
    """Закрыть пул при остановке процесса."""
    global _db_conn
    async with _db_lock:
        if _db_conn is not None:
            await _db_conn.close()
            _db_conn = None


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH, timeout=60.0) as db:
        await db.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bookmaker TEXT NOT NULL,
                account_id TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                receipt_file_id TEXT,
                receipt_chat_id INTEGER,
                receipt_message_id INTEGER,
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id);
            CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposits(status);
            CREATE INDEX IF NOT EXISTS idx_deposits_user_confirmed
                ON deposits(user_id, id) WHERE status = 'confirmed';
            """
        )
        await db.commit()
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
    await _ensure_conn()


async def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    conn = await _ensure_conn()
    async with _db_lock:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user_id, username, first_name, _utc_now()),
        )
        await conn.commit()


async def create_deposit(
    user_id: int,
    bookmaker: str,
    account_id: str,
    amount: float,
) -> int:
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            """
            INSERT INTO deposits (user_id, bookmaker, account_id, amount, status, created_at)
            VALUES (?, ?, ?, ?, 'awaiting_receipt', ?)
            """,
            (user_id, bookmaker, account_id, amount, _utc_now()),
        )
        await conn.commit()
        return int(cur.lastrowid)


async def attach_receipt(
    deposit_id: int,
    receipt_file_id: str,
    receipt_chat_id: int,
    receipt_message_id: int,
) -> None:
    conn = await _ensure_conn()
    async with _db_lock:
        await conn.execute(
            """
            UPDATE deposits
            SET receipt_file_id = ?,
                receipt_chat_id = ?,
                receipt_message_id = ?,
                status = 'pending_review'
            WHERE id = ?
            """,
            (receipt_file_id, receipt_chat_id, receipt_message_id, deposit_id),
        )
        await conn.commit()


async def get_deposit(deposit_id: int) -> dict | None:
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_deposit_status(deposit_id: int, status: str) -> None:
    conn = await _ensure_conn()
    async with _db_lock:
        confirmed = _utc_now() if status == "confirmed" else None
        await conn.execute(
            """
            UPDATE deposits SET status = ?, confirmed_at = COALESCE(?, confirmed_at)
            WHERE id = ?
            """,
            (status, confirmed, deposit_id),
        )
        await conn.commit()


async def user_history(user_id: int, limit: int = 20) -> list[dict]:
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            """
            SELECT id, bookmaker, account_id, amount, status, created_at, confirmed_at
            FROM deposits
            WHERE user_id = ? AND status = 'confirmed'
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
