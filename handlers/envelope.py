import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import cancel_button
from database import upsert_user
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


def _chat_selection_kb(draft_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📤 مشاركة الهمسة", switch_inline_query=f"cw:{draft_id}"))
    kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="env_delete"))
    return kb


def register_envelope_handlers(bot: telebot.TeleBot, user_states: dict):

    # ── Placeholder button handler (prevents Telegram "query is invalid" errors) ──
    @bot.callback_query_handler(func=lambda c: c.data == "cw_processing")
    def placeholder_button(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id, "⏳ جاري تجهيز الهمسة... انتظر لحظة.")

    @bot.callback_query_handler(func=lambda c: c.data == "env_new")
    def start_envelope(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        user_states[user.id] = {"action": "env_awaiting_content"}
        kb = InlineKeyboardMarkup()
        kb.add(cancel_button("env_delete"))
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
    def choose_chat(call: telebot.types.CallbackQuery):
        draft = get_draft(call.from_user.id)
        if not draft:
            bot.answer_callback_query(call.id, "❌ لا يوجد ظرف جاهز.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"✉️ *اختيار المحادثة*\n\n"
            f"📨 {draft['content'][:200]}{'...' if len(draft['content']) > 200 else ''}\n\n"
            f"اضغط زر المشاركة لاختيار المحادثة، ثم اختر نوع الهمسة:",
            parse_mode="Markdown",
            reply_markup=_chat_selection_kb(draft["id"]),
        )

    @bot.callback_query_handler(func=lambda c: c.data == "env_edit")
    def edit_envelope(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        user = call.from_user
        user_states[user.id] = {"action": "env_awaiting_content"}
        kb = InlineKeyboardMarkup()
        kb.add(cancel_button("env_delete"))
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

        create_draft(
            user.id, content,
            conditions_data=state.get("conditions_data") or "",
        )
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
