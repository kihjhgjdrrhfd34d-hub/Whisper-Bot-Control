import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_whisper, can_read_whisper, add_reader_if_new,
    add_curious,
    toggle_whisper_lock, lock_whisper, delete_whisper, clear_whisper_readers,
    get_readers, get_curious_ones,
    get_setting, get_group_settings, is_banned, reader_count,
    create_whisper,
)
from handlers.dashboard import send_dashboard
from services.whisper_service import (
    ensure_user,
    parse_whisper_id,
    is_destructive_whisper,
    is_own_whisper,
    record_read_and_check,
    get_opener_name,
    get_user_display,
    build_first_one_notification,
    build_read_receipt_message,
    build_destructive_receipt_message,
    build_public_whisper_notification,
)

logger = logging.getLogger(__name__)


def _destroy_whisper_message(call, bot):
    """Delete the whisper message from the group; fallback to editing text."""
    try:
        if call.message:
            bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        try:
            text = "💣 تم تدمير هذه الهمسة بعد قراءتها"
            if call.message:
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=None)
            elif call.inline_message_id:
                bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=None)
        except Exception:
            pass


def register_whisper_handlers(bot: telebot.TeleBot, user_states: dict):
    _register_message_handlers(bot, user_states)
    _register_callback_handlers(bot, user_states)
    _register_inline_handlers(bot, user_states)
    _register_dashboard_handlers(bot, user_states)
    _register_reply_handlers(bot, user_states)


def _register_message_handlers(bot, user_states):

    # ─── /mwhisper command: send media whisper by user ID ────────────────
    @bot.message_handler(commands=["mwhisper"])
    def mwhisper_cmd(msg: telebot.types.Message):
        user = msg.from_user
        if is_banned(user.id):
            bot.reply_to(msg, "🚫 أنت محظور.")
            return
        if get_setting("bot_active") != "1":
            bot.reply_to(msg, "⚠️ البوت متوقف مؤقتاً.")
            return

        parts = msg.text.split(None, 1)
        if len(parts) < 2:
            bot.reply_to(
                msg,
                "❌ الاستخدام:\n"
                "`/mwhisper USER_ID`\n"
                "ثم أرسل الوسائط (صورة/فيديو/صوت/مستند/موقع)\n\n"
                "أو أرسل `/mwhisper @username`\n"
                "مثال: `/mwhisper 123456789`",
                parse_mode="Markdown",
            )
            return
        target_str = parts[1].strip()
        try:
            target_id = int(target_str)
        except ValueError:
            t = target_str.lstrip("@")
            from database import search_users
            matches = search_users(t)
            found = False
            for u in matches:
                if u["username"] and u["username"].lower() == t.lower():
                    target_id = u["user_id"]
                    found = True
                    break
            if not found:
                bot.reply_to(msg, "❌ لم يتم العثور على المستخدم. تأكد من صحة المعرف أو اليوزر.")
                return

        user_states[user.id] = {
            "action": "mwhisper_awaiting_media",
            "target_id": target_id,
        }
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action"))
        bot.send_message(
            msg.chat.id,
            f"✅ تم تحديد المستخدم `{target_id}`.\n\n"
            "📎 الآن أرسل الوسائط:\n"
            "• صورة (مع تعليق اختياري)\n"
            "• فيديو (مع تعليق اختياري)\n"
            "• تسجيل صوتي\n"
            "• ملف صوتي\n"
            "• مستند\n"
            "• موقع",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    # ─── /dwhisper command: send destructive whisper by user ID ─────────────
    @bot.message_handler(commands=["dwhisper"])
    def dwhisper_cmd(msg: telebot.types.Message):
        user = msg.from_user
        if is_banned(user.id):
            bot.reply_to(msg, "🚫 أنت محظور.")
            return
        if get_setting("bot_active") != "1":
            bot.reply_to(msg, "⚠️ البوت متوقف مؤقتاً.")
            return

        parts = msg.text.split(None, 2)
        if len(parts) < 3:
            bot.reply_to(
                msg,
                "❌ الاستخدام:\n"
                "`/dwhisper USER_ID النص`\n"
                "مثال: `/dwhisper 123456789 هذه همسة تدميرية`",
                parse_mode="Markdown",
            )
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            bot.reply_to(msg, "❌ معرف المستخدم غير صالح. يجب أن يكون رقماً.")
            return
        content = parts[2].strip()
        if not content:
            bot.reply_to(msg, "❌ نص الهمسة لا يمكن أن يكون فارغاً.")
            return

        hours = 0
        if get_setting("auto_delete_enabled") == "1":
            try:
                hours = int(get_setting("auto_delete_hours"))
            except Exception:
                pass

        group_auto_delete_minutes = 0
        if msg.chat and msg.chat.type in ("group", "supergroup"):
            try:
                gs = get_group_settings(msg.chat.id)
                group_auto_delete_minutes = int(gs.get("auto_delete_minutes", 0))
            except Exception:
                pass

        wid = create_whisper(
            sender_id=user.id,
            content=content,
            whisper_type="first_one",
            target_users=[target_id],
            max_readers=1,
            auto_delete_hours=hours,
            is_destructive=True,
            group_auto_delete_minutes=group_auto_delete_minutes,
        )

        bot_username = bot.get_me().username
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(
            "اضغط للرؤيه 🔒", callback_data=f"read:{wid}",
        ))
        kb.add(InlineKeyboardButton(
            "💬 رد على الهمسة",
            url=f"https://t.me/{bot_username}?start=reply_{wid}",
        ))
        bot.send_message(
            msg.chat.id,
            f"💣 همسة تدميرية للمستخدم `{target_id}`",
            parse_mode="Markdown",
            reply_markup=kb,
        )

        # إرسال لوحة التحكم لمُرسل الهمسة
        try:
            send_dashboard(bot, user.id, wid)
        except Exception:
            pass


def _register_callback_handlers(bot, user_states):

    # ─── Read whisper (pop-up alert) ─────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("read:"))
    def handle_read(call: telebot.types.CallbackQuery):
        user = call.from_user

        # ── Helpers (TeleBot-dependent — stay in handler) ────────────────

        def _is_blocked() -> bool:
            if is_banned(user.id):
                bot.answer_callback_query(
                    call.id, "🚫 أنت محظور من استخدام البوت.", show_alert=True
                )
                return True
            if get_setting("bot_active") != "1":
                bot.answer_callback_query(
                    call.id, "⚠️ البوت متوقف حالياً.", show_alert=True
                )
                return True
            return False

        def _load_whisper(whisper_id: str) -> dict | None:
            w = get_whisper(whisper_id)
            if not w:
                bot.answer_callback_query(
                    call.id, "❌ هذه الهمسة غير موجودة أو تم حذفها.", show_alert=True
                )
            return w

        def _check_access(whisper_id: str, w: dict) -> bool:
            can, reason = can_read_whisper(whisper_id, user.id)
            if can:
                return True
            if reason == "locked":
                if w["whisper_type"] == "first_three" and reader_count(whisper_id) >= 3:
                    opener_name = get_opener_name(whisper_id)
                    bot.answer_callback_query(
                        call.id,
                        f"لقد تم فتح الهمسه من قبل ({opener_name}) انتظر الهمسه الثانيه من نصيبك",
                        show_alert=True,
                    )
                else:
                    bot.answer_callback_query(
                        call.id, "🔒 الهمسة مقفلة حالياً من قِبل صاحبها.", show_alert=True
                    )
            elif reason == "taken":
                opener_name = get_opener_name(whisper_id)
                bot.answer_callback_query(
                    call.id,
                    f"لقد تم فتح الهمسه من قبل ({opener_name}) انتظر الهمسه الثانيه من نصيبك",
                    show_alert=True,
                )
            elif reason == "not_target":
                add_curious(whisper_id, user.id)
                bot.answer_callback_query(
                    call.id,
                    "الهمسه ليست لك بطل فضول 😂",
                    show_alert=True,
                )
            else:
                bot.answer_callback_query(
                    call.id, "❌ لا يمكنك قراءة هذه الهمسة.", show_alert=True
                )
            return False

        def _handle_destructive_everyone(whisper_id: str, w: dict, is_destructive: bool) -> bool:
            if not (is_destructive and w["whisper_type"] == "everyone"):
                return False
            is_new = add_reader_if_new(whisper_id, user.id)
            if not is_new:
                bot.answer_callback_query(
                    call.id, "🔒 لقد قرأت هذه الهمسة التدميرية من قبل!", show_alert=True
                )
                return True
            bot.answer_callback_query(call.id, f"💣 {w['content']}", show_alert=True)
            if get_setting("read_receipt_enabled") == "1":
                try:
                    bot.send_message(w["sender_id"], build_destructive_receipt_message(user))
                except Exception:
                    pass
            lock_whisper(whisper_id)
            _destroy_whisper_message(call, bot)
            return True

        def _answer_with_content(w: dict):
            w_dict = dict(w)
            if w_dict.get("message_type"):
                mt_label = {
                    "photo": "🖼 صورة",
                    "video": "🎬 فيديو",
                    "voice": "🎤 تسجيل صوتي",
                    "audio": "🎵 ملف صوتي",
                    "document": "📄 مستند",
                    "location": "📍 موقع",
                }.get(w_dict["message_type"], w_dict["message_type"])
                caption = w_dict.get("content") or w_dict.get("caption") or ""
                alert_text = f"🤫 {mt_label}"
                if caption:
                    alert_text += f"\n{caption}"
                bot.answer_callback_query(call.id, alert_text, show_alert=True)
            else:
                bot.answer_callback_query(call.id, f"🤫 {w['content']}", show_alert=True)

        def _try_start_dm(whisper_id: str) -> bool:
            try:
                bot.send_chat_action(call.from_user.id, "typing")
                return True
            except Exception:
                bot_info = bot.get_me()
                redirect_url = f"t.me/{bot_info.username}?start={whisper_id}"
                try:
                    bot.answer_callback_query(call.id, url=redirect_url)
                except Exception:
                    pass
                return False

        def _send_content_to_reader(whisper_id: str, w: dict):
            try:
                from handlers.replies import whisper_read_keyboard
                bot_info = bot.get_me()
                _reader_kb = whisper_read_keyboard(whisper_id, bot_info.username)
                w_dict = dict(w)
                media_type = w_dict.get("message_type")
                if media_type:
                    from services.media import send_media_message
                    send_media_message(
                        bot, user.id, w_dict,
                        text=f"🤫 *الهمسة:*",
                        reply_markup=_reader_kb,
                        parse_mode="Markdown",
                    )
                else:
                    bot.send_message(
                        user.id,
                        f"🤫 *الهمسة:*\n\n{w['content']}",
                        parse_mode="Markdown",
                        reply_markup=_reader_kb,
                    )
            except Exception as e:
                logger.debug(f"send whisper to reader: {e}")

        def _update_group_keyboard(whisper_id: str, w: dict, readers: list):
            reader_count_val = len(readers)
            wtype = w["whisper_type"]
            opener_name = get_user_display(user)

            bot_info = bot.get_me()
            bot_username = bot_info.username
            reply_url = f"https://t.me/{bot_username}?start=reply_{whisper_id}"

            should_edit = False
            kb = InlineKeyboardMarkup(row_width=1)

            if wtype == "everyone":
                should_edit = True
                kb.add(InlineKeyboardButton(
                    "اضغط للرؤيه 🔒", callback_data=f"read:{whisper_id}"
                ))
                kb.add(InlineKeyboardButton(
                    "💬 رد على الهمسة", url=reply_url
                ))

            elif wtype == "first_three":
                should_edit = True
                if reader_count_val >= 3:
                    kb.add(InlineKeyboardButton(
                        "🔒 تم قرائة الهمسة", callback_data=f"read:{whisper_id}"
                    ))
                else:
                    kb.add(InlineKeyboardButton(
                        "اضغط للرؤيه 🔒", callback_data=f"read:{whisper_id}"
                    ))
                for r in readers:
                    name = r.get("first_name") or (f"@{r['username']}" if r.get("username") else "مستخدم مجهول")
                    kb.add(InlineKeyboardButton(
                        name, callback_data=f"read:{whisper_id}"
                    ))
                kb.add(InlineKeyboardButton(
                    "💬 رد على الهمسة", url=reply_url
                ))

            else:  # first_one, custom — lock immediately
                should_edit = True
                kb.add(InlineKeyboardButton(
                    "🔒 تم قرائة الهمسة", callback_data=f"read:{whisper_id}"
                ))
                for r in readers:
                    name = r.get("first_name") or (f"@{r['username']}" if r.get("username") else "مستخدم مجهول")
                    kb.add(InlineKeyboardButton(
                        name, callback_data=f"read:{whisper_id}"
                    ))
                kb.add(InlineKeyboardButton(
                    "💬 رد على الهمسة", url=reply_url
                ))

            if should_edit:
                try:
                    if call.inline_message_id:
                        bot.edit_message_reply_markup(
                            inline_message_id=call.inline_message_id,
                            reply_markup=kb,
                        )
                    elif call.message:
                        bot.edit_message_reply_markup(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            reply_markup=kb,
                        )
                except Exception as e:
                    logger.debug(f"edit_reply_markup: {e}")

            return reader_count_val

        def _maybe_self_destruct(whisper_id: str, w: dict, is_destructive: bool, is_new_read: bool, reader_count_val: int):
            if is_destructive and is_new_read:
                if w["whisper_type"] == "first_one":
                    lock_whisper(whisper_id)
                    _destroy_whisper_message(call, bot)
                elif w["whisper_type"] == "first_three" and reader_count_val >= 3:
                    lock_whisper(whisper_id)
                    _destroy_whisper_message(call, bot)
                elif w["whisper_type"] == "everyone":
                    lock_whisper(whisper_id)
                    _destroy_whisper_message(call, bot)

        def _notify_sender_first_one(w: dict, is_first_ever: bool) -> bool:
            if w["whisper_type"] != "first_one" or not is_first_ever:
                return False
            try:
                bot.send_message(
                    w["sender_id"],
                    build_first_one_notification(user, w),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.debug(f"first_one notify: {e}")
            return True

        def _send_read_receipt(w: dict, is_new_read: bool):
            if is_new_read and get_setting("read_receipt_enabled") == "1":
                try:
                    bot.send_message(w["sender_id"], build_read_receipt_message(user))
                except Exception:
                    pass

        def _notify_sender_public_whisper(w: dict, chat_id: int):
            gs = get_group_settings(chat_id)
            if gs.get("read_notifications", 1) != 1:
                return
            try:
                bot.send_message(
                    w["sender_id"],
                    build_public_whisper_notification(user, w),
                )
            except Exception as e:
                logger.warning(
                    f"تعذر إرسال إشعار الهمسة العامة إلى {w['sender_id']}: {e}"
                )

        # ── Main flow ────────────────────────────────────────────────────────

        ensure_user(user.id, user.username, user.first_name, user.last_name)

        if _is_blocked():
            return

        whisper_id = parse_whisper_id(call.data)
        w = _load_whisper(whisper_id)
        if not w:
            return

        is_destructive = is_destructive_whisper(w)

        if not _check_access(whisper_id, w):
            return

        if _handle_destructive_everyone(whisper_id, w, is_destructive):
            return

        _answer_with_content(w)
        if not _try_start_dm(whisper_id):
            return

        if is_own_whisper(user.id, w):
            return

        is_new_read, is_first_ever = record_read_and_check(whisper_id, user.id)
        is_public = (w["whisper_type"] == "everyone")

        if is_new_read:
            _send_content_to_reader(whisper_id, w)

        if is_new_read:
            readers = get_readers(whisper_id)
            reader_count_val = _update_group_keyboard(whisper_id, w, readers)
            _maybe_self_destruct(whisper_id, w, is_destructive, is_new_read, reader_count_val)

        if _notify_sender_first_one(w, is_first_ever):
            return

        if is_public:
            if is_new_read:
                _notify_sender_public_whisper(w, call.message.chat.id)
            return

        _send_read_receipt(w, is_new_read)

    # ─── Lock / unlock ───────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("lock:"))
    def handle_lock(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(
                call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True
            )
            return
        new_state = toggle_whisper_lock(whisper_id)
        label = "مقفلة 🔒" if new_state else "مفتوحة 🔓"
        bot.answer_callback_query(call.id, f"✅ الهمسة أصبحت {label}", show_alert=True)

    # ─── Delete ──────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("delete:"))
    def handle_delete(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(
                call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True
            )
            return
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton(
                "✅ نعم، احذف", callback_data=f"confirm_delete:{whisper_id}"
            ),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action"),
        )
        bot.answer_callback_query(call.id)
        try:
            bot.send_message(
                user.id,
                "⚠️ *هل أنت متأكد من حذف الهمسة؟*\n_لا يمكن التراجع عن هذا الإجراء._",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception:
            bot.answer_callback_query(
                call.id, "⚠️ افتح محادثة البوت أولاً.", show_alert=True
            )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_delete:"))
    def handle_confirm_delete(call: telebot.types.CallbackQuery):
        whisper_id = call.data.split(":", 1)[1]
        delete_whisper(whisper_id)
        bot.answer_callback_query(call.id, "🗑 تم حذف الهمسة بنجاح.", show_alert=True)
        try:
            bot.edit_message_text(
                "🗑 *تم حذف هذه الهمسة.*",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
            )
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda c: c.data == "cancel_action")
    def handle_cancel(call: telebot.types.CallbackQuery):
        user = call.from_user
        state = user_states.get(user.id)

        if state and state.get("action") == "pending_whisper_reply":
            user_states.pop(user.id, None)
            bot.answer_callback_query(call.id, "❌ أُلغي الرد.", show_alert=False)
            return

        bot.answer_callback_query(call.id, "✅ تم.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    # ─── Clear readers ───────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("clear:"))
    def handle_clear(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(
                call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True
            )
            return
        clear_whisper_readers(whisper_id)
        bot.answer_callback_query(
            call.id,
            "🧹 تم مسح قائمة المهموس لهم!\nيمكن لشخص جديد قراءتها الآن.",
            show_alert=True,
        )

    # ─── Edit whisper ─────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("edit:"))
    def handle_edit(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(
                call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True
            )
            return
        user_states[user.id] = {"action": "edit_whisper", "whisper_id": whisper_id}
        bot.answer_callback_query(call.id)
        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action"))
            bot.send_message(
                user.id,
                f"✏️ *تعديل الهمسة*\n\nالنص الحالي:\n_{w['content']}_\n\nأرسل النص الجديد:",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception:
            bot.answer_callback_query(
                call.id, "⚠️ افتح محادثة البوت أولاً.", show_alert=True
            )

    # ─── Curious ones (👀 الفضوليون) ─────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("curious:"))
    def handle_curious(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(
                call.id, "⛔ قائمة الفضوليين للمرسل فقط.", show_alert=True
            )
            return

        curious = get_curious_ones(whisper_id)
        readers = get_readers(whisper_id)

        if not curious:
            bot.answer_callback_query(
                call.id,
                "👀 لا يوجد أحد حاول قراءة الهمسة حتى الآن.",
                show_alert=True,
            )
            return

        from services.whisper_service import build_curious_report_lines

        lines = build_curious_report_lines(curious, readers)

        bot.answer_callback_query(call.id)
        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🔙 إغلاق", callback_data="cancel_action"))
            bot.send_message(
                user.id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception:
            # Fallback: alert with truncated text
            short = "\n".join(lines[:4])
            bot.answer_callback_query(call.id, short, show_alert=True)


def _register_inline_handlers(bot, user_states):
    pass


def _register_dashboard_handlers(bot, user_states):
    pass


def _register_reply_handlers(bot, user_states):
    pass
