from telebot.types import InlineKeyboardButton

_NOOP = "noop"


def back_button(callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 رجوع", callback_data=callback_data)


def cancel_button(callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("❌ إلغاء", callback_data=callback_data)


def confirm_button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(f"✅ {text}", callback_data=callback_data)


def page_indicator(current: int, total_pages: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(f"📄 {current} / {total_pages}", callback_data=_NOOP)
