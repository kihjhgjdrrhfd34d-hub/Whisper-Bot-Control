"""
main.py — Whisper Bot Enterprise entry point.

Boot order:
  1. Logging setup
  2. Config validation
  3. DB init (core + enterprise)
  4. Keep-alive HTTP server
  5. Handler registration (core + enterprise)
  6. Schedulers (core + enterprise)
  7. Web dashboard
  8. Plugin discovery
  9. delete_webhook + polling
"""
import logging
import time

# ── Enterprise logging must be first ─────────────────────────────────────────
from core.logging_config import setup_logging
setup_logging(level="INFO")

from database import init_db
from keep_alive import keep_alive
from bot import bot, register_all_handlers
from scheduler import start_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    # ── 1. Validate config ────────────────────────────────────────────────────
    logger.info("🔍 التحقق من الإعدادات...")
    try:
        from core.config_validator import validate_and_log
        validate_and_log()
    except Exception as exc:
        logger.critical(f"فشل التحقق من الإعدادات: {exc}")
        # Don't hard-crash in dev mode with placeholder token
        logger.warning("متابعة التشغيل بإعدادات افتراضية (وضع التطوير).")

    # ── 2. Core DB init ───────────────────────────────────────────────────────
    logger.info("🚀 تهيئة قاعدة البيانات الأساسية...")
    init_db()

    # ── 3. Enterprise DB init ─────────────────────────────────────────────────
    logger.info("🏢 تهيئة جداول Enterprise...")
    try:
        from enterprise.db_enterprise import init_enterprise_db
        init_enterprise_db()
    except Exception as exc:
        logger.error(f"خطأ في تهيئة Enterprise DB: {exc}", exc_info=True)

    # ── 3b. Replies DB init (after enterprise so migration runs first) ────────
    logger.info("💬 تهيئة جداول الردود على الهمسات...")
    try:
        from database.replies import init_replies_db
        init_replies_db()
    except Exception as exc:
        logger.error(f"خطأ في تهيئة جداول الردود: {exc}", exc_info=True)

    # ── 4. Keep-alive HTTP server ─────────────────────────────────────────────
    logger.info("🌐 تشغيل خادم Keep-Alive (port 8080)...")
    keep_alive()

    # ── 5. Register all handlers ──────────────────────────────────────────────
    logger.info("🤖 تسجيل المعالجات...")
    register_all_handlers()

    # ── 6. Core scheduler ────────────────────────────────────────────────────
    logger.info("⏰ تشغيل جدولة الحذف التلقائي...")
    start_scheduler(bot, interval_hours=1)

    # ── 7. Enterprise scheduler ───────────────────────────────────────────────
    logger.info("🏢 تشغيل جدولة Enterprise...")
    try:
        from enterprise.scheduler_enterprise import start_enterprise_scheduler
        start_enterprise_scheduler(interval_hours=1)
    except Exception as exc:
        logger.error(f"خطأ في جدولة Enterprise: {exc}", exc_info=True)

    # ── 8. Web dashboard ──────────────────────────────────────────────────────
    logger.info("🌐 تشغيل لوحة الويب Enterprise (port 8081)...")
    try:
        from web.app import start_web_dashboard
        start_web_dashboard()
    except Exception as exc:
        logger.error(f"خطأ في تشغيل لوحة الويب: {exc}", exc_info=True)

    # ── 9. Plugin discovery ───────────────────────────────────────────────────
    logger.info("🔌 اكتشاف الإضافات (plugins)...")
    try:
        from core.plugins import plugin_manager
        plugin_manager.discover(bot=bot)
        loaded = plugin_manager.list_plugins()
        logger.info(f"  Plugins loaded: {len(loaded)}")
    except Exception as exc:
        logger.error(f"خطأ في تحميل الإضافات: {exc}", exc_info=True)

    # ── 10. Clear webhook + start polling ────────────────────────────────────
    logger.info("🔌 إلغاء أي webhook نشط...")
    for attempt in range(3):
        try:
            bot.delete_webhook(drop_pending_updates=True)
            logger.info(f"✅ delete_webhook نجح (محاولة {attempt + 1})")
            break
        except Exception as exc:
            logger.warning(f"delete_webhook محاولة {attempt + 1}: {exc}")
            time.sleep(2)

    logger.info("⏳ انتظار 4 ثوانٍ لانتهاء أي جلسة polling قديمة...")
    time.sleep(4)

    logger.info("✅ البوت يعمل الآن! (Enterprise Edition)")
    bot.infinity_polling(
        timeout=60,
        long_polling_timeout=30,
        allowed_updates=[
            "message", "callback_query", "inline_query",
            "chosen_inline_result", "my_chat_member",
        ],
        restart_on_change=False,
    )


if __name__ == "__main__":
    main()
