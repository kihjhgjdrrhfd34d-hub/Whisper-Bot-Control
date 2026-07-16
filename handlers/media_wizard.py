"""
handlers/media_wizard.py — Media Whisper Wizard (v6.0.0)

Flow:
  1. User sends media in private chat.
  2. Bot stores media in pending_media_whispers.
  3. Bot sends "• ارسل همسة •" button.
  4. User clicks button → inline query fires with "m:<pending_id>".
  5. Inline handler creates whispers (same as text) and returns results.
  6. User picks a type → group shows "🔒 اضغط للرؤية".
  7. Reader clicks → existing read handler delivers media.
"""

import json
import logging
import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from database import (
    store_pending_media,
    get_pending_media_by_id,
    delete_pending_media,
    delete_pending_media_by_id,
    get_setting,
    is_banned,
    upsert_user,
    create_whisper,
)

logger = logging.getLogger(__name__)


def _media_label(message_type: str) -> str:
    return {
        "photo": "🖼 صورة",
        "video": "🎬 فيديو",
        "voice": "🎤 تسجيل صوتي",
        "audio": "🎵 ملف صوتي",
        "document": "📄 مستند",
        "animation": "🎞 متحركة",
        "location": "📍 موقع",
    }.get(message_type, message_type)


def _auto_hours() -> int:
    if get_setting("auto_delete_enabled") == "1":
        try:
            return int(get_setting("auto_delete_hours"))
        except Exception:
            pass
    return 0


# ── Whisper type definitions (canonical source — shared with inline.py) ────────
# (wtype, max_readers, menu_title, menu_desc, group_message_text)
FOUR_OPTIONS = [
    ("first_one",   1, "همسة لأول شخص",          "🔒 يقرأها أول شخص فقط",      "هذه همسه سريه لاول شخص يقوم بقرأتها"),
    ("everyone",    0, "همسة للجميع",             "🌍 يمكن لأي شخص قراءتها",    "هذه الهمسه للجميع"),
    ("first_three", 3, "همسة لأول ثلاثة أشخاص",  "👥 يقرأها أول 3 أشخاص فقط", "هذه همسه سريه لاول ثلاثة أشخاص يقومون بقرأتها"),
    ("custom",      0, "همسة بالأيدي او اليوزر",  "🎯 مخصصة لشخص معين",         "هذه همسه سريه مخصصة"),
]

DESTRUCTIVE_OPTIONS = [
    ("first_one",   1, "💣 همسة تدميرية لشخص",          "💥 تُحذف بعد قراءتها",      "💣 همسة تدميرية لشخص واحد"),
    ("first_three", 3, "💣 همسة تدميرية لـ 3 أشخاص",    "💥 تُحذف بعد ثالث قارئ",    "💣 همسة تدميرية لـ 3 أشخاص"),
    ("everyone",    0, "💣 همسة تدميرية للجميع",        "💥 تظهر كتنبيه ولا تتكرر",  "💣 همسة تدميرية للجميع"),
]


def build_media_whisper_inline_results(pending, bot_username, hours):
    """
    Build inline results for a pending media whisper.
    Same structure as text whisper results — reuses create_whisper().
    Returns list of InlineQueryResultArticle.
    """
    results = []
    user_id = pending["user_id"]
    media_content = pending["content"] or ""
    message_type = pending["message_type"]
    file_id = pending["file_id"]
    caption = pending["caption"]

    location_lat = None
    location_lon = None
    if message_type == "location" and file_id:
        try:
            loc_data = json.loads(file_id)
            location_lat = loc_data.get("latitude")
            location_lon = loc_data.get("longitude")
        except Exception:
            pass

    for wtype, max_r, title, desc, group_text in FOUR_OPTIONS:
        if wtype == "custom":
            continue
        try:
            wid = create_whisper(
                sender_id=user_id,
                content=media_content,
                whisper_type=wtype,
                target_users=[],
                max_readers=max_r,
                auto_delete_hours=hours,
                message_type=message_type,
                file_id=file_id,
                caption=caption,
                location_lat=location_lat,
                location_lon=location_lon,
            )
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton(
                "🔒 اضغط للرؤية", callback_data=f"read:{wid}",
            ))
            results.append(
                InlineQueryResultArticle(
                    id=f"{wtype}:{wid}",
                    title=title,
                    description=desc,
                    input_message_content=InputTextMessageContent(
                        message_text="🔒 اضغط للرؤية",
                    ),
                    reply_markup=kb,
                )
            )
        except Exception as e:
            logger.error(f"media inline build [{wtype}]: {e}")

    for wtype, max_r, title, desc, group_text in DESTRUCTIVE_OPTIONS:
        try:
            wid = create_whisper(
                sender_id=user_id,
                content=media_content,
                whisper_type=wtype,
                target_users=[],
                max_readers=max_r,
                auto_delete_hours=hours,
                is_destructive=True,
                message_type=message_type,
                file_id=file_id,
                caption=caption,
                location_lat=location_lat,
                location_lon=location_lon,
            )
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton(
                "🔒 اضغط للرؤية", callback_data=f"read:{wid}",
            ))
            results.append(
                InlineQueryResultArticle(
                    id=f"destructive:{wtype}:{wid}",
                    title=title,
                    description=desc,
                    input_message_content=InputTextMessageContent(
                        message_text="🔒 اضغط للرؤية",
                    ),
                    reply_markup=kb,
                )
            )
        except Exception as e:
            logger.error(f"media inline destructive build [{wtype}]: {e}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Handler registration
# ─────────────────────────────────────────────────────────────────────────────

def register_media_wizard_handlers(bot: telebot.TeleBot, user_states: dict):
    """Register private-chat media handlers for the wizard."""

    SUPPORTED_CONTENT_TYPES = ["photo", "video", "document", "audio", "voice", "animation"]

    def _is_private(msg):
        return msg.chat and msg.chat.type == "private"

    # ── Media message handler (private chat) ──────────────────────────────
    @bot.message_handler(content_types=SUPPORTED_CONTENT_TYPES, func=_is_private)
    def handle_private_media(msg: telebot.types.Message):
        user = msg.from_user
        if not user:
            return
        if is_banned(user.id):
            return
        if get_setting("bot_active") != "1":
            return

        from services.media import extract_media_from_message
        media = extract_media_from_message(msg)

        if not media["message_type"]:
            return

        upsert_user(user.id, user.username, user.first_name, user.last_name)

        pending_id = store_pending_media(
            user_id=user.id,
            message_type=media["message_type"],
            file_id=media["file_id"],
            caption=media["caption"],
            content=media["content"],
        )

        label = _media_label(media["message_type"])

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(
            "• ارسل همسة •",
            switch_inline_query=f"m:{pending_id}",
        ))

        try:
            bot.send_message(
                msg.chat.id,
                f"✅ تم استلام {label}\n\n"
                "اضغط الزر أدناه لإرسالها:",
                parse_mode=None,
                reply_markup=kb,
            )
        except Exception as exc:
            logger.error(f"media_wizard send switch_inline: {exc}")

    # ── Cancel callback (backward compat with old messages) ────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "media_wizard:cancel")
    def cancel_media_wizard(call: telebot.types.CallbackQuery):
        user = call.from_user
        delete_pending_media(user.id)
        bot.answer_callback_query(call.id, "✅ تم الإلغاء.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
