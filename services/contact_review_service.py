import logging
from datetime import datetime, timezone
from database import get_whisper, get_user
from database.contact_review import get_pending_review, approve_review
from handlers._formatting import _fmt_username, _get_sender_display

logger = logging.getLogger(__name__)


def build_review_message(whisper_id: str) -> str | None:
    review = get_pending_review(whisper_id)
    if not review:
        return None

    sender = get_user(review["sender_id"])
    sender_name = _get_sender_display(review["sender_id"])

    target_str = "—"
    if review["target_id"]:
        target = get_user(review["target_id"])
        if target:
            target_str = _get_sender_display(review["target_id"])
        else:
            target_str = str(review["target_id"])

    created = str(review["created_at"])[:16] if review["created_at"] else "—"

    text = (
        "━━━━━━━━━━━━━━━━━━\n"
        "📩 طلب مراجعة همسة جديدة\n\n"
        f"👤 المرسل:\n{sender_name}\n\n"
        f"📌 المستهدف:\n{target_str}\n\n"
        f"🆔 معرف الهمسة:\n<code>{whisper_id}</code>\n\n"
        f"🕐 وقت الإرسال:\n{created}\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    return text
