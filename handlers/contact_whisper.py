import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_IDS
from database import get_whisper, get_user, delete_whisper
from database.contact_review import (
    get_pending_review, approve_review, reject_review,
)
from services.contact_review_service import build_review_message
from handlers._formatting import _fmt_username, _get_sender_display, format_display_time

logger = logging.getLogger(__name__)

_REVIEW_PREFIX = "crv:"


def register_contact_whisper_handlers(bot: telebot.TeleBot, user_states: dict):
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_REVIEW_PREFIX}publish:"))
    def handle_publish(call: telebot.types.CallbackQuery):
        user = call.from_user
        if user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ غير مصرح.", show_alert=True)
            return

        whisper_id = call.data.split(":", 2)[2]
        review = get_pending_review(whisper_id)
        if not review:
            bot.answer_callback_query(call.id, "❌ الطلب غير موجود أو تمت معالجته مسبقاً.", show_alert=True)
            return

        approve_review(whisper_id, user.id)

        # Notify sender
        _notify_sender_approved(bot, review)

        bot.answer_callback_query(call.id, "✅ تم اعتماد الهمسة.", show_alert=True)

        _update_review_message(bot, call, whisper_id, "✅ تم اعتماد الهمسة ونشرها بواسطة بوت التواصل.")

    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_REVIEW_PREFIX}reject:"))
    def handle_reject(call: telebot.types.CallbackQuery):
        user = call.from_user
        if user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ غير مصرح.", show_alert=True)
            return

        whisper_id = call.data.split(":", 2)[2]
        review = get_pending_review(whisper_id)
        if not review:
            bot.answer_callback_query(call.id, "❌ الطلب غير موجود أو تمت معالجته مسبقاً.", show_alert=True)
            return

        reject_review(whisper_id, user.id)
        from database import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM contact_reviews WHERE whisper_id=?", (whisper_id,))
            conn.commit()
        delete_whisper(whisper_id)

        # Notify sender
        _notify_sender_rejected(bot, review)

        bot.answer_callback_query(call.id, "🗑 تم رفض الهمسة وحذفها.", show_alert=True)

        _update_review_message(bot, call, whisper_id, "🗑 تم رفض الهمسة.")

    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_REVIEW_PREFIX}show:"))
    def handle_show_sender(call: telebot.types.CallbackQuery):
        user = call.from_user
        if user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ غير مصرح.", show_alert=True)
            return

        whisper_id = call.data.split(":", 2)[2]
        review = get_pending_review(whisper_id)
        if not review:
            bot.answer_callback_query(call.id, "❌ الطلب غير موجود.", show_alert=True)
            return

        sender = get_user(review["sender_id"])
        sender_name = _get_sender_display(review["sender_id"])
        created = format_display_time(review["created_at"]) or "—"

        text = (
            "━━━━━━━━━━━━━━━━━━\n"
            "📩 معلومات المرسل\n\n"
            f"👤 الاسم:\n{sender_name}\n\n"
            f"🆔 الآيدي:\n<code>{review['sender_id']}</code>\n\n"
            f"🕐 وقت الإرسال:\n{created}\n\n"
            f"📌 النوع الأصلي:\n{review['original_type']}\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        bot.answer_callback_query(call.id)
        try:
            bot.send_message(call.message.chat.id, text, parse_mode="HTML")
        except Exception:
            pass


def _notify_sender_approved(bot, review):
    try:
        bot.send_message(
            review["sender_id"],
            "✅ تمت الموافقة على همستك وإرسالها إلى بوت التواصل للنشر.",
        )
    except Exception as exc:
        logger.warning(f"Failed to notify sender {review['sender_id']} about approval: {exc}")


def _notify_sender_rejected(bot, review):
    try:
        bot.send_message(
            review["sender_id"],
            "❌ لم تتم الموافقة على همستك من قبل بوت التواصل.",
        )
    except Exception as exc:
        logger.warning(f"Failed to notify sender {review['sender_id']} about rejection: {exc}")


def send_review_to_admins(bot, whisper_id: str):
    text = build_review_message(whisper_id)
    if not text:
        return

    from config import ADMIN_IDS

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📤 نشر", callback_data=f"{_REVIEW_PREFIX}publish:{whisper_id}"),
        InlineKeyboardButton("🗑 رفض", callback_data=f"{_REVIEW_PREFIX}reject:{whisper_id}"),
    )
    kb.add(
        InlineKeyboardButton("👤 معلومات المرسل", callback_data=f"{_REVIEW_PREFIX}show:{whisper_id}"),
    )

    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
        except Exception as exc:
            logger.warning(f"Failed to send review to admin {admin_id}: {exc}")


def _update_review_message(bot, call, whisper_id: str, status_text: str):
    try:
        bot.edit_message_text(
            f"🗒️ الهمسة <code>{whisper_id}</code>\n\n{status_text}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
        )
    except Exception:
        pass
