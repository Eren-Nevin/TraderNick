"""Notification service worker processes.

Two standalone long-running processes (own docker-compose services, ingestion
image, command override — NOT supervised streams):

  monitors.evaluate — the cron-like monitor. Evaluates notification_rules on a
                      drift-corrected cadence, applies edge+cooldown, and fans
                      each firing out to a topic's subscribers via a channel.
  monitors.bot      — the interactive Telegram bot listener. Long-polls
                      getUpdates for the user + admin bots, drives the
                      subscribe/unsubscribe menus, and gates admin topics behind
                      the NOTIFICATIONS_ADMIN_SECRET auth step.

Extensibility: monitors.channels defines a Channel ABC + CHANNELS registry so a
future mobile-push channel is one new class, not a rewrite.
"""
