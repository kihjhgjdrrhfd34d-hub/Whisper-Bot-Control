"""
tests/test_variant_dashboard_flow.py — Stage 4: variant whisper dashboard.

Drives the REAL handlers/dashboard.py + handlers/whisper.py registration
over a real SQLite DB:

  - Dashboard text/keyboard: variant label + conditional "عرض النسخ" button.
  - dash_variants handler: sender / admin / unauthorized / non-variant.
  - Variant preview text: numbered list, safe truncation (never in
    callback_data).
  - dash_resend variant branch: rebuilds a variant draft (category=variant,
    full conditions_data) and replies with switch_inline_query="v:<draft_id>",
    WITHOUT creating a duplicate whisper; normal resend unchanged.
  - handle_edit guard: editing a variant whisper is refused in v1; normal
    whispers still enter the edit flow.

Covers (17):
  1.  _is_variant_whisper True for a valid variant whisper.
  2.  _is_variant_whisper False for normal / <2 variants / bad JSON.
  3.  _variant_count counts non-blank variants only.
  4.  _build_variants_preview_text numbered list.
  5.  _build_variants_preview_text truncates an over-long variant (300).
  6.  _build_variants_preview_text truncates an over-long total (3900).
  7.  _build_dashboard_text shows the variant label with count.
  8.  _build_dashboard_text hides the label for normal whispers.
  9.  dashboard_keyboard adds "عرض النسخ" for variants.
  10. dashboard_keyboard omits it for normal whispers.
  11. dash_variants: sender can view (edit called, callback_data has no variants).
  12. dash_variants: admin can view.
  13. dash_variants: unauthorized user refused with alert.
  14. dash_variants: non-variant whisper refused.
  15. dash_variants: missing whisper refused.
  16. dash_resend: variant → draft rebuilt, switch_inline_query="v:<id>",
      no new whisper row created.
  17. dash_resend: single-variant whisper is not variant → normal resend.
  18. dash_resend: normal whisper still creates a fresh duplicate.
  19. handle_edit: variant whisper refused, state NOT set.
  20. handle_edit: normal whisper enters edit flow (state set).
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_variant_dashboard_flow.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.envelope import init_envelope_db, get_draft, delete_draft
from handlers.dashboard import (
    _is_variant_whisper, _variant_count, _build_variants_preview_text,
    _build_dashboard_text, dashboard_keyboard,
    _MAX_PREVIEW_TEXT, _MAX_VARIANT_LEN,
)

SENDER_ID = 50001
ADMIN_ID  = 999
INTRUDER_ID = 50003


def _boot():
    db.init_db()
    init_envelope_db()
    db.upsert_user(SENDER_ID, "sender50001", "Sender", None)
    db.upsert_user(ADMIN_ID, "admin999", "Admin", None)
    db.upsert_user(INTRUDER_ID, "intruder50003", "Intruder", None)


def _make_variant_whisper(variants=None, whisper_type="everyone"):
    variants = variants if variants is not None else ["نسخة أولى", "نسخة ثانية"]
    wid = db.create_whisper(
        SENDER_ID, variants[0], whisper_type,
        conditions_data=json.dumps({"variants": variants}),
    )
    return wid


def _make_normal_whisper(whisper_type="everyone"):
    return db.create_whisper(SENDER_ID, "همسة عادية", whisper_type)


def _w(wid):
    return dict(db.get_whisper(wid))


def _keyboard_button_data(kb):
    if not kb or not hasattr(kb, "keyboard"):
        return []
    return [b.callback_data for row in kb.keyboard for b in row if b.callback_data]


def _keyboard_button_texts(kb):
    if not kb or not hasattr(kb, "keyboard"):
        return []
    return [b.text for row in kb.keyboard for b in row]


class TestDashboardHelpers(unittest.TestCase):

    def setUp(self):
        _boot()

    def test_is_variant_true_for_valid(self):
        wid = _make_variant_whisper()
        self.assertTrue(_is_variant_whisper(_w(wid)))

    def test_is_variant_false_for_non_variant(self):
        wid = _make_normal_whisper()
        self.assertFalse(_is_variant_whisper(_w(wid)))

    def test_is_variant_false_when_less_than_two(self):
        wid = _make_variant_whisper(variants=["وحيدة"])
        self.assertFalse(_is_variant_whisper(_w(wid)))

    def test_is_variant_false_for_bad_json(self):
        wid = _make_normal_whisper()
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE whispers SET conditions_data=? WHERE whisper_id=?",
                ("{not valid json", wid),
            )
        self.assertFalse(_is_variant_whisper(_w(wid)))

    def test_variant_count_counts_only_nonblank(self):
        wid = _make_variant_whisper(variants=["أ", "   ", "ب", "", "ج"])
        self.assertEqual(_variant_count(_w(wid)), 3)

    def test_preview_numbered_list(self):
        wid = _make_variant_whisper(variants=["الأولى", "الثانية", "الثالثة"])
        text = _build_variants_preview_text(_w(wid))
        self.assertIn("1. الأولى", text)
        self.assertIn("2. الثانية", text)
        self.assertIn("3. الثالثة", text)

    def test_preview_truncates_single_variant(self):
        long_variant = "نص طويل جداً " * 60
        wid = _make_variant_whisper(variants=[long_variant, "ثانية"])
        text = _build_variants_preview_text(_w(wid))
        first_line = text.splitlines()[0]
        self.assertTrue(first_line.endswith("…"))
        self.assertLessEqual(len(first_line) - len("1. "), _MAX_VARIANT_LEN + 1)

    def test_preview_truncates_total(self):
        variants = ["النسخة رقم " + str(i) * 300 for i in range(30)]
        wid = _make_variant_whisper(variants=variants)
        text = _build_variants_preview_text(_w(wid))
        self.assertLessEqual(len(text), _MAX_PREVIEW_TEXT + 1 + len("… (بقية النسخ مقطوعة للعرض الآمن)"))
        self.assertIn("مقطوعة", text)

    def test_dashboard_text_shows_variant_label(self):
        wid = _make_variant_whisper(variants=["أ", "ب", "ج"])
        text = _build_dashboard_text(_w(wid))
        self.assertIn("🧬 همسة متغيرة", text)
        self.assertIn("عدد النسخ: 3", text)

    def test_dashboard_text_hides_label_for_normal(self):
        wid = _make_normal_whisper()
        text = _build_dashboard_text(_w(wid))
        self.assertNotIn("🧬 همسة متغيرة", text)

    def test_keyboard_adds_variants_button(self):
        wid = _make_variant_whisper()
        kb = dashboard_keyboard(wid)
        texts = _keyboard_button_texts(kb)
        self.assertIn("🧬 عرض النسخ", texts)
        self.assertIn(f"dsh:vars:{wid}", _keyboard_button_data(kb))

    def test_keyboard_omits_variants_button_for_normal(self):
        wid = _make_normal_whisper()
        kb = dashboard_keyboard(wid)
        self.assertNotIn("🧬 عرض النسخ", _keyboard_button_texts(kb))
        self.assertNotIn(f"dsh:vars:{wid}", _keyboard_button_data(kb))


class TestDashVariantsHandler(unittest.TestCase):
    """Drives the real callback registration via the bot module."""

    @classmethod
    def setUpClass(cls):
        import bot as botmod
        cls.botmod = botmod
        cls.bot = botmod.bot
        cls.user_states = botmod.user_states
        _boot()
        botmod.bot.send_message = MagicMock(return_value=MagicMock(message_id=1, chat=None))
        botmod.bot.answer_callback_query = MagicMock()
        botmod.bot.edit_message_text = MagicMock()
        botmod.bot.edit_message_reply_markup = MagicMock()
        botmod.bot.delete_message = MagicMock()
        botmod.bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
        botmod.bot.get_chat_member = MagicMock(return_value=MagicMock(status="member"))
        if not any(getattr(h['function'], '__name__', '') == 'dash_variants'
                   for h in botmod.bot.callback_query_handlers):
            botmod.register_all_handlers()

    def setUp(self):
        self.user_states.clear()
        delete_draft(SENDER_ID)
        delete_draft(ADMIN_ID)
        self.bot.answer_callback_query.reset_mock()
        self.bot.edit_message_text.reset_mock()
        self.bot.send_message.reset_mock()

    def _callback(self, user_id, data):
        call = MagicMock()
        call.id = f"cb_{data}"
        call.data = data
        u = MagicMock()
        u.id = user_id
        u.username = f"user{user_id}"
        u.first_name = f"User{user_id}"
        call.from_user = u
        call.message = MagicMock()
        call.message.chat.id = user_id
        call.message.message_id = 7000 + user_id
        call.inline_message_id = None
        return call

    def _dispatch(self, call):
        from telebot import ContinueHandling
        for handler in self.bot.callback_query_handlers:
            if self.bot._test_message_handler(handler, call):
                handler['function'](call)
                return handler['function']
        return None

    def test_sender_can_view_preview(self):
        wid = _make_variant_whisper(variants=["أولى", "ثانية"])
        self._dispatch(self._callback(SENDER_ID, f"dsh:vars:{wid}"))
        self.bot.answer_callback_query.assert_called_once_with("cb_dsh:vars:" + wid)
        sent = self.bot.edit_message_text.call_args
        self.assertIsNotNone(sent)
        text = sent[0][0]
        self.assertIn("1. أولى", text)
        self.assertIn("2. ثانية", text)
        self.assertNotIn("أولى", str(sent[1]))  # never in kwargs/callback

    def test_admin_can_view_preview(self):
        wid = _make_variant_whisper()
        self._dispatch(self._callback(ADMIN_ID, f"dsh:vars:{wid}"))
        self.bot.edit_message_text.assert_called_once()

    def test_unauthorized_user_refused(self):
        wid = _make_variant_whisper()
        self._dispatch(self._callback(INTRUDER_ID, f"dsh:vars:{wid}"))
        self.bot.edit_message_text.assert_not_called()
        self.bot.answer_callback_query.assert_called_once()
        alert = self.bot.answer_callback_query.call_args[0][1]
        self.assertIn("الإدمن فقط", alert)

    def test_non_variant_whisper_refused(self):
        wid = _make_normal_whisper()
        self._dispatch(self._callback(SENDER_ID, f"dsh:vars:{wid}"))
        self.bot.edit_message_text.assert_not_called()
        alert = self.bot.answer_callback_query.call_args[0][1]
        self.assertIn("ليست همسة متغيرة", alert)

    def test_missing_whisper_refused(self):
        self._dispatch(self._callback(SENDER_ID, "dsh:vars:nope"))
        self.bot.edit_message_text.assert_not_called()
        alert = self.bot.answer_callback_query.call_args[0][1]
        self.assertIn("غير موجودة", alert)


class TestDashResendHandler(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import bot as botmod
        cls.botmod = botmod
        cls.bot = botmod.bot
        _boot()
        botmod.bot.send_message = MagicMock(return_value=MagicMock(message_id=1, chat=None))
        botmod.bot.answer_callback_query = MagicMock()
        botmod.bot.edit_message_text = MagicMock()
        botmod.bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
        if not any(getattr(h['function'], '__name__', '') == 'dash_resend'
                   for h in botmod.bot.callback_query_handlers):
            botmod.register_all_handlers()

    def setUp(self):
        delete_draft(SENDER_ID)
        self.bot.answer_callback_query.reset_mock()
        self.bot.send_message.reset_mock()

    def _callback(self, user_id, data):
        call = MagicMock()
        call.id = f"cb_{data}"
        call.data = data
        u = MagicMock()
        u.id = user_id
        u.username = f"user{user_id}"
        u.first_name = f"User{user_id}"
        call.from_user = u
        call.message = MagicMock()
        call.message.chat.id = user_id
        call.message.message_id = 8000 + user_id
        call.inline_message_id = None
        return call

    def _dispatch(self, call):
        for handler in self.bot.callback_query_handlers:
            if self.bot._test_message_handler(handler, call):
                handler['function'](call)
                return handler['function']
        return None

    def _whisper_count(self, wid):
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM whispers WHERE whisper_id=?", (wid,)
            ).fetchone()[0]

    def _switch_inline_queries(self):
        queries = []
        for c in self.bot.send_message.call_args_list:
            kb = (c.kwargs or {}).get("reply_markup")
            if not kb or not hasattr(kb, "keyboard"):
                continue
            for row in kb.keyboard:
                for b in row:
                    q = getattr(b, "switch_inline_query", None)
                    if q:
                        queries.append(q)
        return queries

    def test_variant_resend_rebuilds_draft(self):
        wid = _make_variant_whisper(variants=["أولى", "ثانية", "ثالثة"])
        self._dispatch(self._callback(SENDER_ID, f"dsh:rsnd:{wid}"))
        draft = get_draft(SENDER_ID)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["category"], "variant")
        conds = json.loads(draft["conditions_data"])
        self.assertEqual(conds["variants"], ["أولى", "ثانية", "ثالثة"])
        queries = self._switch_inline_queries()
        self.assertEqual(queries, [f"v:{draft['id']}"])
        self.assertEqual(self._whisper_count(wid), 1)  # no duplicate created

    def test_single_variant_whisper_is_not_variant(self):
        wid = _make_variant_whisper(variants=["وحيدة فقط"])
        self.assertFalse(_is_variant_whisper(_w(wid)))
        self._dispatch(self._callback(SENDER_ID, f"dsh:rsnd:{wid}"))
        self.assertIsNone(get_draft(SENDER_ID))
        queries = self._switch_inline_queries()
        self.assertEqual(queries, ["وحيدة فقط"])
        self.assertEqual(self._whisper_count(wid), 1)

    def test_normal_resend_unchanged(self):
        wid = _make_normal_whisper()
        self._dispatch(self._callback(SENDER_ID, f"dsh:rsnd:{wid}"))
        self.assertIsNone(get_draft(SENDER_ID))
        queries = self._switch_inline_queries()
        self.assertEqual(queries, ["همسة عادية"])
        self.assertEqual(self._whisper_count(wid), 1)


class TestEditVariantGuard(unittest.TestCase):
    """handle_edit must refuse variant whispers; normal flow unchanged."""

    @classmethod
    def setUpClass(cls):
        import bot as botmod
        cls.botmod = botmod
        cls.bot = botmod.bot
        cls.user_states = botmod.user_states
        _boot()
        botmod.bot.send_message = MagicMock(return_value=MagicMock(message_id=1, chat=None))
        botmod.bot.answer_callback_query = MagicMock()
        botmod.bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
        if not any(getattr(h['function'], '__name__', '') == 'handle_edit'
                   for h in botmod.bot.callback_query_handlers):
            botmod.register_all_handlers()

    def setUp(self):
        self.user_states.clear()
        self.bot.answer_callback_query.reset_mock()
        self.bot.send_message.reset_mock()

    def _callback(self, user_id, data):
        call = MagicMock()
        call.id = f"cb_{data}"
        call.data = data
        u = MagicMock()
        u.id = user_id
        u.username = f"user{user_id}"
        u.first_name = f"User{user_id}"
        call.from_user = u
        call.message = MagicMock()
        call.message.chat.id = user_id
        call.message.message_id = 9000 + user_id
        call.inline_message_id = None
        return call

    def _dispatch(self, call):
        for handler in self.bot.callback_query_handlers:
            if self.bot._test_message_handler(handler, call):
                handler['function'](call)
                return handler['function']
        return None

    def test_variant_edit_refused(self):
        wid = _make_variant_whisper()
        self._dispatch(self._callback(SENDER_ID, f"edit:{wid}"))
        self.assertNotIn(SENDER_ID, self.user_states)
        self.bot.send_message.assert_not_called()
        alert = self.bot.answer_callback_query.call_args[0][1]
        self.assertIn("غير متاح", alert)

    def test_normal_edit_enters_flow(self):
        wid = _make_normal_whisper()
        self._dispatch(self._callback(SENDER_ID, f"edit:{wid}"))
        self.assertEqual(
            self.user_states.get(SENDER_ID),
            {"action": "edit_whisper", "whisper_id": wid},
        )
        self.bot.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
