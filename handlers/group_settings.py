import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import get_group_settings, update_group_setting
from handlers.admin import _answer, _guard_admin

logger = logging.getLogger(__name__)

_SETTING_LABELS = {
    "public_whispers_enabled": "الهمسات العامة",
    "anonymous_enabled": "الهمسات المجهولة",
    "read_notifications": "إشعارات القراءة",
}

_TOGGLE_KEYS = ["public_whispers_enabled", "anonymous_enabled", "read_notifications"]


def _build_settings_text(chat_id: int) -> str:
    settings = get_group_settings(chat_id)
    lines = ["⚙️ *إعدادات الهمسات*\n"]
    for key in _TOGGLE_KEYS:
        val = settings.get(key, 1)
        icon = "🟢" if val else "🔴"
        lines.append(f"{icon} {_SETTING_LABELS[key]}")
    auto_val = settings.get("auto_delete_minutes", 0)
    if auto_val and auto_val > 0:
        lines.append(f"🕒 الحذف التلقائي: {auto_val} دقيقة")
    else:
        lines.append("🕒 الحذف التلقائي: معطل")
    return "\n".join(lines)


def _settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    settings = get_group_settings(chat_id)
    kb = InlineKeyboardMarkup(row_width=1)
    for key in _TOGGLE_KEYS:
        val = settings.get(key, 1)
        icon = "🟢" if val else "🔴"
        kb.add(InlineKeyboardButton(
            f"{icon} {_SETTING_LABELS[key]}",
            callback_data=f"group_toggle:{key}",
        ))
    auto_val = settings.get("auto_delete_minutes", 0)
    if auto_val and auto_val > 0:
        kb.add(InlineKeyboardButton(
            f"🕒 الحذف التلقائي: {auto_val} دقيقة",
            callback_data="group_toggle:auto_delete_minutes",
        ))
    else:
        kb.add(InlineKeyboardButton(
            "🕒 الحذف التلقائي: معطل",
            callback_data="group_toggle:auto_delete_minutes",
        ))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:main"))
    return kb


def register_group_settings_handlers(bot: telebot.TeleBot, user_states: dict) -> None:

    @bot.callback_query_handler(func=lambda c: c.data == "admin:group_settings")
    def show_group_settings(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        chat_id = call.message.chat.id
        text = _build_settings_text(chat_id)
        kb = _settings_keyboard(chat_id)
        try:
            bot.edit_message_text(
                text, chat_id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb,
            )
        except Exception:
            try:
                bot.send_message(
                    chat_id, text,
                    parse_mode="Markdown", reply_markup=kb,
                )
            except Exception as exc:
                logger.error(f"show_group_settings failed: {exc}")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("group_toggle:"))
    def toggle_group_setting(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        key = call.data.split(":", 1)[1]
        chat_id = call.message.chat.id
        settings = get_group_settings(chat_id)
        current = settings.get(key)

        if key == "auto_delete_minutes":
            new_val = 0 if current and current > 0 else 5
        else:
            new_val = 0 if current else 1

        update_group_setting(chat_id, key, new_val)

        try:
            text = _build_settings_text(chat_id)
            kb = _settings_keyboard(chat_id)
            bot.edit_message_text(
                text, chat_id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb,
            )
        except Exception:
            pass
