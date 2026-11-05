"""Vagas, análise de compatibilidade e central de candidaturas."""
import json

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for)

from ..db import STATUS_KEYS, STATUS_LABELS, STATUSES, WORK_MODES, get_db, log_activity, now, set_job_status
from ..security import login_required, safe_url
from ..services import ai, workflow
from ..services import profile_data as pdata

bp = Blueprint("jobs", __name__)


def _get_job(job_id: int):
    job = get_db().execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, g.user["id"])).fetchone()
    if job is None:
        abort(404, description="Vaga não encontrada.")
    return job


def _form_job() -> tuple[dict, str | None]:
    f = request.form
    data = {k: f.get(k, "").strip() for k in ("company", "title", "location", "salary", "description", "url", "work_mode")}
    err = None
    if not data["company"] or not data["title"]:
        err = "Informe o nome da empresa e o cargo."
    elif data["url"] and not safe_url(data["url"]):
        err = "O link da vaga precisa começar com http:// ou https://."
    elif data["work_mode"] and data["work_mode"] not in dict(WORK_MODES):
        err = "Modalidade inválida."
    return data, err


@bp.route("/jobs")
@login_required
def list_jobs():
    q, status, mode = request.args.get("q", "").strip(), request.args.get("status", ""), request.args.get("mode", "")
    sql, args = "SELECT * FROM jobs WHERE user_id=?", [g.user["id"]]
    if q:
        sql += " AND (company LIKE ? OR title LIKE ? OR location LIKE ?)"
        args += [f"%{q}%"] * 3
    if status in STATUS_KEYS:
        sql += " AND status=?"
        args.append(status)
    if mode in dict(WORK_MODES):
        sql += " AND work_mode=?"
        args.append(mode)
    jobs = get_db().execute(sql + " ORDER BY id DESC", args).fetchall()
    scores = {r["job_id"]: r["score"] for r in get_db().execute(
        "SELECT job_id, score FROM analyses WHERE id IN (SELECT MAX(id) FROM analyses WHERE user_id=? GROUP BY job_id)",
        (g.user["id"],))}
    return render_template("jobs.html", jobs=jobs, scores=scores, q=q, f_status=status, f_mode=mode)


@bp.route("/jobs/new", methods=["GET", "POST"])
@login_required
def new_job():
    if request.method == "POST":
        data, err = _form_job()
        if err:
            flash(err, "error")
            return render_template("job_form.html", job=data, editing=False)
        db, ts = get_db(), now()
        new_id = db.insert(
            "INSERT INTO jobs (user_id,company,title,location,salary,description,url,work_mode,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (g.user["id"], data["company"], data["title"], data["location"], data["salary"], data["description"],
             data["url"], data["work_mode"], "found", ts, ts))
        db.execute("INSERT INTO status_history (job_id,user_id,status,changed_at) VALUES (?,?,?,?)",
                   (new_id, g.user["id"], "found", ts))
        log_activity(g.user["id"], f"Vaga cadastrada: “{data['title']}” ({data['company']})")
        db.commit()
        flash("Vaga cadastrada.", "success")
        return redirect(url_for("jobs.detail", job_id=new_id))
    return render_template("job_form.html", job={}, editing=False)


@bp.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@login_required
def edit_job(job_id):
    job = _get_job(job_id)
    if request.method == "POST":
        data, err = _form_job()
        if err:
            flash(err, "error")
            return render_template("job_form.html", job={**data, "id": job_id}, editing=True)
        db = get_db()
        db.execute("UPDATE jobs SET company=?,title=?,location=?,salary=?,description=?,url=?,work_mode=?,updated_at=? WHERE id=?",
                   (data["company"], data["title"], data["location"], data["salary"], data["description"],
                    data["url"], data["work_mode"], now(), job_id))
        log_activity(g.user["id"], f"Vaga atualizada: “{data['title']}” ({data['company']})")
        db.commit()
        flash("Vaga atualizada.", "success")
        return redirect(url_for("jobs.detail", job_id=job_id))
    return render_template("job_form.html", job=dict(job), editing=True)


@bp.route("/jobs/<int:job_id>")
@login_required
def detail(job_id):
    job, db = _get_job(job_id), get_db()
    row = db.execute("SELECT * FROM analyses WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    analysis = None
    if row:
        analysis = json.loads(row["result_json"])
        analysis.update(created_at=row["created_at"], simulated=bool(row["simulated"]), provider=row["provider"])
    docs = db.execute("SELECT id, kind, title, simulated, updated_at FROM documents WHERE job_id=? ORDER BY id DESC",
                      (job_id,)).fetchall()
    history = db.execute("SELECT status, changed_at FROM status_history WHERE job_id=? ORDER BY id DESC", (job_id,)).fetchall()
    mails = db.execute("SELECT id, subject, from_name, from_addr, received_at, is_read FROM emails "
                       "WHERE job_id=? AND user_id=? AND dismissed=0 ORDER BY received_at DESC LIMIT 10",
                       (job_id, g.user["id"])).fetchall()
    return render_template("job_detail.html", job=job, analysis=analysis, docs=docs, history=history, mails=mails)


@bp.route("/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
def delete_job(job_id):
    job, db = _get_job(job_id), get_db()
    db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    log_activity(g.user["id"], f"Vaga removida: “{job['title']}” ({job['company']})")
    db.commit()
    flash("Vaga removida.", "info")
    return redirect(url_for("jobs.list_jobs"))


@bp.route("/jobs/<int:job_id>/status", methods=["POST"])
@login_required
def change_status(job_id):
    _get_job(job_id)
    status = request.form.get("status", "")
    if status not in STATUS_KEYS:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error="Status inválido."), 400
        abort(400, description="Status inválido.")
    set_job_status(g.user["id"], job_id, status)
    get_db().commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, status=status, label=STATUS_LABELS[status])
    flash(f"Status alterado para “{STATUS_LABELS[status]}”.", "success")
    return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/") and
                    not (request.form.get("back") or "").startswith("//") else url_for("jobs.detail", job_id=job_id))


@bp.route("/applications")
@login_required
def board():
    jobs = get_db().execute("SELECT * FROM jobs WHERE user_id=? ORDER BY updated_at DESC", (g.user["id"],)).fetchall()
    columns = {k: [] for k, _ in STATUSES}
    for j in jobs:
        columns[j["status"]].append(j)
    return render_template("board.html", columns=columns)


@bp.route("/jobs/<int:job_id>/analyze", methods=["POST"])
@login_required
def analyze_job(job_id):
    job, db, uid = _get_job(job_id), get_db(), g.user["id"]
    bundle = pdata.load_bundle(uid)
    if pdata.bundle_is_empty(bundle):
        flash("Cadastre seu perfil (ou importe um currículo) antes de analisar uma vaga.", "error")
        return redirect(url_for("profile.edit"))
    if not (job["description"] or "").strip():
        flash("Esta vaga não tem descrição. Edite a vaga e cole a descrição completa para analisar.", "error")
        return redirect(url_for("jobs.edit_job", job_id=job_id))
    result = workflow.run_and_store_analysis(db, uid, job, bundle)
    db.commit()
    flash("Análise concluída." + (" Resultado SIMULADO (modo demonstrativo)." if result["simulated"] else ""),
          "info" if result["simulated"] else "success")
    return redirect(url_for("jobs.detail", job_id=job_id) + "#analise")


@bp.route("/jobs/<int:job_id>/generate/<kind>", methods=["POST"])
@login_required
def generate(job_id, kind):
    """Gera um rascunho (não salvo) de currículo/carta para edição."""
    if kind not in ("resume", "cover"):
        abort(404)
    job, uid = _get_job(job_id), g.user["id"]
    bundle = pdata.load_bundle(uid)
    if pdata.bundle_is_empty(bundle):
        flash("Cadastre seu perfil antes de gerar documentos.", "error")
        return redirect(url_for("profile.edit"))
    row = get_db().execute("SELECT result_json FROM analyses WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    analysis = json.loads(row["result_json"]) if row else None
    out = ai.generate_document(kind, bundle, dict(job), analysis)
    label = "Currículo" if kind == "resume" else "Carta de apresentação"
    return render_template("document_edit.html", doc=None, job=job, kind=kind,
                           title=f"{label} — {job['title']} ({job['company']})",
                           content=out["content"], simulated=out["simulated"], note=out["note"])
