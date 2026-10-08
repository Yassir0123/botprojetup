import logging
import functools
from typing import Any

from utils.database import execute_query_with_retry, get_connection, release_connection


USER_ACTIVITY_FLAGS = frozenset({
    "fight_current",
    "mission_current",
    "dungeon_current",
    "chapter_count",
})

USER_LOOKUP_COLUMNS = frozenset({
    "user_id",
    "user_authorid",
})

USER_ACTIVITY_LABELS = {
    "fight_current": "fight",
    "mission_current": "mission",
    "dungeon_current": "dungeon",
    "chapter_count": "chapter",
}

USER_ACTIVITY_TIMER_COLUMNS = frozenset({
    "fight_timer",
    "mission_timer",
    "dungeon_timer",
})

USER_STATUS_TABLES = frozenset({
    "market_session",
    "sell_status",
    "shop_status",
})


def _validate_user_activity_columns(flag_column: str, user_column: str) -> None:
    if flag_column not in USER_ACTIVITY_FLAGS:
        raise ValueError(f"Unsupported user activity flag: {flag_column}")
    if user_column not in USER_LOOKUP_COLUMNS:
        raise ValueError(f"Unsupported user lookup column: {user_column}")


def _validate_user_status_table(table_name: str) -> None:
    if table_name not in USER_STATUS_TABLES:
        raise ValueError(f"Unsupported user status table: {table_name}")


def _validate_user_timer_column(timer_column: str) -> None:
    if timer_column not in USER_ACTIVITY_TIMER_COLUMNS:
        raise ValueError(f"Unsupported user activity timer: {timer_column}")


async def _execute_user_activity_update(query: str, params: tuple[Any, ...]) -> int:
    conn = None
    try:
        conn = await get_connection()
        if not conn:
            return 0

        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute(query, params)
            return affected_rows or 0
    except Exception as exc:
        logging.error("User activity update failed: %s", exc)
        return 0
    finally:
        if conn:
            await release_connection(conn)


async def claim_user_activity(user_identifier: Any, flag_column: str, *, user_column: str = "user_id") -> bool:
    """Atomically mark an existing user activity flag as active if it is currently free."""
    _validate_user_activity_columns(flag_column, user_column)
    affected_rows = await _execute_user_activity_update(
        f"""
        UPDATE user
        SET {flag_column} = 1
        WHERE {user_column} = %s
          AND COALESCE({flag_column}, 0) = 0
        """,
        (user_identifier,),
    )
    return affected_rows == 1


async def release_user_activity(user_identifier: Any, flag_column: str, *, user_column: str = "user_id") -> bool:
    """Release an existing user activity flag without changing any other user data."""
    _validate_user_activity_columns(flag_column, user_column)
    affected_rows = await _execute_user_activity_update(
        f"""
        UPDATE user
        SET {flag_column} = 0
        WHERE {user_column} = %s
        """,
        (user_identifier,),
    )
    return affected_rows > 0


async def cleanup_stale_user_activity(
    user_identifier: Any,
    flag_column: str,
    timer_column: str,
    max_age_seconds: int,
    *,
    user_column: str = "user_id",
) -> bool:
    """Release an activity flag only when its existing timer proves it is stale."""
    _validate_user_activity_columns(flag_column, user_column)
    _validate_user_timer_column(timer_column)
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")

    affected_rows = await _execute_user_activity_update(
        f"""
        UPDATE user
        SET {flag_column} = 0
        WHERE {user_column} = %s
          AND COALESCE({flag_column}, 0) = 1
          AND {timer_column} IS NOT NULL
          AND TIMESTAMPDIFF(SECOND, {timer_column}, NOW()) >= %s
        """,
        (user_identifier, max_age_seconds),
    )
    return affected_rows > 0


async def claim_user_status_row(table_name: str, user_id: Any, *, timeout: int = 3) -> bool:
    """Atomically create a per-user status row in an existing status table."""
    _validate_user_status_table(table_name)
    conn = None
    lock_name = f"slbot:{table_name}:{user_id}"
    lock_acquired = False

    try:
        conn = await get_connection()
        if not conn:
            return False

        async with conn.cursor() as cursor:
            await cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout))
            lock_result = await cursor.fetchone()
            lock_acquired = bool(lock_result and lock_result[0] == 1)
            if not lock_acquired:
                return False

            await cursor.execute(
                f"SELECT 1 FROM {table_name} WHERE user_id = %s LIMIT 1",
                (user_id,),
            )
            if await cursor.fetchone():
                return False

            await cursor.execute(
                f"INSERT INTO {table_name} (user_id) VALUES (%s)",
                (user_id,),
            )
            return True
    except Exception as exc:
        logging.error("User status row claim failed for %s/%s: %s", table_name, user_id, exc)
        return False
    finally:
        if conn:
            if lock_acquired:
                try:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception as exc:
                    logging.error("Failed to release advisory lock %s: %s", lock_name, exc)
            await release_connection(conn)


async def create_trade_status_row(initiator_id: Any, target_id: Any, created_at: Any, *, timeout: int = 3) -> int | None:
    """Atomically create a trade_status row for two currently-free users."""
    conn = None
    lock_names = sorted([
        f"slbot:trade_status:{initiator_id}",
        f"slbot:trade_status:{target_id}",
    ])
    acquired_locks: list[str] = []

    try:
        conn = await get_connection()
        if not conn:
            return None

        async with conn.cursor() as cursor:
            for lock_name in lock_names:
                await cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout))
                lock_result = await cursor.fetchone()
                if not lock_result or lock_result[0] != 1:
                    return None
                acquired_locks.append(lock_name)

            await cursor.execute(
                """
                SELECT id_tradestatus FROM trade_status
                WHERE initiator_id IN (%s, %s)
                   OR target_id IN (%s, %s)
                LIMIT 1
                """,
                (initiator_id, target_id, initiator_id, target_id),
            )
            if await cursor.fetchone():
                return None

            await cursor.execute(
                """
                INSERT INTO trade_status (initiator_id, target_id, status, created_at)
                VALUES (%s, %s, 'pending', %s)
                """,
                (initiator_id, target_id, created_at),
            )
            return cursor.lastrowid
    except Exception as exc:
        logging.error("Trade status row creation failed for %s/%s: %s", initiator_id, target_id, exc)
        return None
    finally:
        if conn:
            for lock_name in reversed(acquired_locks):
                try:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception as exc:
                    logging.error("Failed to release advisory lock %s: %s", lock_name, exc)
            await release_connection(conn)


async def get_active_user_activities(user_identifier: Any, *, user_column: str = "user_id") -> list[str]:
    """Read the existing user activity flags and return the active ones."""
    if user_column not in USER_LOOKUP_COLUMNS:
        raise ValueError(f"Unsupported user lookup column: {user_column}")

    rows = await execute_query_with_retry(
        f"""
        SELECT fight_current, mission_current, dungeon_current, chapter_count
        FROM user
        WHERE {user_column} = %s
        """,
        (user_identifier,),
    )
    if not rows:
        return []

    row = rows[0]
    return [
        USER_ACTIVITY_LABELS[column]
        for column in USER_ACTIVITY_FLAGS
        if row.get(column, 0) == 1
    ]


def active_activity_message(lang: int, activities: list[str]) -> str:
    activity_text = ", ".join(activities) if activities else "another activity"
    if lang == 1:
        return "لا يمكنك تعديل البطاقات أو الفريق أثناء وجود نشاط قتالي قيد التنفيذ. يرجى إنهاؤه أولاً."
    if lang == 3:
        return "Vous ne pouvez pas modifier vos cartes ou votre équipe pendant une activité de combat. Terminez-la d'abord."
    return f"You cannot modify cards or teams while {activity_text} is active. Please finish it first."


def block_during_user_activity(get_lang_func=None):
    """Decorator for commands that mutate cards, teams, equipment, or inventory state."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            user_authorid = str(ctx.author.id)
            await cleanup_stale_user_activity(user_authorid, "fight_current", "fight_timer", 1800, user_column="user_authorid")
            await cleanup_stale_user_activity(user_authorid, "mission_current", "mission_timer", 1800, user_column="user_authorid")
            await cleanup_stale_user_activity(user_authorid, "dungeon_current", "dungeon_timer", 1800, user_column="user_authorid")
            activities = await get_active_user_activities(str(ctx.author.id), user_column="user_authorid")
            if activities:
                lang = 0
                if get_lang_func is not None:
                    try:
                        lang = await get_lang_func(ctx.author.id)
                    except Exception:
                        lang = 0
                await ctx.send(active_activity_message(lang, activities))
                return None
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator
