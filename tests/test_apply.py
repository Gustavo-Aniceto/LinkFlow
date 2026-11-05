"""Ranking, pacote, envio por e-mail (SMTP simulado), cadastro restrito e login. Execute: python tests/test_apply.py"""
import email
import imaplib
import re
import sys
import tempfile
from email import policy
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from linkflow import create_app  # noqa: E402
from test_mail import FakeIMAP  # noqa: E402


class FakeSMTP:
    sent = []
    login_ok = True
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        pass

    def login(self, u, p):
        import smtplib
        if not FakeSMTP.login_ok:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        self.user = u

    def send_message(self, msg):
        FakeSMTP.sent.append((self.host, self.port, self.user, msg))
        return {}

    def quit(self):
        pass


def check(cond, text):
    print(("✔ " if cond else "✘ ") + text)
    cond or sys.exit(1)


def main():
    tmp = Path(tempfile.mkdtemp())
    app = create_app({"DATA_DIR": str(tmp), "TESTING": True, "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "",
                      "ALLOWED_EMAILS": ["maria@x.com"], "MAX_SENDS_PER_DAY": 3})
    c = app.test_client()
    tok = lambda p, cl=None: re.search(r'name="_csrf" value="([^"]+)"', (cl or c).get(p).get_data(as_text=True)).group(1)  # noqa: E731

    # --- cadastro restrito e segurança ---
    r = c.post("/register", data={"_csrf": tok("/register"), "name": "Intruso", "email": "intruso@x.com", "password": "senha1234", "password2": "senha1234"}, follow_redirects=True)
    check("restrito" in r.get_data(as_text=True), "cadastro de e-mail fora da lista é bloqueado")
    r = c.post("/register", data={"_csrf": tok("/register"), "name": "Maria", "email": "MARIA@x.com", "password": "senha1234", "password2": "senha1234"})
    check(r.status_code == 302, "e-mail autorizado (sem diferenciar maiúsculas) consegue criar conta")
    h = c.get("/dashboard").headers
    check(h.get("X-Frame-Options") == "DENY" and h.get("X-Content-Type-Options") == "nosniff" and "no-store" in h.get("Cache-Control", ""), "cabeçalhos de segurança presentes")

    # perfil
    t = tok("/profile")
    c.post("/profile", data={"_csrf": t, "full_name": "Maria Souza", "email": "maria@x.com", "headline": "Analista de Dados", "summary": "Analista.",
                             "work_modes": ["remoto"], "skills": "Python, SQL, Power BI",
                             "exp_title": ["Analista"], "exp_company": ["Alfa"], "exp_start": ["2020"], "exp_end": ["Atual"], "exp_desc": ["Dashboards em Power BI e SQL"]})

    # vagas com compatibilidades diferentes
    jobs = {}
    for title, company, desc in [("BI Jr", "Gama SA", "Power BI, SQL e Python. Envie o currículo para rh@gama.com.br ou noreply@gama.com.br"),
                                 ("Engenheiro de Dados", "Zeta", "Airflow, Kafka, Spark, AWS e Java"),
                                 ("Analista de Dados", "Delta", "Python e SQL"),
                                 ("Sem descrição", "Omega", "")]:
        r = c.post("/jobs/new", data={"_csrf": t, "title": title, "company": company, "description": desc, "url": "https://example.com/v"})
        jobs[company] = int(r.headers["Location"].rsplit("/", 1)[1])

    # --- ranking ---
    page = c.get("/ranking").get_data(as_text=True)
    check("Analisar 3 vaga(s) sem análise" in page and "SIMULADOS" in page, "ranking oferece analisar vagas sem análise e avisa que é simulado")
    r = c.post("/ranking/analyze-all", data={"_csrf": t}, follow_redirects=True)
    page = r.get_data(as_text=True)
    check("3 vaga(s) analisada(s)" in page, "analisar todas processa as vagas com descrição (ignora a sem descrição)")
    order = [m.group(1) for m in re.finditer(r'<h3><a href="/jobs/\d+">([^<]+)</a></h3>', page)]
    check(order[0] in ("BI Jr", "Analista de Dados") and order.index("Engenheiro de Dados") > order.index("BI Jr") and order[-1] == "Sem descrição",
          f"ranking ordenado por compatibilidade (melhores primeiro, sem análise por último): {order}")
    check("analyze-all" not in page, "sem vagas pendentes o botão de analisar some")

    # --- pacote ---
    gama = jobs["Gama SA"]
    r = c.post(f"/jobs/{gama}/package", data={"_csrf": t}, follow_redirects=True)
    page = r.get_data(as_text=True)
    check("Pacote montado" in page and "Enviar por e-mail" in page, "pacote gera currículo + carta e abre a página de candidatura")
    check("Para enviar daqui, conecte seu e-mail" in page, "sem e-mail conectado: orienta a conectar")
    check("Pronta para candidatura" in c.get(f"/jobs/{gama}").get_data(as_text=True), "vaga marcada como pronta")
    r = c.post(f"/jobs/{jobs['Omega']}/package", data={"_csrf": t}, follow_redirects=True)
    check("não tem descrição" in r.get_data(as_text=True), "pacote de vaga sem descrição é recusado")

    # --- envio: sem conta ---
    r = c.post(f"/jobs/{gama}/apply/send", data={"_csrf": t, "to": "rh@gama.com.br", "subject": "x", "body": "y", "confirm": "1"}, follow_redirects=True)
    check("Conecte seu e-mail" in r.get_data(as_text=True), "envio sem conta conectada é bloqueado")

    with mock.patch("imaplib.IMAP4_SSL", FakeIMAP), mock.patch("smtplib.SMTP_SSL", FakeSMTP), mock.patch("smtplib.SMTP", FakeSMTP):
        c.post("/inbox/connect", data={"_csrf": t, "preset": "gmail", "host": "imap.gmail.com", "username": "maria@x.com", "password": "goodpass", "port": "993", "folder": "INBOX", "smtp_host": "smtp.gmail.com", "smtp_port": "465"})
        page = c.get(f"/jobs/{gama}/apply").get_data(as_text=True)
        check('value="rh@gama.com.br"' in page, "e-mail do recrutador pré-preenchido")
        check("noreply" not in page, "endereços noreply não são sugeridos")
        m = re.search(r'<textarea name="body"[^>]*>(.*?)</textarea>', page, re.S)
        check(m and "Prezados" in m.group(1) and "# Maria" not in m.group(1), "corpo sugerido vem da carta, sem cabeçalho de marcação")

        base = {"_csrf": t, "to": "rh@gama.com.br", "subject": "Candidatura BI Jr", "body": "Olá, segue meu currículo.", "attach_resume": "1", "attach_cover": "1", "confirm": "1"}
        r = c.post(f"/jobs/{gama}/apply/send", data={**base, "confirm": ""}, follow_redirects=True)
        check("confirmando que revisou" in r.get_data(as_text=True) and not FakeSMTP.sent, "sem confirmação nada é enviado")
        for bad_to in ("a@b.com, c@d.com", "x@y.com\nBcc: z@w.com", "semarroba"):
            r = c.post(f"/jobs/{gama}/apply/send", data={**base, "to": bad_to}, follow_redirects=True)
            check(not FakeSMTP.sent, f"destinatário inválido/múltiplo/injeção recusado: {bad_to!r}".replace("\n", "\\n"))
        r = c.post(f"/jobs/{gama}/apply/send", data={**base, "subject": "Oi\nBcc: evil@x.com"}, follow_redirects=True)
        check(not FakeSMTP.sent and "Quebras de linha" in r.get_data(as_text=True), "injeção de cabeçalho no assunto é recusada")

        FakeSMTP.login_ok = False
        r = c.post(f"/jobs/{gama}/apply/send", data=base, follow_redirects=True)
        check("recusou o login" in r.get_data(as_text=True) and "Candidatura realizada" not in c.get(f"/jobs/{gama}").get_data(as_text=True).split("Status")[1][:300], "falha de login SMTP: mostra erro e NÃO marca como enviada")
        FakeSMTP.login_ok = True

        r = c.post(f"/jobs/{gama}/apply/send", data=base, follow_redirects=True)
        page = r.get_data(as_text=True)
        check(len(FakeSMTP.sent) == 1 and "Candidatura enviada para rh@gama.com.br" in page, "candidatura enviada")
        host, port, user, msg = FakeSMTP.sent[0]
        parsed = email.message_from_bytes(msg.as_bytes(), policy=policy.default)
        atts = [p for p in parsed.iter_attachments()]
        check((host, port, user) == ("smtp.gmail.com", 465, "maria@x.com"), "enviado pelo SMTP da conta conectada (SSL 465)")
        check(parsed["To"] == "rh@gama.com.br" and "Maria Souza" in parsed["From"] and parsed["Subject"] == "Candidatura BI Jr" and parsed["Bcc"] is None, "cabeçalhos corretos, sem Bcc")
        check(len(atts) == 2 and all(a.get_content()[:5] == b"%PDF-" for a in atts), "dois anexos PDF válidos (currículo e carta)")
        check({a.get_filename() for a in atts} == {"Curriculo.pdf", "Carta_de_apresentacao.pdf"}, "nomes dos anexos")
        check("Candidatura realizada" in c.get(f"/jobs/{gama}").get_data(as_text=True), "status da vaga virou 'Candidatura realizada'")
        check("rh@gama.com.br" in c.get(f"/jobs/{gama}/apply").get_data(as_text=True), "histórico de envios registrado")
        check("Candidatura enviada por e-mail" in c.get("/dashboard").get_data(as_text=True), "atividade registrada no dashboard")

        r = c.post(f"/jobs/{gama}/apply/send", data=base, follow_redirects=True)
        check(len(FakeSMTP.sent) == 1 and "nas últimas 24 horas" in r.get_data(as_text=True), "reenvio duplicado para o mesmo e-mail é bloqueado")

        # limite diário (3)
        for i, company in enumerate(("Zeta", "Delta")):
            c.post(f"/jobs/{jobs[company]}/package", data={"_csrf": t})
            r = c.post(f"/jobs/{jobs[company]}/apply/send", data={**base, "to": f"rh{i}@x.com"}, follow_redirects=True)
        check(len(FakeSMTP.sent) == 3, "envios dentro do limite funcionam")
        c.post(f"/jobs/{jobs['Omega']}/apply/send", data={**base, "to": "z@z.com"})
        c.post(f"/jobs/{jobs['Zeta']}/apply/send", data={**base, "to": "novo@x.com"})
        r = c.post(f"/jobs/{jobs['Zeta']}/apply/send", data={**base, "to": "novo2@x.com"}, follow_redirects=True)
        check(len(FakeSMTP.sent) == 3 and "Limite de 3 envios" in r.get_data(as_text=True), "limite diário de envios bloqueia disparo em massa")

    # --- isolamento ---
    app.config["ALLOWED_EMAILS"] = []
    c2 = app.test_client()
    c2.post("/register", data={"_csrf": tok("/register", c2), "name": "Outro", "email": "o@x.com", "password": "senha1234", "password2": "senha1234"})
    check(c2.get(f"/jobs/{gama}/apply").status_code == 404, "outro usuário não acessa candidatura alheia")
    r = c2.post(f"/jobs/{gama}/apply/send", data={"_csrf": tok("/profile", c2), "to": "a@b.com", "subject": "x", "body": "y", "confirm": "1"})
    check(r.status_code == 404, "outro usuário não consegue enviar em nome de outro")

    # --- throttle de login ---
    c3 = app.test_client()
    for i in range(8):
        c3.post("/login", data={"_csrf": tok("/login", c3), "email": "maria@x.com", "password": "errada"})
    r = c3.post("/login", data={"_csrf": tok("/login", c3), "email": "maria@x.com", "password": "senha1234"})
    check(r.status_code == 429, "após 8 falhas o login é bloqueado temporariamente (mesmo com a senha certa)")
    print("\nTodos os testes de candidatura passaram.")


if __name__ == "__main__":
    main()
