"""Testa a integração de e-mail com um servidor IMAP simulado. Execute: python tests/test_mail.py"""
import imaplib
import re
import sqlite3
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from linkflow import create_app  # noqa: E402


def msg(frm, subject, text=None, html=None, mid=None, date="Wed, 04 Nov 2026 10:00:00 -0300"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, "maria@x.com", subject, date
    m["Message-ID"] = mid or f"<{abs(hash((frm, subject)))}@test>"
    if html is not None:
        m.set_content("fallback")
        m.add_alternative(html, subtype="html")
        if text is None:
            m.get_payload()[0].set_payload("")  # só HTML
    else:
        m.set_content(text or "")
    return m.as_bytes()


MESSAGES = {
    1: msg("Gama SA RH <rh@gama.com.br>", "Convite para entrevista - Analista de BI", "Olá Maria, gostaríamos de agendar uma entrevista.\n<script>alert(1)</script>"),
    2: msg("LinkedIn <jobs-noreply@linkedin.com>", "Sua candidatura foi visualizada", "Sua candidatura para Analista de BI na Gama SA foi visualizada."),
    3: msg("Loja <news@loja.com>", "Promoção imperdível", "Compre agora"),
    4: msg("Talent <talent@deltatech.com>", "Próximos passos", html="<html><body><p>Olá!</p><style>p{}</style><p>Segue o teste técnico da <b>Delta Tech</b>.</p></body></html>"),
    5: msg("João <joao@gmail.com>", "Reunião de sábado", "Vamos?"),
    6: msg("LinkedIn <jobs-noreply@linkedin.com>", "Vagas para você", "Veja vagas na Outra Empresa Qualquer"),
}


class FakeIMAP:
    fetched_full = []
    readonly_ok = True

    def __init__(self, host, port=993, timeout=None):
        self.host = host

    def login(self, user, pwd):
        if pwd != "goodpass":
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    def select(self, folder, readonly=False):
        FakeIMAP.readonly_ok &= bool(readonly)
        return "OK", [b"6"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in MESSAGES)]
        uids = [int(x) for x in args[0].split(b",")]
        spec = args[1]
        assert "PEEK" in spec, "deve usar BODY.PEEK para não marcar como lida"
        out = []
        for u in uids:
            raw = MESSAGES[u]
            if "HEADER.FIELDS" in spec:
                head = raw.split(b"\n\n", 1)[0] + b"\n\n"
                out.append((f"{u} (UID {u} BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] {{{len(head)}}}".encode(), head))
            else:
                FakeIMAP.fetched_full.append(u)
                out.append((f"{u} (UID {u} BODY[] {{{len(raw)}}}".encode(), raw))
            out.append(b")")
        return "OK", out

    def logout(self):
        pass


def check(cond, text):
    print(("✔ " if cond else "✘ ") + text)
    cond or sys.exit(1)


def main():
    tmp = Path(tempfile.mkdtemp())
    app = create_app({"DATA_DIR": str(tmp), "TESTING": True, "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""})
    c = app.test_client()
    tok = lambda p: re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa: E731
    c.post("/register", data={"_csrf": tok("/register"), "name": "Maria", "email": "maria@x.com", "password": "senha1234", "password2": "senha1234"})

    with mock.patch("imaplib.IMAP4_SSL", FakeIMAP):
        t = tok("/inbox")
        page = c.get("/inbox").get_data(as_text=True)
        check("Conectar sua caixa de e-mail" in page, "inbox sem conta mostra o formulário de conexão")
        r = c.post("/inbox/sync", data={"_csrf": t}, follow_redirects=True)
        check("Conecte uma conta" in r.get_data(as_text=True), "sincronizar sem conta é recusado")

        r = c.post("/inbox/connect", data={"_csrf": t, "preset": "gmail", "host": "imap.gmail.com", "username": "maria@x.com", "password": "errada", "port": "993", "folder": "INBOX"}, follow_redirects=True)
        check("SENHA DE APLICATIVO" in r.get_data(as_text=True), "senha errada: mensagem orienta sobre senha de app")
        r = c.post("/inbox/connect", data={"_csrf": t, "host": "bad host!", "username": "a@b.com", "password": "x"}, follow_redirects=True)
        check("servidor IMAP válido" in r.get_data(as_text=True), "servidor inválido é rejeitado")
        r = c.post("/inbox/connect", data={"_csrf": t, "preset": "gmail", "host": "imap.gmail.com", "username": "maria@x.com", "password": "good pass", "port": "993", "folder": "INBOX"}, follow_redirects=True)
        check("Conta conectada" in r.get_data(as_text=True), "conexão com senha correta (espaços ignorados)")
        enc = sqlite3.connect(str(tmp / "linkflow.db")).execute("select password_enc from email_accounts").fetchone()[0]
        check("goodpass" not in enc and enc.startswith("gAAAA"), "senha armazenada criptografada (não em texto puro)")

        r = c.post("/inbox/sync", data={"_csrf": t}, follow_redirects=True)
        check("Cadastre ao menos uma vaga" in r.get_data(as_text=True), "sem vagas não guarda nada")

        for title, company, url in [("Analista de BI", "Gama SA", "https://www.linkedin.com/jobs/view/1"),
                                    ("Cientista de Dados", "Delta Tech", "https://deltatech.com/vagas/9"),
                                    ("Dev", "Zeta Ltda", "")]:
            c.post("/jobs/new", data={"_csrf": t, "title": title, "company": company, "url": url, "description": "x"})

        r = c.post("/inbox/sync", data={"_csrf": t}, follow_redirects=True)
        page = r.get_data(as_text=True)
        check("3 nova(s)" in page, "sincronização guardou exatamente 3 mensagens de empresas")
        check(FakeIMAP.readonly_ok, "pasta aberta somente em modo leitura (readonly)")
        check(set(FakeIMAP.fetched_full) == {1, 2, 4, 6}, "corpo completo só baixado de candidatas (empresa/plataforma de vagas)")
        check("Promoção imperdível" not in page and "Reunião de sábado" not in page, "e-mails não relacionados NÃO são guardados")
        check("Vagas para você" not in page, "e-mail do LinkedIn sem empresa das vagas é ignorado")
        check("Convite para entrevista" in page and "Sua candidatura foi visualizada" in page and "Próximos passos" in page, "mensagens ligadas aparecem na caixa")
        check('class="nbadge">3<' in page, "contador de não lidas no menu")

        db = sqlite3.connect(str(tmp / "linkflow.db"))
        rows = {r[0]: (r[1], r[2]) for r in db.execute("select subject, matched_by, (select company from jobs where jobs.id=emails.job_id) from emails")}
        check(rows["Convite para entrevista - Analista de BI"][1] == "Gama SA", "vínculo correto (remetente → Gama SA)")
        check(rows["Sua candidatura foi visualizada"][1] == "Gama SA" and "plataforma" in rows["Sua candidatura foi visualizada"][0], "LinkedIn: vínculo pelo texto do e-mail")
        check(rows["Próximos passos"][1] == "Delta Tech", "domínio deltatech.com → Delta Tech")

        r = c.post("/inbox/sync", data={"_csrf": t}, follow_redirects=True)
        check("0 nova(s)" in r.get_data(as_text=True) and db.execute("select count(*) from emails").fetchone()[0] == 3, "segunda sincronização não duplica")

        eid = db.execute("select id from emails where subject like 'Convite%'").fetchone()[0]
        page = c.get(f"/inbox/{eid}").get_data(as_text=True)
        check("&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)" not in page, "corpo do e-mail é exibido escapado (sem XSS)")
        check("Gama SA" in page and "Atualizar status da vaga" in page, "detalhe mostra a vaga e permite mudar status manualmente")
        job1 = db.execute("select id from jobs where company='Gama SA'").fetchone()[0]
        job_page = c.get(f"/jobs/{job1}").get_data(as_text=True)
        check("E-mails da empresa" in job_page and "Convite para entrevista" in job_page, "página da vaga lista e-mails da empresa")
        check('class="nbadge">2<' in c.get("/inbox").get_data(as_text=True), "ler uma mensagem reduz o contador de não lidas")

        r = c.post(f"/inbox/{eid}/link", data={"_csrf": t, "job_id": ""}, follow_redirects=True)
        check("Sem vaga vinculada" in r.get_data(as_text=True), "desvincular manualmente")
        check("Convite para entrevista" in c.get("/inbox?f=nolink").get_data(as_text=True), "filtro 'sem vaga vinculada'")
        c.post(f"/inbox/{eid}/link", data={"_csrf": t, "job_id": str(job1)})

        c.post(f"/inbox/{eid}/delete", data={"_csrf": t})
        check("Convite para entrevista" not in c.get("/inbox").get_data(as_text=True), "remover esconde a mensagem")
        r = c.post("/inbox/sync", data={"_csrf": t}, follow_redirects=True)
        check("0 nova(s)" in r.get_data(as_text=True) and "Convite para entrevista" not in r.get_data(as_text=True), "mensagem removida não reaparece na sincronização")
        row = db.execute("select body, subject from emails where id=?", (eid,)).fetchone()
        check(row == ("", ""), "conteúdo da mensagem removida é apagado do banco")

        # isolamento
        c2 = app.test_client()
        t2 = re.search(r'name="_csrf" value="([^"]+)"', c2.get("/register").get_data(as_text=True)).group(1)
        c2.post("/register", data={"_csrf": t2, "name": "Outro", "email": "o@x.com", "password": "senha1234", "password2": "senha1234"})
        other = db.execute("select id from emails where dismissed=0 limit 1").fetchone()[0]
        check(c2.get(f"/inbox/{other}").status_code == 404, "outro usuário não lê e-mails alheios")

        r = c.post("/inbox/disconnect", data={"_csrf": t, "delete_emails": "1"}, follow_redirects=True)
        check("Conta desconectada" in r.get_data(as_text=True), "desconectar remove conta")
        check(db.execute("select count(*) from email_accounts").fetchone()[0] == 0 and db.execute("select count(*) from emails").fetchone()[0] == 0, "senha e mensagens apagadas")
    print("\nTodos os testes de e-mail passaram.")


if __name__ == "__main__":
    main()
