"""Testa os caminhos de IA real (Anthropic/OpenAI) com HTTP simulado e o fallback rotulado. python tests/test_ai_providers.py"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests  # noqa: E402

from linkflow import create_app  # noqa: E402
from linkflow.services import ai  # noqa: E402
from linkflow.services.profile_data import empty_bundle  # noqa: E402

BUNDLE = empty_bundle()
BUNDLE["profile"].update(full_name="Ana", headline="Dev")
BUNDLE["skills"] = ["Python"]
JOB = {"company": "ACME", "title": "Dev Python", "description": "Python e Go", "work_mode": "remoto"}
ANALYSIS_JSON = {"score": 70, "met": [{"requirement": "Python", "evidence": "Habilidades"}], "missing": ["Go"],
                 "strengths": ["a"], "improvements": ["b"], "suggestions": ["c"]}


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._p, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._p


def check(cond, msg):
    print(("✔ " if cond else "✘ ") + msg)
    cond or sys.exit(1)


def app_with(**cfg):
    tmp = Path(tempfile.mkdtemp())
    return create_app({"DATA_DIR": str(tmp), "DATABASE": str(tmp / "t.db"), "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", **cfg})


# Anthropic
app = app_with(ANTHROPIC_API_KEY="sk-test")
with app.app_context(), mock.patch("requests.post") as post:
    post.return_value = Resp(200, {"content": [{"type": "text", "text": "```json\n" + json.dumps(ANALYSIS_JSON) + "\n```"}]})
    r = ai.analyze(BUNDLE, JOB)
    check(ai.provider() == "anthropic" and r["simulated"] is False and r["score"] == 70, "Anthropic: análise real, não marcada como simulada")
    check(post.call_args.kwargs["headers"]["x-api-key"] == "sk-test", "Anthropic: chave enviada no header")
    post.return_value = Resp(200, {"content": [{"type": "text", "text": "# Ana\n## Resumo\nTexto"}]})
    d = ai.generate_document("resume", BUNDLE, JOB, r)
    check(d["simulated"] is False and d["content"].startswith("# Ana"), "Anthropic: documento gerado")

# OpenAI
app = app_with(OPENAI_API_KEY="sk-oa")
with app.app_context(), mock.patch("requests.post") as post:
    post.return_value = Resp(200, {"choices": [{"message": {"content": json.dumps(ANALYSIS_JSON)}}]})
    r = ai.analyze(BUNDLE, JOB)
    check(ai.provider() == "openai" and r["simulated"] is False, "OpenAI: análise real")

# Falha de API -> fallback SIMULADO e rotulado
app = app_with(ANTHROPIC_API_KEY="sk-test")
with app.app_context():
    with mock.patch("requests.post", return_value=Resp(401, {"error": "bad key"})):
        r = ai.analyze(BUNDLE, JOB)
        check(r["simulated"] is True and "falhou" in r["note"] and "SIMULADO" in r["note"], "API 401: cai para resultado SIMULADO com aviso")
    with mock.patch("requests.post", side_effect=requests.ConnectionError("offline")):
        r = ai.analyze(BUNDLE, JOB)
        check(r["simulated"] is True and "conexão" in r["note"], "Sem rede: cai para SIMULADO com aviso")
    with mock.patch("requests.post", return_value=Resp(200, {"content": [{"type": "text", "text": "sem json"}]})):
        r = ai.analyze(BUNDLE, JOB)
        check(r["simulated"] is True, "Resposta inválida da IA: cai para SIMULADO")
print("\nTodos os testes de provedores passaram.")
