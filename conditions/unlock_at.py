import datetime
import logging
import time

from conditions import BaseCondition, ConditionResult

logger = logging.getLogger(__name__)

DURATIONS = {
    "5min": 5 * 60,
    "30min": 30 * 60,
    "1h": 60 * 60,
    "6h": 6 * 60 * 60,
    "12h": 12 * 60 * 60,
    "24h": 24 * 60 * 60,
}


class UnlockAtCondition(BaseCondition):
    name = "unlock_at"
    description = "Unlocks the whisper only after a specified time"

    async def evaluate(self, whisper_id: str, user_id: int, **kwargs) -> ConditionResult:
        return self._check(whisper_id, user_id, kwargs)

    def check(self, whisper: dict, user_id: int, config: dict) -> ConditionResult:
        whisper_id = whisper.get("whisper_id", "")
        return self._check(whisper_id, user_id, config)

    # ── internal ──────────────────────────────────────────────────────────

    def _check(self, whisper_id: str, user_id: int, config: dict) -> ConditionResult:
        condition_id = config.get("id", "unlock_at")
        timestamp = resolve_timestamp(config)
        if timestamp is None:
            return ConditionResult(
                passed=False,
                reason="invalid_config",
                requires_interaction=False,
                message="⏳ شرط الفتح بعد وقت غير مكتمل الإعدادات.",
                condition_id=condition_id,
            )
        remaining = timestamp - int(time.time())
        if remaining <= 0:
            return ConditionResult(
                passed=True,
                reason="unlock_time_reached",
                message="",
                condition_id=condition_id,
            )
        return ConditionResult(
            passed=False,
            reason="not_yet",
            requires_interaction=False,
            message=(
                "⏳ لم يحن وقت فتح هذه الهمسة بعد.\n\n"
                f"🕒 الوقت المتبقي:\n{format_remaining(remaining)}"
            ),
            condition_id=condition_id,
        )


def resolve_timestamp(config: dict):
    """Extract the unlock timestamp from a condition config.

    Accepts either {"unlock_at": {"timestamp": <epoch>}} or a flat
    {"timestamp": <epoch>} / {"iso": "<ISO8601>"} layout, as stored by the
    creation wizard inside conditions_data.
    """
    cfg = config or {}
    unlock = cfg.get("unlock_at")
    if isinstance(unlock, dict):
        ts = _coerce_timestamp(unlock.get("timestamp"))
        if ts is not None:
            return ts
        return _parse_iso(unlock.get("iso"))
    ts = _coerce_timestamp(cfg.get("timestamp"))
    if ts is not None:
        return ts
    return _parse_iso(cfg.get("iso"))


def _coerce_timestamp(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value):
    if not isinstance(value, str):
        return None
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def duration_seconds(key: str):
    return DURATIONS.get(key)


def format_remaining(seconds: int) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days} يوم، {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_custom_datetime(raw: str):
    """Parse a user-supplied date/time into an epoch timestamp.

    Returns None when the input does not match any supported format.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace("/", "-")
    if not text:
        return None
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
    )
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None
