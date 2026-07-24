"""
enterprise/handlers_enterprise.py
───────────────────────────────────
Additive enterprise Telegram handlers.
Registered via register_enterprise_handlers(bot, user_states) called at the end
of bot.register_all_handlers() — zero changes to existing handlers.

New commands:
  /rank        — personal XP & level
  /achievements — earned achievements
  /invite      — referral code & invite link
  /activity    — recent activity log
  /report      — report a whisper
  /search      — search in your whispers
  /backup      — admin: trigger backup
  /reports     — admin: list pending reports
"""
from __future__ import annotations

import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.keyboard_utils import back_button, cancel_button

from handlers._formatting import _fmt_username

logger = logging.getLogger(__name__)


def register_enterprise_handlers(bot: telebot.TeleBot, user_states: dict) -> None:
    """Register all enterprise handlers. Called once from register_all_handlers()."""

    from config import ADMIN_IDS
    from database import upsert_user, get_setting, is_banned
    from enterprise.db_enterprise import (
        get_xp, get_user_achievements, generate_referral_code,
        get_activity_log, create_report, list_backups, create_backup,
        get_reports, review_report, count_reports,
        get_favorites, get_archive, search_whispers,
        xp_leaderboard, check_and_grant_achievements,
        award_xp,
    )
    from core.logging_config import audit_log

    def _is_admin(uid: int) -> bool:
        return uid in ADMIN_IDS

    # ── /rank — personal XP & level ──────────────────────────────────────────
    @bot.message_handler(commands=["rank", "xp", "level"])
    def rank_cmd(msg: telebot.types.Message):
        user = msg.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        if get_setting("bot_active") != "1":
            return

        xp_data = get_xp(user.id)
        achievements = get_user_achievements(user.id)
        newly_granted = check_and_grant_achievements(user.id)

        name = user.first_name or "مستخدم"
        text = (
            f"⭐ *رتبتك*\n\n"
            f"👤 {name}\n"
            f"🏅 المستوى: `{xp_data['level']}`\n"
            f"✨ الرتبة: {xp_data['rank_title']}\n"
            f"💎 نقاط XP: `{xp_data['xp']}`\n\n"
            f"🏆 الإنجازات: `{len(achievements)}`"
        )
        if newly_granted:
            text += f"\n\n🎉 *إنجازات جديدة مفتوحة:* {len(newly_granted)}"

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🏆 إنجازاتي", callback_data="ent:achievements"),
            InlineKeyboardButton("🏅 المتصدرون", callback_data="ent:leaderboard"),
        )
        bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=kb)

    # ── /achievements ─────────────────────────────────────────────────────────
    @bot.message_handler(commands=["achievements", "انجازات"])
    def achievements_cmd(msg: telebot.types.Message):
        user = msg.from_user
        check_and_grant_achievements(user.id)
        _send_achievements(bot, msg.chat.id, user.id)

    @bot.callback_query_handler(func=lambda c: c.data == "ent:achievements")
    def achievements_cb(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        check_and_grant_achievements(call.from_user.id)
        _send_achievements(bot, call.message.chat.id, call.from_user.id)

    @bot.callback_query_handler(func=lambda c: c.data == "ent:leaderboard")
    def leaderboard_cb(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        rows = xp_leaderboard(10)
        lines = ["🏅 *المتصدرون في نقاط XP*\n"]
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i+1}."
            uname = _fmt_username(r["username"]) if r.get("username") else r.get("first_name") or "مجهول"
            lines.append(f"{medal} {uname} — `{r['xp']}` XP  ({r['rank_title']})")
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(back_button("ent:close"))
        bot.send_message(
            call.message.chat.id, "\n".join(lines),
            parse_mode="Markdown", reply_markup=kb,
        )

    # ── /invite — referral ────────────────────────────────────────────────────
    @bot.message_handler(commands=["invite", "دعوة", "referral"])
    def invite_cmd(msg: telebot.types.Message):
        user = msg.from_user
        upsert_user(user.id, user.username, user.first_name, user.last_name)
        code = generate_referral_code(user.id)
        me = bot.get_me()
        link = f"https://t.me/{me.username}?start={code}"
        invites_count = _count_invites(user.id)
        text = (
            f"🎁 *نظام الدعوات*\n\n"
            f"رابط الدعوة الخاص بك:\n`{link}`\n\n"
            f"👥 عدد من دعوتهم: `{invites_count}`\n"
            f"💎 XP مكتسب من الدعوات: `{invites_count * 20}`\n\n"
            f"_كل شخص يدخل عبر رابطك = +20 XP_"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📤 مشاركة الرابط", url=f"https://t.me/share/url?url={link}"))
        bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=kb)

    def _count_invites(user_id: int) -> int:
        from enterprise.db_enterprise import count_invites
        return count_invites(user_id)

    # ── /activity — activity log ──────────────────────────────────────────────
    @bot.message_handler(commands=["activity", "نشاطي"])
    def activity_cmd(msg: telebot.types.Message):
        user = msg.from_user
        log = get_activity_log(user.id, limit=10)
        if not log:
            bot.send_message(msg.chat.id, "📋 لا يوجد نشاط مسجل بعد.")
            return
        lines = ["📋 *سجل نشاطك الأخير*\n"]
        action_icons = {
            "whisper_sent": "📨 أرسلت همسة",
            "whisper_read": "👁 قرأت همسة",
            "whisper_deleted": "🗑 حذفت همسة",
            "login": "🔑 دخول",
            "report": "🚨 بلاغ",
        }
        for entry in log:
            icon_label = action_icons.get(entry["action"], f"• {entry['action']}")
            when = str(entry["created_at"])[:16]
            lines.append(f"{icon_label} — `{when}`")
        bot.send_message(msg.chat.id, "\n".join(lines), parse_mode="Markdown")

    # ── /report whisper_id reason ─────────────────────────────────────────────
    @bot.message_handler(commands=["report", "بلاغ"])
    def report_cmd(msg: telebot.types.Message):
        user = msg.from_user
        parts = msg.text.split(None, 2) if msg.text else []
        if len(parts) < 3:
            bot.send_message(
                msg.chat.id,
                "❗ الاستخدام: `/report <whisper_id> <السبب>`\n"
                "مثال: `/report abc123 محتوى مسيء`",
                parse_mode="Markdown",
            )
            return
        _, whisper_id, reason = parts
        report_id = create_report(user.id, whisper_id, reason)
        from core.logging_config import audit_log as _audit
        _audit("REPORT", actor_id=user.id, whisper_id=whisper_id, reason=reason)
        bot.send_message(
            msg.chat.id,
            f"✅ تم إرسال بلاغك بنجاح\n🆔 رقم البلاغ: `{report_id}`",
            parse_mode="Markdown",
        )
        # Notify admins
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(
                    admin_id,
                    f"🚨 *بلاغ جديد #{report_id}*\n\n"
                    f"🆔 الهمسة: `{whisper_id}`\n"
                    f"📝 السبب: {reason}\n"
                    f"👤 المبلِّغ: `{user.id}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    # ── /search <query> ───────────────────────────────────────────────────────
    @bot.message_handler(commands=["search", "بحث"])
    def search_cmd(msg: telebot.types.Message):
        user = msg.from_user
        parts = msg.text.split(None, 1) if msg.text else []
        if len(parts) < 2:
            bot.send_message(
                msg.chat.id,
                "🔍 الاستخدام: `/search <نص البحث>`",
                parse_mode="Markdown",
            )
            return
        query = parts[1]
        results = search_whispers(user.id, query)
        if not results:
            bot.send_message(msg.chat.id, "❌ لم يتم العثور على همسات تطابق بحثك.")
            return
        lines = [f"🔍 *نتائج البحث عن:* `{query}`\n"]
        for r in results[:5]:
            snippet = r["content"][:60] + ("..." if len(r["content"]) > 60 else "")
            lines.append(
                f"• `{r['whisper_id']}` ({r['whisper_type']})\n  _{snippet}_"
            )
        bot.send_message(msg.chat.id, "\n".join(lines), parse_mode="Markdown")

    # ── /favorites — saved whispers ───────────────────────────────────────────
    @bot.message_handler(commands=["favorites", "مفضلتي"])
    def favorites_cmd(msg: telebot.types.Message):
        user = msg.from_user
        favs = get_favorites(user.id)
        if not favs:
            bot.send_message(msg.chat.id, "❤️ مفضلتك فارغة.")
            return
        lines = [f"❤️ *مفضلتك* ({len(favs)} همسة)\n"]
        for f in favs[:10]:
            snippet = (f.get("content") or "")[:50]
            lines.append(f"• `{f['whisper_id']}` — _{snippet}_")
        bot.send_message(msg.chat.id, "\n".join(lines), parse_mode="Markdown")

    # ── Inline: save to favorites ─────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("ent:fav:"))
    def save_favorite_cb(call: telebot.types.CallbackQuery):
        # Answer immediately so Telegram doesn't show loading spinner
        wid = call.data.split(":", 2)[2]
        try:
            from enterprise.db_enterprise import save_favorite
            ok = save_favorite(call.from_user.id, wid)
            msg_text = "❤️ تمت إضافة الهمسة للمفضلة!" if ok else "⚠️ الهمسة في المفضلة مسبقاً."
        except Exception:
            ok, msg_text = False, "⚠️ حدث خطأ مؤقت."
        bot.answer_callback_query(call.id, msg_text, show_alert=True)

    # ── /backup — admin only ──────────────────────────────────────────────────
    @bot.message_handler(commands=["backup"])
    def backup_cmd(msg: telebot.types.Message):
        if not _is_admin(msg.from_user.id):
            return
        filename = create_backup(created_by=msg.from_user.id, notes="manual via /backup")
        audit_log("BACKUP", actor_id=msg.from_user.id, filename=filename)
        bot.send_message(
            msg.chat.id,
            f"✅ *تم إنشاء نسخة احتياطية بنجاح*\n📁 الملف: `{filename}`",
            parse_mode="Markdown",
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:backups")
    def admin_backups(call: telebot.types.CallbackQuery):
        if not _is_admin(call.from_user.id):
            return
        bot.answer_callback_query(call.id)
        backups = list_backups()
        if not backups:
            bot.send_message(call.message.chat.id, "📂 لا توجد نسخ احتياطية بعد.")
            return
        lines = [f"📂 *النسخ الاحتياطية* ({len(backups)} نسخة)\n"]
        for b in backups[:10]:
            size_kb = (b.get("size_bytes") or 0) // 1024
            lines.append(
                f"• `{b['filename']}`\n"
                f"  📏 {size_kb} KB  |  🕐 {str(b['created_at'])[:16]}"
            )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("➕ نسخة جديدة", callback_data="ent:backup_now"),
            back_button("admin:main"),
        )
        bot.send_message(
            call.message.chat.id, "\n".join(lines),
            parse_mode="Markdown", reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: c.data == "ent:backup_now")
    def backup_now_cb(call: telebot.types.CallbackQuery):
        if not _is_admin(call.from_user.id):
            return
        filename = create_backup(created_by=call.from_user.id, notes="admin panel trigger")
        audit_log("BACKUP", actor_id=call.from_user.id, filename=filename)
        bot.answer_callback_query(
            call.id, f"✅ تم إنشاء نسخة احتياطية: {filename}", show_alert=True
        )

    # ── /reports — admin: list pending reports ────────────────────────────────
    @bot.message_handler(commands=["reports"])
    def reports_cmd(msg: telebot.types.Message):
        if not _is_admin(msg.from_user.id):
            return
        _send_reports_list(bot, msg.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data == "admin:reports")
    def admin_reports_cb(call: telebot.types.CallbackQuery):
        if not _is_admin(call.from_user.id):
            return
        bot.answer_callback_query(call.id)
        _send_reports_list(bot, call.message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ent:rpt:"))
    def review_report_cb(call: telebot.types.CallbackQuery):
        if not _is_admin(call.from_user.id):
            return
        _, _, action, rid_str = call.data.split(":", 3)
        rid = int(rid_str)
        status = "resolved" if action == "resolve" else "dismissed"
        review_report(rid, call.from_user.id, status)
        audit_log("REPORT_REVIEWED", actor_id=call.from_user.id,
                  report_id=rid, status=status)
        bot.answer_callback_query(call.id, f"✅ تم تحديث البلاغ #{rid} → {status}")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    # ── Close button ──────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "ent:close")
    def ent_close(call: telebot.types.CallbackQuery):
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    # ── Admin: Enterprise stats panel ─────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:enterprise_stats")
    def enterprise_stats_cb(call: telebot.types.CallbackQuery):
        if not _is_admin(call.from_user.id):
            return
        bot.answer_callback_query(call.id)
        from enterprise.db_enterprise import get_active_users, count_reports, get_snapshots
        text = (
            "📊 *إحصائيات Enterprise*\n\n"
            f"👥 مستخدمون نشطون (7 أيام): `{get_active_users(7)}`\n"
            f"👥 مستخدمون نشطون (30 يوم): `{get_active_users(30)}`\n"
            f"🚨 بلاغات معلقة: `{count_reports('pending')}`\n"
            f"🚨 بلاغات إجمالية: `{count_reports(None)}`\n"
        )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🏅 المتصدرون", callback_data="ent:leaderboard"),
            InlineKeyboardButton("🚨 البلاغات",   callback_data="admin:reports"),
        )
        kb.add(InlineKeyboardButton("📂 النسخ الاحتياطية", callback_data="admin:backups"))
        kb.add(back_button("admin:main"))
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb,
            )
        except Exception:
            bot.send_message(
                call.message.chat.id, text,
                parse_mode="Markdown", reply_markup=kb,
            )

    logger.info("Enterprise handlers registered.")


# ── Private helpers ───────────────────────────────────────────────────────────

def _send_achievements(bot, chat_id: int, user_id: int) -> None:
    from enterprise.db_enterprise import get_user_achievements
    achievements = get_user_achievements(user_id)
    if not achievements:
        bot.send_message(chat_id, "🏆 لم تحصل على أي إنجازات بعد. تابع الاستخدام!")
        return
    lines = [f"🏆 *إنجازاتك* ({len(achievements)} إنجاز)\n"]
    for a in achievements:
        lines.append(
            f"{a['icon']} *{a['title']}*\n"
            f"  _{a.get('description', '')}_ (+{a['xp_reward']} XP)"
        )
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(back_button("ent:close"))
    bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown", reply_markup=kb)


def _send_reports_list(bot, chat_id: int) -> None:
    from enterprise.db_enterprise import get_reports
    reports = get_reports(status="pending", limit=10)
    if not reports:
        bot.send_message(chat_id, "✅ لا توجد بلاغات معلقة.")
        return
    for r in reports:
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ حل", callback_data=f"ent:rpt:resolve:{r['id']}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"ent:rpt:dismiss:{r['id']}"),
        )
        text = (
            f"🚨 *بلاغ #{r['id']}*\n"
            f"🆔 الهمسة: `{r.get('whisper_id', '—')}`\n"
            f"📝 السبب: {r.get('reason', '—')}\n"
            f"👤 المبلّغ: `{r['reporter_id']}`\n"
            f"🕐 التاريخ: {str(r['created_at'])[:16]}"
        )
        bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)
