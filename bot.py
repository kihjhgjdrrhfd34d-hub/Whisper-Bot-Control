import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN, ADMIN_IDS
from database import (
    upsert_user, get_setting, is_banned, get_mandatory_channels,
    update_whisper_content, set_setting, add_mandatory_channel,
    search_users, ban_user, unban_user,
    is_new_user, mark_user_started, get_stats, get_whisper,
)
from handlers.inline import register_inline_handlers
from handlers.whisper import register_whisper_handlers
from handlers.replies import register_reply_handlers, handle_reply_message, whisper_actions_keyboard
from handlers.personal import register_personal_handlers, handle_personal_send_message
from handlers.admin import (
    register_admin_handlers, do_broadcast, is_admin, admin_main_keyboard,
)
from handlers.group_settings import register_group_settings_handlers
from handlers.stats import register_stats_handlers
from handlers.dashboard import register_dashboard_handlers

logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

user_states = {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def _notify_admins_new_user(user):
    """Send new-user notification to all admins (only if toggle is on)."""
    if get_setting("notify_new_user") != "1":
        return
    stats = get_stats()
    total = stats["total_users"]
    uname = f"@{user.username}" if user.username else "—"
    text = (
        "👾 *مستخدم جديد دخل البوت*\n\n"
        f"👤 الاسم: {user.first_name or '—'}\n"
        f"🔗 اليوزر: {uname}\n"
        f"🆔 الآيدي: `{user.id}`\n\n"
        f"📊 إجمالي المستخدمين: `{total}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception:
            pass


def _notify_admins_block(user):
    """Notify admins that a user blocked the bot (if toggle is on)."""
    if get_setting("notify_block") != "1":
        return
    uname = f"@{user.username}" if user.username else "—"
    text = (
        "🚫 *قام مستخدم بحظر البوت*\n\n"
        f"👤 الاسم: {user.first_name or '—'}\n"
        f"🔗 اليوزر: {uname}\n"
        f"🆔 الآيدي: `{user.id}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception:
            pass


def _notify_admins_unblock(user):
    """Notify admins that a user unblocked the bot (if toggle is on)."""
    if get_setting("notify_block") != "1":
        return
    uname = f"@{user.username}" if user.username else "—"
    text = (
        "✅ *قام مستخدم بإزالة حظر البوت*\n\n"
        f"👤 الاسم: {user.first_name or '—'}\n"
        f"🔗 اليوزر: {uname}\n"
        f"🆔 الآيدي: `{user.id}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception:
            pass


def _send_help(b, chat_id):
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
    b.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


_bot_me_cache = None  # cached result of bot.get_me()

def _get_bot_me(b):
    """Return cached bot info — calls get_me() only once per process."""
    global _bot_me_cache
    if _bot_me_cache is None:
        try:
            _bot_me_cache = b.get_me()
        except Exception:
            pass
    return _bot_me_cache


def _main_menu_text_and_kb(b, user):
    me = _get_bot_me(b)
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
        InlineKeyboardButton("❓ المساعدة",   callback_data="help_menu"),
    )
    kb.add(InlineKeyboardButton("🤫 الهمسات الشخصية", callback_data="pers_menu"))
    if user.id in ADMIN_IDS:
        kb.add(InlineKeyboardButton("🛡 لوحة التحكم", callback_data="admin:main_new"))
    return text, kb


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def start_cmd(msg: telebot.types.Message):
    user = msg.from_user

    # Register / update user in DB
    upsert_user(user.id, user.username, user.first_name, user.last_name)

    # ── Notify admins ONLY on the very first /start ───────────────────────
    if is_new_user(user.id):
        mark_user_started(user.id)
        _notify_admins_new_user(user)
        _enterprise_on_new_user(user.id)
    else:
        _enterprise_on_every_start(user.id)

    # ── Extract payload from deep link (e.g. /start <whisper_id>) ────────
    parts = msg.text.split()
    payload = parts[1] if len(parts) > 1 else None

    # ── Guard: banned ──────────────────────────────────────────────────────
    if is_banned(user.id):
        bot.send_message(msg.chat.id, "🚫 أنت محظور من استخدام هذا البوت.")
        return

    # ── Guard: bot disabled ────────────────────────────────────────────────
    if get_setting("bot_active") != "1":
        bot.send_message(msg.chat.id, "⚠️ البوت متوقف مؤقتاً. حاول لاحقاً.")
        return

    # ── Guard: mandatory channel membership ───────────────────────────────
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
                reply_markup=membership_keyboard(),
            )
            return

    # ── Deep link: reply to whisper (e.g. /start reply_abc123) ─────────
    if payload and payload.startswith("reply_"):
        whisper_id = payload[len("reply_"):]
        whisper_obj = get_whisper(whisper_id)
        if whisper_obj:
            user_states[user.id] = {
                "action": "pending_whisper_reply",
                "whisper_id": whisper_id,
            }
            w_dict = dict(whisper_obj)
            if w_dict.get("message_type"):
                from services.media import send_media_message
                send_media_message(
                    bot, msg.chat.id, w_dict,
                    text=f"📝 أنت ترد الآن على الهمسة..\n\n{w_dict['content']}",
                )
            else:
                bot.send_message(
                    msg.chat.id,
                    f"📝 أنت ترد الآن على الهمسة..\n\n{whisper_obj['content']}",
                )
        else:
            bot.send_message(msg.chat.id, "❌ الهمسة غير موجودة.")
        return

    # ── Deep link: show whisper content with action buttons ──────────────
    if payload:
        whisper = get_whisper(payload)
        if whisper:
            kb = whisper_actions_keyboard(payload)
            w_dict = dict(whisper)
            if w_dict.get("message_type"):
                from services.media import send_media_message
                send_media_message(
                    bot, msg.chat.id, w_dict,
                    text=f"🤫 *الهمسة:*",
                    reply_markup=kb,
                    parse_mode="Markdown",
                )
            else:
                bot.send_message(
                    msg.chat.id,
                    f"🤫 *الهمسة:*\n\n{whisper['content']}",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
        else:
            bot.send_message(msg.chat.id, "❌ الهمسة غير موجودة.")
        return

    text, kb = _main_menu_text_and_kb(bot, user)
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
# Block / unblock detection via my_chat_member updates
# ─────────────────────────────────────────────────────────────────────────────

@bot.my_chat_member_handler()
def handle_chat_member_update(update: telebot.types.ChatMemberUpdated):
    """Detect when a user blocks or unblocks the bot."""
    user = update.from_user
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    # blocked: was member/active → now kicked
    if new_status == "kicked" and old_status in ("member", "creator", "administrator"):
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        _notify_admins_block(user)

    # unblocked: was kicked → now member
    elif old_status == "kicked" and new_status == "member":
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        _notify_admins_unblock(user)


# ─────────────────────────────────────────────────────────────────────────────
# /help
# ─────────────────────────────────────────────────────────────────────────────

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
        bot.send_message(
            call.message.chat.id, text, parse_mode="Markdown", reply_markup=kb
        )


# ─────────────────────────────────────────────────────────────────────────────
# Admin entry-point from main menu button
# ─────────────────────────────────────────────────────────────────────────────

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
        reply_markup=admin_main_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Membership check callback
# ─────────────────────────────────────────────────────────────────────────────

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
        bot.answer_callback_query(
            call.id, "❌ لم تشترك في جميع القنوات بعد!", show_alert=True
        )
    else:
        bot.answer_callback_query(call.id, "✅ شكراً! يمكنك الآن استخدام البوت.")
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        start_cmd(call.message)


# ─────────────────────────────────────────────────────────────────────────────
# Generic message handler (state machine for admin actions)
# ─────────────────────────────────────────────────────────────────────────────

@bot.message_handler(
    func=lambda m: True,
    content_types=["text", "photo", "video", "document", "voice", "audio", "sticker",
                   "animation", "contact", "location"],
)
def handle_messages(msg: telebot.types.Message):
    user = msg.from_user
    state = user_states.get(user.id)

    if not state:
        return

    # ── Priority: whisper reply (must be checked before other states) ─────
    if handle_reply_message(bot, msg, user_states):
        return   # message was consumed by the reply handler

    # ── Personal whisper state ────────────────────────────────────────────
    if handle_personal_send_message(bot, msg, user_states):
        return

    action = state.get("action")

    # ── Media whisper state (must be before other text-only states) ──────
    if action == "mwhisper_awaiting_media":
        from services.media import extract_media_from_message
        media = extract_media_from_message(msg)
        target_id = state.get("target_id")

        if not media["message_type"]:
            bot.send_message(msg.chat.id, "⚠️ أرسل وسائط (صورة/فيديو/صوت/مستند/موقع).")
            return True

        hours = 0
        if get_setting("auto_delete_enabled") == "1":
            try:
                hours = int(get_setting("auto_delete_hours"))
            except Exception:
                pass

        content = media["content"] or ""

        wid = create_whisper(
            sender_id=user.id,
            content=content,
            whisper_type="custom",
            target_users=[target_id],
            max_readers=0,
            auto_delete_hours=hours,
            message_type=media["message_type"],
            file_id=media["file_id"],
            caption=media["caption"],
            location_lat=media["location_lat"],
            location_lon=media["location_lon"],
        )

        # Send whisper message to the target user
        bot_username = bot.get_me().username
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(
            "اضغط للرؤيه 🔒", callback_data=f"read:{wid}",
        ))
        kb.add(InlineKeyboardButton(
            "💬 رد على الهمسة",
            url=f"https://t.me/{bot_username}?start=reply_{wid}",
        ))

        from services.media import send_media_message
        try:
            label = f"🤫 همسة سرية لـ `{target_id}`"
            send_media_message(
                bot, msg.chat.id, media,
                text=label,
                reply_markup=kb,
                parse_mode="Markdown",
            )
        except Exception:
            bot.send_message(
                msg.chat.id,
                f"🤫 همسة سرية لـ `{target_id}`",
                parse_mode="Markdown",
                reply_markup=kb,
            )

        try:
            from handlers.dashboard import send_dashboard
            send_dashboard(bot, user.id, wid)
        except Exception:
            pass

        del user_states[user.id]
        return True

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
            bot.send_message(
                msg.chat.id, "❌ لم يتم العثور على مستخدمين بهذا البحث."
            )
        else:
            for u in results[:5]:
                uname = (
                    f"@{u['username']}"
                    if u["username"]
                    else u["first_name"] or "مجهول"
                )
                banned_label = "🚫 محظور" if u["is_banned"] else "✅ نشط"
                kb = InlineKeyboardMarkup(row_width=1)
                if u["is_banned"]:
                    kb.add(
                        InlineKeyboardButton(
                            "✅ رفع الحظر",
                            callback_data=f"unban:{u['user_id']}",
                        )
                    )
                else:
                    kb.add(
                        InlineKeyboardButton(
                            "🚫 حظر المستخدم",
                            callback_data=f"ban:{u['user_id']}",
                        )
                    )
                bot.send_message(
                    msg.chat.id,
                    f"👤 *{uname}*\n"
                    f"🆔 `{u['user_id']}`\n"
                    f"📅 الانضمام: {u['created_at'][:10]}\n"
                    f"🔰 الحالة: {banned_label}",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
        del user_states[user.id]

    elif action in ("broadcast_quick", "broadcast_normal", "broadcast_forward"):
        mode = action.replace("broadcast_", "")
        progress_msg = bot.send_message(
            msg.chat.id, "⏳ جارٍ إرسال الإذاعة لجميع المستخدمين..."
        )
        sent, failed = do_broadcast(bot, msg, mode)
        try:
            bot.edit_message_text(
                f"✅ *انتهت الإذاعة بنجاح!*\n\n"
                f"✔️ تم الإرسال: `{sent}` مستخدم\n"
                f"❌ فشل: `{failed}` مستخدم",
                msg.chat.id,
                progress_msg.message_id,
                parse_mode="Markdown",
            )
        except Exception:
            pass
        del user_states[user.id]

    elif action == "add_channel":
        channel_id = msg.text.strip() if msg.text else ""
        if channel_id:
            try:
                chat_obj = bot.get_chat(channel_id)
                name = chat_obj.title or channel_id
            except Exception:
                name = channel_id
            add_mandatory_channel(channel_id, name)
            bot.send_message(
                msg.chat.id,
                f"✅ تمت إضافة القناة بنجاح!\n📢 *{name}*\n🆔 `{channel_id}`",
                parse_mode="Markdown",
            )
        else:
            bot.send_message(msg.chat.id, "⚠️ أرسل معرف قناة صالح.")
        del user_states[user.id]


# ─────────────────────────────────────────────────────────────────────────────
# Register all handlers
# ─────────────────────────────────────────────────────────────────────────────

def register_all_handlers():
    register_inline_handlers(bot)
    register_whisper_handlers(bot, user_states)
    register_admin_handlers(bot, user_states)
    register_group_settings_handlers(bot, user_states)
    register_stats_handlers(bot)
    register_reply_handlers(bot, user_states)
    register_personal_handlers(bot, user_states)
    register_dashboard_handlers(bot, user_states)


# ─────────────────────────────────────────────────────────────────────────────
# Enterprise: hook XP and activity into start_cmd (additive, backward-compat)
# ─────────────────────────────────────────────────────────────────────────────

def _enterprise_on_new_user(user_id: int) -> None:
    """Award XP and log activity for first-ever /start. Silent on import error."""
    try:
        from enterprise.db_enterprise import award_xp, log_activity, check_and_grant_achievements
        from database import get_setting
        if get_setting("xp_enabled") == "1":
            award_xp(user_id, 5, reason="first_start")
        log_activity(user_id, "login")
        check_and_grant_achievements(user_id)
    except Exception:
        pass   # enterprise layer is optional — never crash the core bot


def _enterprise_on_every_start(user_id: int) -> None:
    """Log each /start visit (not just first). Silent on import error."""
    try:
        from enterprise.db_enterprise import log_activity
        log_activity(user_id, "login")
    except Exception:
        pass


# Monkey-patch register_all_handlers to also register enterprise handlers
_original_register_all_handlers = register_all_handlers


def register_all_handlers():
    _original_register_all_handlers()
    # Enterprise handlers (additive — registered after core handlers)
    try:
        from enterprise.handlers_enterprise import register_enterprise_handlers
        register_enterprise_handlers(bot, user_states)
    except Exception as exc:
        logger.warning(f"Enterprise handlers not loaded: {exc}")
