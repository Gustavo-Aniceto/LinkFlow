"""Cadastro, login e logout."""
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import get_db, now
from ..security import safe_next

bp = Blueprint("auth", __name__)
MAX_FAILED_LOGINS = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        error = None
        if not name:
            error = "Informe seu nome."
        elif not EMAIL_RE.match(email):
            error = "Informe um e-mail válido."
        elif len(password) < 8:
            error = "A senha precisa ter pelo menos 8 caracteres."
        elif password != request.form.get("password2", ""):
            error = "As senhas não conferem."
        elif current_app.config["ALLOWED_EMAILS"] and email not in current_app.config["ALLOWED_EMAILS"]:
            error = "O cadastro neste site é restrito. Este e-mail não está autorizado."
        if error is None:
            db = get_db()
            if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                error = "Este e-mail já está cadastrado."
            else:
                new_id = db.insert("INSERT INTO users (name,email,password_hash,created_at) VALUES (?,?,?,?)",
                                   (name, email, generate_password_hash(password), now()))
                db.execute("INSERT INTO profiles (user_id, full_name, email, updated_at) VALUES (?,?,?,?)",
                           (new_id, name, email, now()))
                db.commit()
                session.clear()
                session["user_id"] = new_id
                flash("Conta criada! Comece completando seu perfil ou importando um currículo.", "success")
                return redirect(url_for("profile.edit"))
        flash(error, "error")
    return render_template("auth.html", mode="register")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        db = get_db()
        since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
        recent = db.execute("SELECT COUNT(*) FROM login_attempts WHERE key=? AND created_at>?", (email, since)).fetchone()[0]
        if recent >= MAX_FAILED_LOGINS:
            flash("Muitas tentativas de login incorretas. Aguarde 15 minutos e tente de novo.", "error")
            return render_template("auth.html", mode="login"), 429
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            db.execute("DELETE FROM login_attempts WHERE key=?", (email,))
            db.commit()
            session.clear()
            session["user_id"] = user["id"]
            return redirect(safe_next(request.args.get("next", "")) or url_for("main.dashboard"))
        db.execute("INSERT INTO login_attempts (key, created_at) VALUES (?,?)", (email, now()))
        db.commit()
        flash("E-mail ou senha incorretos.", "error")
    return render_template("auth.html", mode="login")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Você saiu da conta.", "info")
    return redirect(url_for("auth.login"))
