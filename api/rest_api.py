"""
api/rest_api.py — Full REST API for Whisper-Bot Enterprise.
Mounted at /api/v1 by the web server (web/app.py).

Authentication: Bearer token via API_SECRET env var.
All endpoints return JSON.
"""
from __future__ import annotations

import os
import functools
import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request, abort

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

API_SECRET = os.getenv("API_SECRET", "")


# ── Auth decorator ────────────────────────────────────────────────────────────

def require_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not API_SECRET:
            return f(*args, **kwargs)   # dev mode: no auth
        token = request.headers.get("Authorization", "")
        if not token.startswith("Bearer ") or token[7:] != API_SECRET:
            abort(401, "Unauthorized")
        return f(*args, **kwargs)
    return wrapper


def _ok(data: Any, status: int = 200):
    return jsonify({"status": "ok", "data": data}), status


def _err(msg: str, status: int = 400):
    return jsonify({"status": "error", "message": msg}), status


# ── /users ────────────────────────────────────────────────────────────────────

@api_bp.route("/users", methods=["GET"])
@require_auth
def list_users():
    from database import get_all_users
    page     = int(request.args.get("page", 0))
    per_page = min(int(request.args.get("per_page", 20)), 100)
    rows, total = get_all_users(page=page, per_page=per_page)
    return _ok({
        "total": total,
        "page": page,
        "per_page": per_page,
        "users": [dict(r) for r in rows],
    })


@api_bp.route("/users/<int:user_id>", methods=["GET"])
@require_auth
def get_user_detail(user_id: int):
    from database import get_user, get_user_stats
    from enterprise.db_enterprise import get_xp, get_user_achievements
    u = get_user(user_id)
    if not u:
        return _err("User not found", 404)
    return _ok({
        "user":         dict(u),
        "stats":        get_user_stats(user_id),
        "xp":           get_xp(user_id),
        "achievements": get_user_achievements(user_id),
    })


@api_bp.route("/users/<int:user_id>/ban", methods=["POST"])
@require_auth
def api_ban_user(user_id: int):
    from enterprise.db_enterprise import ban_user_with_reason
    data   = request.get_json(force=True) or {}
    reason = data.get("reason", "API ban")
    hours  = data.get("hours")
    ban_user_with_reason(user_id, reason, banned_by=0, hours=hours)
    return _ok({"banned": user_id})


@api_bp.route("/users/<int:user_id>/unban", methods=["POST"])
@require_auth
def api_unban_user(user_id: int):
    from enterprise.db_enterprise import unban_user_with_reason
    data   = request.get_json(force=True) or {}
    reason = data.get("reason", "API unban")
    unban_user_with_reason(user_id, reason, unbanned_by=0)
    return _ok({"unbanned": user_id})


# ── /whispers ─────────────────────────────────────────────────────────────────

@api_bp.route("/whispers", methods=["GET"])
@require_auth
def list_whispers():
    from database import get_conn
    limit  = min(int(request.args.get("limit", 20)), 100)
    offset = int(request.args.get("offset", 0))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whispers ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM whispers").fetchone()[0]
    return _ok({"total": total, "whispers": [dict(r) for r in rows]})


@api_bp.route("/whispers/<whisper_id>", methods=["GET"])
@require_auth
def get_whisper_detail(whisper_id: str):
    from database import get_whisper, get_readers, get_curious_ones, reader_count
    w = get_whisper(whisper_id)
    if not w:
        return _err("Whisper not found", 404)
    return _ok({
        "whisper":  dict(w),
        "readers":  [dict(r) for r in get_readers(whisper_id)],
        "curious":  [dict(r) for r in get_curious_ones(whisper_id)],
        "read_count": reader_count(whisper_id),
    })


@api_bp.route("/whispers/<whisper_id>", methods=["DELETE"])
@require_auth
def delete_whisper_api(whisper_id: str):
    from database import delete_whisper
    delete_whisper(whisper_id)
    return _ok({"deleted": whisper_id})


# ── /reports ──────────────────────────────────────────────────────────────────

@api_bp.route("/reports", methods=["GET"])
@require_auth
def list_reports():
    from enterprise.db_enterprise import get_reports, count_reports
    status = request.args.get("status", "pending")
    limit  = min(int(request.args.get("limit", 20)), 100)
    return _ok({
        "total":   count_reports(status if status != "all" else None),
        "reports": get_reports(status if status != "all" else None, limit),
    })


@api_bp.route("/reports/<int:report_id>/review", methods=["POST"])
@require_auth
def review_report_api(report_id: int):
    from enterprise.db_enterprise import review_report
    data   = request.get_json(force=True) or {}
    status = data.get("status", "resolved")
    review_report(report_id, reviewed_by=0, new_status=status)
    return _ok({"report_id": report_id, "status": status})


# ── /statistics ───────────────────────────────────────────────────────────────

@api_bp.route("/statistics", methods=["GET"])
@require_auth
def statistics():
    from database import get_stats
    from enterprise.db_enterprise import get_active_users, count_reports, get_snapshots
    data = dict(get_stats())
    data["active_7d"]       = get_active_users(7)
    data["active_30d"]      = get_active_users(30)
    data["pending_reports"] = count_reports("pending")
    return _ok(data)


@api_bp.route("/statistics/snapshots/<period_type>", methods=["GET"])
@require_auth
def stat_snapshots(period_type: str):
    from enterprise.db_enterprise import get_snapshots
    limit = min(int(request.args.get("limit", 30)), 365)
    return _ok(get_snapshots(period_type, limit))


@api_bp.route("/statistics/snapshot", methods=["POST"])
@require_auth
def take_snapshot():
    from enterprise.db_enterprise import snapshot_stats
    period = request.get_json(force=True, silent=True) or {}
    ptype  = period.get("period_type", "daily")
    snapshot_stats(ptype)
    return _ok({"snapshotted": ptype})


# ── /settings ─────────────────────────────────────────────────────────────────

@api_bp.route("/settings", methods=["GET"])
@require_auth
def get_settings():
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return _ok({r["key"]: r["value"] for r in rows})


@api_bp.route("/settings/<key>", methods=["GET"])
@require_auth
def get_one_setting(key: str):
    from database import get_setting
    return _ok({key: get_setting(key)})


@api_bp.route("/settings/<key>", methods=["PUT"])
@require_auth
def set_one_setting(key: str):
    from database import set_setting
    data  = request.get_json(force=True) or {}
    value = data.get("value")
    if value is None:
        return _err("Missing 'value' field")
    set_setting(key, str(value))
    return _ok({key: value})


# ── /health ───────────────────────────────────────────────────────────────────

@api_bp.route("/health", methods=["GET"])
def api_health():
    from core.health import health_check
    data = health_check()
    status_code = 200 if data["status"] == "ok" else 503
    return jsonify(data), status_code


@api_bp.route("/metrics", methods=["GET"])
@require_auth
def api_metrics():
    from core.health import metrics
    return _ok(metrics())


# ── /backups ──────────────────────────────────────────────────────────────────

@api_bp.route("/backups", methods=["GET"])
@require_auth
def list_backups_api():
    from enterprise.db_enterprise import list_backups
    return _ok(list_backups())


@api_bp.route("/backups", methods=["POST"])
@require_auth
def create_backup_api():
    from enterprise.db_enterprise import create_backup
    data  = request.get_json(force=True, silent=True) or {}
    notes = data.get("notes", "API backup")
    filename = create_backup(created_by=None, notes=notes)
    return _ok({"filename": filename}, 201)


# ── /xp leaderboard ───────────────────────────────────────────────────────────

@api_bp.route("/leaderboard", methods=["GET"])
@require_auth
def leaderboard_api():
    from enterprise.db_enterprise import xp_leaderboard
    limit = min(int(request.args.get("limit", 10)), 50)
    return _ok(xp_leaderboard(limit))
