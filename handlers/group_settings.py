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
_AUTO_DELETE_PRESETS = [0, 1, 5, 10, 30, 60]


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
    kb = InlineKeyboardMarkup(row_width=3)
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
            callback_data="noop",
        ))
    else:
        kb.add(InlineKeyboardButton(
            "🕒 الحذف التلقائي: معطل",
            callback_data="noop",
        ))
    presets = _AUTO_DELETE_PRESETS
    kb.add(
        InlineKeyboardButton(
            f"{'✅ ' if auto_val == presets[0] else ''}{presets[0]} دقيقة",
            callback_data=f"group_autodel_set:{presets[0]}",
        ),
        InlineKeyboardButton(
            f"{'✅ ' if auto_val == presets[1] else ''}{presets[1]} دقيقة",
            callback_data=f"group_autodel_set:{presets[1]}",
        ),
        InlineKeyboardButton(
            f"{'✅ ' if auto_val == presets[2] else ''}{presets[2]} دقائق",
            callback_data=f"group_autodel_set:{presets[2]}",
        ),
    )
    kb.add(
        InlineKeyboardButton(
            f"{'✅ ' if auto_val == presets[3] else ''}{presets[3]} دقائق",
            callback_data=f"group_autodel_set:{presets[3]}",
        ),
        InlineKeyboardButton(
            f"{'✅ ' if auto_val == presets[4] else ''}{presets[4]} دقيقة",
            callback_data=f"group_autodel_set:{presets[4]}",
        ),
        InlineKeyboardButton(
            f"{'✅ ' if auto_val == presets[5] else ''}{presets[5]} دقيقة",
            callback_data=f"group_autodel_set:{presets[5]}",
        ),
    )
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
