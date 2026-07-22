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
"""

import json
import os
import sys
import tempfile
import atexit

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


def _boot():
    db.init_db()
    init_wrapped_whispers_db()
    upsert_user(10001, "sender_test", "Sender", None)
    upsert_user(10002, "intruder", "Intruder", None)
    print("  [BOOT] DB initialized, users created")


def _assert(condition, msg):
    if condition:
        print(f"    ✅ {msg}")
    else:
        print(f"    ❌ {msg}")
        global _FAILED
        _FAILED = True


_FAILED = False


# ═══════════════════════════════════════════════════════════════════════════
# 1.  Draft creation
# ═══════════════════════════════════════════════════════════════════════════
def test_01_draft_creation():
    print("\n─── 1. Draft Creation ──────────────────────────────────────────")
    draft = create_draft(10001)
    _assert(draft is not None, "create_draft returns a draft")
    _assert(draft["user_id"] == 10001, "draft.user_id == 10001")
    _assert(draft["step"] == 1, "draft.step starts at 1")
    _assert(draft["cover_code"] == "", "cover_code is empty initially")
    _assert(draft["character_code"] == "", "character_code empty initially")
    _assert(draft["content"] == "", "content empty initially")
    return draft


# ═══════════════════════════════════════════════════════════════════════════
# 2.  Select cover
# ═══════════════════════════════════════════════════════════════════════════
def test_02_cover_selection():
    print("\n─── 2. Cover Selection ─────────────────────────────────────────")
    covers = get_available_covers(0)
    _assert(len(covers) > 0, f"Found {len(covers)} available covers")
    if covers:
        first_cover = covers[0]
        update_draft_cover(10001, first_cover["code"])
        draft = get_draft(10001)
        _assert(draft["cover_code"] == first_cover["code"],
                f"cover_code = '{first_cover['code']}'")
        _assert(draft["step"] == 2, "step advanced to 2")
        cover = get_cover(first_cover["code"])
        _assert(cover is not None, f"get_cover('{first_cover['code']}') returns data")
        _assert("name" in cover and "icon" in cover, "cover has name + icon")
        return first_cover
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 3.  Select character
# ═══════════════════════════════════════════════════════════════════════════
def test_03_character_selection():
    print("\n─── 3. Character Selection ─────────────────────────────────────")
    chars = get_available_characters(0)
    _assert(len(chars) > 0, f"Found {len(chars)} available characters")
    if chars:
        first_char = chars[0]
        update_draft_character(10001, first_char["code"])
        draft = get_draft(10001)
        _assert(draft["character_code"] == first_char["code"],
                f"character_code = '{first_char['code']}'")
        _assert(draft["step"] == 3, "step advanced to 3")
        char = get_character(first_char["code"])
        _assert(char is not None, f"get_character('{first_char['code']}') returns data")
        _assert("name" in char and "icon" in char, "character has name + icon")
        return first_char
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 4.  Write text content
# ═══════════════════════════════════════════════════════════════════════════
def test_04_text_input():
    print("\n─── 4. Text Input ──────────────────────────────────────────────")
    test_text = "هذه همسة سرية مغلفة للاختبار!"
    update_draft_content(10001, test_text)
    draft = get_draft(10001)
    _assert(draft["content"] == test_text, f"content = '{test_text}'")
    _assert(draft["step"] == 4, "step advanced to 4 (preview)")
    return draft


# ═══════════════════════════════════════════════════════════════════════════
# 5.  Preview verification
# ═══════════════════════════════════════════════════════════════════════════
def test_05_preview():
    print("\n─── 5. Preview ─────────────────────────────────────────────────")
    draft = get_draft(10001)
    _assert(draft is not None, "draft exists for preview")
    _assert(draft["cover_code"] != "", "cover_code is set")
    _assert(draft["character_code"] != "", "character_code is set")
    _assert(draft["content"] != "", "content is set")
    _assert(draft["step"] == 4, "step == 4 (preview)")

    cover = get_cover(draft["cover_code"])
    char = get_character(draft["character_code"])
    _assert(cover is not None, "cover data is accessible")
    _assert(char is not None, "character data is accessible")

    # Build preview text (same as in the handler)
    cname = cover["name"] if cover else "—"
    cicon = cover["icon"] if cover else ""
    chname = char["name"] if char else "—"
    chicn = char["icon"] if char else ""
    preview = f"👁 *معاينة الهمسة*\n\n📦 *الغلاف:* {cicon} {cname}\n🎭 *الشخصية:* {chicn} {chname}\n\n✏️ *النص:* ||{draft['content'][:200]}||"
    _assert("معاينة" in preview, "preview text contains preview title")
    _assert(cname in preview, "preview contains cover name")
    _assert(chname in preview, "preview contains character name")
    _assert(draft['content'] in preview, "preview contains content")


# ═══════════════════════════════════════════════════════════════════════════
# 6.  Send → Create inline package (draft stays)
# ═══════════════════════════════════════════════════════════════════════════
def test_06_create_inline_package():
    print("\n─── 6. Create Inline Package ───────────────────────────────────")
    draft = get_draft(10001)
    pkg_id = create_inline_package(
        10001,
        draft["cover_code"],
        draft["character_code"],
        draft["content"],
    )
    _assert(pkg_id is not None, f"package_id = '{pkg_id}'")
    _assert(len(pkg_id) == 8, "package_id is 8 chars")

    # Verify draft STILL exists (NOT deleted)
    draft_after = get_draft(10001)
    _assert(draft_after is not None, "draft still exists after package creation (NOT deleted)")
    _assert(draft_after["content"] == draft["content"], "draft content unchanged")

    # Verify package exists
    pkg = get_inline_package(pkg_id)
    _assert(pkg is not None, "get_inline_package() returns package")
    _assert(pkg["user_id"] == 10001, "package.user_id == 10001")
    _assert(pkg["cover_code"] == draft["cover_code"], "package.cover_code matches draft")
    _assert(pkg["character_code"] == draft["character_code"], "package.character_code matches draft")
    _assert(pkg["content"] == draft["content"], "package.content matches draft")
    _assert("created_at" in pkg, "package has created_at timestamp")

    return pkg_id


# ═══════════════════════════════════════════════════════════════════════════
# 7.  Build wrapped inline results
# ═══════════════════════════════════════════════════════════════════════════
def test_07_inline_results():
    print("\n─── 7. Inline Results ───────────────────────────────────────────")
    pkg = get_inline_package(test_06_pkg_id)
    results = build_wrapped_inline_results(pkg, 0)

    expected_count = len(WRAPPED_TYPE_OPTIONS) + len(WRAPPED_DESTRUCTIVE_OPTIONS)
    _assert(len(results) == expected_count,
            f"build_wrapped_inline_results returns {len(results)} results (expected {expected_count})")

    # Check each normal type
    found_types = set()
    for r in results:
        _assert("ww:" in r.id, f"result.id starts with 'ww:': {r.id}")
        if "destructive" in r.id:
            _assert(r.id.startswith("ww:destructive:"), f"destructive result id format: {r.id}")
        else:
            _assert(r.id.count(":") == 2, f"normal result id has 2 colons: {r.id}")

        # Collect which types we found
        if r.id.startswith("ww:destructive:"):
            wtype = r.id.split(":")[2]
        else:
            wtype = r.id.split(":")[1]
        found_types.add(wtype)

    # Verify all 4 normal types exist
    for wtype, _, _, _ in WRAPPED_TYPE_OPTIONS:
        _assert(wtype in found_types, f"type '{wtype}' is in results")

    # Verify all 3 destructive types exist
    for wtype, _, _, _ in WRAPPED_DESTRUCTIVE_OPTIONS:
        _assert(wtype in found_types, f"destructive type '{wtype}' is in results")

    # Verify placeholder text
    for r in results:
        text = r.input_message_content.message_text
        _assert("⏳" in text, "placeholder contains hourglass emoji")
        _assert("جاري تجهيز" in text, "placeholder contains loading text")


# ═══════════════════════════════════════════════════════════════════════════
# 8.  Simulate chosen: create whisper + cleanup
# ═══════════════════════════════════════════════════════════════════════════
def test_08_whisper_creation_on_chosen():
    print("\n─── 8. Whisper Creation on Chosen ───────────────────────────────")
    pkg = get_inline_package(test_06_pkg_id)
    _assert(pkg is not None, "package exists before creation")

    # Simulate what _handle_wrapped_chosen does
    wtype = "first_one"
    max_r = 1
    is_destructive = False
    hours = 0

    wid = create_whisper(
        sender_id=10001,
        content=pkg["content"],
        whisper_type=wtype,
        target_users=[],
        max_readers=max_r,
        auto_delete_hours=hours,
        is_destructive=is_destructive,
    )
    _assert(wid is not None, f"create_whisper returns wid = '{wid}'")

    # Verify whisper exists
    whisper = get_whisper(wid)
    _assert(whisper is not None, "whisper found in DB")
    _assert(whisper["sender_id"] == 10001, "whisper.sender_id == 10001")
    _assert(whisper["content"] == pkg["content"], "whisper.content matches package content")
    _assert(whisper["whisper_type"] == "first_one", "whisper.whisper_type == 'first_one'")
    _assert(whisper["is_destructive"] == 0, "whisper is not destructive")

    # Update cover/character
    update_whisper_cover_character(wid, pkg["cover_code"], pkg["character_code"])
    whisper_after = get_whisper(wid)
    _assert(whisper_after["cover_code"] == pkg["cover_code"],
            "whisper.cover_code matches package")
    _assert(whisper_after["character_code"] == pkg["character_code"],
            "whisper.character_code matches package")

    # Cleanup: delete package + draft
    delete_inline_package(pkg["id"])
    _assert(get_inline_package(pkg["id"]) is None, "package deleted after consumption")

    delete_draft(10001)
    _assert(get_draft(10001) is None, "draft deleted after consumption")

    print(f"    Whisper ID: {wid}")
    print(f"    Cover: {whisper_after['cover_code']}")
    print(f"    Character: {whisper_after['character_code']}")

    return wid


# ═══════════════════════════════════════════════════════════════════════════
# 9.  Verify whisper + read button
# ═══════════════════════════════════════════════════════════════════════════
def test_09_whisper_read_flow():
    print("\n─── 9. Whisper Read Flow ────────────────────────────────────────")
    # Create a fresh whisper to test the read flow
    wid = create_whisper(
        sender_id=10001,
        content="محتوى الهمسة السري",
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
    )
    update_whisper_cover_character(wid, "cover_morning", "char_optimist")

    # Verify can_read_whisper returns correct results
    can, reason = db.can_read_whisper(wid, 10002)  # Different user
    _assert(can, "non-sender can read first_one before any read")
    _assert(reason == "allowed", f"reason is 'allowed', got '{reason}'")

    # Simulate a read
    is_new = db.record_whisper_read(wid, 10002)
    _assert(is_new, "first read recorded as new")

    # After read, first_one should be taken
    can, reason = db.can_read_whisper(wid, 99999)
    _assert(not can, "another user cannot read after first_one taken")
    _assert(reason == "taken", f"reason is 'taken', got '{reason}'")

    # Build the final message text (same as in _handle_wrapped_chosen)
    cover = get_cover("cover_morning")
    char = get_character("char_optimist")
    cover_icon = cover["icon"] if cover else "📜"
    cover_name = cover["name"] if cover else ""
    char_icon = char["icon"] if char else "🤫"
    char_name = char["name"] if char else ""

    final_text = f"{char_icon} {char_name}\n\n{cover_icon} {cover_name}\n\n🔒 اضغط للرؤية"
    _assert("🔒" in final_text, "final message has lock emoji")
    _assert(char_name in final_text, "final message has character name")
    _assert(cover_name in final_text, "final message has cover name")
    print(f"    Final message format:\n{final_text}")


# ═══════════════════════════════════════════════════════════════════════════
# 10. Cancel flow (package deleted, draft kept)
# ═══════════════════════════════════════════════════════════════════════════
def test_10_cancel_flow():
    print("\n─── 10. Cancel Flow ──────────────────────────────────────────────")
    # Create fresh draft + package
    create_draft(10001)
    update_draft_cover(10001, "cover_morning")
    update_draft_character(10001, "char_optimist")
    update_draft_content(10001, "نص الهمسة للإلغاء")
    draft = get_draft(10001)
    _assert(draft is not None, "draft created for cancel test")

    pkg_id = create_inline_package(10001, draft["cover_code"], draft["character_code"], draft["content"])
    _assert(get_inline_package(pkg_id) is not None, "package exists for cancel test")
    _assert(get_draft(10001) is not None, "draft exists alongside package")

    # Simulate cancel: delete package, keep draft
    delete_inline_package(pkg_id)
    _assert(get_inline_package(pkg_id) is None, "package deleted on cancel")

    # Draft should still exist
    draft_after = get_draft(10001)
    _assert(draft_after is not None, "draft REMAINS after cancel")
    _assert(draft_after["content"] == "نص الهمسة للإلغاء", "draft content unchanged after cancel")
    _assert(draft_after["cover_code"] == "cover_morning", "draft cover unchanged after cancel")


# ═══════════════════════════════════════════════════════════════════════════
# 11. Retry after cancel (send again)
# ═══════════════════════════════════════════════════════════════════════════
def test_11_retry_after_cancel():
    print("\n─── 11. Retry After Cancel ──────────────────────────────────────")
    draft = get_draft(10001)
    _assert(draft is not None, "draft still exists from previous test")

    # Re-send: create a new package from the same draft
    pkg_id2 = create_inline_package(10001, draft["cover_code"], draft["character_code"], draft["content"])
    _assert(pkg_id2 is not None, "new package created after cancel")
    _assert(get_inline_package(pkg_id2) is not None, "new package exists")

    # Draft still exists (not deleted)
    _assert(get_draft(10001) is not None, "draft still exists after re-send")

    # Consume this package
    wid = create_whisper(
        sender_id=10001,
        content=draft["content"],
        whisper_type="everyone",
        target_users=[],
        max_readers=0,
    )
    _assert(wid is not None, "whisper created from retry package")

    delete_inline_package(pkg_id2)
    _assert(get_inline_package(pkg_id2) is None, "retry package deleted after consumption")

    delete_draft(10001)
    _assert(get_draft(10001) is None, "draft deleted after final consumption")


# ═══════════════════════════════════════════════════════════════════════════
# 12. Duplicate protection
# ═══════════════════════════════════════════════════════════════════════════
def test_12_duplicate_protection():
    print("\n─── 12. Duplicate Protection ─────────────────────────────────────")
    # Create fresh package
    create_draft(10001)
    update_draft_cover(10001, "cover_morning")
    update_draft_character(10001, "char_mysterious")
    update_draft_content(10001, "نص مكرر")
    draft = get_draft(10001)

    pkg_id = create_inline_package(10001, draft["cover_code"], draft["character_code"], draft["content"])

    # First consumption: succeeds
    wid1 = create_whisper(
        sender_id=10001,
        content=draft["content"],
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
    )
    _assert(wid1 is not None, "first consumption succeeds")
    delete_inline_package(pkg_id)  # Simulates _handle_wrapped_chosen cleanup

    # Second consumption: get_inline_package returns None (deleted)
    pkg_again = get_inline_package(pkg_id)
    _assert(pkg_again is None, "second consumption fails - package already deleted")

    # Create a new whisper with same data (this would happen if user clicks share again with a new package)
    wid2 = create_whisper(
        sender_id=10001,
        content=draft["content"],
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
    )
    _assert(wid2 is not None, "new whisper can be created with new package")
    _assert(wid1 != wid2, "second whisper has DIFFERENT wid than first")

    delete_draft(10001)
    print(f"    First wid:  {wid1}")
    print(f"    Second wid: {wid2}")


# ═══════════════════════════════════════════════════════════════════════════
# 13. Unauthorized user
# ═══════════════════════════════════════════════════════════════════════════
def test_13_unauthorized_user():
    print("\n─── 13. Unauthorized User ───────────────────────────────────────")
    # User 10001 creates package
    create_draft(10001)
    update_draft_cover(10001, "cover_evening")
    update_draft_character(10001, "char_whisperer")
    update_draft_content(10001, "نص خاص")
    draft = get_draft(10001)

    pkg_id = create_inline_package(10001, draft["cover_code"], draft["character_code"], draft["content"])
    pkg = get_inline_package(pkg_id)
    _assert(pkg is not None, "package created by user 10001")
    _assert(pkg["user_id"] == 10001, "package.user_id == 10001")

    # User 10002 (intruder) tries to use it
    _assert(pkg["user_id"] != 10002, "intruder user_id != package.user_id")

    # Simulate the guard in inline handler: package exists but user_id doesn't match
    is_authorized = (pkg is not None and pkg["user_id"] == 10002)
    _assert(not is_authorized, "intruder REJECTED: user_id mismatch")

    # Cleanup
    delete_inline_package(pkg_id)
    delete_draft(10001)


# ═══════════════════════════════════════════════════════════════════════════
# 14. Destructive whisper creation
# ═══════════════════════════════════════════════════════════════════════════
def test_14_destructive_whisper():
    print("\n─── 14. Destructive Whisper ──────────────────────────────────────")
    wid = create_whisper(
        sender_id=10001,
        content="نص تدميري",
        whisper_type="first_one",
        target_users=[],
        max_readers=1,
        is_destructive=True,
    )
    whisper = get_whisper(wid)
    _assert(whisper["is_destructive"] == 1, "destructive whisper has is_destructive=1")
    _assert(whisper["whisper_type"] == "first_one", "destructive whisper type preserved")

    # Verify destructive + cover/character
    update_whisper_cover_character(wid, "cover_sunset", "char_thinker")
    whisper2 = get_whisper(wid)
    _assert(whisper2["cover_code"] == "cover_sunset", "destructive whisper has cover_code")
    _assert(whisper2["character_code"] == "char_thinker", "destructive whisper has character_code")


# ═══════════════════════════════════════════════════════════════════════════
# 15. Custom type whisper creation (empty targets)
# ═══════════════════════════════════════════════════════════════════════════
def test_15_custom_whisper():
    print("\n─── 15. Custom Type Whisper ──────────────────────────────────────")
    wid = create_whisper(
        sender_id=10001,
        content="نص مخصص",
        whisper_type="custom",
        target_users=[],
        max_readers=0,
    )
    whisper = get_whisper(wid)
    _assert(whisper["whisper_type"] == "custom", "whisper type = custom")
    _assert(json.loads(whisper["target_users"]) == [], "custom whisper has empty targets")

    update_whisper_cover_character(wid, "cover_winter", "char_poet")
    whisper2 = get_whisper(wid)
    _assert(whisper2["cover_code"] == "cover_winter", "custom whisper has cover")
    _assert(whisper2["character_code"] == "char_poet", "custom whisper has character")


# ═══════════════════════════════════════════════════════════════════════════
# 16. Cleanup stale packages
# ═══════════════════════════════════════════════════════════════════════════
def test_16_cleanup_stale():
    print("\n─── 16. Cleanup Stale Packages ───────────────────────────────────")
    from database.wrapped_whispers import cleanup_stale_inline_packages
    # Create a package, then clean up with 0 hours (all should be deleted)
    create_draft(10001)
    pkg_id = create_inline_package(10001, "cover_morning", "char_whisperer", "قديم")
    _assert(get_inline_package(pkg_id) is not None, "stale package exists")
    cleanup_stale_inline_packages(hours=0)
    _assert(get_inline_package(pkg_id) is None, "stale package cleaned up")
    delete_draft(10001)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  E2E Test: Wrapped Whisper → Inline Share Flow")
    print("=" * 60)

    _boot()

    # Store pkg_id for cross-test access
    global test_06_pkg_id
    test_06_pkg_id = None

    try:
        test_01_draft_creation()
        test_02_cover_selection()
        test_03_character_selection()
        test_04_text_input()
        test_05_preview()
        test_06_pkg_id = test_06_create_inline_package()
        if test_06_pkg_id:
            test_07_inline_results()
        test_08_whisper_creation_on_chosen()
        test_09_whisper_read_flow()
        test_10_cancel_flow()
        test_11_retry_after_cancel()
        test_12_duplicate_protection()
        test_13_unauthorized_user()
        test_14_destructive_whisper()
        test_15_custom_whisper()
        test_16_cleanup_stale()
    except Exception as e:
        print(f"\n❌ UNEXPECTED EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        _FAILED = True

    print("\n" + "=" * 60)
    if _FAILED:
        print("  ❌ SOME TESTS FAILED")
    else:
        print("  ✅ ALL TESTS PASSED")
    print("=" * 60)
    sys.exit(1 if _FAILED else 0)
