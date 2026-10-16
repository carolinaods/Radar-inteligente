"""Busca de termos em texto normalizado (sem acentos, sem pontuação)."""
from __future__ import annotations

import re
import unicodedata


def norm(text: str | None) -> str:
    """minúsculas, sem acentos, só letras/dígitos separados por espaço único."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def term_regex(term: str, *, whole: bool | None = None) -> re.Pattern:
    """Termo com fronteira no início; no fim só se curto (siglas) ou whole=True.
    Termos longos funcionam como radicais ("farmac" casa "farmacêutica")."""
    n = norm(term)
    if whole is None:
        whole = len(n) <= 4
    pat = r"(?<![a-z0-9])" + re.escape(n).replace(r"\ ", " ")
    if whole:
        pat += r"(?![a-z0-9])"
    return re.compile(pat)


def count_hits(normed_text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if term_regex(t).search(normed_text)]
