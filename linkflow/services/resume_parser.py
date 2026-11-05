"""Extração de texto de PDF e interpretação heurística em seções do perfil.

A interpretação é por regras (sem IA): funciona offline, mas é aproximada.
Por isso o resultado sempre passa por revisão/edição do usuário antes de ser salvo.
"""
import re
import subprocess
import unicodedata
from pathlib import Path


class ResumeError(Exception):
    pass


# ------------------------------------------------------------------ extração
def extract_text(path: Path) -> str:
    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        text = ""
    if len(text.strip()) < 30:
        try:
            from pypdf import PdfReader
            text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except Exception:
            text = ""
    if len(text.strip()) < 30:
        try:
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                                  text=True, timeout=60).stdout
        except Exception:
            text = ""
    if len(text.strip()) < 30:
        raise ResumeError("Não foi possível extrair texto deste PDF. Ele pode ser uma imagem "
                          "escaneada (sem texto selecionável). Preencha o perfil manualmente "
                          "ou envie um PDF com texto.")
    return text


# ------------------------------------------------------------------ utilidades
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip(" :·•-–—\t")


SECTIONS = {
    "summary": {"resumo", "resumo profissional", "perfil", "perfil profissional", "objetivo",
                "objetivo profissional", "sobre", "sobre mim", "summary", "professional summary",
                "profile", "about", "about me"},
    "experience": {"experiencia", "experiencias", "experiencia profissional",
                   "experiencias profissionais", "historico profissional", "work experience",
                   "experience", "professional experience", "employment history", "carreira"},
    "education": {"formacao", "formacao academica", "educacao", "escolaridade", "education",
                  "academic background", "formacao e educacao"},
    "courses": {"cursos", "certificacoes", "cursos e certificacoes", "certificacoes e cursos",
                "cursos complementares", "certifications", "courses", "training",
                "cursos e treinamentos", "licencas e certificacoes"},
    "skills": {"habilidades", "competencias", "skills", "tecnologias", "conhecimentos",
               "ferramentas", "habilidades tecnicas", "technical skills", "competencias tecnicas",
               "principais habilidades", "top skills"},
    "ignore": {"idiomas", "languages", "referencias", "references", "informacoes adicionais",
               "interesses", "contato", "contact", "dados pessoais"},
}
HEADING_LOOKUP = {name: sec for sec, names in SECTIONS.items() for name in names}

MONTH = r"(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez|feb|apr|may|aug|sep|oct|dec)[a-z]*\.?"
DATE = rf"(?:(?:{MONTH}[\s/.]*(?:de\s*)?|\d{{1,2}}/)?(?:19|20)\d{{2}})"
PRESENT = r"(?:atual|atualmente|presente|present|current|hoje|o momento)"
DATE_RANGE = re.compile(
    rf"({DATE})\s*(?:-|–|—|a|até|ate|to)\s*({DATE}|{PRESENT})", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?9?\d{4}[\s.-]?\d{4}")
LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/[^\s,;|]+", re.IGNORECASE)
LOCATION = re.compile(r"([A-ZÀ-Ú][\wÀ-ÿ.' ]+)\s*[,\-–/]\s*([A-Z]{2})\b")
BULLET = re.compile(r"^\s*[•●▪■◦\-–—*·➢>]\s*")
DEGREE_WORDS = ("bacharel", "licenciatura", "tecnolog", "graduac", "pos-graduac", "pos graduac", "mba",
                "mestrado", "doutorado", "especializac", "ensino medio", "tecnico", "bachelor",
                "master", "phd", "degree", "associate", "engenharia", "administracao", "ciencia")
INSTITUTION_WORDS = ("universidade", "faculdade", "instituto", "centro universitario", "escola",
                     "university", "college", "institute", "school", "senai", "senac", "fatec",
                     "usp", "unicamp", "ufmg", "ufrj", "puc")


def _split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current = None
    for line in lines:
        key = _norm(line)
        sec = HEADING_LOOKUP.get(key) if 0 < len(key) <= 40 else None
        if sec:
            current = sec
            sections.setdefault(sec, [])
            continue
        (sections[current] if current else header).append(line)
    return header, sections


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" |,;-–—·•")


def _split_parts(s: str) -> list[str]:
    return [p for p in (_clean(x) for x in re.split(r"\s+[|\-–—@]\s+|\s*\|\s*|,\s+(?=[A-ZÀ-Ú])", s)) if p]


# ------------------------------------------------------------------ seções
def _parse_header(lines: list[str], text: str) -> dict:
    prof = {"full_name": "", "email": "", "phone": "", "location": "", "headline": "",
            "summary": "", "linkedin_url": "", "salary_expectation": "", "work_modes": []}
    if m := EMAIL.search(text):
        prof["email"] = m.group(0)
    if m := LINKEDIN.search(text):
        url = m.group(0).rstrip(".")
        prof["linkedin_url"] = url if url.startswith("http") else "https://" + url
    for line in lines:
        no_email = EMAIL.sub("", LINKEDIN.sub("", line))
        if m := PHONE.search(no_email):
            digits = re.sub(r"\D", "", m.group(0))
            if 10 <= len(digits) <= 13:
                prof["phone"] = _clean(m.group(0))
                break
    candidates = [l for l in lines if l.strip()]
    for line in candidates[:6]:
        if not prof["full_name"] and not EMAIL.search(line) and not re.search(r"\d", line) \
                and 2 <= len(line.split()) <= 6 and len(line) < 60 and "linkedin" not in line.lower():
            prof["full_name"] = _clean(line).title() if line.isupper() else _clean(line)
            continue
        if prof["full_name"] and not prof["headline"] and not EMAIL.search(line) \
                and not re.search(r"\d{4}", line) and len(line) < 90 and line.strip() != prof["full_name"]:
            if not LOCATION.fullmatch(_clean(line)):
                prof["headline"] = _clean(line)
    for line in candidates[:8]:
        for part in re.split(r"\s*[|•·]\s*", line):
            part = _clean(part)
            if (m := LOCATION.fullmatch(part)) and not re.search(r"@|linkedin|\d", part, re.I):
                prof["location"] = _clean(f"{m.group(1)}, {m.group(2)}")
                break
        if prof["location"]:
            break
    return prof


def _parse_experience(lines: list[str]) -> list[dict]:
    lines = [l for l in lines if l.strip()]
    date_idx = [i for i, l in enumerate(lines) if DATE_RANGE.search(l)]
    if not date_idx:
        blob = "\n".join(lines).strip()
        return [{"company": "", "title": "", "start": "", "end": "", "description": blob}] if blob else []

    def is_header_line(l: str) -> bool:
        return (not BULLET.match(l) and len(l) < 70 and not l.rstrip().endswith(".")
                and not DATE_RANGE.search(l))

    starts, prev_start = [], -1
    for i in date_idx:
        rest = _clean(DATE_RANGE.sub("", lines[i]))
        want = 2 if not rest else 1
        k = 0
        while k < want and i - k - 1 > prev_start and i - k - 1 not in date_idx and is_header_line(lines[i - k - 1]):
            k += 1
        starts.append(i - k)
        prev_start = i
    entries = []
    for n, s in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = lines[s:end]
        di = date_idx[n] - s
        m = DATE_RANGE.search(block[di])
        head = [l for l in block[:di] if l.strip()]
        rest = _clean(DATE_RANGE.sub("", block[di]))
        if rest:
            head.append(rest)
        head_parts = []
        for h in head:
            head_parts += _split_parts(h) or [h]
        title = head_parts[0] if head_parts else ""
        company = head_parts[1] if len(head_parts) > 1 else ""
        desc = "\n".join(BULLET.sub("", l).strip() for l in block[di + 1:] if l.strip())
        end_val = m.group(2)
        if re.fullmatch(PRESENT, end_val, re.I):
            end_val = "Atual"
        entries.append({"company": company, "title": title, "start": m.group(1), "end": end_val,
                        "description": desc})
    return entries


def _parse_education(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    cur: dict | None = None
    for raw in (l for l in lines if l.strip()):
        line = BULLET.sub("", raw)
        start = end = ""
        if m := DATE_RANGE.search(line):
            start, end = m.group(1), m.group(2)
            line = DATE_RANGE.sub("", line)
        elif m := re.search(r"(?:19|20)\d{2}", line):
            end = m.group(0)
            line = line.replace(end, "")
        line = _clean(line)
        if not line:
            if cur is not None:
                cur["start"], cur["end"] = cur["start"] or start, cur["end"] or end
            continue
        n = _norm(line)
        is_inst = any(w in n for w in INSTITUTION_WORDS)
        is_deg = any(w in n for w in DEGREE_WORDS)
        if is_inst and not is_deg:
            if cur is None or cur["institution"]:
                cur = {"institution": "", "degree": "", "start": "", "end": ""}
                entries.append(cur)
            cur["institution"] = line
        else:
            if cur is None or cur["degree"]:
                cur = {"institution": "", "degree": "", "start": "", "end": ""}
                entries.append(cur)
            parts = _split_parts(line)
            cur["degree"] = parts[0] if parts else line
            if len(parts) > 1 and not cur["institution"]:
                cur["institution"] = parts[1]
        cur["start"], cur["end"] = cur["start"] or start, cur["end"] or end
    return entries


def _parse_courses(lines: list[str]) -> list[dict]:
    out = []
    for raw in (l for l in lines if l.strip()):
        line = BULLET.sub("", raw)
        year = ""
        if m := re.search(r"(?:19|20)\d{2}", line):
            year = m.group(0)
            line = _clean(line.replace(year, "").replace("()", ""))
        parts = _split_parts(line)
        if parts:
            out.append({"name": parts[0], "issuer": parts[1] if len(parts) > 1 else "", "year": year})
    return out


def _parse_skills(lines: list[str]) -> list[str]:
    seen, out = set(), []
    for line in lines:
        line = re.sub(r"^[^:]{1,30}:\s*", "", BULLET.sub("", line)) if ":" in line else BULLET.sub("", line)
        for item in re.split(r"[,;•●▪|•·]|\s{3,}|\s[-–—]\s", line):
            item = _clean(item)
            if 1 < len(item) <= 40 and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
    return out


def parse_resume_text(text: str) -> dict:
    lines = [l.rstrip() for l in text.replace("\r", "").split("\n")]
    header, sections = _split_sections(lines)
    bundle = {"profile": _parse_header(header, text), "experiences": [], "education": [],
              "courses": [], "skills": []}
    if "summary" in sections:
        bundle["profile"]["summary"] = " ".join(_clean(l) for l in sections["summary"] if l.strip())
    bundle["experiences"] = _parse_experience(sections.get("experience", []))
    bundle["education"] = _parse_education(sections.get("education", []))
    bundle["courses"] = _parse_courses(sections.get("courses", []))
    bundle["skills"] = _parse_skills(sections.get("skills", []))
    return bundle
