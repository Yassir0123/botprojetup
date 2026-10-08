from contextlib import asynccontextmanager
import functools
from typing import Any, Optional

import aiomysql

from utils.database import get_connection, release_connection, reset_transaction_cursor, set_transaction_cursor


class DbTransaction:
    def __init__(self, conn: Any, cursor: Any):
        self.conn = conn
        self.cursor = cursor

    async def execute(self, query: str, params: Optional[tuple] = None) -> int:
        return await self.cursor.execute(query, params)

    async def fetchall(self, query: str, params: Optional[tuple] = None) -> list[dict[str, Any]]:
        await self.cursor.execute(query, params)
        rows = await self.cursor.fetchall()
        return list(rows or [])

    async def fetchone(self, query: str, params: Optional[tuple] = None) -> Optional[dict[str, Any]]:
        await self.cursor.execute(query, params)
        return await self.cursor.fetchone()


class HandledTransactionRollback(Exception):
    """Rollback a command transaction after the command already notified the user."""


def raise_transaction_rollback():
    raise HandledTransactionRollback()


@asynccontextmanager
async def db_transaction():
    conn = await get_connection()
    if not conn:
        raise RuntimeError("Could not acquire database connection")

    try:
        await conn.begin()
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            token = set_transaction_cursor(cursor)
            try:
                yield DbTransaction(conn, cursor)
            finally:
                reset_transaction_cursor(token)
        await conn.commit()
    except Exception:
        try:
            await conn.rollback()
        finally:
            pass
        raise
    finally:
        await release_connection(conn)


@asynccontextmanager
async def db_advisory_lock(lock_name: str, timeout: int = 5):
    conn = await get_connection()
    if not conn:
        raise RuntimeError("Could not acquire database connection")

    acquired = False
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout))
            row = await cursor.fetchone()
            acquired = bool(row and row[0] == 1)

        if not acquired:
            raise TimeoutError(f"Could not acquire advisory lock: {lock_name}")

        yield
    finally:
        if conn:
            if acquired:
                try:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception:
                    pass
            await release_connection(conn)


def advisory_lock_for_context(prefix: str, timeout: int = 5):
    """Serialize a Discord command/view handler by ctx.author.id without adding schema."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            async with db_advisory_lock(f"{prefix}:{ctx.author.id}", timeout=timeout):
                return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator


def transaction_for_context():
    """Run a Discord command/view handler inside one database transaction."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            try:
                async with db_transaction():
                    return await func(self, ctx, *args, **kwargs)
            except HandledTransactionRollback:
                return None
        return wrapper
    return decorator
