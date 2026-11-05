"""Dashboard."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, redirect, render_template, url_for

from ..db import APPLICATION_STATUSES, STATUSES, get_db
from ..security import login_required

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return redirect(url_for("main.dashboard" if g.user else "auth.login"))


def _week_start(dt: datetime) -> datetime:
    d = dt.astimezone().date()
    return datetime.combine(d - timedelta(days=d.weekday()), datetime.min.time())


@bp.route("/dashboard")
@login_required
def dashboard():
    db, uid = get_db(), g.user["id"]
    counts = {k: 0 for k, _ in STATUSES}
    for r in db.execute("SELECT status, COUNT(*) c FROM jobs WHERE user_id=? GROUP BY status", (uid,)):
        counts[r["status"]] = r["c"]
    total_jobs = sum(counts.values())
    stats = {
        "jobs": total_jobs,
        "applications": sum(counts[s] for s in APPLICATION_STATUSES),
        "pending": counts["applied"],
        "interviews": counts["interview"],
        "approved": counts["approved"],
    }

    # Atividade semanal (últimas 8 semanas): vagas cadastradas, candidaturas e entrevistas
    this_week = _week_start(datetime.now(timezone.utc))
    weeks = [this_week - timedelta(weeks=i) for i in range(7, -1, -1)]
    idx = {w: i for i, w in enumerate(weeks)}
    series = {"Vagas cadastradas": [0] * 8, "Candidaturas": [0] * 8, "Entrevistas": [0] * 8}

    def bucket(ts):
        w = _week_start(datetime.fromisoformat(ts))
        return idx.get(w)

    for r in db.execute("SELECT created_at FROM jobs WHERE user_id=?", (uid,)):
        if (i := bucket(r["created_at"])) is not None:
            series["Vagas cadastradas"][i] += 1
    for r in db.execute("SELECT status, changed_at FROM status_history WHERE user_id=? AND status IN ('applied','interview')", (uid,)):
        if (i := bucket(r["changed_at"])) is not None:
            series["Candidaturas" if r["status"] == "applied" else "Entrevistas"][i] += 1

    chart = {
        "funnel": {"labels": [l for _, l in STATUSES], "keys": [k for k, _ in STATUSES],
                   "values": [counts[k] for k, _ in STATUSES]},
        "weekly": {"labels": [w.strftime("%d/%m") for w in weeks], "series": series},
    }
    activities = db.execute("SELECT message, created_at FROM activities WHERE user_id=? ORDER BY id DESC LIMIT 10",
                            (uid,)).fetchall()
    has_profile = db.execute("SELECT COUNT(*) FROM experiences WHERE user_id=?", (uid,)).fetchone()[0] > 0 or \
        db.execute("SELECT COUNT(*) FROM skills WHERE user_id=?", (uid,)).fetchone()[0] > 0
    return render_template("dashboard.html", stats=stats, chart=chart, activities=activities,
                           has_profile=has_profile)
