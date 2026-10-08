from typing import Optional

from utils.database import execute_query_with_retry


_INVENTORY_TABLE_IDS = {
    "inventory_product": "product_id",
    "inventory_chest": "chest_id",
    "inventory_weapon": "weapon_id",
    "inventory_dungeonkey": "dungeonkey_id",
    "inventory_frags": "frag_id",
    "inventory_orbs": "orb_id",
    "inventory_pickaxe": "id_pickaxe",
    "inventory_skills": "skill_id",
    "inventory_skins": "skin_id",
}

_USER_CURRENCY_COLUMNS = {
    "won_currency",
    "gamble_currency",
    "candy_currency",
}


async def get_inventory_id_for_user(user_id: int, *, for_update: bool = True) -> Optional[int]:
    lock_clause = " FOR UPDATE" if for_update else ""
    rows = await execute_query_with_retry(
        f"SELECT inventory_id FROM inventory WHERE user_id = %s{lock_clause}",
        (user_id,)
    )
    if not rows:
        return None
    return rows[0]["inventory_id"]


async def add_inventory_quantity(table_name: str, inventory_id: int, item_id: int, quantity: int) -> None:
    id_column = _INVENTORY_TABLE_IDS.get(table_name)
    if id_column is None:
        raise ValueError(f"Unsupported inventory table: {table_name}")

    rows = await execute_query_with_retry(
        f"""
        SELECT quantiter
        FROM {table_name}
        WHERE inventory_id = %s AND {id_column} = %s
        FOR UPDATE
        """,
        (inventory_id, item_id)
    )
    if rows:
        await execute_query_with_retry(
            f"""
            UPDATE {table_name}
            SET quantiter = quantiter + %s
            WHERE inventory_id = %s AND {id_column} = %s
            """,
            (quantity, inventory_id, item_id)
        )
    else:
        await execute_query_with_retry(
            f"""
            INSERT INTO {table_name} (inventory_id, {id_column}, quantiter)
            VALUES (%s, %s, %s)
            """,
            (inventory_id, item_id, quantity)
        )


async def add_user_currency(user_id: int, column_name: str, amount: int) -> None:
    if column_name not in _USER_CURRENCY_COLUMNS:
        raise ValueError(f"Unsupported currency column: {column_name}")

    await execute_query_with_retry(
        f"UPDATE user SET {column_name} = {column_name} + %s WHERE user_id = %s",
        (amount, user_id)
    )
