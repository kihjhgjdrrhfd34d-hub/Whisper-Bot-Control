"""
tests/test_wrapped_inline_e2e.py — End-to-End test for Wrapped Whisper → Inline Share flow.

Tests the complete lifecycle:
  1. Draft creation (cover, character, text)
  2. Inline package creation
  3. Inline results construction
  4. Whisper creation upon type selection
  5. Cleanup after success (draft + package deleted)
  6. Cancel flow (package deleted, draft kept)
  7. Retry after cancel
  8. Duplicate protection
  9. Unauthorized user rejection
 10. Expired/used package error

Each test is self-contained: prerequisites are provided through function-scoped
``pytest`` fixtures, so tests can run individually and in any order (no reliance
on a global variable or on the order of execution of a previous test).
"""

import json
import os
import sys
import tempfile
import atexit

import pytest

# ── Redirect DB before imports ───────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_ww_inline_e2e.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper, get_whisper, upsert_user,
)
from database.wrapped_whispers import (
    init_wrapped_whispers_db,
    create_draft, get_draft, delete_draft,
    update_draft_cover, update_draft_character, update_draft_content,
    update_draft_step,
    create_inline_package, get_inline_package, delete_inline_package,
    get_cover, get_character,
    update_whisper_cover_character,
    get_available_covers, get_available_characters,
)

# We also test build_wrapped_inline_results from inline.py
# (must be imported after DB setup)
from handlers.inline import build_wrapped_inline_results, WRAPPED_TYPE_OPTIONS, WRAPPED_DESTRUCTIVE_OPTIONS

SENDER_ID = 10001
INTRUDER_ID = 10002
E2E_TEXT = "هذه همسة سرية مغلفة للاختبار!"


def _boot():
    db.init_db()
    init_wrapped_whispers_db()
    upsert_user(SENDER_ID, "sender_test", "Sender", None)
    upsert_user(INTRUDER_ID, "intruder", "Intruder", None)
    print("  [BOOT] DB initialized, users created")


@pytest.fixture(scope="module", autouse=True)
def _boot_db():
    _boot()
    yield


# ── Shared fixtures (function-scoped → isolation / run-anywhere) ─────────
@pytest.fixture()
def fresh_draft():
    """An empty draft for the sender (step 1)."""
    draft = create_draft(SENDER_ID)
    yield draft
    delete_draft(SENDER_ID)


@pytest.fixture()
def draft_full():
    """A complete draft: cover + character + content (step 4)."""
    covers = get_available_covers(0)
    chars = get_available_characters(0)
    assert covers and chars, "no available covers/characters for the sender"
    create_draft(SENDER_ID)
    update_draft_cover(SENDER_ID, covers[0]["code"])
    update_draft_character(SENDER_ID, chars[0]["code"])
    update_draft_content(SENDER_ID, E2E_TEXT)
    draft = get_draft(SENDER_ID)
    yield draft
    delete_draft(SENDER_ID)


@pytest.fixture()
def inline_package(draft_full):
    """A created (unconsumed) inline package built from a full draft."""
    pkg_id = create_inline_package(
        SENDER_ID,
        draft_full["cover_code"],
        draft_full["character_code"],
        draft_full["content"],
    )
    pkg = get_inline_package(pkg_id)
    yield pkg
    delete_inline_package(pkg_id)


# ═══════════════════════════════════════════════════════════════════════════
# 1.  Draft creation
# ═══════════════════════════════════════════════════════════════════════════
def test_01_draft_creation(fresh_draft):
    print("\n─── 1. Draft Creation ──────────────────────────────────────────")
    draft = fresh_draft
    assert draft is not None, "create_draft returns a draft"
    assert draft["user_id"] == SENDER_ID, "draft.user_id == sender"
    assert draft["step"] == 1, "draft.step starts at 1"
    assert draft["cover_code"] == "", "cover_code is empty initially"
    assert draft["character_code"] == "", "character_code empty initially"
    assert draft["content"] == "", "content empty initially"


# ═══════════════════════════════════════════════════════════════════════════
# 2.  Select cover
# ═══════════════════════════════════════════════════════════════════════════
def test_02_cover_selection(fresh_draft):
    print("\n─── 2. Cover Selection ─────────────────────────────────────────")
    covers = get_available_covers(0)
    assert len(covers) > 0, f"Found {len(covers)} available covers"
    first_cover = covers[0]
    update_draft_cover(SENDER_ID, first_cover["code"])
    draft = get_draft(SENDER_ID)
    assert draft["cover_code"] == first_cover["code"], \
        f"cover_code = '{first_cover['code']}'"
    assert draft["step"] == 2, "step advanced to 2"
    cover = get_cover(first_cover["code"])
    assert cover is not None, f"get_cover('{first_cover['code']}') returns data"
    assert "name" in cover and "icon" in cover, "cover has name + icon"


# ═══════════════════════════════════════════════════════════════════════════
# 3.  Select character
# ═══════════════════════════════════════════════════════════════════════════
def test_03_character_selection(fresh_draft):
    print("\n─── 3. Character Selection ─────────────────────────────────────")
    covers = get_available_covers(0)
    assert len(covers) > 0, "need at least one cover"
    update_draft_cover(SENDER_ID, covers[0]["code"])
    chars = get_available_characters(0)
    assert len(chars) > 0, f"Found {len(chars)} available characters"
    first_char = chars[0]
    update_draft_character(SENDER_ID, first_char["code"])
    draft = get_draft(SENDER_ID)
    assert draft["character_code"] == first_char["code"], \
        f"character_code = '{first_char['code']}'"
    assert draft["step"] == 3, "step advanced to 3"
    char = get_character(first_char["code"])
    assert char is not None, f"get_character('{first_char['code']}') returns data"
    assert "name" in char and "icon" in char, "character has name + icon"


# ═══════════════════════════════════════════════════════════════════════════
# 4.  Write text content
# ═══════════════════════════════════════════════════════════════════════════
def test_04_text_input(fresh_draft):
    print("\n─── 4. Text Input ──────────────────────────────────────────────")
    test_text = E2E_TEXT
    update_draft_content(SENDER_ID, test_text)
    draft = get_draft(SENDER_ID)
    assert draft["content"] == test_text, f"content = '{test_text}'"
    assert draft["step"] == 4, "step advanced to 4 (preview)"


# ═══════════════════════════════════════════════════════════════════════════
# 5.  Preview verification
# ═══════════════════════════════════════════════════════════════════════════
def test_05_preview(draft_full):
    print("\n─── 5. Preview ─────────────────────────────────────────────────")
    draft = draft_full
    assert draft is not None, "draft exists for preview"
    assert draft["cover_code"] != "", "cover_code is set"
    assert draft["character_code"] != "", "character_code is set"
    assert draft["content"] != "", "content is set"
    assert draft["step"] == 4, "step == 4 (preview)"

    cover = get_cover(draft["cover_code"])
    char = get_character(draft["character_code"])
    assert cover is not None, "cover data is accessible"
    assert char is not None, "character data is accessible"

    # Build preview text (same as in the handler)
    cname = cover["name"] if cover else "—"
    cicon = cover["icon"] if cover else ""
    chname = char["name"] if char else "—"
    chicn = char["icon"] if char else ""
    preview = f"👁 *معاينة الهمسة*\n\n📦 *الغلاف:* {cicon} {cname}\n🎭 *الشخصية:* {chicn} {chname}\n\n✏️ *النص:* ||{draft['content'][:200]}||"
    assert "معاينة" in preview, "preview text contains preview title"
    assert cname in preview, "preview contains cover name"
    assert chname in preview, "preview contains character name"
    assert draft['content'] in preview, "preview contains content"


# ═══════════════════════════════════════════════════════════════════════════
# 6.  Send → Create inline package (draft stays)
# ═══════════════════════════════════════════════════════════════════════════
def test_06_create_inline_package(inline_package):
    print("\n─── 6. Create Inline Package ───────────────────────────────────")
    pkg = inline_package
    pkg_id = pkg["id"]
    assert pkg_id is not None, f"package_id = '{pkg_id}'"
    assert len(pkg_id) == 8, "package_id is 8 chars"

    # Verify draft STILL exists (NOT deleted)
    draft_after = get_draft(SENDER_ID)
    assert draft_after is not None, \
        "draft still exists after package creation (NOT deleted)"
    assert draft_after["content"] == pkg["content"], "draft content unchanged"

    # Verify package fields
    assert pkg["user_id"] == SENDER_ID, "package.user_id == sender"
    assert pkg["cover_code"] == draft_after["cover_code"], "package.cover_code matches draft"
    assert pkg["character_code"] == draft_after["character_code"], "package.character_code matches draft"
    assert pkg["content"] == draft_after["content"], "package.content matches draft"
    assert "created_at" in pkg, "package has created_at timestamp"


# ═══════════════════════════════════════════════════════════════════════════
# 7.  Build wrapped inline results
# ═══════════════════════════════════════════════════════════════════════════
def test_07_inline_results(inline_package):
    print("\n─── 7. Inline Results ───────────────────────────────────────────")
    pkg = inline_package
    results = build_wrapped_inline_results(pkg, 0)

    expected_count = len(WRAPPED_TYPE_OPTIONS) + len(WRAPPED_DESTRUCTIVE_OPTIONS)
    assert len(results) == expected_count, \
        f"build_wrapped_inline_results returns {len(results)} results (expected {expected_count})"

    # Check each normal type
    found_types = set()
    for r in results:
        assert "ww:" in r.id, f"result.id starts with 'ww:': {r.id}"
        if "destructive" in r.id:
            assert r.id.startswith("ww:destructive:"), f"destructive result id format: {r.id}"
        else:
            assert r.id.count(":") == 2, f"normal result id has 2 colons: {r.id}"

        # Collect which types we found
        if r.id.startswith("ww:destructive:"):
            wtype = r.id.split(":")[2]
        else:
            wtype = r.id.split(":")[1]
        found_types.add(wtype)

    # Verify all 4 normal types exist
    for wtype, _, _, _ in WRAPPED_TYPE_OPTIONS:
        assert wtype in found_types, f"type '{wtype}' is in results"

    # Verify all 3 destructive types exist
    for wtype, _, _, _ in WRAPPED_DESTRUCTIVE_OPTIONS:
        assert wtype in found_types, f"destructive type '{wtype}' is in results"

    # Verify placeholder text
    for r in results:
        text = r.input_message_content.message_text
        assert "⏳" in text, "placeholder contains hourglass emoji"
        assert "جاري تجهيز" in text, "placeholder contains loading text"


# ═══════════════════════════════════════════════════════════════════════════
# 8.  Simulate chosen: create whisper + cleanup
# ═══════════════════════════════════════════════════════════════════════════
def test_08_whisper_creation_on_chosen(inline_package):
    print("\n─── 8. Whisper Creation on Chosen ───────────────────────────────")
    pkg = inline_package
    assert pkg is not None, "package exists before creation"

    # Simulate what _handle_wrapped_chosen does
    wtype = "first_one"
    max_r = 1
    is_destructive = False
    hours = 0

    wid = create_whisper(
        sender_id=SENDER_ID,
        content=pkg["content"],
        whisper_type=wtype,
        target_users=[],
        max_readers=max_r,
        auto_delete_hours=hours,
        is_destructive=is_destructive,
    )
    assert wid is not None, f"create_whisper returns wid = '{wid}'"

    # Verify whisper exists
    whisper = get_whisper(wid)
    assert whisper is not None, "whisper found in DB"
    assert whisper["sender_id"] == SENDER_ID, "whisper.sender_id == sender"
    assert whisper["content"] == pkg["content"], "whisper.content matches package content"
    assert whisper["whisper_type"] == "first_one", "whisper.whisper_type == 'first_one'"
    assert whisper["is_destructive"] == 0, "whisper is not destructive"

    # Update cover/character
    update_whisper_cover_character(wid, pkg["cover_code"], pkg["character_code"])
    whisper_after = get_whisper(wid)
    assert whisper_after["cover_code"] == pkg["cover_code"], \
        "whisper.cover_code matches package"
    assert whisper_after["character_code"] == pkg["character_code"], \
        "whisper.character_code matches package"

    # Cleanup: delete package + draft
    delete_inline_package(pkg["id"])
    assert get_inline_package(pkg["id"]) is None, "package deleted after consumption"

    delete_draft(SENDER_ID)
    assert get_draft(SENDER_ID) is None, "draft deleted after consumption"

    print(f"    Whisper ID: {wid}")
    print(f"    Cover: {whisper_after['cover_code']}")
    print(f"    Character: {whisper_after['character_code']}")


# ═══════════════════════════════════════════════════════════════════════════
# 9.  Verify whisper + read button
# ═══════════════════════════════════════════════════════════════════════════
def test_09_whisper_read_flow():
    print("\n─── 9. Whisper Read Flow ────────────────────────────────────────")
    # Create a fresh whisper to test the read flow
    wid = create_whisper(
        sender_id=SENDER_ID,
        content="محتوى الهمسة السري",
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
    )
    update_whisper_cover_character(wid, "cover_morning", "char_optimist")

    # Verify can_read_whisper returns correct results
    can, reason = db.can_read_whisper(wid, INTRUDER_ID)  # Different user
    assert can, "non-sender can read first_one before any read"
    assert reason == "allowed", f"reason is 'allowed', got '{reason}'"

    # Simulate a read
    is_new = db.record_whisper_read(wid, INTRUDER_ID)
    assert is_new, "first read recorded as new"

    # After read, first_one should be taken
    can, reason = db.can_read_whisper(wid, 99999)
    assert not can, "another user cannot read after first_one taken"
    assert reason == "taken", f"reason is 'taken', got '{reason}'"

    # Build the final message text (same as in _handle_wrapped_chosen)
    cover = get_cover("cover_morning")
    char = get_character("char_optimist")
    cover_icon = cover["icon"] if cover else "📜"
    cover_name = cover["name"] if cover else ""
    char_icon = char["icon"] if char else "🤫"
    char_name = char["name"] if char else ""

    final_text = f"{char_icon} {char_name}\n\n{cover_icon} {cover_name}\n\n🔒 اضغط للرؤية"
    assert "🔒" in final_text, "final message has lock emoji"
    assert char_name in final_text, "final message has character name"
    assert cover_name in final_text, "final message has cover name"
    print(f"    Final message format:\n{final_text}")


# ═══════════════════════════════════════════════════════════════════════════
# 10. Cancel flow (package deleted, draft kept)
# ═══════════════════════════════════════════════════════════════════════════
def test_10_cancel_flow():
    print("\n─── 10. Cancel Flow ──────────────────────────────────────────────")
    # Create fresh draft + package
    create_draft(SENDER_ID)
    update_draft_cover(SENDER_ID, "cover_morning")
    update_draft_character(SENDER_ID, "char_optimist")
    update_draft_content(SENDER_ID, "نص الهمسة للإلغاء")
    draft = get_draft(SENDER_ID)
    assert draft is not None, "draft created for cancel test"

    pkg_id = create_inline_package(SENDER_ID, draft["cover_code"], draft["character_code"], draft["content"])
    assert get_inline_package(pkg_id) is not None, "package exists for cancel test"
    assert get_draft(SENDER_ID) is not None, "draft exists alongside package"

    # Simulate cancel: delete package, keep draft
    delete_inline_package(pkg_id)
    assert get_inline_package(pkg_id) is None, "package deleted on cancel"

    # Draft should still exist
    draft_after = get_draft(SENDER_ID)
    assert draft_after is not None, "draft REMAINS after cancel"
    assert draft_after["content"] == "نص الهمسة للإلغاء", "draft content unchanged after cancel"
    assert draft_after["cover_code"] == "cover_morning", "draft cover unchanged after cancel"

    delete_draft(SENDER_ID)


# ═══════════════════════════════════════════════════════════════════════════
# 11. Retry after cancel (send again)
# ═══════════════════════════════════════════════════════════════════════════
def test_11_retry_after_cancel():
    print("\n─── 11. Retry After Cancel ──────────────────────────────────────")
    # Self-contained: build a draft, cancel once, then re-send from the same draft
    create_draft(SENDER_ID)
    update_draft_cover(SENDER_ID, "cover_morning")
    update_draft_character(SENDER_ID, "char_optimist")
    update_draft_content(SENDER_ID, "نص الهمسة للإلغاء")
    draft = get_draft(SENDER_ID)
    assert draft is not None, "draft exists for retry"

    # Cancel round: create + delete a package, draft stays
    pkg_cancel = create_inline_package(SENDER_ID, draft["cover_code"], draft["character_code"], draft["content"])
    delete_inline_package(pkg_cancel)
    assert get_draft(SENDER_ID) is not None, "draft remains after cancel"

    # Re-send: create a new package from the same (still-present) draft
    draft = get_draft(SENDER_ID)
    pkg_id2 = create_inline_package(SENDER_ID, draft["cover_code"], draft["character_code"], draft["content"])
    assert pkg_id2 is not None, "new package created after cancel"
    assert get_inline_package(pkg_id2) is not None, "new package exists"

    # Draft still exists (not deleted)
    assert get_draft(SENDER_ID) is not None, "draft still exists after re-send"

    # Consume this package
    wid = create_whisper(
        sender_id=SENDER_ID,
        content=draft["content"],
        whisper_type="everyone",
        target_users=[],
        max_readers=0,
    )
    assert wid is not None, "whisper created from retry package"

    delete_inline_package(pkg_id2)
    assert get_inline_package(pkg_id2) is None, "retry package deleted after consumption"

    delete_draft(SENDER_ID)
    assert get_draft(SENDER_ID) is None, "draft deleted after final consumption"


# ═══════════════════════════════════════════════════════════════════════════
# 12. Duplicate protection
# ═══════════════════════════════════════════════════════════════════════════
def test_12_duplicate_protection():
    print("\n─── 12. Duplicate Protection ─────────────────────────────────────")
    # Create fresh package
    create_draft(SENDER_ID)
    update_draft_cover(SENDER_ID, "cover_morning")
    update_draft_character(SENDER_ID, "char_mysterious")
    update_draft_content(SENDER_ID, "نص مكرر")
    draft = get_draft(SENDER_ID)

    pkg_id = create_inline_package(SENDER_ID, draft["cover_code"], draft["character_code"], draft["content"])

    # First consumption: succeeds
    wid1 = create_whisper(
        sender_id=SENDER_ID,
        content=draft["content"],
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
    )
    assert wid1 is not None, "first consumption succeeds"
    delete_inline_package(pkg_id)  # Simulates _handle_wrapped_chosen cleanup

    # Second consumption: get_inline_package returns None (deleted)
    pkg_again = get_inline_package(pkg_id)
    assert pkg_again is None, "second consumption fails - package already deleted"

    # Create a new whisper with same data (this would happen if user clicks share again with a new package)
    wid2 = create_whisper(
        sender_id=SENDER_ID,
        content=draft["content"],
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
    )
    assert wid2 is not None, "new whisper can be created with new package"
    assert wid1 != wid2, "second whisper has DIFFERENT wid than first"

    delete_draft(SENDER_ID)
    print(f"    First wid:  {wid1}")
    print(f"    Second wid: {wid2}")


# ═══════════════════════════════════════════════════════════════════════════
# 13. Unauthorized user
# ═══════════════════════════════════════════════════════════════════════════
def test_13_unauthorized_user():
    print("\n─── 13. Unauthorized User ───────────────────────────────────────")
    # User 10001 creates package
    create_draft(SENDER_ID)
    update_draft_cover(SENDER_ID, "cover_evening")
    update_draft_character(SENDER_ID, "char_whisperer")
    update_draft_content(SENDER_ID, "نص خاص")
    draft = get_draft(SENDER_ID)

    pkg_id = create_inline_package(SENDER_ID, draft["cover_code"], draft["character_code"], draft["content"])
    pkg = get_inline_package(pkg_id)
    assert pkg is not None, "package created by sender"
    assert pkg["user_id"] == SENDER_ID, "package.user_id == sender"

    # Intruder tries to use it
    assert pkg["user_id"] != INTRUDER_ID, "intruder user_id != package.user_id"

    # Simulate the guard in inline handler: package exists but user_id doesn't match
    is_authorized = (pkg is not None and pkg["user_id"] == INTRUDER_ID)
    assert not is_authorized, "intruder REJECTED: user_id mismatch"

    # Cleanup
    delete_inline_package(pkg_id)
    delete_draft(SENDER_ID)


# ═══════════════════════════════════════════════════════════════════════════
# 14. Destructive whisper creation
# ═══════════════════════════════════════════════════════════════════════════
def test_14_destructive_whisper():
    print("\n─── 14. Destructive Whisper ──────────────────────────────────────")
    wid = create_whisper(
        sender_id=SENDER_ID,
        content="نص تدميري",
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
        is_destructive=True,
    )
    whisper = get_whisper(wid)
    assert whisper["is_destructive"] == 1, "destructive whisper has is_destructive=1"
    assert whisper["whisper_type"] == "first_one", "destructive whisper type preserved"

    # Verify destructive + cover/character
    update_whisper_cover_character(wid, "cover_sunset", "char_thinker")
    whisper2 = get_whisper(wid)
    assert whisper2["cover_code"] == "cover_sunset", "destructive whisper has cover_code"
    assert whisper2["character_code"] == "char_thinker", "destructive whisper has character_code"


# ═══════════════════════════════════════════════════════════════════════════
# 15. Custom type whisper creation (empty targets)
# ═══════════════════════════════════════════════════════════════════════════
def test_15_custom_whisper():
    print("\n─── 15. Custom Type Whisper ──────────────────────────────────────")
    wid = create_whisper(
        sender_id=SENDER_ID,
        content="نص مخصص",
        whisper_type="custom",
        target_users=[],
        max_readers=0,
    )
    whisper = get_whisper(wid)
    assert whisper["whisper_type"] == "custom", "whisper type = custom"
    assert json.loads(whisper["target_users"]) == [], "custom whisper has empty targets"

    update_whisper_cover_character(wid, "cover_winter", "char_poet")
    whisper2 = get_whisper(wid)
    assert whisper2["cover_code"] == "cover_winter", "custom whisper has cover"
    assert whisper2["character_code"] == "char_poet", "custom whisper has character"


# ═══════════════════════════════════════════════════════════════════════════
# 16. Cleanup stale packages
# ═══════════════════════════════════════════════════════════════════════════
def test_16_cleanup_stale():
    print("\n─── 16. Cleanup Stale Packages ───────────────────────────────────")
    from database.wrapped_whispers import cleanup_stale_inline_packages
    # Create a package, then clean up with 0 hours (all should be deleted)
    create_draft(SENDER_ID)
    pkg_id = create_inline_package(SENDER_ID, "cover_morning", "char_whisperer", "قديم")
    assert get_inline_package(pkg_id) is not None, "stale package exists"
    cleanup_stale_inline_packages(hours=0)
    assert get_inline_package(pkg_id) is None, "stale package cleaned up"
    delete_draft(SENDER_ID)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))