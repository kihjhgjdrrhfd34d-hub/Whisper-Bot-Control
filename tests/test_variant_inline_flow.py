"""
tests/test_variant_inline_flow.py — Stage 3: variant whisper inline flow.

Drives the REAL handlers/inline.py registration:
  - "v:<draft_id>" inline query → 5 type results (everyone / first_one /
    first_three / first_five / custom) built WITHOUT exposing variant text.
  - chosen "v:{wtype}:{draft_id}" → creates the actual whisper
    (content = first variant as fallback, conditions_data kept in full,
    max_readers per type), edits the placeholder, and consumes the draft.

Covers:
  1. VARIANT_TYPE_OPTIONS structure (5 types, no new whisper_type).
  2. _valid_variant_draft validation (category, JSON, >=2 variants).
  3. build_variant_inline_results: 5 ids "v:{wtype}:{draft_id}", no text leak.
  4. "v:" query with valid draft → results; update_draft_target honors chat.
  5. Rejections: wrong id, foreign draft, wrong category, bad JSON, <2 variants.
  6. _handle_variant_chosen: everyone/first_one/first_three/first_five/custom,
     max_readers correctness, conditions_data passthrough, draft consumed.
  7. custom: targets resolved from the query after "v:<draft_id>".
  8. Reuse prevention: second chosen after consumption creates nothing.
  9. Co-existence: normal + conditional inline flows unaffected.
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_variant_inline_flow.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.envelope import (
    init_envelope_db, create_draft, get_draft, get_pending_draft, delete_draft,
)
from handlers.inline import (
    VARIANT_TYPE_OPTIONS, _valid_variant_draft,
    build_variant_inline_results, _handle_variant_chosen,
)

SENDER_ID = 40001
TARGET_ID = 40002
GROUP_ID  = -10040001


def _boot():
    db.init_db()
    init_envelope_db()
    db.upsert_user(SENDER_ID, "sender40001", "Sender", None)
    db.upsert_user(TARGET_ID, "target40002", "Target", None)


def _make_variant_draft(user_id, variants, content=None):
    create_draft(
        user_id,
        content=content if content is not None else variants[0],
        category="variant",
        conditions_data=json.dumps({"variants": variants}),
    )
    return get_draft(user_id)


def _make_query(text, chat_id=None):
    q = MagicMock()
    q.id = "q_variant_1"
    q.query = text
    u = MagicMock()
    u.id = SENDER_ID
    u.username = "sender40001"
    u.first_name = "Sender"
    u.last_name = None
    q.from_user = u
    if chat_id:
        q._chat = MagicMock()
        q._chat.id = chat_id
        q._chat.type = "group"
    else:
        q._chat = None
    return q


class TestVariantTypeOptions(unittest.TestCase):
    """VARIANT_TYPE_OPTIONS constant correctness."""

    def setUp(self):
        _boot()

    def test_five_types(self):
        self.assertEqual(len(VARIANT_TYPE_OPTIONS), 5)

    def test_contains_all_types(self):
        types = [o[0] for o in VARIANT_TYPE_OPTIONS]
        for expected in ("everyone", "first_one", "first_three", "first_five", "custom"):
            self.assertIn(expected, types)

    def test_no_new_whisper_type(self):
        from handlers.inline import _TYPE_MAX_READERS
        for wtype, max_r, _, _ in VARIANT_TYPE_OPTIONS:
            self.assertIn(wtype, _TYPE_MAX_READERS)
            self.assertEqual(_TYPE_MAX_READERS[wtype], max_r)

    def test_max_readers_values(self):
        by_type = {o[0]: o[1] for o in VARIANT_TYPE_OPTIONS}
        self.assertEqual(by_type["everyone"], 0)
        self.assertEqual(by_type["first_one"], 1)
        self.assertEqual(by_type["first_three"], 3)
        self.assertEqual(by_type["first_five"], 5)
        self.assertEqual(by_type["custom"], 0)

    def test_titles_descriptions_not_empty(self):
        for _, _, title, desc in VARIANT_TYPE_OPTIONS:
            self.assertTrue(title and title.startswith("🧬"))
            self.assertTrue(desc)


class TestValidVariantDraft(unittest.TestCase):
    """_valid_variant_draft validation logic."""

    def setUp(self):
        _boot()

    def test_valid_returns_sanitized_variants(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب", "نسخة ج"])
        result = _valid_variant_draft(draft)
        self.assertEqual(result, ["نسخة أ", "نسخة ب", "نسخة ج"])

    def test_wrong_category_rejected(self):
        create_draft(SENDER_ID, content="نص", category="conditional",
                     conditions_data=json.dumps({"variants": ["أ", "ب"]}))
        draft = get_draft(SENDER_ID)
        self.assertIsNone(_valid_variant_draft(draft))

    def test_less_than_two_rejected(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة واحدة فقط"])
        self.assertIsNone(_valid_variant_draft(draft))

    def test_missing_conditions_data_rejected(self):
        create_draft(SENDER_ID, content="نص", category="variant", conditions_data="")
        draft = get_draft(SENDER_ID)
        self.assertIsNone(_valid_variant_draft(draft))

    def test_bad_json_rejected(self):
        create_draft(SENDER_ID, content="نص", category="variant",
                     conditions_data="not-json{{{")
        draft = get_draft(SENDER_ID)
        self.assertIsNone(_valid_variant_draft(draft))

    def test_non_dict_conditions_rejected(self):
        create_draft(SENDER_ID, content="نص", category="variant",
                     conditions_data=json.dumps(["أ", "ب"]))
        draft = get_draft(SENDER_ID)
        self.assertIsNone(_valid_variant_draft(draft))

    def test_missing_variants_key_rejected(self):
        create_draft(SENDER_ID, content="نص", category="variant",
                     conditions_data=json.dumps({"other": 1}))
        draft = get_draft(SENDER_ID)
        self.assertIsNone(_valid_variant_draft(draft))

    def test_blank_entries_filtered(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "   ", ""])
        self.assertIsNone(_valid_variant_draft(draft))

    def test_none_draft_rejected(self):
        self.assertIsNone(_valid_variant_draft(None))


class TestBuildVariantInlineResults(unittest.TestCase):
    """build_variant_inline_results structure + no text leak."""

    def setUp(self):
        _boot()

    def test_five_results_with_v_prefix_ids(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب"])
        results = build_variant_inline_results(draft, 0)
        self.assertEqual(len(results), 5)
        ids = {r.id for r in results}
        for wtype, _, _, _ in VARIANT_TYPE_OPTIONS:
            self.assertIn(f"v:{wtype}:{draft['id']}", ids)

    def test_no_variant_text_in_placeholder(self):
        draft = _make_variant_draft(SENDER_ID, ["سري_أ", "سري_ب"])
        results = build_variant_inline_results(draft, 0)
        for r in results:
            text = r.input_message_content.message_text
            self.assertNotIn("سري_أ", text)
            self.assertNotIn("سري_ب", text)
            self.assertIn("🧬", text)
            self.assertIn("⏳", text)

    def test_placeholder_has_processing_keyboard(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب"])
        results = build_variant_inline_results(draft, 0)
        for r in results:
            self.assertIsNotNone(r.reply_markup)

    def test_titles_are_type_titles(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب"])
        results = build_variant_inline_results(draft, 0)
        titles = {r.title for r in results}
        for _, _, title, _ in VARIANT_TYPE_OPTIONS:
            self.assertIn(title, titles)


class TestVariantInlineQueryHandler(unittest.TestCase):
    """'v:' branch of the real inline handler."""

    def _capture_inline_handler(self, bot):
        handlers = []

        def fake_inline_handler(**kwargs):
            def deco(f):
                handlers.append((kwargs, f))
                return f

            return deco

        bot.inline_handler = fake_inline_handler
        return handlers

    def _call_inline(self, bot, query):
        handlers = self._capture_inline_handler(bot)
        from handlers.inline import register_inline_handlers
        register_inline_handlers(bot)
        handler_func = handlers[0][1]
        handler_func(query)

    def _get_result_ids(self, bot):
        if not bot.answer_inline_query.called:
            return []
        args = bot.answer_inline_query.call_args[0]
        return [r.id for r in args[1]]

    def setUp(self):
        _boot()
        db.update_group_setting(GROUP_ID, "spam_limit_enabled", 0)
        self.bot = MagicMock()
        self.bot.get_me.return_value.username = "test_bot"

    def test_valid_draft_returns_five_results(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب"])
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}", chat_id=GROUP_ID))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 5)
        for wtype, _, _, _ in VARIANT_TYPE_OPTIONS:
            self.assertIn(f"v:{wtype}:{draft['id']}", ids)

    def test_updates_draft_target_with_chat(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب"])
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}", chat_id=GROUP_ID))
        pending = get_pending_draft(SENDER_ID)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["target_chat_id"], GROUP_ID)

    def test_answer_called_personal_no_cache(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب"])
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}"))
        _, kwargs = self.bot.answer_inline_query.call_args
        self.assertEqual(kwargs["cache_time"], 0)
        self.assertEqual(kwargs["is_personal"], True)

    def test_unknown_draft_id_returns_error(self):
        self._call_inline(self.bot, _make_query("v:99999"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))

    def test_no_draft_returns_error(self):
        self._call_inline(self.bot, _make_query("v:123"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))

    def test_non_numeric_id_returns_error(self):
        self._call_inline(self.bot, _make_query("v:abc"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))

    def test_foreign_draft_returns_error(self):
        draft = _make_variant_draft(TARGET_ID, ["نسخة أ", "نسخة ب"])
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))

    def test_wrong_category_returns_error(self):
        create_draft(SENDER_ID, content="نص", category="conditional",
                     conditions_data=json.dumps({"variants": ["أ", "ب"]}))
        draft = get_draft(SENDER_ID)
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))

    def test_bad_json_returns_error(self):
        create_draft(SENDER_ID, content="نص", category="variant",
                     conditions_data="not-json{{{")
        draft = get_draft(SENDER_ID)
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))

    def test_single_variant_returns_error(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة واحدة فقط"])
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))

    def test_consumed_draft_returns_error(self):
        draft = _make_variant_draft(SENDER_ID, ["نسخة أ", "نسخة ب"])
        delete_draft(SENDER_ID)
        self._call_inline(self.bot, _make_query(f"v:{draft['id']}"))
        ids = self._get_result_ids(self.bot)
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("v:error"))


class TestVariantChosenHandler(unittest.TestCase):
    """_handle_variant_chosen: creates the actual whisper, consumes draft."""

    def _capture_chosen_handler(self, bot):
        handlers = []

        def fake_chosen_handler(**kwargs):
            def deco(f):
                handlers.append((kwargs, f))
                return f

            return deco

        bot.chosen_inline_handler = fake_chosen_handler
        return handlers

    def _make_result(self, result_id, query_text=None, inline_message_id="im_v_1"):
        r = MagicMock()
        r.result_id = result_id
        r.query = query_text if query_text is not None else ""
        r.inline_message_id = inline_message_id
        u = MagicMock()
        u.id = SENDER_ID
        u.username = "sender40001"
        u.first_name = "Sender"
        r.from_user = u
        return r

    def _setup_pending(self, variants):
        draft = _make_variant_draft(SENDER_ID, variants)
        from database.envelope import update_draft_target
        update_draft_target(SENDER_ID, GROUP_ID)
        return draft

    def setUp(self):
        _boot()
        self.bot = MagicMock()
        self.bot.get_me.return_value.username = "test_bot"

    def test_everyone_creates_whisper(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        before = get_pending_draft(SENDER_ID)
        self.assertIsNotNone(before)
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:everyone:{draft['id']}"), 0
        )
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
            (SENDER_ID,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["content"], "نسخة أ")
        self.assertEqual(row["whisper_type"], "everyone")
        self.assertEqual(row["max_readers"], 0)
        self.assertEqual(json.loads(row["conditions_data"]),
                         {"variants": ["نسخة أ", "نسخة ب"]})

    def test_first_one_max_readers_1(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:first_one:{draft['id']}"), 0
        )
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
            (SENDER_ID,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["whisper_type"], "first_one")
        self.assertEqual(row["max_readers"], 1)

    def test_first_three_max_readers_3(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:first_three:{draft['id']}"), 0
        )
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
            (SENDER_ID,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["whisper_type"], "first_three")
        self.assertEqual(row["max_readers"], 3)

    def test_first_five_max_readers_5(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:first_five:{draft['id']}"), 0
        )
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
            (SENDER_ID,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["whisper_type"], "first_five")
        self.assertEqual(row["max_readers"], 5)

    def test_custom_no_targets(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:custom:{draft['id']}",
                                        query_text=f"v:{draft['id']}"), 0
        )
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
            (SENDER_ID,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["whisper_type"], "custom")
        self.assertEqual(row["max_readers"], 0)
        self.assertEqual(json.loads(row["target_users"]), [])

    def test_custom_with_target_from_query(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot,
            self._make_result(
                f"v:custom:{draft['id']}",
                query_text=f"v:{draft['id']} @target40002",
            ),
            0,
        )
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
            (SENDER_ID,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["whisper_type"], "custom")
        self.assertEqual(json.loads(row["target_users"]), [TARGET_ID])

    def test_draft_consumed_after_success(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:everyone:{draft['id']}"), 0
        )
        self.assertIsNone(get_draft(SENDER_ID))
        self.assertIsNone(get_pending_draft(SENDER_ID))

    def test_second_chosen_after_consumption_creates_nothing(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:everyone:{draft['id']}"), 0
        )
        count_before = db.get_conn().execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        db.get_conn().close()
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:everyone:{draft['id']}"), 0
        )
        conn = db.get_conn()
        count_after = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_after, count_before)

    def test_unknown_wtype_creates_nothing(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        count_before = db.get_conn().execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        db.get_conn().close()
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:bogus:{draft['id']}"), 0
        )
        conn = db.get_conn()
        count_after = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_after, count_before)

    def test_draft_id_mismatch_creates_nothing(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        count_before = db.get_conn().execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        db.get_conn().close()
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:everyone:{draft['id'] + 500}"), 0
        )
        conn = db.get_conn()
        count_after = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_after, count_before)

    def test_malformed_result_id_creates_nothing(self):
        self._setup_pending(["نسخة أ", "نسخة ب"])
        count_before = db.get_conn().execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        db.get_conn().close()
        _handle_variant_chosen(self.bot, self._make_result("v:everyone"), 0)
        conn = db.get_conn()
        count_after = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (SENDER_ID,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_after, count_before)

    def test_edits_inline_placeholder(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:everyone:{draft['id']}"), 0
        )
        self.assertTrue(self.bot.edit_message_text.called)
        args = self.bot.edit_message_text.call_args[0]
        text = args[0]
        self.assertNotIn("نسخة أ", text)
        self.assertNotIn("نسخة ب", text)
        self.assertIn("🧬", text)

    def test_uses_deep_link_button(self):
        draft = self._setup_pending(["نسخة أ", "نسخة ب"])
        _handle_variant_chosen(
            self.bot, self._make_result(f"v:everyone:{draft['id']}"), 0
        )
        self.assertTrue(self.bot.edit_message_text.called)
        _, kwargs = self.bot.edit_message_text.call_args
        kb = kwargs["reply_markup"]
        self.assertIsNotNone(kb)
        urls = [b.url for row in kb.keyboard for b in row]
        self.assertTrue(any(u and "view_" in u for u in urls))


class TestVariantChosenViaHandler(unittest.TestCase):
    """Dispatch through the real chosen_inline_handler."""

    def _capture_chosen_handler(self, bot):
        handlers = []

        def fake_chosen_handler(**kwargs):
            def deco(f):
                handlers.append((kwargs, f))
                return f

            return deco

        bot.chosen_inline_handler = fake_chosen_handler
        return handlers

    def setUp(self):
        _boot()
        self.bot = MagicMock()
        self.bot.get_me.return_value.username = "test_bot"

    def test_dispatch_creates_whisper(self):
        create_draft(SENDER_ID, content="نسخة أ", category="variant",
                     conditions_data=json.dumps({"variants": ["نسخة أ", "نسخة ب"]}))
        draft = get_draft(SENDER_ID)
        from database.envelope import update_draft_target
        update_draft_target(SENDER_ID, GROUP_ID)

        handlers = self._capture_chosen_handler(self.bot)
        from handlers.inline import register_inline_handlers
        register_inline_handlers(self.bot)
        chosen_func = handlers[0][1]
        chosen_func(self._make_result(f"v:first_one:{draft['id']}"))

        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
            (SENDER_ID,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["whisper_type"], "first_one")
        self.assertEqual(row["content"], "نسخة أ")
        self.assertIsNone(get_draft(SENDER_ID))

    def _make_result(self, result_id):
        r = MagicMock()
        r.result_id = result_id
        r.query = ""
        r.inline_message_id = "im_v_dispatch"
        u = MagicMock()
        u.id = SENDER_ID
        u.username = "sender40001"
        u.first_name = "Sender"
        r.from_user = u
        return r


class TestCoexistence(unittest.TestCase):
    """Variant inline flow must not break normal / conditional inline flow."""

    def _capture_inline_handler(self, bot):
        handlers = []

        def fake_inline_handler(**kwargs):
            def deco(f):
                handlers.append((kwargs, f))
                return f

            return deco

        bot.inline_handler = fake_inline_handler
        return handlers

    def _call_inline(self, bot, query):
        handlers = self._capture_inline_handler(bot)
        from handlers.inline import register_inline_handlers
        register_inline_handlers(bot)
        handlers[0][1](query)

    def setUp(self):
        _boot()
        db.update_group_setting(GROUP_ID, "spam_limit_enabled", 0)
        self.bot = MagicMock()
        self.bot.get_me.return_value.username = "test_bot"

    def test_normal_inline_unaffected(self):
        query = _make_query("همسة عادية للجميع", chat_id=GROUP_ID)
        self._call_inline(self.bot, query)
        ids = [r.id for r in self.bot.answer_inline_query.call_args[0][1]]
        self.assertTrue(any("everyone" in i for i in ids))
        self.assertFalse(any(i.startswith("v:") for i in ids))

    def test_conditional_inline_unaffected(self):
        from database.envelope import update_draft_target
        create_draft(SENDER_ID, content="نص مشروط", category="conditional",
                     conditions_data=json.dumps({"op": "always"}))
        draft = get_draft(SENDER_ID)
        update_draft_target(SENDER_ID, GROUP_ID)
        self._call_inline(self.bot, _make_query(f"cw:{draft['id']}", chat_id=GROUP_ID))
        ids = [r.id for r in self.bot.answer_inline_query.call_args[0][1]]
        self.assertTrue(any(i.startswith("cw:") for i in ids))

    def test_variant_does_not_leak_into_normal_flow(self):
        _make_variant_draft(SENDER_ID, ["نسخة سرية أ", "نسخة سرية ب"])
        self._call_inline(self.bot, _make_query("نص عادي", chat_id=GROUP_ID))
        ids = [r.id for r in self.bot.answer_inline_query.call_args[0][1]]
        self.assertFalse(any(i.startswith("v:") for i in ids))
        self.assertTrue(any("first_one" in i for i in ids))


if __name__ == "__main__":
    unittest.main()
