"""Monitor process: `python -m monitors.evaluate`.

A single long-running asyncio process (its own docker-compose service). Every
BASE_TICK it re-reads notification_rules (hot-reloaded via the config store's
TTL cache) and evaluates each enabled rule whose per-rule cadence has elapsed.
Each evaluator returns the subjects currently satisfying its condition; the
edge+cooldown engine diffs those against notification_state and dispatches only
genuine transitions (0→1), staying silent until an entity resets (1→0), with an
optional per-rule cooldown re-arm. Each firing fans out to the subscribers of
its topic via the bot's channel.

Not a supervised stream — a plain process kept alive by compose
`restart: unless-stopped`. Heartbeats are written to ingestion_event_status so
the admin overview still shows it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import ch_status
import notification_config as nc
from .channels import TelegramChannel
from .evaluators import EVALUATORS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitors.evaluate] %(levelname)s %(message)s",
)
# httpx logs the full request URL at INFO — which for Telegram embeds the bot
# token (a secret). Quiet it so tokens never land in the container logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

STREAM_NAME = "notifications.monitor"
BASE_TICK_S = 10.0

# per-rule next-fire wall-clock (monotonic seconds); rules not present fire now.
_next_fire: dict[str, float] = {}
# bot → (token, TelegramChannel) so we rebuild only when the admin rotates a token
_channels: dict[str, tuple[str, TelegramChannel]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _channel_for(bot: str) -> TelegramChannel | None:
    token = nc.get_bot_token(bot)
    if not token:
        return None
    cached = _channels.get(bot)
    if cached and cached[0] == token:
        return cached[1]
    ch = TelegramChannel(token)
    _channels[bot] = (token, ch)
    return ch


def _topic_for(rule: dict, item: dict) -> str | None:
    """Which topic a firing routes to. User rules → the rule's own topic;
    admin rules → the static admin topic for the affected stream's group."""
    if rule["scope"] == "admin":
        grp = item.get("group")
        return f"admin:{grp}" if grp else None
    return rule.get("topic_id") or None


async def _dispatch(bot: str, topic_id: str, text: str) -> int:
    """Send `text` to every current subscriber of the topic. Returns count sent."""
    channel = _channel_for(bot)
    if channel is None:
        log.warning("no %s bot token configured; cannot dispatch", bot)
        return 0
    subs = nc.get_subscribers(bot, topic_id)
    sent = 0
    for s in subs:
        if await channel.send(s["chat_id"], text):
            sent += 1
    return sent


async def _evaluate_rule(rule: dict) -> int:
    """Evaluate one rule, apply edge+cooldown, dispatch. Returns messages sent."""
    evaluator = EVALUATORS.get(rule["kind"])
    if evaluator is None:
        return 0
    firing = await evaluator(rule)  # [{entity, message, group}]
    firing_by_entity = {it["entity"]: it for it in firing}

    prior = nc.get_states(rule["rule_id"])  # {entity: {state, last_fired_at}}
    bot = "admin" if rule["scope"] == "admin" else "user"
    cooldown_s = int(rule.get("cooldown_s", 0) or 0)
    now = _utcnow()
    sent_total = 0

    # entities currently true → decide fire vs hold
    for entity, item in firing_by_entity.items():
        pstate = prior.get(entity)
        was_true = bool(pstate and pstate["state"])
        should_fire = False
        if not was_true:
            should_fire = True  # 0→1 edge
        elif cooldown_s > 0:
            last = pstate.get("last_fired_at")
            if isinstance(last, datetime) and (now - last.replace(tzinfo=None)).total_seconds() >= cooldown_s:
                should_fire = True  # re-arm after cooldown, still true
        if should_fire:
            topic_id = _topic_for(rule, item)
            if topic_id:
                sent_total += await _dispatch(bot, topic_id, item["message"])
            nc.set_state(rule["rule_id"], entity, True, now)
        elif was_true and pstate.get("last_fired_at") is None:
            nc.set_state(rule["rule_id"], entity, True, now)

    # entities that were true but no longer firing → reset (1→0)
    for entity, pstate in prior.items():
        if pstate["state"] and entity not in firing_by_entity:
            last = pstate.get("last_fired_at") or now
            nc.set_state(rule["rule_id"], entity,
                         False, last.replace(tzinfo=None) if isinstance(last, datetime) else now)

    return sent_total


async def _tick() -> tuple[int, int, str | None]:
    """Evaluate all due rules. Returns (rules_evaluated, messages_sent, error)."""
    now_mono = time.monotonic()
    try:
        rules = nc.get_rules()  # enabled, cached
    except Exception as exc:  # noqa: BLE001
        return 0, 0, f"rules read: {exc}"
    evaluated = 0
    sent = 0
    err: str | None = None
    live_ids = set()
    for rule in rules:
        live_ids.add(rule["rule_id"])
        due = _next_fire.get(rule["rule_id"], 0.0)
        if now_mono < due:
            continue
        _next_fire[rule["rule_id"]] = now_mono + max(int(rule.get("cadence_s", 300) or 300), 15)
        try:
            sent += await _evaluate_rule(rule)
            evaluated += 1
        except Exception as exc:  # noqa: BLE001
            err = f"{rule['rule_id']}: {exc}"
            log.exception("rule %s evaluation failed", rule["rule_id"])
    # forget schedule entries for rules that disappeared (disabled/deleted)
    for rid in list(_next_fire):
        if rid not in live_ids:
            _next_fire.pop(rid, None)
    return evaluated, sent, err


async def main():
    log.info("monitors.evaluate up (base tick %.0fs)", BASE_TICK_S)
    try:
        nc.seed_defaults()
    except Exception as exc:  # noqa: BLE001
        log.warning("seed_defaults failed (will retry via ticks): %s", exc)
    await ch_status.bootstrap_counter(STREAM_NAME)
    while True:
        next_fire = time.monotonic() + BASE_TICK_S
        t0 = time.monotonic()
        evaluated, sent, err = await _tick()
        try:
            await ch_status.write_tick(
                STREAM_NAME, sent, error=err, duration_s=time.monotonic() - t0)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(max(0.0, next_fire - time.monotonic()))


if __name__ == "__main__":
    asyncio.run(main())
