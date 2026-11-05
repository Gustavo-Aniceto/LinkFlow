"""Teste ponta a ponta (sem rede, modo demonstrativo). Execute: python tests/test_e2e.py"""
import io
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ[k] = ""

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from linkflow import create_app  # noqa: E402

RESUME_LINES = """Maria Souza Lima
Analista de Dados
maria.lima@example.com | (11) 98765-4321 | São Paulo, SP
linkedin.com/in/marialima

Resumo Profissional
Analista de dados com foco em automação de relatórios e dashboards.

Experiência Profissional
Analista de Dados Pleno
Empresa Alfa Ltda
jan/2021 - Atual
- Criação de dashboards em Power BI para a diretoria
- Consultas em SQL e automação de rotinas com Python

Assistente de BI | Beta Comércio
03/2018 - 12/2020
- Elaboração de relatórios em Excel

Formação Acadêmica
Universidade de São Paulo
Bacharelado em Estatística 2014 - 2017

Cursos e Certificações
Curso de Power BI Avançado - Udemy - 2020
Python para Análise de Dados - Alura - 2019

Habilidades
Python, SQL, Power BI, Excel, Comunicação
""".splitlines()


def make_pdf(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for line in RESUME_LINES:
        c.drawString(50, y, line)
        y -= 16
    c.save()


def csrf_from(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def main():
    tmp = Path(tempfile.mkdtemp())
    app = create_app({"DATA_DIR": str(tmp), "DATABASE": str(tmp / "t.db"), "TESTING": True,
                      "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""})
    cl = app.test_client()
    ok = lambda cond, msg: (print(("✔ " if cond else "✘ ") + msg), cond or sys.exit(1))  # noqa: E731

    def csrf(path="/login"):
        html = cl.get(path).get_data(as_text=True)
        return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)

    # --- auth ---
    ok(cl.get("/dashboard").status_code == 302, "dashboard exige login")
    r = cl.post("/register", data={"name": "Maria", "email": "m@x.com", "password": "senha1234", "password2": "senha1234"})
    ok(r.status_code == 400, "POST sem CSRF é rejeitado")
    t = csrf("/register")
    r = cl.post("/register", data={"_csrf": t, "name": "Maria", "email": "m@x.com", "password": "senha1234", "password2": "senha1234"})
    ok(r.status_code == 302, "cadastro cria conta e redireciona")
    t = csrf("/profile")
    r = cl.get("/dashboard")
    ok(r.status_code == 200 and "Vagas cadastradas" in r.get_data(as_text=True), "dashboard carrega")

    # --- importação de PDF ---
    pdf = tmp / "cv.pdf"
    make_pdf(pdf)
    r = cl.post("/profile/import", data={"_csrf": t, "resume": (open(pdf, "rb"), "cv.pdf")},
                content_type="multipart/form-data")
    ok(r.status_code == 302 and "import=" in r.headers["Location"], "upload de PDF extrai e redireciona")
    page = cl.get(r.headers["Location"]).get_data(as_text=True)
    for needle in ("Maria Souza Lima", "maria.lima@example.com", "Empresa Alfa", "Power BI", "Universidade de São Paulo"):
        ok(needle in page, f"campo extraído aparece no formulário: {needle}")
    from linkflow.services.resume_parser import parse_resume_text
    parsed = parse_resume_text("\n".join(RESUME_LINES))
    ok(parsed["profile"]["location"] == "São Paulo, SP", "parser: localização")
    ok([(e["title"], e["company"], e["start"], e["end"]) for e in parsed["experiences"]] == [
        ("Analista de Dados Pleno", "Empresa Alfa Ltda", "jan/2021", "Atual"),
        ("Assistente de BI", "Beta Comércio", "03/2018", "12/2020")], "parser: experiências (cargo/empresa/datas)")
    ok(parsed["skills"] == ["Python", "SQL", "Power BI", "Excel", "Comunicação"], "parser: habilidades")
    ok(len(parsed["courses"]) == 2 and parsed["courses"][0]["issuer"] == "Udemy", "parser: cursos")
    r = cl.post("/profile/import", data={"_csrf": t, "resume": (io.BytesIO(b"nao e pdf"), "x.pdf")},
                content_type="multipart/form-data", follow_redirects=True)
    ok("não parece ser um PDF" in r.get_data(as_text=True), "arquivo falso .pdf é recusado")
    r = cl.post("/profile/import", data={"_csrf": t, "resume": (io.BytesIO(b"x"), "x.txt")},
                content_type="multipart/form-data", follow_redirects=True)
    ok("Apenas arquivos PDF" in r.get_data(as_text=True), "extensão não-PDF é recusada")

    # --- salvar perfil (editado) ---
    form = {"_csrf": t, "full_name": "Maria Souza Lima", "headline": "Analista de Dados", "email": "maria.lima@example.com",
            "phone": "(11) 98765-4321", "location": "São Paulo, SP", "linkedin_url": "https://linkedin.com/in/marialima",
            "summary": "Analista de dados.", "salary_expectation": "R$ 9.000", "work_modes": ["remoto", "hibrido"],
            "exp_title": ["Analista de Dados Pleno"], "exp_company": ["Empresa Alfa"], "exp_start": ["01/2021"],
            "exp_end": ["Atual"], "exp_desc": ["Dashboards em Power BI\nConsultas SQL e Python"],
            "edu_degree": ["Bacharelado em Estatística"], "edu_institution": ["USP"], "edu_start": ["2014"], "edu_end": ["2017"],
            "course_name": ["Power BI Avançado"], "course_issuer": ["Udemy"], "course_year": ["2020"],
            "skills": "Python, SQL, Power BI, Excel"}
    r = cl.post("/profile", data=form)
    ok(r.status_code == 302, "perfil salvo")
    page = cl.get("/profile").get_data(as_text=True)
    ok("Empresa Alfa" in page and "Power BI Avançado" in page and "R$ 9.000" in page, "perfil persiste no banco")
    r = cl.post("/profile", data={**form, "linkedin_url": "javascript:alert(1)"})
    ok(r.status_code == 200 and "http://" in r.get_data(as_text=True), "URL do LinkedIn inválida é rejeitada")

    # --- vagas ---
    job = {"_csrf": t, "title": "Analista de BI", "company": "Gama SA", "location": "Remoto", "salary": "R$ 8.500",
           "work_mode": "remoto", "url": "https://www.linkedin.com/jobs/view/123",
           "description": "Requisitos:\n- Experiência com Power BI\n- SQL avançado\n- Python\n- Conhecimento em Tableau\n- Inglês fluente"}
    r = cl.post("/jobs/new", data=job)
    ok(r.status_code == 302, "vaga cadastrada")
    jid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    r = cl.post("/jobs/new", data={**job, "url": "ftp://x"})
    ok(r.status_code == 200 and "http://" in r.get_data(as_text=True), "URL de vaga inválida rejeitada")
    page = cl.get("/jobs").get_data(as_text=True)
    ok("Analista de BI" in page and 'target="_blank"' in page and "noopener" in page, "lista de vagas com botão de vaga original")

    # --- análise (simulada) ---
    r = cl.post(f"/jobs/{jid}/analyze", data={"_csrf": t}, follow_redirects=True)
    page = r.get_data(as_text=True)
    ok("SIMULADO" in page, "análise marcada como SIMULADA")
    ok("Requisitos atendidos" in page and "Requisitos não encontrados" in page, "análise mostra atendidos/não encontrados")
    m = re.search(r'--p: (\d+)', page)
    ok(m and 0 < int(m.group(1)) < 100, f"score calculado ({m.group(1) if m else '?'}%)")
    ok("tableau" in page.lower() and "Analisada" in page, "requisito ausente listado e status virou Analisada")
    ok(re.search(r"<li><b>Tableau</b>|<li>Tableau</li>|<li>tableau</li>", page, re.I) is not None, "Tableau (não possuído) está em 'não encontrados'")

    # --- documentos ---
    r = cl.post(f"/jobs/{jid}/generate/resume", data={"_csrf": t})
    page = r.get_data(as_text=True)
    ok(r.status_code == 200 and "Rascunho não salvo" in page and "SIMULADO" in page, "currículo gerado como rascunho simulado")
    ok("Maria Souza Lima" in page and "Empresa Alfa" in page, "currículo usa dados do perfil")
    content = re.search(r'<textarea name="content"[^>]*>(.*?)</textarea>', page, re.S).group(1)
    ok("Tableau" not in content, "currículo NÃO inventa qualificações ausentes")
    r = cl.post(f"/jobs/{jid}/generate/cover", data={"_csrf": t})
    ok("Prezados" in r.get_data(as_text=True), "carta de apresentação gerada")
    r = cl.post("/documents/save", data={"_csrf": t, "kind": "resume", "job_id": jid, "title": "CV Gama",
                                          "content": content + "\n- linha editada", "simulated": "1", "mark_ready": "1"})
    ok(r.status_code == 302, "documento salvo após edição")
    did = int(r.headers["Location"].rstrip("/").split("/")[-1])
    ok("linha editada" in cl.get(f"/documents/{did}").get_data(as_text=True), "edição persistiu")
    r = cl.post("/documents/pdf", data={"_csrf": t, "title": "CV Gama", "content": content + "\n- linha editada"})
    ok(r.status_code == 200 and r.data[:5] == b"%PDF-" and "attachment" in r.headers["Content-Disposition"], "exportação em PDF funciona")
    ok(cl.get(f"/jobs/{jid}").get_data(as_text=True).count("Pronta para candidatura") >= 1, "vaga marcada como pronta ao salvar")

    # --- status / quadro ---
    for status in ("applied", "interview", "approved", "rejected", "found"):
        r = cl.post(f"/jobs/{jid}/status", data={"_csrf": t, "status": status}, headers={"X-Requested-With": "fetch"})
        ok(r.status_code == 200 and r.get_json()["status"] == status, f"status → {status}")
    r = cl.post(f"/jobs/{jid}/status", data={"_csrf": t, "status": "xyz"}, headers={"X-Requested-With": "fetch"})
    ok(r.status_code == 400, "status inválido rejeitado")
    cl.post(f"/jobs/{jid}/status", data={"_csrf": t, "status": "applied"}, headers={"X-Requested-With": "fetch"})
    ok("Analista de BI" in cl.get("/applications").get_data(as_text=True), "central de candidaturas lista a vaga")

    # --- dashboard ---
    page = cl.get("/dashboard").get_data(as_text=True)
    ok("Atividades recentes" in page and "Análise de compatibilidade" in page, "atividades recentes registradas")
    ok(re.search(r"Vagas cadastradas</span><span class=\"stat-value\">1<", page) is not None, "total de vagas = 1")
    ok(re.search(r"Candidaturas pendentes</span><span class=\"stat-value\">1<", page) is not None, "pendentes = 1")

    # --- isolamento entre usuários ---
    cl2 = app.test_client()
    t2 = re.search(r'name="_csrf" value="([^"]+)"', cl2.get("/register").get_data(as_text=True)).group(1)
    cl2.post("/register", data={"_csrf": t2, "name": "Outro", "email": "o@x.com", "password": "senha1234", "password2": "senha1234"})
    ok(cl2.get(f"/jobs/{jid}").status_code == 404, "outro usuário não acessa a vaga")
    ok(cl2.get(f"/documents/{did}").status_code == 404, "outro usuário não acessa o documento")

    # --- exclusão e persistência ---
    app2 = create_app({"DATA_DIR": str(tmp), "DATABASE": str(tmp / "t.db"), "TESTING": True,
                       "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""})
    c3 = app2.test_client()
    t3 = re.search(r'name="_csrf" value="([^"]+)"', c3.get("/login").get_data(as_text=True)).group(1)
    r = c3.post("/login", data={"_csrf": t3, "email": "m@x.com", "password": "senha1234"})
    ok(r.status_code == 302, "login após 'reiniciar' a aplicação")
    t3 = csrf_from(c3, "/jobs/new")  # o login troca a sessão, logo o token CSRF também muda
    ok("Analista de BI" in c3.get("/jobs").get_data(as_text=True), "dados persistem após reiniciar")
    r = c3.post(f"/jobs/{jid}/delete", data={"_csrf": t3}, follow_redirects=True)
    ok("Vaga removida" in r.get_data(as_text=True), "exclusão de vaga")
    ok(cl.get("/rota-inexistente").status_code == 404, "404 tratado")
    print("\nTodos os testes passaram.")


if __name__ == "__main__":
    main()
