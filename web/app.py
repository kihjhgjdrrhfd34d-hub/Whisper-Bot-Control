"""
web/app.py — Enterprise Web Dashboard (Flask).
Runs on a separate port (default 8081) from the keep_alive server.

Pages:
  /              → Dashboard
  /users         → User management
  /reports       → Reports review
  /statistics    → Stats charts
  /backups       → Backup management
  /settings      → Bot settings

All pages are secured by HTTP Basic Auth (WEB_USER / WEB_PASS env vars).
The REST API blueprint (/api/v1) is also mounted here.
"""
from __future__ import annotations

import os
import logging
from functools import wraps
from threading import Thread

from flask import (
    Flask, render_template_string, request, redirect, url_for,
    session, flash,
)

logger = logging.getLogger(__name__)

WEB_PORT  = int(os.getenv("WEB_PORT", "8081"))
WEB_USER  = os.getenv("WEB_USER", "admin")
WEB_PASS  = os.getenv("WEB_PASS", "whisper_admin")
SECRET_KEY = os.getenv("FLASK_SECRET", "whisper_secret_key_change_me")

web_app = Flask(__name__, template_folder="templates")
web_app.secret_key = SECRET_KEY


# ── Register API blueprint ────────────────────────────────────────────────────

def _mount_api() -> None:
    try:
        from api.rest_api import api_bp
        web_app.register_blueprint(api_bp)
        logger.info("REST API blueprint mounted at /api/v1")
    except Exception as exc:
        logger.error(f"Failed to mount API blueprint: {exc}")


_mount_api()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated


# ── Base HTML template ────────────────────────────────────────────────────────

_BASE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Whisper Bot — لوحة التحكم</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
         background: #0d1117; color: #c9d1d9; direction: rtl; }
  .navbar { background: #161b22; border-bottom: 1px solid #30363d;
            padding: 12px 24px; display: flex; align-items: center;
            justify-content: space-between; }
  .navbar h1 { font-size: 1.2rem; color: #58a6ff; }
  .nav-links a { color: #8b949e; text-decoration: none; margin-left: 20px;
                 font-size: 0.9rem; }
  .nav-links a:hover { color: #58a6ff; }
  .container { max-width: 1200px; margin: 32px auto; padding: 0 24px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
          padding: 20px; margin-bottom: 20px; }
  .card h2 { font-size: 1rem; color: #8b949e; margin-bottom: 16px; }
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px,1fr));
               gap: 16px; margin-bottom: 24px; }
  .stat-box { background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
              padding: 16px; text-align: center; }
  .stat-box .num { font-size: 2rem; font-weight: bold; color: #58a6ff; }
  .stat-box .lbl { font-size: 0.8rem; color: #8b949e; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { background: #21262d; padding: 10px 12px; text-align: right;
       color: #8b949e; border-bottom: 1px solid #30363d; }
  td { padding: 10px 12px; border-bottom: 1px solid #21262d; }
  tr:hover td { background: #1c2128; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
           font-size: 0.75rem; }
  .badge-green  { background: #1a4731; color: #3fb950; }
  .badge-red    { background: #4d1a1a; color: #f85149; }
  .badge-yellow { background: #3d2e00; color: #d29922; }
  .btn { padding: 6px 14px; border-radius: 6px; border: none; cursor: pointer;
         font-size: 0.85rem; text-decoration: none; display: inline-block; }
  .btn-primary { background: #238636; color: #fff; }
  .btn-danger  { background: #da3633; color: #fff; }
  .btn-sm { padding: 3px 10px; font-size: 0.78rem; }
  .alert { padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; }
  .alert-success { background: #1a4731; color: #3fb950; }
  .alert-error   { background: #4d1a1a; color: #f85149; }
  form input, form select { background: #0d1117; color: #c9d1d9;
    border: 1px solid #30363d; border-radius: 6px; padding: 6px 10px;
    margin-bottom: 10px; width: 100%; font-size: 0.9rem; }
  .login-box { max-width: 340px; margin: 100px auto; }
</style>
</head>
<body>
{% if session.get('logged_in') %}
<nav class="navbar">
  <h1>🤫 Whisper Bot</h1>
  <div class="nav-links">
    <a href="/">📊 Dashboard</a>
    <a href="/users">👥 المستخدمون</a>
    <a href="/reports">🚨 البلاغات</a>
    <a href="/statistics">📈 الإحصائيات</a>
    <a href="/backups">📂 النسخ الاحتياطية</a>
    <a href="/settings">⚙️ الإعدادات</a>
    <a href="/logout">🚪 خروج</a>
  </div>
</nav>
{% endif %}
<div class="container">
{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="alert alert-{{ cat }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}
{{ content | safe }}
</div>
</body>
</html>
"""


def _render(content: str) -> str:
    from flask import render_template_string
    return render_template_string(_BASE, content=content)


# ── Routes ────────────────────────────────────────────────────────────────────

@web_app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("username") == WEB_USER and
                request.form.get("password") == WEB_PASS):
            session["logged_in"] = True
            return redirect(request.args.get("next") or "/")
        flash("بيانات الدخول غير صحيحة", "error")
    form = """
    <div class="login-box">
      <div class="card">
        <h2 style="text-align:center;font-size:1.4rem;color:#58a6ff;margin-bottom:20px">
          🤫 تسجيل الدخول
        </h2>
        <form method="post">
          <input name="username" placeholder="اسم المستخدم" required>
          <input name="password" type="password" placeholder="كلمة المرور" required>
          <button type="submit" class="btn btn-primary" style="width:100%">دخول</button>
        </form>
      </div>
    </div>"""
    return _render(form)


@web_app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect("/login")


@web_app.route("/")
@login_required
def dashboard():
    try:
        from database import get_stats
        from enterprise.db_enterprise import get_active_users, count_reports
        s = get_stats()
        active_7  = get_active_users(7)
        pending_r = count_reports("pending")
    except Exception:
        s = {}; active_7 = 0; pending_r = 0

    content = f"""
    <h2 style="margin-bottom:20px;color:#58a6ff">📊 Dashboard</h2>
    <div class="stat-grid">
      <div class="stat-box"><div class="num">{s.get('total_users',0)}</div>
        <div class="lbl">إجمالي المستخدمين</div></div>
      <div class="stat-box"><div class="num">{s.get('active_users',0)}</div>
        <div class="lbl">مستخدمون نشطون</div></div>
      <div class="stat-box"><div class="num">{active_7}</div>
        <div class="lbl">نشطون (7 أيام)</div></div>
      <div class="stat-box"><div class="num">{s.get('total_whispers',0)}</div>
        <div class="lbl">إجمالي الهمسات</div></div>
      <div class="stat-box"><div class="num">{s.get('total_reads',0)}</div>
        <div class="lbl">إجمالي القراءات</div></div>
      <div class="stat-box"><div class="num">{s.get('new_today',0)}</div>
        <div class="lbl">مستخدمون اليوم</div></div>
      <div class="stat-box"><div class="num">{s.get('whispers_today',0)}</div>
        <div class="lbl">همسات اليوم</div></div>
      <div class="stat-box"><div class="num"
          style="color:{'#f85149' if pending_r>0 else '#3fb950'}">{pending_r}</div>
        <div class="lbl">بلاغات معلقة</div></div>
    </div>
    <div class="card">
      <h2>⚡ وصول سريع</h2>
      <a href="/users" class="btn btn-primary" style="margin-left:8px">👥 إدارة المستخدمين</a>
      <a href="/reports" class="btn btn-primary" style="margin-left:8px">🚨 مراجعة البلاغات</a>
      <a href="/backups" class="btn btn-primary">📂 النسخ الاحتياطية</a>
    </div>
    """
    return _render(content)


@web_app.route("/users")
@login_required
def users_page():
    from database import get_all_users
    page = int(request.args.get("page", 0))
    rows, total = get_all_users(page=page, per_page=25)
    rows_html = ""
    for u in rows:
        banned = (
            '<span class="badge badge-red">محظور</span>'
            if u["is_banned"] else
            '<span class="badge badge-green">نشط</span>'
        )
        uname = f"@{u['username']}" if u["username"] else u["first_name"] or "—"
        rows_html += f"""
        <tr>
          <td><code>{u['user_id']}</code></td>
          <td>{uname}</td>
          <td>{u.get('created_at','')[:10]}</td>
          <td>{banned}</td>
        </tr>"""
    content = f"""
    <h2 style="margin-bottom:20px;color:#58a6ff">👥 المستخدمون</h2>
    <div class="card">
      <p style="color:#8b949e;margin-bottom:12px">الإجمالي: {total} مستخدم | الصفحة {page+1}</p>
      <table>
        <thead><tr><th>الآيدي</th><th>المستخدم</th><th>تاريخ الانضمام</th><th>الحالة</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <div style="margin-top:16px">
        {'<a href="/users?page=' + str(page-1) + '" class="btn btn-primary btn-sm" style="margin-left:8px">→ السابقة</a>' if page > 0 else ''}
        {'<a href="/users?page=' + str(page+1) + '" class="btn btn-primary btn-sm">← التالية</a>' if (page+1)*25 < total else ''}
      </div>
    </div>"""
    return _render(content)


@web_app.route("/reports")
@login_required
def reports_page():
    from enterprise.db_enterprise import get_reports
    reports = get_reports(status=None, limit=30)
    rows_html = ""
    for r in reports:
        status_map = {
            "pending":   '<span class="badge badge-yellow">معلق</span>',
            "resolved":  '<span class="badge badge-green">محلول</span>',
            "dismissed": '<span class="badge badge-red">مرفوض</span>',
        }
        badge = status_map.get(r.get("status",""), r.get("status",""))
        rows_html += f"""
        <tr>
          <td><code>{r['id']}</code></td>
          <td><code>{r.get('whisper_id','—')}</code></td>
          <td>{r.get('reason','—')[:60]}</td>
          <td><code>{r['reporter_id']}</code></td>
          <td>{badge}</td>
          <td>{str(r.get('created_at',''))[:16]}</td>
        </tr>"""
    content = f"""
    <h2 style="margin-bottom:20px;color:#58a6ff">🚨 البلاغات</h2>
    <div class="card">
      <table>
        <thead><tr><th>#</th><th>الهمسة</th><th>السبب</th>
          <th>المبلّغ</th><th>الحالة</th><th>التاريخ</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>"""
    return _render(content)


@web_app.route("/statistics")
@login_required
def statistics_page():
    from database import get_stats
    from enterprise.db_enterprise import get_active_users, get_snapshots
    s = get_stats()
    snapshots = get_snapshots("daily", 7)
    snap_html = ""
    for snap in snapshots:
        d = snap.get("data", {})
        snap_html += f"""
        <tr>
          <td>{snap['period_label']}</td>
          <td>{d.get('total_users',0)}</td>
          <td>{d.get('new_today',0)}</td>
          <td>{d.get('total_whispers',0)}</td>
          <td>{d.get('whispers_today',0)}</td>
          <td>{d.get('total_reads',0)}</td>
        </tr>"""
    content = f"""
    <h2 style="margin-bottom:20px;color:#58a6ff">📈 الإحصائيات</h2>
    <div class="stat-grid">
      <div class="stat-box"><div class="num">{s.get('total_users',0)}</div>
        <div class="lbl">إجمالي المستخدمين</div></div>
      <div class="stat-box"><div class="num">{get_active_users(7)}</div>
        <div class="lbl">نشطون (7 أيام)</div></div>
      <div class="stat-box"><div class="num">{get_active_users(30)}</div>
        <div class="lbl">نشطون (30 يوم)</div></div>
      <div class="stat-box"><div class="num">{s.get('total_whispers',0)}</div>
        <div class="lbl">إجمالي الهمسات</div></div>
    </div>
    <div class="card">
      <h2>📅 سجل يومي (آخر 7 أيام)</h2>
      <table>
        <thead><tr><th>اليوم</th><th>المستخدمون</th><th>جدد</th>
          <th>الهمسات</th><th>اليوم</th><th>القراءات</th></tr></thead>
        <tbody>{snap_html or '<tr><td colspan=6 style="text-align:center;color:#8b949e">لا توجد بيانات بعد</td></tr>'}</tbody>
      </table>
    </div>"""
    return _render(content)


@web_app.route("/backups", methods=["GET", "POST"])
@login_required
def backups_page():
    from enterprise.db_enterprise import list_backups, create_backup
    if request.method == "POST":
        filename = create_backup(notes="web dashboard")
        flash(f"✅ تم إنشاء نسخة احتياطية: {filename}", "success")
        return redirect("/backups")
    backups = list_backups()
    rows_html = ""
    for b in backups:
        size_kb = (b.get("size_bytes") or 0) // 1024
        rows_html += f"""
        <tr>
          <td><code>{b['filename']}</code></td>
          <td>{size_kb} KB</td>
          <td>{str(b.get('created_at',''))[:16]}</td>
          <td>{b.get('notes','—')}</td>
        </tr>"""
    content = f"""
    <h2 style="margin-bottom:20px;color:#58a6ff">📂 النسخ الاحتياطية</h2>
    <div class="card" style="margin-bottom:16px">
      <form method="post">
        <button type="submit" class="btn btn-primary">➕ إنشاء نسخة احتياطية الآن</button>
      </form>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>اسم الملف</th><th>الحجم</th><th>التاريخ</th><th>ملاحظات</th></tr></thead>
        <tbody>{rows_html or '<tr><td colspan=4 style="text-align:center;color:#8b949e">لا توجد نسخ بعد</td></tr>'}</tbody>
      </table>
    </div>"""
    return _render(content)


@web_app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    from database import get_conn, set_setting, get_setting
    if request.method == "POST":
        for key, value in request.form.items():
            if key.startswith("_"):
                continue
            set_setting(key, value)
        flash("✅ تم حفظ الإعدادات بنجاح", "success")
        return redirect("/settings")

    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()

    fields_html = ""
    for r in rows:
        fields_html += f"""
        <div style="margin-bottom:12px">
          <label style="color:#8b949e;font-size:0.85rem;display:block;margin-bottom:4px">
            {r['key']}
          </label>
          <input name="{r['key']}" value="{r['value']}"
                 style="width:300px;max-width:100%">
        </div>"""

    content = f"""
    <h2 style="margin-bottom:20px;color:#58a6ff">⚙️ الإعدادات</h2>
    <div class="card">
      <form method="post">
        {fields_html}
        <button type="submit" class="btn btn-primary">💾 حفظ التغييرات</button>
      </form>
    </div>"""
    return _render(content)


# ── Web server startup ────────────────────────────────────────────────────────

def run_web() -> None:
    web_app.run(
        host="0.0.0.0",
        port=WEB_PORT,
        debug=False,
        use_reloader=False,
    )


def start_web_dashboard() -> None:
    """Start the web dashboard in a background thread."""
    t = Thread(target=run_web, daemon=True, name="WebDashboard")
    t.start()
    logger.info(f"Web dashboard started on port {WEB_PORT}")
