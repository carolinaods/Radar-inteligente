"""Coleta: busca HTTP educada (retentativas, espera, robots.txt) e leitores por tipo de fonte.

Regras: não contorna paywall/login/bloqueio; 403/404 não são repetidos; tudo que
falha vira erro registrado no log da execução (nunca é escondido).
"""
from __future__ import annotations

import time
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup

from .textutil import norm
from .util import clean_html, iso, make_summary, now_utc

GNEWS = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, defaults: dict):
        self.d = defaults
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _throttle(self) -> None:
        wait = self.d.get("delay_between_requests", 1.0) - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def get(self, url: str):
        headers = {"User-Agent": self.d["user_agent"], "Accept": "*/*"}
        last = "erro desconhecido"
        for attempt in range(1, self.d.get("retries", 3) + 1):
            self._throttle()
            try:
                r = requests.get(url, headers=headers, timeout=self.d.get("request_timeout", 25))
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"[:200]
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(int(r.headers.get("Retry-After", 0) or 0), 30) or 2 ** attempt
                time.sleep(wait)
                continue
            raise FetchError(last)  # 403/404 etc.: não adianta repetir nem contornar
        raise FetchError(f"{last} após {self.d.get('retries', 3)} tentativas")

    def robots_allows(self, url: str) -> bool:
        p = urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = requests.get(f"{host}/robots.txt", headers={"User-Agent": self.d["user_agent"]},
                                 timeout=self.d.get("request_timeout", 25))
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[host] = rp
                else:
                    self._robots[host] = None  # sem robots.txt: permitido
            except requests.RequestException:
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.d["user_agent"], url)


def _item(src: dict, title: str, url: str, summary: str, published: str | None, confirmed: bool,
          publisher: str | None = None) -> dict:
    return {
        "title": title, "summary": summary, "url": url,
        "published": published, "published_confirmed": confirmed,
        "source_id": src["id"], "source_name": src["name"],
        "source_authority": src["authority"], "source_category": src["category"],
        "collected_at": iso(now_utc()), "publisher": publisher,
    }


def _entry_date(e) -> tuple[str | None, bool]:
    t = e.get("published_parsed")
    if t:
        return iso(datetime(*t[:6], tzinfo=timezone.utc)), True
    t = e.get("updated_parsed")  # só "atualizado": mostra, mas NÃO confirma como publicação
    if t:
        return iso(datetime(*t[:6], tzinfo=timezone.utc)), False
    return None, False


def parse_feed_bytes(src: dict, content: bytes, cutoff: datetime | None) -> list[dict]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise FetchError(f"feed inválido: {feed.bozo_exception}")
    out = []
    for e in feed.entries:
        title, url = clean_html(e.get("title")), e.get("link")
        if not title or not url:
            continue
        body = e.get("summary") or (e.get("content") or [{}])[0].get("value", "")
        summary = make_summary(body)
        if norm(summary).startswith(norm(title)):  # Google Notícias repete o título
            summary = ""
        pub, confirmed = _entry_date(e)
        if cutoff and pub and datetime.fromisoformat(pub.replace("Z", "+00:00")) < cutoff:
            continue
        publisher = e["source"].get("title") if isinstance(e.get("source"), dict) else None
        out.append(_item(src, title, url, summary, pub, confirmed, publisher))
    return out


def collect_rss(src: dict, fetcher: Fetcher, defaults: dict) -> list[dict]:
    cutoff = now_utc() - timedelta(days=defaults.get("max_age_days", 60))
    items = parse_feed_bytes(src, fetcher.get(src["url"]).content, cutoff)
    return items[: defaults.get("per_source_limit", 40)]


def collect_html_links(src: dict, fetcher: Fetcher, defaults: dict) -> list[dict]:
    if not fetcher.robots_allows(src["url"]):
        raise FetchError("bloqueado por robots.txt (respeitado)")
    html = fetcher.get(src["url"]).text
    needles = [norm(x) for x in src.get("link_contains", [])]
    seen, out = set(), []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        label = " ".join(a.get_text(" ", strip=True).split())
        href = a["href"]
        if len(label) < 15 or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if needles and not any(n in norm(label) for n in needles):
            continue
        url = urljoin(src["url"], href)
        if url in seen:
            continue
        seen.add(url)
        # páginas de listagem raramente trazem data: NÃO inventamos uma.
        out.append(_item(src, label, url, "", None, False))
        if len(out) >= defaults.get("per_source_limit", 40):
            break
    return out


def collect_google_news(src: dict, fetcher: Fetcher, defaults: dict, clients: list[dict],
                        days: int) -> tuple[list[dict], list[str]]:
    found, errs = [], []
    if src.get("per_client"):
        queries = [(c["name"], f'"{c.get("query", c["name"])}"') for c in clients]
    else:
        queries = [(None, q) for q in src.get("queries", [])]
    consecutive = 0
    for hint, q in queries:
        url = GNEWS.format(q=quote(f"{q} when:{days}d"))
        try:
            items = parse_feed_bytes(src, fetcher.get(url).content, None)
            consecutive = 0
        except Exception as e:  # noqa: BLE001
            errs.append(f"{hint or q}: {e}"[:160])
            consecutive += 1
            if consecutive >= 5 and not found:
                errs.append("abortado após 5 falhas seguidas (fonte indisponível)")
                break
            continue
        for it in items[:15]:
            if hint:
                it["client_hint"], it["per_client_source"] = hint, True
            found.append(it)
    return found, errs


def collect_manual_items(items: list[dict]) -> list[dict]:
    """Itens digitados à mão em config/manual_items.yml (só dados reais)."""
    out = []
    for m in items or []:
        if not (m.get("title") and m.get("url")):
            continue
        pub = f"{m['published']}T12:00:00Z" if m.get("published") else None
        src = {"id": "manual", "name": m.get("source_name", "Inclusão manual"),
               "authority": m.get("authority", "specialist"), "category": m.get("category", "sap")}
        out.append(_item(src, m["title"], m["url"], make_summary(m.get("summary", "")), pub, bool(pub)))
    return out
