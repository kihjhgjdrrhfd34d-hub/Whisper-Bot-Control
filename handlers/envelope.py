import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import upsert_user, create_whisper, get_setting
from database.envelope import create_draft, get_draft, delete_draft

logger = logging.getLogger(__name__)


def _preview_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👁 معاينة", callback_data="env_preview"),
        InlineKeyboardButton("📤 إرسال الهمسة", callback_data="env_send"),
    )
    kb.add(
        InlineKeyboardButton("✏️ تعديل", callback_data="env_edit"),
        InlineKeyboardButton("🗑 حذف المسودة", callback_data="env_delete"),
    )
    return kb


def _type_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👤 لأول شخص", callback_data="env_send:first_one"),
        InlineKeyboardButton("🌍 للجميع", callback_data="env_send:everyone"),
    )
    kb.add(
        InlineKeyboardButton("👥 لأول 3 أشخاص", callback_data="env_send:first_three"),
    )
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="env_back"))
    return kb


def register_envelope_handlers(bot: telebot.TeleBot, user_states: dict):
    try:
        bot_username = bot.get_me().username
    except Exception:
        bot_username = ""

    @bot.callback_query_handler(func=lambda c: c.data == "env_new")
    def start_envelope(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        user_states[user.id] = {"action": "env_awaiting_content"}
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="env_delete"))
        bot.send_message(
            call.message.chat.id,
            "✉️ *الظرف الشخصي*\n\n"
            "📝 أرسل النص الذي تريد وضعه في الظرف:",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data == "env_preview")
    def preview_envelope(call: telebot.types.CallbackQuery):
        draft = get_draft(call.from_user.id)
        if not draft:
            bot.answer_callback_query(call.id, "❌ لا يوجد ظرف.", show_alert=True)
            return
        bot.answer_callback_query(call.id, draft["content"], show_alert=True)

    @bot.callback_query_handler(func=lambda c: c.data == "env_send")
    def choose_type(call: telebot.types.CallbackQuery):
        draft = get_draft(call.from_user.id)
        if not draft:
            bot.answer_callback_query(call.id, "❌ لا يوجد ظرف جاهز.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"✉️ *اختيار نوع الإرسال*\n\n"
            f"📨 {draft['content'][:200]}{'...' if len(draft['content']) > 200 else ''}\n\n"
            f"اختر نوع الهمسة:",
            parse_mode="Markdown",
            reply_markup=_type_kb(),
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("env_send:"))
    def send_envelope(call: telebot.types.CallbackQuery):
        user = call.from_user
        wtype = call.data.split(":", 1)[1]

        draft = get_draft(user.id)
        if not draft:
            bot.answer_callback_query(call.id, "❌ لا يوجد ظرف جاهز.", show_alert=True)
            user_states.pop(user.id, None)
            return

        content = draft["content"]
        auto_delete_hours = 0
        try:
            if get_setting("auto_delete_enabled") == "1":
                auto_delete_hours = int(get_setting("auto_delete_hours"))
        except Exception:
            pass

        max_readers = 1 if wtype == "first_one" else (3 if wtype == "first_three" else 0)

        try:
            wid = create_whisper(
                sender_id=user.id,
                content=content,
                whisper_type=wtype,
                target_users=[],
                max_readers=max_readers,
                auto_delete_hours=auto_delete_hours,
            )
        except Exception as exc:
            logger.error(f"[ENVELOPE] create_whisper failed: {exc}")
            bot.answer_callback_query(call.id, "❌ فشل إنشاء الهمسة.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "✅ تم إرسال الهمسة!", show_alert=True)

        link = f"tg://resolve?domain={bot_username}&start=view_{wid}"
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("🔗 مشاركة الهمسة", url=link))

        type_labels = {
            "first_one": "لأول شخص ☝️",
            "first_three": "لأول 3 أشخاص 👥",
            "everyone": "للجميع 🌍",
        }
        label = type_labels.get(wtype, wtype)

        bot.send_message(
            user.id,
            f"✅ *تم إنشاء الهمسة بنجاح!*\n\n"
            f"📨 {content[:200]}{'...' if len(content) > 200 else ''}\n\n"
            f"👥 النوع: {label}\n"
            f"🆔 `{wid}`\n\n"
            f"🔗 اضغط لمشاركتها في أي مجموعة:",
            parse_mode="Markdown",
            reply_markup=kb,
        )

        delete_draft(user.id)
        user_states.pop(user.id, None)

    @bot.callback_query_handler(func=lambda c: c.data == "env_edit")
    def edit_envelope(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        user = call.from_user
        user_states[user.id] = {"action": "env_awaiting_content"}
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="env_delete"))
        bot.send_message(
            call.message.chat.id,
            "✏️ أرسل النص الجديد للظرف:",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data == "env_back")
    def back_to_preview(call: telebot.types.CallbackQuery):
        draft = get_draft(call.from_user.id)
        if not draft:
            bot.answer_callback_query(call.id, "❌ لا يوجد ظرف.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "env_delete")
    def delete_envelope(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id, "✅ حُذفت المسودة.")
        user_id = call.from_user.id
        delete_draft(user_id)
        user_states.pop(user_id, None)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass


def handle_envelope_message(bot: telebot.TeleBot, msg: telebot.types.Message,
                            user_states: dict) -> bool:
    user = msg.from_user
    state = user_states.get(user.id)
    if not state:
        return False
    action = state.get("action")

    if action == "env_awaiting_content":
        content = (msg.text or msg.caption or "").strip()
        if not content:
            bot.send_message(msg.chat.id, "⚠️ أرسل نصاً صالحاً للظرف.")
            return True

        create_draft(user.id, content)
        user_states[user.id] = {"action": "env_ready"}

        bot.send_message(
            msg.chat.id,
            f"✉️ *الظرف الشخصي*\n\n"
            f"📨 *المحتوى:*\n{content}\n\n"
            f"🔍 اختر ما تريد فعله:",
            parse_mode="Markdown",
            reply_markup=_preview_kb(),
        )
        return True

    return False
