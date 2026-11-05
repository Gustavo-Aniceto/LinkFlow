"""Garante que o SQL do app é portátil entre SQLite e Postgres e que o PDF é lido sem pdfplumber.
Execute: python tests/test_portability.py"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FORBIDDEN = [r"INSERT\s+OR\s+(IGNORE|REPLACE)", r"\.lastrowid", r"\bIFNULL\(", r"GROUP_CONCAT",
             r"\bAUTOINCREMENT\b", r"\bPRAGMA\b", r"\browid\b", r"\bexecutescript\b"]
ALLOWED_FILES = {"linkflow/db.py"}


def check(cond, text):
    print(("✔ " if cond else "✘ ") + text)
    cond or sys.exit(1)


def main():
    bad = []
    for path in (ROOT / "linkflow").rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        if rel in ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.search(r"\b(SELECT|WHERE|INSERT|UPDATE)\b", line) and re.search(r"\b(strftime|datetime|date)\(", line):
                bad.append(f"{rel}: função de data SQLite: {line.strip()[:60]}")
        for pat in FORBIDDEN:
            for m in re.finditer(pat, text):
                bad.append(f"{rel}: {m.group(0)}")
        # colunas reservadas no Postgres sem aspas dentro de SQL
        for m in re.finditer(r'(SELECT|INSERT INTO|UPDATE)[^"\n]*?[\s,(](start|end)[\s,)=]', text):
            if '"' + m.group(2) + '"' not in m.group(0):
                bad.append(f"{rel}: coluna '{m.group(2)}' sem aspas: {m.group(0)[:70]}")
    check(not bad, "nenhum SQL específico de SQLite fora de db.py" + (": " + "; ".join(bad) if bad else ""))

    from linkflow.db import DB, SCHEMA, _statements
    pg = DB(None, "postgres")
    check(pg._sql("SELECT * FROM jobs WHERE company LIKE ? AND id=?") == "SELECT * FROM jobs WHERE company ILIKE %s AND id=%s", "placeholders ? → %s e LIKE → ILIKE no Postgres")
    sq = DB(None, "sqlite")
    check(sq._sql("SELECT ? LIKE ?") == "SELECT ? LIKE ?", "SQLite não é alterado")
    stmts = _statements("postgres")
    check(all("AUTOINCREMENT" not in s for s in stmts) and any("SERIAL PRIMARY KEY" in s for s in stmts), "DDL do Postgres usa SERIAL")
    check(all(re.search(r'"start"|"end"', s) or not re.search(r"\b(start|end) TEXT", s) for s in stmts), "colunas start/end quoted no DDL")
    check(len(stmts) == len(_statements("sqlite")) and len(stmts) >= 15, f"{len(stmts)} statements de schema")

    # leitura de PDF sem pdfplumber (dependência opcional na Vercel)
    sys.modules["pdfplumber"] = None
    from linkflow.services import resume_parser
    sys.path.insert(0, str(ROOT / "tests"))
    import test_e2e as T
    pdf = Path(tempfile.mkdtemp()) / "cv.pdf"
    T.make_pdf(pdf)
    text = resume_parser.extract_text(pdf)
    parsed = resume_parser.parse_resume_text(text)
    check(parsed["profile"]["full_name"] == "Maria Souza Lima" and len(parsed["experiences"]) == 2 and "Power BI" in parsed["skills"],
          "extração de PDF funciona apenas com pypdf")
    print("\nTodos os testes de portabilidade passaram.")


if __name__ == "__main__":
    main()
