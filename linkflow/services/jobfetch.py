"""Lê uma página pública de vaga a partir de um link e extrai cargo, empresa, local e descrição.

Sem login, sem raspagem em massa: uma única requisição por link informado pelo usuário, com proteção contra SSRF.
LinkedIn e sites que exigem login não são lidos (o usuário cola a descrição).
"""
import html
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 1_500_000
TIMEOUT = 10
BLOCKED_HOSTS = ("linkedin.com", "lnkd.in")  # exigem login / proíbem leitura automatizada
UA = "Mozilla/5.0 (compatible; LinkFlow/1.0; leitura de uma vaga a pedido do usuário)"


class FetchError(Exception):
    """Mensagem em português, segura para mostrar ao usuário."""


def _host_ok(host: str) -> None:
    if not host:
        raise FetchError("Link inválido.")
    h = host.lower().rstrip(".")
    if any(h == b or h.endswith("." + b) for b in BLOCKED_HOSTS):
        raise FetchError("O LinkedIn não permite leitura automática. Cole aqui a descrição da vaga (copie do site) que eu monto o currículo.")
    try:
        infos = socket.getaddrinfo(h, None)
    except socket.gaierror:
        raise FetchError("Não consegui encontrar esse endereço. Confira o link.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise FetchError("Esse endereço não é permitido.")


def fetch_html(url: str) -> tuple[str, str]:
    """Retorna (html, url_final). Segue até 4 redirecionamentos, validando cada destino."""
    cur = url
    for _ in range(5):
        p = urlparse(cur)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchError("O link precisa começar com http:// ou https://.")
        if p.port not in (None, 80, 443):
            raise FetchError("Porta não permitida.")
        _host_ok(p.hostname)
        try:
            r = requests.get(cur, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                                           "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
                             timeout=TIMEOUT, allow_redirects=False, stream=True)
        except requests.RequestException:
            raise FetchError("Não consegui abrir o link (site fora do ar ou bloqueou o acesso).")
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            cur = urljoin(cur, r.headers["Location"])
            r.close()
            continue
        if r.status_code in (401, 403, 999):
            raise FetchError("O site bloqueou a leitura automática. Cole a descrição da vaga no campo abaixo.")
        if r.status_code >= 400:
            raise FetchError(f"O site respondeu com erro {r.status_code}. Confira o link ou cole a descrição.")
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            raise FetchError("O link não aponta para uma página de vaga.")
        raw = b""
        for chunk in r.iter_content(65536):
            raw += chunk
            if len(raw) > MAX_BYTES:
                break
        r.close()
        enc = r.encoding or "utf-8"
        try:
            return raw.decode(enc, errors="replace"), cur
        except LookupError:
            return raw.decode("utf-8", errors="replace"), cur
    raise FetchError("Redirecionamentos demais.")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.jsonld, self._in_ld = [], 0, [], False
        self.title, self._in_title, self.meta, self.h1 = "", False, {}, ""
        self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and "ld+json" in (a.get("type") or ""):
            self._in_ld = True
            self.jsonld.append("")
            return
        if tag == "meta":
            k = a.get("property") or a.get("name")
            if k and a.get("content"):
                self.meta.setdefault(k.lower(), a["content"])
        if tag == "title":
            self._in_title = True
        if tag == "h1" and not self.h1:
            self._in_h1 = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            return
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_ld:
            self.jsonld[-1] += data
            return
        if self._in_title:
            self.title += data
        if self._in_h1:
            self.h1 += data
        if not self.skip:
            self.parts.append(data)


def _clean(text: str) -> str:
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    out, blank = [], 0
    for l in lines:
        if not l:
            blank += 1
            if blank == 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(l)
    return "\n".join(out).strip()


def _find_posting(node):
    if isinstance(node, list):
        for n in node:
            r = _find_posting(n)
            if r:
                return r
    elif isinstance(node, dict):
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            return node
        return _find_posting(node.get("@graph", []))
    return None


def _loc(posting: dict) -> str:
    loc = posting.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        adr = loc.get("address")
        if isinstance(adr, dict):
            return ", ".join(str(adr[k]) for k in ("addressLocality", "addressRegion") if adr.get(k))
        if isinstance(adr, str):
            return adr
    return ""


def _salary(posting: dict) -> str:
    s = posting.get("baseSalary")
    if isinstance(s, dict):
        v = s.get("value")
        if isinstance(v, dict):
            lo, hi, cur = v.get("minValue"), v.get("maxValue"), s.get("currency", "")
            if lo and hi:
                return f"{cur} {lo}–{hi}".strip()
            if v.get("value"):
                return f"{cur} {v['value']}".strip()
    return ""


def parse_job_page(page: str, url: str) -> dict:
    p = _Text()
    try:
        p.feed(page)
    except Exception:  # HTML malformado: usa o que foi lido
        pass
    host = (urlparse(url).hostname or "").removeprefix("www.")
    posting = None
    for blob in p.jsonld:
        try:
            posting = _find_posting(json.loads(blob))
        except ValueError:
            continue
        if posting:
            break
    title = company = location = salary = description = ""
    mode = ""
    if posting:
        title = html.unescape(str(posting.get("title") or ""))
        org = posting.get("hiringOrganization")
        company = html.unescape(str(org.get("name", "") if isinstance(org, dict) else org or ""))
        location, salary = _loc(posting), _salary(posting)
        d = _Text()
        d.feed(str(posting.get("description") or ""))
        description = _clean("".join(d.parts))
        if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE":
            mode = "remote"
    if not title:
        title = (p.meta.get("og:title") or p.h1 or p.title).strip()
        title = re.split(r"\s[|\-–—·]\s", title)[0].strip() if len(title) > 90 else title
    if not company:
        company = p.meta.get("og:site_name", "").strip() or host
    if len(description) < 200:
        body = _clean("".join(p.parts))
        description = body if len(body) > len(description) else description
    return {"title": _clean(title)[:200], "company": _clean(company)[:200], "location": location[:200],
            "salary": salary[:100], "description": description[:20000], "work_mode": mode}


def read_job(url: str) -> dict:
    page, final = fetch_html(url)
    job = parse_job_page(page, final)
    if len(job["description"]) < 200 or not job["title"]:
        raise FetchError("Não consegui encontrar a descrição nessa página (talvez ela carregue só com JavaScript ou exija login). "
                         "Cole a descrição da vaga no campo abaixo.")
    return job
