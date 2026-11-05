"""Perfil profissional e importação de currículo em PDF."""
import json
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from ..db import WORK_MODES, get_db, log_activity, now
from ..security import login_required, safe_url
from ..services import profile_data as pdata
from ..services import resume_parser

bp = Blueprint("profile", __name__)


def _lists(form, *names):
    cols = [form.getlist(n) for n in names]
    n = max((len(c) for c in cols), default=0)
    return [[(c[i].strip() if i < len(c) else "") for c in cols] for i in range(n)]


def bundle_from_form(form) -> dict:
    b = pdata.empty_bundle()
    for f in pdata.PROFILE_FIELDS:
        b["profile"][f] = form.get(f, "").strip()
    valid = {k for k, _ in WORK_MODES}
    b["profile"]["work_modes"] = [m for m in form.getlist("work_modes") if m in valid]
    for c, t, s, e, d in _lists(form, "exp_company", "exp_title", "exp_start", "exp_end", "exp_desc"):
        if c or t or d:
            b["experiences"].append({"company": c, "title": t, "start": s, "end": e, "description": d})
    for i, d, s, e in _lists(form, "edu_institution", "edu_degree", "edu_start", "edu_end"):
        if i or d:
            b["education"].append({"institution": i, "degree": d, "start": s, "end": e})
    for n, i, y in _lists(form, "course_name", "course_issuer", "course_year"):
        if n:
            b["courses"].append({"name": n, "issuer": i, "year": y})
    seen = set()
    for s in form.get("skills", "").replace("\n", ",").replace(";", ",").split(","):
        s = s.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            b["skills"].append(s)
    return b


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def edit():
    uid, db = g.user["id"], get_db()
    if request.method == "POST":
        b = bundle_from_form(request.form)
        link = b["profile"]["linkedin_url"]
        if link and not safe_url(link):
            flash("O link do LinkedIn precisa começar com http:// ou https://.", "error")
            return render_template("profile.html", b=b, imported=False)
        pdata.save_bundle(uid, b)
        if request.form.get("import_id"):
            db.execute("UPDATE resume_uploads SET parsed_json='' WHERE id=? AND user_id=?",
                       (request.form["import_id"], uid))
        log_activity(uid, "Perfil profissional atualizado")
        db.commit()
        flash("Perfil salvo com sucesso.", "success")
        return redirect(url_for("profile.edit"))

    b, imported, import_id = pdata.load_bundle(uid), False, None
    if rid := request.args.get("import"):
        row = db.execute("SELECT * FROM resume_uploads WHERE id=? AND user_id=?", (rid, uid)).fetchone()
        if row and row["parsed_json"]:
            b, imported, import_id = json.loads(row["parsed_json"]), True, row["id"]
            flash("Dados extraídos do PDF carregados abaixo. Revise, edite o que precisar e clique em "
                  "“Salvar perfil” — nada foi gravado ainda.", "info")
    return render_template("profile.html", b=b, imported=imported, import_id=import_id)


@bp.route("/profile/import", methods=["GET", "POST"])
@login_required
def import_resume():
    uid, db = g.user["id"], get_db()
    if request.method == "POST":
        f = request.files.get("resume")
        if not f or not f.filename:
            flash("Selecione um arquivo PDF.", "error")
            return redirect(url_for("profile.import_resume"))
        if not f.filename.lower().endswith(".pdf"):
            flash("Apenas arquivos PDF são aceitos.", "error")
            return redirect(url_for("profile.import_resume"))
        head = f.stream.read(5)
        f.stream.seek(0)
        if head != b"%PDF-":
            flash("O arquivo enviado não parece ser um PDF válido.", "error")
            return redirect(url_for("profile.import_resume"))
        # O PDF não é guardado: lemos em arquivo temporário e apagamos em seguida (só o texto extraído fica no banco).
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resume.pdf"
            f.save(path)
            try:
                text = resume_parser.extract_text(path)
            except resume_parser.ResumeError as exc:
                flash(str(exc), "error")
                return redirect(url_for("profile.import_resume"))
            except Exception:  # PDF corrompido/criptografado
                current_app.logger.exception("Falha ao ler PDF")
                flash("Não foi possível ler este PDF (pode estar corrompido ou protegido por senha).", "error")
                return redirect(url_for("profile.import_resume"))
        parsed = resume_parser.parse_resume_text(text)
        new_id = db.insert(
            "INSERT INTO resume_uploads (user_id,original_name,stored_name,extracted_text,parsed_json,created_at) "
            "VALUES (?,?,?,?,?,?)", (uid, f.filename[:200], "", text, json.dumps(parsed, ensure_ascii=False), now()))
        log_activity(uid, f"Currículo “{f.filename[:60]}” importado")
        db.commit()
        return redirect(url_for("profile.edit", **{"import": new_id}))

    uploads = db.execute("SELECT id, original_name, created_at FROM resume_uploads WHERE user_id=? ORDER BY id DESC LIMIT 10",
                         (uid,)).fetchall()
    return render_template("import.html", uploads=uploads)


@bp.route("/profile/import/<int:rid>/text")
@login_required
def import_text(rid):
    row = get_db().execute("SELECT * FROM resume_uploads WHERE id=? AND user_id=?", (rid, g.user["id"])).fetchone()
    if row is None:
        flash("Importação não encontrada.", "error")
        return redirect(url_for("profile.import_resume"))
    return render_template("import_text.html", row=row)


@bp.route("/profile/import/<int:rid>/delete", methods=["POST"])
@login_required
def import_delete(rid):
    db = get_db()
    row = db.execute("SELECT * FROM resume_uploads WHERE id=? AND user_id=?", (rid, g.user["id"])).fetchone()
    if row:
        db.execute("DELETE FROM resume_uploads WHERE id=?", (rid,))
        db.commit()
        flash("Arquivo importado removido.", "info")
    return redirect(url_for("profile.import_resume"))
