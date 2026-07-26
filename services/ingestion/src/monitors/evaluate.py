"""Monitor process: `python -m monitors.evaluate`.

A single long-running asyncio process (its own docker-compose service). It runs
on a **slot model**: it wakes at the start of every wall-clock minute (a "slot")
and runs every rule that is due for that slot — a rule with cadence C fires at
the slots where the wall clock aligns to C (1m every slot; 5m at :00/:05/…; 15m
at :00/:15/:30/:45; 1h at :00; …). The slot only sets the START time; a slot's
work runs as its own task, so:

  • a slow slot never delays the next one (consecutive slots overlap),
  • each rule's execution is capped at 5 min (memory/hang guard),
  • within a slot, lower-cadence (more time-sensitive) rules run first,
  • best-effort only — no catch-up/retry. If a rule fails or times out we drop
    the notification and (once per failure streak) send a short error notice to
    its topic so the owner can fix it fast.

Stateless kinds (price/positions alerts) dispatch their evaluator output
directly. Edge kinds (admin monitors) run the notification_state edge+cooldown
engine. Heartbeats go to ingestion_event_status so the admin overview shows it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import ch_status
import notification_config as nc
from .channels import TelegramChannel
from .evaluators import EVALUATORS, STATELESS_KINDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitors.evaluate] %(levelname)s %(message)s",
)
# httpx logs the full request URL at INFO — which for Telegram embeds the bot
# token (a secret). Quiet it so tokens never land in the container logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

STREAM_NAME = "notifications.monitor"
_RULE_MAX_S = 300  # hard cap for one rule's execution (5 min)

# bot → (token, TelegramChannel) so we rebuild only when the admin rotates a token
_channels: dict[str, tuple[str, TelegramChannel]] = {}
# rule_id → is-currently-erroring, so an error notice is sent ONCE per failure
# streak (not every slot) — in-memory, best-effort, reset on recovery/restart.
_rule_errored: dict[str, bool] = {}


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
    """Send `text` to every current subscriber of the topic, and record the
    fire (last-triggered time + message) regardless of subscriber count."""
    channel = _channel_for(bot)
    sent = 0
    if channel is None:
        log.warning("no %s bot token configured; recording fire without send", bot)
    else:
        for s in nc.get_subscribers(bot, topic_id):
            if await channel.send(s["chat_id"], text):
                sent += 1
    try:
        nc.record_fired(topic_id, text, sent)
    except Exception as exc:  # noqa: BLE001
        log.warning("record_fired(%s) failed: %s", topic_id, exc)
    return sent


# ── slot scheduling ─────────────────────────────────────────────────────────

def _alert_cadences(rule: dict) -> list[int]:
    """A price_alert's per-alert FIRING cadences (seconds). Each alert fires on
    its own cadence and measures the move over its (separate) window; cadence_s
    falls back to window_s for legacy alerts that predate the split."""
    out = []
    for a in (rule.get("params") or {}).get("alerts", []):
        c = int(a.get("cadence_s") or a.get("window_s") or 0)
        if c > 0:
            out.append(c)
    return out


def _rule_cadence_s(rule: dict) -> int:
    """The rule's slot cadence (seconds) — used both to decide due-ness and to
    order slot execution (smaller = more time-sensitive → runs first). A
    price_alert holds several alerts with their own cadences; the rule's cadence
    is the smallest, so it rides in the most time-sensitive tier."""
    if rule["kind"] == "price_alert":
        cs = _alert_cadences(rule)
        return min(cs) if cs else 60
    return max(int(rule.get("cadence_s") or 300), 60)


def _is_due(rule: dict, slot_epoch: int) -> bool:
    """Is this rule due at this slot? A cadence-C rule is due when the wall clock
    aligns to C. price_alert is due whenever ANY of its alerts' cadence aligns
    (the evaluator then fires just the aligned ones). Uses the SLOT time (not the
    execution time) so a late-running slot still counts as its slot."""
    if rule["kind"] == "price_alert":
        return any(slot_epoch % c < 60 for c in _alert_cadences(rule))
    return slot_epoch % _rule_cadence_s(rule) < 60


# ── evaluate + dispatch one rule ────────────────────────────────────────────

async def _evaluate_and_dispatch(rule: dict, slot_epoch: int, force: bool = False) -> int:
    """Run a rule's evaluator (given the slot's wall-clock epoch) and dispatch.
    Stateless kinds fan out directly; edge kinds run the edge+cooldown engine.
    `force` (manual trigger) makes price_alert ignore its per-alert cadence gate
    so a debug fire produces a report regardless of the wall clock."""
    evaluator = EVALUATORS.get(rule["kind"])
    if evaluator is None:
        return 0
    if force and rule["kind"] == "price_alert":
        firing = await evaluator(rule, slot_epoch, force=True)
    else:
        firing = await evaluator(rule, slot_epoch)  # [{entity, message, group}]

    if rule["kind"] in STATELESS_KINDS:
        bot = "admin" if rule["scope"] == "admin" else "user"
        sent = 0
        for item in firing:
            topic_id = _topic_for(rule, item)
            if topic_id:
                sent += await _dispatch(bot, topic_id, item["message"])
        return sent

    # ── edge + cooldown (admin monitors) ──
    firing_by_entity = {it["entity"]: it for it in firing}
    prior = nc.get_states(rule["rule_id"])
    bot = "admin" if rule["scope"] == "admin" else "user"
    cooldown_s = int(rule.get("cooldown_s", 0) or 0)
    now = _utcnow()
    sent_total = 0
    for entity, item in firing_by_entity.items():
        pstate = prior.get(entity)
        was_true = bool(pstate and pstate["state"])
        should_fire = False
        if not was_true:
            should_fire = True
        elif cooldown_s > 0:
            last = pstate.get("last_fired_at")
            if isinstance(last, datetime) and (now - last.replace(tzinfo=None)).total_seconds() >= cooldown_s:
                should_fire = True
        if should_fire:
            topic_id = _topic_for(rule, item)
            if topic_id:
                sent_total += await _dispatch(bot, topic_id, item["message"])
            nc.set_state(rule["rule_id"], entity, True, now)
        elif was_true and pstate.get("last_fired_at") is None:
            nc.set_state(rule["rule_id"], entity, True, now)
    for entity, pstate in prior.items():
        if pstate["state"] and entity not in firing_by_entity:
            last = pstate.get("last_fired_at") or now
            nc.set_state(rule["rule_id"], entity,
                         False, last.replace(tzinfo=None) if isinstance(last, datetime) else now)
    return sent_total


async def _dispatch_error(rule: dict, exc: BaseException) -> None:
    """Best-effort error notice to a user rule's topic (once per failure streak)
    so the owner can fix it fast. Admin rules have no single widget topic → skip."""
    if rule.get("scope") == "admin":
        return
    topic_id = rule.get("topic_id") or rule.get("rule_id")
    if not topic_id:
        return
    title = rule.get("title") or "Alert"
    reason = "timed out (>5 min)" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) \
        else f"error: {type(exc).__name__}"
    try:
        await _dispatch("user", topic_id, f"⚠️ {title}\nThis alert failed to run ({reason}) — skipped this cycle.")
    except Exception:  # noqa: BLE001
        pass


async def _run_rule(rule: dict, slot_epoch: int, force: bool = False) -> int:
    """Run one rule with a hard 5-min cap. On failure: drop the notification
    (no retry/catch-up) and, once per failure streak, send an error notice."""
    rid = rule.get("rule_id", "?")
    try:
        sent = await asyncio.wait_for(
            _evaluate_and_dispatch(rule, slot_epoch, force=force), timeout=_RULE_MAX_S)
        _rule_errored[rid] = False
        return sent
    except Exception as exc:  # noqa: BLE001  (incl. asyncio.TimeoutError)
        log.warning("rule %s (%s) failed: %s", rid, rule.get("kind"), exc)
        if not _rule_errored.get(rid):
            _rule_errored[rid] = True
            await _dispatch_error(rule, exc)
        return 0


async def _slot(slot_epoch: int, rules: list[dict]) -> None:
    """Execute all rules due at this slot, tier by tier in ascending cadence
    (lower cadence = more time-sensitive → dispatched first), concurrently
    within a tier. Runs as its own task so consecutive slots can overlap."""
    due = [r for r in rules if _is_due(r, slot_epoch)]
    if not due:
        return
    tiers: dict[int, list[dict]] = {}
    for r in due:
        tiers.setdefault(_rule_cadence_s(r), []).append(r)
    sent = 0
    for cad in sorted(tiers):
        results = await asyncio.gather(
            *[_run_rule(r, slot_epoch) for r in tiers[cad]], return_exceptions=True)
        sent += sum(x for x in results if isinstance(x, int))
    try:
        await ch_status.write_tick(STREAM_NAME, sent)
    except Exception:  # noqa: BLE001
        pass


# ── manual triggers (debug "trigger now") ──────────────────────────────────

_TRIGGER_POLL_S = 3.0  # how often to check for manual triggers


async def _trigger_poller() -> None:
    """Poll the triggers table and fire any requested rule IMMEDIATELY (force=True,
    bypassing the cadence). Independent of the slot loop → ~seconds of latency,
    for debugging. Starts at the current watermark so old rows aren't replayed."""
    try:
        since = nc.trigger_watermark()
    except Exception as exc:  # noqa: BLE001
        log.warning("trigger watermark failed: %s", exc)
        since = _utcnow()
    while True:
        await asyncio.sleep(_TRIGGER_POLL_S)
        try:
            pending = nc.read_pending_triggers(since)
        except Exception as exc:  # noqa: BLE001
            log.warning("trigger poll failed: %s", exc)
            continue
        if not pending:
            continue
        since = max(t for _, t in pending)
        for rule_id, _ in pending:
            rule = nc.get_rule(rule_id)
            if not rule:
                continue
            log.info("manual trigger: rule %s (%s)", rule_id, rule.get("kind"))
            asyncio.create_task(_run_rule(rule, int(time.time()), force=True))


async def main():
    log.info("monitors.evaluate up (slot model)")
    try:
        nc.seed_defaults()
    except Exception as exc:  # noqa: BLE001
        log.warning("seed_defaults failed (will retry via slots): %s", exc)
    await ch_status.bootstrap_counter(STREAM_NAME)
    asyncio.create_task(_trigger_poller())  # debug "trigger now" path
    inflight: set[asyncio.Task] = set()
    while True:
        # Wake at the start of each wall-clock minute — the slot boundary.
        await asyncio.sleep(60.0 - (time.time() % 60.0))
        slot_epoch = int(time.time())
        try:
            rules = nc.get_rules()  # enabled, cached
        except Exception as exc:  # noqa: BLE001
            log.warning("rules read failed: %s", exc)
            rules = []
        # Launch the slot as its own task so a long slot doesn't delay the next
        # (slots may overlap); each rule inside is independently capped at 5 min.
        task = asyncio.create_task(_slot(slot_epoch, rules))
        inflight.add(task)
        task.add_done_callback(inflight.discard)
        # A lightweight liveness heartbeat every minute (the slot task writes the
        # real sent-count when it finishes).
        try:
            await ch_status.write_tick(STREAM_NAME, 0, duration_s=0.0)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    asyncio.run(main())
