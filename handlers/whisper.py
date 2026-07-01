import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
<<<<<<< HEAD
    get_whisper, can_read_whisper, add_reader, add_reader_if_new, add_curious,
    toggle_whisper_lock, delete_whisper, clear_whisper_readers,
    update_whisper_content, get_readers, get_curious_ones,
    upsert_user, get_setting, is_banned, reader_count,
=======
    get_whisper, can_read_whisper, add_reader, add_reader_if_new,
    record_whisper_read, add_curious,
    toggle_whisper_lock, lock_whisper, delete_whisper, clear_whisper_readers,
    update_whisper_content, get_readers, get_curious_ones,
    upsert_user, get_setting, is_banned, reader_count,
    create_whisper,
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)
)

logger = logging.getLogger(__name__)

<<<<<<< HEAD
_REPLY_PREFIX = "wsp_reply:"
_CONV_PREFIX = "wsp_conv:"


def _send_reader_reply_dm(bot: telebot.TeleBot, user: telebot.types.User, whisper_id: str) -> None:
    """Send the reader a DM with Reply and Conversation buttons after first read."""
    from handlers.replies import reply_button, conversation_button
    from database.replies import get_replies
    kb = InlineKeyboardMarkup()
    kb.add(reply_button(whisper_id))
    replies = get_replies(whisper_id)
    if replies:
        kb.add(conversation_button(whisper_id))
    try:
        name = user.first_name or "User"
        bot.send_message(
            user.id,
            f"You received a whisper, {name}!",
            reply_markup=kb,
        )
    except Exception as e:
        logger.debug(f"_send_reader_reply_dm failed for {user.id}: {e}")
=======

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
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)


def register_whisper_handlers(bot: telebot.TeleBot, user_states: dict):

<<<<<<< HEAD
    # ─── Read whisper (pop-up alert) ─────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("read:"))
    def handle_read(call: telebot.types.CallbackQuery):
        try:
            user = call.from_user
            try:
                upsert_user(user.id, user.username, user.first_name, user.last_name)
            except Exception:
                pass

            if is_banned(user.id):
                bot.answer_callback_query(
                    call.id, "🚫 أنت محظور من استخدام البوت.", show_alert=True
                )
                return

            if get_setting("bot_active") != "1":
                bot.answer_callback_query(
                    call.id, "⚠️ البوت متوقف حالياً.", show_alert=True
                )
                return

            whisper_id = call.data.split(":", 1)[1]
            w = get_whisper(whisper_id)

            if not w:
                bot.answer_callback_query(
                    call.id, "❌ هذه الهمسة غير موجودة أو تم حذفها.", show_alert=True
                )
                return

            can, reason = can_read_whisper(whisper_id, user.id)

            if not can:
                if reason == "locked":
                    bot.answer_callback_query(
                        call.id, "🔒 الهمسة مقفلة حالياً من قِبل صاحبها.", show_alert=True
                    )
                elif reason == "taken":
                    readers = get_readers(whisper_id)
                    opener_name = readers[0]["first_name"] if readers else "شخص آخر"
=======
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

        wid = create_whisper(
            sender_id=user.id,
            content=content,
            whisper_type="first_one",
            target_users=[target_id],
            max_readers=1,
            auto_delete_hours=hours,
            is_destructive=True,
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

    # ─── Read whisper (pop-up alert) ─────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("read:"))
    def handle_read(call: telebot.types.CallbackQuery):
        user = call.from_user
        # Upsert in background — never block the callback on user registration
        try:
            upsert_user(user.id, user.username, user.first_name, user.last_name)
        except Exception:
            pass

        if is_banned(user.id):
            bot.answer_callback_query(
                call.id, "🚫 أنت محظور من استخدام البوت.", show_alert=True
            )
            return

        if get_setting("bot_active") != "1":
            bot.answer_callback_query(
                call.id, "⚠️ البوت متوقف حالياً.", show_alert=True
            )
            return

        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)

        if not w:
            bot.answer_callback_query(
                call.id, "❌ هذه الهمسة غير موجودة أو تم حذفها.", show_alert=True
            )
            return

        is_destructive = bool(dict(w).get("is_destructive", 0))

        can, reason = can_read_whisper(whisper_id, user.id)

        if not can:
            if reason == "locked":
                if w["whisper_type"] == "first_three" and reader_count(whisper_id) >= 3:
                    _readers = get_readers(whisper_id)
                    opener_name = _readers[0]["first_name"] if _readers else "شخص آخر"
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)
                    bot.answer_callback_query(
                        call.id,
                        f"لقد تم فتح الهمسه من قبل ({opener_name}) انتظر الهمسه الثانيه من نصيبك",
                        show_alert=True,
                    )
<<<<<<< HEAD
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
                return

            if user.id == w["sender_id"]:
                bot.answer_callback_query(call.id, f"🤫 {w['content']}", show_alert=True)
                return

            is_new_read = add_reader_if_new(whisper_id, user.id)
            is_first_ever = (reader_count(whisper_id) == 1) if is_new_read else False

            bot.answer_callback_query(call.id, f"🤫 {w['content']}", show_alert=True)

            if w["whisper_type"] == "first_one" and is_first_ever:
                opener_name = user.first_name or "مجهول"

                locked_kb = InlineKeyboardMarkup(row_width=1)
                locked_kb.add(
                    InlineKeyboardButton(
                        "🔒 تم قرائة الهمسة", callback_data=f"read:{whisper_id}"
                    )
                )
                locked_kb.add(
                    InlineKeyboardButton(opener_name, callback_data=f"read:{whisper_id}")
                )
=======
                else:
                    bot.answer_callback_query(
                        call.id, "🔒 الهمسة مقفلة حالياً من قِبل صاحبها.", show_alert=True
                    )
            elif reason == "taken":
                readers = get_readers(whisper_id)
                opener_name = readers[0]["first_name"] if readers else "شخص آخر"
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
            return

        # ── Destructive everyone: register read first, show as pop-up only ──
        if is_destructive and w["whisper_type"] == "everyone":
            is_new = add_reader_if_new(whisper_id, user.id)
            if not is_new:
                bot.answer_callback_query(
                    call.id, "🔒 لقد قرأت هذه الهمسة التدميرية من قبل!", show_alert=True
                )
                return
            bot.answer_callback_query(call.id, f"💣 {w['content']}", show_alert=True)
            if get_setting("read_receipt_enabled") == "1":
                try:
                    display = f"@{user.username}" if user.username else user.first_name or "شخص"
                    bot.send_message(w["sender_id"], f"👁 قرأ {display} همستك التدميرية!")
                except Exception:
                    pass
            # قفل الهمسة فوراً لمنع أي شخص آخر من قراءتها
            lock_whisper(whisper_id)
            # حذف رسالة الجروب أو استبدالها
            _destroy_whisper_message(call, bot)
            return

        # ── ANSWER the callback FIRST — before any I/O that could fail ──────
        # This ensures Telegram never shows a loading spinner.
        bot.answer_callback_query(call.id, f"🤫 {w['content']}", show_alert=True)

        try:
            bot.send_chat_action(call.from_user.id, "typing")
        except Exception:
            # User hasn't started the bot — redirect to DM
            bot_info = bot.get_me()
            redirect_url = f"t.me/{bot_info.username}?start={whisper_id}"
            try:
                bot.answer_callback_query(call.id, url=redirect_url)
            except Exception:
                pass
            return

        # Sender reads their own whisper: show content only, no side-effects
        if user.id == w["sender_id"]:
            return

        # Another user: register as reader with type-aware locking.
        # record_whisper_read handles:
        #   - "everyone":  NEVER locks
        #   - "first_three": locks only when count >= 3
        # It returns True only on first insert (atomic via changes()).
        # is_new_read drives ALL notifications — no duplicate receipts possible.
        is_new_read = record_whisper_read(whisper_id, user.id)
        # For first_one we still need to know if THIS read was the very first
        # across all users (not just this user).  We check reader_count AFTER
        # insert: if count == 1 it means we were the inserter.
        is_first_ever = (reader_count(whisper_id) == 1) if is_new_read else False

        # ── Send whisper content with Reply + Conversation buttons ────────
        if is_new_read:
            try:
                from handlers.replies import whisper_actions_keyboard as _wak
                _reader_kb = _wak(whisper_id)
                bot.send_message(
                    user.id,
                    f"🤫 *الهمسة:*\n\n{w['content']}",
                    parse_mode="Markdown",
                    reply_markup=_reader_kb,
                )
            except Exception as e:
                logger.debug(f"send whisper to reader: {e}")

        # ── Update group keyboard only when necessary ──
        if is_new_read:
            readers = get_readers(whisper_id)
            reader_count_val = len(readers)
            wtype = w["whisper_type"]
            opener_name = user.first_name or "مجهول"

            bot_info = bot.get_me()
            bot_username = bot_info.username
            reply_url = f"https://t.me/{bot_username}?start=reply_{whisper_id}"

            should_edit = False
            kb = InlineKeyboardMarkup(row_width=1)

            if wtype == "everyone":
                # Add reply button after first read, keep read button available
                should_edit = is_new_read
                if is_new_read:
                    kb.add(InlineKeyboardButton(
                        "اضغط للرؤيه 🔒", callback_data=f"read:{whisper_id}"
                    ))
                    kb.add(InlineKeyboardButton(
                        "💬 رد على الهمسة", url=reply_url
                    ))

            elif wtype == "first_three":
                if reader_count_val >= 3:
                    should_edit = True
                    kb.add(InlineKeyboardButton(
                        "🔒 تم قرائة الهمسة", callback_data=f"read:{whisper_id}"
                    ))
                    for r in readers:
                        name = r["first_name"] or "مجهول"
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
                    name = r["first_name"] or "مجهول"
                    kb.add(InlineKeyboardButton(
                        name, callback_data=f"read:{whisper_id}"
                    ))
                kb.add(InlineKeyboardButton(
                    "💬 رد على الهمسة", url=reply_url
                ))

            if should_edit:
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)
                try:
                    if call.inline_message_id:
                        bot.edit_message_reply_markup(
                            inline_message_id=call.inline_message_id,
<<<<<<< HEAD
                            reply_markup=locked_kb,
=======
                            reply_markup=kb,
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)
                        )
                    elif call.message:
                        bot.edit_message_reply_markup(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
<<<<<<< HEAD
                            reply_markup=locked_kb,
                        )
                except Exception as e:
                    logger.debug(f"edit_reply_markup first_one: {e}")

                try:
                    uname = f"@{user.username}" if user.username else "بدون يوزر"
                    bot.send_message(
                        w["sender_id"],
                        f"- تم مشاهدة همستك من قبل :\n\n"
                        f"• معرف الشخص : {uname}\n"
                        f"• اسم الشخص : {opener_name}\n"
                        f"• ايدي الشخص : {user.id}\n\n"
                        f"• الهمسة :\n{w['content']}",
                    )
                except Exception as e:
                    logger.debug(f"first_one notify: {e}")

                _send_reader_reply_dm(bot, user, whisper_id)
                return

            if is_first_ever and w["whisper_type"] != "first_one":
                _send_reader_reply_dm(bot, user, whisper_id)

            if is_new_read and get_setting("read_receipt_enabled") == "1":
                try:
                    display = f"@{user.username}" if user.username else user.first_name or "شخص"
                    bot.send_message(w["sender_id"], f"👁 قرأ {display} همستك!")
                except Exception:
                    pass

        except Exception as exc:
            logger.error(f"handle_read unhandled: {exc}", exc_info=True)
            try:
                bot.answer_callback_query(
                    call.id, "An error occurred. Please try again.", show_alert=True
=======
                            reply_markup=kb,
                        )
                except Exception as e:
                    logger.debug(f"edit_reply_markup: {e}")

        # ── Self-destruct: lock + delete group message for destructive whispers ──
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

        # first_one whisper: notify owner with detailed info
        if w["whisper_type"] == "first_one" and is_first_ever:
            try:
                uname = f"@{user.username}" if user.username else "بدون يوزر"
                bot.send_message(
                    w["sender_id"],
                    f"- تم مشاهدة همستك من قبل :\n\n"
                    f"• معرف الشخص : {uname}\n"
                    f"• اسم الشخص : {opener_name}\n"
                    f"• ايدي الشخص : {user.id}\n\n"
                    f"• الهمسة :\n{w['content']}",
                )
            except Exception as e:
                logger.debug(f"first_one notify: {e}")
            return

        # Other types: send read receipt to sender ONLY
        if is_new_read and get_setting("read_receipt_enabled") == "1":
            try:
                display = f"@{user.username}" if user.username else user.first_name or "شخص"
                bot.send_message(
                    w["sender_id"],
                    f"👁 قرأ {display} همستك!",
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)
                )
            except Exception:
                pass

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
<<<<<<< HEAD
        bot.answer_callback_query(call.id, "تم الإلغاء.")
        user_states.pop(call.from_user.id, None)
=======
        user = call.from_user
        state = user_states.get(user.id)

        if state and state.get("action") == "pending_whisper_reply":
            user_states.pop(user.id, None)
            bot.answer_callback_query(call.id, "❌ أُلغي الرد.", show_alert=False)
            return

        bot.answer_callback_query(call.id, "✅ تم.")
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)
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

        lines = [
            f"👀 *الأشخاص الذين حاولوا فتح الهمسة*\n"
            f"({len(curious)} شخص)\n"
        ]
        for i, row in enumerate(curious, 1):
            name = row["first_name"] or "مجهول"
            uname = f"@{row['username']}" if row["username"] else "—"
            uid = row["user_id"]
            # tried_at is stored as ISO string; take date+time portion
            tried_at = str(row["tried_at"])[:16] if row["tried_at"] else "—"
            lines.append(
                f"{i}. *{name}*\n"
                f"   🔗 اليوزر: {uname}\n"
                f"   🆔 الآيدي: `{uid}`\n"
                f"   🕐 الوقت: `{tried_at}`\n"
            )
        lines.append(f"👁 قرأها فعلاً: {len(readers)} شخص")

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
