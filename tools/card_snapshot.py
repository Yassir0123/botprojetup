import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_execute_query_with_retry = None
_close_pool = None


STAT_FIELDS = [
    "base_attack",
    "base_health",
    "base_defence",
    "base_speed",
    "attack_stat",
    "health_stat",
    "defence_stat",
    "speed_stat",
    "power_stat",
    "current_health",
]

POINT_FIELDS = [
    "current_statpoints",
    "used_statpoints",
    "current_attackpoints",
    "current_healthpoints",
    "current_defencepoints",
    "current_speedpoints",
    "used_attackpoints",
    "used_healthpoints",
    "used_defencepoints",
    "used_speedpoints",
    "level_attackpoints",
    "level_healthpoints",
    "level_defencepoints",
    "level_speedpoints",
    "current_cap_attack",
    "current_cap_health",
    "current_cap_defence",
    "current_cap_speed",
]

EQUIPMENT_FIELDS = [
    "weapon_id",
    "armor_id",
    "shield_id",
    "id_weaponhalloween",
    "id_armorhalloween",
    "id_shieldhalloween",
]

CHARM_FIELDS = [
    "charm_attack",
    "charm_health",
    "charm_defence",
    "charm_speed",
    "attack_count",
    "health_count",
    "defence_count",
    "speed_count",
    "id_enhancer",
    "fight_count",
]

LEVEL_FIELDS = [
    "current_level",
    "max_leveled",
    "current_xp",
    "xp",
    "evo",
]

CARD_TEMPLATE_FIELDS = [
    "card_id",
    "card_name",
    "card_rank",
    "type_card",
    "card_class_id",
    "card_class_name",
    "attack",
    "health",
    "defence",
    "speed",
    "power",
    "xp_cap",
    "attack_percentage",
    "health_percentage",
    "defence_percentage",
    "speed_percentage",
]


async def execute_query_with_retry(query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]] | None:
    global _execute_query_with_retry, _close_pool
    if _execute_query_with_retry is None:
        from utils.database import close_pool, execute_query_with_retry as db_execute_query_with_retry

        _execute_query_with_retry = db_execute_query_with_retry
        _close_pool = close_pool
    return await _execute_query_with_retry(query, params)


async def close_database_pool() -> None:
    if _close_pool is not None:
        await _close_pool()


def pick(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields if field in row}


def compact_row(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields}


async def get_user(args: argparse.Namespace) -> dict[str, Any]:
    if args.user_id is not None:
        result = await execute_query_with_retry(
            """
            SELECT user_id, user_authorid, current_team, team_count, team_cap
            FROM user
            WHERE user_id = %s
            """,
            (args.user_id,),
        )
    else:
        result = await execute_query_with_retry(
            """
            SELECT user_id, user_authorid, current_team, team_count, team_cap
            FROM user
            WHERE user_authorid = %s
            """,
            (str(args.discord_id),),
        )

    if not result:
        raise LookupError("User not found.")
    return result[0]


async def get_card(user_id: int, args: argparse.Namespace) -> dict[str, Any]:
    base_query = """
    SELECT uc.*, c.card_name, c.card_rank, c.type_card,
           c.card_class_id, cc.card_class_name,
           c.attack, c.health, c.defence, c.speed, c.power, c.xp_cap,
           c.attack_percentage, c.health_percentage, c.defence_percentage, c.speed_percentage
    FROM user_cards uc
    JOIN card c ON uc.card_id = c.card_id
    LEFT JOIN card_class cc ON c.card_class_id = cc.card_class_id
    WHERE uc.user_id = %s
    """
    params: tuple[Any, ...]
    if args.card_row_id is not None:
        query = base_query + " AND uc.id_ligneusercards = %s"
        params = (user_id, args.card_row_id)
    else:
        query = base_query + " AND uc.name LIKE %s ORDER BY uc.id_ligneusercards ASC"
        params = (user_id, f"%{args.card_name}%")

    result = await execute_query_with_retry(query, params)
    if not result:
        raise LookupError("Card not found for this user.")
    if len(result) > 1 and args.card_row_id is None:
        matches = [
            {
                "id_ligneusercards": row.get("id_ligneusercards"),
                "name": row.get("name"),
                "card_id": row.get("card_id"),
                "current_level": row.get("current_level"),
            }
            for row in result
        ]
        raise LookupError(
            "Multiple cards matched; rerun with --card-row-id. "
            + json.dumps(matches, ensure_ascii=False)
        )
    return result[0]


async def get_team_state(user: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    user_id = user["user_id"]
    current_team = user.get("current_team")
    card_id = card.get("card_id")

    teams_with_card = await execute_query_with_retry(
        """
        SELECT t.team_id, t.team_bg, tc.card_id
        FROM team_cards tc
        JOIN team t ON tc.team_id = t.team_id
        WHERE t.user_id = %s AND tc.card_id = %s
        ORDER BY t.team_id ASC
        """,
        (user_id, card_id),
    ) or []

    current_team_cards = []
    support_cards = []
    support_totals = {
        "flat_attack": 0,
        "flat_health": 0,
        "flat_defence": 0,
        "flat_speed": 0,
        "pct_attack": 0,
        "pct_health": 0,
        "pct_defence": 0,
        "pct_speed": 0,
    }

    if current_team is not None:
        current_team_cards = await execute_query_with_retry(
            """
            SELECT tc.card_id, uc.id_ligneusercards, uc.name, c.type_card,
                   uc.power_stat, uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
            JOIN card c ON uc.card_id = c.card_id
            WHERE tc.team_id = %s
            ORDER BY tc.card_id ASC, uc.id_ligneusercards ASC
            """,
            (user_id, current_team),
        ) or []

        support_cards = await execute_query_with_retry(
            """
            SELECT uc.id_ligneusercards, uc.card_id, uc.name,
                   c.attack, c.health, c.defence, c.speed,
                   c.attack_percentage, c.health_percentage, c.defence_percentage, c.speed_percentage,
                   uc.weapon_id, uc.armor_id, uc.shield_id,
                   uc.id_weaponhalloween, uc.id_armorhalloween, uc.id_shieldhalloween
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
            JOIN card c ON uc.card_id = c.card_id
            WHERE tc.team_id = %s AND c.type_card = 'Support'
            ORDER BY uc.id_ligneusercards ASC
            """,
            (user_id, current_team),
        ) or []

        for support in support_cards:
            support_totals["flat_attack"] += support.get("attack") or 0
            support_totals["flat_health"] += support.get("health") or 0
            support_totals["flat_defence"] += support.get("defence") or 0
            support_totals["flat_speed"] += support.get("speed") or 0
            support_totals["pct_attack"] += support.get("attack_percentage") or 0
            support_totals["pct_health"] += support.get("health_percentage") or 0
            support_totals["pct_defence"] += support.get("defence_percentage") or 0
            support_totals["pct_speed"] += support.get("speed_percentage") or 0

    return {
        "current_team": current_team,
        "team_count": user.get("team_count"),
        "team_cap": user.get("team_cap"),
        "selected_card_in_current_team": any(row.get("team_id") == current_team for row in teams_with_card),
        "teams_containing_same_card_id": teams_with_card,
        "current_team_cards": current_team_cards,
        "support_cards_in_current_team": support_cards,
        "support_totals_from_card_rows_only": support_totals,
        "note": "team_cards stores card_id, so duplicate owned copies of the same card_id cannot be distinguished here.",
    }


async def get_equipment_details(card: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    regular_map = {
        "weapon": card.get("weapon_id"),
        "armor": card.get("armor_id"),
        "shield": card.get("shield_id"),
    }
    halloween_map = {
        "weapon_halloween": card.get("id_weaponhalloween"),
        "armor_halloween": card.get("id_armorhalloween"),
        "shield_halloween": card.get("id_shieldhalloween"),
    }

    for label, weapon_id in regular_map.items():
        if not weapon_id:
            details[label] = None
            continue
        result = await execute_query_with_retry(
            """
            SELECT weapon_id, weapon_name, weapon_rank, halloween,
                   attack_boost, health_boost, defense_boost, speed_boost,
                   attack_percentage, health_percentage, defense_percentage, speed_percentage
            FROM weapon
            WHERE weapon_id = %s
            """,
            (weapon_id,),
        )
        details[label] = result[0] if result else {"weapon_id": weapon_id, "missing": True}

    for label, halloween_id in halloween_map.items():
        if not halloween_id:
            details[label] = None
            continue
        result = await execute_query_with_retry(
            """
            SELECT id_weaponhalloween, weapon_id, weapon_surname,
                   attack_boost, health_boost, defense_boost, speed_boost,
                   attack_percentage, health_percentage, defense_percentage, speed_percentage
            FROM weapon_halloween
            WHERE id_weaponhalloween = %s
            """,
            (halloween_id,),
        )
        details[label] = result[0] if result else {"id_weaponhalloween": halloween_id, "missing": True}

    return details


def build_stat_checks(card: dict[str, Any]) -> dict[str, Any]:
    attack = card.get("attack_stat") or 0
    health = card.get("health_stat") or 0
    defence = card.get("defence_stat") or 0
    speed = card.get("speed_stat") or 0
    calculated_power = attack + health + defence + speed
    return {
        "current_stats_sum": calculated_power,
        "stored_power_stat": card.get("power_stat"),
        "stored_power_matches_current_sum": card.get("power_stat") == calculated_power,
        "current_health_over_health_stat": (
            card.get("current_health") is not None
            and card.get("health_stat") is not None
            and card.get("current_health") > card.get("health_stat")
        ),
    }


async def snapshot(args: argparse.Namespace) -> dict[str, Any]:
    user = await get_user(args)
    card = await get_card(user["user_id"], args)
    team_state = await get_team_state(user, card)
    equipment_details = await get_equipment_details(card)

    return {
        "read_only": True,
        "input": {
            "discord_id": args.discord_id,
            "user_id": args.user_id,
            "card_row_id": args.card_row_id,
            "card_name": args.card_name,
        },
        "user": pick(user, ["user_id", "user_authorid", "current_team", "team_count", "team_cap"]),
        "card_identity": pick(
            card,
            [
                "id_ligneusercards",
                "user_id",
                "card_id",
                "name",
                "card_name",
                "card_rank",
                "type_card",
                "card_class_id",
                "card_class_name",
            ],
        ),
        "card_template_base": compact_row(card, CARD_TEMPLATE_FIELDS),
        "stored_base_stats": compact_row(card, ["base_attack", "base_health", "base_defence", "base_speed"]),
        "current_stats": compact_row(card, ["attack_stat", "health_stat", "defence_stat", "speed_stat", "current_health"]),
        "power": {
            "power_stat": card.get("power_stat"),
            "check": build_stat_checks(card),
        },
        "level_xp": compact_row(card, LEVEL_FIELDS),
        "statpoints": compact_row(card, POINT_FIELDS),
        "equipment_ids": compact_row(card, EQUIPMENT_FIELDS),
        "equipment_details": equipment_details,
        "charms_and_enhancer": compact_row(card, CHARM_FIELDS),
        "team_support_state": team_state,
        "raw_user_card_selected_fields": compact_row(card, STAT_FIELDS + POINT_FIELDS + EQUIPMENT_FIELDS + CHARM_FIELDS + LEVEL_FIELDS),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only card stat snapshot for verifying formulas and concurrency fixes."
    )
    user_group = parser.add_mutually_exclusive_group(required=True)
    user_group.add_argument("--discord-id", type=int, help="Discord author id / user_authorid.")
    user_group.add_argument("--user-id", type=int, help="Internal database user.user_id.")

    card_group = parser.add_mutually_exclusive_group(required=True)
    card_group.add_argument("--card-row-id", type=int, help="Exact user_cards.id_ligneusercards.")
    card_group.add_argument("--card-name", help="Card name search. Must match exactly one owned card row.")

    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of indented JSON.")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    try:
        data = await snapshot(args)
        if args.compact:
            print(json.dumps(data, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except LookupError as exc:
        print(f"Lookup error: {exc}", file=sys.stderr)
        return 2
    finally:
        await close_database_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
