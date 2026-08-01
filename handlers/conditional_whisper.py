import hashlib
import json
import logging
import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import cancel_button
from database import upsert_user
from database.envelope import create_draft, get_draft, update_draft_target

logger = logging.getLogger(__name__)


def _hash_password(password: str) -> dict:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000,
        dklen=32,
    )
    return {
        "hash": dk.hex(),
        "salt": salt,
        "algorithm": "pbkdf2_sha256",
        "iterations": 100000,
        "hint": "",
    }


def _cancel_kb():
    kb = InlineKeyboardMarkup()
    kb.add(cancel_button("cw_cancel"))
    return kb


# ── Condition-type options for the conditional-whisper wizard ───────────
# (label, condition_type). Extend here to add future conditions such as
# channel membership or a time window.
CONDITION_OPTIONS = [
    ("🔑 كلمة مرور", "password"),
    ("❓ سؤال وإجابة", "question"),
]


def _condition_label(state: dict) -> str:
    """Human-readable label for the condition stored in the current draft."""
    conditions_data = state.get("conditions_data") or {}
    if isinstance(conditions_data, str):
        try:
            conditions_data = json.loads(conditions_data)
        except (json.JSONDecodeError, TypeError):
            conditions_data = {}
    if "question" in conditions_data:
        return "❓ محمية بسؤال وجواب"
    if "password" in conditions_data:
        return "🔐 محمية بكلمة مرور"
    return "🔐 همسة مشروطة"


def register_conditional_whisper_handlers(bot: telebot.TeleBot, user_states: dict):
    try:
        bot_username = bot.get_me().username
    except Exception:
        bot_username = ""

    @bot.callback_query_handler(func=lambda c: c.data == "cwhisper_start")
    def cwhisper_start(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        user_states[user.id] = {"action": "cw_awaiting_condition_type"}
        kb = InlineKeyboardMarkup(row_width=1)
        for label, cond_type in CONDITION_OPTIONS:
            kb.add(InlineKeyboardButton(label, callback_data=f"cw_cond:{cond_type}"))
        kb.add(cancel_button("cw_cancel"))
        bot.send_message(
            call.message.chat.id,
            "🛡 *اختر نوع الشرط*",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("cw_cond:"))
    def cw_cond(call: telebot.types.CallbackQuery):
        user = call.from_user
        cond_type = call.data.split(":", 1)[1]
        state = user_states.get(user.id)
        if not state or state.get("action") != "cw_awaiting_condition_type":
            bot.answer_callback_query(call.id, "❌ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
            return

        if cond_type == "password":
            bot.answer_callback_query(call.id)
            user_states[user.id] = {"action": "cw_awaiting_password"}
            bot.send_message(
                call.message.chat.id,
                "🔐 *همسة مشروطة*\n\n"
                "أرسل كلمة المرور للهمسة:",
                parse_mode="Markdown",
                reply_markup=_cancel_kb(),
            )
        elif cond_type == "question":
            bot.answer_callback_query(call.id)
            user_states[user.id] = {"action": "cw_awaiting_question"}
            bot.send_message(
                call.message.chat.id,
                "❓ أرسل السؤال.",
                reply_markup=_cancel_kb(),
            )
        else:
            bot.answer_callback_query(call.id, "❌ شرط غير معروف.", show_alert=True)

    @bot.callback_query_handler(func=lambda c: c.data == "cw_cancel")
    def cw_cancel(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id, "✅ أُلغي.")
        user_states.pop(call.from_user.id, None)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass


def handle_conditional_whisper_message(
    bot: telebot.TeleBot, msg: telebot.types.Message, user_states: dict
) -> bool:
    user = msg.from_user
    state = user_states.get(user.id)
    if not state:
        return False
    action = state.get("action")

    if action == "cw_awaiting_password":
        password = (msg.text or "").strip()
        if not password:
            bot.send_message(msg.chat.id, "⚠️ أرسل كلمة المرور.")
            return True
        if password.startswith("/"):
            bot.send_message(msg.chat.id, "⚠️ كلمة المرور لا يمكن أن تبدأ بـ /")
            return True

        user_states[user.id] = {
            "action": "cw_awaiting_confirmation",
            "cw_password": password,
        }
        kb = InlineKeyboardMarkup()
        kb.add(cancel_button("cw_cancel"))
        bot.send_message(
            msg.chat.id,
            "🔐 أعد كتابة كلمة المرور للتأكيد:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return True

    if action == "cw_awaiting_confirmation":
        confirm = (msg.text or "").strip()
        saved_password = state.get("cw_password", "")
        if not confirm:
            bot.send_message(msg.chat.id, "⚠️ أعد كتابة كلمة المرور للتأكيد.")
            return True

        if confirm != saved_password:
            kb = InlineKeyboardMarkup()
            kb.add(cancel_button("cw_cancel"))
            bot.send_message(
                msg.chat.id,
                "❌ كلمة المرور غير متطابقة! أعد المحاولة:\n\n"
                "أرسل كلمة المرور من جديد:",
                reply_markup=kb,
            )
            user_states[user.id] = {"action": "cw_awaiting_password"}
            return True

        password_config = _hash_password(saved_password)
        user_states[user.id] = {
            "action": "cw_awaiting_content",
            "cw_password_config": password_config,
            "conditions_data": {
                "password": password_config,
            },
        }
        kb = InlineKeyboardMarkup()
        kb.add(cancel_button("cw_cancel"))
        bot.send_message(
            msg.chat.id,
            "✅ تم حفظ كلمة المرور.\n\n"
            "📝 أرسل نص الهمسة:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return True

    if action == "cw_awaiting_question":
        question = (msg.text or "").strip()
        if not question:
            bot.send_message(msg.chat.id, "⚠️ أرسل السؤال.")
            return True
        if question.startswith("/"):
            bot.send_message(msg.chat.id, "⚠️ السؤال لا يمكن أن يبدأ بـ /")
            return True

        user_states[user.id] = {
            "action": "cw_awaiting_answer",
            "cw_question": question,
        }
        bot.send_message(
            msg.chat.id,
            "✍️ أرسل الإجابة الصحيحة.",
            reply_markup=_cancel_kb(),
        )
        return True

    if action == "cw_awaiting_answer":
        answer = (msg.text or "").strip()
        question = state.get("cw_question", "")
        if not answer:
            bot.send_message(msg.chat.id, "⚠️ أرسل الإجابة الصحيحة.")
            return True
        if not question:
            bot.send_message(msg.chat.id, "❌ انتهت الجلسة. ابدأ من جديد.")
            user_states.pop(user.id, None)
            return True

        answer_config = _hash_password(answer)
        answer_config["question"] = question
        user_states[user.id] = {
            "action": "cw_awaiting_content",
            "cw_question": question,
            "conditions_data": {
                "question": answer_config,
            },
        }
        bot.send_message(
            msg.chat.id,
            "✅ تم حفظ السؤال والإجابة.\n\n"
            "📝 أرسل نص الهمسة:",
            parse_mode="Markdown",
            reply_markup=_cancel_kb(),
        )
        return True

    if action == "cw_awaiting_content":
        content = (msg.text or msg.caption or "").strip()
        if not content:
            bot.send_message(msg.chat.id, "⚠️ أرسل نصاً صالحاً للهمسة.")
            return True

        conditions_data = state.get("conditions_data") or {}
        try:
            create_draft(
                user.id,
                content,
                conditions_data=json.dumps(conditions_data),
            )
        except Exception as exc:
            logger.error(f"[CWHISPER] create_draft failed: {exc}")
            bot.send_message(msg.chat.id, "❌ فشل إنشاء الهمسة.")
            return True

        try:
            update_draft_target(user.id, 0)
        except Exception as exc:
            logger.error(f"[CWHISPER] update_draft_target failed: {exc}")
            bot.send_message(msg.chat.id, "❌ فشل تجهيز الهمسة.")
            return True

        draft = get_draft(user.id)
        if not draft:
            bot.send_message(msg.chat.id, "❌ فشل تجهيز الهمسة.")
            return True
        draft_id = draft["id"]

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("📤 مشاركة الهمسة", switch_inline_query=f"cw:{draft_id}"))
        kb.add(cancel_button("cw_cancel"))

        bot.send_message(
            msg.chat.id,
            f"✅ *تم تجهيز الهمسة المشروطة بنجاح!*\n\n"
            f"{_condition_label(state)}\n"
            f"📝 {content[:200]}{'...' if len(content) > 200 else ''}\n\n"
            f"اضغط زر المشاركة، ثم اختر نوع الهمسة من القائمة:",
            parse_mode="Markdown",
            reply_markup=kb,
        )

        user_states.pop(user.id, None)
        return True

    return False
