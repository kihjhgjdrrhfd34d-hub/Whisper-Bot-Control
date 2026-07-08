
import logging
import time
from database import init_db
from keep_alive import keep_alive
from bot import bot, register_all_handlers
from scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    logger.info("🚀 تهيئة قاعدة البيانات...")
    init_db()

    logger.info("🌐 تشغيل خادم Keep-Alive...")
    keep_alive()

    logger.info("🤖 تسجيل المعالجات...")
    register_all_handlers()

    logger.info("⏰ تشغيل جدولة الحذف التلقائي...")
    start_scheduler(bot, interval_hours=1)

    logger.info("🔌 إلغاء أي webhook نشط وإنهاء الجلسات المتعارضة...")
    for attempt in range(3):
        try:
            bot.delete_webhook(drop_pending_updates=True)
            logger.info(f"✅ delete_webhook نجح (محاولة {attempt + 1})")
            break
        except Exception as e:
            logger.warning(f"delete_webhook محاولة {attempt + 1}: {e}")
            time.sleep(2)

    logger.info("⏳ انتظار 4 ثوانٍ لانتهاء أي جلسة polling قديمة...")
    time.sleep(4)

    logger.info("✅ البوت يعمل الآن!")
    bot.infinity_polling(
        timeout=60,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"],
        restart_on_change=False,
    )


if __name__ == "__main__":
    main()
