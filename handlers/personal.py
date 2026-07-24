import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import back_button, cancel_button
from database import upsert_user
from database.personal import (
    create_personal_whisper, get_personal_whisper,
    get_user_inbox, get_user_sent,
    mark_as_read, count_unread,
)

logger = logging.getLogger(__name__)


def register_personal_handlers(bot: telebot.TeleBot, user_states: dict):

    @bot.callback_query_handler(func=lambda c: c.data == "pers_menu")
    def personal_menu(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        _show_personal_menu(bot, call.message.chat.id, user, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("pers_inbox:"))
    def handle_inbox(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        user = call.from_user
        try:
            offset = int(call.data.split(":", 1)[1])
        except (IndexError, ValueError):
            offset = 0
        _show_inbox(bot, call.message, user.id, offset)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("pers_sent:"))
    def handle_sent(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        user = call.from_user
        try:
            offset = int(call.data.split(":", 1)[1])
        except (IndexError, ValueError):
            offset = 0
        _show_sent(bot, call.message, user.id, offset)

    @bot.callback_query_handler(func=lambda c: c.data == "pers_new:")
    def start_send(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        user_states[user.id] = {"action": "pers_awaiting_target"}
        kb = InlineKeyboardMarkup()
        kb.add(cancel_button("pers_cancel"))
        bot.send_message(
            call.message.chat.id,
            "📝 *إرسال همسة شخصية*\n\n"
            "أرسل معرف المستخدم (ID) أو اليوزر (@username)،\n"
            "أو قم بالرد على إحدى رسائله:",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data == "pers_cancel")
    def cancel_send(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id, "✅ أُلغي.")
        user_states.pop(call.from_user.id, None)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith("pers_read:"))
    def handle_read(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        pw = get_personal_whisper(whisper_id)
        if not pw:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if pw["recipient_id"] != user.id and pw["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذه الهمسة ليست لك.", show_alert=True)
            return
        if pw["recipient_id"] == user.id and not pw["is_read"]:
            mark_as_read(whisper_id, user.id)
        sender_name = pw["sender_name"] or f"المستخدم {pw['sender_id']}"
        text = (
            f"📩 *همسة شخصية*\n\n"
            f"من: {sender_name} (`{pw['sender_id']}`)\n"
            f"الوقت: {pw['created_at'][:16]}\n\n"
            f"{pw['content']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 رجوع للوارد", callback_data="pers_inbox:0"))
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb,
            )
        except Exception:
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "pers_back")
    def back_to_menu(call: telebot.types.CallbackQuery):
        user = call.from_user
        bot.answer_callback_query(call.id)
        _show_personal_menu(bot, call.message.chat.id, user, call.message.message_id)


def handle_personal_send_message(bot: telebot.TeleBot, msg: telebot.types.Message,
                                 user_states: dict) -> bool:
    user = msg.from_user
    state = user_states.get(user.id)
    if not state:
        return False
    action = state.get("action")

    if action == "pers_awaiting_target":
        target_id, hint = _resolve_target(bot, msg)
        if target_id is None:
            bot.send_message(msg.chat.id, hint or "❌ لم أتمكن من العثور على المستخدم. تأكد من صحة المعرف.")
            return True
        if target_id == user.id:
            bot.send_message(msg.chat.id, "❌ لا يمكنك إرسال همسة لنفسك.")
            return True
        user_states[user.id] = {"action": "pers_awaiting_message", "target_id": target_id}
        kb = InlineKeyboardMarkup()
        kb.add(cancel_button("pers_cancel"))
        bot.send_message(
            msg.chat.id,
            f"✅ تم تحديد المستخدم `{target_id}`.\n\nالآن أرسل نص الهمسة:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return True

    if action == "pers_awaiting_message":
        content = (msg.text or msg.caption or "").strip()
        if not content:
            bot.send_message(msg.chat.id, "⚠️ أرسل نصاً صالحاً للهمسة.")
            return True
        target_id = state.get("target_id")
        if not target_id:
            bot.send_message(msg.chat.id, "❌ خطأ: لم يتم تحديد المستهدف. ابدأ من جديد.")
            del user_states[user.id]
            return True
        wid = create_personal_whisper(user.id, target_id, content)
        sender_name = user.first_name or f"المستخدم {user.id}"
        delivered = _deliver_notification(bot, target_id, user.id, wid, sender_name)
        if delivered:
            bot.send_message(
                msg.chat.id,
                f"✅ *تم إرسال الهمسة الشخصية بنجاح!*\n\n📤 إلى: `{target_id}`",
                parse_mode="Markdown",
            )
        else:
            bot.send_message(
                msg.chat.id,
                f"✅ *تم إرسال الهمسة الشخصية بنجاح!*\n\n"
                f"📤 إلى: `{target_id}`\n\n"
                f"⚠️ تعذر إرسال الإشعار للمستخدم — لم يبدأ البوت بعد أو قام بحظره.",
                parse_mode="Markdown",
            )
        del user_states[user.id]
        return True

    return False


def _resolve_target(bot, msg):
    text = (msg.text or "").strip()

    if msg.reply_to_message and msg.reply_to_message.from_user:
        sender = msg.reply_to_message.from_user
        if not sender.is_bot:
            return sender.id, None

    if not text:
        return None, "⚠️ أرسل معرف المستخدم أو اليوزر، أو قم بالرد على رسالة مستخدم."

    if text.startswith("@"):
        username = text[1:].lower()
        from database import search_users
        matches = search_users(username)
        for u in matches:
            if u["username"] and u["username"].lower() == username:
                return u["user_id"], None
        return None, f"❌ لم أجد مستخدمًا باليوزر `{text}`. تأكد من صحة اليوزر وأن المستخدم قد استخدم البوت سابقًا."

    try:
        uid = int(text)
        if uid <= 0:
            return None, "⚠️ المعرف الرقمي يجب أن يكون عددًا صحيحًا موجبًا."
        return uid, None
    except ValueError:
        return None, f"❌ المدخل `{text}` ليس معرفًا رقميًا ولا يوزر (@username)."


def _deliver_notification(bot, recipient_id, sender_id, whisper_id, sender_name):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📩 عرض الهمسة", callback_data=f"pers_read:{whisper_id}"))
    try:
        bot.send_message(
            recipient_id,
            f"📩 *همسة شخصية جديدة!*\n\n"
            f"لقد أرسل لك {sender_name} 🤫\n"
            f"اضغط الزر أدناه لقراءتها:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return True
    except Exception:
        return False


def _show_personal_menu(bot, chat_id, user, edit_msg_id=None):
    unread = count_unread(user.id)
    unread_badge = f" ({unread} جديدة)" if unread > 0 else ""
    text = (
        f"🤫 *الهمسات الشخصية*\n\n"
        f"أهلاً بك يا `{user.id}` 👋\n"
        f"يمكنك إرسال همسة خاصة لشخص معين، ولن يراها أحد غيره.\n\n"
        f"📊 همسات غير مقروءة: `{unread}`{unread_badge}"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📩 صندوق الوارد", callback_data="pers_inbox:0"),
        InlineKeyboardButton("📤 الهمسات المُرسلة", callback_data="pers_sent:0"),
    )
    kb.add(InlineKeyboardButton("✉️ إرسال همسة شخصية", callback_data="pers_new:"))
    kb.add(back_button("back_to_main"))
    if edit_msg_id:
        try:
            bot.edit_message_text(text, chat_id, edit_msg_id, parse_mode="Markdown", reply_markup=kb)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


def _show_inbox(bot, message, user_id, offset=0):
    rows, total = get_user_inbox(user_id, limit=5, offset=offset)
    if not rows:
        try:
            bot.edit_message_text(
                "📭 *صندوق الوارد*\n\nلا توجد همسات شخصية.",
                message.chat.id, message.message_id,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup().add(
                    back_button("pers_back")),
            )
        except Exception:
            bot.send_message(message.chat.id, "📭 *صندوق الوارد*\n\nلا توجد همسات شخصية.",
                             parse_mode="Markdown")
        return
    kb = InlineKeyboardMarkup(row_width=1)
    for pw in rows:
        status = "✅" if pw["is_read"] else "🆕"
        name = pw["sender_name"] or f"المستخدم {pw['sender_id']}"
        kb.add(InlineKeyboardButton(
            f"{status} {name}", callback_data=f"pers_read:{pw['whisper_id']}"))
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"pers_inbox:{offset - 5}"))
    nav.append(InlineKeyboardButton(f"{offset // 5 + 1}", callback_data="noop"))
    if offset + 5 < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"pers_inbox:{offset + 5}"))
    if nav:
        kb.row(*nav)
    kb.add(back_button("pers_back"))
    try:
        bot.edit_message_text(
            f"📩 *صندوق الوارد* (إجمالي: {total})",
            message.chat.id, message.message_id,
            parse_mode="Markdown", reply_markup=kb,
        )
    except Exception:
        bot.send_message(
            message.chat.id, f"📩 *صندوق الوارد* (إجمالي: {total})",
            parse_mode="Markdown", reply_markup=kb,
        )


def _show_sent(bot, message, user_id, offset=0):
    rows, total = get_user_sent(user_id, limit=5, offset=offset)
    if not rows:
        try:
            bot.edit_message_text(
                "📤 *الهمسات المُرسلة*\n\nلم ترسل أي همسات شخصية بعد.",
                message.chat.id, message.message_id,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup().add(
                    back_button("pers_back")),
            )
        except Exception:
            bot.send_message(message.chat.id, "📤 *الهمسات المُرسلة*\n\nلم ترسل أي همسات شخصية بعد.",
                             parse_mode="Markdown")
        return
    lines = []
    for pw in rows:
        status = "✅ مقروءة" if pw["is_read"] else "🕐 في الانتظار"
        name = pw["recipient_name"] or f"المستخدم {pw['recipient_id']}"
        lines.append(f"• إلى {name} — {status}")
    kb = InlineKeyboardMarkup(row_width=2)
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"pers_sent:{offset - 5}"))
    nav.append(InlineKeyboardButton(f"{offset // 5 + 1}", callback_data="noop"))
    if offset + 5 < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"pers_sent:{offset + 5}"))
    if nav:
        kb.row(*nav)
    kb.add(back_button("pers_back"))
    text = f"📤 *الهمسات المُرسلة* (إجمالي: {total})\n\n" + "\n".join(lines)
    try:
        bot.edit_message_text(
            text, message.chat.id, message.message_id,
            parse_mode="Markdown", reply_markup=kb,
        )
    except Exception:
        bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=kb)
