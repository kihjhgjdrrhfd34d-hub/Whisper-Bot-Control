import logging
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import confirm_button, cancel_button
from database import (
    get_whisper, get_user, can_read_whisper, add_reader_if_new,
    add_curious,
    toggle_whisper_lock, lock_whisper, delete_whisper, clear_whisper_readers,
    get_readers, get_curious_ones,
    get_setting, get_group_settings, is_banned, reader_count,
    create_whisper,
    check_whisper_rate_limit, record_whisper_timestamp, SPAM_BLOCK_MESSAGE,
    update_whisper_group_message,
)
from conditions import ConditionUI, registry as condition_registry
from handlers.dashboard import send_dashboard
from services.whisper_service import (
    ensure_user,
    parse_whisper_id,
    is_destructive_whisper,
    is_own_whisper,
    record_read_and_check,
    get_opener_name,
    get_user_display,
    get_reader_display_name,
    build_first_one_notification,
    build_first_three_read_notification,
    build_read_receipt_message,
    build_destructive_receipt_message,
    build_public_whisper_notification,
)

logger = logging.getLogger(__name__)

_OPENED_LABEL = "تم قراءة الهمسة 🔒"
_BEFOREAD_LABEL = "اضغط للرؤية 🔓"

_KEYBOARD_LOCKS: dict[str, threading.Lock] = {}

def _get_keyboard_lock(whisper_id: str) -> threading.Lock:
    return _KEYBOARD_LOCKS.setdefault(whisper_id, threading.Lock())


def _extract_condition_config(w_dict: dict, condition_type: str) -> dict | None:
    """Extract config dict for a condition_type from whisper's conditions_data."""
    raw = w_dict.get("conditions_data")
    if not raw:
        return None
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(raw, dict):
        cfg = raw.get(condition_type)
        return dict(cfg) if isinstance(cfg, dict) else None
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and (item.get("type") == condition_type or item.get("condition_name") == condition_type):
                return {k: v for k, v in item.items() if k not in ("type", "id", "condition_name")}
    return None


def _build_opened_keyboard(whisper_id, readers=None):
    w = get_whisper(whisper_id)
    if not w:
        return None
    wtype = w["whisper_type"]

    if wtype == "everyone":
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton(_BEFOREAD_LABEL, callback_data=f"read:{whisper_id}"))
        _add_reaction_buttons(kb, whisper_id)
        return kb

    kb = InlineKeyboardMarkup(row_width=2)
    is_first_three_unlocked = wtype == "first_three" and len(readers) < 3
    label = _BEFOREAD_LABEL if is_first_three_unlocked else _OPENED_LABEL
    cb = f"read:{whisper_id}" if is_first_three_unlocked else "noop"
    kb.add(InlineKeyboardButton(label, callback_data=cb))
    if readers:
        show_names = False
        if wtype == "first_one":
            show_names = True
        elif wtype == "first_three" and len(readers) > 0:
            show_names = True
        if show_names:
            max_names = 3 if wtype == "first_three" else len(readers)
            names_added = []
            for r in readers[:max_names]:
                name = get_reader_display_name(r)
                names_added.append(name)
                kb.add(InlineKeyboardButton(f"👤 {name}", callback_data="noop"))
            logger.info("[UI] _build_opened_keyboard names=%s whisper_id=%s wtype=%s",
                        names_added, whisper_id, wtype)
    return kb


def _edit_group_to_opened(bot, whisper_id, call=None):
    w = get_whisper(whisper_id)
    if not w:
        return
    w_data = dict(w)

    inline_msg_id = w_data.get("group_inline_message_id")
    group_chat_id = w_data.get("group_chat_id")
    group_msg_id = w_data.get("group_message_id")

    if not inline_msg_id and not (group_chat_id and group_msg_id):
        return

    readers = get_readers(whisper_id)
    kb = _build_opened_keyboard(whisper_id, readers=readers)
    if kb is None:
        return

    try:
        if call and getattr(call, "inline_message_id", None):
            imid = call.inline_message_id
            logger.info("[UI] _edit_group_to_opened via call.inline_message_id=%s whisper_id=%s", imid, whisper_id)
            bot.edit_message_reply_markup(
                inline_message_id=imid, reply_markup=kb,
            )
        elif inline_msg_id:
            logger.info("[UI] _edit_group_to_opened via DB inline_message_id=%s whisper_id=%s", inline_msg_id, whisper_id)
            bot.edit_message_reply_markup(
                inline_message_id=inline_msg_id, reply_markup=kb,
            )
        elif group_chat_id and group_msg_id:
            logger.info("[UI] _edit_group_to_opened via DB chat_id=%s message_id=%s whisper_id=%s", group_chat_id, group_msg_id, whisper_id)
            bot.edit_message_reply_markup(
                chat_id=group_chat_id, message_id=group_msg_id, reply_markup=kb,
            )
    except Exception as e:
        err_str = str(e).lower()
        if "not modified" in err_str or "message_not_modified" in err_str:
            logger.debug("[UI] _edit_group_to_opened not-modified whisper_id=%s: %s", whisper_id, e)
        else:
            logger.warning("[UI] _edit_group_to_opened FAILED for whisper_id=%s: %s", whisper_id, e)


def _destroy_whisper_message(call, bot):
    """Delete the whisper message from the group; fallback to editing text."""
    if call is None:
        return
    try:
        if call.message:
            bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as exc:
        logger.debug("[DESTROY] delete_message failed: %s", exc)
        try:
            text = "💣 تم تدمير هذه الهمسة بعد قراءتها"
            if call.message:
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=None)
            elif call.inline_message_id:
                bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=None)
        except Exception as inner_exc:
            logger.warning("[DESTROY] fallback edit also failed: %s", inner_exc)


def _update_group_keyboard(bot, whisper_id, w, call=None):
    if not isinstance(w, dict):
        w = dict(w)

    wtype = w["whisper_type"]

    lock = _get_keyboard_lock(whisper_id)
    with lock:
        readers = get_readers(whisper_id)
        reader_count_val = len(readers)

        kb = InlineKeyboardMarkup(row_width=2)

        if wtype == "everyone":
            kb.add(InlineKeyboardButton(_BEFOREAD_LABEL, callback_data=f"read:{whisper_id}"))
            _add_reaction_buttons(kb, whisper_id)

        elif wtype == "first_three":
            actual_count = reader_count(whisper_id)
            if actual_count >= 3:
                kb.add(InlineKeyboardButton(_OPENED_LABEL, callback_data="noop"))
            else:
                kb.add(InlineKeyboardButton(_BEFOREAD_LABEL, callback_data=f"read:{whisper_id}"))
            for r in readers:
                name = get_reader_display_name(r)
                kb.add(InlineKeyboardButton(f"👤 {name}", callback_data="noop"))

        elif wtype == "first_one":
            kb.add(InlineKeyboardButton(_OPENED_LABEL, callback_data="noop"))
            for r in readers:
                name = get_reader_display_name(r)
                kb.add(InlineKeyboardButton(f"👤 {name}", callback_data="noop"))
        else:  # custom — لا نعرض أسماء القراء ولا أزرار التفاعل
            kb.add(InlineKeyboardButton(_OPENED_LABEL, callback_data="noop"))

        inline_msg_id = w.get("group_inline_message_id")
        group_chat_id = w.get("group_chat_id")
        group_msg_id = w.get("group_message_id")

        try:
            if call and getattr(call, "inline_message_id", None):
                imid = call.inline_message_id
                logger.info("[UI] _update_group_keyboard editing via call.inline_message_id=%s whisper_id=%s reader_count=%s",
                            imid, whisper_id, reader_count_val)
                bot.edit_message_reply_markup(
                    inline_message_id=imid, reply_markup=kb,
                )
                logger.info("[UI] _update_group_keyboard OK via call.inline_message_id=%s whisper_id=%s",
                            imid, whisper_id)
            elif call and call.message and call.message.chat.id and call.message.message_id:
                cid = call.message.chat.id
                mid = call.message.message_id
                logger.info("[UI] _update_group_keyboard editing via call.message chat_id=%s message_id=%s whisper_id=%s reader_count=%s",
                            cid, mid, whisper_id, reader_count_val)
                bot.edit_message_reply_markup(
                    chat_id=cid,
                    message_id=mid, reply_markup=kb,
                )
                logger.info("[UI] _update_group_keyboard OK via call.message chat_id=%s message_id=%s whisper_id=%s",
                            cid, mid, whisper_id)
            elif inline_msg_id:
                logger.info("[UI] _update_group_keyboard editing via DB inline_message_id=%s whisper_id=%s reader_count=%s",
                            inline_msg_id, whisper_id, reader_count_val)
                bot.edit_message_reply_markup(
                    inline_message_id=inline_msg_id, reply_markup=kb,
                )
                logger.info("[UI] _update_group_keyboard OK via DB inline_message_id=%s whisper_id=%s",
                            inline_msg_id, whisper_id)
            elif group_chat_id and group_msg_id:
                logger.info("[UI] _update_group_keyboard editing via DB chat_id=%s message_id=%s whisper_id=%s reader_count=%s",
                            group_chat_id, group_msg_id, whisper_id, reader_count_val)
                bot.edit_message_reply_markup(
                    chat_id=group_chat_id,
                    message_id=group_msg_id, reply_markup=kb,
                )
                logger.info("[UI] _update_group_keyboard OK via DB chat_id=%s message_id=%s whisper_id=%s",
                            group_chat_id, group_msg_id, whisper_id)
            else:
                logger.warning(
                    "[UI] SKIPPING keyboard update — no valid coords available! whisper_id=%s",
                    whisper_id,
                )
        except Exception as e:
            err_str = str(e).lower()
            if "not modified" in err_str or "message_not_modified" in err_str:
                logger.debug(
                    "[UI] _update_group_keyboard not-modified whisper_id=%s: %s",
                    whisper_id, e,
                )
            else:
                logger.warning("[UI] _update_group_keyboard FAILED for whisper_id=%s: %s",
                               whisper_id, e)

        return reader_count_val


def _add_reaction_buttons(kb, whisper_id):
    try:
        from enterprise.db_enterprise import count_whisper_likes, count_whisper_dislikes
        like_count = count_whisper_likes(whisper_id)
        dislike_count = count_whisper_dislikes(whisper_id)
    except Exception as exc:
        logger.warning("[UI] reaction count failed for whisper_id=%s: %s", whisper_id, exc)
        like_count = 0
        dislike_count = 0
    kb.add(
        InlineKeyboardButton(f"❤️ {like_count}", callback_data=f"like:{whisper_id}"),
        InlineKeyboardButton(f"👎 {dislike_count}", callback_data=f"dislike:{whisper_id}"),
    )


def _complete_read_flow(bot, call, user, whisper_id, w, is_destructive):
    """Shared read-completion logic.
    Called from handle_read() (callback context, call is not None)
    and cond_answer_handler (message context, call is None).
    """
    # Normalize sqlite3.Row → dict so closures can safely use .get()
    w = dict(w) if not isinstance(w, dict) else w

    # ── Helper closures ──────────────────────────────────────────────
    def _check_access():
        can, reason = can_read_whisper(whisper_id, user.id)
        if can:
            return True

        msgs = {
            "locked": "🔒 الهمسة مقفلة حالياً من قِبل صاحبها.",
            "taken_one": "تم فتح هذه الهمسة بواسطة أول شخص.",
            "taken_three": "اكتمل عدد القراء لهذه الهمسة.",
            "taken": "🔒 هذه الهمسة تم فتحها بالفعل.",
            "not_target": "الهمسه ليست لك بطل فضول 😂",
            "not_found": "❌ هذه الهمسة غير موجودة.",
            "unknown": "❌ خطأ في التحقق من الهمسة.",
        }
        default_msg = "❌ لا يمكنك قراءة هذه الهمسة."

        if reason == "not_target":
            add_curious(whisper_id, user.id)
        elif reason == "not_found":
            logger.warning("[ACCESS] not_found whisper_id=%s user_id=%s", whisper_id, user.id)
        elif reason == "unknown":
            logger.error("[ACCESS] unknown whisper_id=%s user_id=%s wtype=%s", whisper_id, user.id, w.get("whisper_type", "?"))

        if reason == "taken":
            if w["whisper_type"] == "first_one":
                msg = msgs["taken_one"]
            elif w["whisper_type"] == "first_three":
                msg = msgs["taken_three"]
            else:
                msg = msgs["taken"]
        else:
            msg = msgs.get(reason, default_msg)

        if call:
            bot.answer_callback_query(call.id, msg, show_alert=True)
        else:
            bot.send_message(user.id, msg)
        return False

    def _handle_destructive_everyone():
        if not (is_destructive and w["whisper_type"] == "everyone"):
            return False
        logger.info("[DESTROY] whisper_id=%s type=everyone is_destructive=True", whisper_id)
        is_new = add_reader_if_new(whisper_id, user.id)
        logger.info("[DESTROY] add_reader_if_new result=%s whisper_id=%s", is_new, whisper_id)
        if not is_new:
            msg = "🔒 لقد قرأت هذه الهمسة التدميرية من قبل!"
            if call:
                bot.answer_callback_query(call.id, msg, show_alert=True)
            else:
                bot.send_message(user.id, msg)
            return True
        if call:
            bot.answer_callback_query(call.id, f"💣 {w['content']}", show_alert=True)
        else:
            bot.send_message(user.id, f"💣 {w['content']}")
        logger.info("[DESTROY] content shown whisper_id=%s", whisper_id)
        # everyone type: NEVER modify group keyboard, NEVER lock/delete, keep in DB
        if get_setting("read_receipt_enabled") == "1":
            try:
                bot.send_message(w["sender_id"], build_destructive_receipt_message(user, w))
            except Exception as exc:
                logger.warning("[DESTROY] destructive receipt failed for sender_id=%s: %s", w["sender_id"], exc)
        _send_reply_invitation(whisper_id)
        logger.info("[DESTROY] complete (no group edit, no lock/delete) whisper_id=%s", whisper_id)
        return True

    def _answer_with_content():
        w_dict = dict(w)
        if w_dict.get("message_type"):
            mt_label = {
                "photo": "🖼 صورة", "video": "🎬 فيديو", "voice": "🎤 تسجيل صوتي",
                "audio": "🎵 ملف صوتي", "document": "📄 مستند", "location": "📍 موقع",
                "animation": "🎞 متحركة",
            }.get(w_dict["message_type"], w_dict["message_type"])
            caption = w_dict.get("content") or w_dict.get("caption") or ""
            alert_text = f"🤫 {mt_label}"
            if caption:
                alert_text += f"\n{caption}"
            if call:
                bot.answer_callback_query(call.id, alert_text, show_alert=True)
            else:
                bot.send_message(user.id, alert_text)
        else:
            content = f"🤫 {w['content']}" if w.get("content") else "🤫 (محتوى فارغ)"
            if call:
                bot.answer_callback_query(call.id, content, show_alert=True)
            else:
                bot.send_message(user.id, content)

    def _maybe_self_destruct(is_new_read, reader_count_val):
        if is_destructive and is_new_read:
            wtype_str = w["whisper_type"] if isinstance(w, dict) else dict(w).get("whisper_type", "?")
            logger.info("[DESTROY] _maybe_self_destruct whisper_id=%s type=%s reader_count_val=%d",
                        whisper_id, wtype_str, reader_count_val)
            if w["whisper_type"] == "first_one":
                logger.info("[DESTROY] first_one lock+destroy whisper_id=%s", whisper_id)
                lock_whisper(whisper_id)
                _destroy_whisper_message(call, bot)
                logger.info("[DESTROY] first_one complete whisper_id=%s", whisper_id)
            elif w["whisper_type"] == "first_three" and reader_count_val >= 3:
                logger.info("[DESTROY] first_three lock+destroy whisper_id=%s readers=%d", whisper_id, reader_count_val)
                lock_whisper(whisper_id)
                _destroy_whisper_message(call, bot)
                logger.info("[DESTROY] first_three complete whisper_id=%s", whisper_id)
            elif w["whisper_type"] == "everyone":
                logger.info("[DESTROY] everyone lock+destroy whisper_id=%s", whisper_id)
                lock_whisper(whisper_id)
                _destroy_whisper_message(call, bot)
                logger.info("[DESTROY] everyone complete whisper_id=%s", whisper_id)

    def _notify_sender_first_three_read():
        text = build_first_three_read_notification(user, w)
        try:
            bot.send_message(w["sender_id"], text)
        except Exception as exc:
            logger.warning("[NOTIFY] _notify_sender_first_three_read failed: %s", exc)

    def _notify_sender_reader_name(reader_count_val):
        try:
            display = get_user_display(user)
            bot.send_message(w["sender_id"], f"👀 قام {display} بفتح همستك.\n\nعدد القراء: {reader_count_val}")
        except Exception as exc:
            logger.warning("[NOTIFY] _notify_sender_reader_name failed: %s", exc)

    def _notify_sender_read():
        w_dict = dict(w) if not isinstance(w, dict) else w
        text = build_read_receipt_message(user, w_dict)
        try:
            bot.send_message(w_dict["sender_id"], text)
        except Exception as exc:
            logger.warning("[NOTIFY] _notify_sender_read failed: %s", exc)

    def _notify_sender_first_one(is_first_ever):
        if w["whisper_type"] != "first_one" or not is_first_ever:
            return False
        try:
            bot.send_message(w["sender_id"], build_first_one_notification(user, w), parse_mode="HTML")
        except Exception as e:
            logger.debug(f"first_one notify: {e}")
        return True

    def _send_read_receipt(is_new_read):
        if is_new_read and get_setting("read_receipt_enabled") == "1":
            try:
                bot.send_message(w["sender_id"], build_read_receipt_message(user, w))
            except Exception as exc:
                logger.warning("[SEND] _send_read_receipt failed for sender_id=%s: %s", w["sender_id"], exc)

    def _send_reply_invitation(wid: str):
        try:
            rw = get_whisper(wid)
            if not rw:
                return
            rw = dict(rw)
            sender = get_user(rw["sender_id"])
            if sender:
                first = (sender["first_name"] or "").strip()
                last = (sender["last_name"] or "").strip()
                sname = " ".join(filter(None, [first, last])).strip()
                if not sname:
                    sname = f"@{sender['username']}" if sender.get("username") else "شخص"
            else:
                sname = "شخص"
            content_text = rw.get("content") or ""
            if not content_text and rw.get("message_type"):
                mt_label = {
                    "photo": "🖼 صورة", "video": "🎬 فيديو", "voice": "🎤 تسجيل صوتي",
                    "audio": "🎵 ملف صوتي", "document": "📄 مستند", "location": "📍 موقع",
                    "animation": "🎞 متحركة",
                }.get(rw["message_type"], rw["message_type"])
                caption = rw.get("caption") or ""
                content_text = mt_label
                if caption:
                    content_text += f"\n{caption}"
            wtype_label = {
                "first_one": "همسة لأول شخص",
                "first_three": "همسة لأول 3 أشخاص",
                "everyone": "همسة للجميع",
                "contact_whisper": "همسة تواصل",
            }.get(rw.get("whisper_type", ""), rw.get("whisper_type", ""))
            msg_text = (
                f"💌 همسة جديدة للرد\n\n"
                f"🏷 النوع:\n{wtype_label}\n\n"
                f"👤 المرسل:\n{sname}\n\n"
                f"📝 الهمسة:\n{content_text}\n\n"
                f"────────────\n\n"
                f"✨ هل أعجبتك الهمسة؟\n\n"
                f"يمكنك إرسال ردك لصاحب الهمسة من الزر أدناه 💬"
            )
            _kb = InlineKeyboardMarkup(row_width=1)
            _kb.add(InlineKeyboardButton("💬 الرد على الهمسة", callback_data=f"wsp_reply:whisper:{wid}"))
            bot.send_message(user.id, msg_text, reply_markup=_kb)
        except Exception as exc:
            logger.warning("[SEND] _send_reply_invitation failed for user_id=%s whisper_id=%s: %s",
                           user.id, wid, exc)

    # ── Main flow ───────────────────────────────────────────────────
    if not _check_access():
        return
    if _handle_destructive_everyone():
        return

    if w["whisper_type"] == "first_three":
        logger.info("[READ] type=first_three whisper_id=%s", whisper_id)

    is_new_read, is_first_ever = record_read_and_check(whisper_id, user.id)

    if is_new_read:
        _answer_with_content()
    logger.log(
        logging.DEBUG if is_new_read else logging.WARNING,
        "[FLOW] is_new_read=%s is_first_ever=%s type=%s whisper_id=%s",
        is_new_read, is_first_ever, w["whisper_type"], whisper_id,
    )

    if is_new_read:
        readers = get_readers(whisper_id)
        logger.info("[DB] readers_count=%d type=%s whisper_id=%s",
                    len(readers), w["whisper_type"], whisper_id)
        logger.info("[UI] readers sent=%d readers=%s whisper_id=%s",
                    len(readers), [r.get("first_name", r.get("user_id")) for r in readers], whisper_id)
        reader_count_val = len(readers)
        _update_group_keyboard(bot, whisper_id, w, call=call)
        _send_reply_invitation(whisper_id)
        _maybe_self_destruct(is_new_read, reader_count_val)
        if w["whisper_type"] == "first_three":
            _notify_sender_first_three_read()
        if w["whisper_type"] not in ("first_one", "first_three", "everyone", "contact_whisper"):
            logger.debug("[NOTIFY] calling _notify_sender_reader_name type=%s reader_count=%d whisper_id=%s",
                         w["whisper_type"], reader_count_val, whisper_id)
            _notify_sender_reader_name(reader_count_val)
            logger.debug("[NOTIFY] _notify_sender_reader_name completed whisper_id=%s", whisper_id)
        if w["whisper_type"] == "everyone":
            _notify_sender_read()
            return
    logger.info("[READ_NOTIFY] whisper_type=%s sender_id=%s reader_id=%s is_new_read=%s",
                w["whisper_type"], w["sender_id"], user.id, is_new_read)
    if w["whisper_type"] not in ("first_three",):
        if _notify_sender_first_one(is_first_ever):
            return
        _send_read_receipt(is_new_read)


def handle_condition_answer_message(bot, msg, user_states) -> bool:
    """Process a condition answer (e.g. password) submitted by a user in
    cond_answer state. Returns True if the message was consumed.

    Extracted from cond_answer_handler so the generic message dispatcher can
    route condition answers to the condition engine before any fall-through.
    """
    user = msg.from_user
    state = user_states.get(user.id)
    if not state or state.get("action") != "cond_answer":
        return False
    if not (getattr(msg, "text", None) and not msg.text.startswith("/")):
        return False
    whisper_id = state.get("whisper_id", "")
    cond_type = state.get("condition_type", "")
    if not whisper_id or not cond_type:
        user_states.pop(user.id, None)
        return True
    w = get_whisper(whisper_id)
    if not w:
        bot.reply_to(msg, "❌ هذه الهمسة غير موجودة.")
        user_states.pop(user.id, None)
        return True
    w_dict = dict(w)
    config = _extract_condition_config(w_dict, cond_type)
    if not config:
        bot.reply_to(msg, "❌ شرط غير موجود.")
        user_states.pop(user.id, None)
        return True
    handler = condition_registry.get(cond_type)
    if not handler:
        bot.reply_to(msg, "❌ نوع الشرط غير معروف.")
        user_states.pop(user.id, None)
        return True
    result = handler.handle_interaction(
        call=None, bot=bot, whisper=w_dict,
        user_id=user.id, config=config,
        answer=msg.text,
    )
    if result.passed:
        all_results = condition_registry.check_all(w_dict, user.id)
        unmet = [r for r in all_results if not r.passed]
        if not unmet:
            user_states.pop(user.id, None)
            _complete_read_flow(
                bot, call=None, user=user,
                whisper_id=whisper_id, w=w_dict,
                is_destructive=is_destructive_whisper(w),
            )
        else:
            for r in unmet:
                if r.requires_interaction:
                    ConditionUI.render_interaction(
                        call=None, bot=bot, whisper=w_dict,
                        condition_result=r,
                        user_states=user_states,
                        user_id=user.id,
                    )
                    return True
                else:
                    bot.send_message(user.id, r.message or "❌ لم يتم استيفاء الشروط.")
                    user_states.pop(user.id, None)
                    return True
    else:
        bot.send_message(user.id, result.message)
        if not result.requires_interaction:
            user_states.pop(user.id, None)
    return True


def register_whisper_handlers(bot: telebot.TeleBot, user_states: dict):
    _register_message_handlers(bot, user_states)
    _register_callback_handlers(bot, user_states)
    _register_inline_handlers(bot, user_states)
    _register_dashboard_handlers(bot, user_states)
    _register_reply_handlers(bot, user_states)


def _register_message_handlers(bot, user_states):

    # ─── Direct media whispers from group replies ──────────────────────────
    def _handle_media_reply(msg, content_type):
        """Shared handler for media messages that are replies in groups.

        Creates a whisper from the media content, targeted at the replied-to
        user, and posts a group message with an inline keyboard.
        """
        user = msg.from_user
        if not user:
            return

        if msg.chat.type not in ("group", "supergroup"):
            return

        if not msg.reply_to_message:
            return

        target_user = msg.reply_to_message.from_user
        if not target_user:
            return

        if target_user.id == user.id:
            return

        if is_banned(user.id):
            return

        if get_setting("bot_active") != "1":
            return

        chat_id = msg.chat.id
        gs = get_group_settings(chat_id)
        if not gs.get("public_whispers_enabled", 1):
            return

        allowed, _ = check_whisper_rate_limit(user.id, chat_id)
        if not allowed:
            try:
                bot.reply_to(msg, SPAM_BLOCK_MESSAGE)
            except Exception:
                logger.exception("[SPAM] bot.reply_to failed")
            return

        ensure_user(user.id, user.username, user.first_name, user.last_name)
        ensure_user(target_user.id, target_user.username,
                     target_user.first_name, target_user.last_name)

        from services.media import extract_media_from_message
        media = extract_media_from_message(msg)

        hours = 0
        if get_setting("auto_delete_enabled") == "1":
            try:
                hours = int(get_setting("auto_delete_hours"))
            except Exception:
                logger.exception("[MEDIA_REPLY] parse auto_delete_hours failed")

        group_auto_delete_minutes = 0
        try:
            group_auto_delete_minutes = int(gs.get("auto_delete_minutes", 0))
        except Exception:
            logger.exception("[MEDIA_REPLY] parse group_auto_delete_minutes failed")

        wid = create_whisper(
            sender_id=user.id,
            content=media["content"],
            whisper_type="everyone",
            target_users=[target_user.id],
            auto_delete_hours=hours,
            group_auto_delete_minutes=group_auto_delete_minutes,
            message_type=media["message_type"],
            file_id=media["file_id"],
            caption=media["caption"],
            location_lat=media["location_lat"],
            location_lon=media["location_lon"],
        )

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
            "location": "📍 هذه همسة تحتوي على موقع",
            "animation": "🎞 هذه همسة تحتوي على متحركة",
        }.get(media["message_type"], "📎 هذه همسة تحتوي على وسائط")

        try:
            sent_msg = bot.send_message(
                chat_id, media_label, reply_markup=kb,
            )
            if sent_msg:
                update_whisper_group_message(
                    wid, chat_id=chat_id, message_id=sent_msg.message_id,
                )
        except Exception:
            logger.exception("[MEDIA_REPLY] send_message failed")

        try:
            send_dashboard(bot, user.id, wid)
        except Exception:
            logger.exception("[MEDIA_REPLY] send_dashboard failed")

        record_whisper_timestamp(user.id, chat_id)

    @bot.message_handler(content_types=["photo"])
    def _media_photo_reply(msg: telebot.types.Message):
        _handle_media_reply(msg, "photo")

    @bot.message_handler(content_types=["video"])
    def _media_video_reply(msg: telebot.types.Message):
        _handle_media_reply(msg, "video")

    @bot.message_handler(content_types=["audio"])
    def _media_audio_reply(msg: telebot.types.Message):
        _handle_media_reply(msg, "audio")

    @bot.message_handler(content_types=["voice"])
    def _media_voice_reply(msg: telebot.types.Message):
        _handle_media_reply(msg, "voice")

    @bot.message_handler(content_types=["document"])
    def _media_document_reply(msg: telebot.types.Message):
        _handle_media_reply(msg, "document")

    @bot.message_handler(content_types=["location"])
    def _media_location_reply(msg: telebot.types.Message):
        _handle_media_reply(msg, "location")

    @bot.message_handler(content_types=["animation"])
    def _media_animation_reply(msg: telebot.types.Message):
        _handle_media_reply(msg, "animation")

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
                logger.exception("[DESTRUCTIVE] parse auto_delete_hours failed")

        group_auto_delete_minutes = 0
        if msg.chat and msg.chat.type in ("group", "supergroup"):
            try:
                gs = get_group_settings(msg.chat.id)
                group_auto_delete_minutes = int(gs.get("auto_delete_minutes", 0))
            except Exception:
                logger.exception("[DESTRUCTIVE] parse group_auto_delete_minutes failed")

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

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(
            "🔒 اضغط للرؤية",
            callback_data=f"read:{wid}",
        ))
        try:
            sent_msg = bot.send_message(
                msg.chat.id,
                f"💣 همسة تدميرية للمستخدم `{target_id}`",
                parse_mode="Markdown",
                reply_markup=kb,
            )
            if sent_msg:
                update_whisper_group_message(
                    wid, chat_id=msg.chat.id, message_id=sent_msg.message_id,
                )
        except Exception:
            logger.exception("[DESTRUCTIVE] send_message failed")

        # إرسال لوحة التحكم لمُرسل الهمسة
        try:
            send_dashboard(bot, user.id, wid)
        except Exception:
            logger.exception("[DESTRUCTIVE] send_dashboard failed")

    # ─── Condition answer handler (receives password text) ────────────────
    @bot.message_handler(func=lambda m: (
        m.text and user_states.get(m.from_user.id, {}).get("action") == "cond_answer"
        and not m.text.startswith("/")
    ))
    def cond_answer_handler(msg: telebot.types.Message):
        handle_condition_answer_message(bot, msg, user_states)


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
            import os
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
                bot.answer_callback_query(
                    call.id, "🔒 الهمسة مقفلة حالياً من قِبل صاحبها.", show_alert=True
                )
            elif reason == "taken":
                if w["whisper_type"] == "first_one":
                    bot.answer_callback_query(
                        call.id, "تم فتح هذه الهمسة بواسطة أول شخص.", show_alert=True,
                    )
                elif w["whisper_type"] == "first_three":
                    bot.answer_callback_query(
                        call.id, "اكتمل عدد القراء لهذه الهمسة.", show_alert=True,
                    )
                else:
                    bot.answer_callback_query(
                        call.id, "🔒 هذه الهمسة تم فتحها بالفعل.", show_alert=True,
                    )
            elif reason == "not_target":
                add_curious(whisper_id, user.id)
                bot.answer_callback_query(
                    call.id,
                    "الهمسه ليست لك بطل فضول 😂",
                    show_alert=True,
                )
            elif reason == "not_found":
                logger.warning("[ACCESS] not_found whisper_id=%s user_id=%s", whisper_id, user.id)
                bot.answer_callback_query(
                    call.id, "❌ هذه الهمسة غير موجودة.", show_alert=True,
                )
            elif reason == "unknown":
                logger.error("[ACCESS] unknown whisper_id=%s user_id=%s wtype=%s", whisper_id, user.id, w.get("whisper_type", "?"))
                bot.answer_callback_query(
                    call.id, "❌ خطأ في التحقق من الهمسة.", show_alert=True,
                )
            else:
                bot.answer_callback_query(
                    call.id, "❌ لا يمكنك قراءة هذه الهمسة.", show_alert=True
                )
            return False

        def _handle_destructive_everyone(whisper_id: str, w: dict, is_destructive: bool) -> bool:
            if not (is_destructive and w["whisper_type"] == "everyone"):
                return False
            logger.info("[DESTROY] whisper_id=%s type=everyone is_destructive=True", whisper_id)
            is_new = add_reader_if_new(whisper_id, user.id)
            logger.info("[DESTROY] add_reader_if_new result=%s whisper_id=%s", is_new, whisper_id)
            if not is_new:
                bot.answer_callback_query(
                    call.id, "🔒 لقد قرأت هذه الهمسة التدميرية من قبل!", show_alert=True
                )
                return True
            bot.answer_callback_query(call.id, f"💣 {w['content']}", show_alert=True)
            logger.info("[DESTROY] content shown whisper_id=%s", whisper_id)
            # everyone type: NEVER modify group keyboard, NEVER lock/delete, keep in DB
            if get_setting("read_receipt_enabled") == "1":
                try:
                    bot.send_message(w["sender_id"], build_destructive_receipt_message(user, w))
                except Exception as exc:
                    logger.warning("[DESTROY] destructive receipt failed for sender_id=%s: %s",
                                   w["sender_id"], exc)
            _send_reply_invitation(whisper_id)
            logger.info("[DESTROY] complete (no group edit, no lock/delete) whisper_id=%s", whisper_id)
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
                    "animation": "🎞 متحركة",
                }.get(w_dict["message_type"], w_dict["message_type"])
                caption = w_dict.get("content") or w_dict.get("caption") or ""
                alert_text = f"🤫 {mt_label}"
                if caption:
                    alert_text += f"\n{caption}"
                bot.answer_callback_query(call.id, alert_text, show_alert=True)
            else:
                bot.answer_callback_query(call.id, f"🤫 {w['content']}", show_alert=True)



        def _maybe_self_destruct(whisper_id: str, w, is_destructive: bool, is_new_read: bool, reader_count_val: int):
            if is_destructive and is_new_read:
                wtype_str = w["whisper_type"] if isinstance(w, dict) else dict(w).get("whisper_type", "?")
                logger.info("[DESTROY] _maybe_self_destruct whisper_id=%s type=%s reader_count_val=%d",
                            whisper_id, wtype_str, reader_count_val)
                if w["whisper_type"] == "first_one":
                    logger.info("[DESTROY] first_one lock+destroy whisper_id=%s", whisper_id)
                    lock_whisper(whisper_id)
                    _destroy_whisper_message(call, bot)
                    logger.info("[DESTROY] first_one complete whisper_id=%s", whisper_id)
                elif w["whisper_type"] == "first_three" and reader_count_val >= 3:
                    logger.info("[DESTROY] first_three lock+destroy whisper_id=%s readers=%d",
                                whisper_id, reader_count_val)
                    lock_whisper(whisper_id)
                    _destroy_whisper_message(call, bot)
                    logger.info("[DESTROY] first_three complete whisper_id=%s", whisper_id)
                elif w["whisper_type"] == "everyone":
                    logger.info("[DESTROY] everyone lock+destroy whisper_id=%s", whisper_id)
                    lock_whisper(whisper_id)
                    _destroy_whisper_message(call, bot)
                    logger.info("[DESTROY] everyone complete whisper_id=%s", whisper_id)

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
                    bot.send_message(w["sender_id"], build_read_receipt_message(user, w))
                except Exception as exc:
                    logger.warning("[SEND] _send_read_receipt failed for sender_id=%s: %s",
                                   w["sender_id"], exc)

        def _send_reply_invitation(wid: str):
            """Send a private DM to the reader with a reply button."""
            try:
                w = get_whisper(wid)
                if not w:
                    return
                w = dict(w)

                sender = get_user(w["sender_id"])
                if sender:
                    first = (sender["first_name"] or "").strip()
                    last = (sender["last_name"] or "").strip()
                    sender_name = " ".join(filter(None, [first, last])).strip()
                    if not sender_name:
                        sender_name = f"@{sender['username']}" if sender.get("username") else "شخص"
                else:
                    sender_name = "شخص"

                content_text = w.get("content") or ""
                if not content_text and w.get("message_type"):
                    mt_label = {
                        "photo": "🖼 صورة",
                        "video": "🎬 فيديو",
                        "voice": "🎤 تسجيل صوتي",
                        "audio": "🎵 ملف صوتي",
                        "document": "📄 مستند",
                        "location": "📍 موقع",
                        "animation": "🎞 متحركة",
                    }.get(w["message_type"], w["message_type"])
                    caption = w.get("caption") or ""
                    content_text = mt_label
                    if caption:
                        content_text += f"\n{caption}"

                wtype_label = {
                    "first_one": "همسة لأول شخص",
                    "first_three": "همسة لأول 3 أشخاص",
                    "everyone": "همسة للجميع",
                    "contact_whisper": "همسة تواصل",
                }.get(w.get("whisper_type", ""), w.get("whisper_type", ""))

                msg_text = (
                    f"💌 همسة جديدة للرد\n\n"
                    f"🏷 النوع:\n{wtype_label}\n\n"
                    f"👤 المرسل:\n{sender_name}\n\n"
                    f"📝 الهمسة:\n{content_text}\n\n"
                    f"────────────\n\n"
                    f"✨ هل أعجبتك الهمسة؟\n\n"
                    f"يمكنك إرسال ردك لصاحب الهمسة من الزر أدناه 💬"
                )

                _kb = InlineKeyboardMarkup(row_width=1)
                _kb.add(InlineKeyboardButton(
                    "💬 الرد على الهمسة",
                    callback_data=f"wsp_reply:whisper:{wid}",
                ))
                bot.send_message(
                    user.id,
                    msg_text,
                    reply_markup=_kb,
                )
            except Exception as exc:
                logger.warning("[SEND] _send_reply_invitation failed for user_id=%s whisper_id=%s: %s",
                               user.id, wid, exc)

        def _notify_sender_first_three_read(w):
            text = build_first_three_read_notification(user, w)
            try:
                bot.send_message(w["sender_id"], text)
            except Exception as exc:
                logger.warning(
                    "[NOTIFY] _notify_sender_first_three_read failed for sender_id=%s whisper_id=%s: %s",
                    w["sender_id"], whisper_id, exc,
                )

        def _notify_sender_reader_name(w, reader_count_val):
            try:
                display = get_user_display(user)
                bot.send_message(
                    w["sender_id"],
                    f"👀 قام {display} بفتح همستك.\n\nعدد القراء: {reader_count_val}",
                )
            except Exception as exc:
                logger.warning("[NOTIFY] _notify_sender_reader_name failed for whisper_id=%s sender_id=%s: %s",
                               whisper_id, w["sender_id"], exc)

        def _notify_sender_read(w):
            w_dict = dict(w) if not isinstance(w, dict) else w
            text = build_read_receipt_message(user, w_dict)
            try:
                bot.send_message(w_dict["sender_id"], text)
            except Exception as exc:
                logger.warning("[NOTIFY] _notify_sender_read failed for sender_id=%s whisper_id=%s: %s",
                               w_dict["sender_id"], whisper_id, exc)

        def _notify_sender_public_whisper(w: dict, chat_id: int):
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
        w_dict_check = dict(w)

        is_destructive = is_destructive_whisper(w)

        # ── Admins & sender: view content without being counted ──────
        from config import ADMIN_IDS
        if user.id in ADMIN_IDS:
            w_dict_admin = dict(w)
            content_admin = w_dict_admin.get("content") or w_dict_admin.get("caption") or ""
            if w_dict_admin.get("message_type"):
                mt_label = {
                    "photo": "🖼 صورة", "video": "🎬 فيديو", "voice": "🎤 تسجيل صوتي",
                    "audio": "🎵 ملف صوتي", "document": "📄 مستند", "location": "📍 موقع",
                    "animation": "🎞 متحركة",
                }.get(w_dict_admin["message_type"], w_dict_admin["message_type"])
                alert_text = f"🤫 {mt_label}"
                if content_admin:
                    alert_text += f"\n{content_admin}"
            else:
                alert_text = f"🤫 {content_admin}" if content_admin else "🤫 (محتوى فارغ)"
            bot.answer_callback_query(call.id, alert_text, show_alert=True)
            return
        own = is_own_whisper(user.id, w)
        if own:
            _answer_with_content(w)
            return

        if not _check_access(whisper_id, w):
            return

        # ★★★ Phase 2: ConditionEngine — check_all integration ★★★
        w_dict_cond = dict(w)
        cond_results = condition_registry.check_all(w_dict_cond, user.id)
        unmet = [r for r in cond_results if not r.passed]
        if unmet:
            for result in unmet:
                if result.requires_interaction:
                    ConditionUI.render_interaction(
                        call, bot, w_dict_cond, result,
                        user_states=user_states,
                    )
                    return
                else:
                    bot.answer_callback_query(
                        call.id,
                        result.message or "❌ لم يتم استيفاء الشروط.",
                        show_alert=True,
                    )
                    return

        _complete_read_flow(bot, call, user, whisper_id, w, is_destructive)

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

    # ─── Opened (disabled button) ────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("opened:"))
    def handle_opened(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["sender_id"] == user.id:
            bot.answer_callback_query(
                call.id, "لقد قمت بفتح الهمسة بالفعل!", show_alert=True,
            )
        else:
            bot.answer_callback_query(
                call.id, "⚠️ تم فتح الهمسة بالفعل", show_alert=True,
            )

    # ─── Noop / Ignore (display-only buttons) ────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data in ("noop", "ignore"))
    def handle_noop(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        return

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
            confirm_button("نعم، احذف", f"confirm_delete:{whisper_id}"),
            cancel_button("cancel_action"),
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
            logger.exception("[DELETE_CONFIRM] edit_message_text failed")

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
            logger.exception("[CANCEL] delete_message failed")

    # ─── Cancel condition answer ──────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("cond_cancel:"))
    def handle_cond_cancel(call: telebot.types.CallbackQuery):
        user = call.from_user
        user_states.pop(user.id, None)
        bot.answer_callback_query(call.id, "❌ أُلغي طلب كلمة المرور.", show_alert=True)

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
            kb.add(cancel_button("cancel_action"))
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

    # ─── Like (❤️ الإعجاب) ──────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("like:"))
    def handle_like(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["whisper_type"] != "everyone":
            readers = get_readers(whisper_id)
            if not any(r["user_id"] == user.id for r in readers):
                bot.answer_callback_query(
                    call.id, "❌ يجب عليك فتح الهمسة أولاً لتتمكن من الإعجاب.", show_alert=True,
                )
                return
        try:
            from enterprise.db_enterprise import (
                save_favorite, remove_dislike, count_whisper_likes, has_user_disliked,
            )
            if has_user_disliked(user.id, whisper_id):
                remove_dislike(user.id, whisper_id)
            ok = save_favorite(user.id, whisper_id)
            msg = "❤️ تم إعجابك!" if ok else "❤️ أعجبت من قبل!"
            readers_updated = get_readers(whisper_id)
            kb = _build_opened_keyboard(whisper_id, readers=readers_updated)
            if kb is not None:
                try:
                    if call.inline_message_id:
                        bot.edit_message_reply_markup(
                            inline_message_id=call.inline_message_id, reply_markup=kb,
                        )
                    elif call.message:
                        bot.edit_message_reply_markup(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            reply_markup=kb,
                        )
                except Exception:
                    logger.exception("[LIKE] edit_message_reply_markup failed")
            bot.answer_callback_query(call.id, msg, show_alert=True)
        except Exception as e:
            logger.exception("[LIKE] like failed for whisper_id=%s", whisper_id)
            bot.answer_callback_query(call.id, "⚠️ حدث خطأ.", show_alert=True)

    # ─── Dislike (👎 عدم الإعجاب) ──────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("dislike:"))
    def handle_dislike(call: telebot.types.CallbackQuery):
        user = call.from_user
        whisper_id = call.data.split(":", 1)[1]
        w = get_whisper(whisper_id)
        if not w:
            bot.answer_callback_query(call.id, "❌ الهمسة غير موجودة.", show_alert=True)
            return
        if w["whisper_type"] != "everyone":
            readers = get_readers(whisper_id)
            if not any(r["user_id"] == user.id for r in readers):
                bot.answer_callback_query(
                    call.id, "❌ يجب عليك فتح الهمسة أولاً لتتمكن من التفاعل.", show_alert=True,
                )
                return
        try:
            from enterprise.db_enterprise import (
                save_dislike, remove_favorite, count_whisper_dislikes, has_user_liked,
            )
            if has_user_liked(user.id, whisper_id):
                remove_favorite(user.id, whisper_id)
            ok = save_dislike(user.id, whisper_id)
            msg = "👎 تم تسجيل عدم إعجابك!" if ok else "👎 اخترت عدم الإعجاب من قبل!"
            readers_updated = get_readers(whisper_id)
            kb = _build_opened_keyboard(whisper_id, readers=readers_updated)
            if kb is not None:
                try:
                    if call.inline_message_id:
                        bot.edit_message_reply_markup(
                            inline_message_id=call.inline_message_id, reply_markup=kb,
                        )
                    elif call.message:
                        bot.edit_message_reply_markup(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            reply_markup=kb,
                        )
                except Exception:
                    logger.exception("[DISLIKE] edit_message_reply_markup failed")
            bot.answer_callback_query(call.id, msg, show_alert=True)
        except Exception as e:
            logger.exception("[DISLIKE] dislike failed for whisper_id=%s", whisper_id)
            bot.answer_callback_query(call.id, "⚠️ حدث خطأ.", show_alert=True)


def _register_inline_handlers(bot, user_states):
    pass


def _register_dashboard_handlers(bot, user_states):
    pass


def _register_reply_handlers(bot, user_states):
    pass
