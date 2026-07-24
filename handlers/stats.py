import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import back_button
from database import get_user_stats, upsert_user, get_setting, is_banned


def register_stats_handlers(bot: telebot.TeleBot):

    @bot.message_handler(commands=["stats", "احصائياتي", "mystats"])
    def my_stats_cmd(msg: telebot.types.Message):
        user = msg.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)

        if is_banned(user.id):
            bot.send_message(msg.chat.id, "🚫 أنت محظور.")
            return

        if get_setting("bot_active") != "1":
            bot.send_message(msg.chat.id, "⚠️ البوت متوقف مؤقتاً.")
            return

        _send_user_stats(bot, msg.chat.id, user.id, user.first_name)

    @bot.callback_query_handler(func=lambda c: c.data == "my_stats")
    def my_stats_cb(call: telebot.types.CallbackQuery):
        user = call.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        bot.answer_callback_query(call.id)
        _send_user_stats(bot, call.message.chat.id, user.id, user.first_name)

    @bot.callback_query_handler(func=lambda c: c.data == "refresh_stats")
    def refresh_stats(call: telebot.types.CallbackQuery):
        user = call.from_user
        s = get_user_stats(user.id)
        text = _build_stats_text(user.first_name, s)
        kb = _stats_keyboard()
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id, "🔄 تم التحديث!")


def _send_user_stats(bot, chat_id, user_id, first_name):
    s = get_user_stats(user_id)
    text = _build_stats_text(first_name, s)
    kb = _stats_keyboard()
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


def _build_stats_text(first_name, s):
    name = first_name or "أنت"
    sent = s["sent"]
    reads = s["received_reads"]
    read_rate = round((reads / sent * 100)) if sent > 0 else 0

    type_lines = []
    if s["type_everyone"]:
        type_lines.append(f"  🌍 للجميع: `{s['type_everyone']}`")
    if s["type_first_one"]:
        type_lines.append(f"  ☝️ لأول شخص: `{s['type_first_one']}`")
    if s["type_first_three"]:
        type_lines.append(f"  3️⃣ لأول 3: `{s['type_first_three']}`")
    if s["type_custom"]:
        type_lines.append(f"  🎯 مخصصة: `{s['type_custom']}`")

    types_section = "\n".join(type_lines) if type_lines else "  _لا توجد همسات بعد_"

    text = (
        f"📊 *إحصائياتك الشخصية*\n"
        f"مرحباً {name}!\n\n"
        f"📤 *الهمسات المُرسَلة:*\n"
        f"├ إجمالي الهمسات: `{sent}`\n"
        f"├ قرأها الآخرون: `{reads}` مرة\n"
        f"├ نسبة القراءة: `{read_rate}%`\n"
        f"├ فضوليون حاولوا: `{s['curious_on_mine']}`\n"
        f"└ مقفلة حالياً: `{s['locked']}`\n\n"
        f"🗂 *تصنيف همساتك:*\n"
        f"{types_section}\n\n"
        f"📥 *الهمسات المُستَقبَلة:*\n"
        f"└ قرأت همسات الآخرين: `{s['read_others']}` مرة"
    )
    return text


def _stats_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔄 تحديث", callback_data="refresh_stats"),
        InlineKeyboardButton("🤫 أرسل همسة", switch_inline_query=" "),
    )
    kb.add(back_button("back_to_main"))
    return kb
