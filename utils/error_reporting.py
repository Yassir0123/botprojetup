import traceback
from typing import Any

import discord

from utils.discord_safe import safe_send


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default) if obj is not None else default


def _short_text(value: Any, limit: int = 300) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _traceback_tail(error: BaseException, limit: int = 900) -> str:
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if len(tb) <= limit:
        return tb
    return "...\n" + tb[-limit:]


def compact_card_rows(cards: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    if not isinstance(cards, list):
        return []

    compact = []
    for card in cards[:limit]:
        if not isinstance(card, dict):
            continue
        compact.append({
            "card_id": card.get("card_id"),
            "name": card.get("card_name") or card.get("name"),
            "position": card.get("position"),
            "level": card.get("current_level"),
            "hp": card.get("current_health"),
            "max_hp": card.get("health_stat"),
            "power": card.get("power_stat"),
        })
    return compact


def compact_opponent_rows(opponents: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    if isinstance(opponents, dict):
        opponents = [opponents]
    if not isinstance(opponents, list):
        return []

    compact = []
    for opponent in opponents[:limit]:
        if not isinstance(opponent, dict):
            continue
        compact.append({
            "id": opponent.get("missioncards_id") or opponent.get("dungeon_id") or opponent.get("card_id"),
            "name": opponent.get("name") or opponent.get("card_name"),
            "rank": opponent.get("card_rank") or opponent.get("rank"),
            "round": opponent.get("round"),
            "hp": opponent.get("current_health"),
            "max_hp": opponent.get("health_stat"),
            "power": opponent.get("power_stat"),
        })
    return compact


def build_error_report(
    system_name: str,
    error: BaseException,
    *,
    ctx: Any = None,
    interaction: Any = None,
    extra: dict[str, Any] | None = None,
) -> str:
    channel = _safe_attr(interaction, "channel") or _safe_attr(ctx, "channel")
    guild = _safe_attr(interaction, "guild") or _safe_attr(ctx, "guild")
    user = _safe_attr(interaction, "user") or _safe_attr(ctx, "author")

    lines = [
        f"🚨 Error in {system_name}",
        "```",
        f"type: {type(error).__name__}",
        f"error: {_short_text(error, 500)}",
        f"channel: {_safe_attr(channel, 'name', 'unknown')} ({_safe_attr(channel, 'id', 'unknown')})",
        f"server: {_safe_attr(guild, 'name', 'unknown')} ({_safe_attr(guild, 'id', 'unknown')})",
        f"user: {_safe_attr(user, 'name', 'unknown')} ({_safe_attr(user, 'id', 'unknown')})",
    ]

    if extra:
        for key, value in extra.items():
            lines.append(f"{key}: {_short_text(value, 500)}")

    lines.extend([
        "```",
        "Traceback tail:",
        f"```python\n{_traceback_tail(error)}\n```",
    ])
    return "\n".join(lines)


async def send_support_error(
    bot: Any,
    channel_id: int,
    system_name: str,
    error: BaseException,
    *,
    ctx: Any = None,
    interaction: Any = None,
    extra: dict[str, Any] | None = None,
    mention: str | None = "<@818132161534230559>",
) -> bool:
    support_channel = bot.get_channel(channel_id)
    if not support_channel:
        return False

    report = build_error_report(system_name, error, ctx=ctx, interaction=interaction, extra=extra)
    if mention:
        report = f"{mention}\n{report}"

    if len(report) <= 2000:
        ok, _ = await safe_send(support_channel, report)
        return ok

    ok, _ = await safe_send(support_channel, report[:1990])
    return ok
