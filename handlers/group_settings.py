import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import back_button, section_header, status_button
from database import get_group_settings, update_group_setting
from handlers.admin import _answer, _guard_admin

logger = logging.getLogger(__name__)

_SETTING_LABELS = {
    "public_whispers_enabled": "الهمسات العامة",
    "anonymous_enabled": "الهمسات المجهولة",
    "read_notifications": "إشعارات القراءة",
    "spam_limit_enabled": "حماية Spam",
}

_TOGGLE_KEYS = ["public_whispers_enabled", "anonymous_enabled", "read_notifications"]
_AUTO_DELETE_PRESETS = [0, 1, 5, 10, 30, 60]
_SPAM_COUNT_PRESETS = [3, 5, 10]


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
    spam_enabled = settings.get("spam_limit_enabled", 1)
    spam_count = settings.get("spam_limit_count", 5)
    spam_icon = "🟢" if spam_enabled else "🔴"
    if spam_enabled:
        lines.append(f"{spam_icon} حماية Spam: {spam_count} همسة / {settings.get('spam_limit_window_seconds', 60)} ث")
    else:
        lines.append(f"{spam_icon} حماية Spam: معطل")
    return "\n".join(lines)


def _settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    settings = get_group_settings(chat_id)
    kb = InlineKeyboardMarkup(row_width=2)

    # 💬 الهمسات
    kb.add(section_header("💬 الهمسات"))
    for key in _TOGGLE_KEYS:
        val = settings.get(key, 1)
        icon = "🟢" if val else "🔴"
        kb.add(InlineKeyboardButton(
            f"{icon} {_SETTING_LABELS[key]}",
            callback_data=f"group_toggle:{key}",
        ))

    # 🛡️ الحماية
    kb.add(section_header("🛡️ الحماية"))
    spam_enabled = settings.get("spam_limit_enabled", 1)
    spam_icon = "🟢" if spam_enabled else "🔴"
    kb.add(InlineKeyboardButton(
        f"{spam_icon} {_SETTING_LABELS['spam_limit_enabled']}",
        callback_data="group_toggle:spam_limit_enabled",
    ))
    spam_count = settings.get("spam_limit_count", 5)
    kb.add(status_button("عدد الهمسات", str(spam_count), "🚫"))
    spam_presets = []
    for preset in _SPAM_COUNT_PRESETS:
        label = f"{'✅ ' if spam_count == preset else ''}{preset}"
        spam_presets.append(InlineKeyboardButton(
            label, callback_data=f"group_spam_set:{preset}",
        ))
    kb.row(*spam_presets)

    # 🗑️ الحذف التلقائي
    auto_val = settings.get("auto_delete_minutes", 0)
    kb.add(section_header("🗑️ الحذف التلقائي"))
    if auto_val and auto_val > 0:
        kb.add(status_button("المدة", f"{auto_val} دقيقة", "⏱"))
    else:
        kb.add(status_button("المدة", "معطل", "⏱"))
    presets = _AUTO_DELETE_PRESETS
    auto_buttons = [
        InlineKeyboardButton(
            f"{'✅ ' if auto_val == p else ''}{p} {'دقائق' if p in (5, 10) else 'دقيقة'}",
            callback_data=f"group_autodel_set:{p}",
        ) for p in presets
    ]
    for i in range(0, len(auto_buttons), 2):
        kb.add(*auto_buttons[i:i + 2])

    kb.add(back_button("admin:main"))
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

    @bot.callback_query_handler(func=lambda c: c.data.startswith("group_autodel_set:"))
    def set_auto_delete_minutes(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        value = int(call.data.split(":", 1)[1])
        chat_id = call.message.chat.id
        update_group_setting(chat_id, "auto_delete_minutes", value)

        try:
            text = _build_settings_text(chat_id)
            kb = _settings_keyboard(chat_id)
            bot.edit_message_text(
                text, chat_id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb,
            )
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith("group_spam_set:"))
    def set_spam_limit_count(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        value = int(call.data.split(":", 1)[1])
        chat_id = call.message.chat.id
        update_group_setting(chat_id, "spam_limit_count", value)

        try:
            text = _build_settings_text(chat_id)
            kb = _settings_keyboard(chat_id)
            bot.edit_message_text(
                text, chat_id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb,
            )
        except Exception:
            pass
