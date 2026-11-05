"""Autenticação por sessão, proteção CSRF e utilitários de validação."""
import functools
import secrets
from urllib.parse import urlparse

from flask import abort, g, redirect, request, session, url_for

from .db import get_db


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf() -> None:
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("_csrf", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, description="Token de segurança inválido. Recarregue a página e tente de novo.")


def load_user() -> None:
    uid = session.get("user_id")
    g.user = None
    if uid:
        g.user = get_db().execute("SELECT id, name, email FROM users WHERE id=?", (uid,)).fetchone()
        if g.user is None:
            session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def safe_url(value: str) -> str:
    """Retorna a URL se for http(s) válida; senão string vazia."""
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return value
    return ""


def safe_next(target: str) -> str:
    """Evita open redirect: só caminhos locais."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return ""
