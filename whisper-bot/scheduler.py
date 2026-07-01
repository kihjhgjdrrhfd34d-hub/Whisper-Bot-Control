import time
import logging
from threading import Thread
from datetime import datetime

logger = logging.getLogger(__name__)

_scheduler_thread = None


def _run_scheduler(interval_seconds: int, bot):
    """حلقة الجدولة — تعمل في خيط منفصل إلى الأبد."""
    while True:
        time.sleep(interval_seconds)
        _tick(bot)


def _tick(bot):
    try:
        from database import delete_expired_whispers, get_setting
        if get_setting("auto_delete_enabled") != "1":
            return
        deleted = delete_expired_whispers()
        if deleted > 0:
            logger.info(f"🗑 الحذف التلقائي: تم حذف {deleted} همسة منتهية الصلاحية.")
    except Exception as e:
        logger.error(f"خطأ في دورة الحذف التلقائي: {e}")


def start_scheduler(bot, interval_hours: int = 1):
    """تشغيل الجدولة في خيط خلفي — تُستدعى مرة واحدة فقط."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    interval_seconds = interval_hours * 3600
    logger.info(f"⏰ جدولة الحذف التلقائي كل {interval_hours} ساعة.")
    _scheduler_thread = Thread(
        target=_run_scheduler,
        args=(interval_seconds, bot),
        daemon=True,
        name="WhisperAutoDelete"
    )
    _scheduler_thread.start()
