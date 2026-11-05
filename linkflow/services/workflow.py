"""Operações compartilhadas: análise armazenada, pacote de candidatura e texto de e-mail."""
import json
import re

from ..db import log_activity, now, set_job_status
from . import ai


def latest_analysis(db, job_id: int):
    row = db.execute("SELECT * FROM analyses WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    if row is None:
        return None
    data = json.loads(row["result_json"])
    data.update(created_at=row["created_at"], simulated=bool(row["simulated"]), provider=row["provider"])
    return data


def run_and_store_analysis(db, uid: int, job, bundle: dict) -> dict:
    result = ai.analyze(bundle, dict(job))
    db.insert("INSERT INTO analyses (job_id,user_id,score,result_json,simulated,provider,created_at) VALUES (?,?,?,?,?,?,?)",
              (job["id"], uid, result["score"], json.dumps(result, ensure_ascii=False),
               1 if result["simulated"] else 0, result["provider"], now()))
    if job["status"] == "found":
        set_job_status(uid, job["id"], "analyzed", quiet=True)
    score_txt = f"{result['score']}%" if result["score"] is not None else "sem pontuação"
    log_activity(uid, f"Análise de compatibilidade{' (simulada)' if result['simulated'] else ''}: "
                      f"“{job['title']}” — {score_txt}")
    return result


DOC_LABELS = {"resume": "Currículo", "cover": "Carta de apresentação"}


def build_package(db, uid: int, job, bundle: dict) -> dict:
    """Gera (ou reaproveita a análise) e salva currículo + carta direcionados. Não envia nada."""
    analysis = latest_analysis(db, job["id"]) or run_and_store_analysis(db, uid, job, bundle)
    ids, simulated = {}, False
    ts = now()
    for kind in ("resume", "cover"):
        out = ai.generate_document(kind, bundle, dict(job), analysis)
        simulated = simulated or out["simulated"]
        ids[kind] = db.insert(
            "INSERT INTO documents (user_id,job_id,kind,title,content,simulated,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (uid, job["id"], kind, f"{DOC_LABELS[kind]} — {job['title']} ({job['company']})", out["content"],
             1 if out["simulated"] else 0, ts, ts))
    if job["status"] in ("found", "analyzed"):
        set_job_status(uid, job["id"], "ready", quiet=True)
    log_activity(uid, f"Pacote de candidatura montado{' (simulado)' if simulated else ''}: “{job['title']}” ({job['company']})")
    return {"ids": ids, "simulated": simulated, "analysis": analysis}


def latest_doc(db, uid: int, job_id: int, kind: str):
    return db.execute("SELECT * FROM documents WHERE user_id=? AND job_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                      (uid, job_id, kind)).fetchone()


def cover_to_email_body(content: str) -> str:
    """Corpo do e-mail a partir da carta: remove o cabeçalho (# Nome + linha de contato)."""
    lines = (content or "").replace("\r", "").split("\n")
    if lines and lines[0].startswith("# "):
        i = 1
        while i < len(lines) and lines[i].strip():
            i += 1
        lines = lines[i:]
    text = "\n".join(l for l in lines if not l.startswith("#")).strip()
    return re.sub(r"\n{3,}", "\n\n", text)
