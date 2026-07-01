import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
DATABASE_PATH = os.getenv("DATABASE_PATH", "whispers.db")
KEEP_ALIVE_PORT = int(os.getenv("KEEP_ALIVE_PORT", "8080"))

DEFAULT_SETTINGS = {
    "bot_active": "1",
    "membership_check": "0",
    "content_protection": "0",
    "notifications": "1",
    "auto_delete_enabled": "0",
    "auto_delete_hours": "24",
    "mandatory_channels": "",
}

