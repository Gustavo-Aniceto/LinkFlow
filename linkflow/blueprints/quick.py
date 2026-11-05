"""Vaga por link: lê a página da vaga, cadastra e monta o currículo (+ carta) direcionado em um passo."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..db import get_db, log_activity, now
from ..security import login_required, safe_url
from ..services import ai, jobfetch, workflow
from ..services import profile_data as pdata

bp = Blueprint("quick", __name__)


def _create_job(db, uid, data, url):
    ts = now()
    job_id = db.insert(
        "INSERT INTO jobs (user_id,company,title,location,salary,description,url,work_mode,status,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (uid, data["company"], data["title"], data.get("location", ""), data.get("salary", ""), data["description"],
         url, data.get("work_mode", ""), "found", ts, ts))
    db.execute("INSERT INTO status_history (job_id,user_id,status,changed_at) VALUES (?,?,?,?)", (job_id, uid, "found", ts))
    log_activity(uid, f"Vaga importada por link: “{data['title']}” ({data['company']})")
    return job_id


@bp.route("/quick", methods=["GET", "POST"])
@login_required
def quick():
    form = {"url": "", "text": "", "title": "", "company": ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        uid, db = g.user["id"], get_db()
        bundle = pdata.load_bundle(uid)
        if not (bundle["experiences"] or bundle["skills"] or bundle["education"] or bundle["profile"]["summary"]):
            flash("Cadastre seu perfil (ou importe seu currículo) primeiro: o currículo é montado só com os seus dados.", "error")
            return redirect(url_for("profile.edit"))
        url = form["url"]
        if url and not safe_url(url):
            flash("O link precisa começar com http:// ou https://.", "error")
            return render_template("quick.html", form=form, need_text=False)
        if not url and not form["text"]:
            flash("Cole o link da vaga (ou a descrição).", "error")
            return render_template("quick.html", form=form, need_text=False)
        if form["text"]:  # descrição colada pelo usuário (LinkedIn, sites com login, etc.)
            if len(form["text"]) < 80:
                flash("A descrição está curta demais para montar um bom currículo.", "error")
                return render_template("quick.html", form=form, need_text=True)
            data = {"title": form["title"] or "Vaga", "company": form["company"] or "Empresa", "description": form["text"][:20000]}
            if not form["title"] or not form["company"]:
                guess = _guess_header(form["text"])
                data["title"] = form["title"] or guess[0]
                data["company"] = form["company"] or guess[1]
        else:
            try:
                data = jobfetch.read_job(url)
            except jobfetch.FetchError as e:
                flash(str(e), "error")
                return render_template("quick.html", form=form, need_text=True)
            data["company"] = data["company"] or "Empresa"
        job_id = _create_job(db, uid, data, url)
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        res = workflow.build_package(db, uid, job, bundle)
        db.commit()
        flash(("Vaga lida e currículo montado" + (" (SIMULADO: modo demonstrativo)" if res["simulated"] else "") +
               ". Revise, edite se quiser e baixe o PDF. Confira também cargo e empresa, que foram lidos automaticamente."),
              "info" if res["simulated"] else "success")
        return redirect(url_for("apply.apply_page", job_id=job_id))
    return render_template("quick.html", form=form, need_text=False)


def _guess_header(text: str) -> tuple[str, str]:
    first = next((l.strip() for l in text.splitlines() if l.strip()), "Vaga")
    return first[:120], "Empresa"
