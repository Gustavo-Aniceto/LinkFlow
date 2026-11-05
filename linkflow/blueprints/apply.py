"""Ranking por compatibilidade, pacote de candidatura e envio por e-mail (com confirmação)."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from ..db import APPLICATION_STATUSES, get_db, log_activity, now, set_job_status
from ..security import login_required
from ..services import ai, mailbox, workflow
from ..services import profile_data as pdata
from ..services.pdf_export import text_to_pdf

bp = Blueprint("apply", __name__)


def _job(job_id: int):
    job = get_db().execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, g.user["id"])).fetchone()
    if job is None:
        abort(404, description="Vaga não encontrada.")
    return job


def _bundle_or_redirect():
    bundle = pdata.load_bundle(g.user["id"])
    if pdata.bundle_is_empty(bundle):
        flash("Cadastre seu perfil (ou importe um currículo) antes de continuar.", "error")
        return None, redirect(url_for("profile.edit"))
    return bundle, None


# ------------------------------------------------------------------ ranking
@bp.route("/ranking")
@login_required
def ranking():
    db, uid = get_db(), g.user["id"]
    show_all = request.args.get("all") == "1"
    rows = db.execute(
        """SELECT j.*, a.score AS score, a.simulated AS a_sim, a.created_at AS analyzed_at
           FROM jobs j LEFT JOIN analyses a ON a.id = (SELECT MAX(id) FROM analyses WHERE job_id = j.id)
           WHERE j.user_id=?""", (uid,)).fetchall()
    jobs = [r for r in rows if show_all or r["status"] not in APPLICATION_STATUSES]
    jobs.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), -r["id"]))
    pending = [r for r in jobs if r["score"] is None and (r["description"] or "").strip()]
    limit = 3 if ai.provider() != "demo" else 25
    return render_template("ranking.html", jobs=jobs, show_all=show_all, pending=len(pending), batch=min(limit, len(pending)))


@bp.route("/ranking/analyze-all", methods=["POST"])
@login_required
def analyze_all():
    db, uid = get_db(), g.user["id"]
    bundle, redir = _bundle_or_redirect()
    if redir:
        return redir
    limit = 3 if ai.provider() != "demo" else 25  # limita tempo/custo por clique (funções serverless têm tempo máximo)
    todo = db.execute(
        """SELECT j.* FROM jobs j WHERE j.user_id=? AND TRIM(j.description) <> ''
           AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.job_id = j.id)
           AND j.status NOT IN ('applied','interview','approved','rejected') ORDER BY j.id DESC LIMIT ?""", (uid, limit)).fetchall()
    for job in todo:
        workflow.run_and_store_analysis(db, uid, job, bundle)
        db.commit()
    flash(f"{len(todo)} vaga(s) analisada(s)." + (" Resultados SIMULADOS (modo demonstrativo)." if ai.provider() == "demo" else ""),
          "info" if ai.provider() == "demo" else "success")
    return redirect(url_for("apply.ranking"))


# ------------------------------------------------------------------ pacote
@bp.route("/jobs/<int:job_id>/package", methods=["POST"])
@login_required
def package(job_id):
    job, db, uid = _job(job_id), get_db(), g.user["id"]
    bundle, redir = _bundle_or_redirect()
    if redir:
        return redir
    if not (job["description"] or "").strip() and workflow.latest_analysis(db, job_id) is None:
        flash("Esta vaga não tem descrição. Edite a vaga e cole a descrição para montar o pacote.", "error")
        return redirect(url_for("jobs.edit_job", job_id=job_id))
    res = workflow.build_package(db, uid, job, bundle)
    db.commit()
    flash("Pacote montado: currículo e carta direcionados salvos. Revise antes de enviar." +
          (" (SIMULADO: modo demonstrativo)" if res["simulated"] else ""), "info" if res["simulated"] else "success")
    return redirect(url_for("apply.apply_page", job_id=job_id))


# ------------------------------------------------------------------ candidatura
def _sent_last_24h(db, uid):
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    return db.execute("SELECT COUNT(*) FROM sent_applications WHERE user_id=? AND sent_at>?", (uid, since)).fetchone()[0]


@bp.route("/jobs/<int:job_id>/apply")
@login_required
def apply_page(job_id):
    job, db, uid = _job(job_id), get_db(), g.user["id"]
    resume, cover = workflow.latest_doc(db, uid, job_id, "resume"), workflow.latest_doc(db, uid, job_id, "cover")
    acc = db.execute("SELECT username, smtp_host, smtp_port FROM email_accounts WHERE user_id=?", (uid,)).fetchone()
    profile = pdata.load_bundle(uid)["profile"]
    suggestions = mailbox.find_recruiter_emails(job["description"])
    sent = db.execute("SELECT * FROM sent_applications WHERE user_id=? AND job_id=? ORDER BY id DESC", (uid, job_id)).fetchall()
    defaults = {
        "to": suggestions[0] if suggestions else "",
        "subject": f"Candidatura — {job['title']} — {profile['full_name'] or g.user['name']}",
        "body": workflow.cover_to_email_body(cover["content"]) if cover else "",
    }
    return render_template("apply.html", job=job, resume=resume, cover=cover, acc=acc, suggestions=suggestions,
                           sent=sent, defaults=defaults, sent_today=_sent_last_24h(db, uid),
                           max_sends=current_app.config["MAX_SENDS_PER_DAY"])


@bp.route("/jobs/<int:job_id>/apply/send", methods=["POST"])
@login_required
def send_application(job_id):
    job, db, uid, f = _job(job_id), get_db(), g.user["id"], request.form
    back = redirect(url_for("apply.apply_page", job_id=job_id))
    to_addr, subject, body = f.get("to", "").strip(), f.get("subject", "").strip(), f.get("body", "").strip()
    if f.get("confirm") != "1":
        flash("Marque a caixa confirmando que revisou a mensagem e os anexos.", "error")
        return back
    acc = db.execute("SELECT * FROM email_accounts WHERE user_id=?", (uid,)).fetchone()
    if acc is None or not acc["smtp_host"]:
        flash("Conecte seu e-mail em “E-mails” para poder enviar candidaturas.", "error")
        return back
    if not subject or not body:
        flash("Assunto e mensagem são obrigatórios.", "error")
        return back
    if len(subject) > 200 or len(body) > 20000:
        flash("Assunto ou mensagem longos demais.", "error")
        return back
    if _sent_last_24h(db, uid) >= current_app.config["MAX_SENDS_PER_DAY"]:
        flash(f"Limite de {current_app.config['MAX_SENDS_PER_DAY']} envios em 24 horas atingido (proteção contra disparo em massa).", "error")
        return back
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    if db.execute("SELECT 1 FROM sent_applications WHERE user_id=? AND job_id=? AND LOWER(to_addr)=? AND sent_at>?",
                  (uid, job_id, to_addr.lower(), since)).fetchone():
        flash("Você já enviou uma candidatura desta vaga para este e-mail nas últimas 24 horas.", "error")
        return back

    attachments, names = [], []
    if f.get("attach_resume") == "1":
        doc = workflow.latest_doc(db, uid, job_id, "resume")
        if doc:
            attachments.append(("Curriculo.pdf", text_to_pdf(doc["content"], doc["title"])))
    if f.get("attach_cover") == "1":
        doc = workflow.latest_doc(db, uid, job_id, "cover")
        if doc:
            attachments.append(("Carta_de_apresentacao.pdf", text_to_pdf(doc["content"], doc["title"])))
    names = [n for n, _ in attachments]
    profile = pdata.load_bundle(uid)["profile"]
    try:
        msg = mailbox.build_application_email(profile["full_name"] or g.user["name"], acc["username"], to_addr, subject, body, attachments)
        pwd = mailbox.decrypt_password(current_app.config["SECRET_KEY"], acc["password_enc"])
        mailbox.send_message(acc["smtp_host"], acc["smtp_port"], acc["username"], pwd, msg)
    except mailbox.MailError as exc:
        flash(str(exc), "error")
        return back
    except Exception:
        current_app.logger.exception("Erro inesperado no envio")
        flash("Erro inesperado ao enviar. Nada foi marcado como enviado.", "error")
        return back
    db.insert("INSERT INTO sent_applications (user_id,job_id,to_addr,subject,attachments,sent_at) VALUES (?,?,?,?,?,?)",
              (uid, job_id, to_addr, subject, ", ".join(names), now()))
    if job["status"] not in ("interview", "approved", "rejected", "applied"):
        set_job_status(uid, job_id, "applied", quiet=True)
    log_activity(uid, f"Candidatura enviada por e-mail: “{job['title']}” ({job['company']}) → {to_addr}")
    db.commit()
    flash(f"Candidatura enviada para {to_addr}. A vaga foi marcada como “Candidatura realizada”.", "success")
    return back
