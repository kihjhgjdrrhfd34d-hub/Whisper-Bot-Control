import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN, ADMIN_IDS
from database import (
    upsert_user, get_setting, is_banned, get_mandatory_channels,
    update_whisper_content, set_setting, add_mandatory_channel,
    search_users, ban_user, unban_user,
    is_new_user, mark_user_started, get_stats, get_whisper,
    create_whisper,
)
from handlers.inline import register_inline_handlers
from handlers.whisper import register_whisper_handlers
from handlers.replies import register_reply_handlers, handle_reply_message
from handlers.personal import register_personal_handlers, handle_personal_send_message
from handlers.admin import (
    register_admin_handlers, do_broadcast, is_admin, admin_main_keyboard,
)
from handlers.group_settings import register_group_settings_handlers
from handlers.stats import register_stats_handlers
from handlers.dashboard import register_dashboard_handlers
from handlers._formatting import _fmt_username
from handlers.media_wizard import register_media_wizard_handlers
from handlers.media_whispers import register_media_whisper_handlers

logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

user_states = {}

register_media_wizard_handlers(bot, user_states)


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
    uname = _fmt_username(user.username) if user.username else "—"
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
    uname = _fmt_username(user.username) if user.username else "—"
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
    uname = _fmt_username(user.username) if user.username else "—"
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
    kb.add(InlineKeyboardButton("✉️ ظرف شخصي", callback_data="env_new"))
    kb.add(InlineKeyboardButton("🎭 همسة مغلفة", callback_data="pkg_start"))
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
        if not whisper_obj:
            bot.send_message(msg.chat.id, "❌ الهمسة غير موجودة.")
            return

        w_dict = dict(whisper_obj)

        from database import can_read_whisper
        can, reason = can_read_whisper(whisper_id, user.id)
        if not can and reason != "sender":
            bot.send_message(msg.chat.id, "⛔ لا يمكنك الرد على هذه الهمسة.")
            return

        user_states[user.id] = {
            "action": "pending_whisper_reply",
            "whisper_id": whisper_id,
        }
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
        return

    # ── Deep link: show whisper content with action buttons ──────────────
    if payload:
        # Strip view_ prefix if present (e.g. "view_abc123" → "abc123")
        whisper_id_payload = payload[len("view_"):] if payload.startswith("view_") else payload

        from database import (
            can_read_whisper, delete_whisper, get_readers, add_curious,
            lock_whisper, reader_count,
        )
        from services.whisper_service import (
            record_read_and_check, is_destructive_whisper,
            build_first_one_notification, build_read_receipt_message,
            build_public_whisper_notification,
        )

        whisper = get_whisper(whisper_id_payload)
        if not whisper:
            bot.send_message(msg.chat.id, "🔒 هذه الهمسة غير موجودة أو تم مشاهدتها بالفعل")
            return

        w_dict = dict(whisper)

        # ── CRITICAL: Access control BEFORE sending any content ─────────
        can, reason = can_read_whisper(whisper_id_payload, user.id)
        if not can:
            if reason == "taken":
                opener_name = ""
                readers = get_readers(whisper_id_payload)
                if readers:
                    r = readers[0]
                    opener_name = r.get("first_name") or (f"@{r['username']}" if r.get("username") else "شخص آخر")
                if reason == "taken":
                    bot.send_message(
                        msg.chat.id,
                        f"🔒 هذه الهمسة تم فتحها بالفعل من قبل ({opener_name}).",
                    )
                else:
                    bot.send_message(msg.chat.id, "🔒 هذه الهمسة مقفلة.")
            elif reason == "locked":
                bot.send_message(msg.chat.id, "🔒 هذه الهمسة مقفلة.")
            else:
                bot.send_message(msg.chat.id, "🔒 هذه الهمسة غير موجودة أو تم مشاهدتها بالفعل")
            try:
                add_curious(whisper_id_payload, user.id)
            except Exception:
                pass
            return

        # ── Sender viewing own whisper: show content without reply button ──
        if reason == "sender":
            if w_dict.get("message_type"):
                from services.media import send_media_message
                send_media_message(bot, msg.chat.id, w_dict)
            else:
                content = w_dict.get("content", "")
                if content:
                    bot.send_message(msg.chat.id, content)
                else:
                    bot.send_message(msg.chat.id, "🔒 هذه الهمسة غير موجودة أو تم مشاهدتها بالفعل")
            return

        # ── Record the read for non-senders ────────────────────────────
        is_new_read, is_first_ever = record_read_and_check(whisper_id_payload, user.id)

        # ── Send content with reply button (URL deep-link) ─────────────
        me = _get_bot_me(bot)
        bot_username = me.username if me else ""

        if w_dict.get("message_type"):
            from handlers.media_whispers import media_whisper_read_keyboard
            _mw_kb = media_whisper_read_keyboard(whisper_id_payload, bot_username)
            from services.media import send_media_message
            send_media_message(bot, msg.chat.id, w_dict, reply_markup=_mw_kb)
        else:
            from handlers.replies import whisper_read_keyboard
            _reader_kb = whisper_read_keyboard(whisper_id_payload, bot_username)
            content = w_dict.get("content", "")
            if content:
                bot.send_message(msg.chat.id, content, reply_markup=_reader_kb)
            else:
                bot.send_message(msg.chat.id, "🔒 هذه الهمسة غير موجودة أو تم مشاهدتها بالفعل")
                return

        # ── Update group keyboard after read (single source of truth) ──
        if is_new_read:
            try:
                all_readers = get_readers(whisper_id_payload)
                from handlers.whisper import _update_group_keyboard
                _update_group_keyboard(bot, whisper_id_payload, w_dict, all_readers)
            except Exception:
                pass

        # ── Apply destructive deletion rules ────────────────────────────
        is_destructive = is_destructive_whisper(w_dict)
        if is_destructive and is_new_read:
            wtype = w_dict.get("whisper_type")
            r_count = reader_count(whisper_id_payload)
            if wtype == "first_one":
                lock_whisper(whisper_id_payload)
                delete_whisper(whisper_id_payload)
            elif wtype == "first_three" and r_count >= 3:
                lock_whisper(whisper_id_payload)
                delete_whisper(whisper_id_payload)

        # ── Notify sender ──────────────────────────────────────────────
        # first_one / first_three: NO notifications to sender
        if is_new_read:
            sender_id = w_dict["sender_id"]
            wtype = w_dict.get("whisper_type")
            if wtype == "everyone":
                try:
                    reader_first_name = user.first_name or "مستخدم"
                    bot.send_message(sender_id, f"👤 قام {reader_first_name} بقراءة همستك للجميع للتو!")
                except Exception:
                    pass
            elif get_setting("read_receipt_enabled") == "1" and wtype not in ("first_one", "first_three"):
                try:
                    bot.send_message(sender_id, build_read_receipt_message(user))
                except Exception:
                    pass
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
    func=lambda m: user_states.get(m.from_user.id) is not None,
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

    # ── Media whisper reply (mwreply: system) ────────────────────────────
    from handlers.media_whispers import handle_media_reply_message
    if handle_media_reply_message(bot, msg, user_states):
        return   # message was consumed by the media reply handler

    # ── Personal whisper state ────────────────────────────────────────────
    if handle_personal_send_message(bot, msg, user_states):
        return

    # ── Envelope draft state ──────────────────────────────────────────────
    from handlers.envelope import handle_envelope_message
    if handle_envelope_message(bot, msg, user_states):
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

        # Send whisper message to the target user (callback button)
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(
            "🔒 اضغط للرؤية",
            callback_data=f"read:{wid}",
        ))

        media_label = {
            "photo":    "📷 هذه همسة تحتوي على صورة",
            "video":    "🎬 هذه همسة تحتوي على فيديو",
            "voice":    "🎤 هذه همسة تحتوي على تسجيل صوتي",
            "audio":    "🎵 هذه همسة تحتوي على ملف صوتي",
            "document": "📄 هذه همسة تحتوي على مستند",
            "animation": "🎞 هذه همسة تحتوي على متحركة",
            "location": "📍 هذه همسة تحتوي على موقع",
        }.get(media.get("message_type"), "📎 هذه همسة تحتوي على وسائط")

        try:
            from database import update_whisper_group_message
            sent_msg = bot.send_message(
                msg.chat.id,
                media_label,
                reply_markup=kb,
            )
            if sent_msg:
                update_whisper_group_message(
                    wid, chat_id=msg.chat.id, message_id=sent_msg.message_id,
                )
        except Exception:
            pass

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
                    _fmt_username(u["username"])
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
    from handlers.envelope import register_envelope_handlers
    register_envelope_handlers(bot, user_states)
    from handlers.package_flow import register_package_flow_handlers
    register_package_flow_handlers(bot, user_states)
    register_media_whisper_handlers(bot, user_states)


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
