import logging
import re
import telebot
from telebot.types import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from database import create_whisper, get_setting, upsert_user, get_group_settings
from handlers.dashboard import send_dashboard


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

# ── Whisper type definitions ──────────────────────────────────────────────────
# (wtype, max_readers, menu_title, menu_desc, group_message_text)
FOUR_OPTIONS = [
    ("first_one",   1, "همسة لأول شخص",          "🔒 يقرأها أول شخص فقط",      "هذه همسه سريه لاول شخص يقوم بقرأتها"),
    ("everyone",    0, "همسة للجميع",             "🌍 يمكن لأي شخص قراءتها",    "هذه الهمسه للجميع"),
    ("first_three", 3, "همسة لأول ثلاثة أشخاص",  "👥 يقرأها أول 3 أشخاص فقط", "هذه همسه سريه لاول ثلاثة أشخاص يقومون بقرأتها"),
    ("custom",      0, "همسة بالأيدي او اليوزر",  "🎯 مخصصة لشخص معين",         "هذه همسه سريه مخصصة"),
]

# Whisper types that receive a control-panel DM after being sent
CONTROL_PANEL_TYPES = {"custom"}

# ── Destructive (self-destructing) variants ────────────────────────────────────
DESTRUCTIVE_OPTIONS = [
    ("first_one",   1, "💣 همسة تدميرية لشخص",          "💥 تُحذف بعد قراءتها",      "💣 همسة تدميرية لشخص واحد"),
    ("first_three", 3, "💣 همسة تدميرية لـ 3 أشخاص",    "💥 تُحذف بعد ثالث قارئ",    "💣 همسة تدميرية لـ 3 أشخاص"),
    ("everyone",    0, "💣 همسة تدميرية للجميع",        "💥 تظهر كتنبيه ولا تتكرر",  "💣 همسة تدميرية للجميع"),
]


def _read_button(whisper_id: str, bot_username: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
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

        # ── Empty query: show placeholder ─────────────────────────────────
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

        # ── Check public whispers setting for group chats ─────────────────
        chat_public_allowed = True
        group_auto_delete_minutes = 0
        if hasattr(query, '_chat') and query._chat and query._chat.id:
            try:
                gs = get_group_settings(query._chat.id)
                chat_public_allowed = bool(gs.get("public_whispers_enabled", 1))
                group_auto_delete_minutes = int(gs.get("auto_delete_minutes", 0))
            except Exception:
                pass

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

                if wtype == "custom" and target_label:
                    btn_kb = InlineKeyboardMarkup(row_width=1)
                    btn_kb.add(InlineKeyboardButton(
                        f"همسة لـ {target_label} 🔒", callback_data=f"read:{wid}"
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
