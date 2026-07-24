"""Notification delivery channels.

`Channel` is the extensibility seam: the monitor only needs `send(target, text)`
to fan a firing out to a subscriber. Phase 1 ships `TelegramChannel`; a future
`PushChannel` (mobile) is one more class + one `CHANNELS` entry, with no change
to the monitor.

TelegramChannel also carries the *interactive* surface the bot listener needs
(getUpdates long-poll + inline-keyboard menus). Those are Telegram-specific and
live on the concrete class, not the ABC — a push channel manages subscriptions
differently.
"""
from __future__ import annotations

import abc
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class Channel(abc.ABC):
    """A way to deliver a notification. Constructed with whatever credential the
    channel needs (a Telegram bot token today)."""

    name: str = "channel"

    @abc.abstractmethod
    async def send(self, target: str, text: str) -> bool:
        """Deliver `text` to `target` (a Telegram chat_id today). Returns True on
        success. Must never raise — log and return False so one bad recipient
        doesn't abort a fan-out."""
        raise NotImplementedError


class TelegramChannel(Channel):
    name = "telegram"
    API = "https://api.telegram.org"

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0))
        return self._client

    async def aclose(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call(self, method: str, payload: dict) -> dict | None:
        """POST to a Telegram Bot API method. Returns the parsed `result` on
        ok=true, else None (logged)."""
        url = f"{self.API}/bot{self.token}/{method}"
        try:
            resp = await (await self._http()).post(url, json=payload)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("telegram %s failed: %s", method, exc)
            return None
        if not data.get("ok"):
            log.warning("telegram %s rejected: %s", method, data.get("description"))
            return None
        return data.get("result")

    # ── Channel.send ───────────────────────────────────────────────────────
    async def send(self, target: str, text: str) -> bool:
        res = await self._call("sendMessage", {
            "chat_id": target, "text": text, "disable_web_page_preview": True,
        })
        return res is not None

    # ── interactive surface (bot listener) ──────────────────────────────────
    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        """Long-poll for updates. Returns the raw update list (possibly empty)."""
        payload: dict[str, Any] = {"timeout": timeout,
                                   "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        # long-poll needs a read timeout longer than the server-side `timeout`.
        url = f"{self.API}/bot{self.token}/getUpdates"
        try:
            resp = await (await self._http()).post(
                url, json=payload, timeout=httpx.Timeout(timeout + 10))
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("telegram getUpdates failed: %s", exc)
            return []
        if not data.get("ok"):
            log.warning("telegram getUpdates rejected: %s", data.get("description"))
            return []
        return data.get("result") or []

    async def send_menu(self, chat_id: str, text: str,
                        buttons: list[tuple[str, str]]) -> dict | None:
        """Send `text` with an inline keyboard. `buttons` is a list of
        (label, callback_data); one button per row."""
        kb = [[{"text": label, "callback_data": data}] for label, data in buttons]
        return await self._call("sendMessage", {
            "chat_id": chat_id, "text": text,
            "reply_markup": {"inline_keyboard": kb},
        })

    async def edit_menu(self, chat_id: str, message_id: int, text: str,
                        buttons: list[tuple[str, str]]) -> dict | None:
        kb = [[{"text": label, "callback_data": data}] for label, data in buttons]
        return await self._call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text,
            "reply_markup": {"inline_keyboard": kb},
        })

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery",
                         {"callback_query_id": callback_id, "text": text})


# Channel registry — extend here for new delivery methods (mobile push, etc.).
# Keyed by channel name; value is the factory (token/credential → Channel).
CHANNELS: dict[str, Any] = {
    "telegram": TelegramChannel,
}
