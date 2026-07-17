"""
handlers/replies.py — نظام المحادثة ثنائية الاتجاه للهمسات.

البنية
-----------
جميع الردود مرتبطة بالهمسة الأم (whisper_id). هذا ليس تطبيق محادثة؛
كل رد يشير إلى whisper_id محدد.

تدفق المحادثة
-----------------
1. المستخدم ب يقرأ همسة مخصصة → يستلم رسالة خاصة بمحتوى الهمسة
   مع أزرار "↩️ الرد" (و "📜 المحادثة" إن وُجدت ردود).

2. المستخدم ب يضغط ↩️ الرد.
   الكولباك: ``wsp_reply:whisper:<whisper_id>``
   البوت يتحقق من الصلاحيات، ثم يخزّن حالة معلقة:
       user_states[user_b_id] = {
           "action": "pending_whisper_reply",
           "whisper_id": "<id>",
       }
   تُعَدَّل رسالة الهمسة نفسها (وليس إرسال رسالة جديدة) لعرض "✏️ أرسل ردّك…"

3. المستخدم ب يرسل رسالة (نص / صورة / فيديو / صوت / مستند / ملصق / متحركة / جهة اتصال / موقع).
   معالج الرسائل في bot.py يستدعي handle_reply_message().
   البوت يحفظ الرد في قاعدة البيانات، ثم يعيد توجيهه بهوية المرسل الحقيقية
   إلى مرسل الهمسة الأصلي مع زري ↩️ الرد و 📜 المحادثة لتمكينه من الرد.

4. المستخدم أ يضغط ↩️ الرد على إشعار الرد الوارد.
   نفس التدفق — المستخدم أ يصبح في حالة pending_whisper_reply.

5. تستمر السلسلة في كلا الاتجاهين تحت نفس whisper_id.
   فقط المرسل الأصلي والمستقبل الأصلي يمكنهما المشاركة.

الهوية
-------
الردود ليست مجهولة أبداً. يُعرض اسم المرسل (و @username إن وُجد) في كل رد.

سجل المحادثة
-----------------
زر 📜 المحادثة يعرض جميع الردود بترتيب زمني مع أسماء المرسلين والوسائط.
"""

import json
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_IDS
from database import get_whisper, is_banned, get_setting
from database.replies import (
    can_reply_to_whisper,
    create_reply,
    get_reply,
    get_replies,
    count_replies,
    MAX_REPLIES_PER_WHISPER,
)

logger = logging.getLogger(__name__)

# ── بادئات الكولباك ──────────────────────────────────────────────────────────
_REPLY_PREFIX = "wsp_reply:"
_REPLY_WHISPER_PREFIX = "wsp_reply:whisper:"
_REPLY_REPLY_PREFIX  = "wsp_reply:reply:"
_CONV_PREFIX = "wsp_conv:"
_CLOSE_CONV_PREFIX = "close_conv:"
_MAX_CAPTION = 200


# ─────────────────────────────────────────────────────────────────────────────
# دوال مساعدة للأزرار
# ─────────────────────────────────────────────────────────────────────────────

def reply_button(identifier: str, is_reply: bool = False) -> InlineKeyboardButton:
    if is_reply:
        return InlineKeyboardButton("↩️ الرد", callback_data=f"{_REPLY_REPLY_PREFIX}{identifier}")
    return InlineKeyboardButton("↩️ الرد", callback_data=f"{_REPLY_WHISPER_PREFIX}{identifier}")


def conversation_button(whisper_id: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("📜 المحادثة", callback_data=f"{_CONV_PREFIX}{whisper_id}")


def reply_keyboard(identifier: str, is_reply: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(reply_button(identifier, is_reply))
    return kb


def whisper_actions_keyboard(whisper_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    btn_conv = conversation_button(whisper_id) if count_replies(whisper_id) > 0 else None
    if btn_conv:
        kb.add(reply_button(whisper_id), btn_conv)
    else:
        kb.add(reply_button(whisper_id))
    return kb


def whisper_read_keyboard(whisper_id: str, bot_username: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    if bot_username:
        reply_url = f"tg://resolve?domain={bot_username}&start=reply_{whisper_id}"
        kb.add(InlineKeyboardButton("↩️ رد", url=reply_url))
    else:
        kb.add(InlineKeyboardButton("↩️ رد", callback_data=f"{_REPLY_WHISPER_PREFIX}{whisper_id}"))
    if count_replies(whisper_id) > 0:
        kb.add(conversation_button(whisper_id))
    return kb


def _is_reply_callback(data: str) -> bool:
    return data.startswith(_REPLY_PREFIX)


def _is_conv_callback(data: str) -> bool:
    return data.startswith(_CONV_PREFIX)


def _is_close_conv_callback(data: str) -> bool:
    return data.startswith(_CLOSE_CONV_PREFIX)


def _format_time(iso_str) -> str:
    """استخراج الوقت (HH:MM) من نص ISO زمني."""
    if not iso_str:
        return ""
    try:
        return iso_str[:16].replace("T", " ")[11:16]
    except Exception:
        return ""


from handlers._formatting import _get_sender_display

# ─────────────────────────────────────────────────────────────────────────────
# بداية تدفق الرد: معالج الكولباك
# ─────────────────────────────────────────────────────────────────────────────

def _handle_reply_callback(
    bot: telebot.TeleBot,
    call: telebot.types.CallbackQuery,
    user_states: dict,
) -> None:
    """
    عند الضغط على ↩️ الرد في أي رسالة همسة.
    - تبقى رسالة الهمسة كما هي دون تعديل.
    - يتم تخزين حالة معلقة فقط.
    - المستخدم يكتب ردّه مباشرة في محادثة البوت.
    - الضغط مرة أخرى يُلغي حالة الرد المعلقة.
    """
    user = call.from_user
    data = call.data

    parent_reply_id = None
    whisper_id = None

    if data.startswith(_REPLY_WHISPER_PREFIX):
        whisper_id = data[len(_REPLY_WHISPER_PREFIX):]
    elif data.startswith(_REPLY_REPLY_PREFIX):
        ref_reply_id = data[len(_REPLY_REPLY_PREFIX):]
        reply = get_reply(ref_reply_id)
        if not reply:
            bot.answer_callback_query(
                call.id, "❌ الرد غير موجود.", show_alert=True
            )
            return
        whisper_id = reply["whisper_id"]
        parent_reply_id = ref_reply_id
    elif data.startswith(_REPLY_PREFIX):
        whisper_id = data[len(_REPLY_PREFIX):]
    else:
        bot.answer_callback_query(
            call.id, "⚠️ رابط غير صالح.", show_alert=True
        )
        return

    # ── التحقق: الردود مفعّلة ───────────────────────────────────
    if get_setting("whisper_replies_enabled") != "1":
        bot.answer_callback_query(
            call.id, "💬 الردود معطّلة.", show_alert=True
        )
        return

    # ── التحقق: غير محظور ──────────────────────────────────────
    if is_banned(user.id):
        bot.answer_callback_query(
            call.id, "🚫 أنت محظور.", show_alert=True
        )
        return

    # ── التحقق: البوت نشط ──────────────────────────────────────
    if get_setting("bot_active") != "1":
        bot.answer_callback_query(
            call.id, "⚠️ البوت متوقف.", show_alert=True
        )
        return

    # ── التحقق: الهمسة موجودة ──────────────────────────────────
    w = get_whisper(whisper_id)
    if not w:
        bot.answer_callback_query(
            call.id,
            "❌ الهمسة غير موجودة أو تم حذفها.",
            show_alert=True,
        )
        return

    # ── التحقق: الصلاحية ───────────────────────────────────────
    ok, reason = can_reply_to_whisper(whisper_id, user.id)
    if not ok:
        msg_map = {
            "whisper_not_found": "❌ الهمسة غير موجودة.",
            "whisper_locked":    "🔒 الهمسة مقفلة.",
            "not_participant":   "⛔ فقط المرسل والقرّاء يمكنهم الرد.",
            "reply_cap_reached": f"⚠️ تم الوصول للحد الأقصى ({MAX_REPLIES_PER_WHISPER}).",
        }
        bot.answer_callback_query(
            call.id,
            msg_map.get(reason, "❌ لا يمكنك الرد على هذه الهمسة."),
            show_alert=True,
        )
        return

    # ── التبديل: ضغط مرة أخرى = إلغاء ──────────────────────────
    existing = user_states.get(user.id)
    if existing and existing.get("action") == "pending_whisper_reply" \
            and existing.get("whisper_id") == whisper_id:
        user_states.pop(user.id, None)
        bot.answer_callback_query(call.id, "❌ أُلغي الرد.", show_alert=False)
        return

    # ── تخزين الحالة المعلقة ───────────────────────────────────
    state = {
        "action": "pending_whisper_reply",
        "whisper_id": whisper_id,
    }
    if parent_reply_id:
        state["parent_reply_id"] = parent_reply_id
    user_states[user.id] = state

    # إرسال نص الهمسة الأصلية للمستخدم ليرى ماذا سيرد عليه
    try:
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
            reply_text = f"📝 *الهمسة الأصلية:* ({mt_label})\n\n{w['content']}\n\n✏️ أرسل ردّك الآن:"
        else:
            reply_text = f"📝 *الهمسة الأصلية:*\n\n{w['content']}\n\n✏️ أرسل ردّك الآن:"
        bot.send_message(
            user.id,
            reply_text,
            parse_mode="Markdown",
        )
    except Exception:
        pass

    bot.answer_callback_query(call.id, "📝 أرسل ردّك الآن في البوت.", show_alert=False)


# ─────────────────────────────────────────────────────────────────────────────
# معالج محتوى الرد (يُستدعى من bot.py)
# ─────────────────────────────────────────────────────────────────────────────

def handle_reply_message(
    bot: telebot.TeleBot,
    msg: telebot.types.Message,
    user_states: dict,
) -> bool:
    """
    معالجة رسالة واردة عندما يكون المستخدم في حالة pending_whisper_reply.

    تُرجع True إذا تم استهلاك الرسالة (لا ينبغي للمستدعي المتابعة).
    تُرجع False إذا لم تكن هذه حالة رد معلقة (يستمر المستدعي).
    """
    user = msg.from_user
    state = user_states.get(user.id)
    if not state or state.get("action") != "pending_whisper_reply":
        return False

    whisper_id = state["whisper_id"]
    parent_reply_id = state.get("parent_reply_id")

    # ── التحقق: الردود لا تزال مفعّلة ──────────────────────────
    if get_setting("whisper_replies_enabled") != "1":
        bot.send_message(msg.chat.id, "💬 الردود معطّلة.")
        del user_states[user.id]
        return True

    # ── التحقق: الهمسة لا تزال موجودة ──────────────────────────
    w = get_whisper(whisper_id)
    if not w:
        bot.send_message(msg.chat.id, "❌ الهمسة لم تعد موجودة.")
        del user_states[user.id]
        return True

    # ── استخراج المحتوى + الوسائط ──────────────────────────────
    content, media_type, file_id = _extract_media(msg)

    if not content and not file_id:
        bot.send_message(
            msg.chat.id,
            "⚠️ رسالة فارغة. أرسل نصاً أو وسائط.",
        )
        return True

    # ── حفظ الرد ───────────────────────────────────────────────
    reply_id = create_reply(
        whisper_id=whisper_id,
        sender_id=user.id,
        content=content,
        media_type=media_type,
        file_id=file_id,
        parent_reply_id=parent_reply_id,
    )
    if not reply_id:
        bot.send_message(
            msg.chat.id,
            "⚠️ تعذّر حفظ الرد (قد يكون الحد الأقصى قد بَلَغ).",
        )
        del user_states[user.id]
        return True

    # ── توجيه الرد إلى المستلمين ───────────────────────────────
    delivery_ok = False
    try:
        delivery_ok = _route_reply(bot, msg, reply_id, whisper_id, user.id, w,
                                   content, media_type, file_id, parent_reply_id)
    except Exception as exc:
        print(f"DEBUG: _route_reply exception: {exc}")
        logger.error(f"unhandled exception in _route_reply: {exc}", exc_info=True)

    # ── تأكيد إرسال الرد للقارئ ────────────────────────────────
    if delivery_ok:
        try:
            bot.send_message(user.id, "✅ تم إرسال ردك بنجاح!")
        except Exception:
            pass
    else:
        try:
            bot.send_message(
                user.id,
                "⚠️ تعذّر إرسال ردّك إلى المستلم. "
                "قد يكون البوت محظوراً لدى المستخدم أو أن المستخدم أوقف البوت."
            )
        except Exception:
            pass

    # ── إخطار الآدمن بنسخة من الرد ─────────────────────────────
    try:
        sender_display = _get_sender_display(user.id)
        admin_parts = ["📬 *رد جديد على همسة*\n"]
        admin_parts.append(f"👤 من: {sender_display}")
        admin_parts.append(f"🆔 ايدي المرسل: `{user.id}`")
        admin_parts.append(f"🔗 معرف الهمسة: `{whisper_id}`")
        if content:
            admin_parts.append(f"\n💬 *نص الرد:*\n{content}")
        if media_type:
            admin_parts.append(f"📎 *نوع المرفق:* `{media_type}`")
        admin_parts.append(f"\n📨 *الهمسة الأصلية:*\n{w['content']}")
        admin_text = "\n".join(admin_parts)
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, admin_text, parse_mode=None)
            except Exception:
                pass
    except Exception:
        pass

    del user_states[user.id]
    return True


# ─────────────────────────────────────────────────────────────────────────────
# استعادة رسالة الهمسة
# ─────────────────────────────────────────────────────────────────────────────

def _restore_whisper_message(
    bot: telebot.TeleBot,
    chat_id: int,
    message_id: int,
    whisper_row,
    whisper_id: str,
) -> None:
    """تعديل رسالة الهمسة الخاصة لعرض محتواها الأصلي مع أزرار الإجراءات."""
    kb = whisper_actions_keyboard(whisper_id)
    w_dict = dict(whisper_row)
    if w_dict.get("message_type"):
        mt_label = {
            "photo": "🖼 صورة",
            "video": "🎬 فيديو",
            "voice": "🎤 تسجيل صوتي",
            "audio": "🎵 ملف صوتي",
            "document": "📄 مستند",
            "location": "📍 موقع",
        }.get(w_dict["message_type"], w_dict["message_type"])
        content = w_dict.get("content") or ""
        display = f"🤫 *الهمسة:* ({mt_label})"
        if content:
            display += f"\n\n{content}"
        try:
            bot.edit_message_text(
                display,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception:
            pass
    else:
        try:
            bot.edit_message_text(
                f"🤫 *الهمسة:*\n\n{whisper_row['content']}",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=None,
                reply_markup=kb,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# التوجيه: إعادة توجيه الرد مع هوية المرسل إلى جميع المشاركين الآخرين
# ─────────────────────────────────────────────────────────────────────────────

def _route_reply(
    bot: telebot.TeleBot,
    original_msg: telebot.types.Message,
    reply_id: str,
    whisper_id: str,
    replier_id: int,
    whisper_row,
    content: str,
    media_type,
    file_id,
    parent_reply_id=None,
) -> bool:
    """
    توجيه الرد إلى المستلم الصحيح مع هوية المرسل.

    التوجيه ثنائي الاتجاه:
    - إذا وُجد parent_reply_id، يُوجَّه الرد إلى مرسل ذلك الرد الأصل
    - وإلا، يُوجَّه الرد إلى مرسل الهمسة الأصلي

    تتضمن الرسالة المُرسلة زري ↩️ الرد و 📜 المحادثة.

    تُرجع True إذا نجح التوصيل إلى مستلم واحد على الأقل، False إذا فشل الكل.
    """
    sender_display = _get_sender_display(replier_id)
    header = f"💬 *رد من:*\n{sender_display}\n\n"

    sender_id = whisper_row["sender_id"]

    if parent_reply_id:
        parent_reply = get_reply(parent_reply_id)
        if parent_reply:
            sender_id = parent_reply["sender_id"]

    if sender_id is None:
        bot.send_message(
            original_msg.chat.id,
            "⚠️ عذراً، لا يمكن الوصول لبيانات مرسل الهمسة لتوجيه الرد إليه.",
        )
        return False
    recipients = {int(sender_id)}

    if not recipients:
        return False

    # لوحة المفاتيح مع زري الرد والمحادثة في صف واحد
    kb = InlineKeyboardMarkup(row_width=2)
    btn_conv = conversation_button(whisper_id) if count_replies(whisper_id) > 1 else None
    if btn_conv:
        kb.add(reply_button(reply_id, is_reply=True), btn_conv)
    else:
        kb.add(reply_button(reply_id, is_reply=True))

    success = False
    for recipient_id in recipients:
        try:
            _deliver_reply(
                bot, original_msg, recipient_id,
                header, content, media_type, file_id, kb,
            )
            success = True
        except Exception as exc:
            print(f"DEBUG: Telegram API Error: {exc}")
            logger.error(
                f"reply delivery failed whisper={whisper_id!r} "
                f"recipient={recipient_id} reply={reply_id!r}: {exc}"
            )
    return success


def _deliver_reply(
    bot: telebot.TeleBot,
    original_msg: telebot.types.Message,
    recipient_id: int,
    header: str,
    content: str,
    media_type,
    file_id,
    kb: InlineKeyboardMarkup,
) -> None:
    """إرسال محتوى الرد إلى مستلم واحد مع هوية المرسل."""
    print(f"DEBUG: Trying to send to sender_id: {recipient_id} | Type: {type(recipient_id)}")
    if recipient_id is None:
        print("DEBUG: recipient_id is None, cannot send")
        return
    if media_type == "photo":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_photo(recipient_id, file_id, caption=caption,
                       parse_mode=None, reply_markup=kb)

    elif media_type == "video":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_video(recipient_id, file_id, caption=caption,
                       parse_mode=None, reply_markup=kb)

    elif media_type == "voice":
        bot.send_voice(recipient_id, file_id, reply_markup=kb)
        if content:
            bot.send_message(recipient_id, header + content,
                             parse_mode=None)

    elif media_type == "audio":
        bot.send_audio(recipient_id, file_id, reply_markup=kb)
        if content:
            bot.send_message(recipient_id, header + content,
                             parse_mode=None)

    elif media_type == "document":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_document(recipient_id, file_id, caption=caption,
                          parse_mode=None, reply_markup=kb)

    elif media_type == "sticker":
        bot.send_sticker(recipient_id, file_id)
        bot.send_message(recipient_id, header.rstrip(),
                         parse_mode=None, reply_markup=kb)

    elif media_type == "animation":
        caption = (header + content)[:1024] if content else header.rstrip()
        bot.send_animation(recipient_id, file_id, caption=caption,
                           parse_mode=None, reply_markup=kb)

    elif media_type == "contact":
        cd = json.loads(file_id)
        bot.send_contact(
            recipient_id,
            phone_number=cd["phone_number"],
            first_name=cd["first_name"],
            last_name=cd.get("last_name", ""),
            reply_markup=kb,
        )
        if content:
            bot.send_message(recipient_id, header + content,
                             parse_mode=None)

    elif media_type == "location":
        loc = json.loads(file_id)
        bot.send_location(
            recipient_id,
            latitude=loc["latitude"],
            longitude=loc["longitude"],
            reply_markup=kb,
        )
        if content:
            bot.send_message(recipient_id, header + content,
                             parse_mode=None)

    else:
        # رد نصي فقط
        text = header + (content or "")
        text = text[:4096]
        bot.send_message(recipient_id, text,
                         parse_mode=None, reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
# سجل المحادثة
# ─────────────────────────────────────────────────────────────────────────────

def _handle_conversation_callback(
    bot: telebot.TeleBot,
    call: telebot.types.CallbackQuery,
    user_states: dict,
) -> None:
    """عرض سجل الردود الكامل لهمسة — يُعَدِّل الرسالة الحالية."""
    user = call.from_user
    whisper_id = call.data[len(_CONV_PREFIX):]

    w = get_whisper(whisper_id)
    if not w:
        bot.answer_callback_query(
            call.id, "❌ الهمسة غير موجودة.", show_alert=True
        )
        return

    ok, reason = can_reply_to_whisper(whisper_id, user.id)
    if not ok:
        bot.answer_callback_query(
            call.id, "⛔ لا يمكنك عرض هذه المحادثة.", show_alert=True
        )
        return

    replies = get_replies(whisper_id)
    if not replies:
        bot.answer_callback_query(
            call.id, "📜 لا توجد ردود بعد.", show_alert=True
        )
        return

    bot.answer_callback_query(call.id)

    # بناء نص المحادثة — الهمسة ثم الردود بالتسلسل
    lines = []
    lines.append("🤫 *الهمسة*")
    w_dict = dict(w)
    if w_dict.get("message_type"):
        mt_label = {
            "photo": "🖼 [صورة]",
            "video": "🎬 [فيديو]",
            "voice": "🎤 [تسجيل صوتي]",
            "audio": "🎵 [موسيقى]",
            "document": "📄 [مستند]",
            "location": "📍 [موقع]",
        }.get(w_dict["message_type"], f"[{w_dict['message_type']}]")
        content = w_dict.get("content") or ""
        if content:
            lines.append(f"{mt_label} {content}")
        else:
            lines.append(mt_label)
    else:
        lines.append(w['content'])
    lines.append("")
    lines.append("───────────────")

    for r in replies:
        sender = _get_sender_display(r["sender_id"])
        time_str = _format_time(r.get("created_at", ""))
        time_tag = f" • {time_str}" if time_str else ""
        reply_text = r["content"] or ""
        if r["media_type"]:
            media_label = {
                "photo": "🖼 [صورة]",
                "video": "🎬 [فيديو]",
                "voice": "🎤 [تسجيل صوتي]",
                "audio": "🎵 [موسيقى]",
                "document": "📄 [مستند]",
                "sticker": "🏷 [ملصق]",
                "animation": "🎞 [متحركة]",
                "contact": "👤 [جهة اتصال]",
                "location": "📍 [موقع]",
            }.get(r["media_type"], f"[{r['media_type']}]")
            if reply_text:
                reply_entry = f"{media_label}\n{reply_text}"
            else:
                reply_entry = media_label
        else:
            reply_entry = reply_text

        lines.append(f"👤 {sender}{time_tag}")
        if reply_entry:
            lines.append(reply_entry)
        lines.append("")

    lines.append("───────────────")

    conv_text = "\n".join(lines)
    conv_text = conv_text[:4096]  # حد تليجرام

    conv_kb = InlineKeyboardMarkup(row_width=2)
    conv_kb.add(
        reply_button(whisper_id),
        InlineKeyboardButton("🔙 رجوع", callback_data=f"{_CLOSE_CONV_PREFIX}{whisper_id}"),
    )

    try:
        bot.edit_message_text(
            conv_text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode=None,
            reply_markup=conv_kb,
        )
    except Exception as exc:
        logger.error(f"conv edit_message_text failed: {exc}")
        try:
            bot.send_message(
                user.id,
                conv_text,
                parse_mode=None,
                reply_markup=conv_kb,
            )
        except Exception:
            pass


def _send_reply_to_user(
    bot: telebot.TeleBot,
    recipient_id: int,
    sender_display: str,
    content: str,
    media_type,
    file_id,
) -> None:
    """
    إرسال رد واحد (نص أو وسائط) مع نسبه إلى المرسل.
    يُستخدم عند عرض سجل المحادثة في رسالة منفصلة (حالة احتياطية).
    """
    header = f"💬 *{sender_display}*"

    if media_type == "photo":
        caption = header
        if content:
            caption += f"\n\n{content}"
        bot.send_photo(recipient_id, file_id, caption=caption[:1024],
                       parse_mode=None)

    elif media_type == "video":
        caption = header
        if content:
            caption += f"\n\n{content}"
        bot.send_video(recipient_id, file_id, caption=caption[:1024],
                       parse_mode=None)

    elif media_type == "voice":
        bot.send_voice(recipient_id, file_id)
        if content:
            bot.send_message(recipient_id, f"{header}\n\n{content}",
                             parse_mode=None)
        else:
            bot.send_message(recipient_id, header, parse_mode=None)

    elif media_type == "audio":
        bot.send_audio(recipient_id, file_id)
        if content:
            bot.send_message(recipient_id, f"{header}\n\n{content}",
                             parse_mode=None)
        else:
            bot.send_message(recipient_id, header, parse_mode=None)

    elif media_type == "document":
        caption = header
        if content:
            caption += f"\n\n{content}"
        bot.send_document(recipient_id, file_id, caption=caption[:1024],
                          parse_mode=None)

    elif media_type == "sticker":
        bot.send_sticker(recipient_id, file_id)
        bot.send_message(recipient_id, header, parse_mode=None)

    elif media_type == "animation":
        caption = header
        if content:
            caption += f"\n\n{content}"
        bot.send_animation(recipient_id, file_id, caption=caption[:1024],
                           parse_mode=None)

    elif media_type == "contact":
        cd = json.loads(file_id)
        bot.send_contact(
            recipient_id,
            phone_number=cd["phone_number"],
            first_name=cd["first_name"],
            last_name=cd.get("last_name", ""),
        )
        if content:
            bot.send_message(recipient_id, f"{header}\n\n{content}",
                             parse_mode=None)

    elif media_type == "location":
        loc = json.loads(file_id)
        bot.send_location(
            recipient_id,
            latitude=loc["latitude"],
            longitude=loc["longitude"],
        )
        if content:
            bot.send_message(recipient_id, f"{header}\n\n{content}",
                             parse_mode=None)

    else:
        text = header
        if content:
            text += f"\n\n{content}"
        bot.send_message(recipient_id, text[:4096], parse_mode=None)


# ─────────────────────────────────────────────────────────────────────────────
# دالة استخراج الوسائط
# ─────────────────────────────────────────────────────────────────────────────

def _extract_media(msg: telebot.types.Message):
    """
    استخراج (content, media_type, file_id) من رسالة تليجرام.

    content هو النص أو التعليق (قد يكون فارغاً).
    media_type هو أحد SUPPORTED_MEDIA + animation/contact/location،
               أو None للنص فقط.
    file_id هو file_id الخاص بتليجرام (أو JSON للاتصال/الموقع) أو None.

    للاتصال والموقع، يُخزَّن file_id كـ JSON blob.
    """
    content = ""
    media_type = None
    file_id = None

    if msg.content_type == "text":
        content = (msg.text or "").strip()

    elif msg.content_type == "photo":
        media_type = "photo"
        file_id = msg.photo[-1].file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "video":
        media_type = "video"
        file_id = msg.video.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "voice":
        media_type = "voice"
        file_id = msg.voice.file_id

    elif msg.content_type == "audio":
        media_type = "audio"
        file_id = msg.audio.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "document":
        media_type = "document"
        file_id = msg.document.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "sticker":
        media_type = "sticker"
        file_id = msg.sticker.file_id

    elif msg.content_type == "animation":
        media_type = "animation"
        file_id = msg.animation.file_id
        content = (msg.caption or "").strip()

    elif msg.content_type == "contact":
        media_type = "contact"
        cd = msg.contact
        file_id = json.dumps({
            "phone_number": cd.phone_number,
            "first_name": cd.first_name,
            "last_name": cd.last_name or "",
        })

    elif msg.content_type == "location":
        media_type = "location"
        loc = msg.location
        file_id = json.dumps({
            "latitude": loc.latitude,
            "longitude": loc.longitude,
        })

    return content, media_type, file_id


# ─────────────────────────────────────────────────────────────────────────────
# تسجيل المعالجات
# ─────────────────────────────────────────────────────────────────────────────

def register_reply_handlers(bot: telebot.TeleBot, user_states: dict) -> None:
    """
    تسجيل معالجات كولباك الرد والمحادثة والإغلاق في البوت.

    معالج الرسائل غير مُسجَّل هنا — إنه مدمج في handle_messages()
    في bot.py للحفاظ على ترتيب المعالجات وضمان فحص حالة الرد
    قبل الحالات الأخرى.
    """

    @bot.callback_query_handler(func=lambda c: _is_reply_callback(c.data))
    def reply_callback(call: telebot.types.CallbackQuery):
        try:
            _handle_reply_callback(bot, call, user_states)
        except Exception as exc:
            logger.error(f"reply_callback unhandled: {exc}", exc_info=True)
            try:
                bot.answer_callback_query(
                    call.id, "⚠️ حدث خطأ غير متوقع.", show_alert=True
                )
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda c: _is_conv_callback(c.data))
    def conv_callback(call: telebot.types.CallbackQuery):
        try:
            _handle_conversation_callback(bot, call, user_states)
        except Exception as exc:
            logger.error(f"conv_callback unhandled: {exc}", exc_info=True)
            try:
                bot.answer_callback_query(
                    call.id, "⚠️ حدث خطأ غير متوقع.", show_alert=True
                )
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda c: _is_close_conv_callback(c.data))
    def close_conv_callback(call: telebot.types.CallbackQuery):
        """إغلاق المحادثة — استعادة رسالة الهمسة الأصلية."""
        whisper_id = call.data[len(_CLOSE_CONV_PREFIX):]
        w = get_whisper(whisper_id)
        if w:
            try:
                _restore_whisper_message(
                    bot, call.message.chat.id, call.message.message_id,
                    w, whisper_id,
                )
            except Exception as exc:
                logger.error(f"close_conv restore failed: {exc}")
        bot.answer_callback_query(call.id, "✅ تم العودة.", show_alert=False)
