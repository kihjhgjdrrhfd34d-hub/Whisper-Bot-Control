import json
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import back_button, cancel_button, confirm_button
from database import (
    upsert_user, get_setting, is_banned,
    create_whisper, update_whisper_group_message,
)
from database.package_flow import (
    create_package, get_package, delete_package,
    update_package_cover, update_package_character,
    update_package_content, update_package_target,
    update_package_type, update_package_step,
    get_available_covers, get_cover,
    get_available_characters, get_character,
)
from handlers.dashboard import send_dashboard

logger = logging.getLogger(__name__)

_STEP = "pkg_step"


def _auto_hours():
    if get_setting("auto_delete_enabled") == "1":
        try:
            return int(get_setting("auto_delete_hours"))
        except Exception:
            pass
    return 0


def _get_user_xp(user_id):
    try:
        from enterprise.db_enterprise import get_xp
        return get_xp(user_id).get("xp", 0)
    except Exception:
        return 0


def _back_kb(cb_data="pkg_cancel"):
    kb = InlineKeyboardMarkup()
    kb.add(cancel_button(cb_data))
    return kb


def _cancel_kb():
    return _back_kb("pkg_cancel")


def _build_cover_kb(user_id):
    xp = _get_user_xp(user_id)
    covers = get_available_covers(xp)
    kb = InlineKeyboardMarkup(row_width=2)
    for c in covers:
        kb.add(InlineKeyboardButton(
            f"{c['icon']} {c['name']}",
            callback_data=f"pkg_cover:{c['code']}",
        ))
    kb.add(cancel_button("pkg_cancel"))
    return kb


def _build_character_kb(user_id):
    xp = _get_user_xp(user_id)
    chars = get_available_characters(xp)
    kb = InlineKeyboardMarkup(row_width=2)
    for ch in chars:
        kb.add(InlineKeyboardButton(
            f"{ch['icon']} {ch['name']}",
            callback_data=f"pkg_char:{ch['code']}",
        ))
    kb.add(back_button("pkg_back_cover"))
    return kb


def _build_draft_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ تعديل النص", callback_data="pkg_edit_text"),
        confirm_button("تأكيد", "pkg_confirm"),
    )
    kb.add(cancel_button("pkg_cancel"))
    return kb


def _build_type_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("☝️ لأول شخص", callback_data="pkg_type:first_one"),
        InlineKeyboardButton("🌍 للجميع", callback_data="pkg_type:everyone"),
    )
    kb.add(
        InlineKeyboardButton("👥 لأول 3", callback_data="pkg_type:first_three"),
        InlineKeyboardButton("🎯 مخصصة", callback_data="pkg_type:custom"),
    )
    kb.add(back_button("pkg_back_chat"))
    return kb


def _build_type_kb_with_maxreaders():
    kb = InlineKeyboardMarkup(row_width=2)
    for wtype, mr, label in [
        ("first_one", 1, "☝️ لأول شخص"),
        ("everyone", 0, "🌍 للجميع"),
        ("first_three", 3, "👥 لأول 3"),
    ]:
        kb.add(InlineKeyboardButton(label, callback_data=f"pkg_do:{wtype}:{mr}"))
    kb.add(back_button("pkg_back_chat"))
    return kb


def register_package_flow_handlers(bot: telebot.TeleBot, user_states: dict):

    # ── Step 1: Show covers ──────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_start")
    def step1_covers(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        if is_banned(user.id):
            bot.send_message(call.message.chat.id, "🚫 أنت محظور من استخدام البوت.")
            return
        if get_setting("bot_active") != "1":
            bot.send_message(call.message.chat.id, "⚠️ البوت متوقف مؤقتاً.")
            return

        create_package(user.id)
        user_states[user.id] = {_STEP: 1}

        covers = get_available_covers(_get_user_xp(user.id))
        if not covers:
            bot.send_message(call.message.chat.id, "❌ لا توجد أغلفة متاحة حالياً.")
            return

        lines = ["🎭 *الهمسة المغلفة* — الخطوة 1 من 6", "", "اختر *الغلاف* لهمستك:"]
        bot.send_message(
            call.message.chat.id,
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=_build_cover_kb(user.id),
        )

    # ── Step 2: Save cover, show characters ─────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("pkg_cover:"))
    def step2_characters(call: telebot.types.CallbackQuery):
        user = call.from_user
        cover_code = call.data.split(":", 1)[1]

        cover = get_cover(cover_code)
        if not cover:
            bot.answer_callback_query(call.id, "❌ الغلاف غير موجود.", show_alert=True)
            return

        upsert_user(user.id, user.username, user.first_name, user.last_name)
        update_package_cover(user.id, cover_code)
        user_states[user.id] = {_STEP: 2}
        bot.answer_callback_query(call.id)

        lines = [
            f"✅ تم اختيار الغلاف: {cover['icon']} {cover['name']}",
            "",
            "🎭 *الهمسة المغلفة* — الخطوة 2 من 6",
            "",
            "اختر *شخصية الرسالة*:",
        ]
        try:
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=_build_character_kb(user.id),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=_build_character_kb(user.id),
            )

    # ── Back to covers ─────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_back_cover")
    def back_to_covers(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_package_step(user.id, 1)
        user_states[user.id] = {_STEP: 1}
        bot.answer_callback_query(call.id)
        lines = ["🎭 *الهمسة المغلفة* — الخطوة 1 من 6", "", "اختر *الغلاف* لهمستك:"]
        try:
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=_build_cover_kb(user.id),
            )
        except Exception:
            pass

    # ── Step 3: Save character, ask for text ────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("pkg_char:"))
    def step3_text(call: telebot.types.CallbackQuery):
        user = call.from_user
        char_code = call.data.split(":", 1)[1]

        char = get_character(char_code)
        if not char:
            bot.answer_callback_query(call.id, "❌ الشخصية غير موجودة.", show_alert=True)
            return

        update_package_character(user.id, char_code)
        user_states[user.id] = {_STEP: 3}
        bot.answer_callback_query(call.id)

        lines = [
            f"✅ تم اختيار الشخصية: {char['icon']} {char['name']}",
            "",
            "✏️ *الهمسة المغلفة* — الخطوة 3 من 6",
            "",
            "أرسل *نص الهمسة* الذي تريد إخفاءه:",
        ]
        try:
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=_back_kb("pkg_back_char"),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=_back_kb("pkg_back_char"),
            )

    # ── Back to characters ─────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_back_char")
    def back_to_chars(call: telebot.types.CallbackQuery):
        user = call.from_user
        pkg = get_package(user.id)
        if not pkg:
            bot.answer_callback_query(call.id, "❌ لا توجد حزمة.", show_alert=True)
            return
        update_package_step(user.id, 2)
        user_states[user.id] = {_STEP: 2}
        bot.answer_callback_query(call.id)
        lines = [
            f"🎭 *الهمسة المغلفة* — الخطوة 2 من 6",
            "",
            "اختر *شخصية الرسالة*:",
        ]
        try:
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=_build_character_kb(user.id),
            )
        except Exception:
            pass

    # ── Step 4: Receive text content (message handler) ─────────────────────
    def _handle_text_input(msg):
        user = msg.from_user
        state = user_states.get(user.id)
        if not state or state.get(_STEP) != 3:
            return False

        content = (msg.text or msg.caption or "").strip()
        if not content:
            bot.send_message(msg.chat.id, "⚠️ أرسل نصاً صالحاً.")
            return True

        upsert_user(user.id, user.username, user.first_name, user.last_name)
        update_package_content(user.id, content)
        user_states[user.id] = {_STEP: 4}

        pkg = get_package(user.id)
        cover = get_cover(pkg["cover_code"])
        char = get_character(pkg["character_code"])
        cname = cover["name"] if cover else "—"
        ciname = cover["icon"] if cover else ""
        chname = char["name"] if char else "—"
        chicn = char["icon"] if char else ""

        lines = [
            "📦 *المسودة* — الخطوة 4 من 6",
            "",
            f"🎭 الغلاف: {ciname} {cname}",
            f"👤 الشخصية: {chicn} {chname}",
            f"📝 النص: ||{pkg['content'][:200]}||",
        ]
        if len(pkg["content"]) > 200:
            lines[-1] = f"📝 النص: ||{pkg['content'][:200]}...||"
        lines.append("")
        lines.append("اختر الإجراء:")

        try:
            bot.send_message(
                msg.chat.id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=_build_draft_kb(),
            )
        except Exception:
            bot.send_message(
                msg.chat.id,
                "📦 *المسودة*\n\nاختر الإجراء:",
                parse_mode="Markdown",
                reply_markup=_build_draft_kb(),
            )
        return True

    # ── Register text handler ──────────────────────────────────────────────
    @bot.message_handler(
        func=lambda m: user_states.get(m.from_user.id, {}).get(_STEP) == 3,
        content_types=["text"],
    )
    def text_input_handler(msg):
        _handle_text_input(msg)

    # ── Back to text editing ───────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_edit_text")
    def edit_text(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_package_step(user.id, 3)
        user_states[user.id] = {_STEP: 3}
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                "✏️ أرسل النص الجديد:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=_back_kb("pkg_back_char"),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                "✏️ أرسل النص الجديد:",
                reply_markup=_back_kb("pkg_back_char"),
            )

    # ── Step 5: Confirm draft → select chat ────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_confirm")
    def step5_chat(call: telebot.types.CallbackQuery):
        user = call.from_user
        pkg = get_package(user.id)
        if not pkg or not pkg.get("content"):
            bot.answer_callback_query(call.id, "❌ المسودة فارغة.", show_alert=True)
            return
        update_package_step(user.id, 5)
        user_states[user.id] = {_STEP: 5}
        bot.answer_callback_query(call.id)

        lines = [
            "💬 *الهمسة المغلفة* — الخطوة 5 من 6",
            "",
            "اختر *المحادثة* التي تريد إرسال الهمسة إليها.",
            "",
            "✦ أرسل *إعادة توجيه (Forward)* لرسالة من المجموعة",
            "✦ أو أرسل *معرف المحادثة الرقمي*",
        ]
        try:
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=_cancel_kb(),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=_cancel_kb(),
            )

    # ── Receive forwarded message / chat ID ────────────────────────────────
    @bot.message_handler(
        func=lambda m: user_states.get(m.from_user.id, {}).get(_STEP) == 5,
        content_types=["text", "photo", "video", "document", "voice", "audio",
                       "animation", "location"],
    )
    def step5_chat_input(msg):
        user = msg.from_user
        state = user_states.get(user.id)
        if not state or state.get(_STEP) != 5:
            return

        chat_id = None
        chat_title = None

        if msg.forward_from_chat:
            chat_id = msg.forward_from_chat.id
            chat_title = msg.forward_from_chat.title or f"مجموعة {chat_id}"
        elif msg.forward_from:
            bot.send_message(
                msg.chat.id,
                "⚠️ هذه رسالة من مستخدم وليست من مجموعة. أعد توجيه رسالة *من المجموعة* التي تريد الإرسال إليها.",
                parse_mode="Markdown",
            )
            return
        elif msg.text and msg.text.strip():
            text = msg.text.strip().lstrip("-")
            if text.isdigit():
                chat_id = int(msg.text.strip())
                chat_title = f"محادثة {chat_id}"
            else:
                bot.send_message(
                    msg.chat.id,
                    "⚠️ المدخل غير صالح. أرسل إعادة توجيه من المجموعة أو المعرف الرقمي.",
                )
                return
        else:
            bot.send_message(
                msg.chat.id,
                "⚠️ أعد توجيه رسالة من المجموعة التي تريد الإرسال إليها.",
            )
            return

        if chat_id and chat_id > 0:
            bot.send_message(
                msg.chat.id,
                "⚠️ هذا معرف مستخدم وليس مجموعة. أعد توجيه رسالة *من المجموعة* التي تريد الإرسال إليها.",
                parse_mode="Markdown",
            )
            return

        update_package_target(user.id, chat_id, chat_title or "")
        user_states[user.id] = {_STEP: 6}

        lines = [
            f"✅ تم اختيار المحادثة: {chat_title or chat_id}",
            "",
            "🔒 *الهمسة المغلفة* — الخطوة 6 من 6",
            "",
            "اختر *نوع الهمسة*:",
        ]
        bot.send_message(
            msg.chat.id,
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=_build_type_kb(),
        )

    # ── Step 6: Select type → finalize ─────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("pkg_type:"))
    def step6_type(call: telebot.types.CallbackQuery):
        user = call.from_user
        wtype = call.data.split(":", 1)[1]

        if wtype == "custom":
            user_states[user.id] = {_STEP: 7, "pkg_custom_type": True}
            bot.answer_callback_query(call.id)
            try:
                bot.edit_message_text(
                    "🎯 أدخل معرف المستخدم (ID) أو اليوزر (@username) الذي تريد إرسال الهمسة له:",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=_back_kb("pkg_back_type"),
                )
            except Exception:
                bot.send_message(
                    call.message.chat.id,
                    "🎯 أدخل معرف المستخدم (ID) أو اليوزر (@username):",
                    reply_markup=_back_kb("pkg_back_type"),
                )
            return

        _finalize_package(bot, user, call, wtype, user_states)

    # ── Custom target input ────────────────────────────────────────────────
    @bot.message_handler(
        func=lambda m: user_states.get(m.from_user.id, {}).get(_STEP) == 7,
        content_types=["text"],
    )
    def custom_target_input(msg):
        user = msg.from_user
        state = user_states.get(user.id)
        if not state or state.get(_STEP) != 7:
            return

        text = (msg.text or "").strip()
        target = None

        if text.startswith("@"):
            username = text[1:].lower()
            from database import search_users
            matches = search_users(username)
            for u in matches:
                if u["username"] and u["username"].lower() == username:
                    target = u["user_id"]
                    break
            if not target:
                bot.send_message(
                    msg.chat.id,
                    f"❌ لم أجد مستخدمًا باليوزر `{text}`.",
                    parse_mode="Markdown",
                )
                return
        else:
            try:
                target = int(text)
            except ValueError:
                bot.send_message(msg.chat.id, "❌ المدخل غير صالح. أدخل ID رقمي أو @username.")
                return

        update_package_type(user.id, "custom", target_users=[target], max_readers=0)
        _do_finalize(bot, user, msg.chat.id, msg.message_id, user_states)
        return

    # ── Back to type selection from custom ─────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_back_type")
    def back_to_type(call: telebot.types.CallbackQuery):
        user = call.from_user
        pkg = get_package(user.id)
        if not pkg:
            bot.answer_callback_query(call.id, "❌ لا توجد حزمة.", show_alert=True)
            return
        update_package_step(user.id, 6)
        user_states[user.id] = {_STEP: 6}
        bot.answer_callback_query(call.id)
        lines = [
            "🔒 *الهمسة المغلفة* — الخطوة 6 من 6",
            "",
            "اختر *نوع الهمسة*:",
        ]
        try:
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=_build_type_kb(),
            )
        except Exception:
            pass

    # ── Back to chat selection ─────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_back_chat")
    def back_to_chat(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_package_step(user.id, 5)
        user_states[user.id] = {_STEP: 5}
        bot.answer_callback_query(call.id)
        lines = [
            "💬 *الهمسة المغلفة* — الخطوة 5 من 6",
            "",
            "أرسل إعادة توجيه (Forward) من المجموعة التي تريد:",
        ]
        try:
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=_cancel_kb(),
            )
        except Exception:
            pass

    # ── Cancel ─────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "pkg_cancel")
    def cancel_package(call: telebot.types.CallbackQuery):
        user = call.from_user
        delete_package(user.id)
        user_states.pop(user.id, None)
        bot.answer_callback_query(call.id, "✅ تم الإلغاء.", show_alert=True)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass


def _finalize_package(bot, user, call, wtype, user_states):
    mr = 1 if wtype == "first_one" else (3 if wtype == "first_three" else 0)
    update_package_type(user.id, wtype, target_users=[], max_readers=mr)
    _do_finalize(bot, user, call.message.chat.id, call.message.message_id, user_states)


def _do_finalize(bot, user, chat_id, message_id, user_states):
    pkg = get_package(user.id)
    if not pkg:
        bot.send_message(chat_id, "❌ حدث خطأ. ابدأ من جديد.")
        return

    content = pkg.get("content", "")
    wtype = pkg.get("whisper_type")
    target_users = json.loads(pkg.get("target_users", "[]"))
    max_readers = pkg.get("max_readers", 0)
    target_chat = pkg.get("target_chat_id")
    logger.info("[PACKAGE] finalize user=%s pkg=%s", user.id, pkg)
    target_title = pkg.get("target_chat_title", "")

    if not content or not wtype or not target_chat:
        bot.send_message(chat_id, "❌ بيانات غير مكتملة. ابدأ من جديد.")
        delete_package(user.id)
        return

    try:
        wid = create_whisper(
            sender_id=user.id,
            content=content,
            whisper_type=wtype,
            target_users=target_users,
            max_readers=max_readers,
            auto_delete_hours=_auto_hours(),
        )
    except Exception as exc:
        logger.error(f"create_whisper failed: {exc}")
        bot.send_message(chat_id, "❌ فشل إنشاء الهمسة.")
        return

    cover = get_cover(pkg.get("cover_code", "cover_classic")) or {}
    char = get_character(pkg.get("character_code", "char_whisperer")) or {}

    cover_name = cover.get("name", "أساسي 📜")
    cover_icon = cover.get("icon", "📜")
    char_name = char.get("name", "المُهمس 🤫")
    char_icon = char.get("icon", "🤫")
    greeting = char.get("greeting", "")

    type_labels = {
        "first_one": "لأول شخص ☝️",
        "everyone": "للجميع 🌍",
        "first_three": "لأول 3 👥",
        "custom": "مخصصة 🎯",
    }
    type_label = type_labels.get(wtype, wtype)

    group_text_parts = [
        f"{char_icon} *{char_name}*",
        f"📦 {cover_icon} {cover_name}",
    ]
    if greeting:
        group_text_parts.append(f"_{greeting}_")
    group_text_parts.append("")
    group_text_parts.append("🔒 اضغط للرؤية")
    group_text = "\n".join(group_text_parts)

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔒 اضغط للرؤية", callback_data=f"read:{wid}"))

    try:
        sent = bot.send_message(
            target_chat,
            group_text,
            parse_mode="Markdown",
            reply_markup=kb,
        )
        if sent:
            update_whisper_group_message(
                wid, chat_id=target_chat, message_id=sent.message_id,
            )
    except Exception as exc:
        bot.send_message(
            chat_id,
            f"⚠️ تعذر الإرسال إلى {target_title or target_chat}.\n"
            f"تأكد أن البوت عضو في المجموعة.\n(`{exc}`)",
        )

    confirm_lines = [
        "✅ *تم إنشاء الهمسة المغلفة!*",
        "",
        f"📦 الغلاف: {cover_icon} {cover_name}",
        f"👤 الشخصية: {char_icon} {char_name}",
        f"🔒 النوع: {type_label}",
        f"💬 المحادثة: {target_title or target_chat}",
        f"🆔 `{wid}`",
    ]
    try:
        bot.edit_message_text(
            "\n".join(confirm_lines),
            chat_id,
            message_id,
            parse_mode="Markdown",
        )
    except Exception:
        bot.send_message(
            chat_id,
            "\n".join(confirm_lines),
            parse_mode="Markdown",
        )

    try:
        send_dashboard(bot, user.id, wid)
    except Exception:
        pass

    delete_package(user.id)
    user_states.pop(user.id, None)
