<<<<<<< HEAD
"""
config.py — Whisper Bot Enterprise configuration.

Loading order:
  1. If python-dotenv is installed, load .env from the project root
     (used for local dev, Termux, self-hosted VPS).
  2. Environment variables always win over .env values.
  3. On Render (and any cloud host) just set the env vars — no .env needed.

Critical variables are validated at startup by core/config_validator.py.
"""
import os
from pathlib import Path

# ── Optional .env file support ────────────────────────────────────────────────
# Silently skipped if python-dotenv is not installed (e.g. Render uses raw env vars).
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)  # env vars take precedence
except ImportError:
    pass  # dotenv not installed — environment variables must be set externally

# ── Core settings ─────────────────────────────────────────────────────────────
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")          # empty string → validator raises
ADMIN_IDS       = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]
DATABASE_PATH   = os.getenv("DATABASE_PATH", "whispers.db")
KEEP_ALIVE_PORT = int(os.getenv("KEEP_ALIVE_PORT", "8080"))

# ── Default settings (stored in the DB settings table) ───────────────────────
DEFAULT_SETTINGS = {
    # ── Core bot behaviour ─────────────────────────────────────────────────
    "bot_active":             "1",
    "membership_check":       "0",
    "content_protection":     "0",
    # ── Read receipts ──────────────────────────────────────────────────────
    "notifications":          "1",   # legacy key kept for compatibility
    "read_receipt_enabled":   "1",
    # ── Auto-delete ────────────────────────────────────────────────────────
    "auto_delete_enabled":    "0",
    "auto_delete_hours":      "24",
    # ── Admin notification toggles ─────────────────────────────────────────
    "notify_new_user":        "1",
    "notify_block":           "1",
    # ── Enterprise: anti-spam / rate-limit ────────────────────────────────
    "antispam_enabled":       "1",
    # ── Enterprise: XP system ─────────────────────────────────────────────
    "xp_enabled":             "1",
    # ── Enterprise: backup ────────────────────────────────────────────────
    "auto_backup_enabled":    "0",
    # ── Whisper Replies ───────────────────────────────────────────────────
    "whisper_replies_enabled": "1",
    # ── Legacy / misc ─────────────────────────────────────────────────────────
    "mandatory_channels":     "",
=======
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

DATABASE_PATH = os.getenv("DATABASE_PATH", "whispers.db")
KEEP_ALIVE_PORT = int(os.getenv("KEEP_ALIVE_PORT", "8080"))

DEFAULT_SETTINGS = {
    "bot_active": "1",
    "membership_check": "0",
    "content_protection": "0",
    "notifications": "1",
    "read_receipt_enabled": "1",
    "auto_delete_enabled": "0",
    "auto_delete_hours": "24",
    "notify_new_user": "1",
    "notify_block": "1",
    "antispam_enabled": "1",
    "xp_enabled": "1",
    "auto_backup_enabled": "0",
    "whisper_replies_enabled": "1",
    "mandatory_channels": "",
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)
}
