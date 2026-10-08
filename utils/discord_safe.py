import asyncio
import logging
import time
from typing import Any, Tuple

import aiohttp
import discord


logger = logging.getLogger(__name__)


TRANSIENT_STATUS_CODES = {500, 502, 503, 504}
_last_edit_by_message_id: dict[int, float] = {}


def is_transient_discord_error(error: BaseException) -> bool:
    """Return True for Discord/network failures that are reasonable to retry."""
    if isinstance(error, (discord.Forbidden, discord.NotFound)):
        return False

    if isinstance(error, discord.HTTPException):
        return getattr(error, "status", None) in TRANSIENT_STATUS_CODES

    if isinstance(error, aiohttp.ClientResponseError):
        return error.status in TRANSIENT_STATUS_CODES

    return isinstance(
        error,
        (
            aiohttp.ClientConnectorError,
            aiohttp.ClientOSError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ClientPayloadError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
            ConnectionResetError,
            OSError,
        ),
    )


async def _run_discord_call(label: str, call, *args, attempts: int = 3, **kwargs) -> Tuple[bool, Any]:
    """Run a Discord HTTP call with short retries for transient failures."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return True, await call(*args, **kwargs)
        except (discord.Forbidden, discord.NotFound) as error:
            logger.info("%s skipped: %s", label, error)
            return False, None
        except Exception as error:
            if not is_transient_discord_error(error):
                raise
            last_error = error
            if attempt >= attempts:
                break
            await asyncio.sleep(0.5 * attempt)

    logger.warning("%s failed after %s attempts: %s", label, attempts, last_error)
    return False, None


async def safe_send(target, *args, **kwargs) -> Tuple[bool, Any]:
    return await _run_discord_call("discord send", target.send, *args, **kwargs)


async def safe_edit(message, *args, min_interval: float | None = None, force: bool = False, **kwargs) -> Tuple[bool, Any]:
    if min_interval is not None and not force:
        message_id = getattr(message, "id", None)
        if message_id is not None:
            now = time.monotonic()
            last_edit = _last_edit_by_message_id.get(message_id, 0)
            if now - last_edit < min_interval:
                return True, None
            _last_edit_by_message_id[message_id] = now
            if len(_last_edit_by_message_id) > 5000:
                stale_before = now - 600
                for stored_message_id, edited_at in list(_last_edit_by_message_id.items()):
                    if edited_at < stale_before:
                        _last_edit_by_message_id.pop(stored_message_id, None)

    return await _run_discord_call("discord edit", message.edit, *args, **kwargs)


async def safe_defer(response, *args, **kwargs) -> Tuple[bool, Any]:
    return await _run_discord_call("discord defer", response.defer, *args, **kwargs)


async def safe_response_send(response, *args, **kwargs) -> Tuple[bool, Any]:
    return await _run_discord_call("discord response send", response.send_message, *args, **kwargs)


async def safe_response_edit(response, *args, **kwargs) -> Tuple[bool, Any]:
    return await _run_discord_call("discord response edit", response.edit_message, *args, **kwargs)


async def safe_followup_send(followup, *args, **kwargs) -> Tuple[bool, Any]:
    return await _run_discord_call("discord followup send", followup.send, *args, **kwargs)


async def safe_remove_attachments(message, attachments) -> Tuple[bool, Any]:
    return await _run_discord_call(
        "discord remove attachments",
        message.remove_attachments,
        attachments,
    )
