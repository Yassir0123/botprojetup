import argparse
import json
import sys
from pathlib import Path
from typing import Any


STAT_PATHS = [
    ("current_stats.attack_stat", "current_stats.attack_stat"),
    ("current_stats.health_stat", "current_stats.health_stat"),
    ("current_stats.defence_stat", "current_stats.defence_stat"),
    ("current_stats.speed_stat", "current_stats.speed_stat"),
    ("current_stats.current_health", "current_stats.current_health"),
    ("power.power_stat", "power.power_stat"),
    ("level_xp.current_level", "level_xp.current_level"),
    ("level_xp.current_xp", "level_xp.current_xp"),
    ("level_xp.xp", "level_xp.xp"),
    ("statpoints.current_statpoints", "statpoints.current_statpoints"),
    ("statpoints.used_statpoints", "statpoints.used_statpoints"),
    ("statpoints.current_attackpoints", "statpoints.current_attackpoints"),
    ("statpoints.current_healthpoints", "statpoints.current_healthpoints"),
    ("statpoints.current_defencepoints", "statpoints.current_defencepoints"),
    ("statpoints.current_speedpoints", "statpoints.current_speedpoints"),
    ("statpoints.used_attackpoints", "statpoints.used_attackpoints"),
    ("statpoints.used_healthpoints", "statpoints.used_healthpoints"),
    ("statpoints.used_defencepoints", "statpoints.used_defencepoints"),
    ("statpoints.used_speedpoints", "statpoints.used_speedpoints"),
    ("equipment_ids.weapon_id", "equipment_ids.weapon_id"),
    ("equipment_ids.armor_id", "equipment_ids.armor_id"),
    ("equipment_ids.shield_id", "equipment_ids.shield_id"),
    ("equipment_ids.id_weaponhalloween", "equipment_ids.id_weaponhalloween"),
    ("equipment_ids.id_armorhalloween", "equipment_ids.id_armorhalloween"),
    ("equipment_ids.id_shieldhalloween", "equipment_ids.id_shieldhalloween"),
    ("charms_and_enhancer.charm_attack", "charms_and_enhancer.charm_attack"),
    ("charms_and_enhancer.charm_health", "charms_and_enhancer.charm_health"),
    ("charms_and_enhancer.charm_defence", "charms_and_enhancer.charm_defence"),
    ("charms_and_enhancer.charm_speed", "charms_and_enhancer.charm_speed"),
    ("charms_and_enhancer.id_enhancer", "charms_and_enhancer.id_enhancer"),
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object.")
    return data


def get_path(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def identity(snapshot: dict[str, Any]) -> dict[str, Any]:
    card = snapshot.get("card_identity", {})
    user = snapshot.get("user", {})
    return {
        "user_id": user.get("user_id"),
        "user_authorid": user.get("user_authorid"),
        "id_ligneusercards": card.get("id_ligneusercards"),
        "card_id": card.get("card_id"),
        "name": card.get("name"),
        "card_name": card.get("card_name"),
    }


def calculate_warnings(label: str, snapshot: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    power_check = get_path(snapshot, "power.check")
    if isinstance(power_check, dict):
        if power_check.get("stored_power_matches_current_sum") is False:
            warnings.append(f"{label}: power_stat does not match attack+health+defence+speed.")
        if power_check.get("current_health_over_health_stat") is True:
            warnings.append(f"{label}: current_health is above health_stat.")

    level = get_path(snapshot, "level_xp.current_level")
    max_level = get_path(snapshot, "level_xp.max_leveled")
    if isinstance(level, int) and isinstance(max_level, int) and level > max_level:
        warnings.append(f"{label}: current_level is above max_leveled.")

    current_health = get_path(snapshot, "current_stats.current_health")
    health_stat = get_path(snapshot, "current_stats.health_stat")
    if isinstance(current_health, int) and current_health < 0:
        warnings.append(f"{label}: current_health is negative.")
    if isinstance(current_health, int) and isinstance(health_stat, int) and current_health > health_stat:
        warnings.append(f"{label}: current_health is above health_stat.")

    return warnings


def format_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def compare(before: dict[str, Any], after: dict[str, Any], show_unchanged: bool) -> tuple[list[str], list[str]]:
    warnings = calculate_warnings("before", before) + calculate_warnings("after", after)

    before_identity = identity(before)
    after_identity = identity(after)
    if before_identity.get("id_ligneusercards") != after_identity.get("id_ligneusercards"):
        warnings.append("Snapshots do not point to the same user_cards row.")
    if before_identity.get("user_id") != after_identity.get("user_id"):
        warnings.append("Snapshots do not point to the same user row.")

    lines = [
        "Card Snapshot Compare",
        f"Before: {format_value(before_identity)}",
        f"After:  {format_value(after_identity)}",
        "",
        "Changed Fields:",
    ]

    changed = 0
    for label, dotted_path in STAT_PATHS:
        old_value = get_path(before, dotted_path)
        new_value = get_path(after, dotted_path)
        if old_value != new_value:
            changed += 1
            lines.append(f"- {label}: {format_value(old_value)} -> {format_value(new_value)}")
        elif show_unchanged:
            lines.append(f"- {label}: unchanged ({format_value(old_value)})")

    if changed == 0 and not show_unchanged:
        lines.append("- none")

    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)

    return lines, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only comparer for two tools/card_snapshot.py JSON outputs."
    )
    parser.add_argument("before", type=Path, help="Snapshot JSON captured before a command.")
    parser.add_argument("after", type=Path, help="Snapshot JSON captured after a command.")
    parser.add_argument("--show-unchanged", action="store_true", help="Include unchanged tracked fields.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        before = load_json(args.before)
        after = load_json(args.after)
        lines, warnings = compare(before, after, args.show_unchanged)
    except Exception as exc:
        print(f"Snapshot compare failed: {exc}", file=sys.stderr)
        return 2

    print("\n".join(lines))
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
