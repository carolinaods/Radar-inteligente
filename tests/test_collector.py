"""Testes da lógica do coletor.

ATENÇÃO: os textos abaixo são FIXTURES SINTÉTICAS de teste, escritas só para
exercitar o código. Eles NUNCA são gravados em docs/data nem exibidos no site.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from collector import run as R
from collector import sources as S
from collector.classify import classify, extract_deadlines, match_clients
from collector.store import item_id, merge
from collector.util import canonical_url, iso, norm_title

ROOT = Path(__file__).resolve().parent.parent
KW = yaml.safe_load((ROOT / "config" / "keywords.yml").read_text(encoding="utf-8"))
CLIENTS = yaml.safe_load((ROOT / "config" / "clients.yml").read_text(encoding="utf-8"))["clients"]
NOW = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)


def mk(title, summary="", url="https://news.sap.com/x", authority="official", category="sap",
       published=NOW - timedelta(days=1), **extra):
    d = {
        "title": title, "summary": summary, "url": url, "published": iso(published),
        "published_confirmed": published is not None, "source_id": "t", "source_name": "Fonte teste",
        "source_authority": authority, "source_category": category, "collected_at": iso(NOW),
    }
    d.update(extra)
    return d


def test_clients_list_has_52_unique():
    assert len(CLIENTS) == 52
    assert len({c["name"] for c in CLIENTS}) == 52


def test_canonical_url_strips_tracking_and_slash():
    a = canonical_url("HTTPS://News.SAP.com/a/b/?utm_source=x&id=3#frag")
    b = canonical_url("https://news.sap.com/a/b?id=3")
    assert a == b
    assert item_id("https://x.com/a?utm_medium=z") == item_id("https://x.com/a/")


def test_extract_deadlines_only_future():
    ds = extract_deadlines("Prazo até 30/11/2026 e também 10 de dezembro de 2026; antigo 01/01/2020", NOW.date())
    assert ds == ["2026-11-30", "2026-12-10"]


def test_official_rt_with_near_deadline_is_critical():
    it = classify(mk(
        "Nota Técnica altera leiaute da NF-e para CBS e IBS",
        "A nota técnica publicada torna obrigatório o novo leiaute. Homologação até 20/11/2026.",
        url="https://www.nfe.fazenda.gov.br/nt1", category="reforma_tributaria"), KW, CLIENTS, NOW)
    assert it["relevance"]["level"] == "Crítica"
    assert "reforma_tributaria" in it["categories"]
    assert it["rt_status"] == "Orientação técnica ou nota técnica publicada"
    assert it["relevance"]["justification"]


def test_third_party_never_critical_and_labelled_third_party():
    it = classify(mk(
        "Reforma Tributária: nota técnica obrigatória com prazo 20/11/2026",
        "Análise do escritório: leiaute obrigatório de CBS e IBS.",
        url="https://blog.exemplo.com.br/a", authority="journalistic", category="reforma_tributaria"),
        KW, CLIENTS, NOW)
    assert it["relevance"]["level"] != "Crítica"
    assert it["rt_status"] == "Análise ou interpretação de terceiros"
    assert it["content_kind"] in ("Análise de terceiros", "Notícia jornalística")


def test_forecast_is_not_presented_as_obligation():
    it = classify(mk(
        "Governo deve publicar regulamentação da Reforma Tributária, diz previsão",
        "A minuta pode ser divulgada em breve, com expectativa de entrada em vigor em 2027.",
        url="https://www.gov.br/fazenda/x", category="reforma_tributaria"), KW, CLIENTS, NOW)
    assert it["rt_status"] == "Comunicado ou previsão sujeita a confirmação"
    assert it["relevance"]["level"] != "Crítica"


def test_urgent_word_alone_does_not_make_critical():
    it = classify(mk("URGENTE: SAP anuncia evento", "Evento SAP urgente para clientes.",
                     url="https://exemplo.com/e", authority="journalistic"), KW, CLIENTS, NOW)
    assert it["relevance"]["level"] in ("Informativa", "Média")


def test_unconfirmed_when_ambiguous_without_context():
    # "METRO" é ambíguo: sem termo de setor, vira associação não confirmada
    conf, unconf = match_clients(CLIENTS, "Metro de São Paulo amplia linha", "METRO")
    assert "METRO" not in conf and "METRO" in unconf
    conf, unconf = match_clients(CLIENTS, "Metro AG vende operação de atacado Cash & Carry", "METRO")
    assert "METRO" in conf


def test_non_ambiguous_client_confirmed_and_multi_client():
    conf, _ = match_clients(CLIENTS, "Tereos e São Martinho anunciam acordo de etanol e açúcar na usina", None)
    assert "TEREOS" in conf and "SÃO MARTINHO" in conf


def test_per_client_item_without_mention_is_dropped():
    it = mk("Mercado de capitais sobe", "Nada a ver", authority="journalistic", category="clientes",
            client_hint="CIMED", per_client_source=True)
    assert classify(it, KW, CLIENTS, NOW) is None


def test_client_item_kept_and_associated():
    it = classify(mk("Cimed anuncia nova fábrica e investimento em ERP",
                     "A farmacêutica Cimed vai investir em modernização.",
                     url="https://exemplo.com/cimed", authority="journalistic", category="clientes",
                     client_hint="CIMED", per_client_source=True), KW, CLIENTS, NOW)
    assert it["clients"] == ["CIMED"]
    assert "clientes" in it["categories"]


def test_unknown_date_is_flagged_and_not_invented():
    it = classify(mk("Página oficial SAP sem data", "", published=None), KW, CLIENTS, NOW)
    assert it["published"] is None and it["confidence"] != "Alta"
    assert "não confirmada" in it["relevance"]["justification"]


# ---------- deduplicação e histórico ----------

def _classified(**kw):
    return classify(mk(**kw), KW, CLIENTS, NOW)


def test_dedup_same_url_and_same_title_other_source():
    a = _classified(title="SAP lança release", summary="texto", url="https://news.sap.com/a?utm_source=x")
    base, st = merge([], [a], NOW)
    assert st["new"] == 1
    again = _classified(title="SAP lança release", summary="texto", url="https://news.sap.com/a/")
    base, st = merge(base, [again], NOW + timedelta(days=7))
    assert st["duplicates"] == 1 and st["new"] == 0 and len(base) == 1
    other = _classified(title="SAP lança release!", summary="outro", url="https://outro.com/z")
    base, st = merge(base, [other], NOW + timedelta(days=7))
    assert len(base) == 1 and base[0]["also_in"][0]["url"] == "https://outro.com/z"


def test_substantive_update_keeps_history_and_is_not_new():
    a = _classified(title="SAP Note publicada", summary="versão 1", url="https://news.sap.com/n")
    base, _ = merge([], [a], NOW)
    b = _classified(title="SAP Note publicada", summary="versão 2 com correção", url="https://news.sap.com/n")
    base, st = merge(base, [b], NOW + timedelta(days=7))
    assert st["updated"] == 1 and st["new"] == 0
    assert base[0]["history"] and base[0]["updated_at"]
    assert base[0]["first_seen"] == iso(NOW)


def test_old_items_preserved_when_new_run_adds_more():
    a = _classified(title="Antigo", url="https://news.sap.com/old")
    base, _ = merge([], [a], NOW)
    b = _classified(title="Novo", url="https://news.sap.com/new")
    base, _ = merge(base, [b], NOW + timedelta(days=7))
    assert {i["title"] for i in base} == {"Antigo", "Novo"}


# ---------- RSS e execução ponta a ponta (rede simulada) ----------

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Teste</title>
<item><title>Teste sintetico: SAP Note de correcao para CBS</title><link>https://news.sap.com/teste-1</link>
<description>&lt;p&gt;Correcao de localizacao Brasil. Prazo ate 30/11/2026.&lt;/p&gt;</description>
<pubDate>Thu, 15 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Sem data nenhuma</title><link>https://news.sap.com/teste-2</link><description>x</description></item>
</channel></rss>"""


def test_parse_feed_extracts_fields_and_no_fake_date():
    src = {"id": "t", "name": "T", "authority": "official", "category": "sap"}
    items = S.parse_feed_bytes(src, RSS, None)
    assert len(items) == 2
    assert items[0]["published"] == "2026-10-15T10:00:00Z" and items[0]["published_confirmed"]
    assert items[1]["published"] is None and not items[1]["published_confirmed"]
    assert "<p>" not in items[0]["summary"]


class FakeResp:
    def __init__(self, content):
        self.content = content
        self.text = content.decode("utf-8", "ignore")
        self.status_code = 200


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "DATA", tmp_path / "data")
    # fonte única, para o teste ser rápido
    cfg = yaml.safe_load((ROOT / "config" / "sources.yml").read_text(encoding="utf-8"))
    cfg["sources"] = [s for s in cfg["sources"] if s["id"] in ("sap-news-center", "confaz", "sap-help-whatsnew")]
    orig = R.load_yaml
    monkeypatch.setattr(R, "load_yaml", lambda n: cfg if n == "sources.yml" else orig(n))
    monkeypatch.setattr(S.Fetcher, "_throttle", lambda self: None)
    return tmp_path / "data"


def test_full_run_persists_and_second_run_dedupes(sandbox, monkeypatch):
    def fake_get(self, url):
        if "confaz" in url:
            raise S.FetchError("HTTP 503")  # uma fonte falha...
        return FakeResp(RSS)                # ...e a outra funciona

    monkeypatch.setattr(S.Fetcher, "get", fake_get)
    monkeypatch.setattr(S.Fetcher, "robots_allows", lambda self, u: True)
    assert R.run() == 0
    news = json.loads((sandbox / "news.json").read_text())["items"]
    runs = json.loads((sandbox / "runs.json").read_text())["runs"]
    assert len(news) == 2
    assert runs[-1]["status"] == "parcial"  # fonte com erro não derruba as demais
    errs = {s["id"]: s["status"] for s in runs[-1]["sources"]}
    assert errs["confaz"] == "erro" and errs["sap-news-center"] == "ok" and errs["sap-help-whatsnew"] == "manual"

    R.run()  # segunda execução: nada novo, histórico mantido
    news2 = json.loads((sandbox / "news.json").read_text())["items"]
    runs2 = json.loads((sandbox / "runs.json").read_text())["runs"]
    assert len(news2) == 2 and runs2[-1]["new"] == 0 and runs2[-1]["duplicates"] == 2 and len(runs2) == 2


def test_total_failure_preserves_last_valid_base(sandbox, monkeypatch):
    monkeypatch.setattr(S.Fetcher, "get", lambda self, url: FakeResp(RSS))
    monkeypatch.setattr(S.Fetcher, "robots_allows", lambda self, u: True)
    R.run()
    before = (sandbox / "news.json").read_text()

    def boom(self, url):
        raise S.FetchError("sem rede")

    monkeypatch.setattr(S.Fetcher, "get", boom)
    assert R.run() == 1  # código de saída != 0 => workflow marca falha
    assert (sandbox / "news.json").read_text() == before
    runs = json.loads((sandbox / "runs.json").read_text())["runs"]
    assert runs[-1]["status"] == "falha" and runs[-1]["sources"][0]["error"]
