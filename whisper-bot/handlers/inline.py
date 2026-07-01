import logging
import re
import telebot
from telebot.types import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from database import create_whisper, get_setting, upsert_user

logger = logging.getLogger(__name__)

GROUP_MSG = "هذه همسة سرية 🔒"

FOUR_OPTIONS = [
    ("first_one",   1, "همسة لأول شخص",           "🔒 يقرأها أول شخص فقط",       "هذه همسه سريه لاول شخص يقوم بقرأتها"),
    ("everyone",    0, "همسة للجميع",              "🌍 يمكن لأي شخص قراءتها",      "هذه الهمسه للجميع"),
    ("first_three", 3, "همسة لأول ثلاثة أشخاص",   "👥 يقرأها أول 3 أشخاص فقط",   "هذه همسه سريه لاول ثلاثة أشخاص يقومون بقرأتها"),
    ("custom",      0, "همسة بالأيدي او اليوزر",   "🎯 مخصصة لشخص معين",           "هذه همسه سريه مخصصة"),
]

def _read_button(whisper_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("اضغط للرؤيه 🔒", callback_data=f"read:{whisper_id}"))
    return kb

def _auto_hours() -> int:
    if get_setting("auto_delete_enabled") == "1":
        try:
            return int(get_setting("auto_delete_hours"))
        except Exception:
            pass
    return 0

def register_inline_handlers(bot: telebot.TeleBot):

    @bot.inline_handler(func=lambda query: True)
    def handle_inline(query: telebot.types.InlineQuery):
        user = query.from_user
        try:
            upsert_user(user.id, user.username, user.first_name, user.last_name)
        except Exception as e:
            logger.warning(f"upsert_user: {e}")

        if get_setting("bot_active") != "1":
            try:
                bot.answer_inline_query(
                    query.id, [],
                    switch_pm_text="⚠️ البوت متوقف حالياً",
                    switch_pm_parameter="start",
                    cache_time=0,
                )
            except Exception as e:
                logger.error(f"answer_inline_query (off): {e}")
            return

        raw = query.query.strip()

        if not raw:
            username = f"@{user.username}" if user.username else user.first_name or "مستخدم"
            placeholder = InlineQueryResultArticle(
                id="placeholder",
                title="اكتب الهمسه هنا باليوزر او الايدي",
                description=f"مرحبا علي {username}",
                input_message_content=InputTextMessageContent(
                    message_text="اكتب نص الهمسة بعد اسم البوت ثم اختر النوع 👆"
                ),
            )
            try:
                bot.answer_inline_query(
                    query.id, [placeholder], cache_time=0, is_personal=True
                )
            except Exception as e:
                logger.error(f"answer_inline_query (empty): {e}")
            return

        content = raw
        hours = _auto_hours()
        results = []

        _TARGET_RE = re.compile(r'^(@\w+|[1-9]\d{4,})\s+([\s\S]+)$')
        m = _TARGET_RE.match(raw)
        if m:
            raw_target = m.group(1)
            whisper_body = m.group(2).strip()
            t = raw_target.lstrip("@")
            parsed_target = int(t) if t.isdigit() else t
            display_target = raw_target if raw_target.startswith("@") else raw_target

            try:
                wid_targeted = create_whisper(
                    sender_id=user.id,
                    content=whisper_body,
                    whisper_type="custom",
                    target_users=[parsed_target],
                    max_readers=0,
                    auto_delete_hours=hours,
                )
                snippet = whisper_body[:40] + "..." if len(whisper_body) > 40 else whisper_body
                group_msg_targeted = f"هذه همسة سرية لـ {display_target} 🤫"
                targeted_kb = InlineKeyboardMarkup()
                targeted_kb.add(InlineKeyboardButton(
                    "اضغط للرؤيه 🔒",
                    callback_data=f"read:{wid_targeted}"
                ))
                results.append(
                    InlineQueryResultArticle(
                        id=f"custom:{wid_targeted}",
                        title=f"هذه همسة سرية لـ {display_target} هو فقط م...",
                        description=snippet,
                        input_message_content=InputTextMessageContent(
                            message_text=group_msg_targeted,
                        ),
                        reply_markup=targeted_kb,
                    )
                )
            except Exception as e:
                logger.error(f"inline targeted build: {e}")

        for wtype, max_r, title, desc, group_text in FOUR_OPTIONS:
            try:
                targets = []
                target_label = None
                body = content
                if wtype == "custom":
                    if "|" in content:
                        left, right = content.split("|", 1)
                        body = right.strip() or content
                        raw_targets = left.strip().split()
                        for t in raw_targets:
                            t = t.lstrip("@")
                            targets.append(int(t) if t.isdigit() else t)
                    else:
                        parts = content.split(None, 1)
                        if parts:
                            t = parts[0].lstrip("@")
                            if t.isdigit() or (len(t) > 3 and not " " in t):
                                targets.append(int(t) if t.isdigit() else t)
                                body = parts[1] if len(parts) > 1 else content
                    if targets:
                        first = targets[0]
                        target_label = f"@{first}" if isinstance(first, str) else str(first)

                wid = create_whisper(
                    sender_id=user.id,
                    content=body,
                    whisper_type=wtype,
                    target_users=targets,
                    max_readers=max_r,
                    auto_delete_hours=hours,
                )

                if wtype == "custom" and target_label:
                    btn_text = f"همسة لـ {target_label} 🔒"
                    btn_kb = InlineKeyboardMarkup()
                    btn_kb.add(InlineKeyboardButton(btn_text, callback_data=f"read:{wid}"))
                else:
                    btn_kb = _read_button(wid)

                results.append(
                    InlineQueryResultArticle(
                        id=f"{wtype}:{wid}",
                        title=title,
                        description=f"لـ {target_label}" if (wtype == "custom" and target_label) else desc,
                        input_message_content=InputTextMessageContent(
                            message_text=group_text,
                        ),
                        reply_markup=btn_kb,
                    )
                )
            except Exception as e:
                logger.error(f"inline build [{wtype}]: {e}")

        try:
            bot.answer_inline_query(query.id, results, cache_time=0, is_personal=True)
        except Exception as e:
            logger.error(f"answer_inline_query (typed): {e}")

    @bot.chosen_inline_handler(func=lambda r: True)
    def handle_chosen(result: telebot.types.ChosenInlineResult):
        user = result.from_user
        result_id = result.result_id
        if ":" not in result_id:
            return
        wtype, wid = result_id.split(":", 1)

        TYPE_LABELS = {
            "first_one":   "همسة لأول شخص 🔒",
            "everyone":    "همسة للجميع 🌍",
            "first_three": "همسة لأول ثلاثة 👥",
            "custom":      "همسة مخصصة 🎯",
        }
        label = TYPE_LABELS.get(wtype, "همسة سرية")

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("👁️ عرض",           callback_data=f"read:{wid}"),
            InlineKeyboardButton("🔒 قفل",            callback_data=f"lock:{wid}"),
        )
        kb.add(
            InlineKeyboardButton("✏️ تعديل",          callback_data=f"edit:{wid}"),
            InlineKeyboardButton("🗑️ حذف",            callback_data=f"delete:{wid}"),
        )
        kb.add(
            InlineKeyboardButton("🧹 مسح المهموس",    callback_data=f"clear:{wid}"),
            InlineKeyboardButton("🕵️‍♂️ الفضوليين",     callback_data=f"curious:{wid}"),
        )
        
        try:
            bot.send_message(
                user.id,
                f"🎛 <b>لوحة تحكم همستك</b>\n"
                f"النوع: {label}\n"
                f"🆔 <code>{wid}</code>",
                parse_mode="HTML",
                reply_markup=kb,
            )
            print(f"✅ تم إرسال لوحة التحكم بنجاح للهمسة: {wid}")
        except Exception as e:
            print(f"❌ حدث خطأ أثناء إرسال لوحة التحكم: {e}")
            logger.error(f"chosen_inline DM Error: {e}")
