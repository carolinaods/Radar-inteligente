"""Orquestrador da coleta semanal.

Uso:
    python -m collector.run                 # coleta completa
    python -m collector.run --only <id>     # só uma fonte (teste)
    python -m collector.run --export-only   # só regenera docs/data/config.json

Garantias:
- Uma fonte que falha não derruba as demais.
- Se NENHUMA fonte automática funcionar, a base anterior é preservada intacta
  e o run é registrado como "falha".
- Escrita atômica; histórico de notícias nunca é apagado (ver retention_days).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

from . import sources as S
from .classify import classify
from .store import apply_retention, load_json, merge, save_json_atomic
from .util import iso, now_utc

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "docs" / "data"


def load_yaml(name: str):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8")) or {}


def export_config(clients: list[dict], kw: dict, sources_cfg: dict) -> None:
    """Dados de configuração que o site precisa (sem segredos)."""
    save_json_atomic(
        DATA / "config.json",
        {
            "clients": [{"name": c["name"], "reviewed": c.get("reviewed", False)} for c in clients],
            "sap_modules": list(kw["sap_modules"].keys()),
            "nature": list(kw["nature"].keys()),
            "sources": [
                {"id": s["id"], "name": s["name"], "kind": s["kind"], "enabled": s.get("enabled", True),
                 "authority": s.get("authority"), "verified": s.get("verified", False), "note": s.get("note", "")}
                for s in sources_cfg["sources"]
            ],
        },
    )


def llm_hook(item: dict) -> dict:
    """Ponto de extensão: aqui entraria a chamada à API da Anthropic (opcional,
    com limite de consumo). Hoje não faz nada; a classificação é por regras."""
    return item


def run(only: str | None = None, export_only: bool = False) -> int:
    t0 = time.time()
    started = now_utc()
    clients = load_yaml("clients.yml")["clients"]
    kw = load_yaml("keywords.yml")
    scfg = load_yaml("sources.yml")
    defaults = scfg["defaults"]
    export_config(clients, kw, scfg)
    if export_only:
        return 0

    news_path, runs_path = DATA / "news.json", DATA / "runs.json"
    existing = load_json(news_path, {"items": []})["items"]
    runs = load_json(runs_path, {"runs": []})["runs"]
    first_run = not existing
    days = defaults["first_run_lookback_days"] if first_run else defaults["weekly_lookback_days"]

    fetcher = S.Fetcher(defaults)
    raw_items: list[dict] = []
    src_status: list[dict] = []

    for src in scfg["sources"]:
        if only and src["id"] != only:
            continue
        st = {"id": src["id"], "name": src["name"], "kind": src["kind"], "status": "ok",
              "items": 0, "error": None, "note": src.get("note", "")}
        t1 = time.time()
        if not src.get("enabled", True):
            st["status"] = "desativada"
        elif src["kind"] == "manual":
            st["status"] = "manual"  # exige acompanhamento humano; ver config/manual_items.yml
        else:
            try:
                if src["kind"] == "rss":
                    found = S.collect_rss(src, fetcher, defaults)
                elif src["kind"] == "html_links":
                    found = S.collect_html_links(src, fetcher, defaults)
                elif src["kind"] == "google_news":
                    found, errs = S.collect_google_news(src, fetcher, defaults, clients, days)
                    if errs:
                        st["error"] = f"{len(errs)} consulta(s) falharam: " + " | ".join(errs[:3])
                        st["status"] = "parcial" if found else "erro"
                else:
                    raise S.FetchError(f"tipo de fonte desconhecido: {src['kind']}")
                raw_items.extend(found)
                st["items"] = len(found)
            except Exception as e:  # noqa: BLE001 — registrar e seguir com as demais
                st["status"] = "erro"
                st["error"] = f"{type(e).__name__}: {e}"
        st["duration_s"] = round(time.time() - t1, 1)
        src_status.append(st)
        print(f"[{st['status']:>10}] {src['id']}: {st['items']} itens {st['error'] or ''}", flush=True)

    raw_items.extend(S.collect_manual_items(load_yaml("manual_items.yml").get("items", [])))

    now = now_utc()
    classified = []
    for it in raw_items:
        c = classify(it, kw, clients, now)
        if c is not None:
            classified.append(llm_hook(c))

    automatic = [s for s in src_status if s["kind"] != "manual" and s["status"] != "desativada"]
    ok_count = sum(1 for s in automatic if s["status"] in ("ok", "parcial"))
    err_count = sum(1 for s in automatic if s["status"] in ("erro", "parcial"))
    if only:
        status = "parcial" if err_count else "concluida"
    elif ok_count == 0:
        status = "falha"
    elif err_count:
        status = "parcial"
    else:
        status = "concluida"

    stats = {"new": 0, "duplicates": 0, "updated": 0}
    if status != "falha":
        merged, stats = merge(existing, classified, now)
        merged = apply_retention(merged, int(defaults.get("retention_days", 0) or 0), now)
        merged.sort(key=lambda i: (i.get("published") or i["first_seen"]), reverse=True)
        save_json_atomic(news_path, {"generated_at": iso(now), "items": merged})
    # se falhou, a última base válida permanece como está

    run_rec = {
        "started_at": iso(started),
        "finished_at": iso(now_utc()),
        "duration_s": round(time.time() - t0, 1),
        "status": status,
        "first_run": first_run,
        "sources_consulted": len(automatic),
        "items_found": len(raw_items),
        "items_kept": len(classified),
        "new": stats["new"],
        "duplicates": stats["duplicates"],
        "updated": stats["updated"],
        "sources": src_status,
    }
    runs.append(run_rec)
    save_json_atomic(runs_path, {"runs": runs[-200:]})
    print(f"Execução {status}: novas={stats['new']} duplicadas={stats['duplicates']} atualizadas={stats['updated']}")
    return 0 if status != "falha" else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--export-only", action="store_true")
    a = ap.parse_args()
    sys.exit(run(a.only, a.export_only))
