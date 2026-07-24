"""
handlers/media_whispers.py — Media Whispers cancel callback + reply system (v4.0.0)

The primary media capture handler lives in bot.py (registered immediately
after /start so it fires before the catch-all). This module provides:
  - MEDIA_LABELS dict (used by bot.py)
  - cancel_media_whisper callback query handler
  - Media whisper reply system (mwreply: callback, real-sender delivery)
"""

import json
import logging
import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from handlers.keyboard_utils import back_button
from database import (
    delete_whisper,
    get_whisper,
    get_setting,
    is_banned,
    upsert_user,
)

logger = logging.getLogger(__name__)

# ── Media whisper reply callback prefix ──────────────────────────────────────
_MW_REPLY_PREFIX = "mwreply:"


def mw_reply_button(whisper_id: str, bot_username: str = "") -> InlineKeyboardButton:
    """Build a reply button for media whispers (URL deep-link to bot DM)."""
    if bot_username:
        reply_url = f"tg://resolve?domain={bot_username}&start=reply_{whisper_id}"
        return InlineKeyboardButton("↩️ رد", url=reply_url)
    return InlineKeyboardButton(
        "↩️ رد",
        callback_data=f"{_MW_REPLY_PREFIX}{whisper_id}",
    )


def media_whisper_read_keyboard(whisper_id: str, bot_username: str = "") -> InlineKeyboardMarkup:
    """Build keyboard shown when a media whisper is delivered in private."""
    from database.replies import count_replies
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(mw_reply_button(whisper_id, bot_username))
    if count_replies(whisper_id) > 0:
        from handlers.replies import conversation_button
        kb.add(conversation_button(whisper_id))
    return kb


MEDIA_LABELS = {
    "photo": "📷 صورة",
    "video": "🎬 فيديو",
    "voice": "🎤 تسجيل صوتي",
    "audio": "🎵 ملف صوتي",
    "document": "📄 مستند",
}


def register_media_whisper_handlers(bot: telebot.TeleBot, user_states: dict):
    """Register media whisper cancel + reply callbacks.

    The primary media capture handler lives in bot.py (registered early
    to fire before the catch-all).
    """

    # ── Cancel callback for media whispers ──────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("media_whisper:cancel:"))
    def cancel_media_whisper(call: telebot.types.CallbackQuery):
        user = call.from_user
        parts = call.data.split(":")
        if len(parts) != 3:
            return
        wid = parts[2]

        w = get_whisper(wid)
        if not w or w["sender_id"] != user.id:
            bot.answer_callback_query(
                call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True
            )
            return

        delete_whisper(wid)
        bot.answer_callback_query(call.id, "✅ تم الحذف.")
        try:
            bot.edit_message_text(
                "🗑 تم حذف هذه الهمسة.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass

    # ── Media whisper reply callback (mwreply:) ─────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(_MW_REPLY_PREFIX))
    def handle_mw_reply_callback(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data[len(_MW_REPLY_PREFIX):]

        if get_setting("whisper_replies_enabled") != "1":
            bot.answer_callback_query(
                call.id, "💬 الردود معطّلة.", show_alert=True
            )
            return

        if is_banned(user.id):
            bot.answer_callback_query(
                call.id, "🚫 أنت محظور.", show_alert=True
            )
            return

        if get_setting("bot_active") != "1":
            bot.answer_callback_query(
                call.id, "⚠️ البوت متوقف.", show_alert=True
            )
            return

        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(
                call.id,
                "❌ الهمسة غير موجودة أو تم حذفها.",
                show_alert=True,
            )
            return

        from database.replies import can_reply_to_whisper
        ok, reason = can_reply_to_whisper(whisper_id, user.id)
        if not ok:
            msg_map = {
                "whisper_not_found": "❌ الهمسة غير موجودة.",
                "whisper_locked": "🔒 الهمسة مقفلة.",
                "not_participant": "⛔ فقط المرسل والقرّاء يمكنهم الرد.",
                "reply_cap_reached": "⚠️ تم الوصول للحد الأقصى.",
            }
            bot.answer_callback_query(
                call.id,
                msg_map.get(reason, "❌ لا يمكنك الرد على هذه الهمسة."),
                show_alert=True,
            )
            return

        # Toggle off if already pending
        existing = user_states.get(user.id)
        if (existing
                and existing.get("action") == "pending_media_reply"
                and existing.get("whisper_id") == whisper_id):
            user_states.pop(user.id, None)
            bot.answer_callback_query(call.id, "❌ أُلغي الرد.", show_alert=False)
            return

        # Store pending state
        user_states[user.id] = {
            "action": "pending_media_reply",
            "whisper_id": whisper_id,
        }

        # Show prompt with original whisper context
        w_dict = dict(w)
        if w_dict.get("message_type"):
            mt_label = {
                "photo": "🖼 صورة",
                "video": "🎬 فيديو",
                "voice": "🎤 تسجيل صوتي",
                "audio": "🎵 ملف صوتي",
                "document": "📄 مستند",
                "animation": "🎞 متحركة",
                "location": "📍 موقع",
            }.get(w_dict["message_type"], w_dict["message_type"])
            prompt = (
                f"📝 *الهمسة الأصلية:* ({mt_label})\n\n"
                f"{w['content']}\n\n"
                f"✏️ أرسل ردّك الآن:"
            )
        else:
            prompt = (
                f"📝 *الهمسة الأصلية:*\n\n"
                f"{w['content']}\n\n"
                f"✏️ أرسل ردّك الآن:"
            )

        try:
            bot.send_message(user.id, prompt, parse_mode="Markdown")
        except Exception:
            pass

        bot.answer_callback_query(
            call.id, "📝 أرسل ردّك الآن في البوت.", show_alert=False
        )


# ─────────────────────────────────────────────────────────────────────────────
# Media whisper reply message processing (called from bot.py handle_messages)
# ─────────────────────────────────────────────────────────────────────────────

def handle_media_reply_message(
    bot: telebot.TeleBot,
    msg: telebot.types.Message,
    user_states: dict,
) -> bool:
    """
    Process an incoming reply to a media whisper.

    Returns True if the message was consumed (caller should stop processing).
    Returns False if the user is not in a pending_media_reply state.
    """
    user = msg.from_user
    state = user_states.get(user.id)
    if not state or state.get("action") != "pending_media_reply":
        return False

    whisper_id = state["whisper_id"]

    # ── Validate: replies still enabled ──────────────────────────────────
    if get_setting("whisper_replies_enabled") != "1":
        bot.send_message(msg.chat.id, "💬 الردود معطّلة.")
        del user_states[user.id]
        return True

    # ── Validate: whisper still exists ───────────────────────────────────
    w = get_whisper(whisper_id)
    if not w:
        bot.send_message(msg.chat.id, "❌ الهمسة لم تعد موجودة.")
        del user_states[user.id]
        return True

    # ── Extract content + media ──────────────────────────────────────────
    from services.media import extract_media_from_message
    media = extract_media_from_message(msg)
    content = media["content"] or ""
    media_type = media["message_type"]
    file_id = media["file_id"]

    # Handle sticker (not in extract_media_from_message)
    if msg.content_type == "sticker":
        media_type = "sticker"
        file_id = msg.sticker.file_id
        content = ""

    if not content and not file_id:
        bot.send_message(
            msg.chat.id,
            "⚠️ رسالة فارغة. أرسل نصاً أو وسائط.",
        )
        return True

    # ── Save reply to database ───────────────────────────────────────────
    from database.replies import create_reply
    reply_id = create_reply(
        whisper_id=whisper_id,
        sender_id=user.id,
        content=content,
        media_type=media_type,
        file_id=file_id,
    )
    if not reply_id:
        bot.send_message(
            msg.chat.id,
            "⚠️ تعذّر حفظ الرد (قد يكون الحد الأقصى قد بَلَغ).",
        )
        del user_states[user.id]
        return True

    # ── Deliver reply to original whisper sender ─────────────────────────
    delivery_ok = _deliver_media_reply(
        bot, whisper_id, user.id, content, media_type, file_id,
    )

    # ── Confirm to reader ────────────────────────────────────────────────
    if delivery_ok:
        try:
            bot.send_message(user.id, "✅ تم إرسال ردك بنجاح!")
        except Exception:
            pass
    else:
        try:
            bot.send_message(
                user.id,
                "⚠️ تعذّر إرسال ردّك إلى المستلم. "
                "قد يكون البوت محظوراً لدى المستخدم أو أن المستخدم أوقف البوت.",
            )
        except Exception:
            pass

    # ── Notify admin ─────────────────────────────────────────────────────
    try:
        from handlers._formatting import _get_sender_display
        from config import ADMIN_IDS
        sender_display = _get_sender_display(user.id)
        admin_parts = ["📬 *رد جديد على همسة (وسائط)*\n"]
        admin_parts.append(f"👤 من: {sender_display}")
        admin_parts.append(f"🆔 ايدي المرسل: `{user.id}`")
        admin_parts.append(f"🔗 معرف الهمسة: `{whisper_id}`")
        if content:
            admin_parts.append(f"\n💬 *نص الرد:*\n{content}")
        if media_type:
            admin_parts.append(f"📎 *نوع المرفق:* `{media_type}`")
        admin_parts.append(f"\n📨 *الهمسة الأصلية:*\n{w['content']}")
        admin_text = "\n".join(admin_parts)
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, admin_text, parse_mode=None)
            except Exception:
                pass
    except Exception:
        pass

    del user_states[user.id]
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Delivery: send reply to original whisper sender with real identity
# ─────────────────────────────────────────────────────────────────────────────

def _deliver_media_reply(
    bot: telebot.TeleBot,
    whisper_id: str,
    replier_id: int,
    content: str,
    media_type,
    file_id,
) -> bool:
    """
    Deliver a media whisper reply to the original sender.

    Shows the real sender identity (first name + username).
    Keeps the reply linked to the original whisper for statistics.
    """
    from handlers._formatting import _get_sender_display

    w = get_whisper(whisper_id)
    if not w:
        return False

    sender_display = _get_sender_display(replier_id)
    header = f"💬 *رد من:*\n{sender_display}\n\n"

    original_sender_id = w["sender_id"]
    if original_sender_id is None:
        return False

    # Build keyboard with mwreply: button + conversation link
    kb = InlineKeyboardMarkup(row_width=2)
    from database.replies import count_replies
    btn_conv = None
    if count_replies(whisper_id) > 1:
        from handlers.replies import conversation_button
        btn_conv = conversation_button(whisper_id)

    mw_username = ""
    try:
        mw_username = bot.get_me().username
    except Exception:
        pass

    if btn_conv:
        kb.add(mw_reply_button(whisper_id, mw_username), btn_conv)
    else:
        kb.add(mw_reply_button(whisper_id, mw_username))

    # Deliver based on media type
    try:
        if media_type == "photo":
            caption = (header + content)[:1024] if content else header.rstrip()
            bot.send_photo(
                original_sender_id, file_id,
                caption=caption, parse_mode=None, reply_markup=kb,
            )

        elif media_type == "video":
            caption = (header + content)[:1024] if content else header.rstrip()
            bot.send_video(
                original_sender_id, file_id,
                caption=caption, parse_mode=None, reply_markup=kb,
            )

        elif media_type == "voice":
            bot.send_voice(original_sender_id, file_id, reply_markup=kb)
            if content:
                bot.send_message(
                    original_sender_id, header + content, parse_mode=None,
                )

        elif media_type == "audio":
            bot.send_audio(original_sender_id, file_id, reply_markup=kb)
            if content:
                bot.send_message(
                    original_sender_id, header + content, parse_mode=None,
                )

        elif media_type == "document":
            caption = (header + content)[:1024] if content else header.rstrip()
            bot.send_document(
                original_sender_id, file_id,
                caption=caption, parse_mode=None, reply_markup=kb,
            )

        elif media_type == "sticker":
            bot.send_sticker(original_sender_id, file_id)
            bot.send_message(
                original_sender_id, header.rstrip(),
                parse_mode=None, reply_markup=kb,
            )

        elif media_type == "animation":
            caption = (header + content)[:1024] if content else header.rstrip()
            bot.send_animation(
                original_sender_id, file_id,
                caption=caption, parse_mode=None, reply_markup=kb,
            )

        elif media_type == "contact":
            cd = json.loads(file_id)
            bot.send_contact(
                original_sender_id,
                phone_number=cd["phone_number"],
                first_name=cd["first_name"],
                last_name=cd.get("last_name", ""),
                reply_markup=kb,
            )
            if content:
                bot.send_message(
                    original_sender_id, header + content, parse_mode=None,
                )

        elif media_type == "location":
            loc = json.loads(file_id)
            bot.send_location(
                original_sender_id,
                latitude=loc["latitude"],
                longitude=loc["longitude"],
                reply_markup=kb,
            )
            if content:
                bot.send_message(
                    original_sender_id, header + content, parse_mode=None,
                )

        else:
            # Text-only reply
            text = header + (content or "")
            bot.send_message(
                original_sender_id, text[:4096],
                parse_mode=None, reply_markup=kb,
            )

        return True

    except Exception as exc:
        logger.error(
            f"media reply delivery failed whisper={whisper_id!r}: {exc}"
        )
        return False
