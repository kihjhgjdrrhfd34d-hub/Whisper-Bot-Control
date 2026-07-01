import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_setting, set_setting, get_stats, get_all_users, search_users,
    ban_user, unban_user, get_user, add_mandatory_channel,
    remove_mandatory_channel, get_mandatory_channels
)
from config import ADMIN_IDS
import time


def is_admin(user_id):
    return user_id in ADMIN_IDS


def admin_main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚙️ الإعدادات", callback_data="admin:settings"),
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin:stats"),
    )
    kb.add(
        InlineKeyboardButton("👥 المستخدمون", callback_data="admin:users"),
        InlineKeyboardButton("📢 الإذاعة", callback_data="admin:broadcast"),
    )
    kb.add(
        InlineKeyboardButton("📌 الاشتراك الإجباري", callback_data="admin:channels"),
    )
    return kb


def settings_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)

    def toggle_btn(label, key, icon_on="✅", icon_off="❌"):
        val = get_setting(key)
        icon = icon_on if val == "1" else icon_off
        return InlineKeyboardButton(f"{icon} {label}", callback_data=f"toggle:{key}")

    kb.add(toggle_btn("تشغيل البوت", "bot_active", "🟢 البوت يعمل", "🔴 البوت متوقف"))
    kb.add(toggle_btn("التحقق من العضوية الإجبارية", "membership_check"))
    kb.add(toggle_btn("حماية المحتوى (منع التوجيه)", "content_protection"))
    kb.add(toggle_btn("إشعارات القراءة للمرسل", "notifications"))
    kb.add(toggle_btn("الحذف التلقائي للهمسات", "auto_delete_enabled"))

    hours = get_setting("auto_delete_hours")
    kb.add(InlineKeyboardButton(f"⏱ مدة الحذف التلقائي: {hours} ساعة", callback_data="admin:set_delete_hours"))
    kb.add(InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="admin:main"))
    return kb


def broadcast_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⚡ إذاعة سريعة (توجيه مباشر)", callback_data="admin:broadcast_quick"))
    kb.add(InlineKeyboardButton("📝 إذاعة عادية (نص/صورة/فيديو)", callback_data="admin:broadcast_normal"))
    kb.add(InlineKeyboardButton("↩️ توجيه رسالة محددة", callback_data="admin:broadcast_forward"))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:main"))
    return kb


def channels_keyboard():
    channels = get_mandatory_channels()
    kb = InlineKeyboardMarkup(row_width=1)
    if channels:
        for ch in channels:
            name = ch["channel_name"] or ch["channel_id"]
            kb.add(InlineKeyboardButton(f"🗑 حذف: {name}", callback_data=f"del_channel:{ch['channel_id']}"))
    else:
        kb.add(InlineKeyboardButton("ℹ️ لا توجد قنوات مضافة", callback_data="noop"))
    kb.add(InlineKeyboardButton("➕ إضافة قناة جديدة", callback_data="admin:add_channel"))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:main"))
    return kb


def register_admin_handlers(bot: telebot.TeleBot, user_states: dict):

    @bot.message_handler(commands=["admin"])
    def admin_cmd(msg: telebot.types.Message):
        if not is_admin(msg.from_user.id):
            return
        bot.send_message(
            msg.chat.id,
            "🛡 *لوحة التحكم الإدارية*\n\nمرحباً بك يا أدمن! اختر القسم المطلوب:",
            parse_mode="Markdown",
            reply_markup=admin_main_keyboard()
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:main")
    def admin_main(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ غير مصرح.", show_alert=True)
            return
        try:
            bot.edit_message_text(
                "🛡 *لوحة التحكم الإدارية*\n\nمرحباً بك يا أدمن! اختر القسم المطلوب:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=admin_main_keyboard()
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                "🛡 *لوحة التحكم الإدارية*\n\nاختر القسم المطلوب:",
                parse_mode="Markdown",
                reply_markup=admin_main_keyboard()
            )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "admin:settings")
    def admin_settings(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        try:
            bot.edit_message_text(
                "⚙️ *إعدادات البوت*\n\nاضغط على أي خيار لتفعيله أو إيقافه:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=settings_keyboard()
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                "⚙️ *إعدادات البوت*\n\nاضغط على أي خيار لتفعيله أو إيقافه:",
                parse_mode="Markdown",
                reply_markup=settings_keyboard()
            )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("toggle:"))
    def toggle_setting(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        key = call.data.split(":", 1)[1]
        current = get_setting(key)
        new_val = "0" if current == "1" else "1"
        set_setting(key, new_val)
        bot.answer_callback_query(call.id, "✅ تم التحديث بنجاح")
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id, call.message.message_id,
                reply_markup=settings_keyboard()
            )
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "admin:set_delete_hours")
    def set_delete_hours(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        user_states[call.from_user.id] = {"action": "set_delete_hours"}
        bot.answer_callback_query(call.id)
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(
            InlineKeyboardButton("6 ساعات", callback_data="quick_hours:6"),
            InlineKeyboardButton("12 ساعة", callback_data="quick_hours:12"),
            InlineKeyboardButton("24 ساعة", callback_data="quick_hours:24"),
            InlineKeyboardButton("48 ساعة", callback_data="quick_hours:48"),
            InlineKeyboardButton("72 ساعة", callback_data="quick_hours:72"),
            InlineKeyboardButton("168 ساعة (أسبوع)", callback_data="quick_hours:168"),
        )
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "⏱ اختر مدة الحذف التلقائي أو أرسل رقماً مخصصاً (بالساعات):",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("quick_hours:"))
    def quick_hours(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        hours = call.data.split(":")[1]
        set_setting("auto_delete_hours", hours)
        user_states.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, f"✅ تم ضبط مدة الحذف على {hours} ساعة", show_alert=True)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "cancel_admin_input")
    def cancel_admin_input(call: telebot.types.CallbackQuery):
        user_states.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, "تم الإلغاء")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "admin:stats")
    def admin_stats(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
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
        kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:main"))
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb
            )
        except Exception:
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "admin:users")
    def admin_users(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        rows, total = get_all_users(page=0)
        lines = [f"👥 *قائمة المستخدمين*\nالإجمالي: `{total}` مستخدم\n"]
        for u in rows[:15]:
            uname = f"@{u['username']}" if u['username'] else u['first_name'] or "مجهول"
            banned = " 🚫" if u['is_banned'] else " ✅"
            lines.append(f"• {uname}{banned} `{u['user_id']}`")

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🔍 بحث عن مستخدم", callback_data="admin:search_user"),
            InlineKeyboardButton("🔙 رجوع", callback_data="admin:main"),
        )
        try:
            bot.edit_message_text(
                "\n".join(lines), call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb
            )
        except Exception:
            bot.send_message(call.message.chat.id, "\n".join(lines), parse_mode="Markdown", reply_markup=kb)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "admin:search_user")
    def search_user_prompt(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        user_states[call.from_user.id] = {"action": "search_user"}
        bot.answer_callback_query(call.id)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "🔍 أرسل يوزرنيم أو ID المستخدم للبحث:",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ban:") or c.data.startswith("unban:"))
    def handle_ban_unban(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        action, uid_str = call.data.split(":", 1)
        uid = int(uid_str)
        if action == "ban":
            ban_user(uid)
            bot.answer_callback_query(call.id, f"🚫 تم حظر المستخدم {uid} بنجاح", show_alert=True)
        else:
            unban_user(uid)
            bot.answer_callback_query(call.id, f"✅ تم رفع الحظر عن {uid} بنجاح", show_alert=True)
        try:
            kb = InlineKeyboardMarkup()
            if action == "ban":
                kb.add(InlineKeyboardButton("✅ رفع الحظر", callback_data=f"unban:{uid}"))
            else:
                kb.add(InlineKeyboardButton("🚫 حظر", callback_data=f"ban:{uid}"))
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast")
    def admin_broadcast(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        try:
            bot.edit_message_text(
                "📢 *قسم الإذاعة*\n\nاختر نوع الإذاعة المطلوبة:",
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=broadcast_keyboard()
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                "📢 *قسم الإذاعة*\n\nاختر نوع الإذاعة المطلوبة:",
                parse_mode="Markdown", reply_markup=broadcast_keyboard()
            )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast_quick")
    def broadcast_quick_prompt(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        user_states[call.from_user.id] = {"action": "broadcast_quick"}
        bot.answer_callback_query(call.id)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "⚡ *الإذاعة السريعة*\n\nأرسل الرسالة الآن وسيتم توجيهها لجميع المستخدمين:",
            parse_mode="Markdown",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast_normal")
    def broadcast_normal_prompt(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        user_states[call.from_user.id] = {"action": "broadcast_normal"}
        bot.answer_callback_query(call.id)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "📝 *الإذاعة العادية*\n\nأرسل النص أو الصورة أو الفيديو مع أو بدون تعليق:",
            parse_mode="Markdown",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast_forward")
    def broadcast_forward_prompt(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        user_states[call.from_user.id] = {"action": "broadcast_forward"}
        bot.answer_callback_query(call.id)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "↩️ *توجيه رسالة*\n\nأعد توجيه أي رسالة تريد إرسالها للمستخدمين:",
            parse_mode="Markdown",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:channels")
    def admin_channels(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        channels = get_mandatory_channels()
        count = len(channels)
        text = (
            f"📌 *الاشتراك الإجباري*\n\n"
            f"عدد القنوات المضافة: `{count}`\n\n"
            f"_اضغط على أي قناة لحذفها، أو أضف قناة جديدة:_"
        )
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=channels_keyboard()
            )
        except Exception:
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=channels_keyboard())
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "admin:add_channel")
    def add_channel_prompt(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        user_states[call.from_user.id] = {"action": "add_channel"}
        bot.answer_callback_query(call.id)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_admin_input"))
        bot.send_message(
            call.message.chat.id,
            "📌 *إضافة قناة*\n\nأرسل معرف القناة:\n"
            "• `@channel_username`\n"
            "• أو `-100123456789` (للقنوات الخاصة)\n\n"
            "_تأكد أن البوت مشرف في القناة!_",
            parse_mode="Markdown",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("del_channel:"))
    def del_channel(call: telebot.types.CallbackQuery):
        if not is_admin(call.from_user.id):
            return
        ch_id = call.data.split(":", 1)[1]
        remove_mandatory_channel(ch_id)
        bot.answer_callback_query(call.id, "✅ تم حذف القناة بنجاح")
        channels = get_mandatory_channels()
        count = len(channels)
        text = (
            f"📌 *الاشتراك الإجباري*\n\n"
            f"عدد القنوات المضافة: `{count}`\n\n"
            f"_اضغط على أي قناة لحذفها، أو أضف قناة جديدة:_"
        )
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=channels_keyboard()
            )
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    def noop(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)


def do_broadcast(bot: telebot.TeleBot, msg: telebot.types.Message, mode: str):
    from database import get_all_users
    rows, total = get_all_users(page=0, per_page=999999)
    sent = 0
    failed = 0
    for u in rows:
        try:
            if mode == "quick" or mode == "forward":
                bot.forward_message(u["user_id"], msg.chat.id, msg.message_id)
            elif mode == "normal":
                if msg.content_type == "text":
                    bot.send_message(u["user_id"], msg.text or "")
                elif msg.content_type == "photo":
                    bot.send_photo(u["user_id"], msg.photo[-1].file_id, caption=msg.caption)
                elif msg.content_type == "video":
                    bot.send_video(u["user_id"], msg.video.file_id, caption=msg.caption)
                elif msg.content_type == "document":
                    bot.send_document(u["user_id"], msg.document.file_id, caption=msg.caption)
                else:
                    bot.forward_message(u["user_id"], msg.chat.id, msg.message_id)
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.04)
    return sent, failed
