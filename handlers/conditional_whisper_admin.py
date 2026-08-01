"""
handlers/conditional_whisper_admin.py — Admin panel for conditional whispers.

Design rules follow handlers/admin.py:
  1. EVERY callback handler calls bot.answer_callback_query() as the FIRST
     action — before any DB query or Telegram API call.
  2. All guard branches also answer the callback before returning.
  3. Text content is capped below Telegram's 4096-character limit.

NOT connected to the database yet — state is kept in the module-level flag
CONDITIONAL_WHISPERS_ENABLED until DB integration lands.
"""
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import back_button
from handlers.admin import _answer, _guard_admin, _safe_edit_text

logger = logging.getLogger(__name__)

CONDITIONAL_WHISPERS_ENABLED = True


def _conditional_whisper_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    """Keyboard for the conditional whispers admin panel."""
    kb = InlineKeyboardMarkup(row_width=1)
    if enabled:
        kb.add(
            InlineKeyboardButton("🔴 إيقاف النظام",
                                 callback_data="admin:conditional_whispers:disable"),
        )
    else:
        kb.add(
            InlineKeyboardButton("🟢 تشغيل النظام",
                                 callback_data="admin:conditional_whispers:enable"),
        )
    kb.add(
        InlineKeyboardButton("🔄 تحديث الحالة",
                             callback_data="admin:conditional_whispers:refresh"),
    )
    kb.add(back_button("admin:main"))
    return kb


def _conditional_whisper_text(enabled: bool) -> str:
    status = "🟢 مفعلة" if enabled else "🔴 متوقفة"
    return f"💡 إدارة الهمسات المشروطة\n\nالحالة: {status}"


def register_conditional_whisper_admin_handlers(bot: telebot.TeleBot) -> None:

    # ── Conditional whispers panel ───────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:conditional_whispers")
    def admin_conditional_whispers(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        _safe_edit_text(
            bot, call,
            _conditional_whisper_text(CONDITIONAL_WHISPERS_ENABLED),
            _conditional_whisper_keyboard(CONDITIONAL_WHISPERS_ENABLED),
        )

    # ── Disable ──────────────────────────────────────────────────────────────
    @bot.callback_query_handler(
        func=lambda c: c.data == "admin:conditional_whispers:disable"
    )
    def conditional_whispers_disable(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        global CONDITIONAL_WHISPERS_ENABLED
        CONDITIONAL_WHISPERS_ENABLED = False
        _answer(bot, call, "🔴 تم إيقاف النظام", alert=True)
        _safe_edit_text(
            bot, call,
            _conditional_whisper_text(CONDITIONAL_WHISPERS_ENABLED),
            _conditional_whisper_keyboard(CONDITIONAL_WHISPERS_ENABLED),
        )

    # ── Enable ───────────────────────────────────────────────────────────────
    @bot.callback_query_handler(
        func=lambda c: c.data == "admin:conditional_whispers:enable"
    )
    def conditional_whispers_enable(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        global CONDITIONAL_WHISPERS_ENABLED
        CONDITIONAL_WHISPERS_ENABLED = True
        _answer(bot, call, "🟢 تم تشغيل النظام", alert=True)
        _safe_edit_text(
            bot, call,
            _conditional_whisper_text(CONDITIONAL_WHISPERS_ENABLED),
            _conditional_whisper_keyboard(CONDITIONAL_WHISPERS_ENABLED),
        )

    # ── Refresh ──────────────────────────────────────────────────────────────
    @bot.callback_query_handler(
        func=lambda c: c.data == "admin:conditional_whispers:refresh"
    )
    def conditional_whispers_refresh(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        _safe_edit_text(
            bot, call,
            _conditional_whisper_text(CONDITIONAL_WHISPERS_ENABLED),
            _conditional_whisper_keyboard(CONDITIONAL_WHISPERS_ENABLED),
        )
