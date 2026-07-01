import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_whisper, can_read_whisper, add_reader, add_curious,
    toggle_whisper_lock, delete_whisper, clear_whisper_readers,
    update_whisper_content, get_readers, get_curious_ones,
    upsert_user, get_setting, is_banned, reader_count
)

logger = logging.getLogger(__name__)


def register_whisper_handlers(bot: telebot.TeleBot, user_states: dict):

    # ─── قراءة الهمسة (تظهر كـ pop-up alert) ─────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("read:"))
    def handle_read(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)

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

        # ── صاحب الهمسة يضغط الزر: يرى المحتوى فقط، لا قفل ولا إشعار ─────────
        if user.id == w["sender_id"]:
            bot.answer_callback_query(call.id, f"🤫 {w['content']}", show_alert=True)
            return

        # ── مستخدم آخر: تسجيله كقارئ ────────────────────────────────────────
        is_first_reader = reader_count(whisper_id) == 0
        add_reader(whisper_id, user.id)

        # عرض محتوى الهمسة كـ pop-up alert — بدون عداد القراءات
        bot.answer_callback_query(call.id, f"🤫 {w['content']}", show_alert=True)

        # همسة لأول شخص: تحديث الكيبورد + إشعار المالك عند الفتح الأول
        if w["whisper_type"] == "first_one" and is_first_reader:
            opener_name = user.first_name or "مجهول"

            # تحديث أزرار رسالة المجموعة فوراً
            locked_kb = InlineKeyboardMarkup(row_width=1)
            locked_kb.add(
                InlineKeyboardButton("🔒 تم قرائة الهمسة", callback_data=f"read:{whisper_id}")
            )
            locked_kb.add(
                InlineKeyboardButton(opener_name, callback_data=f"read:{whisper_id}")
            )
            try:
                if call.inline_message_id:
                    bot.edit_message_reply_markup(
                        inline_message_id=call.inline_message_id,
                        reply_markup=locked_kb,
                    )
                elif call.message:
                    bot.edit_message_reply_markup(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=locked_kb,
                    )
            except Exception as e:
                logger.debug(f"edit_reply_markup first_one: {e}")

            # إشعار المالك في الخاص
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

        # بقية الأنواع: إشعار فقط إذا كانت الإشعارات مفعّلة
        if get_setting("notifications") == "1":
            try:
                display = f"@{user.username}" if user.username else user.first_name or "شخص"
                bot.send_message(
                    w["sender_id"],
                    f"👁 قرأ {display} همستك!",
                )
            except Exception:
                pass

    # ─── قفل / فتح ──────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("lock:"))
    def handle_lock(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return

        new_state = toggle_whisper_lock(whisper_id)
        label = "مقفلة 🔒" if new_state else "مفتوحة 🔓"
        bot.answer_callback_query(call.id, f"✅ الهمسة أصبحت {label}", show_alert=True)

    # ─── حذف ────────────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("delete:"))
    def handle_delete(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete:{whisper_id}"),
            InlineKeyboardButton("❌ إلغاء",     callback_data="cancel_action"),
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
            bot.answer_callback_query(call.id, "⚠️ افتح محادثة البوت أولاً.", show_alert=True)

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
        bot.answer_callback_query(call.id, "تم الإلغاء.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    # ─── مسح المهموس لهم ────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("clear:"))
    def handle_clear(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
            return
        clear_whisper_readers(whisper_id)
        bot.answer_callback_query(
            call.id,
            "🧹 تم مسح قائمة المهموس لهم!\nيمكن لشخص جديد قراءتها الآن.",
            show_alert=True,
        )

    # ─── تعديل ──────────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("edit:"))
    def handle_edit(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ هذا الإجراء للمرسل فقط.", show_alert=True)
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
            bot.answer_callback_query(call.id, "⚠️ افتح محادثة البوت أولاً.", show_alert=True)

    # ─── الفضوليون ───────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("curious:"))
    def handle_curious(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] != user.id:
            bot.answer_callback_query(call.id, "⛔ قائمة الفضوليين للمرسل فقط.", show_alert=True)
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

        lines = [f"🕵️ *الفضوليين على همستك* ({len(curious)} شخص):\n"]
        for i, row in enumerate(curious, 1):
            uname = f"@{row['username']}" if row["username"] else row["first_name"] or "مجهول"
            lines.append(f"{i}. {uname} — `{row['user_id']}`")
        lines.append(f"\n👁 قرأها فعلاً: {len(readers)} شخص")

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
            bot.answer_callback_query(call.id, "\n".join(lines[:6]), show_alert=True)
