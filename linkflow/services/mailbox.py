"""Leitura de e-mails via IMAP (SOMENTE LEITURA) e vínculo com as vagas.

Princípios:
- Nunca envia, apaga, move ou marca mensagens no servidor (usa BODY.PEEK e pasta em modo readonly).
- Só guarda no LinkFlow as mensagens que casam com alguma vaga cadastrada; o resto da caixa é ignorado.
- A senha (de aplicativo) é guardada criptografada (Fernet) com chave derivada da SECRET_KEY local.
"""
import base64
import email
import hashlib
import imaplib
import re
import smtplib
import socket
import ssl
import unicodedata
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

# (rótulo, servidor IMAP, porta IMAP, servidor SMTP, porta SMTP)
PRESETS = {
    "gmail": ("Gmail", "imap.gmail.com", 993, "smtp.gmail.com", 465),
    "outlook": ("Outlook / Hotmail", "outlook.office365.com", 993, "smtp.office365.com", 587),
    "yahoo": ("Yahoo", "imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465),
    "icloud": ("iCloud", "imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "outro": ("Outro (informar servidor)", "", 993, "", 465),
}
JOB_BOARDS = ("linkedin.com", "gupy.io", "indeed.com", "catho.com.br", "greenhouse.io", "lever.co", "workable.com",
              "recruitee.com", "vagas.com.br", "infojobs.com.br", "glassdoor.com", "kenoby.com", "pandape.com.br",
              "inhire.app", "solides.com.br", "smartrecruiters.com")
GENERIC_DOMAINS = {"gmail", "hotmail", "outlook", "yahoo", "icloud", "uol", "bol", "terra", "live", "msn", "protonmail"}
COMPANY_SUFFIXES = {"ltda", "sa", "s", "a", "inc", "llc", "eireli", "me", "epp", "corp", "corporation", "co",
                    "company", "group", "grupo", "do", "da", "de", "dos", "das", "e"}
MAX_HEADERS, MAX_FULL, MAX_BODY, SINCE_DAYS = 300, 80, 20000, 90


class MailError(Exception):
    pass


# ------------------------------------------------------------------ senha criptografada
def _fernet(secret_key: str):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover
        raise MailError("Falta o pacote 'cryptography'. Rode: pip install -r requirements.txt") from exc
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))


def encrypt_password(secret_key: str, password: str) -> str:
    return _fernet(secret_key).encrypt(password.encode()).decode()


def decrypt_password(secret_key: str, token: str) -> str:
    try:
        return _fernet(secret_key).decrypt(token.encode()).decode()
    except MailError:
        raise
    except Exception as exc:
        raise MailError("Não foi possível ler a senha salva (a SECRET_KEY mudou?). Conecte a conta novamente.") from exc


# ------------------------------------------------------------------ IMAP
def _connect(host: str, port: int, username: str, password: str, timeout: int = 30):
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    except (socket.gaierror, socket.timeout, OSError) as exc:
        raise MailError(f"Não foi possível conectar a {host}:{port} ({exc}). Confira o servidor e sua internet.") from exc
    try:
        conn.login(username, password)
    except imaplib.IMAP4.error as exc:
        try:
            conn.logout()
        except Exception:
            pass
        raise MailError("Login recusado pelo servidor. No Gmail/Outlook/Yahoo é preciso usar uma SENHA DE APLICATIVO "
                        "(com verificação em duas etapas ativa), não a senha normal, e o acesso IMAP precisa estar "
                        f"habilitado. Resposta do servidor: {_clean_err(exc)}") from exc
    return conn


def _clean_err(exc) -> str:
    return re.sub(r"\s+", " ", str(exc.args[0] if exc.args else exc)).strip("b'\" ")[:160]


def test_login(host: str, port: int, username: str, password: str) -> None:
    conn = _connect(host, port, username, password)
    try:
        conn.logout()
    except Exception:
        pass


# ------------------------------------------------------------------ texto / casamento
def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _words(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", _fold(s)).strip() + " "


def company_key(company: str) -> tuple[str, str]:
    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", _fold(company)).split() if t not in COMPANY_SUFFIXES]
    return " ".join(tokens), "".join(tokens)


def _domain_labels(addr: str) -> tuple[str, list[str]]:
    domain = _fold(addr.rsplit("@", 1)[-1]) if "@" in addr else ""
    return domain, [l for l in domain.split(".") if l]


def _is_board(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in JOB_BOARDS)


def _url_domain_label(url: str) -> str:
    m = re.match(r"https?://([^/\s]+)", url or "", re.I)
    if not m:
        return ""
    host = _fold(m.group(1)).split(":")[0]
    if _is_board(host):
        return ""
    labels = [l for l in host.split(".") if l and l not in ("www", "com", "br", "org", "net", "io", "co")]
    return labels[-1] if labels else ""


def match_job(jobs, from_name: str, from_addr: str, subject: str, body_head: str | None):
    """Retorna (job_id, motivo) ou (None, ''). `jobs` = linhas com id/company/title/url."""
    domain, labels = _domain_labels(from_addr)
    board = _is_board(domain)
    name_w, subj_w = _words(from_name), _words(subject)
    body_w = _words(body_head[:3000]) if (board and body_head) else ""
    hits = []
    for j in jobs:
        phrase, compact = company_key(j["company"])
        if len(compact) < 3:
            continue
        reason = ""
        needle = f" {phrase} "
        if not board and needle in name_w:
            reason = "nome do remetente"
        elif not board and compact in [l for l in labels if l not in GENERIC_DOMAINS]:
            reason = "domínio do remetente"
        elif not board and (u := _url_domain_label(j["url"])) and u in labels and u not in GENERIC_DOMAINS:
            reason = "domínio da vaga"
        elif needle in subj_w:
            reason = "assunto"
        elif body_w and needle in body_w:
            reason = "texto (via plataforma de vagas)"
        if reason:
            title_w = {w for w in _words(j["title"]).split() if len(w) > 3}
            score = len(title_w & set((subj_w + body_w).split()))
            hits.append((score, j["id"], reason))
    if not hits:
        return None, ""
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)  # mais palavras do cargo; depois a vaga mais recente
    return hits[0][1], hits[0][2]


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def _html_to_text(html: str) -> str:
    p = _Strip()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return "".join(p.out)


def message_text(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        text = part.get_content()
        if part.get_content_type() == "text/html":
            text = _html_to_text(text)
    except Exception:
        return ""
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()[:MAX_BODY]


def _received(msg) -> str:
    try:
        dt = parsedate_to_datetime(str(msg["date"]))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch_items(conn, uids: list[bytes], spec: str):
    """Itera (uid, bytes) de um FETCH por UID."""
    for i in range(0, len(uids), 50):
        chunk = uids[i:i + 50]
        typ, data = conn.uid("FETCH", b",".join(chunk), spec)
        if typ != "OK":
            raise MailError("O servidor recusou a leitura das mensagens.")
        for item in data:
            if isinstance(item, tuple):
                m = re.search(rb"UID (\d+)", item[0])
                if m:
                    yield m.group(1), item[1]


def sync(host: str, port: int, username: str, password: str, folder: str, jobs, known_ids: set[str]):
    """Lê a caixa e devolve (mensagens_analisadas, [dict de mensagens que casam com vagas e ainda não guardadas])."""
    conn = _connect(host, port, username, password)
    found, scanned = [], 0
    try:
        typ, _ = conn.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            raise MailError(f"Não consegui abrir a pasta “{folder}”.")
        since = (datetime.now(timezone.utc) - timedelta(days=SINCE_DAYS)).strftime("%d-%b-%Y")
        typ, data = conn.uid("SEARCH", None, "SINCE", since)
        uids = (data[0].split() if typ == "OK" and data and data[0] else [])[-MAX_HEADERS:]
        candidates: list[tuple[bytes, dict]] = []
        for uid, raw in _fetch_items(conn, uids, "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"):
            scanned += 1
            h = email.message_from_bytes(raw, policy=policy.default)
            mid = (str(h["message-id"] or "").strip()) or f"uid-{host}-{username}-{uid.decode()}"
            if mid in known_ids:
                continue
            name, addr = parseaddr(str(h["from"] or ""))
            info = {"message_id": mid, "from_name": name, "from_addr": addr, "subject": str(h["subject"] or "")}
            job_id, why = match_job(jobs, name, addr, info["subject"], None)
            if job_id or _is_board(_domain_labels(addr)[0]):
                candidates.append((uid, info))
        full_uids = [u for u, _ in candidates][-MAX_FULL:]
        by_uid = dict(candidates)
        for uid, raw in _fetch_items(conn, full_uids, "(UID BODY.PEEK[])"):
            info = by_uid[uid]
            msg = email.message_from_bytes(raw, policy=policy.default)
            body = message_text(msg)
            job_id, why = match_job(jobs, info["from_name"], info["from_addr"], info["subject"], body)
            if job_id:
                found.append({**info, "job_id": job_id, "matched_by": why, "body": body, "received_at": _received(msg)})
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return scanned, found


# ------------------------------------------------------------------ envio (SMTP)
ADDR_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
FIND_ADDR_RE = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
NOREPLY_RE = re.compile(r"no-?reply|do-?not-?reply|naoresponda|nao-responda", re.I)


def default_smtp(imap_host: str) -> tuple[str, int]:
    for _, ih, _, sh, sp in PRESETS.values():
        if ih and ih == imap_host:
            return sh, sp
    return re.sub(r"^imap\.", "smtp.", imap_host), 465


def find_recruiter_emails(text: str) -> list[str]:
    seen, out = set(), []
    for m in FIND_ADDR_RE.findall(text or ""):
        low = m.lower().rstrip(".")
        if low not in seen and not NOREPLY_RE.search(low):
            seen.add(low)
            out.append(low)
    return out


def valid_recipient(addr: str) -> bool:
    return bool(addr) and len(addr) <= 254 and bool(ADDR_RE.match(addr)) and ".." not in addr


def build_application_email(from_name: str, from_addr: str, to_addr: str, subject: str, body: str,
                            attachments: list[tuple[str, bytes]]) -> EmailMessage:
    for field in (from_name, from_addr, to_addr, subject):
        if re.search(r"[\r\n]", field or ""):
            raise MailError("Quebras de linha não são permitidas no destinatário ou no assunto.")
    if not valid_recipient(to_addr):
        raise MailError("Informe um único e-mail de destinatário válido.")
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.rsplit("@", 1)[-1])
    msg.set_content(body)
    for filename, data in attachments:
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
    return msg


def send_message(host: str, port: int, username: str, password: str, msg: EmailMessage) -> None:
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls(context=ssl.create_default_context())
        try:
            server.login(username, password)
            refused = server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("O servidor SMTP recusou o login. Use a senha de aplicativo e confirme que o envio (SMTP) "
                        f"está habilitado na sua conta. Resposta: {_clean_err(exc)}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError("O servidor recusou o destinatário. Confira o e-mail do recrutador.") from exc
    except (smtplib.SMTPException, socket.gaierror, socket.timeout, OSError) as exc:
        raise MailError(f"Falha ao enviar pelo servidor {host}:{port}: {_clean_err(exc)}") from exc
    if refused:
        raise MailError("O servidor recusou o destinatário. Confira o e-mail do recrutador.")
