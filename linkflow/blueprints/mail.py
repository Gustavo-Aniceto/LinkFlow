"""E-mail: conecta uma caixa IMAP (somente leitura) e mostra respostas das empresas ligadas às vagas."""
import re

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from ..db import get_db, log_activity, now
from ..security import login_required
from ..services import mailbox

bp = Blueprint("mail", __name__)
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _account():
    return get_db().execute("SELECT * FROM email_accounts WHERE user_id=?", (g.user["id"],)).fetchone()


def _get_email(email_id: int):
    row = get_db().execute("SELECT e.*, j.title AS job_title, j.company AS job_company, j.status AS job_status "
                           "FROM emails e LEFT JOIN jobs j ON j.id = e.job_id WHERE e.id=? AND e.user_id=? AND e.dismissed=0",
                           (email_id, g.user["id"])).fetchone()
    if row is None:
        abort(404, description="Mensagem não encontrada.")
    return row


@bp.route("/inbox")
@login_required
def inbox():
    db, uid = get_db(), g.user["id"]
    acc = _account()
    flt = request.args.get("f", "all")
    sql = ("SELECT e.*, j.title AS job_title, j.company AS job_company FROM emails e "
           "LEFT JOIN jobs j ON j.id = e.job_id WHERE e.user_id=? AND e.dismissed=0")
    args = [uid]
    if flt == "unread":
        sql += " AND e.is_read=0"
    elif flt == "nolink":
        sql += " AND e.job_id IS NULL"
    msgs = db.execute(sql + " ORDER BY e.received_at DESC LIMIT 200", args).fetchall()
    unread = db.execute("SELECT COUNT(*) FROM emails WHERE user_id=? AND is_read=0 AND dismissed=0", (uid,)).fetchone()[0]
    return render_template("inbox.html", acc=acc, msgs=msgs, flt=flt, unread=unread, PRESETS=mailbox.PRESETS,
                           since_days=mailbox.SINCE_DAYS)


@bp.route("/inbox/connect", methods=["POST"])
@login_required
def connect():
    f = request.form
    preset = f.get("preset", "gmail")
    host = (f.get("host", "").strip() or mailbox.PRESETS.get(preset, ("", "", 993, "", 465))[1])
    username, password = f.get("username", "").strip(), f.get("password", "").replace(" ", "")
    folder = f.get("folder", "").strip() or "INBOX"
    try:
        port = int(f.get("port") or 993)
    except ValueError:
        port = 0
    smtp_host = f.get("smtp_host", "").strip()
    try:
        smtp_port = int(f.get("smtp_port") or 0)
    except ValueError:
        smtp_port = -1
    if not smtp_host or not smtp_port:
        d_host, d_port = mailbox.default_smtp(host)
        smtp_host, smtp_port = smtp_host or d_host, smtp_port or d_port
    error = None
    if not HOST_RE.match(host or ""):
        error = "Informe um servidor IMAP válido (ex.: imap.gmail.com)."
    elif not 1 <= port <= 65535:
        error = "Porta inválida."
    elif not HOST_RE.match(smtp_host or "") or not 1 <= smtp_port <= 65535:
        error = "Servidor/porta SMTP inválidos."
    elif not username or not password:
        error = "Informe o e-mail e a senha de aplicativo."
    elif len(folder) > 100 or re.search(r'["\r\n]', folder):
        error = "Nome de pasta inválido."
    if not error:
        try:
            mailbox.test_login(host, port, username, password)
            enc = mailbox.encrypt_password(current_app.config["SECRET_KEY"], password)
        except mailbox.MailError as exc:
            error = str(exc)
    if error:
        flash(error, "error")
        return redirect(url_for("mail.inbox"))
    db = get_db()
    db.execute(
        """INSERT INTO email_accounts (user_id,host,port,username,password_enc,folder,smtp_host,smtp_port,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET host=excluded.host, port=excluded.port, username=excluded.username,
               password_enc=excluded.password_enc, folder=excluded.folder, smtp_host=excluded.smtp_host,
               smtp_port=excluded.smtp_port""",
        (g.user["id"], host, port, username, enc, folder, smtp_host, smtp_port, now()))
    log_activity(g.user["id"], f"Caixa de e-mail conectada ({username})")
    db.commit()
    flash("Conta conectada. Clique em “Sincronizar agora” para buscar respostas das empresas.", "success")
    return redirect(url_for("mail.inbox"))


@bp.route("/inbox/sync", methods=["POST"])
@login_required
def sync_now():
    acc, db, uid = _account(), get_db(), g.user["id"]
    if acc is None:
        flash("Conecte uma conta de e-mail primeiro.", "error")
        return redirect(url_for("mail.inbox"))
    jobs = db.execute("SELECT id, company, title, url FROM jobs WHERE user_id=?", (uid,)).fetchall()
    if not jobs:
        flash("Cadastre ao menos uma vaga: só guardamos e-mails que casam com suas vagas.", "error")
        return redirect(url_for("mail.inbox"))
    known = {r[0] for r in db.execute("SELECT message_id FROM emails WHERE user_id=?", (uid,))}
    try:
        pwd = mailbox.decrypt_password(current_app.config["SECRET_KEY"], acc["password_enc"])
        scanned, found = mailbox.sync(acc["host"], acc["port"], acc["username"], pwd, acc["folder"], jobs, known)
    except mailbox.MailError as exc:
        flash(str(exc), "error")
        return redirect(url_for("mail.inbox"))
    except Exception:
        current_app.logger.exception("Falha na sincronização de e-mail")
        flash("Erro inesperado ao ler a caixa de e-mail. Veja o log do servidor.", "error")
        return redirect(url_for("mail.inbox"))
    new = 0
    for m in found:
        cur = db.execute(
            """INSERT INTO emails (user_id,job_id,message_id,from_name,from_addr,subject,received_at,body,matched_by,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (user_id, message_id) DO NOTHING""",
            (uid, m["job_id"], m["message_id"], m["from_name"], m["from_addr"], m["subject"], m["received_at"],
             m["body"], m["matched_by"], now()))
        new += cur.rowcount
    db.execute("UPDATE email_accounts SET last_sync=?, last_scanned=? WHERE user_id=?", (now(), scanned, uid))
    if new:
        log_activity(uid, f"{new} nova(s) mensagem(ns) de empresas encontrada(s) no e-mail")
    db.commit()
    flash(f"Sincronizado: {scanned} mensagens recentes verificadas, {new} nova(s) ligada(s) às suas vagas.",
          "success" if new else "info")
    return redirect(url_for("mail.inbox"))


@bp.route("/inbox/disconnect", methods=["POST"])
@login_required
def disconnect():
    db, uid = get_db(), g.user["id"]
    db.execute("DELETE FROM email_accounts WHERE user_id=?", (uid,))
    if request.form.get("delete_emails") == "1":
        db.execute("DELETE FROM emails WHERE user_id=?", (uid,))
    log_activity(uid, "Caixa de e-mail desconectada")
    db.commit()
    flash("Conta desconectada e senha removida." + (" Mensagens guardadas foram apagadas." if request.form.get("delete_emails") == "1" else ""), "info")
    return redirect(url_for("mail.inbox"))


@bp.route("/inbox/<int:email_id>")
@login_required
def view(email_id):
    msg = _get_email(email_id)
    db = get_db()
    if not msg["is_read"]:
        db.execute("UPDATE emails SET is_read=1 WHERE id=?", (email_id,))
        db.commit()
    jobs = db.execute("SELECT id, title, company FROM jobs WHERE user_id=? ORDER BY company, title", (g.user["id"],)).fetchall()
    return render_template("email_view.html", m=msg, jobs=jobs)


@bp.route("/inbox/<int:email_id>/link", methods=["POST"])
@login_required
def relink(email_id):
    _get_email(email_id)
    db = get_db()
    job_id = request.form.get("job_id", type=int)
    if job_id and not db.execute("SELECT 1 FROM jobs WHERE id=? AND user_id=?", (job_id, g.user["id"])).fetchone():
        abort(404, description="Vaga não encontrada.")
    db.execute("UPDATE emails SET job_id=?, matched_by=? WHERE id=?", (job_id, "vínculo manual" if job_id else "", email_id))
    db.commit()
    flash("Vínculo atualizado.", "success")
    return redirect(url_for("mail.view", email_id=email_id))


@bp.route("/inbox/<int:email_id>/unread", methods=["POST"])
@login_required
def mark_unread(email_id):
    _get_email(email_id)
    get_db().execute("UPDATE emails SET is_read=0 WHERE id=?", (email_id,))
    get_db().commit()
    return redirect(url_for("mail.inbox"))


@bp.route("/inbox/<int:email_id>/delete", methods=["POST"])
@login_required
def delete(email_id):
    _get_email(email_id)
    get_db().execute("UPDATE emails SET dismissed=1, job_id=NULL, body='', subject='', from_name='', from_addr='', "
                     "is_read=1 WHERE id=?", (email_id,))
    get_db().commit()
    flash("Mensagem removida do LinkFlow (a original continua na sua caixa de e-mail).", "info")
    return redirect(url_for("mail.inbox"))
