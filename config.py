import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

DATABASE_PATH = os.getenv("DATABASE_PATH", "whispers.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
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
    "notify_returning_user": "1",
    "notify_block": "1",
    "antispam_enabled": "1",
    "xp_enabled": "1",
    "auto_backup_enabled": "0",
    "whisper_replies_enabled": "1",
    "mandatory_channels": "",
}

GROUP_DEFAULT_SETTINGS = {
    "public_whispers_enabled": "1",
    "anonymous_enabled": "1",
    "read_notifications": "1",
    "auto_delete_minutes": "0",
    "spam_limit_enabled": "1",
    "spam_limit_count": "5",
    "spam_limit_window_seconds": "60",
}
