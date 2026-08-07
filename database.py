from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
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

            -- Копии заявки в чатах админов: чтобы после решения убрать кнопки у ВСЕХ,
            -- а не только у того админа, который нажал.
            CREATE TABLE IF NOT EXISTS deposit_admin_msgs (
                deposit_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (deposit_id, chat_id, message_id)
            );

            -- Настройки, меняемые из админ-бота (сейчас — QR оплаты).
            -- QR лежит в БД, а не на диске: файл пришлось бы синхронизировать между
            -- процессами и переживать деплой, а тут он приезжает вместе с бэкапом app.db.
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value BLOB,
                meta TEXT,
                updated_at TEXT NOT NULL,
                rev INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id);
            CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposits(status);
            CREATE INDEX IF NOT EXISTS idx_deposits_user_confirmed
                ON deposits(user_id, id) WHERE status = 'confirmed';
            CREATE INDEX IF NOT EXISTS idx_admin_msgs_deposit
                ON deposit_admin_msgs(deposit_id);
            """
        )
        await db.commit()
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
    await _ensure_conn()


PAYMENT_QR_KEY = "payment_qr"


async def get_payment_qr() -> dict | None:
    """QR оплаты, загруженный админом. None — используется QR по умолчанию."""
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            "SELECT value, meta, updated_at FROM app_settings WHERE key = ?",
            (PAYMENT_QR_KEY,),
        )
        row = await cur.fetchone()
    if row is None or row["value"] is None:
        return None
    return {
        "data": bytes(row["value"]),
        "filename": row["meta"] or "payment.png",
        "updated_at": row["updated_at"],
    }


async def get_payment_qr_version() -> str | None:
    """Метка версии QR без чтения самих байтов — для кэша в qr_util.

    Счётчик, а не updated_at: с точностью до секунды две замены подряд дали бы
    одну и ту же метку, и второй процесс (REDIS_URL, несколько инстансов)
    продолжил бы отдавать старый QR.
    """
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            "SELECT rev FROM app_settings WHERE key = ? AND value IS NOT NULL",
            (PAYMENT_QR_KEY,),
        )
        row = await cur.fetchone()
    return str(row["rev"]) if row else None


async def set_payment_qr(data: bytes, filename: str) -> str:
    conn = await _ensure_conn()
    now = _utc_now()
    async with _db_lock:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, meta, updated_at, rev)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                meta = excluded.meta,
                updated_at = excluded.updated_at,
                rev = app_settings.rev + 1
            """,
            (PAYMENT_QR_KEY, data, filename, now),
        )
        await conn.commit()
    return now


async def clear_payment_qr() -> bool:
    """Вернуться к QR из .env / файла в репозитории. False — своего QR и не было.

    Строка не удаляется, а обнуляется: после DELETE счётчик rev начался бы заново,
    и чужой процесс с кэшем той же версии продолжил бы отдавать старую картинку.
    """
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            """
            UPDATE app_settings
            SET value = NULL, meta = NULL, updated_at = ?, rev = rev + 1
            WHERE key = ? AND value IS NOT NULL
            """,
            (_utc_now(), PAYMENT_QR_KEY),
        )
        await conn.commit()
        return cur.rowcount > 0


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
    user_id: int,
    receipt_file_id: str,
    receipt_chat_id: int,
    receipt_message_id: int,
) -> bool:
    """Прикрепить чек к своей заявке, ожидающей чек.

    Условия в WHERE не косметические: deposit_id приходит из FSM, который переживает
    рестарт (Redis). Без них пользователь мог приложить чек к чужой, просроченной или
    уже подтверждённой заявке. False — заявка не в том состоянии, чек не принят.
    """
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            """
            UPDATE deposits
            SET receipt_file_id = ?,
                receipt_chat_id = ?,
                receipt_message_id = ?,
                status = 'pending_review'
            WHERE id = ? AND user_id = ? AND status = 'awaiting_receipt'
            """,
            (receipt_file_id, receipt_chat_id, receipt_message_id, deposit_id, user_id),
        )
        await conn.commit()
        return cur.rowcount > 0


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


async def transition_deposit(deposit_id: int, *, expected: str, new: str) -> dict | None:
    """Сменить статус, только если он всё ещё `expected`. Иначе None.

    Атомарность важна: заявка приходит всем админам сразу, и двое могут нажать
    «Подтвердить» одновременно. Проверка «прочитали статус → записали новый» не
    защищала — пользователь получал два уведомления о зачислении.
    Возвращает строку заявки, если переход выполнил именно этот вызов.
    """
    conn = await _ensure_conn()
    async with _db_lock:
        confirmed = _utc_now() if new == "confirmed" else None
        cur = await conn.execute(
            """
            UPDATE deposits
            SET status = ?, confirmed_at = COALESCE(?, confirmed_at)
            WHERE id = ? AND status = ?
            """,
            (new, confirmed, deposit_id, expected),
        )
        if cur.rowcount == 0:
            await conn.commit()
            return None
        cur = await conn.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,))
        row = await cur.fetchone()
        await conn.commit()
        return dict(row) if row else None


async def expire_stale_deposits(older_than_sec: int) -> list[dict]:
    """Просрочить заявки без чека, «зависшие» после рестарта процесса.

    Таймеры живут в памяти, поэтому перезапуск оставлял заявки в awaiting_receipt
    навсегда. Вызывается при старте.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_sec)).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            """
            SELECT id, user_id FROM deposits
            WHERE status = 'awaiting_receipt' AND created_at < ?
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            await conn.execute(
                """
                UPDATE deposits SET status = 'expired_timeout'
                WHERE status = 'awaiting_receipt' AND created_at < ?
                """,
                (cutoff,),
            )
        await conn.commit()
        return rows


async def add_admin_message(deposit_id: int, chat_id: int, message_id: int) -> None:
    conn = await _ensure_conn()
    async with _db_lock:
        await conn.execute(
            """
            INSERT OR IGNORE INTO deposit_admin_msgs (deposit_id, chat_id, message_id)
            VALUES (?, ?, ?)
            """,
            (deposit_id, chat_id, message_id),
        )
        await conn.commit()


async def pop_admin_messages(deposit_id: int) -> list[dict]:
    """Забрать и удалить копии заявки в чатах админов."""
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            "SELECT chat_id, message_id FROM deposit_admin_msgs WHERE deposit_id = ?",
            (deposit_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        await conn.execute("DELETE FROM deposit_admin_msgs WHERE deposit_id = ?", (deposit_id,))
        await conn.commit()
        return rows


async def user_confirmed_count(user_id: int) -> int:
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM deposits WHERE user_id = ? AND status = 'confirmed'",
            (user_id,),
        )
        row = await cur.fetchone()
        return int(row[0])


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


async def admin_deposits_list(
    status: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> list[dict]:
    conn = await _ensure_conn()
    async with _db_lock:
        if status:
            cur = await conn.execute(
                """
                SELECT d.*, u.username, u.first_name
                FROM deposits d
                LEFT JOIN users u ON u.user_id = d.user_id
                WHERE d.status = ?
                ORDER BY d.id DESC
                LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            )
        else:
            cur = await conn.execute(
                """
                SELECT d.*, u.username, u.first_name
                FROM deposits d
                LEFT JOIN users u ON u.user_id = d.user_id
                ORDER BY d.id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def admin_deposits_count(status: str | None = None) -> int:
    conn = await _ensure_conn()
    async with _db_lock:
        if status:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM deposits WHERE status = ?",
                (status,),
            )
        else:
            cur = await conn.execute("SELECT COUNT(*) FROM deposits")
        row = await cur.fetchone()
        return int(row[0])


async def admin_deposits_stats() -> dict[str, dict]:
    conn = await _ensure_conn()
    async with _db_lock:
        cur = await conn.execute(
            """
            SELECT status, COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total
            FROM deposits
            GROUP BY status
            """
        )
        rows = await cur.fetchall()
        return {r["status"]: {"count": int(r["cnt"]), "total": float(r["total"])} for r in rows}
