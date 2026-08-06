import asyncio
import inspect
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConditionResult:
    passed: bool
    reason: str = ""
    data: Optional[dict] = None
    condition_type: str = ""
    condition_id: str = ""
    requires_interaction: bool = False
    message: str = ""


class BaseCondition(ABC):
    name: str = ""
    description: str = ""

    @abstractmethod
    async def evaluate(self, whisper_id: str, user_id: int, **kwargs) -> ConditionResult:
        ...


class ConditionRegistry:
    def __init__(self):
        self._conditions: dict[str, BaseCondition] = {}

    def register(self, condition: BaseCondition) -> None:
        if condition.name in self._conditions:
            logger.warning("Condition %s already registered, overwriting", condition.name)
        self._conditions[condition.name] = condition

    def get(self, name: str) -> Optional[BaseCondition]:
        return self._conditions.get(name)

    def all(self) -> dict[str, BaseCondition]:
        return dict(self._conditions)

    def unregister(self, name: str) -> None:
        self._conditions.pop(name, None)

    def check_all(self, whisper: dict, user_id: int) -> list[ConditionResult]:
        conditions_data = whisper.get("conditions_data")
        if not conditions_data:
            return []
        if isinstance(conditions_data, str):
            try:
                conditions_data = json.loads(conditions_data)
            except (json.JSONDecodeError, TypeError):
                logger.warning("[COND] invalid conditions_data JSON for whisper_id=%s", whisper.get("whisper_id", "?"))
                return []
        if not conditions_data:
            return []

        results = []
        if isinstance(conditions_data, dict):
            for cond_name, config in conditions_data.items():
                handler = self.get(cond_name)
                if handler:
                    cfg = config or {}
                    cfg_dict = cfg if isinstance(cfg, dict) else {}
                    coro = handler.evaluate(
                        whisper_id=whisper["whisper_id"],
                        user_id=user_id,
                        **cfg_dict,
                    )
                    result = _resolve_coro(coro)
                    result.condition_type = cond_name
                    result.condition_id = cond_name
                    meta = getattr(result, "data", None) or {}
                    if isinstance(meta, dict) and meta.get("message"):
                        result.message = meta["message"]
                    results.append(result)
        elif isinstance(conditions_data, list):
            for config in conditions_data:
                if not isinstance(config, dict):
                    continue
                cond_type = config.get("type") or config.get("condition_name", "")
                if not cond_type:
                    continue
                handler = self.get(cond_type)
                if handler:
                    call_kwargs = {k: v for k, v in config.items() if k not in ("type", "id", "condition_name")}
                    coro = handler.evaluate(
                        whisper_id=whisper["whisper_id"],
                        user_id=user_id,
                        **call_kwargs,
                    )
                    result = _resolve_coro(coro)
                    result.condition_type = cond_type
                    result.condition_id = config.get("id", cond_type)
                    meta = getattr(result, "data", None) or {}
                    if isinstance(meta, dict) and meta.get("message"):
                        result.message = meta["message"]
                    results.append(result)
        return results


def _resolve_coro(coro):
    if inspect.iscoroutine(coro):
        try:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(coro)
            finally:
                loop.close()
                asyncio.set_event_loop(None)

        except Exception as e:
            logger.exception("[COND] coroutine execution failed: %s", e)
            return ConditionResult(
                passed=False,
                reason="condition_error"
            )

    return coro


registry = ConditionRegistry()


def discover_conditions():
    import importlib
    import pkgutil
    import conditions
    for importer, modname, ispkg in pkgutil.iter_modules(conditions.__path__):
        if modname == "__init__" or ispkg:
            continue
        try:
            module = importlib.import_module(f"conditions.{modname}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseCondition) and attr is not BaseCondition:
                    instance = attr()
                    if instance.name:
                        registry.register(instance)
                        logger.debug("Discovered condition: %s", instance.name)
        except Exception as e:
            logger.error("Failed to load condition module %s: %s", modname, e)


class ConditionUI:
    @staticmethod
    def render_interaction(call, bot, whisper: dict, condition_result: ConditionResult,
                           user_states: dict | None = None, user_id: int | None = None):
        logger.info(
            "[COND_UI] render_interaction condition_type=%s condition_id=%s whisper_id=%s",
            condition_result.condition_type,
            condition_result.condition_id,
            whisper.get("whisper_id", "?"),
        )
        msg = condition_result.message or f"⚠️ هذا الشرط يتطلب تفاعلاً: {condition_result.condition_type}"
        if call:
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        whisper_id = whisper.get("whisper_id", "")
        cond_type = condition_result.condition_type
        kb = InlineKeyboardMarkup()
        if cond_type == "multiple_choice":
            meta = getattr(condition_result, "data", None) or {}
            choices = meta.get("choices") or []
            for i, choice in enumerate(choices):
                kb.add(InlineKeyboardButton(
                    f"{i + 1}️⃣ {choice}",
                    callback_data=f"mc_pick:{whisper_id}:{i}",
                ))
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data=f"cond_cancel:{whisper_id}"))
        target_id = user_id
        if target_id is None and call:
            target_id = getattr(getattr(call, "from_user", None), "id", None)
        if target_id:
            try:
                bot.send_message(target_id, msg, reply_markup=kb)
            except Exception as e:
                logger.warning("[COND_UI] send_message failed: %s", e)
        if user_states is not None and target_id is not None:
            user_states[target_id] = {
                "action": "cond_answer",
                "whisper_id": whisper_id,
                "condition_type": cond_type,
            }


# ── Auto-discovery: populate the condition registry at import time ─────
discover_conditions()
