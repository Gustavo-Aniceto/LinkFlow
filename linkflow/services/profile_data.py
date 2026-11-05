"""Leitura/escrita do perfil profissional como um dicionário único."""
from ..db import get_db, now

PROFILE_FIELDS = ["full_name", "email", "phone", "location", "headline", "summary",
                  "linkedin_url", "salary_expectation"]


def empty_bundle() -> dict:
    return {
        "profile": {f: "" for f in PROFILE_FIELDS} | {"work_modes": []},
        "experiences": [], "education": [], "courses": [], "skills": [],
    }


def load_bundle(user_id: int) -> dict:
    db = get_db()
    b = empty_bundle()
    row = db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if row:
        for f in PROFILE_FIELDS:
            b["profile"][f] = row[f] or ""
        b["profile"]["work_modes"] = [m for m in (row["work_modes"] or "").split(",") if m]
    b["experiences"] = [dict(r) for r in db.execute(
        'SELECT company,title,"start","end",description FROM experiences WHERE user_id=? ORDER BY position,id', (user_id,))]
    b["education"] = [dict(r) for r in db.execute(
        'SELECT institution,degree,"start","end" FROM education WHERE user_id=? ORDER BY position,id', (user_id,))]
    b["courses"] = [dict(r) for r in db.execute(
        "SELECT name,issuer,year FROM courses WHERE user_id=? ORDER BY position,id", (user_id,))]
    b["skills"] = [r["name"] for r in db.execute(
        "SELECT name FROM skills WHERE user_id=? ORDER BY position,id", (user_id,))]
    return b


def save_bundle(user_id: int, b: dict) -> None:
    db = get_db()
    p = b["profile"]
    db.execute(
        """INSERT INTO profiles (user_id, full_name, email, phone, location, headline, summary,
               linkedin_url, salary_expectation, work_modes, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, email=excluded.email,
               phone=excluded.phone, location=excluded.location, headline=excluded.headline,
               summary=excluded.summary, linkedin_url=excluded.linkedin_url,
               salary_expectation=excluded.salary_expectation, work_modes=excluded.work_modes,
               updated_at=excluded.updated_at""",
        (user_id, *(p[f] for f in PROFILE_FIELDS), ",".join(p["work_modes"]), now()),
    )
    for table in ("experiences", "education", "courses", "skills"):
        db.execute("DELETE FROM " + table + " WHERE user_id=?", (user_id,))
    for i, e in enumerate(b["experiences"]):
        db.execute('INSERT INTO experiences (user_id,position,company,title,"start","end",description) VALUES (?,?,?,?,?,?,?)',
                   (user_id, i, e["company"], e["title"], e["start"], e["end"], e["description"]))
    for i, e in enumerate(b["education"]):
        db.execute('INSERT INTO education (user_id,position,institution,degree,"start","end") VALUES (?,?,?,?,?,?)',
                   (user_id, i, e["institution"], e["degree"], e["start"], e["end"]))
    for i, c in enumerate(b["courses"]):
        db.execute("INSERT INTO courses (user_id,position,name,issuer,year) VALUES (?,?,?,?,?)",
                   (user_id, i, c["name"], c["issuer"], c["year"]))
    for i, s in enumerate(b["skills"]):
        db.execute("INSERT INTO skills (user_id,position,name) VALUES (?,?,?)", (user_id, i, s))


def bundle_is_empty(b: dict) -> bool:
    p = b["profile"]
    return not (any(p[f] for f in PROFILE_FIELDS) or b["experiences"] or b["education"]
                or b["courses"] or b["skills"])


def bundle_to_text(b: dict) -> str:
    """Representação em texto do perfil (usada nos prompts e na comparação)."""
    p = b["profile"]
    out = [f"Nome: {p['full_name']}", f"Título: {p['headline']}", f"Localização: {p['location']}",
           f"Modalidades desejadas: {', '.join(p['work_modes'])}",
           f"Pretensão salarial: {p['salary_expectation']}", f"Resumo: {p['summary']}",
           "Habilidades: " + ", ".join(b["skills"]), "", "EXPERIÊNCIAS:"]
    for e in b["experiences"]:
        out.append(f"- {e['title']} | {e['company']} | {e['start']} – {e['end']}\n  {e['description']}")
    out.append("\nFORMAÇÃO:")
    for e in b["education"]:
        out.append(f"- {e['degree']} | {e['institution']} | {e['start']} – {e['end']}")
    out.append("\nCURSOS E CERTIFICAÇÕES:")
    for c in b["courses"]:
        out.append(f"- {c['name']} | {c['issuer']} | {c['year']}")
    return "\n".join(out)
