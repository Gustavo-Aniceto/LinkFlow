"""Currículo por link: leitura da vaga (simulada), SSRF, fallback de texto colado. Execute: python tests/test_quick.py"""
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from linkflow import create_app  # noqa: E402
from linkflow.services import jobfetch  # noqa: E402

JOB_HTML = """<html><head><title>Vaga | Acme</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Analista de Dados Pleno",
"hiringOrganization":{"@type":"Organization","name":"Acme Tecnologia"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"São Paulo","addressRegion":"SP"}},
"description":"<p>Buscamos pessoa analista de dados.</p><ul><li>Python e SQL avancado</li><li>Power BI</li><li>Comunicacao com areas de negocio</li></ul><p>Voce vai construir dashboards, automatizar relatorios e apoiar decisoes da diretoria com analises claras e confiaveis.</p><p>Contato: rh@acme.com</p>"}
</script></head><body><nav>menu</nav><h1>Analista de Dados Pleno</h1></body></html>"""

PLAIN_HTML = "<html><head><title>Dev Backend - Foo</title></head><body><h1>Dev Backend</h1><p>" + ("Trabalho com Python e APIs REST em equipe. " * 12) + "</p></body></html>"


def main():
    # parser
    j = jobfetch.parse_job_page(JOB_HTML, "https://acme.com/vaga/1")
    assert j["title"] == "Analista de Dados Pleno" and j["company"] == "Acme Tecnologia", j
    assert j["location"] == "São Paulo, SP" and "Python e SQL" in j["description"] and "menu" not in j["description"], j
    p = jobfetch.parse_job_page(PLAIN_HTML, "https://www.foo.com/x")
    assert p["title"] == "Dev Backend" and p["company"] == "foo.com" and "APIs REST" in p["description"], p

    # SSRF / bloqueios
    for bad in ("http://127.0.0.1/x", "http://localhost/x", "http://169.254.169.254/latest", "http://10.0.0.5/",
                "ftp://x.com/a", "https://www.linkedin.com/jobs/view/123"):
        try:
            jobfetch.fetch_html(bad)
            raise AssertionError("deveria bloquear " + bad)
        except jobfetch.FetchError:
            pass

    tmp = Path(tempfile.mkdtemp())
    app = create_app({"DATA_DIR": str(tmp), "TESTING": True, "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "ALLOWED_EMAILS": []})
    c = app.test_client()
    token = lambda p="/login": re.search(r'name="_csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa: E731

    c.post("/register", data={"_csrf": token("/register"), "name": "Ana", "email": "ana@example.com", "password": "senha1234", "password2": "senha1234"})
    t = token("/quick")
    # sem perfil -> manda cadastrar
    r = c.post("/quick", data={"_csrf": t, "url": "https://acme.com/vaga/1"})
    assert r.status_code == 302 and "/profile" in r.headers["Location"], r.headers
    t = token("/profile")
    c.post("/profile", data={"_csrf": t, "full_name": "Ana Souza", "headline": "Analista de dados", "email": "ana@example.com",
                              "summary": "Analista com experiência em Python, SQL e Power BI.", "skills": "Python, SQL, Power BI"})
    t = token("/quick")
    with mock.patch.object(jobfetch, "fetch_html", return_value=(JOB_HTML, "https://acme.com/vaga/1")):
        r = c.post("/quick", data={"_csrf": t, "url": "https://acme.com/vaga/1"}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Analista de Dados Pleno" in body and "Acme Tecnologia" in body, body[:500]
    assert "Currículo" in body
    docs = c.get("/documents").get_data(as_text=True)
    assert "Currículo — Analista de Dados Pleno" in docs and "Carta de apresentação" in docs

    # falha de leitura -> pede texto colado
    t = token("/quick")
    with mock.patch.object(jobfetch, "fetch_html", side_effect=jobfetch.FetchError("O site bloqueou a leitura automática.")):
        r = c.post("/quick", data={"_csrf": t, "url": "https://x.com/v"})
    assert r.status_code == 200 and "Cole a descrição" in r.get_data(as_text=True) or "Descrição da vaga" in r.get_data(as_text=True)

    # texto colado (LinkedIn)
    t = token("/quick")
    txt = "Engenheiro de Dados\n" + "Procuramos pessoa com Python, SQL e experiencia em pipelines de dados para atuar na area. " * 3
    r = c.post("/quick", data={"_csrf": t, "url": "https://www.linkedin.com/jobs/view/1", "text": txt, "company": "Beta"}, follow_redirects=True)
    assert r.status_code == 200 and "Beta" in r.get_data(as_text=True)

    # sem csrf
    assert c.post("/quick", data={"url": "https://a.com"}).status_code in (400, 403)
    print("Todos os testes de currículo por link passaram.")


if __name__ == "__main__":
    main()
