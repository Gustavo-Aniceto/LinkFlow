"""Banco de dados: SQLite (local) ou Postgres (produção, via DATABASE_URL).

O restante do código usa só `get_db()` e esta API pequena:
    db.execute(sql, args) -> cursor (fetchone/fetchall/rowcount); use `?` como placeholder
    db.insert(sql, args)  -> id da linha criada
    db.commit()
As linhas são dicionários que também aceitam índice numérico (row[0]).
"""
import re
import sqlite3
from datetime import datetime, timezone

from flask import current_app, g

STATUSES = [
    ("found", "Encontrada"),
    ("analyzed", "Analisada"),
    ("ready", "Pronta para candidatura"),
    ("applied", "Candidatura realizada"),
    ("interview", "Entrevista"),
    ("approved", "Aprovado"),
    ("rejected", "Reprovado"),
]
STATUS_KEYS = [k for k, _ in STATUSES]
STATUS_LABELS = dict(STATUSES)
APPLICATION_STATUSES = ("applied", "interview", "approved", "rejected")
WORK_MODES = [("remoto", "Remoto"), ("hibrido", "Híbrido"), ("presencial", "Presencial")]

SCHEMA_VERSION = "3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    full_name TEXT DEFAULT '', email TEXT DEFAULT '', phone TEXT DEFAULT '',
    location TEXT DEFAULT '', headline TEXT DEFAULT '', summary TEXT DEFAULT '',
    linkedin_url TEXT DEFAULT '', salary_expectation TEXT DEFAULT '',
    work_modes TEXT DEFAULT '', updated_at TEXT
);
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position INTEGER DEFAULT 0,
    company TEXT DEFAULT '', title TEXT DEFAULT '', "start" TEXT DEFAULT '',
    "end" TEXT DEFAULT '', description TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS education (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position INTEGER DEFAULT 0,
    institution TEXT DEFAULT '', degree TEXT DEFAULT '', "start" TEXT DEFAULT '', "end" TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position INTEGER DEFAULT 0,
    name TEXT DEFAULT '', issuer TEXT DEFAULT '', year TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position INTEGER DEFAULT 0,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resume_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL, stored_name TEXT NOT NULL DEFAULT '',
    extracted_text TEXT DEFAULT '', parsed_json TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company TEXT NOT NULL, title TEXT NOT NULL, location TEXT DEFAULT '',
    salary TEXT DEFAULT '', description TEXT DEFAULT '', url TEXT DEFAULT '',
    work_mode TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'found',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score INTEGER, result_json TEXT NOT NULL, simulated INTEGER NOT NULL DEFAULT 1,
    provider TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_accounts (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 993, username TEXT NOT NULL,
    password_enc TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'INBOX',
    smtp_host TEXT NOT NULL DEFAULT '', smtp_port INTEGER NOT NULL DEFAULT 0,
    last_sync TEXT, last_scanned INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    message_id TEXT NOT NULL, from_name TEXT DEFAULT '', from_addr TEXT DEFAULT '',
    subject TEXT DEFAULT '', received_at TEXT, body TEXT DEFAULT '',
    matched_by TEXT DEFAULT '', is_read INTEGER NOT NULL DEFAULT 0, dismissed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, message_id)
);
CREATE TABLE IF NOT EXISTS sent_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    to_addr TEXT NOT NULL, subject TEXT NOT NULL, attachments TEXT DEFAULT '', sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_hist_user ON status_history(user_id, status, changed_at);
CREATE INDEX IF NOT EXISTS idx_act_user ON activities(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_emails_user ON emails(user_id, received_at);
CREATE INDEX IF NOT EXISTS idx_sent_user ON sent_applications(user_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_login_key ON login_attempts(key, created_at)
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Row(dict):
    """Linha de resultado: acesso por nome (row["x"]) ou por posição (row[0])."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _sqlite_row_factory(cursor, row):
    return Row(zip([c[0] for c in cursor.description], row))


def _pg_row_factory(cursor):
    cols = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(cols, values))


class DB:
    def __init__(self, conn, dialect: str):
        self.conn, self.dialect = conn, dialect

    def _sql(self, sql: str) -> str:
        if self.dialect == "postgres":
            sql = sql.replace("?", "%s")
            sql = re.sub(r"\bLIKE\b", "ILIKE", sql)  # SQLite ignora maiúsculas no LIKE; Postgres não
        return sql

    def execute(self, sql: str, args=()):
        return self.conn.execute(self._sql(sql), tuple(args))

    def insert(self, sql: str, args=()) -> int:
        if self.dialect == "postgres":
            return self.conn.execute(self._sql(sql) + " RETURNING id", tuple(args)).fetchone()[0]
        return self.conn.execute(sql, tuple(args)).lastrowid

    def commit(self):
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def _statements(dialect: str) -> list[str]:
    ddl = SCHEMA
    if dialect == "postgres":
        ddl = ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    return [s.strip() for s in ddl.split(";\n") if s.strip()]


def database_url(app=None) -> str:
    return (app or current_app).config.get("DATABASE_URL", "")


def _connect(app) -> DB:
    url = database_url(app)
    if url:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Falta o pacote psycopg (pip install -r requirements.txt).") from exc
        conn = psycopg.connect(url, row_factory=_pg_row_factory, prepare_threshold=None, connect_timeout=10)
        return DB(conn, "postgres")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = _sqlite_row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return DB(conn, "sqlite")


_schema_ready: set = set()


def ensure_schema(app) -> None:
    """Cria/atualiza as tabelas. No Postgres só roda se a versão do schema mudou."""
    key = database_url(app) or app.config["DATABASE"]
    if key in _schema_ready:
        return
    db = _connect(app)
    try:
        if db.dialect == "postgres":
            # Lock por transação: funciona também atrás do pooler (pgbouncer) do Neon, onde locks de sessão não valem.
            try:
                db.execute("SELECT pg_advisory_xact_lock(727274)")  # evita corrida entre cold starts
                db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if not row or row["value"] != SCHEMA_VERSION:
                    for stmt in _statements("postgres"):
                        db.execute(stmt)
                    db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                               "ON CONFLICT (key) DO UPDATE SET value=excluded.value", (SCHEMA_VERSION,))
                db.commit()
            except Exception:
                db.conn.rollback()
                raise
        else:
            for stmt in _statements("sqlite"):
                db.execute(stmt)
            _sqlite_upgrade(db)
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            db.commit()
    finally:
        db.close()
    _schema_ready.add(key)


def _sqlite_upgrade(db: DB) -> None:
    """Colunas novas em bancos SQLite criados por versões anteriores."""
    def cols(table):
        return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
    if "smtp_host" not in cols("email_accounts"):
        db.execute("ALTER TABLE email_accounts ADD COLUMN smtp_host TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE email_accounts ADD COLUMN smtp_port INTEGER NOT NULL DEFAULT 0")
    if "dismissed" not in cols("emails"):
        db.execute("ALTER TABLE emails ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0")


def get_db() -> DB:
    if "db" not in g:
        g.db = _connect(current_app)
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()  # descarta transação aberta (leituras) sem efeito colateral


def init_db(app) -> None:
    app.teardown_appcontext(close_db)
    if not database_url(app):
        ensure_schema(app)  # SQLite: imediato; Postgres: no primeiro request (ver create_app)


def log_activity(user_id: int, message: str) -> None:
    get_db().execute("INSERT INTO activities (user_id, message, created_at) VALUES (?,?,?)",
                     (user_id, message, now()))


def set_job_status(user_id: int, job_id: int, status: str, quiet: bool = False) -> None:
    """Altera o status de uma vaga, registra histórico e atividade."""
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
    if job is None or job["status"] == status:
        return
    ts = now()
    db.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (status, ts, job_id))
    db.execute("INSERT INTO status_history (job_id, user_id, status, changed_at) VALUES (?,?,?,?)",
               (job_id, user_id, status, ts))
    if not quiet:
        log_activity(user_id, f"Vaga “{job['title']}” ({job['company']}) → {STATUS_LABELS.get(status, status)}")
