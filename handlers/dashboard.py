"""
handlers/dashboard.py — لوحة تحكم متكاملة للهمسة (Whisper Dashboard) v1.1

تُرسَل هذه اللوحة للمُرسِل في الخاص بعد إرسال أي همسة،
وتسمح له بمشاهدة الإحصائيات والقراء والردود وإعادة الإرسال والتثبيت
والحذف والإغلاق.

Callback data patterns (بادئة dsh = dashboard):
    dsh:show:<whisper_id>    — عرض لوحة التحكم
    dsh:stats:<whisper_id>   — عرض الإحصائيات
    dsh:rdrs:<whisper_id>    — عرض قائمة القراء
    dsh:rpls:<whisper_id>    — عرض الردود
    dsh:rsnd:<whisper_id>    — إعادة إرسال
    dsh:pin:<whisper_id>     — تثبيت / إلغاء تثبيت
    dsh:del:<whisper_id>     — حذف (مع تأكيد)
    dsh:cdel:<whisper_id>    — تأكيد الحذف
    dsh:close:<whisper_id>   — إغلاق الهمسة
    dsh:back:<whisper_id>    — عودة للوحة التحكم
"""

import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_whisper, get_readers, reader_count, delete_whisper,
    close_whisper, toggle_pin_whisper, create_whisper,
    get_setting,
)
from database.replies import count_replies, get_replies
from handlers._formatting import _get_sender_display

logger = logging.getLogger(__name__)

# ── تواريخ البادئات (لتجنب التكرار) ──────────────────────────────────────────
_DASH_PREFIX = "dsh:"

# ── دوال مساعدة ──────────────────────────────────────────────────────────────

def _get_type_label(whisper_type: str) -> str:
    labels = {
        "first_one": "لأول شخص ☝️",
        "everyone": "للجميع 🌍",
        "first_three": "لأول 3 👥",
        "custom": "مخصصة 🎯",
    }
    return labels.get(whisper_type, whisper_type)


def _format_time(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        return iso_str[:16].replace("T", " ")
    except Exception:
        return str(iso_str)



def _dash_id(wid: str) -> str:
    """Return a short display ID for the whisper."""
    return wid[:8].upper()


# ── بناء لوحة المفاتيح (الأزرار) ─────────────────────────────────────────────

def dashboard_keyboard(whisper_id: str) -> InlineKeyboardMarkup:
    """الأزرار الرئيسية للوحة التحكم."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 الإحصائيات", callback_data=f"{_DASH_PREFIX}stats:{whisper_id}"),
        InlineKeyboardButton("👁️ عرض القراء", callback_data=f"{_DASH_PREFIX}rdrs:{whisper_id}"),
    )
    kb.add(
        InlineKeyboardButton("💬 عرض الردود", callback_data=f"{_DASH_PREFIX}rpls:{whisper_id}"),
        InlineKeyboardButton("📤 إعادة إرسال", callback_data=f"{_DASH_PREFIX}rsnd:{whisper_id}"),
    )
    kb.add(
        InlineKeyboardButton("📌 تثبيت", callback_data=f"{_DASH_PREFIX}pin:{whisper_id}"),
        InlineKeyboardButton("🗑 حذف", callback_data=f"{_DASH_PREFIX}del:{whisper_id}"),
    )
    kb.add(
        InlineKeyboardButton("🔒 إغلاق", callback_data=f"{_DASH_PREFIX}close:{whisper_id}"),
    )
    return kb


def _back_button(whisper_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔙 عودة للوحة التحكم", callback_data=f"{_DASH_PREFIX}show:{whisper_id}"))
    return kb


# ── بناء نص اللوحة ───────────────────────────────────────────────────────────

def _build_dashboard_text(w) -> str:
    """بناء النص الأساسي للوحة التحكم."""
    w = dict(w)
    readers = get_readers(w["whisper_id"]) if callable(get_readers) else []
    r_count = len(readers)
    max_r = w.get("max_readers", 0)
    replies_count = count_replies(w["whisper_id"])
    created = _format_time(w.get("created_at", ""))
    closed = bool(w.get("is_closed", 0))
    pinned = bool(w.get("is_pinned", 0))

    type_label = _get_type_label(w["whisper_type"])
    display_id = _dash_id(w["whisper_id"])

    if max_r > 0:
        reads_line = f"{r_count} / {max_r}"
    else:
        reads_line = str(r_count)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📨 معلومات الهمسة",
        "",
        f"🆔 رقم الهمسة:",
        f"#{display_id}",
        "",
        f"📅 تاريخ الإرسال:",
        f"{created}",
        "",
        f"👥 نوع الهمسة:",
        f"{type_label}",
        "",
        f"👁️ عدد القراءات:",
        f"{reads_line}",
        "",
        f"💬 عدد الردود:",
        f"{replies_count}",
        "",
    ]

    if closed:
        lines.append("🔒 الحالة: مغلقة")
        lines.append("")
    if pinned:
        lines.append("📌 الحالة: مثبتة")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _build_stats_text(w) -> str:
    """بناء نص الإحصائيات."""
    w = dict(w)
    r_count = reader_count(w["whisper_id"])
    replies_count = count_replies(w["whisper_id"])
    readers = get_readers(w["whisper_id"])

    last_read = "—"
    last_reply = "—"
    if readers:
        last_reader = readers[-1]
        last_read = _get_sender_display(last_reader["user_id"])

    replies = get_replies(w["whisper_id"])
    if replies:
        last_rep = replies[-1]
        last_reply = _get_sender_display(last_rep["sender_id"])
        if last_rep.get("created_at"):
            last_reply = f"{last_reply}\n🕒 {_format_time(last_rep['created_at'])}"

    closed = bool(w.get("is_closed", 0))
    if closed:
        status = "🔒 مغلقة"
    elif w.get("is_locked"):
        status = "🔒 مقفلة"
    else:
        status = "✅ مفتوحة"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 الإحصائيات",
        "",
        f"👁️ عدد القراءات: {r_count}",
        f"💬 عدد الردود: {replies_count}",
        f"📌 آخر قراءة: {last_read}",
        f"💬 آخر رد: {last_reply}",
        f"🔰 حالة الهمسة: {status}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def _build_readers_text(whisper_id: str) -> str:
    """بناء نص قائمة القراء."""
    readers = get_readers(whisper_id)
    if not readers:
        return "👁️ لا يوجد قراء بعد."

    lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "👁️ قائمة القراء", ""]
    for i, r in enumerate(readers, 1):
        name = r.get("first_name") or "مجهول"
        uname = f"@{r['username']}" if r.get("username") else "—"
        uid = r["user_id"]
        lines.append(f"{i}. 👤 {name}")
        lines.append(f"   {uname}")
        lines.append(f"   🆔 {uid}")
        lines.append("")

    lines.append(f"الإجمالي: {len(readers)}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _build_replies_text(whisper_id: str) -> str:
    """بناء نص جميع الردود."""
    replies = get_replies(whisper_id)
    if not replies:
        return "💬 لا توجد ردود بعد."

    lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "💬 جميع الردود", ""]
    for r in replies:
        sender = _get_sender_display(r["sender_id"])
        time_str = _format_time(r.get("created_at", ""))
        content = r.get("content") or ""
        lines.append(f"👤 {sender}")
        lines.append(f"🕒 {time_str}")
        if content:
            lines.append(f"📝 {content}")
        if r.get("media_type"):
            lines.append(f"📎 {r['media_type']}")
        lines.append("")

    lines.append(f"الإجمالي: {len(replies)} رد")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


# ── إرسال لوحة التحكم ───────────────────────────────────────────────────────

def send_dashboard(bot: telebot.TeleBot, user_id: int, whisper_id: str) -> None:
    """إرسال لوحة التحكم لمُرسل الهمسة في الخاص."""
    w = get_whisper(whisper_id)
    if not w:
        return
    text = _build_dashboard_text(w)
    kb = dashboard_keyboard(whisper_id)
    try:
        bot.send_message(user_id, text, parse_mode="Markdown", reply_markup=kb)
    except Exception as exc:
        logger.error(f"send_dashboard failed for {user_id}: {exc}")


# ── معالجات الكولباك ─────────────────────────────────────────────────────────

def _is_dashboard_callback(data: str) -> bool:
    return data.startswith(_DASH_PREFIX)


def register_dashboard_handlers(bot: telebot.TeleBot, user_states: dict) -> None:
    """تسجيل جميع معالجات لوحة التحكم."""

    # ── عرض لوحة التحكم ──────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}show:"))
    def dash_show(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        text = _build_dashboard_text(w)
        kb = dashboard_keyboard(whisper_id)
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb,
            )
        except Exception:
            try:
                bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass

    # ── الإحصائيات ────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}stats:"))
    def dash_stats(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        text = _build_stats_text(w)
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=_back_button(whisper_id),
            )
        except Exception:
            try:
                bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=_back_button(whisper_id))
            except Exception:
                pass

    # ── عرض القراء ────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}rdrs:"))
    def dash_readers(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        text = _build_readers_text(whisper_id)
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=_back_button(whisper_id),
            )
        except Exception:
            try:
                bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=_back_button(whisper_id))
            except Exception:
                pass

    # ── عرض الردود ────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}rpls:"))
    def dash_replies(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        text = _build_replies_text(whisper_id)
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=_back_button(whisper_id),
            )
        except Exception:
            try:
                bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=_back_button(whisper_id))
            except Exception:
                pass

    # ── إعادة إرسال ───────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}rsnd:"))
    def dash_resend(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        w = dict(w)
        hours = 0
        if get_setting("auto_delete_enabled") == "1":
            try:
                hours = int(get_setting("auto_delete_hours"))
            except Exception:
                pass
        new_wid = create_whisper(
            sender_id=user.id,
            content=w["content"],
            whisper_type=w["whisper_type"],
            target_users=w.get("target_users", "[]") if isinstance(w.get("target_users"), list) else [],
            max_readers=w.get("max_readers", 0),
            auto_delete_hours=hours,
            is_destructive=bool(w.get("is_destructive", 0)),
        )
        # إرسال رسالة إعادة الإرسال مع زر switch_inline_query
        resend_kb = InlineKeyboardMarkup()
        resend_kb.add(InlineKeyboardButton(
            "📤 اضغط لإعادة الإرسال",
            switch_inline_query=w["content"],
        ))
        bot.answer_callback_query(call.id, "✅ تم إنشاء نسخة جديدة!", show_alert=False)
        try:
            bot.send_message(
                call.message.chat.id,
                f"📤 *تم إعادة إنشاء الهمسة بنجاح!*\n\n"
                f"اضغط الزر أدناه، اختر المجموعة التي تريد،"
                f" ثم اختر نوع الهمسة.",
                parse_mode="Markdown",
                reply_markup=resend_kb,
            )
        except Exception:
            pass

    # ── تثبيت / إلغاء تثبيت ───────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}pin:"))
    def dash_pin(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        new_state = toggle_pin_whisper(whisper_id)
        if new_state is None:
            bot.answer_callback_query(call.id, "❌ خطأ في التثبيت.", show_alert=True)
            return
        label = "مثبتة 📌" if new_state else "غير مثبتة"
        bot.answer_callback_query(call.id, f"✅ تم {label}.", show_alert=True)
        # تحديث اللوحة
        w = get_whisper(whisper_id)
        if w:
            text = _build_dashboard_text(w)
            try:
                bot.edit_message_text(
                    text, call.message.chat.id, call.message.message_id,
                    parse_mode="Markdown", reply_markup=dashboard_keyboard(whisper_id),
                )
            except Exception:
                pass

    # ── حذف (مع تأكيد) ──────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}del:"))
    def dash_delete(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        confirm_kb = InlineKeyboardMarkup(row_width=2)
        confirm_kb.add(
            InlineKeyboardButton("✅ نعم، احذف", callback_data=f"{_DASH_PREFIX}cdel:{whisper_id}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"{_DASH_PREFIX}show:{whisper_id}"),
        )
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                "⚠️ *هل أنت متأكد من حذف الهمسة؟*\n_لا يمكن التراجع عن هذا الإجراء._",
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=confirm_kb,
            )
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}cdel:"))
    def dash_confirm_delete(call: telebot.types.CallbackQuery):
        whisper_id = call.data.split(":", 2)[2]
        delete_whisper(whisper_id)
        bot.answer_callback_query(call.id, "🗑 تم حذف الهمسة بنجاح.", show_alert=True)
        try:
            bot.edit_message_text(
                "🗑 *تم حذف هذه الهمسة.*",
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown",
            )
        except Exception:
            pass

    # ── إغلاق ──────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(f"{_DASH_PREFIX}close:"))
    def dash_close(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 2)[2]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        if dict(w).get("is_closed", 0):
            bot.answer_callback_query(call.id, "🔒 الهمسة مغلقة بالفعل.", show_alert=True)
            return
        close_whisper(whisper_id)
        bot.answer_callback_query(call.id, "🔒 تم إغلاق الهمسة نهائياً.", show_alert=True)
        # تحديث اللوحة
        w = get_whisper(whisper_id)
        if w:
            text = _build_dashboard_text(w)
            try:
                bot.edit_message_text(
                    text, call.message.chat.id, call.message.message_id,
                    parse_mode="Markdown", reply_markup=dashboard_keyboard(whisper_id),
                )
            except Exception:
                pass
