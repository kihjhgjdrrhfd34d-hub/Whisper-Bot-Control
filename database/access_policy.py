"""
database/access_policy.py — Centralised whisper access-policy helpers.

This is the single source of truth for the *pure* access decisions that both
the SQLite (database/replies.py) and the PostgreSQL (database/pg_replies.py)
adapter layers share. It holds no DB access and no SQL on purpose, so the
policy cannot drift between backends.

What lives here
---------------
* ``actor_role`` — classify a user against a whisper as
  ``"sender" | "reader" | "outsider"``.  This is the canonical participant
  check used by the reply authorisation and by ``is_own_whisper``.
* ``is_participant_role`` — whether a classified role may take part.
* ``reply_gate`` — the reply authorisation decision given a role, the current
  reply count and the cap.  Reproduces, line for line, the participant/cap
  branch of ``can_reply_to_whisper`` (see database/replies.py).

Behaviour contract
------------------
This module is read-only about existing behaviour.  Every rule here is lifted
verbatim from the current implementation; it introduces no new access rule
and must keep the exact same (result, reason) pairs as the functions it
serves.

Notable rules preserved:
* Closing/locking a whisper NEVER disables replies — it only stops new reads.
  An authorised participant (sender or an existing reader) keeps the ability
  to reply even after the whisper is closed / locked.  For that reason the
  closed/locked flags are intentionally NOT part of ``reply_gate``.
* Read permission stays separate from reply permission.  The read gate is the
  per-backend ``can_read_whisper`` (database/__init__.py and pg_core.py);
  this module does not re-implement it, so the two never drift.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Actor classification
# ─────────────────────────────────────────────────────────────────────────────

def actor_role(sender_id: int, user_id: int, is_reader: bool = False) -> str:
    """Classify ``user_id`` against a whisper owned by ``sender_id``.

    Returns
    -------
    "sender"   — the whisper sender (always authoritative, even if the sender
                 also appears in whisper_readers).
    "reader"   — an authorised reader (present in whisper_readers).
    "outsider" — neither sender nor an existing reader.

    The sender check wins, mirroring the ordering in ``can_reply_to_whisper``:
    the reader lookup is only meaningful when the user is not the sender.
    """
    if user_id == sender_id:
        return "sender"
    if is_reader:
        return "reader"
    return "outsider"


def is_participant_role(role: str) -> bool:
    """True when a classified role is allowed to take part in the whisper."""
    return role in ("sender", "reader")


# ─────────────────────────────────────────────────────────────────────────────
# Reply authorisation gate
# ─────────────────────────────────────────────────────────────────────────────

def reply_gate(role: str, reply_count: int, max_replies: int):
    """Decide whether a classified role may send a reply to a whisper.

    Mirrors ``can_reply_to_whisper`` exactly:
    - Outsiders cannot reply, regardless of whisper state.
    - Participants are blocked only when the reply cap is reached.
    - The closed/locked state is deliberately ignored: closing/locking stops
      new reads, never replies from authorised participants.

    Returns (True, "ok") or (False, reason_string).
    """
    if not is_participant_role(role):
        return False, "not_participant"
    if reply_count >= max_replies:
        return False, "reply_cap_reached"
    return True, "ok"
