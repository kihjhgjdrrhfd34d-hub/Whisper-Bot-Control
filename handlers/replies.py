"""
handlers/replies.py — Two-way Whisper Conversation System.

Architecture
------------
Replies form a two-way conversation between the original whisper sender and
the original recipient (first reader). Every reply uses the same whisper_id.
This is NOT anonymous messaging — both participants know who they are
conversing with.

Flow
----
1. User A sends a whisper → User B opens it.
   User B immediately receives a DM with:
     💬 Reply
     📜 Conversation (if replies exist)

2. User B presses 💬 Reply, sends content.
   Reply is saved under whisper_id and delivered to User A.
   User A receives:
     "You received a reply to your whisper."
     💬 Reply
     📜 Conversation

3. User A presses 💬 Reply, sends content.
   Reply goes back to User B.

4. Continue forever.

Permissions
-----------
Only the original sender and the first reader may reply.
Nobody else.

State key used in user_states
-----------------------------
{"action": "pending_whisper_reply", "whisper_id": str}

Supported media
---------------
text, photo, video, voice, audio, document, sticker, animation, contact, location
"""

import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import get_whisper, is_banned, get_setting
from database.replies import (
    can_reply_to_whisper,
    create_reply,
    get_replies,
    get_reply_recipient,
    MAX_REPLIES_PER_WHISPER,
    SUPPORTED_MEDIA,
)

logger = logging.getLogger(__name__)

_REPLY_PREFIX = "wsp_reply:"
_CONV_PREFIX = "wsp_conv:"
_MAX_CAPTION = 200


def reply_button(whisper_id: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("💬 Reply", callback_data=f"{_REPLY_PREFIX}{whisper_id}")


def conversation_button(whisper_id: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("📜 Conversation", callback_data=f"{_CONV_PREFIX}{whisper_id}")


def reply_keyboard(whisper_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(reply_button(whisper_id))
    replies = get_replies(whisper_id)
    if replies:
        kb.add(conversation_button(whisper_id))
    return kb


def _is_reply_callback(data: str) -> bool:
    return data.startswith(_REPLY_PREFIX)


def _is_conv_callback(data: str) -> bool:
    return data.startswith(_CONV_PREFIX)


def _handle_reply_callback(
    bot: telebot.TeleBot,
    call: telebot.types.CallbackQuery,
    user_states: dict,
) -> None:
    user = call.from_user
    whisper_id = call.data[len(_REPLY_PREFIX):]

    if get_setting("whisper_replies_enabled") != "1":
        bot.answer_callback_query(
            call.id, "Replies are currently disabled.", show_alert=True
        )
        return

    if is_banned(user.id):
        bot.answer_callback_query(
            call.id, "You are banned.", show_alert=True
        )
        return

    if get_setting("bot_active") != "1":
        bot.answer_callback_query(
            call.id, "Bot is currently offline.", show_alert=True
        )
        return

    w = get_whisper(whisper_id)
    if not w:
        bot.answer_callback_query(
            call.id,
            "This whisper no longer exists.",
            show_alert=True,
        )
        return

    ok, reason = can_reply_to_whisper(whisper_id, user.id)
    if not ok:
        msg_map = {
            "whisper_not_found": "Whisper not found.",
            "whisper_locked":    "Whisper is locked. Cannot reply.",
            "not_participant":   "You are not part of this conversation.",
            "reply_cap_reached": f"Reply limit ({MAX_REPLIES_PER_WHISPER}) reached.",
        }
        bot.answer_callback_query(
            call.id,
            msg_map.get(reason, "You cannot reply to this whisper."),
            show_alert=True,
        )
        return

    user_states[user.id] = {
        "action": "pending_whisper_reply",
        "whisper_id": whisper_id,
    }

    bot.answer_callback_query(call.id)

    cancel_kb = InlineKeyboardMarkup()
    cancel_kb.add(InlineKeyboardButton("Cancel", callback_data="cancel_action"))
    try:
        bot.send_message(
            user.id,
            "Send your reply.\n\n"
            "You can send text, photo, video, voice, audio, document, sticker, GIF, contact, or location.",
            reply_markup=cancel_kb,
        )
    except Exception as exc:
        logger.error(f"reply prompt send failed for user {user.id}: {exc}")
        user_states.pop(user.id, None)


def _handle_conv_callback(
    bot: telebot.TeleBot,
    call: telebot.types.CallbackQuery,
) -> None:
    user = call.from_user
    whisper_id = call.data[len(_CONV_PREFIX):]

    bot.answer_callback_query(call.id)

    w = get_whisper(whisper_id)
    if not w:
        bot.send_message(user.id, "This whisper no longer exists.")
        return

    all_replies = get_replies(whisper_id)

    lines = []
    lines.append("Conversation")
    lines.append("")
    lines.append(f"Whisper: {w['content']}")
    lines.append("")

    for i, r in enumerate(all_replies, 1):
        sender_tag = "You" if r["sender_id"] == user.id else "Them"
        content = r["content"] or f"[{r['media_type'] or 'media'}]"
        ts = str(r["created_at"])[:19] if r["created_at"] else ""
        lines.append(f"{i}. {sender_tag} ({ts}):")
        lines.append(f"   {content}")
        lines.append("")

    if not all_replies:
        lines.append("No replies yet.")

    kb = InlineKeyboardMarkup()
    kb.add(reply_button(whisper_id))

    text = "\n".join(lines)[:4096]
    try:
        bot.send_message(user.id, text, reply_markup=kb)
    except Exception as exc:
        logger.error(f"conv view send failed for user {user.id}: {exc}")


def handle_reply_message(
    bot: telebot.TeleBot,
    msg: telebot.types.Message,
    user_states: dict,
) -> bool:
    user = msg.from_user
    state = user_states.get(user.id)
    if not state or state.get("action") != "pending_whisper_reply":
        return False

    whisper_id = state["whisper_id"]
    del user_states[user.id]

    w = get_whisper(whisper_id)
    if not w:
        bot.send_message(msg.chat.id, "This whisper no longer exists.")
        return True

    content, media_type, file_id = _extract_media(msg)

    if not content and not file_id:
        bot.send_message(
            msg.chat.id,
            "Empty message. Please send text or media.",
        )
        return True

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
            "Could not save reply (limit may be reached).",
        )
        return True

    bot.send_message(msg.chat.id, "Your reply has been sent.")

    _route_reply(bot, msg, reply_id, whisper_id, user.id, w, content, media_type, file_id)

    return True


def _route_reply(
    bot: telebot.TeleBot,
    original_msg: telebot.types.Message,
    reply_id: str,
    whisper_id: str,
    replier_id: int,
    whisper_row,
    content: str,
    media_type,
    file_id,
) -> None:
    recipient_id = get_reply_recipient(whisper_id, replier_id)
    if not recipient_id:
        return

    notif_header = "You received a reply to your whisper.\n\n"
    kb = reply_keyboard(whisper_id)

    try:
        _deliver_reply(
            bot, original_msg, recipient_id,
            notif_header, content, media_type, file_id, kb,
        )
    except Exception as exc:
        logger.error(
            f"reply delivery failed whisper={whisper_id!r} "
            f"recipient={recipient_id} reply={reply_id!r}: {exc}"
        )


def _deliver_reply(
    bot: telebot.TeleBot,
    original_msg: telebot.types.Message,
    recipient_id: int,
    header: str,
    content: str,
    media_type,
    file_id,
    kb: InlineKeyboardMarkup,
) -> None:
    if media_type == "photo":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_photo(recipient_id, file_id, caption=caption,
                       parse_mode="Markdown", reply_markup=kb)

    elif media_type == "video":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_video(recipient_id, file_id, caption=caption,
                       parse_mode="Markdown", reply_markup=kb)

    elif media_type == "voice":
        bot.send_voice(recipient_id, file_id, reply_markup=kb)
        if content:
            bot.send_message(recipient_id, header + content,
                             parse_mode="Markdown")

    elif media_type == "audio":
        bot.send_audio(recipient_id, file_id, reply_markup=kb)
        if content:
            bot.send_message(recipient_id, header + content,
                             parse_mode="Markdown")

    elif media_type == "document":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_document(recipient_id, file_id, caption=caption,
                          parse_mode="Markdown", reply_markup=kb)

    elif media_type == "sticker":
        bot.send_sticker(recipient_id, file_id)
        bot.send_message(recipient_id, header.rstrip(),
                         parse_mode="Markdown", reply_markup=kb)

    elif media_type == "animation":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_animation(recipient_id, file_id, caption=caption,
                           parse_mode="Markdown", reply_markup=kb)

    elif media_type == "contact":
        phone = file_id or "Unknown"
        text = header.rstrip() + f"\n\n📞 Contact: {phone}"
        bot.send_message(recipient_id, text,
                         parse_mode="Markdown", reply_markup=kb)

    elif media_type == "location":
        text = header.rstrip() + "\n\n📍 Location shared"
        bot.send_message(recipient_id, text,
                         parse_mode="Markdown", reply_markup=kb)

    else:
        text = header + (content or "")
        text = text[:4096]
        bot.send_message(recipient_id, text,
                         parse_mode="Markdown", reply_markup=kb)


def _extract_media(msg: telebot.types.Message):
    content = ""
    media_type = None
    file_id = None

    if msg.content_type == "text":
        content = (msg.text or "").strip()

    elif msg.content_type == "photo":
        media_type = "photo"
        file_id = msg.photo[-1].file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "video":
        media_type = "video"
        file_id = msg.video.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "voice":
        media_type = "voice"
        file_id = msg.voice.file_id

    elif msg.content_type == "audio":
        media_type = "audio"
        file_id = msg.audio.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "document":
        media_type = "document"
        file_id = msg.document.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "sticker":
        media_type = "sticker"
        file_id = msg.sticker.file_id

    elif msg.content_type == "animation":
        media_type = "animation"
        file_id = msg.animation.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "contact":
        media_type = "contact"
        phone = msg.contact.phone_number if hasattr(msg.contact, 'phone_number') else ""
        first = msg.contact.first_name if hasattr(msg.contact, 'first_name') else ""
        last = msg.contact.last_name if hasattr(msg.contact, 'last_name') else ""
        file_id = f"{first} {last}".strip() + f" - {phone}" if phone else f"{first} {last}".strip()

    elif msg.content_type == "location":
        media_type = "location"
        file_id = ""

    return content, media_type, file_id


def register_reply_handlers(bot: telebot.TeleBot, user_states: dict) -> None:

    @bot.callback_query_handler(func=lambda c: _is_reply_callback(c.data))
    def reply_callback(call: telebot.types.CallbackQuery):
        try:
            _handle_reply_callback(bot, call, user_states)
        except Exception as exc:
            logger.error(f"reply_callback unhandled: {exc}", exc_info=True)
            try:
                bot.answer_callback_query(
                    call.id, "An unexpected error occurred. Try again.", show_alert=True
                )
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda c: _is_conv_callback(c.data))
    def conv_callback(call: telebot.types.CallbackQuery):
        try:
            _handle_conv_callback(bot, call)
        except Exception as exc:
            logger.error(f"conv_callback unhandled: {exc}", exc_info=True)
            try:
                bot.answer_callback_query(
                    call.id, "An unexpected error occurred. Try again.", show_alert=True
                )
            except Exception:
                pass
