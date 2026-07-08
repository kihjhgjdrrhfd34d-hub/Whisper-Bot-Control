import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN, ADMIN_IDS
from database import (
    upsert_user, get_setting, is_banned, get_mandatory_channels,
    update_whisper_content, set_setting, add_mandatory_channel,
    search_users, ban_user, unban_user
)
from handlers.inline import register_inline_handlers
from handlers.whisper import register_whisper_handlers
from handlers.admin import (
    register_admin_handlers, do_broadcast, is_admin, admin_main_keyboard
)
from handlers.stats import register_stats_handlers

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

user_states = {}


def membership_keyboard():
    channels = get_mandatory_channels()
    kb = InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        name = ch["channel_name"] or ch["channel_id"]
        cid = ch["channel_id"]
        if not cid.startswith("-"):
            url = f"https://t.me/{cid.lstrip('@')}"
        else:
            url = f"https://t.me/c/{str(cid).lstrip('-100')}"
        kb.add(InlineKeyboardButton(f"📢 {name}", url=url))
    kb.add(InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_membership"))
    return kb


@bot.message_handler(commands=["start"])
def start_cmd(msg: telebot.types.Message):
    user = msg.from_user
    upsert_user(user.id, user.username, user.first_name, user.last_name)
for admin_id in ADMIN_IDS:
    try:
        bot.send_message(
            admin_id,
            f"""🔔 مستخدم دخل البوت

👤 الاسم: {user.first_name}
🆔 الآيدي: {user.id}
📎 اليوزر: @{user.username if user.username else 'لا يوجد'}
"""
        )
    except:
        pass
    if is_banned(user.id):
        bot.send_message(msg.chat.id, "🚫 أنت محظور من استخدام هذا البوت.")
        return

    if get_setting("bot_active") != "1":
        bot.send_message(msg.chat.id, "⚠️ البوت متوقف مؤقتاً. حاول لاحقاً.")
        return

    if get_setting("membership_check") == "1":
        channels = get_mandatory_channels()
        not_subscribed = []
        for ch in channels:
            try:
                member = bot.get_chat_member(ch["channel_id"], user.id)
                if member.status in ("left", "kicked"):
                    not_subscribed.append(ch)
            except Exception:
                pass
        if not_subscribed:
            bot.send_message(
                msg.chat.id,
                "📌 *يجب الاشتراك في القنوات التالية للمتابعة:*",
                parse_mode="Markdown",
                reply_markup=membership_keyboard()
            )
            return

    text, kb = _main_menu_text_and_kb(bot, user)
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=kb)


@bot.message_handler(commands=["help"])
def help_cmd(msg: telebot.types.Message):
    _send_help(bot, msg.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "help_menu")
def help_menu_cb(call: telebot.types.CallbackQuery):
    bot.answer_callback_query(call.id)
    _send_help(bot, call.message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "back_to_main")
def back_to_main_cb(call: telebot.types.CallbackQuery):
    user = call.from_user
    bot.answer_callback_query(call.id)
    text, kb = _main_menu_text_and_kb(bot, user)
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb,
        )
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=kb)


def _send_help(bot, chat_id):
    text = (
        "📖 *دليل الاستخدام*\n\n"
        "*إرسال همسة:*\n"
        "اكتب `@اسم_البوت` في أي محادثة، ثم اكتب همستك\n\n"
        "*أنواع الهمسات:*\n"
        "🌍 *للجميع* — يراها أي شخص يضغط عليها\n"
        "☝️ *لأول شخص* — فقط أول من يفتحها\n"
        "3️⃣ *لأول 3* — أول ثلاثة أشخاص\n"
        "🎯 *مخصصة* — `@يوزر | النص` أو `ID | النص`\n\n"
        "*أزرار الهمسة:*\n"
        "👁 عرض — لقراءة الهمسة\n"
        "🔒 قفل / 🔓 فتح — للمرسل فقط\n"
        "✏️ تعديل — تغيير النص للمرسل\n"
        "🗑 حذف — حذف الهمسة نهائياً\n"
        "🧹 مسح المهموس — إعادة تعيين القراء\n"
        "🕵️ الفضوليين — من حاول القراءة دون إذن\n\n"
        "*الأوامر:*\n"
        "/start — الصفحة الرئيسية\n"
        "/stats — إحصائياتك الشخصية\n"
        "/help — هذه الصفحة"
    )
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🤫 أرسل همسة", switch_inline_query=" "))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


def _main_menu_text_and_kb(bot, user):
    me = bot.get_me()
    name = user.first_name or "صديقي"
    text = (
        f"🤫 أهلاً *{name}*!\n\n"
        f"أنا بوت الهمسات السرية 🔐\n\n"
        f"*كيف تستخدمني؟*\n"
        f"اكتب `@{me.username}` في أي محادثة أو مجموعة\n"
        f"ثم اكتب نص همستك واختر نوعها 👇\n\n"
        f"*أنواع الهمسات:*\n"
        f"🌍 *للجميع* — يقرأها أي شخص\n"
        f"☝️ *لأول شخص* — أول من يفتحها فقط\n"
        f"3️⃣ *لأول 3 أشخاص* — أول ثلاثة\n"
        f"🎯 *مخصصة* — اكتب `@يوزر | النص`\n\n"
        f"💡 يمكنك أيضاً استخدام الآيدي الرقمي:\n"
        f"`123456789 | النص`"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🤫 أرسل همسة الآن", switch_inline_query=" "))
    kb.add(
        InlineKeyboardButton("📊 إحصائياتي", callback_data="my_stats"),
        InlineKeyboardButton("❓ المساعدة", callback_data="help_menu"),
    )
    if user.id in ADMIN_IDS:
        kb.add(InlineKeyboardButton("🛡 لوحة التحكم", callback_data="admin:main_new"))
    return text, kb


@bot.callback_query_handler(func=lambda c: c.data == "admin:main_new")
def admin_main_new(call: telebot.types.CallbackQuery):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ غير مصرح.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "🛡 *لوحة التحكم الإدارية*\n\nمرحباً بك يا أدمن! اختر القسم المطلوب:",
        parse_mode="Markdown",
        reply_markup=admin_main_keyboard()
    )


@bot.callback_query_handler(func=lambda c: c.data == "check_membership")
def check_membership_cb(call: telebot.types.CallbackQuery):
    user = call.from_user
    channels = get_mandatory_channels()
    not_subscribed = []
    for ch in channels:
        try:
            member = bot.get_chat_member(ch["channel_id"], user.id)
            if member.status in ("left", "kicked"):
                not_subscribed.append(ch)
        except Exception:
            pass

    if not_subscribed:
        bot.answer_callback_query(call.id, "❌ لم تشترك في جميع القنوات بعد!", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "✅ شكراً! يمكنك الآن استخدام البوت.")
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        start_cmd(call.message)


@bot.message_handler(func=lambda m: True, content_types=["text", "photo", "video", "document"])
def handle_messages(msg: telebot.types.Message):
    user = msg.from_user
    state = user_states.get(user.id)

    if not state:
        return

    action = state.get("action")

    if action == "edit_whisper":
        whisper_id = state["whisper_id"]
        new_text = msg.text or msg.caption or ""
        if new_text:
            update_whisper_content(whisper_id, new_text)
            bot.send_message(msg.chat.id, "✅ تم تعديل الهمسة بنجاح!")
        else:
            bot.send_message(msg.chat.id, "⚠️ أرسل نصاً صالحاً.")
        del user_states[user.id]

    elif action == "set_delete_hours":
        txt = msg.text.strip() if msg.text else ""
        if txt.isdigit() and int(txt) > 0:
            set_setting("auto_delete_hours", txt)
            bot.send_message(msg.chat.id, f"✅ تم ضبط مدة الحذف على {txt} ساعة.")
        else:
            bot.send_message(msg.chat.id, "⚠️ أرسل رقماً صحيحاً موجباً.")
        del user_states[user.id]

    elif action == "search_user":
        query = msg.text.strip() if msg.text else ""
        results = search_users(query)
        if not results:
            bot.send_message(msg.chat.id, "❌ لم يتم العثور على مستخدمين بهذا البحث.")
        else:
            for u in results[:5]:
                uname = f"@{u['username']}" if u['username'] else u['first_name'] or "مجهول"
                banned = "🚫 محظور" if u['is_banned'] else "✅ نشط"
                kb = InlineKeyboardMarkup(row_width=1)
                if u['is_banned']:
                    kb.add(InlineKeyboardButton("✅ رفع الحظر", callback_data=f"unban:{u['user_id']}"))
                else:
                    kb.add(InlineKeyboardButton("🚫 حظر المستخدم", callback_data=f"ban:{u['user_id']}"))
                bot.send_message(
                    msg.chat.id,
                    f"👤 *{uname}*\n"
                    f"🆔 `{u['user_id']}`\n"
                    f"📅 الانضمام: {u['created_at'][:10]}\n"
                    f"🔰 الحالة: {banned}",
                    parse_mode="Markdown",
                    reply_markup=kb
                )
        del user_states[user.id]

    elif action in ("broadcast_quick", "broadcast_normal", "broadcast_forward"):
        mode = action.replace("broadcast_", "")
        progress_msg = bot.send_message(msg.chat.id, "⏳ جارٍ إرسال الإذاعة لجميع المستخدمين...")
        sent, failed = do_broadcast(bot, msg, mode)
        try:
            bot.edit_message_text(
                f"✅ *انتهت الإذاعة بنجاح!*\n\n"
                f"✔️ تم الإرسال: `{sent}` مستخدم\n"
                f"❌ فشل: `{failed}` مستخدم",
                msg.chat.id,
                progress_msg.message_id,
                parse_mode="Markdown"
            )
        except Exception:
            pass
        del user_states[user.id]

    elif action == "add_channel":
        channel_id = msg.text.strip() if msg.text else ""
        if channel_id:
            try:
                chat = bot.get_chat(channel_id)
                name = chat.title or channel_id
            except Exception:
                name = channel_id
            add_mandatory_channel(channel_id, name)
            bot.send_message(
                msg.chat.id,
                f"✅ تمت إضافة القناة بنجاح!\n📢 *{name}*\n🆔 `{channel_id}`",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(msg.chat.id, "⚠️ أرسل معرف قناة صالح.")
        del user_states[user.id]


def register_all_handlers():
    register_inline_handlers(bot)
    register_whisper_handlers(bot, user_states)
    register_admin_handlers(bot, user_states)
    register_stats_handlers(bot)
