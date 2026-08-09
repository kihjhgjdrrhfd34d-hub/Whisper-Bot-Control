"""
tests/test_variant_wizard_flow.py — Stage 2: variant whisper creation wizard.

Driven through the REAL registered handlers (bot.py + handlers/variant_whisper.py),
mirroring tests/test_question_condition_flow.py.

Covers:
  1. The main menu exposes the "🧬 همسة متغيرة" button (vwhisper_start).
  2. Starting the wizard sets user_states and asks for the first variant.
  3. Variants are collected (min 2, max 5).
  4. Empty / media / command texts are rejected (except /cancel).
  5. /cancel and the cancel button clear the state safely.
  6. "✅ تم" (>= 2 variants) creates a *draft only*:
       category="variant", content=first variant,
       conditions_data={"variants": [...]} — no whisper, no inline button.
  7. The variant draft coexists with conditional (cw) drafts without collision.
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_variant_wizard_flow.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.envelope import (
    init_envelope_db, get_draft, get_pending_draft, delete_draft,
)
from database.whisper_conditions import init_whisper_conditions_db
from handlers.variant_whisper import ACTION, MIN_VARIANTS, MAX_VARIANTS

import bot as botmod
bot = botmod.bot
user_states = botmod.user_states

SENDER_ID = 30001
GROUP_ID  = -10030001


def _dispatch_message(msg):
    """First-matching message handler wins — mirrors telebot's break-on-match."""
    from telebot import ContinueHandling
    for handler in bot.message_handlers:
        if bot._test_message_handler(handler, msg):
            result = handler['function'](msg)
            if not isinstance(result, ContinueHandling):
                return handler['function']
    return None


def _dispatch_callback(call):
    for handler in bot.callback_query_handlers:
        if bot._test_message_handler(handler, call):
            handler['function'](call)
            return handler['function']
    return None


def _make_user(user_id, username, first_name):
    u = MagicMock()
    u.id = user_id
    u.username = username
    u.first_name = first_name
    u.last_name = None
    return u


def _make_message(user_id, text, chat_type="private"):
    msg = MagicMock()
    msg.content_type = "text"
    msg.text = text
    msg.caption = None
    msg.from_user = _make_user(user_id, f"user{user_id}", f"User{user_id}")
    msg.chat = MagicMock()
    msg.chat.id = user_id if chat_type == "private" else GROUP_ID
    msg.chat.type = chat_type
    msg.message_id = 5000 + user_id
    msg.reply_to_message = None
    return msg


def _make_media_message(user_id, content_type="photo"):
    msg = MagicMock()
    msg.content_type = content_type
    msg.text = None
    msg.caption = None
    msg.from_user = _make_user(user_id, f"user{user_id}", f"User{user_id}")
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = "private"
    msg.message_id = 5000 + user_id
    msg.reply_to_message = None
    return msg


def _make_callback(user_id, data):
    call = MagicMock()
    call.id = f"cb_{data}"
    call.data = data
    call.from_user = _make_user(user_id, f"user{user_id}", f"User{user_id}")
    call.message = MagicMock()
    call.message.chat.id = user_id
    call.message.message_id = 6000 + user_id
    call.inline_message_id = None
    return call


def _texts_sent():
    return [c.args[1] for c in bot.send_message.call_args_list
            if len(c.args) >= 2 and isinstance(c.args[1], str)]


def _alerts_shown():
    return [c.args[1] for c in bot.answer_callback_query.call_args_list
            if len(c.args) >= 2 and isinstance(c.args[1], str)]


def _has_switch_inline_button():
    for c in bot.send_message.call_args_list:
        kb = (c.kwargs or {}).get("reply_markup")
        if not kb or not hasattr(kb, "keyboard"):
            continue
        for row in kb.keyboard:
            for btn in row:
                if getattr(btn, "switch_inline_query", None):
                    return True
    return False


def _whisper_count(sender_id):
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (sender_id,)
        ).fetchone()[0]


class TestVariantWizardFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_envelope_db()
        init_whisper_conditions_db()
        bot.send_message = MagicMock(return_value=MagicMock(message_id=1, chat=None))
        bot.answer_callback_query = MagicMock()
        bot.reply_to = MagicMock(return_value=MagicMock(message_id=2, chat=None))
        bot.edit_message_reply_markup = MagicMock()
        bot.delete_message = MagicMock()
        bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
        bot.get_chat_member = MagicMock(return_value=MagicMock(status="member"))
        if not any(getattr(h['function'], '__name__', '') == 'vwhisper_start'
                   for h in bot.callback_query_handlers):
            botmod.register_all_handlers()

    def setUp(self):
        user_states.clear()
        delete_draft(SENDER_ID)
        db.delete_pending_media(SENDER_ID)
        bot.send_message.reset_mock()
        bot.answer_callback_query.reset_mock()
        bot.reply_to.reset_mock()
        bot.edit_message_reply_markup.reset_mock()
        bot.delete_message.reset_mock()
        db.upsert_user(SENDER_ID, "sender", "Sender", None)

    # ── Menu / entry point ────────────────────────────────────────────────
    def test_main_menu_has_variant_button(self):
        user = _make_user(SENDER_ID, "sender", "Sender")
        _text, kb = botmod._main_menu_text_and_kb(bot, user)
        buttons = [(b.text, b.callback_data)
                   for row in kb.keyboard for b in row]
        self.assertIn(("🧬 همسة متغيرة", "vwhisper_start"), buttons)

    def test_start_wizard_sets_state_and_asks_first_variant(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        self.assertEqual(user_states.get(SENDER_ID),
                         {"action": ACTION, "variants": []})
        self.assertTrue(any("النسخة الأولى" in t for t in _texts_sent()))

    # ── Collection: min / max ─────────────────────────────────────────────
    def test_first_variant_stored_then_second_prompt(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_message(SENDER_ID, "نسخة أولى"))
        state = user_states.get(SENDER_ID)
        self.assertEqual(state["variants"], ["نسخة أولى"])
        self.assertTrue(any("النسخة الثانية" in t for t in _texts_sent()))

    def test_done_with_one_variant_rejected(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_message(SENDER_ID, "نسخة وحيدة"))
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_done"))
        self.assertTrue(any("نسختين على الأقل" in a for a in _alerts_shown()))
        self.assertIsNotNone(user_states.get(SENDER_ID), "state must persist")
        self.assertIsNone(get_draft(SENDER_ID), "no draft before min variants")

    def test_two_variants_then_done_creates_draft_only(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_message(SENDER_ID, "نسخة أولى"))
        _dispatch_message(_make_message(SENDER_ID, "نسخة ثانية"))
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_done"))

        self.assertIsNone(user_states.get(SENDER_ID), "state cleared after save")
        draft = get_draft(SENDER_ID)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["category"], "variant")
        self.assertEqual(draft["content"], "نسخة أولى")
        self.assertEqual(draft["status"], "pending")
        conds = json.loads(draft["conditions_data"])
        self.assertEqual(conds, {"variants": ["نسخة أولى", "نسخة ثانية"]})

        pending = get_pending_draft(SENDER_ID)
        self.assertEqual(pending["id"], draft["id"])
        self.assertEqual(_whisper_count(SENDER_ID), 0, "draft only — no whisper")
        self.assertFalse(_has_switch_inline_button(),
                         "no inline share button in stage 2")

    def test_five_variants_auto_finalize(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        for i in range(1, MAX_VARIANTS + 1):
            _dispatch_message(_make_message(SENDER_ID, f"نسخة {i}"))
        self.assertIsNone(user_states.get(SENDER_ID), "auto-finalize at max")
        draft = get_draft(SENDER_ID)
        self.assertIsNotNone(draft)
        conds = json.loads(draft["conditions_data"])
        self.assertEqual(len(conds["variants"]), MAX_VARIANTS)

    def test_sixth_variant_rejected_when_state_manually_over_max(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        user_states[SENDER_ID] = {
            "action": ACTION,
            "variants": ["أ", "ب", "ج", "د", "هـ"],
        }
        before = user_states[SENDER_ID]
        _dispatch_message(_make_message(SENDER_ID, "نسخة سادسة"))
        self.assertTrue(any("الحد الأقصى" in t for t in _texts_sent()))
        self.assertEqual(user_states.get(SENDER_ID), before,
                         "over-max message must be rejected without changes")

    # ── Rejections ────────────────────────────────────────────────────────
    def test_empty_variant_rejected(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_message(SENDER_ID, "   "))
        self.assertEqual(user_states.get(SENDER_ID)["variants"], [])
        self.assertTrue(any("غير فارغة" in t for t in _texts_sent()))

    def test_media_message_rejected_and_not_stored(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_media_message(SENDER_ID, "photo"))
        self.assertEqual(user_states.get(SENDER_ID)["variants"], [],
                         "media must not be collected as a variant")
        self.assertTrue(any("نصية فقط" in t for t in _texts_sent()))
        self.assertFalse(any("تم استلام" in t for t in _texts_sent()),
                         "media wizard must not hijack the message")
        self.assertIsNone(db.get_pending_media(SENDER_ID),
                          "no pending media row must be created")

    def test_other_command_rejected_but_state_kept(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_message(SENDER_ID, "/foo"))
        self.assertEqual(user_states.get(SENDER_ID)["variants"], [])
        self.assertTrue(any("ليست نسخة صالحة" in t for t in _texts_sent()))
        self.assertIsNotNone(user_states.get(SENDER_ID))

    # ── Cancel ────────────────────────────────────────────────────────────
    def test_cancel_command_clears_state(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_message(SENDER_ID, "نسخة أولى"))
        _dispatch_message(_make_message(SENDER_ID, "/cancel"))
        self.assertIsNone(user_states.get(SENDER_ID))
        self.assertTrue(any("أُلغي" in t for t in _texts_sent()))
        self.assertIsNone(get_draft(SENDER_ID), "no draft after cancel")

    def test_cancel_button_clears_state(self):
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_cancel"))
        self.assertIsNone(user_states.get(SENDER_ID))
        self.assertTrue(any("أُلغي" in a for a in _alerts_shown()))
        bot.delete_message.assert_called()

    # ── Isolation from other systems ──────────────────────────────────────
    def test_private_chat_only(self):
        from handlers.variant_whisper import handle_variant_whisper_message
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        group_msg = _make_message(SENDER_ID, "نسخة", chat_type="group")
        self.assertFalse(
            handle_variant_whisper_message(bot, group_msg, user_states),
            "wizard must not consume group messages",
        )

    def test_variant_state_ignored_by_conditional_handler(self):
        from handlers.conditional_whisper import handle_conditional_whisper_message
        user_states[SENDER_ID] = {"action": ACTION, "variants": ["أ"]}
        msg = _make_message(SENDER_ID, "محتوى مشروط")
        self.assertFalse(handle_conditional_whisper_message(bot, msg, user_states))
        self.assertEqual(user_states.get(SENDER_ID)["variants"], ["أ"])

    def test_conditional_state_ignored_by_variant_handler(self):
        from handlers.variant_whisper import handle_variant_whisper_message
        user_states[SENDER_ID] = {"action": "cw_awaiting_content", "conditions_data": {}}
        msg = _make_message(SENDER_ID, "نص")
        self.assertFalse(handle_variant_whisper_message(bot, msg, user_states))

    def test_variant_draft_coexists_with_conditional_draft(self):
        # Variant draft first.
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_start"))
        _dispatch_message(_make_message(SENDER_ID, "نسخة أولى"))
        _dispatch_message(_make_message(SENDER_ID, "نسخة ثانية"))
        _dispatch_callback(_make_callback(SENDER_ID, "vwhisper_done"))
        variant_draft = get_draft(SENDER_ID)
        self.assertEqual(variant_draft["category"], "variant")

        # Conditional draft afterwards — must create a separate latest row.
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:password"))
        _dispatch_message(_make_message(SENDER_ID, "secret"))
        _dispatch_message(_make_message(SENDER_ID, "secret"))
        _dispatch_message(_make_message(SENDER_ID, "محتوى بكلمة مرور"))
        self.assertIsNone(user_states.get(SENDER_ID))

        latest = get_draft(SENDER_ID)
        self.assertEqual(latest["content"], "محتوى بكلمة مرور")
        self.assertNotEqual(latest["id"], variant_draft["id"],
                            "latest-wins must target the conditional draft")
        import database.envelope as _envelope
        with _envelope.get_conn() as conn:
            rows = conn.execute(
                "SELECT category FROM whisper_drafts WHERE user_id=?",
                (SENDER_ID,),
            ).fetchall()
        categories = [r["category"] for r in rows]
        self.assertIn("variant", categories)
        self.assertIn("", categories)
        self.assertEqual(_whisper_count(SENDER_ID), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
