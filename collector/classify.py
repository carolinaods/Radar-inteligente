"""Classificação por regras (sem IA paga). O resultado é uma RECOMENDAÇÃO automática.

classify(item, kw, clients, now) -> dict | None   (None = descartar)
match_clients(clients, text, hint) -> (confirmados, não_confirmados)
extract_deadlines(text, today) -> ["AAAA-MM-DD", ...]  (somente datas futuras)
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from .textutil import count_hits, norm, term_regex
from .util import norm as plain_norm
from .util import parse_iso

CONTENT_KIND = {
    "official": "Conteúdo oficial",
    "specialist": "Fonte especializada",
    "journalistic": "Notícia jornalística",
}
RT_OFFICIAL_LAW = "Lei ou regulamentação publicada oficialmente"
RT_TECHNICAL = "Orientação técnica ou nota técnica publicada"
RT_PENDING = "Comunicado ou previsão sujeita a confirmação"
RT_THIRD = "Análise ou interpretação de terceiros"
_PENDING_WORDS = ["previsao", "deve ", "devera", "pode ", "podera", "estuda", "proposta",
                  "consulta publica", "minuta", "projeto de lei", "expectativa"]
_LEVELS = [("critical", "Crítica"), ("high", "Alta"), ("medium", "Média"), ("info", "Informativa")]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto",
     "setembro", "outubro", "novembro", "dezembro"])}
_DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(20\d{2})(?!\d)")
_DATE_TXT = re.compile(r"(?<!\d)(\d{1,2})(?:º|o)?\s+de\s+([A-Za-zÀ-ÿ]+)\s+de\s+(20\d{2})")


def extract_deadlines(text: str, today: date) -> list[str]:
    found = {(int(y), int(m), int(d)) for d, m, y in _DATE_NUM.findall(text)}
    for d, mname, y in _DATE_TXT.findall(text):
        m = _MONTHS.get(plain_norm(mname))
        if m:
            found.add((int(y), m, int(d)))
    out = []
    for y, m, d in sorted(found):
        try:
            dt = date(y, m, d)
        except ValueError:
            continue
        if dt > today:
            out.append(dt.isoformat())
    return out


def match_clients(clients: list[dict], text: str, hint: str | None = None) -> tuple[list[str], list[str]]:
    """Associa o texto a clientes. Nomes ambíguos só confirmam com termo de contexto."""
    t = norm(text)
    confirmed, unconfirmed = [], []
    for c in clients:
        if hint and c["name"] != hint:
            continue
        names = {norm(n) for n in [c["name"], *c.get("aliases", [])] if norm(n)}
        alt = "|".join(re.escape(n).replace(r"\ ", " ") for n in sorted(names, key=len, reverse=True))
        if not re.search(r"(?<![a-z0-9])(?:" + alt + r")(?![a-z0-9])", t):
            continue
        ctx_hit = any(term_regex(x, whole=False).search(t) for x in (c.get("context") or []))
        if not c.get("ambiguous") or ctx_hit:
            confirmed.append(c["name"])
        else:
            unconfirmed.append(c["name"])
    return confirmed, unconfirmed


def classify(item: dict, kw: dict, clients: list[dict], now: datetime) -> dict | None:
    sc = kw["scoring"]
    out = dict(item)
    title, summary = item["title"], item.get("summary", "")
    raw = f"{title}. {summary}"
    text = norm(raw)
    authority = item.get("source_authority", "journalistic")

    # --- clientes ---
    hint = item.get("client_hint") if item.get("per_client_source") else None
    conf, unconf = match_clients(clients, raw, hint)
    if item.get("per_client_source") and not (conf or unconf):
        return None  # a busca devolveu algo que nem cita o cliente
    out["clients"], out["clients_unconfirmed"] = conf, unconf

    # --- categorias, tags, módulos, natureza ---
    cats = {item["source_category"]} if item.get("source_category") else set()
    topic_hits: list[str] = []
    for cat, terms in kw["categories"].items():
        hits = count_hits(text, terms)
        if hits:
            cats.add(cat)
            if cat in ("sap", "reforma_tributaria"):
                topic_hits += hits
    if conf:
        cats.add("clientes")
    out["categories"] = sorted(cats)
    out["tags"] = sorted(set(topic_hits))[:8]
    out["sap_modules"] = [m for m, terms in kw["sap_modules"].items() if count_hits(text, terms)]
    out["nature"] = [n for n, terms in kw["nature"].items() if count_hits(text, terms)]
    urgency = count_hits(text, kw["urgency_terms"])

    # --- prazos citados (só datas futuras) ---
    deadlines = []
    if urgency or "Prazo ou cronograma" in out["nature"]:
        deadlines = extract_deadlines(raw, now.date())
    out["deadlines"] = deadlines

    # --- pontuação 0-100 ---
    parts = {"fonte": sc["reliability_points"].get(authority, 0)}
    pub = parse_iso(item.get("published"))
    confirmed_date = bool(pub and item.get("published_confirmed"))
    parts["recência"] = 0
    if confirmed_date:
        age = (now - pub).total_seconds() / 86400
        for r in sc["recency"]:
            if age <= r["max_days"]:
                parts["recência"] = r["points"]
                break
    parts["tema"] = min(len(set(topic_hits)) * sc["topic_points_per_hit"], sc["topic_points_cap"])
    parts["urgência"] = min(len(urgency) * sc["urgency_points_per_hit"], sc["urgency_points_cap"])
    parts["cliente"] = sc["client_confirmed_points"] if conf else 0
    db = sc["deadline_bonus"]
    parts["prazo próximo"] = 0
    if deadlines and (date.fromisoformat(deadlines[0]) - now.date()).days <= db["within_days"]:
        parts["prazo próximo"] = db["points"]
    score = min(sum(parts.values()), 100)

    lv = sc["levels"]
    key = "critical" if score >= lv["critical"] else "high" if score >= lv["high"] else "medium" if score >= lv["medium"] else "info"
    note = ""
    if key == "critical" and not (authority == "official" and urgency and confirmed_date):
        key = "high"
        note = " Rebaixada de Crítica: falta evidência verificável (fonte oficial + prazo/obrigação + data confirmada)."
    label = dict(_LEVELS)[key]
    just = (f"Pontuação automática {score}/100 ("
            + ", ".join(f"{k} {v}" for k, v in parts.items() if v) + ")." + note)
    if not confirmed_date:
        just += " Data de publicação não confirmada."
    out["relevance"] = {"score": score, "level": label, "justification": just}
    out["action_level"] = kw["action_labels"][key]
    out["action_ai"] = kw["recommended_actions"][key]

    mods = ", ".join(out["sap_modules"][:3])
    nat = out["nature"][0] if out["nature"] else None
    if mods or nat:
        out["impact_ai"] = ((f"Possível relação com {mods}. " if mods else "")
                            + (f"Natureza provável: {nat}. " if nat else "")
                            + "O impacto real depende da versão, do ambiente e do escopo de cada projeto.")
    else:
        out["impact_ai"] = "Evidências insuficientes para estimar impacto nos projetos."

    # --- tipo de conteúdo e status regulatório ---
    kind = CONTENT_KIND.get(authority, "Notícia jornalística")
    if authority != "official" and "Análise de especialista" in out["nature"]:
        kind = "Análise de terceiros"
    out["content_kind"] = kind

    rt = None
    if "reforma_tributaria" in cats:
        low = raw.lower()
        if authority != "official":
            rt = RT_THIRD
        elif any(w in text + " " for w in _PENDING_WORDS):
            rt = RT_PENDING
        elif "nota técnica" in low or "leiaute" in low or "layout" in low:
            rt = RT_TECHNICAL
        elif "Obrigação ou mudança oficial" in out["nature"]:
            rt = RT_OFFICIAL_LAW
        else:
            rt = RT_PENDING
    out["rt_status"] = rt

    # --- confiança ---
    if authority == "official" and confirmed_date and len(set(topic_hits)) >= 2:
        conf_lbl = "Alta"
    elif unconf and not conf or not confirmed_date:
        conf_lbl = "Baixa"
    else:
        conf_lbl = "Média"
    out["confidence"] = conf_lbl
    out["analysis_origin"] = "Análise gerada automaticamente por regras; não é determinação da fonte."
    return out
