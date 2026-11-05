"""Documentos gerados: salvar, editar, listar, excluir e exportar em PDF."""
import re
import unicodedata

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db, log_activity, now, set_job_status
from ..security import login_required
from ..services.pdf_export import text_to_pdf

bp = Blueprint("documents", __name__)
KIND_LABELS = {"resume": "Currículo", "cover": "Carta de apresentação"}


def _get_doc(doc_id: int):
    doc = get_db().execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, g.user["id"])).fetchone()
    if doc is None:
        abort(404, description="Documento não encontrado.")
    return doc


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:60] or "documento"


@bp.route("/documents")
@login_required
def list_docs():
    docs = get_db().execute(
        """SELECT d.*, j.title AS job_title, j.company AS job_company FROM documents d
           LEFT JOIN jobs j ON j.id = d.job_id WHERE d.user_id=? ORDER BY d.updated_at DESC""",
        (g.user["id"],)).fetchall()
    return render_template("documents.html", docs=docs, KIND_LABELS=KIND_LABELS)


@bp.route("/documents/<int:doc_id>")
@login_required
def edit(doc_id):
    doc = _get_doc(doc_id)
    job = None
    if doc["job_id"]:
        job = get_db().execute("SELECT * FROM jobs WHERE id=?", (doc["job_id"],)).fetchone()
    return render_template("document_edit.html", doc=doc, job=job, kind=doc["kind"], title=doc["title"],
                           content=doc["content"], simulated=bool(doc["simulated"]), note="")


@bp.route("/documents/save", methods=["POST"])
@login_required
def save():
    db, uid, f = get_db(), g.user["id"], request.form
    title, content = f.get("title", "").strip(), f.get("content", "").strip()
    kind = f.get("kind", "")
    if kind not in KIND_LABELS or not title or not content:
        flash("Título e conteúdo são obrigatórios.", "error")
        return redirect(request.referrer or url_for("documents.list_docs"))
    simulated = 1 if f.get("simulated") == "1" else 0
    job_id = f.get("job_id", type=int)
    job = db.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, uid)).fetchone() if job_id else None
    ts = now()
    if doc_id := f.get("doc_id", type=int):
        _get_doc(doc_id)
        db.execute("UPDATE documents SET title=?, content=?, updated_at=? WHERE id=?", (title, content, ts, doc_id))
    else:
        doc_id = db.insert(
            "INSERT INTO documents (user_id,job_id,kind,title,content,simulated,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (uid, job["id"] if job else None, kind, title, content, simulated, ts, ts))
    log_activity(uid, f"{KIND_LABELS[kind]} salvo: “{title[:70]}”")
    if job and f.get("mark_ready") == "1" and job["status"] in ("found", "analyzed"):
        set_job_status(uid, job["id"], "ready")
    db.commit()
    flash("Documento salvo.", "success")
    return redirect(url_for("documents.edit", doc_id=doc_id))


@bp.route("/documents/pdf", methods=["POST"])
@login_required
def pdf():
    """Exporta para PDF o conteúdo atual do editor (salvo ou não)."""
    content, title = request.form.get("content", ""), request.form.get("title", "").strip() or "Documento"
    if not content.strip():
        abort(400, description="Nada para exportar: o documento está vazio.")
    data = text_to_pdf(content, title)
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_slug(title)}.pdf"'})


@bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete(doc_id):
    doc = _get_doc(doc_id)
    db = get_db()
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    log_activity(g.user["id"], f"Documento removido: “{doc['title'][:70]}”")
    db.commit()
    flash("Documento removido.", "info")
    return redirect(url_for("documents.list_docs"))
