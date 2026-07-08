"""
core/events.py — Lightweight synchronous event bus.

Usage:
    from core.events import event_bus
    event_bus.subscribe("on_whisper_created", my_handler)
    event_bus.fire("on_whisper_created", whisper_id=wid, sender_id=uid)

All handlers are called synchronously in registration order.
Exceptions inside a handler are caught and logged so they never crash the bot.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


class EventBus:
    """Central publish/subscribe event dispatcher."""

    # Canonical event names
    ON_WHISPER_CREATED   = "on_whisper_created"
    ON_WHISPER_READ      = "on_whisper_read"
    ON_WHISPER_DELETED   = "on_whisper_deleted"
    ON_USER_JOIN         = "on_user_join"
    ON_USER_BANNED       = "on_user_banned"
    ON_USER_UNBANNED     = "on_user_unbanned"
    ON_REPORT_CREATED    = "on_report_created"
    ON_BROADCAST_SENT    = "on_broadcast_sent"
    ON_XP_AWARDED        = "on_xp_awarded"
    ON_LEVEL_UP          = "on_level_up"
    ON_ACHIEVEMENT       = "on_achievement"
    ON_BACKUP_CREATED    = "on_backup_created"
    ON_SPAM_DETECTED     = "on_spam_detected"

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable) -> None:
        """Register *handler* to be called when *event* fires."""
        self._handlers[event].append(handler)
        logger.debug(f"EventBus: subscribed {handler.__name__} → {event}")

    def unsubscribe(self, event: str, handler: Callable) -> None:
        try:
            self._handlers[event].remove(handler)
        except ValueError:
            pass

    def fire(self, event: str, **kwargs: Any) -> None:
        """Dispatch *event* to all subscribers."""
        for handler in self._handlers.get(event, []):
            try:
                handler(**kwargs)
            except Exception as exc:
                logger.error(
                    f"EventBus: handler {handler.__name__} raised on {event}: {exc}",
                    exc_info=True,
                )

    def subscribers(self, event: str) -> List[Callable]:
        return list(self._handlers.get(event, []))


# Singleton instance used throughout the project
event_bus = EventBus()
