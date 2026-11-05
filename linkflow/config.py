"""Configuração da aplicação (lê variáveis de ambiente e o arquivo .env)."""
import os
import secrets
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ON_VERCEL = bool(os.environ.get("VERCEL"))


def load_env_file(path: Path = ROOT / ".env") -> None:
    """Carrega pares CHAVE=VALOR de um .env simples (sem sobrescrever o ambiente)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _csv(value: str) -> list[str]:
    return [v.strip().lower() for v in (value or "").replace(";", ",").split(",") if v.strip()]


def build_config(data_dir_override=None) -> dict:
    load_env_file()
    if data_dir_override:
        data_dir = Path(data_dir_override).resolve()
    elif ON_VERCEL:
        data_dir = Path(tempfile.gettempdir()) / "linkflow"  # único local gravável na Vercel (efêmero)
    else:
        data_dir = Path(os.environ.get("LINKFLOW_DATA_DIR") or os.environ.get("APPLYFLOW_DATA_DIR") or ROOT / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Migração do nome antigo (ApplyFlow): reaproveita o banco existente.
    old_db, new_db = data_dir / "applyflow.db", data_dir / "linkflow.db"
    if old_db.exists() and not new_db.exists():
        for suffix in ("", "-wal", "-shm"):
            if (data_dir / f"applyflow.db{suffix}").exists():
                (data_dir / f"applyflow.db{suffix}").rename(data_dir / f"linkflow.db{suffix}")

    secret = os.environ.get("SECRET_KEY", "").strip()
    if not secret:
        if ON_VERCEL:
            raise RuntimeError("Defina a variável de ambiente SECRET_KEY no projeto da Vercel.")
        key_file = data_dir / ".secret_key"
        if key_file.exists():
            secret = key_file.read_text().strip()
        else:
            secret = secrets.token_hex(32)
            key_file.write_text(secret)

    database_url = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]

    return {
        "SECRET_KEY": secret,
        "DATA_DIR": data_dir,
        "DATABASE": str(data_dir / "linkflow.db"),
        "DATABASE_URL": database_url,
        "MAX_CONTENT_LENGTH": 4 * 1024 * 1024,  # 4 MB (limite de corpo das funções da Vercel é 4,5 MB)
        "ALLOWED_EMAILS": _csv(os.environ.get("ALLOWED_EMAILS", "")),
        "MAX_SENDS_PER_DAY": int(os.environ.get("MAX_SENDS_PER_DAY", "10")),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip(),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "").strip(),
        "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip(),
        "AI_TIMEOUT": int(os.environ.get("AI_TIMEOUT", "45" if ON_VERCEL else "90")),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": ON_VERCEL,
        "PERMANENT_SESSION_LIFETIME": 60 * 60 * 24 * 14,
    }
