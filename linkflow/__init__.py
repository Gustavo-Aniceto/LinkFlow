"""LinkFlow — fábrica da aplicação Flask."""
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .config import build_config
from .db import STATUSES, STATUS_LABELS, WORK_MODES, init_db
from .security import check_csrf, csrf_token, load_user
from .services import ai


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    overrides = dict(overrides or {})
    app.config.update(build_config(overrides.get("DATA_DIR")))
    if "DATABASE" not in overrides:
        overrides.pop("DATABASE", None)
    app.config.update(overrides)

    init_db(app)

    @app.before_request
    def _prepare_database():  # Postgres: cria as tabelas no primeiro acesso (não no import)
        from .db import ensure_schema
        ensure_schema(app)

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.endpoint not in ("static",):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    from .blueprints import apply, auth, documents, jobs, mail, main, profile
    for bp in (auth.bp, main.bp, profile.bp, jobs.bp, apply.bp, documents.bp, mail.bp):
        app.register_blueprint(bp)

    app.before_request(load_user)
    app.before_request(check_csrf)

    @app.template_filter("localdt")
    def localdt(value, fmt="%d/%m/%Y %H:%M"):
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value).astimezone().strftime(fmt)
        except ValueError:
            return value

    # Globais: ficam visíveis também dentro de macros importados (que não recebem o contexto).
    app.jinja_env.globals.update(csrf_token=csrf_token, STATUSES=STATUSES, STATUS_LABELS=STATUS_LABELS,
                                 WORK_MODES=WORK_MODES, WORK_MODE_LABELS=dict(WORK_MODES))

    @app.context_processor
    def inject():
        unread = 0
        if g.get("user"):
            from .db import get_db
            try:
                unread = get_db().execute("SELECT COUNT(*) FROM emails WHERE user_id=? AND is_read=0 AND dismissed=0",
                                          (g.user["id"],)).fetchone()[0]
            except Exception:
                unread = 0
        return {"ai_provider": ai.provider(), "ai_provider_label": ai.provider_label(),
                "current_user": g.get("user"), "unread_emails": unread}

    def wants_json() -> bool:
        return request.path.startswith("/api/") or request.is_json or \
            request.headers.get("X-Requested-With") == "fetch"

    @app.errorhandler(HTTPException)
    def http_error(err):
        if wants_json():
            return jsonify(error=err.description), err.code
        titles = {400: "Requisição inválida", 404: "Página não encontrada",
                  413: "Arquivo muito grande (máximo 4 MB)", 405: "Método não permitido"}
        return render_template("error.html", code=err.code, title=titles.get(err.code, err.name),
                               message=err.description), err.code

    @app.errorhandler(Exception)
    def server_error(err):
        app.logger.exception("Erro inesperado")
        if wants_json():
            return jsonify(error="Erro interno. Veja o log do servidor."), 500
        return render_template("error.html", code=500, title="Algo deu errado",
                               message="Ocorreu um erro inesperado. Os detalhes foram registrados no log."), 500

    return app
