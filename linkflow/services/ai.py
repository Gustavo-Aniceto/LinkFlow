"""Camada de IA do LinkFlow.

Provedores (escolhidos pelas chaves no .env):
  1. Anthropic  (ANTHROPIC_API_KEY)
  2. OpenAI     (OPENAI_API_KEY)
  3. DEMO       (sem chave) → comparação por palavras-chave, SIMULADA e rotulada como tal.

Regra central: nunca inventar experiências ou qualificações. No modo demo, os textos
usam exclusivamente dados que o usuário cadastrou no perfil.
"""
import json
import re
import unicodedata

import requests
from flask import current_app

DEMO_NOTE = ("Resultado SIMULADO (modo demonstrativo): comparação por palavras-chave entre o seu perfil e a "
             "descrição da vaga, sem inteligência artificial. Configure uma chave de API no .env para análise real.")

SYSTEM_RULES = (
    "Você é um consultor de carreira rigoroso e honesto. Use SOMENTE informações presentes no perfil do "
    "candidato. NUNCA invente experiências, cargos, empresas, datas, ferramentas, certificações, métricas ou "
    "resultados. Se algo exigido pela vaga não está no perfil, diga que não foi encontrado. Responda em "
    "português do Brasil."
)


class AIError(Exception):
    pass


# ------------------------------------------------------------------ provedor
def provider() -> str:
    cfg = current_app.config
    if cfg["ANTHROPIC_API_KEY"]:
        return "anthropic"
    if cfg["OPENAI_API_KEY"]:
        return "openai"
    return "demo"


def provider_label() -> str:
    cfg = current_app.config
    return {"anthropic": f"Anthropic ({cfg['ANTHROPIC_MODEL']})", "openai": f"OpenAI-compatível ({cfg['OPENAI_MODEL']})" if "api.openai.com" not in cfg["OPENAI_BASE_URL"] else f"OpenAI ({cfg['OPENAI_MODEL']})",
            "demo": "Modo demonstrativo (simulado)"}[provider()]


def _call_llm(system: str, user: str, json_mode: bool) -> str:
    cfg, prov = current_app.config, provider()
    timeout = cfg["AI_TIMEOUT"]
    try:
        if prov == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages", timeout=timeout,
                headers={"x-api-key": cfg["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": cfg["ANTHROPIC_MODEL"], "max_tokens": 4096, "system": system,
                      "messages": [{"role": "user", "content": user}]})
            if r.status_code != 200:
                raise AIError(f"Anthropic retornou {r.status_code}: {r.text[:300]}")
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        if prov == "openai":
            body = {"model": cfg["OPENAI_MODEL"], "messages": [{"role": "system", "content": system},
                                                              {"role": "user", "content": user}]}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            r = requests.post(cfg["OPENAI_BASE_URL"] + "/chat/completions", timeout=timeout,
                              headers={"Authorization": f"Bearer {cfg['OPENAI_API_KEY']}"}, json=body)
            if r.status_code != 200:
                raise AIError(f"OpenAI retornou {r.status_code}: {r.text[:300]}")
            return r.json()["choices"][0]["message"]["content"]
    except requests.RequestException as exc:
        raise AIError(f"Falha de conexão com a API de IA: {exc}") from exc
    raise AIError("Nenhum provedor de IA configurado.")


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AIError("A IA não retornou um JSON válido.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AIError(f"JSON inválido retornado pela IA: {exc}") from exc


# ------------------------------------------------------------------ utilidades demo
VOCAB_TERMS = sorted({
    "python", "java", "javascript", "typescript", "c#", "c++", "golang", "rust", "php", "ruby", "kotlin", "swift",
    "scala", "sql", "nosql", "html", "css", "react", "angular", "vue", "svelte", "next.js", "node.js", "express",
    "django", "flask", "fastapi", "spring", ".net", "laravel", "rails", "android", "ios", "flutter", "react native",
    "docker", "kubernetes", "terraform", "ansible", "jenkins", "git", "github", "gitlab", "ci/cd", "devops", "aws",
    "azure", "gcp", "linux", "bash", "postgresql", "mysql", "sqlite", "mongodb", "redis", "elasticsearch", "kafka",
    "rabbitmq", "graphql", "rest", "api", "microservices", "microsserviços", "tdd", "agile", "scrum", "kanban",
    "jira", "figma", "ux", "ui", "power bi", "tableau", "excel", "pandas", "numpy", "spark", "hadoop", "airflow",
    "etl", "dbt", "machine learning", "deep learning", "nlp", "tensorflow", "pytorch", "ciência de dados",
    "análise de dados", "business intelligence", "estatística", "sap", "salesforce", "crm", "erp", "seo",
    "google analytics", "marketing digital", "copywriting", "vendas", "negociação", "atendimento ao cliente",
    "liderança", "gestão de projetos", "gestão de equipes", "planejamento estratégico", "comunicação",
    "trabalho em equipe", "inglês", "espanhol", "contabilidade", "finanças", "controladoria", "recursos humanos",
    "recrutamento", "treinamento", "logística", "supply chain", "compras", "qualidade", "lean", "six sigma", "pmp",
    "itil", "lgpd", "segurança da informação", "redes", "suporte técnico", "help desk", "photoshop", "illustrator",
    "premiere", "after effects", "autocad", "solidworks", "word", "powerpoint", "office", "english", "spanish",
    "leadership", "project management", "data analysis", "sales", "customer service", "communication", "teamwork",
})
SYNONYMS = {"node.js": ["nodejs", "node"], "next.js": ["nextjs"], "postgresql": ["postgres"], "golang": ["go lang"],
            ".net": ["dotnet", "asp.net"], "inglês": ["english"], "english": ["inglês"], "spanish": ["espanhol"],
            "espanhol": ["spanish"], "machine learning": ["aprendizado de máquina"]}
STOP = set("""para com uma que dos das nos nas por mais como ter ser são sua seu seus suas sobre entre também ao aos
experiência conhecimento conhecimentos desejável diferencial necessário necessária requisitos vaga ou the and for with
you will have our are your from that this experience knowledge skills years anos ano em de da do e a o as os um""".split())


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _has_term(folded_text: str, term: str) -> bool:
    for t in [term, *SYNONYMS.get(term, [])]:
        pat = r"(?<![\w+#.])" + re.escape(_fold(t)) + r"(?![\w+#])"
        if re.search(pat, folded_text):
            return True
    return False


def _profile_sources(b: dict) -> list[tuple[str, str]]:
    """Pares (rótulo, texto) para localizar evidências."""
    p = b["profile"]
    src = [("Resumo/título", f"{p['headline']} {p['summary']}"), ("Habilidades", " , ".join(b["skills"]))]
    for e in b["experiences"]:
        src.append((f"Experiência: {e['title']} @ {e['company']}".strip(), f"{e['title']} {e['company']} {e['description']}"))
    for e in b["education"]:
        src.append((f"Formação: {e['degree']}".strip(), f"{e['degree']} {e['institution']}"))
    for c in b["courses"]:
        src.append((f"Curso: {c['name']}", f"{c['name']} {c['issuer']}"))
    return [(label, _fold(text)) for label, text in src]


def _find_evidence(sources, term) -> str | None:
    for label, text in sources:
        if _has_term(text, term):
            return label
    return None


def _extract_requirements(job_text: str) -> list[str]:
    folded = _fold(job_text)
    found = [t for t in VOCAB_TERMS if _has_term(folded, t)]
    # remove termos contidos em outros mais longos (ex.: "java" dentro de "javascript" já é tratado pela borda)
    if len(found) < 3:
        for line in job_text.splitlines():
            if re.match(r"^\s*[-•*●▪·]\s+\S", line):
                req = re.sub(r"^\s*[-•*●▪·]\s+", "", line).strip().rstrip(";.")
                if 3 < len(req) <= 140 and req not in found:
                    found.append(req)
    return found[:25]


def _matches_free_text(sources, req: str) -> str | None:
    words = {w for w in re.findall(r"\w{4,}", _fold(req)) if w not in STOP}
    if not words:
        return None
    for label, text in sources:
        have = {w for w in words if w in text}
        if len(have) / len(words) >= 0.6:
            return label
    return None


_SPECIAL = {"power bi": "Power BI", "c#": "C#", "c++": "C++", ".net": ".NET", "node.js": "Node.js",
            "next.js": "Next.js", "ci/cd": "CI/CD", "nosql": "NoSQL", "graphql": "GraphQL", "mysql": "MySQL",
            "postgresql": "PostgreSQL", "mongodb": "MongoDB", "tensorflow": "TensorFlow", "pytorch": "PyTorch",
            "javascript": "JavaScript", "typescript": "TypeScript", "github": "GitHub", "gitlab": "GitLab",
            "devops": "DevOps", "powerpoint": "PowerPoint", "sqlite": "SQLite", "fastapi": "FastAPI", "ios": "iOS",
            "etl": "ETL", "api": "API", "sql": "SQL", "aws": "AWS", "gcp": "GCP", "html": "HTML", "css": "CSS",
            "ux": "UX", "ui": "UI", "crm": "CRM", "erp": "ERP", "seo": "SEO", "sap": "SAP", "nlp": "NLP", "pmp": "PMP",
            "lgpd": "LGPD", "itil": "ITIL", "tdd": "TDD", "rest": "REST", "dbt": "dbt"}


def _pretty(term: str) -> str:
    if term in _SPECIAL:
        return _SPECIAL[term]
    if term in VOCAB_TERMS:
        return " ".join(w if w in ("de", "da", "do", "e", "ao") else w.capitalize() for w in term.split())
    return term  # requisito em texto livre: mantém como na vaga


# ------------------------------------------------------------------ análise
def _demo_analysis(b: dict, job: dict) -> dict:
    job_text = f"{job['title']}\n{job['description']}"
    reqs = _extract_requirements(job_text)
    sources = _profile_sources(b)
    met, missing = [], []
    for r in reqs:
        ev = _find_evidence(sources, r) if r in VOCAB_TERMS else _matches_free_text(sources, r)
        (met if ev else missing).append({"requirement": _pretty(r), "evidence": ev} if ev else _pretty(r))
    total = len(reqs)
    score = round(100 * len(met) / total) if total else None

    strengths, improvements, suggestions = [], [], []
    if met:
        strengths.append(f"{len(met)} de {total} requisitos identificados aparecem no seu perfil.")
    modes = b["profile"]["work_modes"]
    if job.get("work_mode") and job["work_mode"] in modes:
        strengths.append(f"A modalidade da vaga ({job['work_mode']}) está entre as que você deseja.")
    if b["experiences"]:
        strengths.append(f"Você tem {len(b['experiences'])} experiência(s) profissional(is) cadastrada(s).")
    if b["courses"]:
        strengths.append(f"{len(b['courses'])} curso(s)/certificação(ões) cadastrado(s) reforçam o perfil.")
    if job.get("work_mode") and modes and job["work_mode"] not in modes:
        improvements.append(f"A vaga é {job['work_mode']}, mas sua preferência cadastrada é: {', '.join(modes)}.")
    if missing:
        improvements.append("Requisitos não encontrados no perfil: " + "; ".join(missing[:6])
                            + (" …" if len(missing) > 6 else "") + ".")
    if not b["skills"]:
        improvements.append("Seu perfil não tem habilidades cadastradas — isso reduz a precisão da análise.")
    if not b["experiences"]:
        improvements.append("Nenhuma experiência cadastrada para comparar com a vaga.")
    if not reqs:
        improvements.append("Não foi possível identificar requisitos na descrição. Cole a descrição completa da vaga.")
    if met:
        suggestions.append("Destaque no resumo e nas primeiras linhas do currículo: "
                           + ", ".join(m["requirement"] for m in met[:6]) + ".")
        suggestions.append("Coloque as habilidades que a vaga pede no início da lista de habilidades.")
    if missing:
        suggestions.append("Se você realmente possui alguma experiência com os itens não encontrados, "
                           "cadastre no perfil com contexto real. Não inclua o que você não fez.")
    suggestions.append("Descreva resultados reais (o que você fez e o impacto) nas experiências mais relacionadas à vaga.")
    return {"score": score, "met": met, "missing": missing, "strengths": strengths,
            "improvements": improvements, "suggestions": suggestions}


def _llm_analysis(b: dict, job: dict, profile_text: str) -> dict:
    prompt = f"""PERFIL DO CANDIDATO:
{profile_text}

VAGA:
Empresa: {job['company']}
Cargo: {job['title']}
Local: {job.get('location', '')} | Modalidade: {job.get('work_mode', '')} | Salário: {job.get('salary', '')}
Descrição:
{job.get('description', '')}

Analise a compatibilidade entre o perfil e a vaga. Responda APENAS com JSON neste formato:
{{"score": <inteiro 0-100>,
 "met": [{{"requirement": "<requisito da vaga>", "evidence": "<onde aparece no perfil>"}}],
 "missing": ["<requisito da vaga que NÃO aparece no perfil>"],
 "strengths": ["..."], "improvements": ["..."],
 "suggestions": ["<sugestões de como personalizar o currículo SEM inventar nada>"]}}"""
    data = _parse_json(_call_llm(SYSTEM_RULES, prompt, json_mode=True))
    try:
        score = max(0, min(100, int(round(float(data.get("score", 0))))))
    except (TypeError, ValueError):
        score = None
    met = []
    for m in data.get("met", []) or []:
        if isinstance(m, dict):
            met.append({"requirement": str(m.get("requirement", "")), "evidence": str(m.get("evidence", ""))})
        else:
            met.append({"requirement": str(m), "evidence": ""})
    as_list = lambda k: [str(x) for x in (data.get(k) or [])]  # noqa: E731
    return {"score": score, "met": met, "missing": as_list("missing"), "strengths": as_list("strengths"),
            "improvements": as_list("improvements"), "suggestions": as_list("suggestions")}


def analyze(b: dict, job: dict) -> dict:
    """Retorna análise com chaves: score, met, missing, strengths, improvements, suggestions,
    simulated (bool), provider (str), note (str)."""
    from .profile_data import bundle_to_text
    prov, note = provider(), ""
    if prov != "demo":
        try:
            res = _llm_analysis(b, job, bundle_to_text(b))
            res.update(simulated=False, provider=prov, note="")
            return res
        except AIError as exc:
            note = f"A API de IA falhou ({exc}). Exibindo resultado SIMULADO. "
    res = _demo_analysis(b, job)
    res.update(simulated=True, provider="demo", note=note + DEMO_NOTE)
    return res


# ------------------------------------------------------------------ documentos
def _rel_terms(b: dict, job: dict) -> set[str]:
    folded = _fold(f"{job['title']} {job.get('description', '')}")
    return {t for t in VOCAB_TERMS if _has_term(folded, t)}


def _demo_resume(b: dict, job: dict) -> str:
    p, terms = b["profile"], _rel_terms(b, job)
    folded_terms = {_fold(t) for t in terms}

    def relevance(skill: str) -> int:
        f = _fold(skill)
        return 0 if any(f == t or t in f for t in folded_terms) else 1

    L = [f"# {p['full_name'] or 'Seu Nome'}"]
    if p["headline"]:
        L.append(p["headline"])
    contact = " | ".join(x for x in [p["email"], p["phone"], p["location"], p["linkedin_url"]] if x)
    if contact:
        L.append(contact)
    if p["summary"]:
        L += ["", "## Resumo profissional", p["summary"]]
    if b["skills"]:
        L += ["", "## Habilidades", ", ".join(sorted(b["skills"], key=relevance))]
    if b["experiences"]:
        L += ["", "## Experiência profissional"]
        for e in b["experiences"]:
            period = " – ".join(x for x in [e["start"], e["end"]] if x)
            L.append("### " + " — ".join(x for x in [e["title"], e["company"], period] if x))
            for ln in (e["description"] or "").splitlines():
                if ln.strip():
                    L.append("- " + ln.strip().lstrip("-•* ").strip())
    if b["education"]:
        L += ["", "## Formação acadêmica"]
        for e in b["education"]:
            period = " – ".join(x for x in [e["start"], e["end"]] if x)
            L.append("- " + " — ".join(x for x in [e["degree"], e["institution"], period] if x))
    if b["courses"]:
        L += ["", "## Cursos e certificações"]
        for c in b["courses"]:
            L.append("- " + " — ".join(x for x in [c["name"], c["issuer"], c["year"]] if x))
    return "\n".join(L)


def _demo_cover(b: dict, job: dict) -> str:
    p = b["profile"]
    sources = _profile_sources(b)
    matched = [t for t in sorted(_rel_terms(b, job)) if _find_evidence(sources, t)]
    L = [f"# {p['full_name'] or 'Seu Nome'}",
         " | ".join(x for x in [p["email"], p["phone"], p["location"]] if x), "",
         f"Prezados(as) responsáveis pela seleção da {job['company']},", "",
         f"Venho manifestar meu interesse na vaga de {job['title']}."]
    if p["headline"] or p["summary"]:
        L += ["", " ".join(x for x in [p["headline"] + "." if p["headline"] else "", p["summary"]] if x)]
    if b["experiences"]:
        e = b["experiences"][0]
        who = " na ".join(x for x in [e["title"], e["company"]] if x)
        if who:
            L += ["", f"Minha experiência mais recente cadastrada é como {who}."]
    if matched:
        L += ["", "Entre os requisitos da vaga, tenho em meu histórico: " + ", ".join(matched[:8]) + "."]
    L += ["", "Coloco-me à disposição para uma conversa, na qual poderei detalhar minha trajetória e como posso "
              "contribuir com a equipe.", "", "Atenciosamente,", p["full_name"] or "Seu Nome"]
    return "\n".join(L)


def _llm_document(kind: str, b: dict, job: dict, analysis: dict | None) -> str:
    from .profile_data import bundle_to_text
    extra = ""
    if analysis:
        extra = ("\nRequisitos atendidos (use como foco): " + "; ".join(m["requirement"] for m in analysis.get("met", []))
                 + "\nRequisitos NÃO encontrados (NÃO mencione como se o candidato tivesse): "
                 + "; ".join(analysis.get("missing", [])))
    fmt = ("Formato: texto simples com marcação: '# Nome' (título), '## SEÇÃO', '### Cargo — Empresa — período', "
           "'- item' para marcadores." if kind == "resume" else
           "Formato: carta de apresentação em texto simples, 3 a 4 parágrafos curtos, tom profissional, "
           "sem inventar nada. Comece com '# Nome' e linha de contato.")
    task = ("Gere um CURRÍCULO direcionado à vaga, reorganizando e destacando o que já existe no perfil."
            if kind == "resume" else "Escreva uma CARTA DE APRESENTAÇÃO para a vaga usando apenas fatos do perfil.")
    prompt = (f"{task}\n{fmt}\n\nPERFIL:\n{bundle_to_text(b)}\n\nVAGA:\n{job['company']} — {job['title']}\n"
              f"{job.get('description', '')}\n{extra}\n\nResponda somente com o documento, sem comentários.")
    return _call_llm(SYSTEM_RULES, prompt, json_mode=False).strip()


def generate_document(kind: str, b: dict, job: dict, analysis: dict | None = None) -> dict:
    """kind: 'resume' | 'cover'. Retorna {content, simulated, provider, note}."""
    prov, note = provider(), ""
    if prov != "demo":
        try:
            return {"content": _llm_document(kind, b, job, analysis), "simulated": False, "provider": prov, "note": ""}
        except AIError as exc:
            note = f"A API de IA falhou ({exc}). Documento gerado em modo SIMULADO. "
    content = _demo_resume(b, job) if kind == "resume" else _demo_cover(b, job)
    return {"content": content, "simulated": True, "provider": "demo",
            "note": note + "Modo demonstrativo: o texto foi montado por modelo fixo usando apenas os dados do seu perfil "
                           "(nada foi escrito por IA). Revise antes de salvar."}
