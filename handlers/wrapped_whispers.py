import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    upsert_user, get_setting, is_banned,
)
from database.wrapped_whispers import (
    create_draft, get_draft, delete_draft,
    update_draft_cover, update_draft_character,
    update_draft_content,
    update_draft_step,
    get_available_covers, get_cover,
    get_available_characters, get_character,
    create_inline_package, delete_inline_package,
)

logger = logging.getLogger(__name__)

_STEP = "ww_step"
_ITEMS_PER_PAGE = 4


def _page_text(page, total):
    total_pages = max(1, (total + _ITEMS_PER_PAGE - 1) // _ITEMS_PER_PAGE)
    return f"الصفحة {page + 1} / {total_pages}"


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


def _edit(bot, chat_id, msg_id, text, kb=None):
    try:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        try:
            bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)
        except Exception:
            pass


def _build_start_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("✨ إنشاء همسة", callback_data="ww_create"))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
    return kb


def _build_cover_kb(user_id, page=0):
    xp = _get_user_xp(user_id)
    covers = get_available_covers(xp)
    total = len(covers)
    start = page * _ITEMS_PER_PAGE
    end = min(start + _ITEMS_PER_PAGE, total)
    page_covers = covers[start:end]

    kb = InlineKeyboardMarkup(row_width=2)
    for i in range(0, len(page_covers), 2):
        row = page_covers[i:i+2]
        kb.add(*[InlineKeyboardButton(f"{c['icon']} {c['name']}", callback_data=f"ww_cover:{c['code']}") for c in row])

    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"ww_cover_page:{page-1}"))
    if end < total:
        pagination.append(InlineKeyboardButton("➡️ التالي", callback_data=f"ww_cover_page:{page+1}"))
    if pagination:
        kb.add(*pagination)

    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="ww_back_start"))
    return kb, total


def _build_character_kb(user_id, page=0):
    xp = _get_user_xp(user_id)
    chars = get_available_characters(xp)
    total = len(chars)
    start = page * _ITEMS_PER_PAGE
    end = min(start + _ITEMS_PER_PAGE, total)
    page_chars = chars[start:end]

    kb = InlineKeyboardMarkup(row_width=2)
    for i in range(0, len(page_chars), 2):
        row = page_chars[i:i+2]
        kb.add(*[InlineKeyboardButton(f"{ch['icon']} {ch['name']}", callback_data=f"ww_char:{ch['code']}") for ch in row])

    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"ww_char_page:{page-1}"))
    if end < total:
        pagination.append(InlineKeyboardButton("➡️ التالي", callback_data=f"ww_char_page:{page+1}"))
    if pagination:
        kb.add(*pagination)

    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="ww_back_cover"))
    return kb, total


def _build_text_input_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="ww_back_char"))
    return kb


def _build_preview_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📨 إرسال الهمسة", callback_data="ww_send_whisper"))
    kb.add(InlineKeyboardButton("✏️ تعديل النص", callback_data="ww_edit_text"))
    kb.add(InlineKeyboardButton("📦 تغيير الغلاف", callback_data="ww_change_cover"))
    kb.add(InlineKeyboardButton("🎭 تغيير الشخصية", callback_data="ww_change_char"))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="ww_back_to_text"))
    return kb


def _build_share_kb(package_id):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📤 مشاركة الهمسة", switch_inline_query=f"ww:{package_id}"))
    kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="ww_cancel"))
    return kb


def register_wrapped_whisper_handlers(bot: telebot.TeleBot, user_states: dict):

    # ── Placeholder button handler (prevents Telegram "query is invalid" errors) ──
    @bot.callback_query_handler(func=lambda c: c.data == "ww_processing")
    def placeholder_button(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id, "⏳ جاري تجهيز الهمسة... انتظر لحظة.")

    # ── Stage 0: Start screen ────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "ww_start")
    def start_screen(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        if is_banned(user.id):
            bot.send_message(call.message.chat.id, "🚫 أنت محظور من استخدام البوت.")
            return
        if get_setting("bot_active") != "1":
            bot.send_message(call.message.chat.id, "⚠️ البوت متوقف مؤقتاً.")
            return

        user_states[user.id] = {
            _STEP: 0,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }

        _edit(bot, call.message.chat.id, call.message.message_id,
              "🎭 *الهمسة المغلفة*\n\nمرحباً بك في صانع الهمسات المغلفة!\nاختر إنشاء همسة جديدة للبدء.",
              _build_start_kb())

    # ── Stage 1: Cover selection ─────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "ww_create")
    def step_cover(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        create_draft(user.id)
        user_states[user.id] = {
            _STEP: 1,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        kb, total = _build_cover_kb(user.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"📦 *اختر الغلاف*\n\n{_page_text(0, total)}\n\nاختر الغلاف الذي تريد استخدامه لهمستك:",
              kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ww_cover_page:"))
    def cover_page(call: telebot.types.CallbackQuery):
        page = int(call.data.split(":", 1)[1])
        user = call.from_user
        state = user_states.get(user.id, {})
        user_states[user.id] = {**state, _STEP: 1}
        bot.answer_callback_query(call.id)
        kb, total = _build_cover_kb(user.id, page=page)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"📦 *اختر الغلاف*\n\n{_page_text(page, total)}\n\nاختر الغلاف الذي تريد استخدامه لهمستك:",
              kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ww_cover:"))
    def step_cover_choose(call: telebot.types.CallbackQuery):
        user = call.from_user
        cover_code = call.data.split(":", 1)[1]
        cover = get_cover(cover_code)
        if not cover:
            bot.answer_callback_query(call.id, "❌ الغلاف غير موجود.", show_alert=True)
            return
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        update_draft_cover(user.id, cover_code)
        user_states[user.id] = {
            _STEP: 2,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        kb, total = _build_character_kb(user.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"✅ {cover['icon']} {cover['name']}\n\n🎭 *اختر الشخصية*\n\n{_page_text(0, total)}\n\nاختر الشخصية التي تتحدث بها:",
              kb)

    @bot.callback_query_handler(func=lambda c: c.data == "ww_back_cover")
    def back_to_cover(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_draft_step(user.id, 1)
        user_states[user.id] = {
            _STEP: 1,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        kb, total = _build_cover_kb(user.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"📦 *اختر الغلاف*\n\n{_page_text(0, total)}\n\nاختر الغلاف الذي تريد استخدامه لهمستك:",
              kb)

    # ── Stage 2: Character selection ─────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("ww_char_page:"))
    def char_page(call: telebot.types.CallbackQuery):
        page = int(call.data.split(":", 1)[1])
        user = call.from_user
        state = user_states.get(user.id, {})
        user_states[user.id] = {**state, _STEP: 2}
        bot.answer_callback_query(call.id)
        kb, total = _build_character_kb(user.id, page=page)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"🎭 *اختر الشخصية*\n\n{_page_text(page, total)}\n\nاختر الشخصية التي تتحدث بها:",
              kb)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ww_char:"))
    def step_char_choose(call: telebot.types.CallbackQuery):
        user = call.from_user
        char_code = call.data.split(":", 1)[1]
        char = get_character(char_code)
        if not char:
            bot.answer_callback_query(call.id, "❌ الشخصية غير موجودة.", show_alert=True)
            return
        update_draft_character(user.id, char_code)
        user_states[user.id] = {
            _STEP: 3,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"✅ {char['icon']} {char['name']}\n\n✏️ *أرسل نص الهمسة الآن*\n\nاكتب النص الذي تريد إخفاءه داخل الهمسة:",
              _build_text_input_kb())

    @bot.callback_query_handler(func=lambda c: c.data == "ww_back_char")
    def back_to_char(call: telebot.types.CallbackQuery):
        user = call.from_user
        draft = get_draft(user.id)
        if not draft:
            bot.answer_callback_query(call.id, "❌ لا توجد مسودة.", show_alert=True)
            return
        update_draft_step(user.id, 2)
        user_states[user.id] = {
            _STEP: 2,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        kb, total = _build_character_kb(user.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"🎭 *اختر الشخصية*\n\n{_page_text(0, total)}\n\nاختر الشخصية التي تتحدث بها:",
              kb)

    # ── Stage 3: Receive text content ────────────────────────────────────────
    @bot.message_handler(
        func=lambda m: user_states.get(m.from_user.id, {}).get(_STEP) == 3,
        content_types=["text"],
    )
    def text_input_handler(msg):
        logger.info("[WW] text handler reached user=%s state=%s", msg.from_user.id, user_states.get(msg.from_user.id))
        user = msg.from_user
        state = user_states.get(user.id)
        if not state or state.get(_STEP) != 3:
            return

        content = (msg.text or msg.caption or "").strip()
        if not content:
            bot.send_message(msg.chat.id, "⚠️ أرسل نصاً صالحاً.")
            return

        upsert_user(user.id, user.username, user.first_name, user.last_name)
        update_draft_content(user.id, content)
        ww_chat_id = state.get("ww_chat_id") or msg.chat.id
        ww_msg_id = state.get("ww_msg_id")
        user_states[user.id] = {**state, _STEP: 4}

        draft = get_draft(user.id)
        cover = get_cover(draft.get("cover_code")) if draft.get("cover_code") else None
        char = get_character(draft.get("character_code")) if draft.get("character_code") else None
        cname = cover["name"] if cover else "—"
        cicon = cover["icon"] if cover else ""
        chname = char["name"] if char else "—"
        chicn = char["icon"] if char else ""

        preview = (
            f"👁 *معاينة الهمسة*\n\n"
            f"📦 *الغلاف:* {cicon} {cname}\n"
            f"🎭 *الشخصية:* {chicn} {chname}\n\n"
            f"✏️ *النص:* ||{draft['content'][:200]}||"
        )
        if len(draft["content"]) > 200:
            preview = (
                f"👁 *معاينة الهمسة*\n\n"
                f"📦 *الغلاف:* {cicon} {cname}\n"
                f"🎭 *الشخصية:* {chicn} {chname}\n\n"
                f"✏️ *النص:* ||{draft['content'][:200]}...||"
            )

        if ww_msg_id:
            _edit(bot, ww_chat_id, ww_msg_id, preview, _build_preview_kb())
        else:
            bot.send_message(msg.chat.id, preview, parse_mode="Markdown",
                             reply_markup=_build_preview_kb())

    # ── Stage 4: Preview actions ─────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "ww_back_to_text")
    def back_to_text(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_draft_step(user.id, 3)
        user_states[user.id] = {
            _STEP: 3,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              "✏️ *أرسل نص الهمسة الآن*\n\nاكتب النص الجديد الذي تريد إخفاءه:",
              _build_text_input_kb())

    @bot.callback_query_handler(func=lambda c: c.data == "ww_edit_text")
    def edit_text(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_draft_step(user.id, 3)
        user_states[user.id] = {
            _STEP: 3,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              "✏️ *أرسل نص الهمسة الآن*\n\nاكتب النص الجديد الذي تريد إخفاءه:",
              _build_text_input_kb())

    @bot.callback_query_handler(func=lambda c: c.data == "ww_change_cover")
    def change_cover(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_draft_step(user.id, 1)
        user_states[user.id] = {
            _STEP: 1,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        kb, total = _build_cover_kb(user.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"📦 *تغيير الغلاف*\n\n{_page_text(0, total)}\n\nاختر الغلاف الجديد:",
              kb)

    @bot.callback_query_handler(func=lambda c: c.data == "ww_change_char")
    def change_char(call: telebot.types.CallbackQuery):
        user = call.from_user
        update_draft_step(user.id, 2)
        user_states[user.id] = {
            _STEP: 2,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        kb, total = _build_character_kb(user.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              f"🎭 *تغيير الشخصية*\n\n{_page_text(0, total)}\n\nاختر الشخصية الجديدة:",
              kb)

    # ── Stage 5: Send whisper → create inline package → share ──────────────
    @bot.callback_query_handler(func=lambda c: c.data == "ww_send_whisper")
    def step_send_whisper(call: telebot.types.CallbackQuery):
        user = call.from_user
        draft = get_draft(user.id)
        if not draft or not draft.get("content"):
            bot.answer_callback_query(call.id, "❌ المسودة فارغة.", show_alert=True)
            return

        cover_code = draft.get("cover_code", "")
        character_code = draft.get("character_code", "")
        content = draft.get("content", "")

        if not content:
            bot.answer_callback_query(call.id, "❌ بيانات غير مكتملة.", show_alert=True)
            return

        try:
            pkg_id = create_inline_package(user.id, cover_code, character_code, content)
        except Exception as exc:
            logger.error(f"create_inline_package failed: {exc}")
            bot.answer_callback_query(call.id, "❌ فشل تجهيز الهمسة.", show_alert=True)
            return

        user_states[user.id] = {
            _STEP: 5,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
            "ww_package_id": pkg_id,
        }
        bot.answer_callback_query(call.id)

        cover = get_cover(cover_code) if cover_code else None
        char = get_character(character_code) if character_code else None
        cname = cover["name"] if cover else "—"
        cicon = cover["icon"] if cover else ""
        chname = char["name"] if char else "—"
        chicn = char["icon"] if char else ""

        share_text = (
            f"✅ *تم تجهيز الهمسة المغلفة!*\n\n"
            f"📦 *الغلاف:* {cicon} {cname}\n"
            f"🎭 *الشخصية:* {chicn} {chname}\n\n"
            f"اضغط زر المشاركة لإرسالها إلى أي محادثة:\n"
            f"سيظهر لك بعدها أنواع الهمسات للاختيار."
        )

        _edit(bot, call.message.chat.id, call.message.message_id,
              share_text, _build_share_kb(pkg_id))

    @bot.callback_query_handler(func=lambda c: c.data == "ww_back_to_preview")
    def back_to_preview(call: telebot.types.CallbackQuery):
        user = call.from_user
        draft = get_draft(user.id)
        if not draft:
            bot.answer_callback_query(call.id, "❌ لا توجد مسودة.", show_alert=True)
            return
        update_draft_step(user.id, 4)
        user_states[user.id] = {
            _STEP: 4,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        cover = get_cover(draft.get("cover_code")) if draft.get("cover_code") else None
        char = get_character(draft.get("character_code")) if draft.get("character_code") else None
        cname = cover["name"] if cover else "—"
        cicon = cover["icon"] if cover else ""
        chname = char["name"] if char else "—"
        chicn = char["icon"] if char else ""

        preview = (
            f"👁 *معاينة الهمسة*\n\n"
            f"📦 *الغلاف:* {cicon} {cname}\n"
            f"🎭 *الشخصية:* {chicn} {chname}\n\n"
            f"✏️ *النص:* ||{draft['content'][:200]}||"
        )
        if len(draft["content"]) > 200:
            preview = (
                f"👁 *معاينة الهمسة*\n\n"
                f"📦 *الغلاف:* {cicon} {cname}\n"
                f"🎭 *الشخصية:* {chicn} {chname}\n\n"
                f"✏️ *النص:* ||{draft['content'][:200]}...||"
            )
        _edit(bot, call.message.chat.id, call.message.message_id,
              preview, _build_preview_kb())

    # ── Back to start ────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "ww_back_start")
    def back_to_start(call: telebot.types.CallbackQuery):
        user = call.from_user
        delete_draft(user.id)
        user_states[user.id] = {
            _STEP: 0,
            "ww_chat_id": call.message.chat.id,
            "ww_msg_id": call.message.message_id,
        }
        bot.answer_callback_query(call.id)
        _edit(bot, call.message.chat.id, call.message.message_id,
              "🎭 *الهمسة المغلفة*\n\nمرحباً بك في صانع الهمسات المغلفة!\nاختر إنشاء همسة جديدة للبدء.",
              _build_start_kb())

    # ── Cancel ───────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "ww_cancel")
    def cancel_whisper(call: telebot.types.CallbackQuery):
        user = call.from_user
        state = user_states.get(user.id, {})
        pkg_id = state.get("ww_package_id")
        if pkg_id:
            try:
                delete_inline_package(pkg_id)
            except Exception:
                pass

        draft = get_draft(user.id)
        if draft:
            update_draft_step(user.id, 4)
            user_states[user.id] = {
                _STEP: 4,
                "ww_chat_id": call.message.chat.id,
                "ww_msg_id": call.message.message_id,
            }
            bot.answer_callback_query(call.id, "✅ تم الإلغاء. يمكنك إعادة المحاولة.", show_alert=True)
            cover = get_cover(draft.get("cover_code")) if draft.get("cover_code") else None
            char = get_character(draft.get("character_code")) if draft.get("character_code") else None
            cname = cover["name"] if cover else "—"
            cicon = cover["icon"] if cover else ""
            chname = char["name"] if char else "—"
            chicn = char["icon"] if char else ""
            preview = (
                f"👁 *معاينة الهمسة*\n\n"
                f"📦 *الغلاف:* {cicon} {cname}\n"
                f"🎭 *الشخصية:* {chicn} {chname}\n\n"
                f"✏️ *النص:* ||{draft['content'][:200]}||"
            )
            if len(draft["content"]) > 200:
                preview = (
                    f"👁 *معاينة الهمسة*\n\n"
                    f"📦 *الغلاف:* {cicon} {cname}\n"
                    f"🎭 *الشخصية:* {chicn} {chname}\n\n"
                    f"✏️ *النص:* ||{draft['content'][:200]}...||"
                )
            _edit(bot, call.message.chat.id, call.message.message_id, preview, _build_preview_kb())
        else:
            user_states.pop(user.id, None)
            bot.answer_callback_query(call.id, "✅ تم الإلغاء.", show_alert=True)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
