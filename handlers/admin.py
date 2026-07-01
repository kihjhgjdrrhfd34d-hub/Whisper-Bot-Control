"""
handlers/admin.py — Admin panel for Whisper Bot Enterprise.

Key design rules applied here:
  1. EVERY callback handler calls bot.answer_callback_query() as the FIRST
     action — before any DB query or Telegram API call — to prevent Telegram's
     loading spinner from showing.
  2. All DB queries are limited / paginated so they cannot block the handler
     for more than a few milliseconds.
  3. All guard branches (is_admin check, etc.) also answer the callback before
     returning so the callback is never left unanswered.
  4. Text content is capped below Telegram's 4096-character limit.
"""
import time
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_setting, set_setting, get_stats, get_all_users, search_users,
    ban_user, unban_user, get_user, add_mandatory_channel,
    remove_mandatory_channel, get_mandatory_channels,
)
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

# Maximum characters we will put into a single Telegram message
_MAX_MSG_LEN = 3800
# Users shown per page in the user list
_USERS_PER_PAGE = 10


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _answer(bot: telebot.TeleBot, call: telebot.types.CallbackQuery,
            text: str = "", alert: bool = False) -> None:
    """Answer a callback query, swallowing any exception."""
    try:
        bot.answer_callback_query(call.id, text, show_alert=alert)
    except Exception:
        pass


def _guard_admin(bot: telebot.TeleBot,
                 call: telebot.types.CallbackQuery) -> bool:
    """
    Answer the callback and return False if user is not admin.
    Always answers so Telegram never shows a loading spinner.
    """
    if not is_admin(call.from_user.id):
        _answer(bot, call, "⛔ غير مصرح.", alert=True)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard builders  (pure functions — no DB access except where noted)
# ─────────────────────────────────────────────────────────────────────────────

def admin_main_keyboard() -> InlineKeyboardMarkup:
    notify_new_user = get_setting("notify_new_user")
    notify_block = get_setting("notify_block")
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(
            f"{'✅' if notify_new_user == '1' else '❌'} إشعارات الدخول",
            callback_data="admin:notify_new_user",
        ),
        InlineKeyboardButton(
            f"{'✅' if notify_block == '1' else '❌'} إشعارات الحظر",
            callback_data="admin:notify_block",
        ),
    )
    kb.add(
        InlineKeyboardButton("📊 الإحصائيات",      callback_data="admin:stats"),
        InlineKeyboardButton("📢 الإذاعة",         callback_data="admin:broadcast"),
    )
    kb.add(
        InlineKeyboardButton("👥 المستخدمون",      callback_data="admin:users:0"),
        InlineKeyboardButton("⚙️ إعدادات البوت",  callback_data="admin:settings"),
    )
    kb.add(
        InlineKeyboardButton("📌 الاشتراك الإجباري", callback_data="admin:channels"),
    )
    kb.add(
        InlineKeyboardButton("🚨 البلاغات",          callback_data="admin:reports"),
        InlineKeyboardButton("📂 النسخ الاحتياطية", callback_data="admin:backups"),
    )
    kb.add(
        InlineKeyboardButton("🏢 إحصائيات Enterprise", callback_data="admin:enterprise_stats"),
    )
    return kb


def _toggle_btn(label: str, key: str,
                icon_on: str = "✅", icon_off: str = "❌") -> InlineKeyboardButton:
    val = get_setting(key)
    icon = icon_on if val == "1" else icon_off
    return InlineKeyboardButton(f"{icon} {label}", callback_data=f"toggle:{key}")


def settings_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(_toggle_btn(
        "حالة البوت", "bot_active",
        icon_on="🤖 البوت يعمل ✅", icon_off="🤖 البوت متوقف ❌",
    ))
    kb.add(_toggle_btn("✅ التحقق من الاشتراك الإجباري", "membership_check"))
    kb.add(_toggle_btn("🔒 حماية المحتوى ومنع التوجيه",  "content_protection"))
    kb.add(_toggle_btn("👁 إشعارات القراءة للمرسل",       "read_receipt_enabled"))
    kb.add(_toggle_btn("🗑 الحذف التلقائي للهمسات",       "auto_delete_enabled"))

    current_hours = get_setting("auto_delete_hours") or "24"
    kb.add(InlineKeyboardButton(
        f"⏰ مدة الحذف الحالية: {current_hours} ساعة", callback_data="noop",
    ))
    kb.add(
        InlineKeyboardButton("1 ساعة",  callback_data="quick_hours:1"),
        InlineKeyboardButton("6 ساعات", callback_data="quick_hours:6"),
        InlineKeyboardButton("12 ساعة", callback_data="quick_hours:12"),
    )
    kb.add(
        InlineKeyboardButton("24 ساعة", callback_data="quick_hours:24"),
        InlineKeyboardButton("48 ساعة", callback_data="quick_hours:48"),
        InlineKeyboardButton("7 أيام",  callback_data="quick_hours:168"),
    )
    kb.add(InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="admin:main"))
    kb.add(InlineKeyboardButton("── Enterprise ──", callback_data="noop"))
    kb.add(_toggle_btn("⚔️ مكافحة السبام (Anti-Spam)", "antispam_enabled"))
    kb.add(_toggle_btn("⭐ نظام نقاط XP",              "xp_enabled"))
    kb.add(_toggle_btn("💾 نسخ احتياطي تلقائي",        "auto_backup_enabled"))
    kb.add(InlineKeyboardButton("── الردود ──", callback_data="noop"))
    kb.add(_toggle_btn("💬 الردود على الهمسات",         "whisper_replies_enabled"))
    return kb


def broadcast_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⚡ إذاعة سريعة (توجيه مباشر)", callback_data="admin:broadcast_quick"))
    kb.add(InlineKeyboardButton("📝 إذاعة عادية (نص/صورة/فيديو)", callback_data="admin:broadcast_normal"))
    kb.add(InlineKeyboardButton("↩️ توجيه رسالة محددة",           callback_data="admin:broadcast_forward"))
    kb.add(InlineKeyboardButton("🔙 رجوع",                        callback_data="admin:main"))
    return kb


def channels_keyboard() -> InlineKeyboardMarkup:
    channels = get_mandatory_channels()
    kb = InlineKeyboardMarkup(row_width=1)
    if channels:
        for ch in channels:
            name = ch["channel_name"] or ch["channel_id"]
            kb.add(InlineKeyboardButton(
                f"🗑 حذف: {name}", callback_data=f"del_channel:{ch['channel_id']}",
            ))
    else:
        kb.add(InlineKeyboardButton("ℹ️ لا توجد قنوات مضافة", callback_data="noop"))
    kb.add(InlineKeyboardButton("➕ إضافة قناة جديدة", callback_data="admin:add_channel"))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:main"))
    return kb


def _users_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    """Pagination keyboard for the users list."""
    kb = InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"admin:users:{page - 1}"))
    nav.append(InlineKeyboardButton(f"📄 {page + 1}", callback_data="noop"))
    if (page + 1) * _USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"admin:users:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.add(
        InlineKeyboardButton("🔍 بحث عن مستخدم", callback_data="admin:search_user"),
        InlineKeyboardButton("🔙 رجوع",           callback_data="admin:main"),
    )
    return kb


# ─────────────────────────────────────────────────────────────────────────────
# Handler registration
# ─────────────────────────────────────────────────────────────────────────────

def register_admin_handlers(bot: telebot.TeleBot, user_states: dict) -> None:

    # ── /admin command ───────────────────────────────────────────────────────
    @bot.message_handler(commands=["admin"])
    def admin_cmd(msg: telebot.types.Message):
        if not is_admin(msg.from_user.id):
            return
        bot.send_message(
            msg.chat.id,
            "🛡 *لوحة التحكم الإدارية*\n\nمرحباً بك يا أدمن! اختر القسم المطلوب:",
            parse_mode="Markdown",
            reply_markup=admin_main_keyboard(),
        )

    # ── Back to main panel ───────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:main")
    def admin_main(call: telebot.types.CallbackQuery):
        # Answer FIRST — always
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        _safe_edit_text(
            bot, call,
            "🛡 *لوحة التحكم الإدارية*\n\naختر القسم المطلوب:",
            admin_main_keyboard(),
        )

    # ── Notification toggles ─────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:notify_new_user")
    def admin_notify_new_user(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        current = get_setting("notify_new_user")
        new_val = "0" if current == "1" else "1"
        set_setting("notify_new_user", new_val)
        label = "✅ مفعّل" if new_val == "1" else "❌ معطّل"
        _answer(bot, call, f"🔔 إشعارات الدخول أصبحت {label}", alert=True)
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id,
                reply_markup=admin_main_keyboard(),
            )
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "admin:notify_block")
    def admin_notify_block(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        current = get_setting("notify_block")
        new_val = "0" if current == "1" else "1"
        set_setting("notify_block", new_val)
        label = "✅ مفعّل" if new_val == "1" else "❌ معطّل"
        _answer(bot, call, f"🚫 إشعارات الحظر أصبحت {label}", alert=True)
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id,
                reply_markup=admin_main_keyboard(),
            )
        except Exception:
            pass

    # ── Settings panel ───────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:settings")
    def admin_settings(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        _safe_edit_text(
            bot, call,
            "⚙️ *إعدادات البوت*\n\nاضغط على أي خيار لتفعيله أو إيقافه:",
            settings_keyboard(),
        )

    # ── Generic toggle handler ───────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("toggle:"))
    def toggle_setting(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        key = call.data.split(":", 1)[1]
        current = get_setting(key)
        new_val = "0" if current == "1" else "1"
        set_setting(key, new_val)
        _answer(bot, call, "✅ تم التحديث بنجاح")
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id,
                reply_markup=settings_keyboard(),
            )
        except Exception:
            pass

    # ── Preset delete-hours ──────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("quick_hours:"))
    def quick_hours(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        hours = call.data.split(":")[1]
        set_setting("auto_delete_hours", hours)
        user_states.pop(call.from_user.id, None)
        _answer(bot, call, f"✅ تم ضبط مدة الحذف على {hours} ساعة", alert=True)
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id,
                reply_markup=settings_keyboard(),
            )
        except Exception:
            pass

    # ── Cancel admin text input ──────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "cancel_admin_input")
    def cancel_admin_input(call: telebot.types.CallbackQuery):
        _answer(bot, call, "تم الإلغاء")
        user_states.pop(call.from_user.id, None)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    # ── Stats ────────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:stats")
    def admin_stats(call: telebot.types.CallbackQuery):
        # Answer FIRST, then do the DB query
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        s = get_stats()
        text = (
            "📊 *إحصائيات البوت*\n\n"
            "👥 *المستخدمون:*\n"
            f"├ إجمالي: `{s['total_users']}`\n"
            f"├ نشطون: `{s['active_users']}`\n"
            f"├ محظورون: `{s['banned_users']}`\n"
            f"└ انضموا اليوم: `{s['new_today']}`\n\n"
            "🤫 *الهمسات:*\n"
            f"├ إجمالي الهمسات: `{s['total_whispers']}`\n"
            f"├ همسات اليوم: `{s['whispers_today']}`\n"
            f"└ إجمالي القراءات: `{s['total_reads']}`"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 تحديث", callback_data="admin:stats"))
        kb.add(InlineKeyboardButton("🔙 رجوع",  callback_data="admin:main"))
        _safe_edit_text(bot, call, text, kb)

    # ── Users list (paginated) ───────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:users:"))
    def admin_users(call: telebot.types.CallbackQuery):
        # Answer the callback IMMEDIATELY — before any DB call
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return

        # Parse page number safely
        try:
            page = int(call.data.split(":")[-1])
        except (ValueError, IndexError):
            page = 0

        rows, total = get_all_users(page=page, per_page=_USERS_PER_PAGE)
        pages = max(1, (total + _USERS_PER_PAGE - 1) // _USERS_PER_PAGE)

        lines = [
            f"👥 *قائمة المستخدمين*\n"
            f"الإجمالي: `{total}` مستخدم — صفحة `{page + 1}` من `{pages}`\n"
        ]
        for u in rows:
            uname = f"@{u['username']}" if u["username"] else u["first_name"] or "مجهول"
            # Truncate long names to keep message short
            uname = uname[:20]
            banned_mark = " 🚫" if u["is_banned"] else " ✅"
            lines.append(f"• {uname}{banned_mark} `{u['user_id']}`")

        text = "\n".join(lines)
        # Safety cap: never exceed Telegram limit
        if len(text) > _MAX_MSG_LEN:
            text = text[:_MAX_MSG_LEN] + "\n…"

        _safe_edit_text(bot, call, text, _users_keyboard(page, total))

    # ── Keep old callback_data "admin:users" working (redirects to page 0) ──
    @bot.callback_query_handler(func=lambda c: c.data == "admin:users")
    def admin_users_legacy(call: telebot.types.CallbackQuery):
        """Backward-compatible redirect — old buttons sent 'admin:users'."""
        call.data = "admin:users:0"
        admin_users(call)

    # ── Search user prompt ───────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:search_user")
    def search_user_prompt(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        user_states[call.from_user.id] = {"action": "search_user"}
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "🔍 أرسل يوزرنيم أو ID المستخدم للبحث:",
            reply_markup=kb,
        )

    # ── Ban / unban ──────────────────────────────────────────────────────────
    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("ban:") or c.data.startswith("unban:")
    )
    def handle_ban_unban(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        action, uid_str = call.data.split(":", 1)
        try:
            uid = int(uid_str)
        except ValueError:
            _answer(bot, call, "❌ معرف غير صالح.", alert=True)
            return
        if action == "ban":
            ban_user(uid)
            _answer(bot, call, f"🚫 تم حظر المستخدم {uid} بنجاح", alert=True)
        else:
            unban_user(uid)
            _answer(bot, call, f"✅ تم رفع الحظر عن {uid} بنجاح", alert=True)
        try:
            kb = InlineKeyboardMarkup()
            if action == "ban":
                kb.add(InlineKeyboardButton("✅ رفع الحظر", callback_data=f"unban:{uid}"))
            else:
                kb.add(InlineKeyboardButton("🚫 حظر", callback_data=f"ban:{uid}"))
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id, reply_markup=kb
            )
        except Exception:
            pass

    # ── Broadcast menu ───────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast")
    def admin_broadcast(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        _safe_edit_text(
            bot, call,
            "📢 *قسم الإذاعة*\n\nاختر نوع الإذاعة المطلوبة:",
            broadcast_keyboard(),
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast_quick")
    def broadcast_quick_prompt(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        user_states[call.from_user.id] = {"action": "broadcast_quick"}
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "⚡ *الإذاعة السريعة*\n\nأرسل الرسالة الآن وسيتم توجيهها لجميع المستخدمين:",
            parse_mode="Markdown", reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast_normal")
    def broadcast_normal_prompt(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        user_states[call.from_user.id] = {"action": "broadcast_normal"}
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "📝 *الإذاعة العادية*\n\nأرسل النص أو الصورة أو الفيديو:",
            parse_mode="Markdown", reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast_forward")
    def broadcast_forward_prompt(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        user_states[call.from_user.id] = {"action": "broadcast_forward"}
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "↩️ *توجيه رسالة*\n\nأعد توجيه أي رسالة تريد إرسالها للمستخدمين:",
            parse_mode="Markdown", reply_markup=kb,
        )

    # ── Mandatory channels ───────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:channels")
    def admin_channels(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        channels = get_mandatory_channels()
        text = (
            f"📌 *الاشتراك الإجباري*\n\n"
            f"عدد القنوات المضافة: `{len(channels)}`\n\n"
            f"_اضغط على أي قناة لحذفها، أو أضف قناة جديدة:_"
        )
        _safe_edit_text(bot, call, text, channels_keyboard())

    @bot.callback_query_handler(func=lambda c: c.data == "admin:add_channel")
    def add_channel_prompt(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        user_states[call.from_user.id] = {"action": "add_channel"}
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "📌 *إضافة قناة*\n\nأرسل معرف القناة:\n"
            "• `@channel_username`\n"
            "• أو `-100123456789` (للقنوات الخاصة)\n\n"
            "_تأكد أن البوت مشرف في القناة!_",
            parse_mode="Markdown", reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("del_channel:"))
    def del_channel(call: telebot.types.CallbackQuery):
        _answer(bot, call)
        if not _guard_admin(bot, call):
            return
        ch_id = call.data.split(":", 1)[1]
        remove_mandatory_channel(ch_id)
        _answer(bot, call, "✅ تم حذف القناة بنجاح")
        channels = get_mandatory_channels()
        text = (
            f"📌 *الاشتراك الإجباري*\n\n"
            f"عدد القنوات المضافة: `{len(channels)}`\n\n"
            f"_اضغط على أي قناة لحذفها، أو أضف قناة جديدة:_"
        )
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=channels_keyboard(),
            )
        except Exception:
            pass

    # ── No-op placeholder ────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    def noop(call: telebot.types.CallbackQuery):
        _answer(bot, call)

    # NOTE: admin:reports, admin:backups, admin:enterprise_stats are handled
    # by enterprise/handlers_enterprise.py (registered after this module).
    # No stub handlers here — enterprise module takes full ownership.


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_edit_text(bot: telebot.TeleBot, call: telebot.types.CallbackQuery,
                    text: str, reply_markup: InlineKeyboardMarkup) -> None:
    """Edit the message in-place; fall back to sending a new message."""
    try:
        bot.edit_message_text(
            text, call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=reply_markup,
        )
    except Exception:
        try:
            bot.send_message(
                call.message.chat.id, text,
                parse_mode="Markdown", reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.error(f"_safe_edit_text failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast helper (module-level, called from bot.py)
# ─────────────────────────────────────────────────────────────────────────────

def do_broadcast(bot: telebot.TeleBot, msg: telebot.types.Message,
                 mode: str) -> tuple:
    from database import get_all_users as _get_all
    rows, _total = _get_all(page=0, per_page=999_999)
    sent = 0
    failed = 0
    for u in rows:
        try:
            uid = u["user_id"]
            if mode in ("quick", "forward"):
                bot.forward_message(uid, msg.chat.id, msg.message_id)
            elif mode == "normal":
                ct = msg.content_type
                if ct == "text":
                    bot.send_message(uid, msg.text or "")
                elif ct == "photo":
                    bot.send_photo(uid, msg.photo[-1].file_id, caption=msg.caption)
                elif ct == "video":
                    bot.send_video(uid, msg.video.file_id, caption=msg.caption)
                elif ct == "document":
                    bot.send_document(uid, msg.document.file_id, caption=msg.caption)
                else:
                    bot.forward_message(uid, msg.chat.id, msg.message_id)
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.04)   # Telegram rate-limit: ~25 msg/s
    return sent, failed
