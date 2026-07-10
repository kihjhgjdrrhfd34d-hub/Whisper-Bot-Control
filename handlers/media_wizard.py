"""
handlers/media_wizard.py — Media Whisper Wizard (v2.2.0)

Flow:
  1. User sends media (photo/video/document/audio/voice/animation) in private chat.
  2. Bot stores media in pending_media_whispers and shows preview with
     a single "• ارسل همسة •" button (switch_inline_query).
  3. User clicks the button → Telegram opens chat selector.
  4. User picks a chat → inline query fires.
  5. Inline handler returns whisper-type selection as media inline results
     (each result carries the actual media + read button).
  6. User picks a type → message lands in chat.
  7. chosen_inline_result handler creates the whisper record, sends the
     dashboard to the user, and cleans up the pending media.
"""

import logging
import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedVoice,
    InlineQueryResultCachedGif,
    InputTextMessageContent,
)
from database import (
    store_pending_media,
    get_pending_media,
    get_pending_media_by_id,
    delete_pending_media,
    get_setting,
    is_banned,
    upsert_user,
)

logger = logging.getLogger(__name__)

MEDIA_WIZARD_CANCEL = "media_wizard:cancel"

# ── Whisper type options shown in inline results ──────────────────────────────
WIZARD_TYPES = [
    ("custom",    "🔒 همسة خاصة",      "إرسال كهمسة خاصة لشخص معيّن"),
    ("everyone",  "🌍 همسة للجميع",     "يمكن لأي شخص قراءتها"),
    ("first_one", "☝️ همسة لأول شخص",  "فقط أول من يفتحها"),
    ("first_three", "👥 همسة لأول 3",   "أول ثلاثة أشخاص فقط"),
]


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


# ─────────────────────────────────────────────────────────────────────────────
# Build inline query results for a given pending media + whisper type
# ─────────────────────────────────────────────────────────────────────────────

def build_wizard_inline_results(pending, bot_username: str) -> list:
    """
    Build 4 InlineQueryResult* objects (one per whisper type) from a
    pending media record.  Each result carries the actual media so the
    selected type is rendered as a media message in the chat.
    """
    results = []
    mt = pending["message_type"]
    fid = pending["file_id"]
    caption = pending["caption"] or pending["content"] or ""

    for wtype, title, desc in WIZARD_TYPES:
        result_id = f"mw:{wtype}:{pending['id']}"
        read_cb = f"mw_read:{wtype}:{pending['id']}"

        read_kb = InlineKeyboardMarkup(row_width=1)
        read_kb.add(InlineKeyboardButton(
            "اضغط للرؤيه 🔒", callback_data=read_cb,
        ))

        if mt == "photo":
            results.append(InlineQueryResultCachedPhoto(
                id=result_id,
                photo_file_id=fid,
                title=title,
                description=desc,
                caption=f"🤫 {title}\n{caption}"[:1024] if caption else f"🤫 {title}",
                reply_markup=read_kb,
            ))

        elif mt == "video":
            results.append(InlineQueryResultCachedVideo(
                id=result_id,
                video_file_id=fid,
                title=title,
                description=desc,
                caption=f"🤫 {title}\n{caption}"[:1024] if caption else f"🤫 {title}",
                reply_markup=read_kb,
            ))

        elif mt == "document":
            results.append(InlineQueryResultCachedDocument(
                id=result_id,
                document_file_id=fid,
                title=title,
                description=desc,
                caption=f"🤫 {title}\n{caption}"[:1024] if caption else f"🤫 {title}",
                reply_markup=read_kb,
            ))

        elif mt == "audio":
            results.append(InlineQueryResultCachedAudio(
                id=result_id,
                audio_file_id=fid,
                reply_markup=read_kb,
            ))

        elif mt == "voice":
            results.append(InlineQueryResultCachedVoice(
                id=result_id,
                voice_file_id=fid,
                title=title,
                reply_markup=read_kb,
            ))

        elif mt == "animation":
            results.append(InlineQueryResultCachedGif(
                id=result_id,
                gif_file_id=fid,
                title=title,
                description=desc,
                caption=f"🤫 {title}\n{caption}"[:1024] if caption else f"🤫 {title}",
                reply_markup=read_kb,
            ))

        else:
            # Fallback: cached photo result
            results.append(InlineQueryResultCachedPhoto(
                id=result_id,
                photo_file_id=fid,
                title=title,
                description=desc,
                caption=f"🤫 {title}\n{caption}"[:1024] if caption else f"🤫 {title}",
                reply_markup=read_kb,
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Handler registration
# ─────────────────────────────────────────────────────────────────────────────

def register_media_wizard_handlers(bot: telebot.TeleBot, user_states: dict):
    """Register private-chat media handlers for the wizard."""

    SUPPORTED_CONTENT_TYPES = ["photo", "video", "document", "audio", "voice", "animation"]

    def _is_private(msg):
        return msg.chat and msg.chat.type == "private"

    # ── Media message handler (private chat) ──────────────────────────────────
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
        caption_preview = f"\n📝 {media['caption']}" if media.get("caption") else ""
        text = (
            f"✅ *تم استلام {_media_label(media['message_type'])}*\n"
            f"{caption_preview}\n\n"
            f"اضغط الزر أدناه لإرسالها كهمسة:"
        )

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(
            "• ارسل همسة •",
            switch_inline_query="",
        ))
        kb.add(InlineKeyboardButton(
            "❌ إلغاء",
            callback_data=MEDIA_WIZARD_CANCEL,
        ))

        try:
            bot.send_message(
                msg.chat.id, text,
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.error(f"media_wizard send confirmation: {exc}")

    # ── Cancel callback ───────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == MEDIA_WIZARD_CANCEL)
    def cancel_media_wizard(call: telebot.types.CallbackQuery):
        user = call.from_user
        delete_pending_media(user.id)
        bot.answer_callback_query(call.id, "✅ تم الإلغاء.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    # ── Media wizard read callback (fallback if chosen_inline_result is slow) ─
    @bot.callback_query_handler(func=lambda c: c.data.startswith("mw_read:"))
    def handle_mw_read(call: telebot.types.CallbackQuery):
        """
        Handle the read button on media wizard inline results.
        This is a fallback in case the chosen_inline_result handler
        hasn't edited the markup yet. Creates the whisper on the fly.
        """
        user = call.from_user
        parts = call.data.split(":")
        if len(parts) != 3:
            bot.answer_callback_query(call.id, "❌ خطأ.", show_alert=True)
            return

        _, wtype, pending_id_str = parts
        try:
            pending_id = int(pending_id_str)
        except ValueError:
            bot.answer_callback_query(call.id, "❌ خطأ.", show_alert=True)
            return

        pending = get_pending_media_by_id(pending_id)
        if not pending:
            bot.answer_callback_query(
                call.id, "❌ الهمسة غير موجودة أو تم إرسالها بالفعل.",
                show_alert=True,
            )
            return

        max_readers = 0
        if wtype == "first_one":
            max_readers = 1
        elif wtype == "first_three":
            max_readers = 3

        from database import create_whisper
        wid = create_whisper(
            sender_id=user.id,
            content=pending["content"] or "",
            whisper_type=wtype,
            target_users=[],
            max_readers=max_readers,
            auto_delete_hours=_auto_hours(),
            message_type=pending["message_type"],
            file_id=pending["file_id"],
            caption=pending["caption"],
        )

        # Update the button to standard read callback
        read_kb = InlineKeyboardMarkup(row_width=1)
        read_kb.add(InlineKeyboardButton(
            "اضغط للرؤيه 🔒", callback_data=f"read:{wid}",
        ))
        try:
            if call.inline_message_id:
                bot.edit_message_reply_markup(
                    inline_message_id=call.inline_message_id,
                    reply_markup=read_kb,
                )
            elif call.message:
                bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=read_kb,
                )
        except Exception:
            pass

        from handlers.dashboard import send_dashboard
        try:
            send_dashboard(bot, user.id, wid)
        except Exception:
            pass

        delete_pending_media(user.id)
        bot.answer_callback_query(call.id, "✅ تم إنشاء الهمسة!")
