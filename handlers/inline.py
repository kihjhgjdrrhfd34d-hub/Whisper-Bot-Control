import logging
import re
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
)
from handlers.dashboard import send_dashboard
from handlers.media_wizard import (
    FOUR_OPTIONS, DESTRUCTIVE_OPTIONS,
    build_media_whisper_inline_results, _auto_hours,
)


# ── Monkey-patch InlineQuery to expose chat info ────────────────────────────
# pyTelegramBotAPI 4.21.0 receives the `chat` field from Telegram in **kwargs
# but does not store it.  We patch __init__ to preserve it as self._chat.
import telebot.types as _types
_orig_inline_init = _types.InlineQuery.__init__
def _inline_init_with_chat(self, id, from_user, query, offset, chat_type=None, location=None, **kwargs):
    _orig_inline_init(self, id, from_user, query, offset, chat_type=chat_type, location=location)
    chat_data = kwargs.get('chat')
    if chat_data is not None:
        try:
            from telebot.types import Chat
            self._chat = Chat.de_json(chat_data) if isinstance(chat_data, (dict, str)) else chat_data
        except Exception:
            self._chat = None
    else:
        self._chat = None
_types.InlineQuery.__init__ = _inline_init_with_chat

logger = logging.getLogger(__name__)

# Whisper types that receive a control-panel DM after being sent
CONTROL_PANEL_TYPES = {"custom"}


def _read_button(whisper_id: str, bot_username: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    view_url = f"tg://resolve?domain={bot_username}&start=view_{whisper_id}"
    kb.add(InlineKeyboardButton("🔒 اضغط للرؤية", url=view_url))
    return kb


def register_inline_handlers(bot: telebot.TeleBot):

    # ── Inline query handler ─────────────────────────────────────────────────
    @bot.inline_handler(func=lambda query: True)
    def handle_inline(query: telebot.types.InlineQuery):
        user = query.from_user
        try:
            upsert_user(user.id, user.username, user.first_name, user.last_name)
        except Exception as e:
            logger.warning(f"upsert_user: {e}")

        try:
            bot_username = bot.get_me().username
        except Exception:
            bot_username = bot.token.split(":")[0]  # fallback

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
        """
        Send the whisper control panel to the sender ONLY for custom whispers.
        Public / first_one / first_three whispers do NOT get a control panel.
        """
        user = result.from_user
        result_id = result.result_id

        if ":" not in result_id:
            return

        # Skip non-whisper results (e.g. error messages)
        if result_id.startswith("error:"):
            return

        # Detect destructive prefix
        if result_id.startswith("destructive:"):
            _, wtype, wid = result_id.split(":", 2)
        else:
            wtype, wid = result_id.split(":", 1)

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
