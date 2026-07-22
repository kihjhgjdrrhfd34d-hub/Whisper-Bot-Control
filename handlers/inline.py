import logging
import re
import traceback
import telebot
from telebot.types import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from database import (
    create_whisper, get_setting, upsert_user, get_group_settings,
    check_whisper_rate_limit, record_whisper_timestamp, SPAM_BLOCK_MESSAGE,
    get_whisper, get_pending_media_by_id, delete_pending_media_by_id,
    update_whisper_group_message,
)
from database.wrapped_whispers import (
    get_inline_package, delete_inline_package, delete_draft,
    update_whisper_cover_character,
    get_cover, get_character,
)
from handlers.dashboard import send_dashboard
from handlers.media_wizard import (
    FOUR_OPTIONS, DESTRUCTIVE_OPTIONS,
    build_media_whisper_inline_results, _auto_hours,
)


# ── InlineQuery subclass that preserves the chat field ─────────────────────
# pyTelegramBotAPI 4.x receives the `chat` field from Telegram in **kwargs
# but does not store it.  This subclass preserves it as self._chat via a
# custom de_json that returns InlineQueryWithChat instances.
import telebot.types as _types

class InlineQueryWithChat(_types.InlineQuery):
    @classmethod
    def de_json(cls, json_string):
        if json_string is None:
            return None
        obj = cls.check_json(json_string)
        obj['from_user'] = _types.User.de_json(obj.pop('from'))
        if 'location' in obj:
            obj['location'] = _types.Location.de_json(obj['location'])
        return cls(**obj)

    def __init__(self, id, from_user, query, offset, chat_type=None, location=None, **kwargs):
        chat_data = kwargs.pop('chat', None)
        super().__init__(id, from_user, query, offset, chat_type=chat_type, location=location, **kwargs)
        if chat_data is not None:
            try:
                self._chat = _types.Chat.de_json(chat_data) if isinstance(chat_data, (dict, str)) else chat_data
            except Exception:
                self._chat = None
        else:
            self._chat = None

_types.InlineQuery.de_json = InlineQueryWithChat.de_json
logger = logging.getLogger(__name__)

# Whisper types that receive a control-panel DM after being sent
CONTROL_PANEL_TYPES = {"custom"}


def _read_button(whisper_id: str, bot_username: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("اضغط للرؤية 🔒", callback_data=f"read:{whisper_id}"))
    return kb


# ── Wrapped whisper type definitions ─────────────────────────────────────
# (wtype, max_readers, menu_title, menu_desc)
WRAPPED_TYPE_OPTIONS = [
    ("first_one",   1, "☝️ لأول شخص",          "يقرأها أول شخص فقط"),
    ("everyone",    0, "🌍 للجميع",             "يمكن لأي شخص قراءتها"),
    ("first_three", 3, "👥 لأول 3 أشخاص",      "يقرأها أول 3 أشخاص فقط"),
    ("custom",      0, "🎯 مخصصة",             "مخصصة لشخص معين (عدّل الأهداف من لوحة التحكم)"),
]

WRAPPED_DESTRUCTIVE_OPTIONS = [
    ("first_one",   1, "💣 تدميرية لأول شخص",          "تًحذف بعد قراءتها"),
    ("first_three", 3, "💣 تدميرية لأول 3 أشخاص",      "تُحذف بعد ثالث قارئ"),
    ("everyone",    0, "💣 تدميرية للجميع",            "تظهر كتنبيه ولا تتكرر"),
]


def build_wrapped_inline_results(package, hours):
    """
    Build inline results for a wrapped whisper package.
    Creates placeholder results WITHOUT calling create_whisper().
    The actual whisper is created in handle_chosen() when user selects a type.
    Returns list of InlineQueryResultArticle.
    """
    results = []
    cover_code = package.get("cover_code", "")
    character_code = package.get("character_code", "")
    content = package.get("content", "")

    cover = get_cover(cover_code) if cover_code else None
    char = get_character(character_code) if character_code else None
    cover_icon = cover["icon"] if cover else "📜"
    cover_name = cover["name"] if cover else "أساسي"
    char_icon = char["icon"] if char else "🤫"
    char_name = char["name"] if char else "المُهمس"

    placeholder_text = (
        f"{char_icon} {char_name}\n\n"
        f"{cover_icon} {cover_name}\n\n"
        f"⏳ جاري تجهيز الهمسة..."
    )

    # Placeholder must include a reply_markup so Telegram sends inline_message_id
    # in ChosenInlineResult, allowing the bot to edit the message later.
    placeholder_kb = InlineKeyboardMarkup(row_width=1)
    placeholder_kb.add(InlineKeyboardButton("⏳ جاري التجهيز...", callback_data="ww_processing"))

    # Normal types
    for wtype, max_r, title, desc in WRAPPED_TYPE_OPTIONS:
        try:
            results.append(
                InlineQueryResultArticle(
                    id=f"ww:{wtype}:{package['id']}",
                    title=title,
                    description=desc,
                    input_message_content=InputTextMessageContent(
                        message_text=placeholder_text,
                    ),
                    reply_markup=placeholder_kb,
                )
            )
        except Exception as e:
            logger.error(f"wrapped inline build [{wtype}]: {e}")

    # Destructive types
    for wtype, max_r, title, desc in WRAPPED_DESTRUCTIVE_OPTIONS:
        try:
            results.append(
                InlineQueryResultArticle(
                    id=f"ww:destructive:{wtype}:{package['id']}",
                    title=title,
                    description=desc,
                    input_message_content=InputTextMessageContent(
                        message_text=placeholder_text,
                    ),
                    reply_markup=placeholder_kb,
                )
            )
        except Exception as e:
            logger.error(f"wrapped destructive inline build [{wtype}]: {e}")

    return results


def register_inline_handlers(bot: telebot.TeleBot):
    try:
        bot_username = bot.get_me().username
    except Exception:
        bot_username = ""

    # ── Inline query handler ─────────────────────────────────────────────────
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

        # ── Media whisper "m:" prefix — create whispers from pending ──────
        if raw.startswith("m:"):
            _pending_id_str = raw[2:].strip()
            if _pending_id_str:
                try:
                    _pending = get_pending_media_by_id(int(_pending_id_str))
                except (ValueError, TypeError):
                    _pending = None
                if _pending and _pending["user_id"] == user.id:
                    hours = _auto_hours()
                    results = build_media_whisper_inline_results(
                        _pending, bot_username, hours,
                    )
                    if results:
                        try:
                            bot.answer_inline_query(
                                query.id, results, cache_time=0, is_personal=True,
                            )
                        except Exception as e:
                            logger.error(f"answer_inline_query (m: media): {e}")
                        try:
                            delete_pending_media_by_id(_pending["id"])
                        except Exception:
                            pass
                        return

        # ── Wrapped whisper "ww:" prefix — create placeholder results ────
        if raw.startswith("ww:"):
            _pkg_id = raw[3:].strip()
            if _pkg_id:
                _pkg = get_inline_package(_pkg_id)
                if _pkg and _pkg["user_id"] == user.id:
                    hours = _auto_hours()
                    results = build_wrapped_inline_results(_pkg, hours)
                    if results:
                        try:
                            bot.answer_inline_query(
                                query.id, results, cache_time=0, is_personal=True,
                            )
                        except Exception as e:
                            logger.error(f"answer_inline_query (ww:): {e}")
                        return
                else:
                    try:
                        bot.answer_inline_query(
                            query.id,
                            [InlineQueryResultArticle(
                                id="ww:error:expired",
                                title="❌ الهمسة غير متاحة",
                                description="انتهت صلاحيتها أو تم استخدامها بالفعل.",
                                input_message_content=InputTextMessageContent(
                                    message_text="❌ انتهت صلاحية الهمسة أو تم استخدامها بالفعل."
                                ),
                            )],
                            cache_time=0,
                            is_personal=True,
                        )
                    except Exception as e:
                        logger.error(f"answer_inline_query (ww: error): {e}")
                    return

        # ── Empty query: show placeholder ─────────────────────────────────
        if not raw:
            username = user.first_name or (f"@{user.username}" if user.username else "مستخدم")
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

        # ── Check public whispers setting for group chats ─────────────────
        chat_public_allowed = True
        group_auto_delete_minutes = 0
        chat_id_for_spam = None
        if hasattr(query, '_chat') and query._chat and query._chat.id:
            chat_id_for_spam = query._chat.id
            try:
                gs = get_group_settings(query._chat.id)
                chat_public_allowed = bool(gs.get("public_whispers_enabled", 1))
                group_auto_delete_minutes = int(gs.get("auto_delete_minutes", 0))
            except Exception:
                pass

        # ── Whisper rate-limit check (per-user per-group) ────────────────
        if chat_id_for_spam is not None:
            allowed, _count = check_whisper_rate_limit(user.id, chat_id_for_spam)
            if not allowed:
                try:
                    bot.answer_inline_query(
                        query.id,
                        [InlineQueryResultArticle(
                            id="error:rate_limit",
                            title="⏳ تم تجاوز الحد المسموح",
                            description=SPAM_BLOCK_MESSAGE,
                            input_message_content=InputTextMessageContent(
                                message_text=SPAM_BLOCK_MESSAGE,
                            ),
                        )],
                        cache_time=0,
                        is_personal=True,
                    )
                except Exception as e:
                    logger.error(f"answer_inline_query (rate_limit): {e}")
                return

        # ── Auto-detect `@user text` or `ID text` pattern → custom whisper ──
        _TARGET_RE = re.compile(r'^(@\w+|[1-9]\d{4,})\s+([\s\S]+)$')
        m = _TARGET_RE.match(raw)
        if m:
            raw_target = m.group(1)
            whisper_body = m.group(2).strip()
            t = raw_target.lstrip("@")
            parsed_target = int(t) if t.isdigit() else t
            display_target = raw_target

            try:
                wid_targeted = create_whisper(
                    sender_id=user.id,
                    content=whisper_body,
                    whisper_type="custom",
                    target_users=[parsed_target],
                    max_readers=0,
                    auto_delete_hours=hours,
                    group_auto_delete_minutes=group_auto_delete_minutes,
                )
                if chat_id_for_spam is not None:
                    record_whisper_timestamp(user.id, chat_id_for_spam)
                snippet = (
                    whisper_body[:40] + "..."
                    if len(whisper_body) > 40
                    else whisper_body
                )
                group_msg_targeted = f"هذه همسة سرية لـ {display_target} 🤫"
                targeted_kb = _read_button(wid_targeted, bot_username)
                results.append(
                    InlineQueryResultArticle(
                        id=f"custom:{wid_targeted}",
                        title=f"همسة سرية لـ {display_target} فقط...",
                        description=snippet,
                        input_message_content=InputTextMessageContent(
                            message_text=group_msg_targeted,
                        ),
                        reply_markup=targeted_kb,
                    )
                )
            except Exception as e:
                logger.error(f"inline targeted build: {e}")

        # ── Four standard options ─────────────────────────────────────────
        for wtype, max_r, title, desc, group_text in FOUR_OPTIONS:
            if wtype == "everyone" and not chat_public_allowed:
                continue
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
                            if t.isdigit() or (len(t) > 3 and " " not in t):
                                targets.append(int(t) if t.isdigit() else t)
                                body = parts[1] if len(parts) > 1 else content
                    if targets:
                        first = targets[0]
                        target_label = (
                            f"@{first}" if isinstance(first, str) else str(first)
                        )

                wid = create_whisper(
                    sender_id=user.id,
                    content=body,
                    whisper_type=wtype,
                    target_users=targets,
                    max_readers=max_r,
                    auto_delete_hours=hours,
                    group_auto_delete_minutes=group_auto_delete_minutes,
                )
                if chat_id_for_spam is not None:
                    record_whisper_timestamp(user.id, chat_id_for_spam)

                if wtype == "custom" and target_label:
                    btn_kb = InlineKeyboardMarkup(row_width=1)
                    btn_kb.add(InlineKeyboardButton(
                        f"همسة لـ {target_label} 🔒",
                        url=f"tg://resolve?domain={bot_username}&start=view_{wid}",
                    ))
                else:
                    btn_kb = _read_button(wid, bot_username)

                results.append(
                    InlineQueryResultArticle(
                        id=f"{wtype}:{wid}",
                        title=title,
                        description=(
                            f"لـ {target_label}"
                            if (wtype == "custom" and target_label)
                            else desc
                        ),
                        input_message_content=InputTextMessageContent(
                            message_text=group_text,
                        ),
                        reply_markup=btn_kb,
                    )
                )
            except Exception as e:
                logger.error(f"inline build [{wtype}]: {e}")

        # ── Error result when public whispers are disabled ────────────────
        if not chat_public_allowed and raw:
            results.append(
                InlineQueryResultArticle(
                    id="error:public_disabled",
                    title="❌ الهمسات العامة معطلة",
                    description="غير متاحة في هذه المجموعة",
                    input_message_content=InputTextMessageContent(
                        message_text="❌ الهمسات العامة معطلة في هذه المجموعة"
                    ),
                )
            )

        # ── Destructive options ───────────────────────────────────────────
        for wtype, max_r, title, desc, group_text in DESTRUCTIVE_OPTIONS:
            if wtype == "everyone" and not chat_public_allowed:
                continue
            try:
                wid = create_whisper(
                    sender_id=user.id,
                    content=content,
                    whisper_type=wtype,
                    target_users=[],
                    max_readers=max_r,
                    auto_delete_hours=hours,
                    is_destructive=True,
                    group_auto_delete_minutes=group_auto_delete_minutes,
                )
                if chat_id_for_spam is not None:
                    record_whisper_timestamp(user.id, chat_id_for_spam)
                btn_kb = _read_button(wid, bot_username)
                results.append(
                    InlineQueryResultArticle(
                        id=f"destructive:{wtype}:{wid}",
                        title=title,
                        description=desc,
                        input_message_content=InputTextMessageContent(
                            message_text=group_text,
                        ),
                        reply_markup=btn_kb,
                    )
                )
            except Exception as e:
                logger.error(f"inline destructive build [{wtype}]: {e}")

        try:
            bot.answer_inline_query(query.id, results, cache_time=0, is_personal=True)
        except Exception as e:
            logger.error(f"answer_inline_query (typed): {e}")

    # ── Chosen inline result handler ─────────────────────────────────────────
    @bot.chosen_inline_handler(func=lambda r: True)
    def handle_chosen(result: telebot.types.ChosenInlineResult):
        user = result.from_user
        result_id = result.result_id

        if ":" not in result_id:
            return

        if result_id.startswith("error:"):
            return

        # ── Wrapped whisper: create whisper now ──────────────────────────
        if result_id.startswith("ww:"):
            _handle_wrapped_chosen(bot, result, _auto_hours())
            return

        # ── Detect destructive prefix ────────────────────────────────────
        if result_id.startswith("destructive:"):
            _, wtype, wid = result_id.split(":", 2)
        else:
            wtype, wid = result_id.split(":", 1)

        # ── Store inline_message_id for group button editing ────────────
        if result.inline_message_id:
            try:
                update_whisper_group_message(wid, inline_message_id=result.inline_message_id)
                logger.info("[INLINE] stored inline_message_id for wid=%s", wid)
            except Exception as exc:
                logger.warning("[INLINE] failed to store inline_message_id for wid=%s: %s", wid, exc)

        # ── Old control panel (custom whispers only, backward compat) ─────
        if wtype in CONTROL_PANEL_TYPES:

            TYPE_LABELS = {
                "custom": "همسة مخصصة 🎯",
            }
            label = TYPE_LABELS.get(wtype, "همسة سرية")

            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("👁️ عرض",        callback_data=f"read:{wid}"),
                InlineKeyboardButton("🔒 قفل",         callback_data=f"lock:{wid}"),
            )
            kb.add(
                InlineKeyboardButton("✏️ تعديل",       callback_data=f"edit:{wid}"),
                InlineKeyboardButton("🗑️ حذف",         callback_data=f"delete:{wid}"),
            )
            kb.add(
                InlineKeyboardButton("🧹 مسح المهموس", callback_data=f"clear:{wid}"),
                InlineKeyboardButton("👀 الفضوليون",   callback_data=f"curious:{wid}"),
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
                logger.info(f"Control panel sent for whisper: {wid}")
            except Exception as e:
                logger.error(f"chosen_inline DM error: {e}")

        # ── إرسال لوحة التحكم الجديدة لجميع أنواع الهمسات ─────────────────
        try:
            send_dashboard(bot, user.id, wid)
            logger.info(f"Dashboard sent for whisper: {wid}")
        except Exception as e:
            logger.error(f"dashboard DM error: {e}")


def _handle_wrapped_chosen(bot, result, hours):
    """
    Handle chosen inline result for wrapped whispers.
    Creates the actual whisper and edits the placeholder message.
    """
    user = result.from_user
    result_id = result.result_id

    # Log inline_message_id existence early for debugging
    imid = result.inline_message_id
    if imid:
        logger.info("[WW] inline_message_id PRESENT: %s", imid)
    else:
        logger.warning("[WW] inline_message_id is MISSING (None) — "
                       "Telegram will not send inline_message_id unless the placeholder "
                       "InlineQueryResultArticle has a reply_markup attached. "
                       "Check build_wrapped_inline_results() for reply_markup.")

    # Parse: "ww:{wtype}:{package_id}" or "ww:destructive:{wtype}:{package_id}"
    parts = result_id.split(":")
    if len(parts) == 3:
        # Normal: ww:{wtype}:{package_id}
        _, wtype, pkg_id = parts
        is_destructive = False
    elif len(parts) == 4:
        # Destructive: ww:destructive:{wtype}:{package_id}
        _, _, wtype, pkg_id = parts
        is_destructive = True
    else:
        logger.warning("[WW] invalid result_id format: %s", result_id)
        return

    package = get_inline_package(pkg_id)
    if not package:
        logger.warning("[WW] package not found: %s", pkg_id)
        return

    content = package.get("content", "")
    cover_code = package.get("cover_code", "")
    character_code = package.get("character_code", "")

    max_readers_map = {"first_one": 1, "everyone": 0, "first_three": 3, "custom": 0}
    max_r = max_readers_map.get(wtype, 0)

    try:
        wid = create_whisper(
            sender_id=user.id,
            content=content,
            whisper_type=wtype,
            target_users=[],
            max_readers=max_r,
            auto_delete_hours=hours,
            is_destructive=is_destructive,
        )
    except Exception as exc:
        logger.error("[WW] create_whisper failed: %s", exc)
        return

    if cover_code or character_code:
        try:
            update_whisper_cover_character(wid, cover_code, character_code)
        except Exception as exc:
            logger.warning("[WW] update_whisper_cover_character failed: %s", exc)

    cover = get_cover(cover_code) if cover_code else None
    char = get_character(character_code) if character_code else None
    cover_icon = cover["icon"] if cover else "📜"
    cover_name = cover["name"] if cover else "أساسي"
    char_icon = char["icon"] if char else "🤫"
    char_name = char["name"] if char else "المُهمس"

    final_text = (
        f"{char_icon} {char_name}\n\n"
        f"{cover_icon} {cover_name}\n\n"
        f"🔒 اضغط للرؤية"
    )

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔒 اضغط للرؤية", callback_data=f"read:{wid}"))

    logger.info("[WW] final_text prepared: wid=%s cover=%s char=%s text='%s'",
                wid, cover_code, character_code, final_text.replace('\n', ' | '))
    logger.info("[WW] keyboard contains: callback_data=read:%s", wid)

    imid = result.inline_message_id
    if imid:
        logger.info("[WW] editing inline message: inline_message_id=%s wid=%s", imid, wid)
        try:
            bot.edit_message_text(
                final_text,
                inline_message_id=imid,
                reply_markup=kb,
            )
            logger.info("[WW] edit inline message SUCCEEDED wid=%s — placeholder replaced", wid)
        except Exception as exc:
            logger.warning("[WW] edit inline message FAILED wid=%s: %s", wid, exc)
            traceback.print_exc()
    else:
        logger.warning(
            "[WW] inline_message_id is None — cannot edit placeholder message. "
            "wid=%s result_id=%s user=%s. "
            "This means the placeholder message was sent WITHOUT a reply_markup, "
            "or Telegram did not provide inline_message_id in the ChosenInlineResult.",
            wid, result_id, user.id,
        )

    if imid:
        try:
            update_whisper_group_message(wid, inline_message_id=imid)
        except Exception as exc:
            logger.warning("[WW] store inline_message_id failed: %s", exc)

    try:
        send_dashboard(bot, user.id, wid)
    except Exception as exc:
        logger.warning("[WW] send_dashboard failed: %s", exc)

    delete_inline_package(pkg_id)
    try:
        delete_draft(user.id)
    except Exception:
        pass
    logger.info("[WW] whisper created wid=%s wtype=%s destructive=%s pkg=%s", wid, wtype, is_destructive, pkg_id)
