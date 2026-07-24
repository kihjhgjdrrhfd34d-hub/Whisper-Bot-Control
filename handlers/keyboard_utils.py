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


def section_header(text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=_NOOP)


def toggle_button(label: str, key: str, enabled: bool, icon_on: str = "✅", icon_off: str = "❌") -> InlineKeyboardButton:
    icon = icon_on if enabled else icon_off
    return InlineKeyboardButton(f"{icon} {label}", callback_data=f"toggle:{key}")


def status_button(label: str, status_text: str, status_icon: str = "ℹ️") -> InlineKeyboardButton:
    return InlineKeyboardButton(f"{status_icon} {label}: {status_text}", callback_data=_NOOP)