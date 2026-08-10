"""
handlers/variant_whisper.py — Variant Whisper Wizard (Stage 2)

Collects 2..5 text variants from a user in a private chat. Each reader will
see exactly one deterministic variant (see services.whisper_service.resolve_variant).

Stage 2 creates a *draft* (whisper_drafts row) — no whisper is created yet.
The completion message offers a "📤 مشاركة الهمسة" button that opens the
inline query list (handlers/inline.py, "v:" prefix) to pick the type and
post the whisper. There is no dashboard from this wizard itself.

Flow:
  1. User taps "🧬 همسة متغيرة" in the main menu → callback vwhisper_start.
  2. User sends text variants (2..5). Media / commands are rejected
     (except /cancel).
  3. User taps "✅ تم" (after >= 2 variants) or the 5th variant is reached.
  4. A whisper_drafts row is created:
       category="variant"
       content=first variant
       conditions_data={"variants": [...]}
"""

import json
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import cancel_button
from database import upsert_user
from database.envelope import create_draft, get_draft, update_draft_target

logger = logging.getLogger(__name__)

MIN_VARIANTS = 2
MAX_VARIANTS = 5

ACTION = "vwhisper_awaiting_variant"


def _cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(cancel_button("vwhisper_cancel"))
    return kb


def _done_cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("✅ تم", callback_data="vwhisper_done"))
    kb.add(cancel_button("vwhisper_cancel"))
    return kb


def _preview_text(variants) -> str:
    return "\n".join(f"{i + 1}. {v}" for i, v in enumerate(variants))


def register_variant_whisper_handlers(bot: telebot.TeleBot, user_states: dict):

    @bot.callback_query_handler(func=lambda c: c.data == "vwhisper_start")
    def vwhisper_start(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        user_states[user.id] = {
            "action": ACTION,
            "variants": [],
        }
        bot.send_message(
            call.message.chat.id,
            "🧬 همسة متغيرة\n\n"
            "كل قارئ سيرى نسخة واحدة (نفس القارئ يرى نفس النسخة دائماً).\n\n"
            f"أرسل النسخة الأولى (نص فقط) — مطلوب من {MIN_VARIANTS} إلى {MAX_VARIANTS} نسخ:",
            reply_markup=_cancel_kb(),
        )

    @bot.callback_query_handler(func=lambda c: c.data == "vwhisper_done")
    def vwhisper_done(call: telebot.types.CallbackQuery):
        user = call.from_user
        state = user_states.get(user.id)
        if not state or state.get("action") != ACTION:
            bot.answer_callback_query(call.id, "❌ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
            return
        variants = state.get("variants") or []
        if len(variants) < MIN_VARIANTS:
            bot.answer_callback_query(
                call.id,
                f"تحتاج إلى نسختين على الأقل. أرسل المزيد.",
                show_alert=True,
            )
            return
        bot.answer_callback_query(call.id)
        _finalize_variant_draft(bot, user.id, variants, user_states)

    @bot.callback_query_handler(func=lambda c: c.data == "vwhisper_cancel")
    def vwhisper_cancel(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id, "✅ أُلغي.")
        user_states.pop(call.from_user.id, None)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass


def handle_variant_whisper_message(bot, msg, user_states) -> bool:
    """Consume a message for the variant wizard; returns True if handled."""
    user = msg.from_user
    state = user_states.get(user.id)
    if not state:
        return False
    if state.get("action") != ACTION:
        return False
    if not (msg.chat and msg.chat.type == "private"):
        return False

    if msg.content_type != "text":
        bot.send_message(msg.chat.id, "⚠️ النسخ نصية فقط في الهمسة المتغيرة.")
        return True

    text = (msg.text or "").strip()

    if text.startswith("/"):
        if text.lower().startswith("/cancel"):
            user_states.pop(user.id, None)
            bot.send_message(msg.chat.id, "✅ أُلغي.")
            return True
        bot.send_message(
            msg.chat.id,
            "⚠️ هذه ليست نسخة صالحة. أرسل نص النسخة، أو /cancel للإلغاء.",
        )
        return True

    if not text:
        bot.send_message(msg.chat.id, "⚠️ أرسل نسخة نصية غير فارغة.")
        return True

    variants = list(state.get("variants") or [])
    if len(variants) >= MAX_VARIANTS:
        bot.send_message(
            msg.chat.id,
            f"⚠️ وصلت إلى الحد الأقصى ({MAX_VARIANTS} نسخ).",
            reply_markup=_done_cancel_kb(),
        )
        return True

    variants.append(text)

    if len(variants) < MIN_VARIANTS:
        user_states[user.id] = {"action": ACTION, "variants": variants}
        bot.send_message(
            msg.chat.id,
            f"تم حفظ النسخة {len(variants)}.\n\nأرسل النسخة الثانية:",
            reply_markup=_cancel_kb(),
        )
        return True

    if len(variants) == MAX_VARIANTS:
        _finalize_variant_draft(bot, user.id, variants, user_states)
        return True

    user_states[user.id] = {"action": ACTION, "variants": variants}
    bot.send_message(
        msg.chat.id,
        f"تم حفظ النسخة {len(variants)}.\n\n"
        f"النسخ الحالية:\n{_preview_text(variants)}\n\n"
        "أرسل النسخة التالية، أو اضغط «✅ تم».",
        reply_markup=_done_cancel_kb(),
    )
    return True


def _finalize_variant_draft(bot, user_id: int, variants: list, user_states: dict) -> None:
    """Create a whisper_drafts row only — no whisper, no inline, no dashboard."""
    try:
        create_draft(
            user_id,
            content=variants[0],
            category="variant",
            conditions_data=json.dumps({"variants": variants}, ensure_ascii=False),
        )
    except Exception as exc:
        logger.error("[VWHISPER] create_draft failed: %s", exc)
        bot.send_message(user_id, "❌ فشل حفظ المسودة.")
        return

    try:
        update_draft_target(user_id, 0)
    except Exception as exc:
        logger.error("[VWHISPER] update_draft_target failed: %s", exc)
        bot.send_message(user_id, "❌ فشل تجهيز المسودة.")
        return

    draft = get_draft(user_id)
    if not draft:
        bot.send_message(user_id, "❌ فشل تجهيز المسودة.")
        return

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📤 مشاركة الهمسة", switch_inline_query=f"v:{draft['id']}"))

    bot.send_message(
        user_id,
        f"✅ تم تجهيز مسودة الهمسة المتغيرة!\n\n"
        f"النسخ ({len(variants)}):\n{_preview_text(variants)}\n\n"
        "اضغط زر المشاركة، ثم اختر نوع الهمسة من القائمة.",
        reply_markup=kb,
    )
    user_states.pop(user_id, None)
