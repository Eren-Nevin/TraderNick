"""Telegram bot listener: `python -m monitors.bot`.

One long-running asyncio process (its own docker-compose service) running two
independent getUpdates long-poll loops — one for the USER bot, one for the ADMIN
bot. It drives the interactive subscribe/unsubscribe menus and, for the admin
bot, gates access behind the NOTIFICATIONS_ADMIN_SECRET reply.

Subscriptions + auth are persisted in ClickHouse (via notification_config), so
the monitor process can fan out to a topic's current subscribers. Callback
handlers set ABSOLUTE state (subscribe / unsubscribe, not toggle) so a
reprocessed update on restart is idempotent — no offset table needed.
"""
from __future__ import annotations

import asyncio
import hmac
import logging

import config
import notification_config as nc
from .channels import TelegramChannel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitors.bot] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _build_menu(bot: str, chat_id: str) -> tuple[str, list[tuple[str, str]]]:
    """(text, [(label, callback_data)]) for a chat's topic menu."""
    topics = nc.get_topics(bot=bot, enabled_only=True)
    subscribed = nc.get_chat_subscriptions(bot, chat_id)
    if not topics:
        return ("No notification topics are available yet.", [])
    buttons: list[tuple[str, str]] = []
    for t in topics:
        is_sub = t["topic_id"] in subscribed
        mark = "✅" if is_sub else "☐"
        buttons.append((f"{mark} {t['title']}",
                        ("unsub:" if is_sub else "sub:") + t["topic_id"]))
    header = ("Admin alert topics" if bot == "admin" else "Notification topics")
    return (f"{header} — tap to subscribe / unsubscribe:", buttons)


async def _send_menu(channel: TelegramChannel, bot: str, chat_id: str):
    text, buttons = _build_menu(bot, chat_id)
    if buttons:
        await channel.send_menu(chat_id, text, buttons)
    else:
        await channel.send(chat_id, text)


async def _handle_message(bot: str, channel: TelegramChannel, msg: dict,
                          awaiting: set[str]):
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if not chat_id:
        return
    text = (msg.get("text") or "").strip()
    username = (msg.get("from", {}) or {}).get("username", "") or ""

    if bot == "admin":
        # Auth gate: an unauthed chat's next text is treated as the secret.
        if not nc.is_admin_authed(chat_id):
            if chat_id in awaiting and text and not text.startswith("/"):
                secret = config.NOTIFICATIONS_ADMIN_SECRET or ""
                if secret and hmac.compare_digest(text, secret):
                    nc.set_admin_authed(chat_id, True)
                    awaiting.discard(chat_id)
                    await channel.send(chat_id, "✅ Authenticated. You can now subscribe to admin alerts.")
                    await _send_menu(channel, bot, chat_id)
                else:
                    await channel.send(chat_id, "❌ Incorrect secret. Try again.")
                return
            awaiting.add(chat_id)
            await channel.send(
                chat_id,
                "🔒 This is the admin alerts bot. Reply with the admin secret to continue.")
            return

    # Authed (or user bot): any text / /start shows the menu.
    await _send_menu(channel, bot, chat_id)


async def _handle_callback(bot: str, channel: TelegramChannel, cq: dict,
                           _awaiting: set[str]):
    data = cq.get("data") or ""
    cq_id = cq.get("id") or ""
    msg = cq.get("message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    msg_id = msg.get("message_id")
    username = (cq.get("from", {}) or {}).get("username", "") or ""
    if not chat_id or ":" not in data:
        await channel.answer_callback(cq_id)
        return

    if bot == "admin" and not nc.is_admin_authed(chat_id):
        await channel.answer_callback(cq_id, "Not authorized.")
        return

    action, topic_id = data.split(":", 1)
    if action == "sub":
        nc.set_subscription(bot, topic_id, chat_id, True, username)
        await channel.answer_callback(cq_id, "Subscribed ✅")
    elif action == "unsub":
        nc.set_subscription(bot, topic_id, chat_id, False, username)
        await channel.answer_callback(cq_id, "Unsubscribed")
    else:
        await channel.answer_callback(cq_id)
        return
    # refresh the menu in place
    text, buttons = _build_menu(bot, chat_id)
    if msg_id and buttons:
        await channel.edit_menu(chat_id, msg_id, text, buttons)


async def _run_bot(bot: str):
    """One bot's long-poll loop. Survives a missing/rotated token (waits until
    the admin sets it) and any per-update error."""
    offset: int | None = None
    awaiting: set[str] = set()
    channel: TelegramChannel | None = None
    cur_token: str | None = None
    log.info("bot loop starting: %s", bot)
    while True:
        token = nc.get_bot_token(bot)
        if not token:
            await asyncio.sleep(15)
            continue
        if token != cur_token:
            if channel is not None:
                await channel.aclose()
            channel = TelegramChannel(token)
            cur_token = token
            offset = None  # fresh bot → let Telegram replay pending once
            log.info("bot %s: token configured, polling", bot)
        try:
            updates = await channel.get_updates(offset, timeout=25)
        except Exception as exc:  # noqa: BLE001
            log.warning("bot %s getUpdates error: %s", bot, exc)
            await asyncio.sleep(5)
            continue
        for u in updates:
            offset = int(u["update_id"]) + 1
            try:
                if "message" in u:
                    await _handle_message(bot, channel, u["message"], awaiting)
                elif "callback_query" in u:
                    await _handle_callback(bot, channel, u["callback_query"], awaiting)
            except Exception as exc:  # noqa: BLE001
                log.exception("bot %s update handling failed: %s", bot, exc)


async def main():
    log.info("monitors.bot up")
    try:
        nc.seed_defaults()
    except Exception as exc:  # noqa: BLE001
        log.warning("seed_defaults failed: %s", exc)
    await asyncio.gather(_run_bot("user"), _run_bot("admin"))


if __name__ == "__main__":
    asyncio.run(main())
