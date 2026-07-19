"""
Full flow trace — NO pre-calls to add_reader_if_new.
Simulates real handle_read flow exactly.
"""

import logging
import sys
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger("FLOW_TRACE")

SENDER_ID = 99999
READER_IDS = [1001, 1002, 1003]

_ph_texts = {
    ("first_one", False): "هذه همسه سريه لاول شخص يقوم بقرأتها",
    ("first_one", True): "💣 همسة تدميرية لشخص واحد",
    ("first_three", False): "هذه همسه سريه لاول ثلاثة أشخاص يقومون بقرأتها",
    ("first_three", True): "💣 همسة تدميرية لـ 3 أشخاص",
}


def _simulate_update_keyboard(whisper_id, w, readers):
    from services.whisper_service import get_reader_display_name
    if not isinstance(w, dict):
        w = dict(w)
    reader_count_val = len(readers)
    wtype = w["whisper_type"]

    show_reader_names = False
    if wtype == "first_one" and reader_count_val > 0:
        show_reader_names = True
    elif wtype == "first_three" and reader_count_val >= 3:
        show_reader_names = True

    kb = InlineKeyboardMarkup(row_width=2)

    if wtype == "everyone":
        kb.add(InlineKeyboardButton("تم قراءة الهمسة 🔓", callback_data=f"opened:{whisper_id}"))
    elif wtype == "first_three":
        if reader_count_val >= 3:
            kb.add(InlineKeyboardButton("تم قراءة الهمسة 🔓", callback_data=f"opened:{whisper_id}"))
        else:
            kb.add(InlineKeyboardButton("اضغط للرؤية 🔒", callback_data=f"read:{whisper_id}"))
    else:
        kb.add(InlineKeyboardButton("تم قراءة الهمسة 🔓", callback_data=f"opened:{whisper_id}"))

    if show_reader_names:
        max_names = 3 if wtype == "first_three" else len(readers)
        names_added = []
        for r in readers[:max_names]:
            name = get_reader_display_name(r)
            names_added.append(name)
            kb.add(InlineKeyboardButton(f"👤 {name}", callback_data="noop"))
        logger.info("[UI] reader_names_added=%s", names_added)

    is_opened = (
        wtype == "everyone" or
        wtype == "first_one" or
        (wtype == "first_three" and reader_count_val >= 3)
    )
    if is_opened:
        kb.add(
            InlineKeyboardButton("❤️ 0", callback_data=f"like:{whisper_id}"),
            InlineKeyboardButton("👎 0", callback_data=f"dislike:{whisper_id}"),
        )

    new_text = None
    if show_reader_names:
        lines = ["👀 فتحها:"]
        max_names = 3 if wtype == "first_three" else len(readers)
        for r in readers[:max_names]:
            name = get_reader_display_name(r)
            lines.append(f"• {name}")
        reader_section = "\n".join(lines)
        placeholder = _ph_texts.get((wtype, w.get("is_destructive", False)), "")
        new_text = placeholder + "\n\n" + reader_section if placeholder else reader_section

    kb_rows = []
    for row in kb.keyboard:
        kb_rows.append([btn.text for btn in row])
    logger.info("[UI] KEYBOARD rows=%s", kb_rows)
    if new_text:
        logger.info("[UI] new_text=%s", repr(new_text))

    return reader_count_val


def _simulate_maybe_destruct(whisper_id, w, is_destructive, is_new_read, reader_count_val):
    logger.info("")
    logger.info("========== [DESTROY] _maybe_self_destruct ==========")
    wtype_str = w["whisper_type"] if isinstance(w, dict) else dict(w).get("whisper_type", "?")
    logger.info("[DESTROY] whisper_id=%s type=%s is_destructive=%s is_new_read=%s reader_count_val=%d",
                whisper_id, wtype_str, is_destructive, is_new_read, reader_count_val)
    if not (is_destructive and is_new_read):
        logger.info("[DESTROY] SKIP: is_destructive=%s is_new_read=%s", is_destructive, is_new_read)
        return
    if w["whisper_type"] == "first_one":
        logger.info("[DESTROY] WILL DESTROY (first_one)")
    elif w["whisper_type"] == "first_three" and reader_count_val >= 3:
        logger.info("[DESTROY] WILL DESTROY (first_three count=%d)", reader_count_val)
    elif w["whisper_type"] == "everyone":
        logger.info("[DESTROY] WILL DESTROY (everyone)")
    else:
        logger.info("[DESTROY] No destroy: type=%s count=%d", w["whisper_type"], reader_count_val)


def run_scenario(label, wid, reader_ids, expected_destroy_at=None):
    from database import get_whisper, get_readers, reader_count
    from services.whisper_service import record_read_and_check, is_destructive_whisper

    for i, uid in enumerate(reader_ids, 1):
        logger.info("")
        logger.info("── Reader %d/%d uid=%d ──", i, len(reader_ids), uid)

        is_new_read, is_first_ever = record_read_and_check(wid, uid)
        rcount = reader_count(wid)
        readers = get_readers(wid)
        w = get_whisper(wid)
        is_destructive = is_destructive_whisper(w)
        wtype = dict(w).get("whisper_type", "?")

        logger.info("[DB] record_read_and_check=(%s, %s) reader_count=%d type=%s is_destructive=%s",
                    is_new_read, is_first_ever, rcount, wtype, is_destructive)

        if is_new_read:
            rcv = _simulate_update_keyboard(wid, w, readers)
            _simulate_maybe_destruct(wid, w, is_destructive, is_new_read, rcv)

    w_final = get_whisper(wid)
    logger.info("")
    logger.info("-- Final state --")
    reader_count_final = reader_count(wid)
    readers_final = get_readers(wid)
    logger.info("reader_count=%d readers=%s", reader_count_final,
                [(r["user_id"], r.get("username")) for r in readers_final])
    if w_final:
        wd = dict(w_final)
        logger.info("DB still has: is_locked=%s content_preview=%s",
                    wd.get("is_locked"), str(wd.get("content", ""))[:40])
    else:
        logger.info("whisper deleted from DB")
    from database import delete_whisper
    delete_whisper(wid)
    logger.info("✅ %s complete", label)


if __name__ == "__main__":
    from database import upsert_user, create_whisper
    upsert_user(SENDER_ID, "sender", "Sender", None)
    for uid in READER_IDS:
        upsert_user(uid, f"reader{uid%10}", f"User{uid}", None)

    # ── Scenario 1: first_three normal ──
    logger.info("")
    logger.info("╔════════════════════════════════════════════════╗")
    logger.info("║  SCENARIO 1: first_three NORMAL               ║")
    logger.info("╚════════════════════════════════════════════════╝")
    wid1 = create_whisper(sender_id=SENDER_ID, content="Normal first_three",
                          whisper_type="first_three", target_users=[], max_readers=3)
    run_scenario("Scenario 1", wid1, READER_IDS)

    # ── Scenario 2: first_three destructive ──
    logger.info("")
    logger.info("╔════════════════════════════════════════════════╗")
    logger.info("║  SCENARIO 2: first_three DESTRUCTIVE          ║")
    logger.info("╚════════════════════════════════════════════════╝")
    wid2 = create_whisper(sender_id=SENDER_ID, content="Destructive first_three",
                          whisper_type="first_three", target_users=[], max_readers=3,
                          is_destructive=True)
    run_scenario("Scenario 2", wid2, READER_IDS, expected_destroy_at=3)

    # ── Scenario 3: first_one destructive ──
    logger.info("")
    logger.info("╔════════════════════════════════════════════════╗")
    logger.info("║  SCENARIO 3: first_one DESTRUCTIVE            ║")
    logger.info("╚════════════════════════════════════════════════╝")
    wid3 = create_whisper(sender_id=SENDER_ID, content="Destructive first_one",
                          whisper_type="first_one", target_users=[READER_IDS[0]],
                          max_readers=1, is_destructive=True)
    run_scenario("Scenario 3", wid3, [READER_IDS[0]])

    # ── Scenario 4: everyone destructive ──
    logger.info("")
    logger.info("╔════════════════════════════════════════════════╗")
    logger.info("║  SCENARIO 4: everyone DESTRUCTIVE             ║")
    logger.info("╚════════════════════════════════════════════════╝")
    wid4 = create_whisper(sender_id=SENDER_ID, content="Destructive everyone",
                          whisper_type="everyone", target_users=[], max_readers=0,
                          is_destructive=True)
    run_scenario("Scenario 4", wid4, [READER_IDS[0]])

    logger.info("")
    logger.info("╔════════════════════════════════════════════════╗")
    logger.info("║  ALL SCENARIOS COMPLETE                       ║")
    logger.info("╚════════════════════════════════════════════════╝")
